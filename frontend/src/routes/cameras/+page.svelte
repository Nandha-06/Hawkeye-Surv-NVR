<script lang="ts">
    import { onMount, onDestroy } from 'svelte';
    import { slide, fly, fade } from 'svelte/transition';

    interface CameraConfig {
        id: string;
        name: string;
        source: 'rtsp' | 'webcam';
        url?: string;
        detect_url?: string;
        enabled: boolean;
        fps: number;
        confidence: number;
        enable_motion_gating: boolean;
        motion_masks?: [number, number][][];
        alert_zones?: [number, number][][];
    }

    let cameras = $state<CameraConfig[]>([]);
    let isSaving = $state(false);
    let message = $state('');
    let messageType = $state<'success' | 'error' | 'info' | ''>('');

    let expandedCameraSettings = $state<Record<string, boolean>>({});

    let activeEditorCamera = $state<CameraConfig | null>(null);
    let drawingPoints = $state<[number, number][]>([]);
    let cursorX = $state(0);
    let cursorY = $state(0);
    let svgElement = $state<SVGElement | null>(null);
    let drawingMode = $state<'mask' | 'zone'>('mask');

    let editorStream = $state<MediaStream | null>(null);

    function bindStream(node: HTMLVideoElement, stream: MediaStream | null) {
        node.srcObject = stream;
        if (stream) {
            node.play().catch(err => {
                console.error("Editor video play error:", err);
            });
        }
        return {
            update(newStream: MediaStream | null) {
                node.srcObject = newStream;
                if (newStream) {
                    node.play().catch(err => {
                        console.error("Editor video update play error:", err);
                    });
                }
            },
            destroy() {
                node.srcObject = null;
            }
        };
    }

    async function startWebcam() {
        if (!activeEditorCamera || activeEditorCamera.source !== 'webcam') return;
        try {
            editorStream = await navigator.mediaDevices.getUserMedia({
                video: { width: 1280, height: 720 },
                audio: false
            });
        } catch (err) {
            console.error('Failed to access webcam for editor:', err);
            showNotification('Failed to access local webcam feed.', 'error');
        }
    }

    function stopWebcam() {
        if (editorStream) {
            editorStream.getTracks().forEach(track => track.stop());
            editorStream = null;
        }
    }

    function openMaskEditor(camera: CameraConfig) {
        activeEditorCamera = camera;
        drawingPoints = [];
        drawingMode = 'mask';
        if (camera.source === 'webcam') {
            setTimeout(startWebcam, 50);
        }
    }

    function handleSvgClick(e: MouseEvent) {
        if (!svgElement || !activeEditorCamera) return;
        const rect = svgElement.getBoundingClientRect();
        const x = (e.clientX - rect.left) / rect.width;
        const y = (e.clientY - rect.top) / rect.height;

        if (drawingPoints.length >= 3) {
            const firstPt = drawingPoints[0];
            const dx = x - firstPt[0];
            const dy = y - firstPt[1];
            const dist = Math.sqrt(dx * dx + dy * dy);
            if (dist < 0.03) {
                closePolygon();
                return;
            }
        }

        drawingPoints = [...drawingPoints, [x, y]];
    }

    function handleSvgMouseMove(e: MouseEvent) {
        if (!svgElement) return;
        const rect = svgElement.getBoundingClientRect();
        cursorX = (e.clientX - rect.left) / rect.width;
        cursorY = (e.clientY - rect.top) / rect.height;
    }

    function closePolygon() {
        if (!activeEditorCamera || drawingPoints.length < 3) return;
        if (drawingMode === 'mask') {
            if (!activeEditorCamera.motion_masks) {
                activeEditorCamera.motion_masks = [];
            }
            activeEditorCamera.motion_masks = [...activeEditorCamera.motion_masks, drawingPoints];
            showNotification('Motion exclusion mask added. Click Save to persist.', 'success');
        } else {
            if (!activeEditorCamera.alert_zones) {
                activeEditorCamera.alert_zones = [];
            }
            activeEditorCamera.alert_zones = [...activeEditorCamera.alert_zones, drawingPoints];
            showNotification('Activity alert zone added. Click Save to persist.', 'success');
        }
        drawingPoints = [];
    }

    function cancelDrawing() {
        drawingPoints = [];
    }

    function removePolygon(index: number, type: 'mask' | 'zone') {
        if (!activeEditorCamera) return;
        if (type === 'mask') {
            if (!activeEditorCamera.motion_masks) return;
            activeEditorCamera.motion_masks = activeEditorCamera.motion_masks.filter((_, i) => i !== index);
            showNotification('Motion exclusion mask removed.', 'success');
        } else {
            if (!activeEditorCamera.alert_zones) return;
            activeEditorCamera.alert_zones = activeEditorCamera.alert_zones.filter((_, i) => i !== index);
            showNotification('Activity alert zone removed.', 'success');
        }
    }

    function clearAllPolygons() {
        if (!activeEditorCamera) return;
        if (drawingMode === 'mask') {
            activeEditorCamera.motion_masks = [];
            showNotification('All motion exclusion masks cleared.', 'success');
        } else {
            activeEditorCamera.alert_zones = [];
            showNotification('All activity alert zones cleared.', 'success');
        }
        drawingPoints = [];
    }

    function closeMaskEditor() {
        stopWebcam();
        activeEditorCamera = null;
        drawingPoints = [];
    }

    let newCamName = $state('');
    let newCamSource = $state<'rtsp' | 'webcam'>('webcam');
    let newCamUrl = $state('');
    let newCamDetectUrl = $state('');
    let newCamFps = $state(5);
    let newCamConfidence = $state(0.80);
    let newCamMotionGating = $state(true);
    let showAddForm = $state(false);

    async function fetchCameras() {
        try {
            const res = await fetch('/api/v1/cameras');
            if (res.ok) {
                cameras = await res.json();
            } else {
                showNotification('Failed to fetch cameras from database', 'error');
            }
        } catch (err: any) {
            showNotification(`Error: ${err.message}`, 'error');
        }
    }

    onMount(fetchCameras);

    function showNotification(msg: string, type: 'success' | 'error' | 'info') {
        message = msg;
        messageType = type;
        setTimeout(() => {
            message = '';
            messageType = '';
        }, 4000);
    }

    async function saveCameras() {
        isSaving = true;
        try {
            const res = await fetch('/api/v1/cameras', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(cameras)
            });
            const data = await res.json();
            if (res.ok && data.success) {
                showNotification('Camera configurations saved. Restart AI Engine to apply.', 'success');
            } else {
                showNotification(data.error || 'Failed to persist camera configurations', 'error');
            }
        } catch (err: any) {
            showNotification(`Save error: ${err.message}`, 'error');
        } finally {
            isSaving = false;
        }
    }

    function addCamera() {
        if (!newCamName.trim()) {
            showNotification('Camera name is required', 'error');
            return;
        }

        const id = newCamName.toLowerCase().replace(/[^a-z0-9]+/g, '_');
        if (cameras.some(c => c.id === id)) {
            showNotification('A camera with this name or ID already exists', 'error');
            return;
        }

        const newCamera: CameraConfig = {
            id,
            name: newCamName,
            source: newCamSource,
            enabled: true,
            fps: newCamFps,
            confidence: newCamConfidence,
            enable_motion_gating: newCamMotionGating
        };

        if (newCamSource === 'rtsp') {
            newCamera.url = newCamUrl;
            if (newCamDetectUrl.trim()) {
                newCamera.detect_url = newCamDetectUrl;
            }
        }

        cameras = [...cameras, newCamera];

        newCamName = '';
        newCamSource = 'webcam';
        newCamUrl = '';
        newCamDetectUrl = '';
        newCamFps = 5;
        newCamConfidence = 0.80;
        newCamMotionGating = true;
        showAddForm = false;
        showNotification('New camera configuration appended. Click Save to persist.', 'success');
    }

    function removeCamera(id: string) {
        cameras = cameras.filter(c => c.id !== id);
        showNotification('Camera removed. Click Save to persist.', 'success');
    }

    onDestroy(() => {
        stopWebcam();
    });

    let cameraStats = $derived({
        total: cameras.length,
        enabled: cameras.filter(c => c.enabled).length,
        rtsp: cameras.filter(c => c.source === 'rtsp').length,
        webcam: cameras.filter(c => c.source === 'webcam').length
    });
