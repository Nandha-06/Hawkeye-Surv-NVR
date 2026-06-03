<script lang="ts">
    import { onDestroy, onMount, tick } from 'svelte';
    import { getApiToken, buildWsUrl } from '$lib/apiToken';
    import { browser } from '$app/environment';
    import UnifiedPlayer from '$lib/components/UnifiedPlayer.svelte';
    import { devMode } from '$lib/devMode.svelte';
    import type {
        SkillMetadata,
        ProviderConfig,
        ActiveInferenceConfig
    } from '$lib/types';

    // ==========================================
    // 1. GENERAL INTERFACES
    // ==========================================
    interface CameraConfig {
        id: string;
        name: string;
        source: 'rtsp' | 'webcam';
        url?: string;
        enabled: boolean;
        fps?: number;
        confidence?: number;
        enable_motion_gating?: boolean;
    }

    interface RecorderStatus {
        cameraId: string;
        cameraName: string;
        source: CameraConfig['source'];
        state: 'recording' | 'stopped' | 'error' | 'unsupported';
        message?: string;
        startedAt?: string;
        segmentSeconds: number;
        indexedSegments: number;
        lastSegmentAt?: string;
    }

    // ==========================================
    // 2. UNIFIED TELEMETRY & PIPELINE STATE
    // ==========================================
    let ws = $state<WebSocket | null>(null);
    let wsStatus = $state<'connecting' | 'connected' | 'disconnected'>('disconnected');
    let wsReconnectTimer: ReturnType<typeof setTimeout> | null = null;
    
    let cameras = $state<CameraConfig[]>([]);
    let recorders = $state<RecorderStatus[]>([]);
    let allSkills = $state<SkillMetadata[]>([]);
    
    let activeSkillStatus = $state<'stopped' | 'starting' | 'ready' | 'error'>('stopped');
    let selectedSkillId = $state('perception-core');
    let selectedSkill = $derived(allSkills.find(s => s.id === selectedSkillId));

    let showOverlays = $state(true);
    let gridLayout = $state<'auto' | '1x1' | '2x2' | '3x3' | '4x4'>('auto');
    let focusedCameraId = $state<string | null>(null);
    let detectionFps = $state(0);
    let frameCounter = 0;
    let fpsWindowStart = Date.now();

    // Hardware load stats state
    let hardwareStats = $state({
        cpu: 0,
        gpu: 0,
        memory: { used: 0, total: 16.0, percent: 0 },
        storage: { used: 0, total: 512, percent: 0 }
    });

    // Canvas & Video element bindings
    let cameraCanvases: Record<string, HTMLCanvasElement> = {};
    let overlayCanvases: Record<string, HTMLCanvasElement> = {};
    let webcamVideos: Record<string, HTMLVideoElement> = {};
    let webcamStreams: Record<string, MediaStream> = {};
    let webcamIntervals: Record<string, ReturnType<typeof setInterval>> = {};
    let captureCanvases: Record<string, HTMLCanvasElement> = {};
    let webrtcConnections: Record<string, RTCPeerConnection> = {};
    let webrtcStreams: Record<string, MediaStream> = {};

    // Browser recording
    interface ActiveBrowserRecorder {
        recorder: MediaRecorder;
        intervalId?: ReturnType<typeof setInterval>;
        chunks?: Blob[];
        segmentStartTime: string;
    }
    let browserRecorders: Record<string, ActiveBrowserRecorder> = {};

    const enabledCameras = $derived(cameras.filter(camera => camera.enabled));
    const visibleCameras = $derived(focusedCameraId
        ? enabledCameras.filter(camera => camera.id === focusedCameraId)
        : enabledCameras
    );
    const activeRecorders = $derived(recorders.filter(recorder => recorder.state === 'recording').length);

    // Model Downloader & Customizer states
    let providers: Record<string, ProviderConfig> = $state({});
    let localModels: { name: string; sizeBytes: number; path: string; isVlm: boolean }[] = $state([]);
    let activeInference: ActiveInferenceConfig = $state({
        llm: { type: 'local-engine', engineId: 'llama-cpp', modelId: null, port: 5411, provider: null, cloudModelId: null },
        vlm: { type: 'local-engine', engineId: 'llama-cpp', modelId: null, port: 5405, provider: null, cloudModelId: null }
    });
    let activeDownloads: Record<string, { percent: number; bytesDownloaded: number; totalBytes: number; status: string; error?: string }> = $state({});

    // Realtime structured logs states
    interface LogEntry {
        timestamp: string;
        source: 'stdout' | 'stderr';
        level: 'info' | 'warn' | 'error' | 'success';
        message: string;
        tag?: string;
    }
    let structuredLogs: Record<string, LogEntry[]> = $state({});
    let logLevelFilter = $state<'all' | 'info' | 'warn' | 'error' | 'success'>('all');
    let logSearchTerm = $state('');
    let scrollLock = $state(true);
    let terminalElement = $state<HTMLDivElement | null>(null);

    // PTZ command & overlay state
    let activePtzCameraId = $state<string | null>(null);

    // Diagnostics & Self-Healing Warnings
    let diagnosticWarnings = $state<{code: string; message: string}[]>([]);

    // Loaded AI modules telemetry
    let activeModules = $state({ face_recognition: false, tracking: false });
    // Camera-specific motion gating states (power saving)
    let motionGatedCameras = $state<Record<string, boolean>>({});
    
    let isSurveillanceToggling = $state(false);
    const isSurveillanceActive = $derived(
        (activeSkillStatus === 'ready' || activeSkillStatus === 'starting') || activeRecorders > 0
    );

    function togglePtzOverlay(cameraId: string) {
        activePtzCameraId = activePtzCameraId === cameraId ? null : cameraId;
    }

    async function sendPtzCommand(cameraId: string, action: 'move' | 'stop', x: number = 0, y: number = 0, zoom: number = 0) {
        try {
            await fetch(`/api/v1/cameras/${cameraId}/ptz`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ action, x, y, zoom })
            });
        } catch (err) {
            console.error('[PTZ Command] Request failed:', err);
        }
    }

    // ==========================================
    // 3. PIPELINE STATE (DEPLOY & CONFIG)
    // ==========================================
    let skillConfigValues: Record<string, Record<string, any>> = $state({});
    let isDeployingSkill: Record<string, boolean> = $state({});
    let deployLogs: Record<string, string> = $state({});
    let deployProgress: Record<string, number> = $state({});
    let executionLogs: Record<string, string> = $state({});

    // ==========================================
    // 4. INITIALIZATION & SOCKET SETUP
    // ==========================================
    onMount(async () => {
        await Promise.all([loadCameras(), loadRecorders()]);
        connectWS();
    });

    $effect(() => {
        if (!browser) return;
        if (cameras.length === 0) return;

        const isDetectionActive = activeSkillStatus === 'ready' || activeSkillStatus === 'starting';
        const isRecordingActive = recorders.some(r => r.state === 'recording');

        if (isDetectionActive || isRecordingActive) {
            for (const camera of enabledCameras) {
                if (camera.source === 'webcam') {
                    startWebcam(camera);
                } else if (camera.source === 'rtsp') {
                    startWebRTC(camera);
                }
            }
        } else {
            stopWebcams();
            for (const camera of enabledCameras) {
                if (camera.source === 'rtsp') {
                    stopWebRTC(camera.id);
                }
            }
        }
    });

    // Erase overlay canvas immediately when overlays are toggled off
    $effect(() => {
        if (!showOverlays) {
            for (const [cameraId, canvas] of Object.entries(overlayCanvases)) {
                if (canvas) {
                    const ctx = canvas.getContext('2d');
                    if (ctx) {
                        ctx.clearRect(0, 0, canvas.width, canvas.height);
                    }
                }
            }
        }
    });

    // Reset module telemetry and motion gating when system stops
    $effect(() => {
        if (activeSkillStatus === 'stopped') {
            activeModules = { face_recognition: false, tracking: false };
            motionGatedCameras = {};
        }
    });

    onDestroy(() => {
        stopWebcams();
        for (const camera of enabledCameras) {
            if (camera.source === 'rtsp') {
                stopWebRTC(camera.id);
            }
        }
        if (wsReconnectTimer) clearTimeout(wsReconnectTimer);
        if (ws) {
            ws.onclose = null;
            ws.close();
        }
    });

    async function loadCameras() {
        const res = await fetch('/api/v1/cameras');
        cameras = res.ok ? await res.json() : [];
    }

    async function loadRecorders() {
        const res = await fetch('/api/v1/recorders');
        recorders = res.ok ? await res.json() : [];
        syncBrowserRecorders();
    }

    // ==========================================
    // 5. WEBCAM INTERFACE & STREAMS
    // ==========================================
    async function startWebcam(camera: CameraConfig) {
        if (webcamStreams[camera.id]) return;
        try {
            const mediaStream = await navigator.mediaDevices.getUserMedia({
                video: { width: 640, height: 480 },
                audio: false
            });
            webcamStreams[camera.id] = mediaStream;
            await tick();

            const video = webcamVideos[camera.id];
            if (video) {
                video.srcObject = mediaStream;
                await video.play().catch(() => {});
            }

            const canvas = document.createElement('canvas');
            canvas.width = 640;
            canvas.height = 480;
            captureCanvases[camera.id] = canvas;

            const intervalMs = Math.max(200, Math.round(1000 / (camera.fps || 5)));
            let frameId = 0;
            webcamIntervals[camera.id] = setInterval(() => {
                if (activeSkillStatus !== 'ready' || !ws || ws.readyState !== WebSocket.OPEN) return;
                if (!video || video.readyState < video.HAVE_CURRENT_DATA) return;

                const ctx = canvas.getContext('2d');
                if (!ctx) return;
                ctx.drawImage(video, 0, 0, canvas.width, canvas.height);
                ws.send(JSON.stringify({
                    action: 'feed_frame',
                    skillId: selectedSkillId,
                    cameraId: camera.id,
                    frameId: frameId++,
                    frame: canvas.toDataURL('image/jpeg', 0.72),
                    timestamp: new Date().toISOString()
                }));
            }, intervalMs);

            syncBrowserRecorders();
        } catch (err) {
            console.error(`[Webcam] Failed to start webcam stream for ${camera.id}:`, err);
        }
    }

    function stopWebcams() {
        for (const interval of Object.values(webcamIntervals)) clearInterval(interval);
        webcamIntervals = {};
        for (const cameraId of Object.keys(browserRecorders)) stopBrowserRecording(cameraId);
        for (const stream of Object.values(webcamStreams)) stream.getTracks().forEach(track => track.stop());
        webcamStreams = {};
        captureCanvases = {};
    }

    async function startWebRTC(camera: CameraConfig) {
        if (webrtcConnections[camera.id]) return;
        try {
            console.log(`Starting WebRTC stream for RTSP camera: ${camera.id}`);
            const pc = new RTCPeerConnection({
                iceServers: [{ urls: 'stun:stun.l.google.com:19302' }]
            });
            webrtcConnections[camera.id] = pc;
            
            pc.addTransceiver('video', { direction: 'recvonly' });
            pc.addTransceiver('audio', { direction: 'recvonly' });
            
            pc.ontrack = (event) => {
                console.log(`WebRTC Track received for camera ${camera.id}: ${event.track.kind}`);
                const video = webcamVideos[camera.id];
                if (!video) return;
                
                if (!webrtcStreams[camera.id]) {
                    webrtcStreams[camera.id] = new MediaStream();
                }
                const stream = webrtcStreams[camera.id];
                if (!stream.getTracks().some(track => track.id === event.track.id)) {
                    stream.addTrack(event.track);
                }
                if (video.srcObject !== stream) {
                    video.srcObject = stream;
                }
            };
            
            const offer = await pc.createOffer();
            await pc.setLocalDescription(offer);
            
            const go2rtcUrl = `http://127.0.0.1:1984/api/webrtc?src=${encodeURIComponent(camera.id)}`;
            const response = await fetch(go2rtcUrl, {
                method: 'POST',
                headers: { 'Content-Type': 'application/sdp' },
                body: pc.localDescription?.sdp || ''
            });
            
            if (!response.ok) {
                throw new Error(`go2rtc returned HTTP ${response.status}`);
            }
            
            const sdpAnswer = await response.text();
            await pc.setRemoteDescription(new RTCSessionDescription({ type: 'answer', sdp: sdpAnswer }));
        } catch (err) {
            console.error(`[WebRTC] Failed to connect stream for ${camera.id}:`, err);
        }
    }

    function stopWebRTC(cameraId: string) {
        if (webrtcConnections[cameraId]) {
            webrtcConnections[cameraId].close();
            delete webrtcConnections[cameraId];
        }
        if (webrtcStreams[cameraId]) {
            webrtcStreams[cameraId].getTracks().forEach(track => track.stop());
            delete webrtcStreams[cameraId];
        }
        const video = webcamVideos[cameraId];
        if (video) {
            video.srcObject = null;
        }
    }

    // ==========================================
    // 6. BROWSER CONTINUOUS RECORDINGS PIPELINE
    // ==========================================
    function startBrowserRecording(camera: CameraConfig) {
        const stream = webcamStreams[camera.id];
        if (!stream || browserRecorders[camera.id]) return;

        const mimeType = MediaRecorder.isTypeSupported('video/webm;codecs=vp8') ? 'video/webm;codecs=vp8' : 'video/webm';
        const recorder = new MediaRecorder(stream, { mimeType });
        
        let segmentStartTime = new Date().toISOString();

        recorder.ondataavailable = async (e) => {
            if (e.data && e.data.size > 0) {
                const chunkStartTime = segmentStartTime;
                segmentStartTime = new Date().toISOString();
                await uploadWebcamChunk(camera.id, e.data, chunkStartTime, segmentStartTime);
            }
        };

        recorder.start(6000);

        browserRecorders[camera.id] = { recorder, segmentStartTime };
    }

    function stopBrowserRecording(cameraId: string) {
        const session = browserRecorders[cameraId];
        if (!session) return;
        if (session.recorder.state === 'recording') {
            session.recorder.stop();
        }
        delete browserRecorders[cameraId];
    }

    async function uploadWebcamChunk(cameraId: string, blob: Blob, start: string, end: string) {
        const formData = new FormData();
        formData.append('video', blob, 'webcam.webm');
        formData.append('camera_id', cameraId);
        formData.append('start_time', start);
        formData.append('end_time', end);
        formData.append('type', 'continuous');
        try {
            await fetch('/api/v1/recordings', { method: 'POST', body: formData });
            await loadRecorders();
        } catch (_) {}
    }

    function syncBrowserRecorders() {
        for (const camera of enabledCameras) {
            if (camera.source === 'webcam' && !camera.url) {
                const recorder = recorders.find(r => r.cameraId === camera.id);
                if (recorder?.state === 'recording') {
                    if (!browserRecorders[camera.id]) startBrowserRecording(camera);
                } else {
                    if (browserRecorders[camera.id]) stopBrowserRecording(camera.id);
                }
            }
        }
    }

    // ==========================================
    // 7. REAL-TIME CANVAS DRAWING UTILITIES
    // ==========================================
    const frameImages: Record<string, HTMLImageElement> = {};

    function drawLiveFrame(cameraId: string, frameData: string) {
        const canvas = cameraCanvases[cameraId];
        const ctx = canvas?.getContext('2d');
        if (!canvas || !ctx) return;

        let img = frameImages[cameraId];
        if (!img) {
            img = new Image();
            frameImages[cameraId] = img;
        }

        img.onload = () => {
            const currentCanvas = cameraCanvases[cameraId];
            const currentCtx = currentCanvas?.getContext('2d');
            if (!currentCanvas || !currentCtx) return;

            if (currentCanvas.width !== img.width || currentCanvas.height !== img.height) {
                currentCanvas.width = img.width;
                currentCanvas.height = img.height;
            }
            currentCtx.drawImage(img, 0, 0);
        };
        img.src = frameData;
    }

    function drawDetections(cameraId: string, objects: any[]) {
        const overlay = overlayCanvases[cameraId];
        const ctx = overlay?.getContext('2d');
        if (!overlay || !ctx) return;

        const video = webcamVideos[cameraId];
        const baseCanvas = cameraCanvases[cameraId];
        const targetWidth = video?.videoWidth || baseCanvas?.width || 640;
        const targetHeight = video?.videoHeight || baseCanvas?.height || 480;

        if (overlay.width !== targetWidth || overlay.height !== targetHeight) {
            overlay.width = targetWidth;
            overlay.height = targetHeight;
        } else {
            ctx.clearRect(0, 0, overlay.width, overlay.height);
        }

        if (!showOverlays) return;

        for (const obj of objects) {
            const [x1, y1, x2, y2] = obj.bbox;
            const color = colorForClass(obj.class);
            const label = `${obj.class} ${Math.round(obj.confidence * 100)}%`;
            ctx.strokeStyle = color;
            ctx.lineWidth = 2;
            ctx.strokeRect(x1, y1, x2 - x1, y2 - y1);
            ctx.fillStyle = color;
            ctx.font = '12px Inter, sans-serif';
            const labelWidth = ctx.measureText(label).width + 10;
            ctx.fillRect(x1, Math.max(0, y1 - 20), labelWidth, 20);
            ctx.fillStyle = '#fff';
            ctx.fillText(label, x1 + 5, Math.max(14, y1 - 6));
        }
    }

    function colorForClass(className: string) {
        if (className === 'person') return '#38bdf8';
        if (className === 'car') return '#f59e0b';
        if (className === 'dog' || className === 'cat') return '#22c55e';
        return '#a78bfa';
    }

    function updateDetectionFps() {
        frameCounter += 1;
        const now = Date.now();
        if (now - fpsWindowStart >= 1000) {
            detectionFps = Math.round((frameCounter * 1000) / (now - fpsWindowStart));
            frameCounter = 0;
            fpsWindowStart = now;
        }
    }

    // ==========================================
    // 8. WS CONTROL & EVENT ACTIONS
    // ==========================================
    function appendToExecutionLogs(sId: string, logMsg: string) {
        if (!executionLogs[sId]) {
            executionLogs[sId] = '';
        }
        executionLogs[sId] += logMsg + '\n';
        const maxLength = 50000;
        if (executionLogs[sId].length > maxLength) {
            const sliceIndex = executionLogs[sId].length - maxLength;
            const nextNewline = executionLogs[sId].indexOf('\n', sliceIndex);
            if (nextNewline !== -1) {
                executionLogs[sId] = executionLogs[sId].substring(nextNewline + 1);
            } else {
                executionLogs[sId] = executionLogs[sId].slice(-maxLength);
            }
        }
    }

    function connectWS() {
        if (!browser) return;
        wsStatus = 'connecting';
        void (async () => {
            const token = await getApiToken();
            const wsUrl = buildWsUrl('/api/ws', token);
            try {
                ws = new WebSocket(wsUrl);
            } catch (err) {
                console.error('WebSocket construction failed', err);
                wsStatus = 'disconnected';
                activeSkillStatus = 'stopped';
                if (wsReconnectTimer) clearTimeout(wsReconnectTimer);
                wsReconnectTimer = setTimeout(connectWS, 3000);
                return;
            }

            ws.onopen = () => {
                wsStatus = 'connected';
                ws?.send(JSON.stringify({ action: 'list_skills' }));
                ws?.send(JSON.stringify({ action: 'get_settings' }));
                ws?.send(JSON.stringify({ action: 'list_local_models' }));
            };

            ws.onclose = () => {
                wsStatus = 'disconnected';
                activeSkillStatus = 'stopped';
                if (wsReconnectTimer) clearTimeout(wsReconnectTimer);
                wsReconnectTimer = setTimeout(connectWS, 3000);
            };

            ws.onmessage = async (event) => {
            try {
                const data = JSON.parse(event.data);
                
                if (data.event === 'skills_list') {
                    allSkills = data.skills || [];
                    allSkills.forEach(skill => {
                        if (!skillConfigValues[skill.id]) {
                            skillConfigValues[skill.id] = {};
                            skill.configParams.forEach(p => {
                                skillConfigValues[skill.id][p.name] = p.default;
                            });
                        }
                    });
                }
                if (data.event === 'deploy_progress') {
                    const sId = data.skillId;
                    if (sId) {
                        isDeployingSkill[sId] = true;
                        if (!deployLogs[sId]) deployLogs[sId] = '';
                        if (data.message) {
                            deployLogs[sId] += data.message + '\n';
                            const parsedEntry = parseLogLine(`[deploy] ${data.message}`);
                            if (!structuredLogs[sId]) structuredLogs[sId] = [];
                            structuredLogs[sId] = [...structuredLogs[sId], parsedEntry].slice(-400);
                        }
                        if (data.stage === 'complete') {
                            deployProgress[sId] = 100;
                            isDeployingSkill[sId] = false;
                            ws?.send(JSON.stringify({ action: 'list_skills' }));
                        }
                    }
                }
                if (data.event === 'log') {
                    const sId = data.skillId;
                    if (sId) {
                        if (data.message) {
                            appendToExecutionLogs(sId, data.message);
                            const parsedEntry = parseLogLine(data.message);
                            if (!structuredLogs[sId]) structuredLogs[sId] = [];
                            structuredLogs[sId] = [...structuredLogs[sId], parsedEntry].slice(-400);
                        }
                    }
                }
                if (data.event === 'progress') {
                    const sId = data.skillId;
                    if (sId && data.message) {
                        const logMsg = `[Progress] [${data.stage || 'info'}] ${data.message}`;
                        appendToExecutionLogs(sId, logMsg);
                        const parsedEntry = parseLogLine(logMsg);
                        if (!structuredLogs[sId]) structuredLogs[sId] = [];
                        structuredLogs[sId] = [...structuredLogs[sId], parsedEntry].slice(-400);
                    }
                }
                if (data.event === 'perf_stats') {
                    // Update dynamic system telemetry from websocket stats
                    if (data.cpu !== undefined) {
                        hardwareStats.cpu = data.cpu;
                    }
                    if (data.gpu !== undefined) {
                        hardwareStats.gpu = data.gpu;
                    }
                    if (data.memory) {
                        hardwareStats.memory = {
                            used: parseFloat((data.memory.used / 1024 / 1024 / 1024).toFixed(1)),
                            total: parseFloat((data.memory.total / 1024 / 1024 / 1024).toFixed(1)),
                            percent: data.memory.percent
                        };
                    }
                    if (data.storage) {
                        hardwareStats.storage = {
                            used: parseFloat((data.storage.used / 1024 / 1024 / 1024).toFixed(1)),
                            total: parseFloat((data.storage.total / 1024 / 1024 / 1024).toFixed(1)),
                            percent: data.storage.percent
                        };
                    }

                    const sId = data.skillId;
                    if (sId) {
                        const avgTime = data.timings_ms?.total?.avg || 0;
                        const fpsVal = avgTime > 0 ? (1000 / avgTime).toFixed(1) : 'N/A';
                        const logMsg = `[Perf] Total Frames: ${data.total_frames} | FPS: ${fpsVal} (Inference: ${data.timings_ms?.inference?.avg || 0}ms)`;
                        appendToExecutionLogs(sId, logMsg);
                        const parsedEntry = parseLogLine(logMsg);
                        if (!structuredLogs[sId]) structuredLogs[sId] = [];
                        structuredLogs[sId] = [...structuredLogs[sId], parsedEntry].slice(-400);
                    }
                }
                if (data.event === 'threat_analysis') {
                    const sId = data.skillId;
                    if (sId && data.message) {
                        const logMsg = `[VLM] [${(data.alert_type || 'info').toUpperCase()}] ${data.message}`;
                        appendToExecutionLogs(sId, logMsg);
                        const parsedEntry = parseLogLine(logMsg);
                        if (!structuredLogs[sId]) structuredLogs[sId] = [];
                        structuredLogs[sId] = [...structuredLogs[sId], parsedEntry].slice(-400);
                    }
                }
                if (data.event === 'error') {
                    const sId = data.skillId;
                    if (sId && data.message) {
                        const logMsg = `[Error] ${data.message}`;
                        appendToExecutionLogs(sId, logMsg);
                        const parsedEntry = parseLogLine(logMsg);
                        if (!structuredLogs[sId]) structuredLogs[sId] = [];
                        structuredLogs[sId] = [...structuredLogs[sId], parsedEntry].slice(-400);
                    }
                }
                if (data.event === 'diagnostic_warning') {
                    if (!diagnosticWarnings.some(w => w.code === data.code)) {
                        diagnosticWarnings = [...diagnosticWarnings, { code: data.code, message: data.message }];
                    }
                }
                if (data.event === 'ready') {
                    activeSkillStatus = 'ready';
                    if (data.modules) {
                        activeModules = {
                            face_recognition: !!data.modules.face_recognition,
                            tracking: !!data.modules.tracking
                        };
                    }
                }
                if (data.event === 'stopped') {
                    activeSkillStatus = 'stopped';
                }
                
                // Native WebRTC handles live frame rendering directly in <video> elements.
                if (data.event === 'detections' && data.skillId === selectedSkillId) {
                    updateDetectionFps();
                    motionGatedCameras[data.cameraId] = !!data.motion_gated;
                    drawDetections(data.cameraId, data.objects || []);
                }

                // LOCAL MODELS AND SETTINGS SYNC EVENTS
                if (data.event === 'settings_data') {
                    providers = data.providers;
                    activeInference = data.activeInference;
                    localModels = data.localModels;
                }
                if (data.event === 'settings_saved') {
                    providers = data.providers;
                    activeInference = data.activeInference;
                }
                if (data.event === 'local_models_list') {
                    localModels = data.localModels;
                    // Reset selected model if it no longer exists
                    if (activeInference.vlm.modelId && !localModels.some(m => m.name === activeInference.vlm.modelId)) {
                        activeInference.vlm.modelId = null;
                    }
                }
                if (data.event === 'model_deleted') {
                    if (data.success) {
                        localModels = data.localModels;
                        // Reset selected model if it was deleted
                        if (activeInference.vlm.modelId === data.filename) {
                            activeInference.vlm.modelId = null;
                        }
                    } else {
                        alert(`Failed to delete model ${data.filename}`);
                    }
                }
                if (data.event === 'download_progress') {
                    activeDownloads = {
                        ...activeDownloads,
                        [data.downloadId]: {
                            percent: data.percent,
                            bytesDownloaded: data.bytesDownloaded,
                            totalBytes: data.totalBytes,
                            status: data.status,
                            error: data.message
                        }
                    };
                    if (data.status === 'completed') {
                        setTimeout(() => {
                            const copy = { ...activeDownloads };
                            delete copy[data.downloadId];
                            activeDownloads = copy;
                            ws?.send(JSON.stringify({ action: 'list_local_models' }));
                        }, 2000);
                    }
                }
            } catch (_) {}
        };
        })();
    }

    async function recorderAction(action: 'start_all' | 'stop_all') {
        const res = await fetch('/api/v1/recorders', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ action })
        });
        if (res.ok) await loadRecorders();
    }

    function startInference() {
        if (!ws || ws.readyState !== WebSocket.OPEN) return;
        activeSkillStatus = 'starting';
        ws.send(JSON.stringify({ action: 'start_skill', skillId: selectedSkillId, config: skillConfigValues[selectedSkillId] || {} }));
    }

    function stopInference() {
        ws?.send(JSON.stringify({ action: 'stop_skill', skillId: selectedSkillId }));
        activeSkillStatus = 'stopped';
    }

    async function toggleSurveillance() {
        if (isSurveillanceToggling) return;
        isSurveillanceToggling = true;
        try {
            const isActive = (activeSkillStatus === 'ready' || activeSkillStatus === 'starting') || activeRecorders > 0;
            if (isActive) {
                stopInference();
                await recorderAction('stop_all');
            } else {
                startInference();
                await recorderAction('start_all');
            }
        } catch (err) {
            console.error('[Surveillance Toggle] Error toggling surveillance systems:', err);
        } finally {
            isSurveillanceToggling = false;
        }
    }

    function deploySkill(skillId: string) {
        if (!ws || ws.readyState !== WebSocket.OPEN) return;
        deployLogs[skillId] = 'Deploy started...\n';
        deployProgress[skillId] = 5;
        isDeployingSkill[skillId] = true;
        ws.send(JSON.stringify({ action: 'deploy_skill', skillId, config: skillConfigValues[skillId] || {} }));
    }

    // Logs structuring and parsing helper
    function parseLogLine(rawLine: string): LogEntry {
        const now = new Date();
        const timeStr = now.toLocaleTimeString([], { hour12: false }) + '.' + String(now.getMilliseconds()).padStart(3, '0');
        
        let source: 'stdout' | 'stderr' = 'stdout';
        let cleanLine = rawLine.trim();
        let tag = '';
        
        if (cleanLine.startsWith('[stderr]')) {
            source = 'stderr';
            cleanLine = cleanLine.substring(8).trim();
        } else if (cleanLine.startsWith('[err]')) {
            source = 'stderr';
            cleanLine = cleanLine.substring(5).trim();
        }
        
        // Extract tag like [Progress], [Perf], [VLM], [deploy], [Perception], etc.
        const tagMatch = cleanLine.match(/^\[([a-zA-Z0-9_-]+)\]\s*/);
        if (tagMatch) {
            tag = tagMatch[1];
            if (tag.toLowerCase() !== 'stderr' && tag.toLowerCase() !== 'err') {
                cleanLine = cleanLine.substring(tagMatch[0].length).trim();
            } else {
                tag = '';
            }
        }
        
        cleanLine = cleanLine.replace(/^\[[a-zA-Z0-9_-]+\]\s*/, '');
        
        let level: LogEntry['level'] = 'info';
        const lower = cleanLine.toLowerCase();
        const tagLower = tag.toLowerCase();
        
        if (source === 'stderr') {
            if (lower.includes('error') || lower.includes('exception') || lower.includes('fail') || lower.includes('traceback')) {
                level = 'error';
            } else {
                level = 'warn';
            }
        } else {
            if (lower.includes('error') || lower.includes('exception') || lower.includes('fail') || lower.includes('traceback') || tagLower === 'error') {
                level = 'error';
            } else if (lower.includes('warn') || lower.includes('warning') || lower.includes('attention') || tagLower === 'warn') {
                level = 'warn';
            } else if (lower.includes('success') || lower.includes('ready') || lower.includes('connected') || lower.includes('compiled') || tagLower === 'success') {
                level = 'success';
            }
        }
        
        if (tagLower === 'progress' || tagLower === 'deploy') {
            if (lower.includes('error') || lower.includes('fail')) {
                level = 'error';
            } else if (lower.includes('complete') || lower.includes('success') || lower.includes('installed')) {
                level = 'success';
            } else {
                level = 'info';
            }
        } else if (tagLower === 'perf') {
            level = 'success';
        }
        
        return {
            timestamp: timeStr,
            source,
            level,
            tag: tag || undefined,
            message: cleanLine
        };
    }

    // Scroll Lock Auto-Scroll Effect
    $effect(() => {
        if (terminalElement && scrollLock) {
            const logsLength = (structuredLogs[selectedSkillId] || []).length;
            if (logsLength > 0) {
                tick().then(() => {
                    if (terminalElement) {
                        terminalElement.scrollTop = terminalElement.scrollHeight;
                    }
                });
            }
        }
    });

    // Model Downloader & Switcher triggers
    function downloadModel(repo: string, filename: string, isVlm: boolean) {
        if (!ws || ws.readyState !== WebSocket.OPEN) return;
        ws.send(JSON.stringify({
            action: 'download_model',
            repo,
            filename,
            isVlm
        }));
    }

    function selectVlmModel(modelName: string) {
        if (!activeInference) return;
        activeInference.vlm.type = 'local-engine';
        activeInference.vlm.modelId = (modelName === 'null' || modelName === '' || !modelName) ? null : modelName;
        
        ws?.send(JSON.stringify({
            action: 'save_settings',
            providers,
            activeInference
        }));
    }

    function deleteModel(filename: string, isVlm: boolean) {
        if (!confirm(`Are you sure you want to delete ${filename}?`)) return;
        ws?.send(JSON.stringify({
            action: 'delete_model',
            filename,
            isVlm
        }));
    }
