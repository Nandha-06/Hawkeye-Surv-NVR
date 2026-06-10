<script lang="ts">
    import { onDestroy, onMount, tick } from 'svelte';
    import { getApiToken, buildWsUrl } from '$lib/apiToken';
    import { browser } from '$app/environment';
    import { fly, fade } from 'svelte/transition';
    import UnifiedPlayer from '$lib/components/UnifiedPlayer.svelte';
    import { devMode } from '$lib/devMode.svelte';
    import type {
        SkillMetadata,
        ProviderConfig,
        ActiveInferenceConfig
    } from '$lib/types';

    interface CameraConfig {
        id: string;
        name: string;
        source: 'rtsp' | 'webcam' | 'file';
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

    let hardwareStats = $state({
        cpu: 0,
        gpu: 0,
        memory: { used: 0, total: 16.0, percent: 0 },
        storage: { used: 0, total: 512, percent: 0 }
    });

    let cameraCanvases: Record<string, HTMLCanvasElement> = {};
    let overlayCanvases: Record<string, HTMLCanvasElement> = {};
    let webcamVideos: Record<string, HTMLVideoElement> = {};
    let webcamStreams: Record<string, MediaStream> = {};
    let webcamIntervals: Record<string, ReturnType<typeof setInterval>> = {};
    let captureCanvases: Record<string, HTMLCanvasElement> = {};
    let webrtcConnections: Record<string, RTCPeerConnection> = {};
    let webrtcStreams: Record<string, MediaStream> = {};
    let waitingForFrameAck: Record<string, boolean> = {};

    interface ActiveBrowserRecorder {
        recorder: MediaRecorder;
        intervalId?: ReturnType<typeof setInterval>;
        chunks?: Blob[];
        segmentStartTime: string;
    }
    let browserRecorders: Record<string, ActiveBrowserRecorder> = {};

    const enabledCameras = $derived(cameras.filter(c => c.enabled));
    const visibleCameras = $derived(focusedCameraId
        ? enabledCameras.filter(c => c.id === focusedCameraId)
        : enabledCameras
    );
    const activeRecorders = $derived(recorders.filter(r => r.state === 'recording').length);

    let providers: Record<string, ProviderConfig> = $state({});
    let localModels: { name: string; sizeBytes: number; path: string; isVlm: boolean }[] = $state([]);
    let activeInference: ActiveInferenceConfig = $state({
        llm: { type: 'local-engine', engineId: 'llama-cpp', modelId: null, port: 5411, provider: null, cloudModelId: null },
        vlm: { type: 'local-engine', engineId: 'llama-cpp', modelId: null, port: 5405, provider: null, cloudModelId: null }
    });
    let activeDownloads: Record<string, { percent: number; bytesDownloaded: number; totalBytes: number; status: string; error?: string }> = $state({});

    let toastMessage = $state('');
    let toastType = $state<'info' | 'success' | 'warning' | 'error'>('info');
    let showToast = $state(false);
    let toastTimer: ReturnType<typeof setTimeout> | null = null;
    let wsReconnectAttempts = $state(0);
    let skillsLoadError = $state('');

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

    let activePtzCameraId = $state<string | null>(null);

    let diagnosticWarnings = $state<{code: string; message: string}[]>([]);
    let activeModules = $state({ face_recognition: false, tracking: false });
    let motionGatedCameras = $state<Record<string, boolean>>({});

    let isSurveillanceToggling = $state(false);
    const isSurveillanceActive = $derived(
        activeSkillStatus === 'ready' || activeSkillStatus === 'starting'
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

    let skillConfigValues: Record<string, Record<string, any>> = $state({});
    let isDeployingSkill: Record<string, boolean> = $state({});
    let deployProgress: Record<string, number> = $state({});

    onMount(async () => {
        await Promise.all([loadCameras(), loadRecorders()]);
        connectWS();
    });

    $effect(() => {
        if (!browser) return;
        if (cameras.length === 0) return;

        const isDetectionActive = activeSkillStatus === 'ready' || activeSkillStatus === 'starting';

        if (isDetectionActive) {
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

    $effect(() => {
        if (!showOverlays) {
            for (const [, canvas] of Object.entries(overlayCanvases)) {
                if (canvas) {
                    const ctx = canvas.getContext('2d');
                    if (ctx) {
                        ctx.clearRect(0, 0, canvas.width, canvas.height);
                    }
                }
            }
        }
    });

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
            let lastAckTime = 0;
            
            const captureNext = () => {
                // Always reschedule to keep the loop alive
                webcamIntervals[camera.id] = setTimeout(captureNext, intervalMs) as any;

                if (activeSkillStatus !== 'ready' || !ws || ws.readyState !== WebSocket.OPEN) return;
                if (!video || video.readyState < video.HAVE_CURRENT_DATA) return;

                if (waitingForFrameAck[camera.id]) {
                    // Clear stale ack after 3 seconds to prevent permanent block
                    if (Date.now() - lastAckTime > 3000) {
                        waitingForFrameAck[camera.id] = false;
                    } else {
                        return;
                    }
                }

                const ctx = canvas.getContext('2d');
                if (!ctx) return;
                ctx.drawImage(video, 0, 0, canvas.width, canvas.height);
                
                waitingForFrameAck[camera.id] = true;
                lastAckTime = Date.now();
                
                canvas.toBlob((blob) => {
                    if (!blob) {
                        waitingForFrameAck[camera.id] = false;
                        return;
                    }
                    blob.arrayBuffer().then((imgBuffer) => {
                        const meta = JSON.stringify({
                            action: 'feed_frame',
                            skillId: selectedSkillId,
                            cameraId: camera.id,
                            frameId: frameId++,
                            timestamp: new Date().toISOString()
                        });
                        
                        const metaBytes = new TextEncoder().encode(meta);
                        const payload = new Uint8Array(4 + metaBytes.length + imgBuffer.byteLength);
                        
                        const dv = new DataView(payload.buffer);
                        dv.setUint32(0, metaBytes.length, true); // Little endian
                        
                        payload.set(metaBytes, 4);
                        payload.set(new Uint8Array(imgBuffer), 4 + metaBytes.length);
                        
                        ws?.send(payload);
                    }).catch(() => { waitingForFrameAck[camera.id] = false; });
                }, 'image/jpeg', 0.72);
            };
            
            captureNext();

            syncBrowserRecorders();
        } catch (err) {
            console.error(`[Webcam] Failed to start webcam stream for ${camera.id}:`, err);
        }
    }

    function stopWebcams() {
        for (const interval of Object.values(webcamIntervals)) clearTimeout(interval);
        webcamIntervals = {};
        for (const cameraId of Object.keys(browserRecorders)) stopBrowserRecording(cameraId);
        for (const stream of Object.values(webcamStreams)) stream.getTracks().forEach(track => track.stop());
        webcamStreams = {};
        captureCanvases = {};
    }

    async function startWebRTC(camera: CameraConfig) {
        if (webrtcConnections[camera.id]) return;
        try {
            const pc = new RTCPeerConnection({
                iceServers: [{ urls: 'stun:stun.l.google.com:19302' }]
            });
            webrtcConnections[camera.id] = pc;

            pc.addTransceiver('video', { direction: 'recvonly' });
            pc.addTransceiver('audio', { direction: 'recvonly' });

            pc.ontrack = (event) => {
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

            const baseUrl = import.meta.env.VITE_GO2RTC_URL || `http://${window.location.hostname}:1984/api/webrtc`;
            const go2rtcUrl = `${baseUrl}?src=${encodeURIComponent(camera.id)}`;
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
            const uploadRes = await fetch('/api/v1/recordings', { method: 'POST', body: formData });
            if (!uploadRes.ok) {
                console.error('[Browser Recorder] Upload failed with status:', uploadRes.status);
            }
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
            ctx.font = '12px JetBrains Mono, monospace';
            const labelWidth = ctx.measureText(label).width + 10;
            ctx.fillRect(x1, Math.max(0, y1 - 20), labelWidth, 20);
            ctx.fillStyle = '#fff';
            ctx.fillText(label, x1 + 5, Math.max(14, y1 - 6));
        }
    }

    function colorForClass(className: string) {
        if (className === 'person') return '#4DD4E0';
        if (className === 'car') return '#FF9F4A';
        if (className === 'dog' || className === 'cat') return '#22c55e';
        return '#8B7CF6';
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

    function triggerToast(msg: string, type: 'info' | 'success' | 'warning' | 'error' = 'info') {
        toastMessage = msg;
        toastType = type;
        showToast = true;
        if (toastTimer) clearTimeout(toastTimer);
        toastTimer = setTimeout(() => showToast = false, 4000);
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
                wsReconnectAttempts = 0;
                skillsLoadError = '';
                ws?.send(JSON.stringify({ action: 'list_skills' }));
                ws?.send(JSON.stringify({ action: 'get_settings' }));
                ws?.send(JSON.stringify({ action: 'list_local_models' }));
            };

            ws.onclose = () => {
                wsStatus = 'disconnected';
                activeSkillStatus = 'stopped';
                if (wsReconnectTimer) clearTimeout(wsReconnectTimer);
                const backoff = Math.min(30000, 1000 * Math.pow(1.5, wsReconnectAttempts));
                wsReconnectAttempts++;
                wsReconnectTimer = setTimeout(connectWS, backoff);
            };

            ws.onmessage = async (event) => {
                if (typeof event.data !== 'string') return;
                try {
                    const data = JSON.parse(event.data);

                    if (data.event === 'skills_list') {
                        allSkills = data.skills || [];
                        skillsLoadError = '';
                        allSkills.forEach(skill => {
                            if (skill.isDeploying) {
                                isDeployingSkill[skill.id] = true;
                            }
                            if (!skillConfigValues[skill.id]) {
                                skillConfigValues[skill.id] = {};
                                skill.configParams?.forEach(p => {
                                    skillConfigValues[skill.id][p.name] = p.default;
                                });
                            }
                        });

                        const activeSkill = allSkills.find(s => s.isRunning);
                        if (activeSkill) {
                            selectedSkillId = activeSkill.id;
                            activeSkillStatus = 'ready';
                        } else if (allSkills.length > 0 && (!selectedSkillId || !allSkills.find(s => s.id === selectedSkillId))) {
                            selectedSkillId = allSkills[0].id;
                        }
                    }
                    if (data.event === 'deploy_progress') {
                        const sId = data.skillId;
                        if (sId) {
                            isDeployingSkill[sId] = true;
                            if (data.message) {
                                const parsedEntry = parseLogLine(`[deploy] ${data.message}`);
                                if (!structuredLogs[sId]) structuredLogs[sId] = [];
                                structuredLogs[sId] = [...structuredLogs[sId], parsedEntry].slice(-400);
                            }
                            if (data.stage === 'complete') {
                                deployProgress[sId] = 100;
                                isDeployingSkill[sId] = false;
                                ws?.send(JSON.stringify({ action: 'list_skills' }));
                            } else if (data.stage === 'error') {
                                deployProgress[sId] = 0;
                                isDeployingSkill[sId] = false;
                            }
                        }
                    }
                    if (data.event === 'log') {
                        const sId = data.skillId;
                        if (sId) {
                            if (data.message) {
                                const prefix = data.source === 'stderr' ? '[stderr] ' : '';
                                const parsedEntry = parseLogLine(prefix + data.message);
                                if (!structuredLogs[sId]) structuredLogs[sId] = [];
                                structuredLogs[sId] = [...structuredLogs[sId], parsedEntry].slice(-400);
                            }
                        }
                    }
                    if (data.event === 'progress') {
                        const sId = data.skillId;
                        if (sId && data.message) {
                            const logMsg = `[Progress] [${data.stage || 'info'}] ${data.message}`;
                            const parsedEntry = parseLogLine(logMsg);
                            if (!structuredLogs[sId]) structuredLogs[sId] = [];
                            structuredLogs[sId] = [...structuredLogs[sId], parsedEntry].slice(-400);
                        }
                    }
                    if (data.event === 'perf_stats') {
                        if (data.cpu !== undefined) hardwareStats.cpu = data.cpu;
                        if (data.gpu !== undefined) hardwareStats.gpu = data.gpu;
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
                            const parsedEntry = parseLogLine(logMsg);
                            if (!structuredLogs[sId]) structuredLogs[sId] = [];
                            structuredLogs[sId] = [...structuredLogs[sId], parsedEntry].slice(-400);
                        }
                    }
                    if (data.event === 'threat_analysis') {
                        const sId = data.skillId;
                        if (sId && data.message) {
                            const logMsg = `[VLM] [${(data.alert_type || 'info').toUpperCase()}] ${data.message}`;
                            const parsedEntry = parseLogLine(logMsg);
                            if (!structuredLogs[sId]) structuredLogs[sId] = [];
                            structuredLogs[sId] = [...structuredLogs[sId], parsedEntry].slice(-400);
                        }
                    }
                    if (data.event === 'error') {
                        const sId = data.skillId;
                        if (sId && data.message) {
                            const logMsg = `[Error] ${data.message}`;
                            const parsedEntry = parseLogLine(logMsg);
                            if (!structuredLogs[sId]) structuredLogs[sId] = [];
                            structuredLogs[sId] = [...structuredLogs[sId], parsedEntry].slice(-400);
                        }
                        if (!sId && data.message) {
                            skillsLoadError = data.message;
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
                    if (data.event === 'starting') {
                        activeSkillStatus = 'starting';
                    }
                    if (data.event === 'stopped') {
                        activeSkillStatus = 'stopped';
                        if (data.skillId && data.message) {
                            const parsedEntry = parseLogLine(`[stderr] ${data.message}`);
                            if (!structuredLogs[data.skillId]) structuredLogs[data.skillId] = [];
                            structuredLogs[data.skillId] = [...structuredLogs[data.skillId], parsedEntry].slice(-400);
                        }
                    }
                    if (data.event === 'detections' && data.skillId === selectedSkillId) {
                        waitingForFrameAck[data.cameraId] = false;
                        updateDetectionFps();
                        motionGatedCameras[data.cameraId] = !!data.motion_gated;
                        drawDetections(data.cameraId, data.objects || []);
                    }
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
                        if (activeInference.vlm.modelId && !localModels.some(m => m.name === activeInference.vlm.modelId)) {
                            activeInference.vlm.modelId = null;
                        }
                    }
                    if (data.event === 'model_deleted') {
                        if (data.success) {
                            localModels = data.localModels;
                            if (activeInference.vlm.modelId === data.filename) {
                                activeInference.vlm.modelId = null;
                            }
                        } else {
                            triggerToast(`Failed to delete model ${data.filename}`, 'error');
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
        const parsedEntry = parseLogLine('[Progress] Starting perception process…');
        if (!structuredLogs[selectedSkillId]) structuredLogs[selectedSkillId] = [];
        structuredLogs[selectedSkillId] = [...structuredLogs[selectedSkillId], parsedEntry].slice(-400);
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
            const isActive = activeSkillStatus === 'ready' || activeSkillStatus === 'starting';
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
        deployProgress[skillId] = 5;
        isDeployingSkill[skillId] = true;
        ws?.send(JSON.stringify({ action: 'deploy_skill', skillId, config: skillConfigValues[skillId] || {} }));
    }

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

    function downloadModel(repo: string, filename: string, isVlm: boolean) {
        if (!ws || ws.readyState !== WebSocket.OPEN) return;
        ws?.send(JSON.stringify({
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
    <title>Hawkeye · Live Grid</title>
</svelte:head>

<div class="max-w-[1600px] mx-auto p-5 md:p-7 flex flex-col gap-5">
    {#if showToast}
        <div class="fixed bottom-6 right-6 z-[100] panel !p-0"
            transition:fly={{ y: 16, duration: 200 }}>
            <div class="flex items-center gap-3 px-4 py-3">
                <span class="w-1.5 h-1.5 rounded-full status-pulse"
                    class:bg-cyan={toastType === 'info'}
                    class:bg-jade={toastType === 'success'}
                    class:bg-gold={toastType === 'warning'}
                    class:bg-crimson={toastType === 'error'}></span>
                <span class="text-xs font-semibold text-foreground">{toastMessage}</span>
            </div>
        </div>
    {/if}

    <!-- Header bar -->
    <header class="flex flex-col md:flex-row md:items-center justify-between gap-3 page-enter">
        <div>
            <div class="flex items-center gap-2.5">
                <span class="badge {activeSkillStatus === 'ready' ? 'badge-jade' : activeSkillStatus === 'starting' ? 'badge-gold' : activeSkillStatus === 'error' ? 'badge-crimson' : 'badge-muted'}">
                    <span class="w-1.5 h-1.5 rounded-full {activeSkillStatus === 'ready' ? 'bg-jade status-pulse' : activeSkillStatus === 'starting' ? 'bg-gold animate-pulse' : activeSkillStatus === 'error' ? 'bg-crimson' : 'bg-muted-foreground'}"></span>
                    {activeSkillStatus.toUpperCase()}
                </span>
                <span class="text-[11px] text-muted-foreground font-mono">{detectionFps} FPS</span>
            </div>
            <h1 class="text-2xl md:text-3xl font-display font-bold text-foreground tracking-tight mt-1.5 leading-none">Live Grid</h1>
        </div>

        <div class="flex items-center gap-2 flex-wrap">
            <button class="btn btn-sm" onclick={() => showOverlays = !showOverlays}>
                <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5">
                    {#if showOverlays}
                        <path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><circle cx="12" cy="12" r="3"/>
                    {:else}
                        <path d="M17.94 17.94A10.07 10.07 0 0 1 12 20c-7 0-11-8-11-8a18.45 18.45 0 0 1 5.06-5.94M9.9 4.24A9.12 9.12 0 0 1 12 4c7 0 11 8 11 8a18.5 18.5 0 0 1-2.16 3.19m-6.72-1.07a3 3 0 1 1-4.24-4.24"/><line x1="1" y1="1" x2="23" y2="23"/>
                    {/if}
                </svg>
                Overlays {showOverlays ? 'on' : 'off'}
            </button>

            {#if allSkills.length > 0}
                <select class="select !h-8 !w-auto text-xs" bind:value={selectedSkillId} disabled={activeSkillStatus === 'ready' || activeSkillStatus === 'starting'}>
                    {#each allSkills.filter(s => ['detection', 'analysis', 'transformation', 'privacy'].includes(s.category)) as skill}
                        <option value={skill.id}>{skill.name}</option>
                    {/each}
                </select>
            {/if}

            {#if isSurveillanceToggling}
                <button class="btn" disabled>
                    <span class="w-3 h-3 rounded-full border-2 border-muted-foreground border-t-transparent animate-spin"></span>
                    Initializing…
                </button>
            {:else if isSurveillanceActive}
                <button class="btn btn-crimson" onclick={toggleSurveillance}>
                    <span class="w-1.5 h-1.5 rounded-full bg-white status-pulse"></span>
                    Stop surveillance
                </button>
            {:else}
                <button class="btn btn-jade" onclick={toggleSurveillance}>
                    <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/></svg>
                    Start surveillance
                </button>
            {/if}
        </div>
    </header>

    <!-- Diagnostic warnings -->
    {#if diagnosticWarnings.length > 0}
        <div class="flex flex-col gap-2 page-enter stagger-1">
            {#each diagnosticWarnings as warning}
                <div class="flex items-start gap-3 p-3.5 panel !border-crimson/30 !bg-crimson/5" in:fly={{ y: -8, duration: 200 }}>
                    <div class="w-8 h-8 rounded-md bg-crimson/15 text-crimson flex items-center justify-center shrink-0">
                        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5">
                            <path d="M10.29 3.86 1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/>
                            <line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/>
                        </svg>
                    </div>
                    <div class="flex-1 min-w-0">
                        <p class="text-sm font-semibold text-foreground capitalize">{warning.code.replace(/_/g, ' ').toLowerCase()}</p>
                        <p class="text-xs text-muted-foreground mt-0.5 leading-snug">{warning.message}</p>
                    </div>
                    <div class="flex items-center gap-1.5 shrink-0">
                        {#if warning.code === 'CUDA_FALLBACK_TO_CPU' || warning.code === 'FACE_RECOG_LOAD_FAILED'}
                            <button class="btn btn-crimson btn-sm" onclick={() => { diagnosticWarnings = diagnosticWarnings.filter(w => w.code !== warning.code); deploySkill('perception-core'); }}>
                                Re-run setup
                            </button>
                        {/if}
                        <button class="btn btn-sm" onclick={() => diagnosticWarnings = diagnosticWarnings.filter(w => w.code !== warning.code)}>
                            Dismiss
                        </button>
                    </div>
                </div>
            {/each}
        </div>
    {/if}

    <!-- Telemetry strip -->
    <section class="grid grid-cols-2 md:grid-cols-5 gap-3 page-enter stagger-1">
        <div class="panel px-4 py-3 flex flex-col gap-1">
            <span class="section-eyebrow">Core</span>
            <div class="flex items-center gap-1.5">
                <span class="w-1.5 h-1.5 rounded-full {wsStatus === 'connected' ? 'bg-jade status-pulse' : 'bg-crimson'}"></span>
                <span class="text-sm font-bold text-foreground">{wsStatus}</span>
            </div>
        </div>
        <div class="panel px-4 py-3 flex flex-col gap-1">
            <span class="section-eyebrow">Engine</span>
            <div class="flex items-center gap-1.5">
                <span class="text-sm font-bold text-foreground capitalize">{activeSkillStatus}</span>
                {#if activeSkillStatus === 'ready' && activeModules.face_recognition}
                    <span class="w-1.5 h-1.5 rounded-full bg-iris" title="Face Re-ID"></span>
                {/if}
            </div>
        </div>
        <div class="panel px-4 py-3 flex flex-col gap-1">
            <span class="section-eyebrow">Feeds</span>
            <span class="text-sm font-bold text-foreground tabular-nums">{enabledCameras.length} <span class="text-muted-foreground font-normal text-xs">operational</span></span>
        </div>
        <div class="panel px-4 py-3 flex flex-col gap-1">
            <span class="section-eyebrow">NVR</span>
            <div class="flex items-center gap-1.5">
                {#if activeRecorders > 0}<span class="w-1.5 h-1.5 rounded-full bg-crimson status-pulse"></span>{/if}
                <span class="text-sm font-bold text-foreground tabular-nums">{activeRecorders} <span class="text-muted-foreground font-normal text-xs">continuous</span></span>
            </div>
        </div>
        <div class="panel px-4 py-3 flex flex-col gap-1">
            <span class="section-eyebrow">Analytics</span>
            <span class="text-sm font-bold text-cyan tabular-nums font-mono">{activeSkillStatus === 'ready' ? `${detectionFps} FPS` : '0 FPS'}</span>
        </div>
    </section>

    <!-- Camera grid + sidebar layout -->
    <div class="grid grid-cols-1 xl:grid-cols-12 gap-5 page-enter stagger-2">
        <!-- Grid + below config -->
        <div class="xl:col-span-8 flex flex-col gap-5">
            <!-- Camera grid -->
            <section class="panel !p-4 flex flex-col">
                <div class="flex items-center justify-between gap-2 mb-3">
                    <div class="flex items-center gap-2">
                        <h2 class="section-title">Camera Grid</h2>
                        <div class="flex items-center bg-surface-2 border border-border rounded-md p-0.5">
                            {#each ['auto', '1x1', '2x2', '3x3', '4x4'] as layout}
                                <button
                                    class="px-2 h-6 rounded text-[10px] font-bold uppercase tracking-wider transition-all
                                        {gridLayout === layout ? 'bg-primary text-primary-foreground' : 'text-muted-foreground'}"
                                    onclick={() => gridLayout = layout as any}
                                >
                                    {layout}
                                </button>
                            {/each}
                        </div>
                    </div>
                    {#if focusedCameraId}
                        <button class="btn btn-sm" onclick={() => focusedCameraId = null}>
                            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><path d="M9 9l6 6M15 9l-6 6"/></svg>
                            Exit focus
                        </button>
                    {/if}
                </div>

                {#if enabledCameras.length === 0}
                    <div class="min-h-[400px] flex flex-col items-center justify-center text-center border border-dashed border-border rounded-xl">
                        <div class="w-14 h-14 rounded-xl bg-surface-2 border border-border flex items-center justify-center text-muted-foreground mb-3">
                            <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5">
                                <rect x="2" y="3" width="20" height="14" rx="2"/><line x1="8" y1="21" x2="16" y2="21"/><line x1="12" y1="17" x2="12" y2="21"/>
                            </svg>
                        </div>
                        <p class="text-sm font-semibold text-foreground">Surveillance feeds offline</p>
                        <p class="text-xs text-muted-foreground mt-1 max-w-xs">Add webcams or RTSP streams from Cameras to populate the grid</p>
                        <a href="/cameras" class="btn btn-primary btn-sm mt-4">Configure cameras</a>
                    </div>
                {:else}
                    <div class="grid gap-3
                        {gridLayout === '1x1' || focusedCameraId ? 'grid-cols-1' : ''}
                        {gridLayout === '2x2' ? 'grid-cols-2' : ''}
                        {gridLayout === '3x3' ? 'grid-cols-3' : ''}
                        {gridLayout === '4x4' ? 'grid-cols-4' : ''}
                        {gridLayout === 'auto' ? 'lg:grid-cols-2 xl:grid-cols-3' : ''}
                    ">
                        {#each visibleCameras as camera (camera.id)}
                            {#if camera.source === 'rtsp'}
                                <UnifiedPlayer {camera} />
                            {:else}
                                {@const recorder = recorders.find(r => r.cameraId === camera.id)}
                                <article class="flex flex-col bg-card border border-border rounded-xl overflow-hidden shadow-sm">
                                    <div
                                        class="relative w-full aspect-video bg-black overflow-hidden group cursor-zoom-in corner-brackets"
                                        onclick={() => focusedCameraId = focusedCameraId === camera.id ? null : camera.id}
                                        role="button"
                                        tabindex="0"
                                        onkeydown={(e) => { if (e.key === 'Enter') focusedCameraId = focusedCameraId === camera.id ? null : camera.id; }}
                                    >
                                        <video bind:this={webcamVideos[camera.id]} autoplay playsinline muted class="absolute inset-0 w-full h-full object-contain"></video>
                                        <canvas bind:this={overlayCanvases[camera.id]} class="absolute inset-0 w-full h-full object-contain pointer-events-none"></canvas>

                                        {#if activeSkillStatus === 'ready'}
                                            <div class="absolute left-3 top-3 flex gap-1.5 z-20">
                                                {#if motionGatedCameras[camera.id]}
                                                    <span class="badge badge-gold !h-5">
                                                        <span class="w-1 h-1 rounded-full bg-gold animate-pulse"></span>
                                                        Static
                                                    </span>
                                                {:else}
                                                    <span class="badge badge-jade !h-5">
                                                        <span class="w-1 h-1 rounded-full bg-jade status-pulse"></span>
                                                        Processing
                                                    </span>
                                                {/if}
                                            </div>
                                        {/if}

                                        <div class="absolute left-3 bottom-3 flex gap-1.5 z-10">
                                            {#if recorder?.state === 'recording'}
                                                <span class="badge badge-crimson !h-5 font-mono">
                                                    <span class="w-1 h-1 rounded-full bg-crimson status-pulse"></span>
                                                    REC
                                                </span>
                                            {/if}
                                            <span class="badge !h-5 font-mono">
                                                {recorder?.indexedSegments || 0} SEG
                                            </span>
                                        </div>

                                        <button
                                            class="absolute right-3 bottom-3 z-10 w-7 h-7 rounded-md bg-black/60 border border-border/40 text-white/80 hover:text-white hover:bg-black/80 opacity-0 group-hover:opacity-100 transition-all"
                                            onclick={(e) => { e.stopPropagation(); togglePtzOverlay(camera.id); }}
                                            title="PTZ controls"
                                        >
                                            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" class="mx-auto">
                                                <circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z"/>
                                            </svg>
                                        </button>
                                    </div>

                                    <div class="px-4 py-2.5 flex items-center justify-between border-t border-border">
                                        <div class="min-w-0">
                                            <p class="text-xs font-semibold text-foreground truncate">{camera.name}</p>
                                            <p class="text-[10px] text-muted-foreground uppercase tracking-wider font-mono mt-0.5">{camera.source}</p>
                                        </div>
                                        <span class="text-[10px] font-mono text-muted-foreground">{camera.fps || 5} FPS</span>
                                    </div>
                                </article>
                            {/if}
                        {/each}
                    </div>
                {/if}
            </section>

            <!-- Skill config -->
            <section class="panel">
                <div class="panel-header">
                    <div>
                        <h2 class="section-title">Engine Configuration</h2>
                        {#if selectedSkill}
                            <p class="text-[11px] text-muted-foreground mt-0.5">{selectedSkill.name} · {selectedSkill.category}</p>
                        {/if}
                    </div>
                    {#if selectedSkill}
                        <span class="badge {selectedSkill.isInstalled ? 'badge-jade' : 'badge-ember'}">
                            {selectedSkill.isInstalled ? 'Installed' : 'Not installed'}
                        </span>
                    {/if}
                </div>

                <div class="panel-body">
                    {#if selectedSkill}
                        {@const isRunning = activeSkillStatus === 'ready' || activeSkillStatus === 'starting'}
                        {@const activeDl = isDeployingSkill[selectedSkill.id]}

                        <p class="text-xs text-muted-foreground leading-relaxed mb-4">{selectedSkill.description}</p>

                        <div class="grid grid-cols-1 md:grid-cols-2 gap-3 max-h-[280px] overflow-y-auto pr-1">
                            {#if selectedSkill.configParams && selectedSkill.configParams.length > 0}
                                {#each selectedSkill.configParams as param}
                                    <div class="flex flex-col gap-1.5">
                                        <label class="section-eyebrow" for="param-{param.name}">{param.label}</label>
                                        {#if param.type === 'select'}
                                            <select id="param-{param.name}" bind:value={skillConfigValues[selectedSkill.id][param.name]} disabled={isRunning} class="select">
                                                {#each param.options || [] as opt}
                                                    <option value={typeof opt === 'object' ? opt.value : opt}>
                                                        {typeof opt === 'object' ? opt.label : opt}
                                                    </option>
                                                {/each}
                                            </select>
                                        {:else if param.type === 'boolean'}
                                            <label class="flex items-center gap-2.5 h-9 px-3 rounded-lg border border-border bg-surface-2 cursor-pointer">
                                                <input type="checkbox" bind:checked={skillConfigValues[selectedSkill.id][param.name]} disabled={isRunning} class="checkbox" />
                                                <span class="text-xs text-foreground font-medium">Enable</span>
                                            </label>
                                        {:else if param.type === 'number'}
                                            <input type="number" id="param-{param.name}" bind:value={skillConfigValues[selectedSkill.id][param.name]} min={param.min} max={param.max} disabled={isRunning} class="input" />
                                        {:else}
                                            <input type="text" id="param-{param.name}" bind:value={skillConfigValues[selectedSkill.id][param.name]} disabled={isRunning} class="input" />
                                        {/if}
                                        {#if param.description}
                                            <p class="text-[10px] text-muted-foreground leading-snug">{param.description}</p>
                                        {/if}
                                    </div>
                                {/each}
                            {:else}
                                <p class="text-xs text-muted-foreground text-center p-4 border border-dashed border-border rounded-lg md:col-span-2">No configurable parameters.</p>
                            {/if}
                        </div>

                        <div class="pt-4 border-t border-border mt-4">
                            {#if !selectedSkill.isInstalled}
                                <button class="btn btn-iris w-full" disabled={activeDl} onclick={() => deploySkill(selectedSkill.id)}>
                                    {activeDl ? 'Installing…' : 'Install skill & virtualenv'}
                                </button>
                            {:else}
                                <div class="text-xs font-semibold text-jade bg-jade/10 border border-jade/20 rounded-lg p-2.5 text-center">
                                    ✓ Skill ready · last verified {new Date().toLocaleTimeString()}
                                </div>
                            {/if}
                        </div>
                    {:else}
                        <div class="text-xs text-muted-foreground text-center p-8">
                            {#if skillsLoadError}
                                <p class="text-crimson">{skillsLoadError}</p>
                                <button class="btn btn-sm mt-3" onclick={() => ws?.send(JSON.stringify({ action: 'list_skills' }))}>Retry</button>
                            {:else if wsStatus !== 'connected'}
                                Connecting to perception service…
                            {:else}
                                Loading configurations…
                            {/if}
                        </div>
                    {/if}
                </div>
            </section>
        </div>

        <!-- Right: Terminal & Downloads -->
        <div class="xl:col-span-4 flex flex-col gap-5">
            <!-- Terminal -->
            <section class="panel !p-0 flex flex-col h-[600px]">
                <div class="px-4 h-12 border-b border-border flex items-center justify-between gap-2">
                    <div class="flex items-center gap-2">
                        <h2 class="text-sm font-semibold font-display">Perception Terminal</h2>
                        {#if activeSkillStatus === 'ready'}
                            <span class="w-1.5 h-1.5 rounded-full bg-jade status-pulse"></span>
                        {/if}
                    </div>
                    <div class="flex items-center gap-1">
                        <input
                            type="text"
                            bind:value={logSearchTerm}
                            placeholder="Filter…"
                            class="input !h-7 !w-28 !text-[10px]"
                        />
                        <div class="flex bg-surface-2 border border-border rounded p-0.5 text-[9px] font-bold">
                            {#each [['all', 'all'], ['success', 'ok'], ['warn', 'wrn'], ['error', 'err']] as [val, label]}
                                <button
                                    class="px-1.5 h-5 rounded transition-all
                                        {logLevelFilter === val ? 'bg-primary text-primary-foreground' : 'text-muted-foreground'}"
                                    onclick={() => logLevelFilter = val as any}
                                >{label.toUpperCase()}</button>
                            {/each}
                        </div>
                        <button class="btn-icon !w-7 !h-7" onclick={() => { if (structuredLogs[selectedSkillId]) structuredLogs[selectedSkillId] = []; }} title="Clear">
                            <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><polyline points="3 6 5 6 21 6"/><path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"/></svg>
                        </button>
                    </div>
                </div>

                {#if selectedSkill}
                    {@const rawList = structuredLogs[selectedSkill.id] || []}
                    {@const filteredList = rawList.filter(log => {
                        const matchesFilter = logLevelFilter === 'all' || log.level === logLevelFilter;
                        const matchesSearch = !logSearchTerm.trim() || log.message.toLowerCase().includes(logSearchTerm.toLowerCase());
                        return matchesFilter && matchesSearch;
                    })}

                    <div
                        bind:this={terminalElement}
                        class="flex-1 bg-black/40 font-mono text-[10.5px] overflow-y-auto p-3 flex flex-col gap-0.5 select-text"
                    >
                        {#if filteredList.length > 0}
                            {#each filteredList as entry}
                                <div class="flex items-start gap-2 hover:bg-white/[0.03] py-0.5 px-1 rounded transition-colors leading-relaxed">
                                    <span class="text-muted-foreground/60 select-none shrink-0 tabular-nums">{entry.timestamp}</span>
                                    {#if entry.level === 'error'}
                                        <span class="badge badge-crimson !h-4 !text-[8px] !px-1 shrink-0">ERR</span>
                                    {:else if entry.level === 'warn'}
                                        <span class="badge badge-gold !h-4 !text-[8px] !px-1 shrink-0">WRN</span>
                                    {:else if entry.level === 'success'}
                                        <span class="badge badge-jade !h-4 !text-[8px] !px-1 shrink-0"> OK </span>
                                    {:else}
                                        <span class="badge !h-4 !text-[8px] !px-1 shrink-0 opacity-60">INF</span>
                                    {/if}
                                    {#if entry.tag}
                                        <span class="badge badge-iris !h-4 !text-[8px] !px-1 shrink-0">{entry.tag.toUpperCase()}</span>
                                    {/if}
                                    <span class="whitespace-pre-wrap break-all flex-1 {entry.level === 'error' ? 'text-crimson-foreground' : entry.level === 'warn' ? 'text-gold/90' : entry.level === 'success' ? 'text-jade/90' : 'text-foreground/85'}">{entry.message}</span>
                                </div>
                            {/each}
                        {:else}
                            <div class="h-full flex flex-col items-center justify-center text-muted-foreground/30 text-center select-none py-12 text-xs">
                                <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" class="mb-2"><rect width="18" height="18" x="3" y="3" rx="2"/><path d="M9 17V7"/><path d="M15 17V7"/></svg>
                                <span class="font-mono">&gt; Terminal ready · awaiting stdout stream…</span>
                            </div>
                        {/if}
                    </div>
                {:else}
                    <div class="flex-1 bg-black/40 font-mono text-[10.5px] overflow-y-auto p-3 text-muted-foreground/50">
                        &gt; {skillsLoadError || (wsStatus === 'connected' ? 'Loading skill catalog…' : 'Connecting to perception service…')}
                    </div>
                {/if}
            </section>

            <!-- Hardware telemetry -->
            <section class="panel !p-0">
                <div class="px-4 h-12 border-b border-border flex items-center justify-between">
                    <h2 class="text-sm font-semibold font-display">Hardware Load</h2>
                    <span class="text-[10px] font-mono text-muted-foreground uppercase tracking-wider">Real-time</span>
                </div>
                <div class="p-4 flex flex-col gap-3">
                    {#each [
                        { label: 'CPU', value: hardwareStats.cpu, color: 'var(--cyan)' },
                        { label: 'GPU', value: hardwareStats.gpu, color: 'var(--iris)' },
                        { label: 'Memory', value: hardwareStats.memory.percent, sub: `${hardwareStats.memory.used} / ${hardwareStats.memory.total} GB`, color: 'var(--jade)' },
                        { label: 'Storage', value: hardwareStats.storage.percent, sub: `${hardwareStats.storage.used} / ${hardwareStats.storage.total} GB`, color: 'var(--ember)' }
                    ] as metric}
                        <div class="flex flex-col gap-1.5">
                            <div class="flex items-center justify-between text-xs">
                                <span class="font-semibold text-foreground">{metric.label}</span>
                                <span class="font-mono tabular-nums text-muted-foreground">{metric.value}%{#if metric.sub}<span class="ml-1.5 opacity-60">{metric.sub}</span>{/if}</span>
                            </div>
                            <div class="h-1.5 rounded-full bg-muted overflow-hidden">
                                <div class="h-full rounded-full transition-all duration-500" style="width: {metric.value}%; background: {metric.color}"></div>
                            </div>
                        </div>
                    {/each}
                </div>
            </section>
        </div>
    </div>

    <!-- Moondream / Models panel (only for visual-event-analyzer) -->
    {#if selectedSkillId === 'visual-event-analyzer'}
        {@const recommendedQuants = [
            { name: 'Moondream2 0.5B · F16', repo: 'moondream/moondream2-gguf', file: 'moondream2-text-model-f16.gguf', size: '950MB', desc: 'Full precision · high fidelity' },
            { name: 'Moondream2 0.5B · Q4', repo: 'Christian-W/moondream2-gguf', file: 'moondream2-text-model-q4_k_m.gguf', size: '300MB', desc: 'Compact · fastest' },
            { name: 'Moondream2 0.5B · Q8', repo: 'Christian-W/moondream2-gguf', file: 'moondream2-text-model-q8_0.gguf', size: '550MB', desc: 'Balanced · excellent precision' }
        ]}
        <section class="panel page-enter stagger-3">
            <div class="panel-header">
                <div>
                    <h2 class="section-title">VLM Model Library</h2>
                    <p class="text-[11px] text-muted-foreground mt-0.5">Download Moondream GGUF quants or select a local model</p>
                </div>
                <button class="btn btn-sm" onclick={() => ws?.send(JSON.stringify({ action: 'list_local_models' }))}>
                    <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><path d="M21 12a9 9 0 1 1-9-9c2.52 0 4.93 1 6.74 2.74L21 8"/><path d="M21 3v5h-5"/></svg>
                    Refresh
                </button>
            </div>
            <div class="panel-body">
                <div class="flex flex-col gap-1.5 mb-4">
                    <span class="section-eyebrow">Active Local VLM</span>
                    <select
                        class="select"
                        bind:value={activeInference.vlm.modelId}
                        onchange={(e) => selectVlmModel((e.target as HTMLSelectElement).value)}
                    >
                        <option value={null}>— Use default local VLM (llava) —</option>
                        {#each localModels.filter(m => m.isVlm) as model}
                            <option value={model.name}>{model.name} ({Math.round(model.sizeBytes / (1024 * 1024))} MB)</option>
                        {/each}
                    </select>
                </div>

                <div class="grid grid-cols-1 md:grid-cols-3 gap-3">
                    {#each recommendedQuants as q}
                        {@const isDownloaded = localModels.some(m => m.name === q.file)}
                        {@const downloadId = `${q.repo}/${q.file}`}
                        {@const downloadInfo = activeDownloads[downloadId]}

                        <div class="rounded-xl border border-border bg-surface-2/50 p-4 flex flex-col gap-3">
                            <div>
                                <p class="text-sm font-semibold text-foreground">{q.name}</p>
                                <p class="text-[10px] text-muted-foreground mt-0.5">{q.desc}</p>
                            </div>
                            <div class="text-[10px] font-mono text-muted-foreground uppercase tracking-wider">{q.size}</div>

                            {#if isDownloaded}
                                <div class="flex items-center gap-2">
                                    <span class="badge badge-jade flex-1 justify-center">✓ Installed</span>
                                    <button type="button" class="btn btn-sm !text-crimson hover:!border-crimson/30" onclick={() => deleteModel(q.file, true)} aria-label="Delete model {q.name}">
                                        <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" aria-hidden="true"><polyline points="3 6 5 6 21 6"/><path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"/></svg>
                                    </button>
                                </div>
                            {:else if downloadInfo}
                                <div class="flex flex-col gap-1.5">
                                    <div class="flex justify-between text-[10px] font-mono">
                                        <span class="text-iris">{downloadInfo.status === 'completed' ? 'Finalizing…' : `Downloading ${downloadInfo.percent}%`}</span>
                                        <span class="text-muted-foreground">{Math.round(downloadInfo.bytesDownloaded / (1024 * 1024))} / {Math.round(downloadInfo.totalBytes / (1024 * 1024))} MB</span>
                                    </div>
                                    <div class="h-1 rounded-full bg-muted overflow-hidden">
                                        <div class="h-full bg-iris" style="width: {downloadInfo.percent}%"></div>
                                    </div>
                                </div>
                            {:else}
                                <button class="btn btn-iris btn-sm" onclick={() => downloadModel(q.repo, q.file, true)}>
                                    <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" x2="12" y1="15" y2="3"/></svg>
                                    Download
                                </button>
                            {/if}
                        </div>
                    {/each}
                </div>
            </div>
        </section>
    {/if}
</div>