</script>

<div class="flex flex-col gap-6 w-full pb-12 page-enter">

    <!-- Notification toast -->
    {#if message}
        <div class="fixed bottom-6 right-6 z-[100] max-w-sm" transition:fly={{ y: 20, duration: 200 }}>
            <div class="px-4 py-3 rounded-xl border backdrop-blur-md flex items-center gap-3
                {messageType === 'success' ? 'bg-jade/10 border-jade/30' : ''}
                {messageType === 'error' ? 'bg-crimson/10 border-crimson/30' : ''}
                {messageType === 'info' ? 'bg-cyan/10 border-cyan/30' : ''}">
                <span class="w-2 h-2 rounded-full"
                    class:bg-jade={messageType === 'success'}
                    class:bg-crimson={messageType === 'error'}
                    class:bg-cyan={messageType === 'info'}></span>
                <span class="text-xs font-semibold text-foreground">{message}</span>
            </div>
        </div>
    {/if}

    <!-- Header -->
    <header class="flex flex-col lg:flex-row lg:items-end justify-between gap-4">
        <div>
            <div class="flex items-center gap-2.5 mb-2">
                <span class="badge badge-cyan">
                    <span class="w-1.5 h-1.5 rounded-full bg-cyan status-pulse"></span>
                    Input Channels
                </span>
                <span class="text-[11px] text-muted-foreground font-mono">{cameraStats.enabled} of {cameraStats.total} active</span>
            </div>
            <h1 class="text-2xl md:text-3xl font-display font-bold text-foreground tracking-tight leading-none">Camera Channels</h1>
            <p class="text-sm text-muted-foreground mt-2">Register local hardware inputs and remote RTSP streams into the perception pipeline.</p>
        </div>

        <div class="flex items-center gap-2 flex-wrap">
            <div class="flex items-center gap-1.5 px-3 h-9 rounded-lg border border-border bg-card">
                <span class="text-[10px] font-mono font-bold text-foreground tabular-nums">{cameraStats.total}</span>
                <span class="text-[10px] text-muted-foreground uppercase tracking-wider">channels</span>
            </div>
            <div class="flex items-center gap-1.5 px-3 h-9 rounded-lg border border-jade/20 bg-jade/5">
                <span class="w-1.5 h-1.5 rounded-full bg-jade"></span>
                <span class="text-[10px] font-mono font-bold text-foreground tabular-nums">{cameraStats.enabled}</span>
                <span class="text-[10px] text-muted-foreground uppercase tracking-wider">live</span>
            </div>
            <div class="flex items-center gap-1.5 px-3 h-9 rounded-lg border border-border bg-card">
                <span class="text-[10px] font-mono font-bold text-foreground tabular-nums">{cameraStats.rtsp}</span>
                <span class="text-[10px] text-muted-foreground uppercase tracking-wider">rtsp</span>
            </div>
            <div class="flex items-center gap-1.5 px-3 h-9 rounded-lg border border-border bg-card">
                <span class="text-[10px] font-mono font-bold text-foreground tabular-nums">{cameraStats.webcam}</span>
                <span class="text-[10px] text-muted-foreground uppercase tracking-wider">usb</span>
            </div>
            <button onclick={() => showAddForm = !showAddForm} class="btn btn-iris btn-sm">
                <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">
                    <line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/>
                </svg>
                Add Channel
            </button>
            <button onclick={saveCameras} disabled={isSaving} class="btn btn-primary btn-sm">
                {#if isSaving}
                    <span class="w-3 h-3 rounded-full border-2 border-current border-t-transparent animate-spin"></span>
                    Saving…
                {:else}
                    <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">
                        <path d="M19 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11l5 5v11a2 2 0 0 1-2 2z"/>
                        <polyline points="17 21 17 13 7 13 7 21"/>
                        <polyline points="7 3 7 8 15 8"/>
                    </svg>
                    Save Changes
                {/if}
            </button>
        </div>
    </header>

    <!-- Add form -->
    {#if showAddForm}
        <section class="panel" transition:slide={{ duration: 200 }}>
            <div class="panel-header">
                <div class="flex items-center gap-2.5">
                    <div class="w-8 h-8 rounded-lg bg-iris/10 border border-iris/20 flex items-center justify-center text-iris">
                        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                            <line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/>
                        </svg>
                    </div>
                    <div>
                        <h2 class="text-sm font-display font-semibold text-foreground">Register New Stream Input</h2>
                        <p class="text-[10px] text-muted-foreground font-mono">Append a new channel to the perception network</p>
                    </div>
                </div>
                <button type="button" onclick={() => showAddForm = false} class="btn-icon !w-8 !h-8" aria-label="Close register form">
                    <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
                        <line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/>
                    </svg>
                </button>
            </div>

            <div class="panel-body">
                <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
                    <div class="flex flex-col gap-1.5">
                        <label for="name" class="section-eyebrow">Friendly Name</label>
                        <input id="name" type="text" bind:value={newCamName} placeholder="e.g. Front Porch Camera" class="input" />
                    </div>

                    <div class="flex flex-col gap-1.5">
                        <label for="source" class="section-eyebrow">Source Stream Type</label>
                        <select id="source" bind:value={newCamSource} class="select">
                            <option value="webcam">Local Webcam (WebRTC/USB)</option>
                            <option value="rtsp">Remote IP Stream (RTSP Link)</option>
                        </select>
                    </div>

                    {#if newCamSource === 'rtsp'}
                        <div class="flex flex-col gap-1.5 md:col-span-2">
                            <label for="url" class="section-eyebrow">RTSP Address (High-Res Record)</label>
                            <input id="url" type="text" bind:value={newCamUrl} placeholder="rtsp://username:password@192.168.1.100:554/live" class="input font-mono text-[11px]" />
                        </div>
                        <div class="flex flex-col gap-1.5 md:col-span-2">
                            <label for="detect_url" class="section-eyebrow">RTSP Sub-Stream (Low-Res AI, Optional)</label>
                            <input id="detect_url" type="text" bind:value={newCamDetectUrl} placeholder="rtsp://username:password@192.168.1.100:554/substream" class="input font-mono text-[11px]" />
                        </div>
                    {/if}
                </div>

                <div class="flex justify-end gap-2 pt-4 mt-2 border-t border-border">
                    <button onclick={() => showAddForm = false} class="btn btn-ghost btn-sm">Cancel</button>
                    <button onclick={addCamera} class="btn btn-iris btn-sm">
                        <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">
                            <path d="M12 5v14M5 12h14"/>
                        </svg>
                        Append Channel
                    </button>
                </div>
            </div>
        </section>
    {/if}

    <!-- Camera grid -->
    {#if cameras.length === 0}
        <div class="flex flex-col items-center justify-center text-center border border-dashed border-border rounded-2xl p-12 min-h-[420px]">
            <div class="w-16 h-16 rounded-2xl bg-surface-2 border border-border flex items-center justify-center text-muted-foreground mb-4">
                <svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" class="text-muted-foreground/60">
                    <path d="M23 19a2 2 0 0 1-2 2H3a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h4l2-3h6l2 3h4a2 2 0 0 1 2 2z"/>
                    <circle cx="12" cy="13" r="4"/>
                </svg>
            </div>
            <h3 class="text-base font-display font-semibold text-foreground">No cameras registered</h3>
            <p class="text-xs text-muted-foreground mt-1 max-w-sm">Register local hardware or remote RTSP connections to build the security network.</p>
            <button onclick={() => showAddForm = true} class="btn btn-primary btn-sm mt-4">
                <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">
                    <line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/>
                </svg>
                Register First Channel
            </button>
        </div>
    {:else}
        <div class="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
            {#each cameras as camera (camera.id)}
                <div class="panel !p-0 overflow-hidden transition-all"
                    class:opacity-60={!camera.enabled}>

                    <!-- Status accent stripe -->
                    <div class="h-1 w-full transition-colors"
                        class:bg-jade={camera.enabled}
                        class:bg-muted={!camera.enabled}></div>

                    <div class="p-4 flex flex-col gap-4">
                        <!-- Header: status + name + actions -->
                        <div class="flex items-start justify-between gap-3">
                            <div class="min-w-0 flex-1">
                                <div class="flex items-center gap-2 mb-1.5">
                                    <span class="w-1.5 h-1.5 rounded-full {camera.enabled ? 'bg-jade status-pulse' : 'bg-muted-foreground'}"></span>
                                    <span class="text-[10px] font-mono font-bold text-muted-foreground uppercase tracking-wider">{camera.enabled ? 'ONLINE' : 'STANDBY'}</span>
                                    <span class="text-[10px] font-mono text-muted-foreground/60">·</span>
                                    <span class="text-[10px] font-mono text-muted-foreground uppercase tracking-wider">{camera.source}</span>
                                </div>
                                <h3 class="text-base font-display font-semibold text-foreground truncate" title={camera.name}>{camera.name}</h3>
                                <p class="text-[10px] text-muted-foreground font-mono mt-0.5 truncate">{camera.id}</p>
                            </div>

                            <div class="flex flex-col items-end gap-2 shrink-0">
                                <!-- Toggle switch -->
                                <label class="relative inline-flex items-center cursor-pointer">
                                    <input type="checkbox" bind:checked={camera.enabled} class="sr-only peer" />
                                    <div class="w-9 h-5 rounded-full bg-muted border border-border peer-checked:bg-jade peer-checked:border-jade transition-all relative
                                        after:content-[''] after:absolute after:top-0.5 after:left-0.5 after:w-3.5 after:h-3.5 after:rounded-full after:bg-foreground after:transition-transform peer-checked:after:translate-x-4
                                        peer-focus-visible:ring-2 peer-focus-visible:ring-ring peer-focus-visible:ring-offset-2 peer-focus-visible:ring-offset-background"></div>
                                </label>
                                <button onclick={() => removeCamera(camera.id)} class="btn-icon !w-7 !h-7 hover:!text-crimson hover:!border-crimson/30" title="Delete">
                                    <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">
                                        <path d="M3 6h18M19 6v14c0 1-1 2-2 2H7c-1-1-2-1-2-2V6M8 6V4c0-1 1-2 2-2h4c1 0 2 1 2 2v2M10 11v6M14 11v6"/>
                                    </svg>
                                </button>
                            </div>
                        </div>

                        <!-- Mini feed preview -->
                        <div class="relative w-full aspect-video bg-black rounded-lg overflow-hidden border border-border surface-grid flex items-center justify-center">
                            <div class="absolute inset-0 flex flex-col items-center justify-center gap-1.5">
                                <div class="w-10 h-10 rounded-lg bg-surface-2/80 border border-border flex items-center justify-center text-muted-foreground">
                                    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">
                                        <path d="M23 19a2 2 0 0 1-2 2H3a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h4l2-3h6l2 3h4a2 2 0 0 1 2 2z"/>
                                        <circle cx="12" cy="13" r="4"/>
                                    </svg>
                                </div>
                                <span class="text-[9px] font-mono font-bold tracking-widest text-muted-foreground uppercase">{camera.enabled ? 'Channel Online' : 'Standby'}</span>
                            </div>

                            <!-- Mask count badge -->
                            {#if (camera.motion_masks?.length || 0) + (camera.alert_zones?.length || 0) > 0}
                                <div class="absolute bottom-2 left-2 flex items-center gap-1.5">
                                    {#if (camera.motion_masks?.length || 0) > 0}
                                        <span class="badge !h-5 !text-[9px] !bg-crimson/10 !text-crimson !border-crimson/30 backdrop-blur-sm">
                                            <span class="w-1 h-1 rounded-full bg-crimson"></span>
                                            {camera.motion_masks!.length} MASK
                                        </span>
                                    {/if}
                                    {#if (camera.alert_zones?.length || 0) > 0}
                                        <span class="badge !h-5 !text-[9px] !bg-jade/10 !text-jade !border-jade/30 backdrop-blur-sm">
                                            <span class="w-1 h-1 rounded-full bg-jade"></span>
                                            {camera.alert_zones!.length} ZONE
                                        </span>
                                    {/if}
                                </div>
                            {/if}

                            <!-- Quick metrics -->
                            <div class="absolute top-2 right-2 flex items-center gap-1.5">
                                <span class="badge !h-5 !text-[9px] !bg-black/60 !border-white/10 backdrop-blur-sm font-mono">{camera.fps}FPS</span>
                            </div>
                        </div>

                        <!-- Quick stats -->
                        <div class="grid grid-cols-3 gap-2 text-[10px] font-mono">
                            <div class="flex flex-col gap-0.5 px-2.5 py-1.5 rounded-md bg-surface-2/40 border border-border/50">
                                <span class="text-muted-foreground uppercase tracking-wider">FPS</span>
                                <span class="text-sm font-bold text-foreground tabular-nums">{camera.fps}</span>
                            </div>
                            <div class="flex flex-col gap-0.5 px-2.5 py-1.5 rounded-md bg-surface-2/40 border border-border/50">
                                <span class="text-muted-foreground uppercase tracking-wider">YOLO</span>
                                <span class="text-sm font-bold text-foreground tabular-nums">{Math.round(camera.confidence * 100)}%</span>
                            </div>
                            <div class="flex flex-col gap-0.5 px-2.5 py-1.5 rounded-md bg-surface-2/40 border border-border/50">
                                <span class="text-muted-foreground uppercase tracking-wider">Gating</span>
                                <span class="text-sm font-bold {camera.enable_motion_gating ? 'text-jade' : 'text-muted-foreground'}">{camera.enable_motion_gating ? 'ON' : 'OFF'}</span>
                            </div>
                        </div>

                        <!-- Configure trigger -->
                        <button
                            onclick={() => expandedCameraSettings[camera.id] = !expandedCameraSettings[camera.id]}
                            class="btn btn-ghost btn-sm w-full justify-between"
                        >
                            <span class="flex items-center gap-2">
                                <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                                    <circle cx="12" cy="12" r="3"/>
                                    <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1 0 2.83 2 2 0 0 1-2.83 0l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83 0 2 2 0 0 1 0-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 0-2.83 2 2 0 0 1 2.83 0l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 0 2 2 0 0 1 0 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z"/>
                                </svg>
                                {expandedCameraSettings[camera.id] ? 'Hide Configuration' : 'Configure Channel'}
                            </span>
                            <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"
                                class="transition-transform {expandedCameraSettings[camera.id] ? 'rotate-180' : ''}">
                                <polyline points="6 9 12 15 18 9"/>
                            </svg>
                        </button>

                        {#if expandedCameraSettings[camera.id]}
                            <div class="flex flex-col gap-3 pt-3 border-t border-border" transition:slide={{ duration: 200 }}>
                                <div class="flex flex-col gap-1.5">
                                    <label for="edit-name-{camera.id}" class="section-eyebrow">Friendly Name</label>
                                    <input id="edit-name-{camera.id}" type="text" bind:value={camera.name} class="input" />
                                </div>

                                {#if camera.source === 'rtsp'}
                                    <div class="flex flex-col gap-1.5">
                                        <label for="edit-url-{camera.id}" class="section-eyebrow">RTSP Stream (High-Res Record)</label>
                                        <input id="edit-url-{camera.id}" type="text" bind:value={camera.url} class="input font-mono text-[11px]" />
                                    </div>
                                    <div class="flex flex-col gap-1.5">
                                        <label for="edit-detect-url-{camera.id}" class="section-eyebrow">RTSP Sub-Stream (Low-Res AI, Optional)</label>
                                        <input id="edit-detect-url-{camera.id}" type="text" bind:value={camera.detect_url} placeholder="Sub-stream link (falls back to main stream)" class="input font-mono text-[11px]" />
                                    </div>
                                {/if}

                                <div class="flex flex-col gap-1.5">
                                    <div class="flex justify-between items-center">
                                        <label for="edit-fps-{camera.id}" class="section-eyebrow">Processor FPS Target</label>
                                        <span class="text-[10px] font-mono font-bold text-cyan">{camera.fps} FPS</span>
                                    </div>
                                    <input id="edit-fps-{camera.id}" type="range" min="1" max="30" bind:value={camera.fps} class="range" />
                                </div>

                                <div class="flex flex-col gap-1.5">
                                    <div class="flex justify-between items-center">
                                        <label for="edit-confidence-{camera.id}" class="section-eyebrow">YOLO Confidence Threshold</label>
                                        <span class="text-[10px] font-mono font-bold text-cyan">{Math.round(camera.confidence * 100)}%</span>
                                    </div>
                                    <input id="edit-confidence-{camera.id}" type="range" min="0.10" max="0.95" step="0.05" bind:value={camera.confidence} class="range" />
                                </div>

                                <label class="flex items-center gap-2.5 p-2.5 rounded-md border border-border bg-surface-2/40 cursor-pointer hover:bg-surface-2 transition-colors">
                                    <input type="checkbox" bind:checked={camera.enable_motion_gating} class="checkbox" />
                                    <span class="text-xs font-semibold text-foreground">Enable Motion Gating</span>
                                    <span class="text-[10px] text-muted-foreground font-mono ml-auto">Saves CPU</span>
                                </label>

                                {#if camera.enable_motion_gating}
                                    <button
                                        type="button"
                                        onclick={() => openMaskEditor(camera)}
                                        class="btn btn-iris btn-sm w-full"
                                    >
                                        <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                                            <path d="M12 19l7-7 3 3-7 7-3-3z"/>
                                            <path d="M18 13l-1.5-7.5L2 2l3.5 14.5L13 18l5-5z"/>
                                            <path d="M2 2l7.586 7.586"/>
                                            <circle cx="11" cy="11" r="2"/>
                                        </svg>
                                        Draw Exclusion Masks &amp; Alert Zones
                                    </button>
                                {/if}
                            </div>
                        {/if}
                    </div>
                </div>
            {/each}
        </div>
    {/if}
</div>

<!-- Polygon editor modal -->
{#if activeEditorCamera}
    <div class="fixed inset-0 z-[100] flex items-center justify-center p-4 bg-black/70 backdrop-blur-sm"
        transition:fade={{ duration: 150 }}>
        <div class="panel w-full max-w-4xl max-h-[90vh] flex flex-col"
            transition:fly={{ y: 20, duration: 200 }}>

            <div class="panel-header">
                <div class="flex flex-col gap-2">
                    <div>
                        <h2 class="text-base font-display font-semibold text-foreground">Surveillance Regions Editor</h2>
                        <p class="text-[10px] text-muted-foreground font-mono">{activeEditorCamera.name} · click to add points, click first point to close</p>
                    </div>
                    <!-- Mode switcher -->
                    <div class="flex items-center bg-surface-2 border border-border rounded-lg p-0.5 h-8 self-start">
                        <button
                            type="button"
                            onclick={() => { drawingMode = 'mask'; drawingPoints = []; }}
                            class="h-6 px-3 rounded-md text-[10px] font-bold uppercase tracking-wider transition-all flex items-center gap-1.5"
                            class:bg-crimson={drawingMode === 'mask'}
                            class:text-crimson-foreground={drawingMode === 'mask'}
                            class:text-muted-foreground={drawingMode !== 'mask'}
                        >
                            <span class="w-1.5 h-1.5 rounded-full"
                                class:bg-crimson-foreground={drawingMode === 'mask'}
                                class:bg-crimson={drawingMode !== 'mask'}></span>
                            Exclusion Mask
                        </button>
                        <button
                            type="button"
                            onclick={() => { drawingMode = 'zone'; drawingPoints = []; }}
                            class="h-6 px-3 rounded-md text-[10px] font-bold uppercase tracking-wider transition-all flex items-center gap-1.5"
                            class:bg-jade={drawingMode === 'zone'}
                            class:text-jade-foreground={drawingMode === 'zone'}
                            class:text-muted-foreground={drawingMode !== 'zone'}
                        >
                            <span class="w-1.5 h-1.5 rounded-full"
                                class:bg-jade-foreground={drawingMode === 'zone'}
                                class:bg-jade={drawingMode !== 'zone'}></span>
                            Alert Zone
                        </button>
                    </div>
                </div>
                <button type="button" onclick={closeMaskEditor} class="btn-icon !w-9 !h-9" aria-label="Close regions editor">
                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
                        <line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/>
                    </svg>
                </button>
            </div>

            <div class="flex-1 overflow-auto p-5">
                <div class="grid grid-cols-1 lg:grid-cols-3 gap-4">
                    <!-- Canvas -->
                    <div class="lg:col-span-2">
                        <div class="relative w-full aspect-video bg-black rounded-xl border border-border overflow-hidden surface-grid">
                            {#if activeEditorCamera.source === 'webcam'}
                                <!-- svelte-ignore a11y_media_has_caption -->
                                <video
                                    use:bindStream={editorStream}
                                    autoplay
                                    playsinline
                                    muted
                                    class="absolute inset-0 w-full h-full object-cover"
                                ></video>
                            {/if}

                            <svg
                                bind:this={svgElement}
                                onclick={handleSvgClick}
                                onmousemove={handleSvgMouseMove}
                                class="absolute inset-0 w-full h-full cursor-crosshair"
                                viewBox="0 0 1000 1000"
                                preserveAspectRatio="none"
                                role="button"
                                tabindex="0"
                                aria-label="Polygon editor"
                                onkeydown={(e) => {
                                    if (e.key === 'Escape') cancelDrawing();
                                    if (e.key === 'Enter') closePolygon();
                                }}
                            >
                                {#if activeEditorCamera.motion_masks}
                                    {#each activeEditorCamera.motion_masks as mask, idx}
                                        <polygon
                                            points={mask.map(pt => `${pt[0] * 1000},${pt[1] * 1000}`).join(' ')}
                                            fill="hsl(0 78% 64% / 0.15)"
                                            stroke="hsl(0 78% 64%)"
                                            stroke-width="3"
                                        />
                                    {/each}
                                {/if}

                                {#if activeEditorCamera.alert_zones}
                                    {#each activeEditorCamera.alert_zones as zone, idx}
                                        <polygon
                                            points={zone.map(pt => `${pt[0] * 1000},${pt[1] * 1000}`).join(' ')}
                                            fill="hsl(152 68% 52% / 0.12)"
                                            stroke="hsl(152 68% 52%)"
                                            stroke-width="3"
                                        />
                                    {/each}
                                {/if}

                                {#if drawingPoints.length > 0}
                                    <polyline
                                        points={drawingPoints.map(pt => `${pt[0] * 1000},${pt[1] * 1000}`).join(' ')}
                                        fill="none"
                                        stroke={drawingMode === 'zone' ? 'hsl(152 68% 52%)' : 'hsl(0 78% 64%)'}
                                        stroke-width="3"
                                        stroke-dasharray="6 4"
                                    />
                                    <line
                                        x1={drawingPoints[drawingPoints.length - 1][0] * 1000}
                                        y1={drawingPoints[drawingPoints.length - 1][1] * 1000}
                                        x2={cursorX * 1000}
                                        y2={cursorY * 1000}
                                        stroke={drawingMode === 'zone' ? 'hsl(152 68% 52%)' : 'hsl(0 78% 64%)'}
                                        stroke-width="2"
                                        stroke-dasharray="4 4"
                                    />
                                {/if}

                                {#each drawingPoints as pt, idx}
                                    <circle
                                        cx={pt[0] * 1000}
                                        cy={pt[1] * 1000}
                                        r={idx === 0 ? 14 : 9}
                                        fill={idx === 0 ? (drawingMode === 'zone' ? 'hsl(152 68% 52%)' : 'hsl(0 78% 64%)') : '#fff'}
                                        stroke={drawingMode === 'zone' ? 'hsl(152 68% 52%)' : 'hsl(0 78% 64%)'}
                                        stroke-width="3"
                                    />
                                {/each}
                            </svg>

                            <!-- Mode hint overlay -->
                            <div class="absolute top-3 left-3 right-3 flex items-center pointer-events-none">
                                <div class="px-2.5 h-7 rounded-md border backdrop-blur-sm flex items-center text-[10px] font-mono font-bold uppercase tracking-wider
                                    {drawingMode === 'mask' ? 'bg-crimson/20 border-crimson/40 text-crimson' : ''}
                                    {drawingMode === 'zone' ? 'bg-jade/20 border-jade/40 text-jade' : ''}">
                                    {drawingMode === 'mask' ? 'Drawing Exclusion Mask' : 'Drawing Alert Zone'}
                                </div>
                            </div>

                            {#if drawingPoints.length < 3}
                                <div class="absolute bottom-3 left-3 px-2.5 h-7 rounded-md border border-border bg-black/70 backdrop-blur-sm flex items-center text-[10px] font-mono text-foreground">
                                    {drawingPoints.length === 0 ? 'Click to place first point' : `${drawingPoints.length} points · ${3 - drawingPoints.length} more to close`}
                                </div>
                            {:else}
                                <div class="absolute bottom-3 left-3 px-2.5 h-7 rounded-md border border-jade/40 bg-jade/20 backdrop-blur-sm flex items-center text-[10px] font-mono text-jade font-bold">
                                    Click first point or "Close Shape" to finish
                                </div>
                            {/if}
                        </div>
                    </div>

                    <!-- Region registry -->
                    <div class="flex flex-col gap-3">
                        <div class="panel !p-0 overflow-hidden">
                            <div class="px-3 py-2.5 border-b border-border flex items-center justify-between">
                                <div class="flex items-center gap-2">
                                    <span class="w-1.5 h-1.5 rounded-full bg-crimson"></span>
                                    <span class="text-[10px] font-mono font-bold text-foreground uppercase tracking-wider">Exclusion Masks</span>
                                </div>
                                <span class="badge !h-5 !text-[9px] !bg-crimson/10 !text-crimson !border-crimson/20 font-mono">{activeEditorCamera.motion_masks?.length || 0}</span>
                            </div>
                            <div class="p-3 max-h-32 overflow-y-auto no-scrollbar">
                                {#if !activeEditorCamera.motion_masks || activeEditorCamera.motion_masks.length === 0}
                                    <span class="text-[11px] text-muted-foreground italic">No active masks</span>
                                {:else}
                                    <div class="flex flex-col gap-1.5">
                                        {#each activeEditorCamera.motion_masks as mask, idx}
                                            <div class="flex items-center justify-between gap-2 px-2 py-1.5 rounded-md border border-crimson/20 bg-crimson/5">
                                                <div class="flex items-center gap-2 min-w-0">
                                                    <span class="w-1.5 h-1.5 rounded-full bg-crimson"></span>
                                                    <span class="text-[10px] font-mono font-bold text-foreground">Mask #{idx + 1}</span>
                                                </div>
                                                <button onclick={() => removePolygon(idx, 'mask')} type="button" class="btn-icon !w-5 !h-5 hover:!text-crimson" title="Remove">
                                                    <svg width="9" height="9" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
                                                </button>
                                            </div>
                                        {/each}
                                    </div>
                                {/if}
                            </div>
                        </div>

                        <div class="panel !p-0 overflow-hidden">
                            <div class="px-3 py-2.5 border-b border-border flex items-center justify-between">
                                <div class="flex items-center gap-2">
                                    <span class="w-1.5 h-1.5 rounded-full bg-jade"></span>
                                    <span class="text-[10px] font-mono font-bold text-foreground uppercase tracking-wider">Alert Zones</span>
                                </div>
                                <span class="badge !h-5 !text-[9px] !bg-jade/10 !text-jade !border-jade/20 font-mono">{activeEditorCamera.alert_zones?.length || 0}</span>
                            </div>
                            <div class="p-3 max-h-32 overflow-y-auto no-scrollbar">
                                {#if !activeEditorCamera.alert_zones || activeEditorCamera.alert_zones.length === 0}
                                    <span class="text-[11px] text-muted-foreground italic">No active zones</span>
                                {:else}
                                    <div class="flex flex-col gap-1.5">
                                        {#each activeEditorCamera.alert_zones as zone, idx}
                                            <div class="flex items-center justify-between gap-2 px-2 py-1.5 rounded-md border border-jade/20 bg-jade/5">
                                                <div class="flex items-center gap-2 min-w-0">
                                                    <span class="w-1.5 h-1.5 rounded-full bg-jade"></span>
                                                    <span class="text-[10px] font-mono font-bold text-foreground">Zone #{idx + 1}</span>
                                                </div>
                                                <button onclick={() => removePolygon(idx, 'zone')} type="button" class="btn-icon !w-5 !h-5 hover:!text-crimson" title="Remove">
                                                    <svg width="9" height="9" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
                                                </button>
                                            </div>
                                        {/each}
                                    </div>
                                {/if}
                            </div>
                        </div>

                        <div class="panel-inset p-3">
                            <span class="section-eyebrow block mb-2">Legend</span>
                            <div class="flex flex-col gap-1.5 text-[10px] font-mono">
                                <div class="flex items-center gap-2">
                                    <span class="w-2 h-2 rounded-full bg-crimson"></span>
                                    <span class="text-foreground">Motion gating exclusion</span>
                                </div>
                                <div class="flex items-center gap-2">
                                    <span class="w-2 h-2 rounded-full bg-jade"></span>
                                    <span class="text-foreground">Activity alert zone</span>
                                </div>
                            </div>
                        </div>
                    </div>
                </div>
            </div>

            <div class="flex justify-between items-center px-5 py-4 border-t border-border">
                <div class="flex gap-2">
                    {#if drawingPoints.length >= 3}
                        <button onclick={closePolygon} type="button" class="btn btn-primary btn-sm">
                            <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">
                                <path d="M20 6 9 17l-5-5"/>
                            </svg>
                            Close Shape
                        </button>
                    {/if}
                    {#if drawingPoints.length > 0}
                        <button onclick={cancelDrawing} type="button" class="btn btn-ghost btn-sm">Cancel Draw</button>
                    {/if}
                </div>

                <div class="flex gap-2">
                    {#if (drawingMode === 'mask' && activeEditorCamera.motion_masks && activeEditorCamera.motion_masks.length > 0) || (drawingMode === 'zone' && activeEditorCamera.alert_zones && activeEditorCamera.alert_zones.length > 0)}
                        <button onclick={clearAllPolygons} type="button" class="btn btn-sm hover:!text-crimson hover:!border-crimson/30">
                            <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">
                                <path d="M3 6h18M19 6v14c0 1-1 2-2 2H7c-1-1-2-1-2-2V6M8 6V4c0-1 1-2 2-2h4c1 0 2 1 2 2v2M10 11v6M14 11v6"/>
                            </svg>
                            Reset Shapes
                        </button>
                    {/if}
                    <button onclick={closeMaskEditor} type="button" class="btn btn-primary btn-sm">Done</button>
                </div>
            </div>
        </div>
    </div>
{/if}