</script>

<svelte:head>
    <title>Hawkeye Live Grid</title>
</svelte:head>

<div class="flex flex-col gap-6 w-full max-w-[1400px] mx-auto min-h-full">
    <!-- Header -->
    <header class="flex flex-col md:flex-row justify-between items-start md:items-center gap-4 bg-card border border-border/80 rounded-2xl p-5 shadow-sm shrink-0">
        <div>
            <h1 class="text-xl font-bold tracking-tight text-foreground font-display">Live Grid</h1>
            <p class="text-xs text-muted-foreground mt-0.5 font-sans">Hawkeye real-time surveillance monitoring and AI pipelines.</p>
        </div>
    </header>

    {#if diagnosticWarnings.length > 0}
        <section class="flex flex-col gap-3 shrink-0">
            {#each diagnosticWarnings as warning}
                <div class="flex items-start justify-between gap-4 p-4 border border-rose-500/20 bg-rose-500/10 text-rose-200 rounded-xl text-xs shadow-sm animate-in slide-in-from-top duration-200">
                    <div class="flex items-start gap-3">
                        <span class="text-base select-none mt-0.5">⚠️</span>
                        <div class="flex flex-col gap-0.5">
                            <span class="font-bold text-foreground capitalize">
                                {warning.code.replace(/_/g, ' ')}
                            </span>
                            <p class="text-rose-300/90 leading-relaxed font-sans">{warning.message}</p>
                        </div>
                    </div>
                    <div class="flex items-center gap-2 shrink-0">
                        {#if warning.code === 'CUDA_FALLBACK_TO_CPU'}
                            <button 
                                class="bg-rose-500 hover:bg-rose-600 text-white font-semibold py-1 px-3 rounded-lg border border-rose-600/30 transition-all cursor-pointer select-none"
                                onclick={() => {
                                    diagnosticWarnings = diagnosticWarnings.filter(w => w.code !== warning.code);
                                    deploySkill('perception-core');
                                }}
                            >
                                Re-run GPU Setup
                            </button>
                        {/if}
                        {#if warning.code === 'FACE_RECOG_LOAD_FAILED'}
                            <button 
                                class="bg-rose-500 hover:bg-rose-600 text-white font-semibold py-1 px-3 rounded-lg border border-rose-600/30 transition-all cursor-pointer select-none"
                                onclick={() => {
                                    diagnosticWarnings = diagnosticWarnings.filter(w => w.code !== warning.code);
                                    deploySkill('perception-core');
                                }}
                            >
                                Reinstall Models
                            </button>
                        {/if}
                        <button 
                            class="bg-muted border border-border hover:bg-accent text-muted-foreground hover:text-foreground font-semibold py-1 px-2.5 rounded-lg transition-all cursor-pointer select-none"
                            onclick={() => {
                                diagnosticWarnings = diagnosticWarnings.filter(w => w.code !== warning.code);
                            }}
                        >
                            Dismiss
                        </button>
                    </div>
                </div>
            {/each}
        </section>
    {/if}

    <div class="flex flex-col gap-6 w-full animate-in fade-in duration-200">
        <!-- Telemetry bar -->
        <section class="grid grid-cols-2 md:grid-cols-5 gap-4 border border-border bg-card/65 rounded-xl p-4 shadow-sm text-xs font-semibold shrink-0">
            <div class="flex flex-col gap-1 pl-1">
                <span class="text-[9px] text-muted-foreground uppercase tracking-wider font-bold">Core Telemetry</span>
                <span class="font-bold text-foreground capitalize flex items-center gap-1">
                    <span class="w-1.5 h-1.5 rounded-full {wsStatus === 'connected' ? 'bg-emerald-400 animate-pulse' : 'bg-rose-400'}"></span>
                    {wsStatus}
                </span>
            </div>
            <div class="flex flex-col gap-1 border-l border-border/60 pl-3">
                <span class="text-[9px] text-muted-foreground uppercase tracking-wider font-bold">Analytics Engine</span>
                <span class="font-bold text-foreground capitalize flex items-center gap-1.5">
                    {activeSkillStatus}
                    {#if activeSkillStatus === 'ready'}
                        {#if activeModules.face_recognition}
                            <span class="w-1.5 h-1.5 rounded-full bg-indigo-400 animate-pulse" title="Face Re-ID Active"></span>
                        {:else}
                            <span class="w-1.5 h-1.5 rounded-full bg-zinc-500" title="Face Re-ID Inactive"></span>
                        {/if}
                    {/if}
                </span>
            </div>
            <div class="flex flex-col gap-1 border-l border-border/60 pl-3">
                <span class="text-[9px] text-muted-foreground uppercase tracking-wider font-bold">Active Feeds</span>
                <span class="font-bold text-foreground">{enabledCameras.length} operational</span>
            </div>
            <div class="flex flex-col gap-1 border-l border-border/60 pl-3">
                <span class="text-[9px] text-muted-foreground uppercase tracking-wider font-bold">NVR Archiver</span>
                <span class="font-bold text-foreground flex items-center gap-1">
                    {#if activeRecorders > 0}
                        <span class="w-1.5 h-1.5 rounded-full bg-rose-500 animate-pulse"></span>
                    {/if}
                    {activeRecorders} continuous
                </span>
            </div>
            <div class="flex flex-col gap-1 border-l border-border/60 pl-3">
                <span class="text-[9px] text-muted-foreground uppercase tracking-wider font-bold">Analytics FPS</span>
                <span class="font-bold text-primary">{activeSkillStatus === 'ready' ? `${detectionFps} FPS` : '0 FPS'}</span>
            </div>
        </section>

        <!-- Controls panel -->
        <section class="flex flex-wrap items-center justify-between gap-3 bg-card border border-border rounded-xl p-4 shadow-sm shrink-0">
            <div class="flex flex-wrap items-center gap-3">
                <span class="text-xs font-bold text-muted-foreground uppercase tracking-wider select-none">System Control</span>
                
                {#if isSurveillanceToggling}
                    <button class="btn disabled py-1.5 px-4 text-xs font-bold bg-muted text-muted-foreground cursor-not-allowed flex items-center gap-2" disabled>
                        <span class="w-3 h-3 rounded-full border-2 border-muted-foreground border-t-transparent animate-spin"></span>
                        Initializing...
                    </button>
                {:else if isSurveillanceActive}
                    <button 
                        class="py-1.5 px-4 text-xs font-extrabold text-white rounded-xl bg-gradient-to-r from-rose-600 to-red-500 hover:from-rose-500 hover:to-red-400 shadow-[0_0_15px_rgba(239,68,68,0.35)] hover:shadow-[0_0_20px_rgba(239,68,68,0.5)] transition-all duration-300 transform active:scale-95 cursor-pointer flex items-center gap-1.5 animate-none"
                        onclick={toggleSurveillance}
                    >
                        <span class="w-1.5 h-1.5 rounded-full bg-white animate-ping"></span>
                        🔴 Stop Surveillance
                    </button>
                {:else}
                    <button 
                        class="py-1.5 px-4 text-xs font-extrabold text-white rounded-xl bg-gradient-to-r from-emerald-600 to-teal-500 hover:from-emerald-500 hover:to-teal-400 shadow-[0_0_15px_rgba(16,185,129,0.35)] hover:shadow-[0_0_20px_rgba(16,185,129,0.5)] transition-all duration-300 transform active:scale-95 cursor-pointer flex items-center gap-1.5 animate-none"
                        onclick={toggleSurveillance}
                    >
                        🛡️ Start Surveillance
                    </button>
                {/if}

                {#if activeSkillStatus === 'ready'}
                    <div class="flex items-center gap-2 border-l border-border/80 pl-3">
                        {#if activeModules.face_recognition}
                            <span class="bg-indigo-500/10 border border-indigo-500/20 text-indigo-400 text-[10px] font-bold px-2 py-0.5 rounded-lg flex items-center gap-1 shadow-[0_0_8px_rgba(99,102,241,0.25)] select-none font-sans">
                                👤 Face Re-ID Enabled
                            </span>
                        {:else}
                            <span class="bg-zinc-800 border border-zinc-700/60 text-zinc-500 text-[10px] font-bold px-2 py-0.5 rounded-lg flex items-center gap-1 select-none font-sans">
                                👤 Face Re-ID Disabled
                            </span>
                        {/if}
                        
                        {#if activeModules.tracking}
                            <span class="bg-blue-500/10 border border-blue-500/20 text-blue-400 text-[10px] font-bold px-2 py-0.5 rounded-lg flex items-center gap-1 shadow-[0_0_8px_rgba(59,130,246,0.25)] select-none font-sans">
                                🎯 Object Tracking Enabled
                            </span>
                        {/if}
                    </div>
                {/if}
            </div>

            <div class="flex flex-wrap items-center gap-2">
                <button class="btn py-1 px-3 text-xs animate-none font-bold" class:active={showOverlays} onclick={() => showOverlays = !showOverlays}>
                    🛡️ Overlays: {showOverlays ? 'On' : 'Off'}
                </button>

                {#if allSkills.length > 0}
                    <select class="select py-1 px-2 text-xs font-sans font-semibold cursor-pointer" bind:value={selectedSkillId} disabled={activeSkillStatus === 'ready' || activeSkillStatus === 'starting'}>
                        {#each allSkills.filter(s => ['detection', 'analysis', 'transformation', 'privacy'].includes(s.category)) as skill}
                            <option value={skill.id}>{skill.name}</option>
                        {/each}
                    </select>
                {/if}
            </div>
        </section>

        <!-- Camera viewports grid -->
        <main class="w-full bg-black/60 border border-border/80 rounded-2xl overflow-hidden p-4 min-h-[420px] flex items-center justify-center relative shadow-inner">
            {#if enabledCameras.length === 0}
                <div class="text-center p-8 flex flex-col items-center gap-3">
                    <div class="w-12 h-12 rounded-full bg-muted border border-border flex items-center justify-center text-muted-foreground shadow-sm">
                        <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><path d="m22 8-6 4 6 4V8Z"/><rect width="14" height="12" x="2" y="6" rx="2" ry="2"/></svg>
                    </div>
                    <div>
                        <h3 class="text-sm font-bold text-foreground">Surveillance Feeds Offline</h3>
                        <p class="text-xs text-muted-foreground mt-0.5">Enable webcams or RTSP cameras from Settings to see streams.</p>
                    </div>
                </div>
            {:else}
                <div class="grid gap-4 w-full h-full {gridLayout === '1x1' || focusedCameraId ? 'grid-cols-1' : gridLayout === '2x2' ? 'grid-cols-2' : gridLayout === '3x3' ? 'grid-cols-3' : gridLayout === '4x4' ? 'grid-cols-4' : 'grid-cols-1 md:grid-cols-2 lg:grid-cols-3'}">
                    {#each visibleCameras as camera}
                        {#if camera.source === 'rtsp'}
                            <UnifiedPlayer {camera} />
                        {:else}
                            {@const recorder = recorders.find(r => r.cameraId === camera.id)}
                            <article class="flex flex-col bg-card border border-border/80 rounded-xl overflow-hidden shadow-md group relative">
                                <div 
                                    class="relative w-full aspect-video bg-black overflow-hidden hover:border-muted-foreground/30 transition-all cursor-zoom-in" 
                                    onclick={() => focusedCameraId = focusedCameraId === camera.id ? null : camera.id}
                                    title="Zoom Stream"
                                    role="button"
                                    tabindex="0"
                                    onkeydown={(e) => { if (e.key === 'Enter') focusedCameraId = focusedCameraId === camera.id ? null : camera.id; }}
                                >
                                    {#if camera.source === 'webcam'}
                                        <video bind:this={webcamVideos[camera.id]} autoplay playsinline muted class="absolute inset-0 w-full h-full object-contain"></video>
                                    {:else}
                                        <canvas bind:this={cameraCanvases[camera.id]} class="absolute inset-0 w-full h-full object-contain"></canvas>
                                    {/if}
                                    <canvas bind:this={overlayCanvases[camera.id]} class="absolute inset-0 w-full h-full object-contain pointer-events-none"></canvas>
                                    
                                    {#if activeSkillStatus === 'ready'}
                                        <div class="absolute left-3 top-3 flex gap-1.5 z-20 font-sans text-[9px]">
                                            {#if motionGatedCameras[camera.id]}
                                                <span class="bg-amber-500/90 backdrop-blur-md border border-amber-400/30 px-2 py-0.5 rounded-lg text-black font-extrabold flex items-center gap-1 shadow-[0_0_8px_rgba(245,158,11,0.4)] animate-pulse select-none font-sans">
                                                    🔋 Power Saving (Static)
                                                </span>
                                            {:else}
                                                <span class="bg-emerald-500/90 backdrop-blur-md border border-emerald-400/30 px-2 py-0.5 rounded-lg text-white font-extrabold flex items-center gap-1 shadow-[0_0_8px_rgba(16,185,129,0.4)] select-none font-sans">
                                                    <span class="w-1.5 h-1.5 rounded-full bg-white animate-ping"></span>
                                                    ⚡ Processing Live
                                                </span>
                                            {/if}
                                        </div>
                                    {/if}

                                    {#if activePtzCameraId === camera.id}
                                        <div 
                                            class="absolute inset-0 bg-black/45 backdrop-blur-xs flex items-center justify-center z-30 transition-all"
                                            onclick={(e) => e.stopPropagation()}
                                            role="none"
                                        >
                                            <div class="bg-card/90 border border-border/80 rounded-2xl p-4 flex flex-col gap-3 items-center shadow-lg relative min-w-[150px]">
                                                <button 
                                                    type="button"
                                                    onclick={() => activePtzCameraId = null}
                                                    class="absolute right-2 top-2 w-5 h-5 rounded-full flex items-center justify-center bg-muted border border-border text-muted-foreground hover:text-foreground text-[10px] cursor-pointer"
                                                >
                                                    ×
                                                </button>
                                                <span class="text-[9px] font-bold uppercase tracking-wider text-muted-foreground select-none">PTZ Pad</span>
                                                
                                                <div class="grid grid-cols-3 gap-1 w-28 h-28 relative">
                                                    <div></div>
                                                    <button 
                                                        type="button"
                                                        onmousedown={() => sendPtzCommand(camera.id, 'move', 0, 0.5)}
                                                        onmouseup={() => sendPtzCommand(camera.id, 'stop')}
                                                        onmouseleave={() => sendPtzCommand(camera.id, 'stop')}
                                                        class="bg-muted hover:bg-accent border border-border rounded flex items-center justify-center font-bold text-xs cursor-pointer select-none"
                                                        title="Move Up"
                                                    >▲</button>
                                                    <div></div>

                                                    <button 
                                                        type="button"
                                                        onmousedown={() => sendPtzCommand(camera.id, 'move', -0.5, 0)}
                                                        onmouseup={() => sendPtzCommand(camera.id, 'stop')}
                                                        onmouseleave={() => sendPtzCommand(camera.id, 'stop')}
                                                        class="bg-muted hover:bg-accent border border-border rounded flex items-center justify-center font-bold text-xs cursor-pointer select-none"
                                                        title="Move Left"
                                                    >◀</button>
                                                    <button 
                                                        type="button"
                                                        onclick={() => sendPtzCommand(camera.id, 'stop')}
                                                        class="bg-rose-500/10 border border-rose-500/20 rounded flex items-center justify-center text-rose-400 font-bold text-[9px] cursor-pointer select-none"
                                                        title="Stop PTZ"
                                                    >STOP</button>
                                                    <button 
                                                        type="button"
                                                        onmousedown={() => sendPtzCommand(camera.id, 'move', 0.5, 0)}
                                                        onmouseup={() => sendPtzCommand(camera.id, 'stop')}
                                                        onmouseleave={() => sendPtzCommand(camera.id, 'stop')}
                                                        class="bg-muted hover:bg-accent border border-border rounded flex items-center justify-center font-bold text-xs cursor-pointer select-none"
                                                        title="Move Right"
                                                    >▶</button>

                                                    <div></div>
                                                    <button 
                                                        type="button"
                                                        onmousedown={() => sendPtzCommand(camera.id, 'move', 0, -0.5)}
                                                        onmouseup={() => sendPtzCommand(camera.id, 'stop')}
                                                        onmouseleave={() => sendPtzCommand(camera.id, 'stop')}
                                                        class="bg-muted hover:bg-accent border border-border rounded flex items-center justify-center font-bold text-xs cursor-pointer select-none"
                                                        title="Move Down"
                                                    >▼</button>
                                                    <div></div>
                                                </div>

                                                <div class="flex gap-2 w-full">
                                                    <button 
                                                        type="button"
                                                        onmousedown={() => sendPtzCommand(camera.id, 'move', 0, 0, 0.5)}
                                                        onmouseup={() => sendPtzCommand(camera.id, 'stop')}
                                                        onmouseleave={() => sendPtzCommand(camera.id, 'stop')}
                                                        class="flex-1 py-1 bg-muted hover:bg-accent border border-border rounded text-[10px] font-bold cursor-pointer select-none"
                                                    >
                                                        Zoom +
                                                    </button>
                                                    <button 
                                                        type="button"
                                                        onmousedown={() => sendPtzCommand(camera.id, 'move', 0, 0, -0.5)}
                                                        onmouseup={() => sendPtzCommand(camera.id, 'stop')}
                                                        onmouseleave={() => sendPtzCommand(camera.id, 'stop')}
                                                        class="flex-1 py-1 bg-muted hover:bg-accent border border-border rounded text-[10px] font-bold cursor-pointer select-none"
                                                    >
                                                        Zoom -
                                                    </button>
                                                </div>
                                            </div>
                                        </div>
                                    {/if}

                                    <div class="absolute left-3 bottom-3 flex gap-2 font-mono text-[9px] z-10">
                                        <span class="bg-black/75 border border-border/40 px-2 py-0.5 rounded text-white flex items-center gap-1">
                                            <span class="w-1.5 h-1.5 rounded-full {recorder?.state === 'recording' ? 'bg-rose-500 animate-pulse' : 'bg-zinc-500'}"></span>
                                            {recorder?.state || 'stopped'}
                                        </span>
                                        <span class="bg-black/75 border border-border/40 px-2 py-0.5 rounded text-white">{recorder?.indexedSegments || 0} segments</span>
                                    </div>
                                </div>

                                <div class="px-4 py-2.5 flex justify-between items-center border-t border-border shrink-0 bg-muted/5">
                                    <div class="min-w-0">
                                        <strong class="text-xs font-bold text-foreground block truncate">{camera.name}</strong>
                                        <span class="text-[10px] text-muted-foreground block truncate">{camera.source} feed</span>
                                    </div>
                                    {#if recorder?.message}
                                        <span class="text-[9px] text-muted-foreground font-semibold px-2 py-0.5 rounded border border-border bg-background truncate max-w-[120px]">{recorder.message}</span>
                                    {/if}
                                </div>
                            </article>
                        {/if}
                    {/each}
                </div>
            {/if}
        </main>

        <!-- Config Panel & Realtime Logs Console -->
        <div class="grid grid-cols-1 lg:grid-cols-12 gap-6 w-full">
            <div class="lg:col-span-6 flex flex-col gap-6">
                <!-- Skill Config Form -->
                <section class="bg-card border border-border/80 rounded-xl p-5 shadow-sm flex flex-col gap-4">
                    {#if selectedSkill}
                        {@const skill = selectedSkill}
                        {@const isRunning = activeSkillStatus === 'ready' || activeSkillStatus === 'starting'}
                        {@const activeDl = isDeployingSkill[skill.id]}
                        
                        <div class="border-b border-border pb-3 flex justify-between items-start">
                            <div>
                                <span class="text-[9px] font-bold uppercase tracking-wider px-2 py-0.5 rounded border bg-indigo-500/10 border-indigo-500/20 text-indigo-400 font-sans">{skill.category}</span>
                                <h3 class="text-sm font-bold text-foreground mt-1.5">{skill.name}</h3>
                                <p class="text-xs text-muted-foreground mt-1 leading-normal">{skill.description}</p>
                            </div>
                            <span class="w-2.5 h-2.5 rounded-full shrink-0 mt-1 {isRunning ? 'bg-emerald-500 shadow-[0_0_8px_#10b981]' : skill.isInstalled ? 'bg-blue-500 shadow-[0_0_8px_#3b82f6]' : 'bg-muted-foreground'}"></span>
                        </div>

                        <div class="flex flex-col gap-4 max-h-[380px] overflow-y-auto pr-1">
                            {#if skill.configParams && skill.configParams.length > 0}
                                {#each skill.configParams as param}
                                    <div class="flex flex-col gap-1.5 text-xs">
                                        <label class="text-xs font-semibold text-muted-foreground" for="param-{param.name}">{param.label}</label>
                                        {#if param.type === 'select'}
                                            <select id="param-{param.name}" bind:value={skillConfigValues[skill.id][param.name]} disabled={isRunning} class="select w-full font-sans">
                                                {#each param.options || [] as opt}
                                                    <option value={typeof opt === 'object' ? opt.value : opt}>
                                                        {typeof opt === 'object' ? opt.label : opt}
                                                    </option>
                                                {/each}
                                            </select>
                                        {:else if param.type === 'boolean'}
                                            <label class="flex items-center gap-2 cursor-pointer py-1 select-none font-sans">
                                                <input type="checkbox" bind:checked={skillConfigValues[skill.id][param.name]} disabled={isRunning} class="w-4 h-4 rounded bg-background border-border text-primary cursor-pointer animate-none" />
                                                <span class="text-xs text-foreground font-medium">Enable feature toggle</span>
                                            </label>
                                        {:else if param.type === 'number'}
                                            <input type="number" id="param-{param.name}" bind:value={skillConfigValues[skill.id][param.name]} min={param.min} max={param.max} disabled={isRunning} class="input font-sans text-xs" />
                                        {:else}
                                            <input type="text" id="param-{param.name}" bind:value={skillConfigValues[skill.id][param.name]} disabled={isRunning} class="input font-sans text-xs" />
                                        {/if}
                                        {#if param.description}
                                            <p class="text-[10px] text-muted-foreground leading-normal font-sans mt-0.5">{param.description}</p>
                                        {/if}
                                    </div>
                                {/each}
                            {:else}
                                <p class="text-xs text-muted-foreground text-center p-4 border border-dashed border-border rounded-lg font-sans">No configurable variables for this skill.</p>
                            {/if}
                        </div>

                        <div class="pt-3 border-t border-border mt-auto">
                            {#if !skill.isInstalled}
                                <button class="btn primary w-full py-2 font-bold animate-none cursor-pointer" disabled={activeDl} onclick={() => deploySkill(skill.id)}>
                                    {activeDl ? '🔧 Executing build pipeline...' : '🔧 Install Skill & Virtualenv'}
                                </button>
                            {:else}
                                <div class="text-xs font-semibold text-emerald-400 bg-emerald-500/10 border border-emerald-500/20 rounded-lg p-3 text-center font-sans">
                                    ✓ Skill Ready & Installed
                                </div>
                            {/if}
                        </div>
                    {:else}
                        <p class="text-xs text-muted-foreground text-center p-8 font-sans">Loading configurations...</p>
                    {/if}
                </section>

                <!-- Moondream VLM Downloader -->
                {#if selectedSkillId === 'visual-event-analyzer'}
                    {@const recommendedQuants = [
                        { name: 'Moondream2 0.5B - F16 (Default)', repo: 'moondream/moondream2-gguf', file: 'moondream2-text-model-f16.gguf', size: '950MB', desc: 'Full precision 0.5B parameter VLM model, high-fidelity' },
                        { name: 'Moondream2 0.5B - Q4 Quant (Highly Compact)', repo: 'Christian-W/moondream2-gguf', file: 'moondream2-text-model-q4_k_m.gguf', size: '300MB', desc: '4-bit quantized 0.5B parameter VLM, extremely fast, low memory' },
                        { name: 'Moondream2 0.5B - Q8 Quant (Balanced)', repo: 'Christian-W/moondream2-gguf', file: 'moondream2-text-model-q8_0.gguf', size: '550MB', desc: '8-bit quantized 0.5B parameter VLM, excellent speed/precision balance' }
                    ]}
                    <section class="bg-card border border-border/80 rounded-xl p-5 shadow-sm flex flex-col gap-5 animate-in slide-in-from-bottom-3 duration-250">
                        <div class="border-b border-border pb-3">
                            <span class="text-[9px] font-bold uppercase tracking-wider px-2 py-0.5 rounded border bg-indigo-500/10 border-indigo-500/20 text-indigo-400 font-sans">Moondream Integration</span>
                            <h3 class="text-sm font-bold text-foreground mt-1.5">Moondream Local Engine Model</h3>
                            <p class="text-xs text-muted-foreground mt-1 leading-normal font-sans">Choose which Moondream model you want to run locally. You can download optimized GGUF quants directly from HuggingFace to your machine.</p>
                        </div>

                        <div class="flex flex-col gap-1.5 text-xs font-sans">
                            <label class="text-xs font-semibold text-muted-foreground" for="vlm-model-select">Selected Local VLM Model</label>
                            <div class="flex gap-2">
                                <select 
                                    id="vlm-model-select" 
                                    class="select flex-1 text-xs font-sans" 
                                    bind:value={activeInference.vlm.modelId}
                                    onchange={(e) => selectVlmModel((e.target as HTMLSelectElement).value)}
                                >
                                    <option value={null}>-- Use default local VLM (llava) --</option>
                                    {#each localModels.filter(m => m.isVlm) as model}
                                        <option value={model.name}>{model.name} ({Math.round(model.sizeBytes / (1024 * 1024))} MB)</option>
                                    {/each}
                                </select>
                                <button class="btn text-xs bg-muted border border-border text-foreground hover:bg-accent font-semibold px-3 py-1 animate-none cursor-pointer" onclick={() => ws?.send(JSON.stringify({ action: 'list_local_models' }))}>
                                    🔄 Refresh
                                </button>
                            </div>
                        </div>

                        <div class="flex flex-col gap-3 font-sans">
                            <span class="text-[10px] font-bold text-muted-foreground uppercase tracking-wider">Recommended Moondream Model Quants</span>
                            
                            <div class="flex flex-col gap-2">
                                {#each recommendedQuants as q}
                                    {@const isDownloaded = localModels.some(m => m.name === q.file)}
                                    {@const downloadId = `${q.repo}/${q.file}`}
                                    {@const downloadInfo = activeDownloads[downloadId]}

                                    <div class="flex flex-col border border-border/80 bg-muted/5 rounded-lg p-3 gap-2.5 text-xs transition-colors hover:border-muted-foreground/20">
                                        <div class="flex justify-between items-start gap-2">
                                            <div class="min-w-0">
                                                <strong class="text-xs font-bold text-foreground block">{q.name}</strong>
                                                <span class="text-[10px] text-muted-foreground mt-0.5 leading-snug block">{q.desc}</span>
                                            </div>
                                            
                                            {#if isDownloaded}
                                                <div class="flex items-center gap-1.5 shrink-0">
                                                    <span class="bg-emerald-500/10 border border-emerald-500/20 text-emerald-400 text-[9px] font-bold px-2 py-0.5 rounded select-none font-sans">✓ Ready</span>
                                                    <button class="btn text-[9px] py-0.5 px-2 bg-rose-500/10 hover:bg-rose-500/20 border border-rose-500/20 text-rose-400 font-bold shrink-0 animate-none cursor-pointer" onclick={() => deleteModel(q.file, true)}>
                                                        🗑 Delete
                                                    </button>
                                                </div>
                                            {:else if downloadInfo}
                                                <span class="bg-indigo-500/10 border border-indigo-500/20 text-indigo-400 text-[9px] font-bold px-2 py-0.5 rounded select-none shrink-0 animate-pulse font-mono">{downloadInfo.percent}%</span>
                                            {:else}
                                                <button class="btn text-[9px] py-0.5 px-2 bg-indigo-600 hover:bg-indigo-500 text-white font-bold shrink-0 animate-none cursor-pointer" onclick={() => downloadModel(q.repo, q.file, true)}>
                                                    🔽 Download ({q.size})
                                                </button>
                                            {/if}
                                        </div>

                                        {#if downloadInfo}
                                            <div class="flex flex-col gap-1 mt-1 font-mono text-[9px] text-muted-foreground">
                                                <div class="w-full bg-muted rounded-full h-1.5 overflow-hidden border border-border/40">
                                                    <div class="bg-indigo-500 h-full shadow-[0_0_8px_#6366f1] transition-all duration-300" style="width: {downloadInfo.percent}%"></div>
                                                </div>
                                                <div class="flex justify-between mt-0.5 select-none text-[8px]">
                                                    <span>{downloadInfo.status === 'completed' ? 'Finalizing GGUF write...' : `Downloading... ${downloadInfo.percent}%`}</span>
                                                    <span>{Math.round(downloadInfo.bytesDownloaded / (1024 * 1024))} MB / {Math.round(downloadInfo.totalBytes / (1024 * 1024))} MB</span>
                                                </div>
                                            </div>
                                        {/if}
                                    </div>
                                {/each}
                            </div>
                        </div>

                        <div class="bg-muted/10 border border-border rounded-xl p-3.5 flex flex-col gap-2.5 text-xs font-sans">
                            <span class="font-bold text-foreground flex items-center gap-1.5">
                                <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><path d="M12 2v20"/><path d="M17 5H9.5a3.5 3.5 0 0 0 0 7h5a3.5 3.5 0 0 1 0 7H6"/></svg>
                                Customizing Moondream Triggers
                            </span>
                            <p class="text-[10px] text-muted-foreground leading-normal mt-0.5">
                                Moondream triggers let you set up custom alerts in natural language. For example:
                            </p>
                            <ul class="list-disc pl-4 text-[9px] text-muted-foreground leading-normal flex flex-col gap-1.5 select-text">
                                <li><strong>Property Protection:</strong> Trigger Prompt: <i>"is a delivery package left on the porch? yes or no"</i>. Match: <i>"yes"</i>. Notification: <i>"📦 Package has arrived!"</i></li>
                                <li><strong>Access Alerts:</strong> Trigger Prompt: <i>"is the person wearing a security helmet? yes or no"</i>. Match: <i>"no"</i>. Notification: <i>"⚠️ safety warning: person on site without helmet!"</i></li>
                            </ul>
                        </div>
                    </section>
                {/if}
            </div>

            <div class="lg:col-span-6 flex flex-col gap-6">
                {#if selectedSkill}
                    {@const skill = selectedSkill}
                    {@const rawList = structuredLogs[skill.id] || []}
                    {@const filteredList = rawList.filter(log => {
                        const matchesFilter = logLevelFilter === 'all' || log.level === logLevelFilter;
                        const matchesSearch = !logSearchTerm.trim() || log.message.toLowerCase().includes(logSearchTerm.toLowerCase());
                        return matchesFilter && matchesSearch;
                    })}
                    
                    {#if deployLogs[skill.id]}
                        <div class="bg-card border border-border/80 rounded-xl p-5 shadow-sm flex flex-col gap-3 shrink-0 animate-in fade-in duration-200">
                            <h4 class="text-xs font-bold uppercase tracking-wider text-foreground">Skill Compilation / Virtualenv stdout</h4>
                            <div class="bg-black border border-border rounded-lg p-3 font-mono text-[10px] text-muted-foreground overflow-y-auto max-h-[160px] leading-relaxed break-all select-text">
                                <pre>{deployLogs[skill.id]}</pre>
                            </div>
                        </div>
                    {/if}

                    <div class="bg-card border border-border/80 rounded-xl p-5 shadow-sm flex flex-col gap-3 flex-1">
                        <div class="flex flex-col sm:flex-row justify-between items-start sm:items-center gap-2 border-b border-border pb-3 shrink-0">
                            <div class="flex items-center gap-2">
                                <h4 class="text-xs font-bold uppercase tracking-wider text-foreground font-display">Real-Time Perception Terminal</h4>
                                {#if activeSkillStatus === 'ready'}
                                    <span class="w-1.5 h-1.5 rounded-full bg-emerald-400 animate-ping"></span>
                                {/if}
                            </div>
                            
                            <div class="flex flex-wrap items-center gap-2 text-[10px] w-full sm:w-auto font-sans">
                                <input 
                                    type="text" 
                                    bind:value={logSearchTerm} 
                                    placeholder="Filter logs..." 
                                    class="bg-background text-foreground border border-border rounded px-2 py-0.5 text-[10px] w-full sm:w-28 focus:outline-none focus:border-indigo-500 font-sans" 
                                />
                                
                                <div class="flex bg-muted p-0.5 rounded border border-border text-[9px] font-sans">
                                    <button class="px-2 py-0.5 rounded font-semibold cursor-pointer {logLevelFilter === 'all' ? 'bg-background text-foreground shadow-sm font-bold' : 'text-muted-foreground hover:text-foreground'}" onclick={() => logLevelFilter = 'all'}>All</button>
                                    <button class="px-2 py-0.5 rounded font-semibold text-emerald-400 cursor-pointer {logLevelFilter === 'success' ? 'bg-background shadow-sm font-bold animate-none' : 'hover:text-foreground'}" onclick={() => logLevelFilter = 'success'}>Success</button>
                                    <button class="px-2 py-0.5 rounded font-semibold text-amber-400 cursor-pointer {logLevelFilter === 'warn' ? 'bg-background shadow-sm font-bold animate-none' : 'hover:text-foreground'}" onclick={() => logLevelFilter = 'warn'}>Warn</button>
                                    <button class="px-2 py-0.5 rounded font-semibold text-rose-400 cursor-pointer {logLevelFilter === 'error' ? 'bg-background shadow-sm font-bold animate-none' : 'hover:text-foreground'}" onclick={() => logLevelFilter = 'error'}>Error</button>
                                </div>

                                <button class="bg-muted hover:bg-accent border border-border px-2 py-0.5 rounded cursor-pointer font-bold animate-none font-sans" onclick={() => { if (structuredLogs[skill.id]) structuredLogs[skill.id] = []; if (executionLogs[skill.id]) executionLogs[skill.id] = ''; }} title="Clear Logs">Clear</button>
                                <button class="border px-2 py-0.5 rounded cursor-pointer font-bold animate-none font-sans {scrollLock ? 'bg-indigo-600/10 border-indigo-500 text-indigo-400' : 'bg-muted border-border text-muted-foreground'}" onclick={() => scrollLock = !scrollLock} title="Toggle Scroll Lock">Lock</button>
                            </div>
                        </div>

                        <div 
                            bind:this={terminalElement}
                            class="flex-1 bg-black border border-border rounded-lg p-3 font-mono text-[10px] overflow-y-auto min-h-[220px] max-h-[380px] flex flex-col gap-1 select-text scrollbar-thin animate-none"
                        >
                            {#if filteredList.length > 0}
                                {#each filteredList as entry}
                                    <div class="flex items-start gap-2 hover:bg-white/5 py-0.5 px-1 rounded transition-colors leading-relaxed animate-in fade-in duration-100">
                                        <span class="text-zinc-600 select-none shrink-0 font-light">{entry.timestamp}</span>
                                        <span class="text-indigo-400/80 font-bold select-none shrink-0" style="font-size: 9px;">[{entry.source}]</span>
                                        
                                        {#if entry.level === 'error'}
                                            <span class="bg-rose-500/10 border border-rose-500/20 text-rose-400 font-extrabold px-1 rounded select-none shrink-0" style="font-size: 8px;">ERR</span>
                                        {:else if entry.level === 'warn'}
                                            <span class="bg-amber-500/10 border border-amber-500/20 text-amber-400 font-extrabold px-1 rounded select-none shrink-0" style="font-size: 8px;">WRN</span>
                                        {:else if entry.level === 'success'}
                                            <span class="bg-emerald-500/10 border border-emerald-500/20 text-emerald-400 font-extrabold px-1 rounded select-none shrink-0" style="font-size: 8px;"> OK </span>
                                        {:else}
                                            <span class="bg-zinc-800 border border-zinc-700 text-zinc-400 font-extrabold px-1 rounded select-none shrink-0" style="font-size: 8px;">INF</span>
                                        {/if}
                                        
                                        {#if entry.tag}
                                            <span class="bg-indigo-500/10 border border-indigo-500/20 text-indigo-400 font-bold px-1.5 py-0.5 rounded select-none shrink-0" style="font-size: 8px;">{entry.tag.toUpperCase()}</span>
                                        {/if}
                                        
                                        <span class="text-muted-foreground whitespace-pre-wrap break-all flex-1 {entry.level === 'error' ? 'text-rose-300 font-medium' : entry.level === 'warn' ? 'text-amber-300/90' : entry.level === 'success' ? 'text-emerald-300/90' : 'text-zinc-300'}">{entry.message}</span>
                                    </div>
                                {/each}
                            {:else}
                                <div class="h-full flex flex-col items-center justify-center text-muted-foreground/30 text-center select-none py-12 font-sans text-xs">
                                    <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" class="mb-2"><rect width="18" height="18" x="3" y="3" rx="2"/><path d="M9 17V7"/><path d="M15 17V7"/></svg>
                                    <span>&gt; Terminal is ready. Stdout and stderr logs will stream here...</span>
                                </div>
                            {/if}
                        </div>
                    </div>
                {/if}
            </div>
        </div>
    </div>
</div>
