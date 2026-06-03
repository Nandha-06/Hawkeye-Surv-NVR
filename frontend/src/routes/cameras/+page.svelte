<script lang="ts">
    import { onMount, onDestroy } from 'svelte';
    import { slide } from 'svelte/transition';

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
    let messageType = $state<'success' | 'error' | ''>('');

    // Toggle advanced configuration panel per camera card
    let expandedCameraSettings = $state<Record<string, boolean>>({});

    // Motion Gating & Alert Zone Polygon Editor state
    let activeEditorCamera = $state<CameraConfig | null>(null);
    let drawingPoints = $state<[number, number][]>([]);
    let cursorX = $state(0);
    let cursorY = $state(0);
    let svgElement = $state<SVGElement | null>(null);
    let drawingMode = $state<'mask' | 'zone'>('mask'); // 'mask' = exclusion mask, 'zone' = alert zone

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
            if (dist < 0.03) { // 3% proximity
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

    // Form state for creating a new camera
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

    function showNotification(msg: string, type: 'success' | 'error') {
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
                showNotification('Camera configurations saved successfully! Restart AI Engine to apply.', 'success');
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

        // Reset form
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
</script>

<div class="cameras-layout">
    <!-- Notifications -->
    {#if message}
        <div class="notification-pill {messageType}">
            <div class="pill-glow"></div>
            <span>{message}</span>
        </div>
    {/if}

    <header class="cameras-header flex items-center justify-between">
        <div class="header-left">
            <h1>📷 Camera Channels</h1>
            <p class="subtitle">Configure and register local hardware inputs and remote RTSP streams</p>
        </div>
        
        <div class="actions-group flex gap-3">
            <button onclick={() => showAddForm = !showAddForm} class="glass-btn">
                Add Channel
            </button>
            <button onclick={saveCameras} disabled={isSaving} class="glass-btn primary">
                {#if isSaving}
                    Saving...
                {:else}
                    Save Changes
                {/if}
            </button>
        </div>
    </header>

    {#if showAddForm}
        <section class="add-camera-panel glass-panel" transition:slide>
            <h3>Register New Stream Input</h3>
            <div class="grid-form p-4">
                <div class="form-group">
                    <label for="name">Friendly Name</label>
                    <input id="name" type="text" bind:value={newCamName} placeholder="e.g. Front Porch Camera" />
                </div>
                
                <div class="form-group">
                    <label for="source">Source Stream Type</label>
                    <select id="source" bind:value={newCamSource}>
                        <option value="webcam">Local Webcam (WebRTC/USB)</option>
                        <option value="rtsp">Remote IP Stream (RTSP Link)</option>
                    </select>
                </div>

                {#if newCamSource === 'rtsp'}
                    <div class="form-group">
                        <label for="url">RTSP Address (High-Res Record)</label>
                        <input id="url" type="text" bind:value={newCamUrl} placeholder="rtsp://username:password@192.168.1.100:554/live" />
                    </div>
                    <div class="form-group">
                        <label for="detect_url">RTSP Sub-Stream (Low-Res AI, Optional)</label>
                        <input id="detect_url" type="text" bind:value={newCamDetectUrl} placeholder="rtsp://username:password@192.168.1.100:554/substream" />
                    </div>
                {/if}

                <div class="form-group span-2 flex justify-end gap-3 mt-3">
                    <button onclick={() => showAddForm = false} class="glass-btn">Cancel</button>
                    <button onclick={addCamera} class="glass-btn primary">Append Channel</button>
                </div>
            </div>
        </section>
    {/if}

    <section class="cameras-stack">
        {#if cameras.length === 0}
            <div class="empty-state text-center p-8 glass-panel">
                <svg width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" class="muted-icon mx-auto mb-2">
                    <path d="M23 19a2 2 0 0 1-2 2H3a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h4l2-3h6l2 3h4a2 2 0 0 1 2 2z"></path>
                    <circle cx="12" cy="13" r="4"></circle>
                </svg>
                <h3>No Cameras Registered</h3>
                <p>Register local cameras or remote RTSP connections above to build your security network.</p>
            </div>
        {:else}
            <div class="cameras-grid">
                {#each cameras as camera}
                    <div class="camera-card glass-panel" class:disabled={!camera.enabled}>
                        <!-- Card Header -->
                        <div class="card-header">
                            <div class="flex items-center gap-2">
                                <div class="status-dot" class:enabled={camera.enabled}></div>
                                <h3 class="camera-name">{camera.name}</h3>
                            </div>
                            
                            <div class="flex items-center gap-3">
                                <label class="switch">
                                    <input type="checkbox" bind:checked={camera.enabled} />
                                    <span class="slider"></span>
                                </label>
                                
                                <button onclick={() => removeCamera(camera.id)} class="btn-delete" title="Delete camera settings">
                                    🗑️
                                </button>
                            </div>
                        </div>

                        <!-- Card Body -->
                        <div class="card-body">
                            <!-- Visual Placeholder Feed thumbnail -->
                            <div class="feed-thumbnail-placeholder">
                                <div class="scanlines"></div>
                                <div class="hud-center">
                                    <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" style="opacity: 0.4;">
                                        <path d="M23 19a2 2 0 0 1-2 2H3a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h4l2-3h6l2 3h4a2 2 0 0 1 2 2z"/><circle cx="12" cy="13" r="4"/>
                                    </svg>
                                    <span class="lbl">{camera.enabled ? 'CHANNEL ONLINE' : 'STANDBY'}</span>
                                </div>
                            </div>

                            <div class="simple-metadata">
                                <div class="meta-row">
                                    <span class="meta-lbl">Pipeline ID</span>
                                    <span class="meta-val font-mono">{camera.id}</span>
                                </div>
                                <div class="meta-row">
                                    <span class="meta-lbl">Stream Source</span>
                                    <span class="meta-val uppercase">{camera.source}</span>
                                </div>
                            </div>

                            <!-- Expandable advanced configuration triggers -->
                            <button onclick={() => expandedCameraSettings[camera.id] = !expandedCameraSettings[camera.id]} class="glass-btn config-btn">
                                ⚙️ {expandedCameraSettings[camera.id] ? 'Hide Configuration' : 'Configure Channel'}
                            </button>

                            <!-- Slider Accordion drawer -->
                            {#if expandedCameraSettings[camera.id]}
                                <div class="advanced-config-drawer" transition:slide>
                                    <div class="inputs-grid">
                                        <div class="form-group span-2">
                                            <label>Friendly Name</label>
                                            <input type="text" bind:value={camera.name} />
                                        </div>

                                        {#if camera.source === 'rtsp'}
                                             <div class="form-group">
                                                 <label>RTSP Stream (High-Res Record)</label>
                                                 <input type="text" bind:value={camera.url} />
                                             </div>
                                             <div class="form-group">
                                                 <label>RTSP Sub-Stream (Low-Res AI, Optional)</label>
                                                 <input type="text" bind:value={camera.detect_url} placeholder="Sub-stream link (falls back to main stream)" />
                                             </div>
                                         {/if}

                                        <div class="form-group">
                                            <label class="slider-lbl">
                                                <span>Processor FPS Target</span>
                                                <span class="val-badge">{camera.fps} FPS</span>
                                            </label>
                                            <input type="range" min="1" max="30" bind:value={camera.fps} class="styled-range" />
                                        </div>

                                        <div class="form-group">
                                            <label class="slider-lbl">
                                                <span>YOLO Threshold</span>
                                                <span class="val-badge">{Math.round(camera.confidence * 100)}%</span>
                                            </label>
                                            <input type="range" min="0.10" max="0.95" step="0.05" bind:value={camera.confidence} class="styled-range" />
                                        </div>

                                        <div class="form-group flex-row span-2">
                                            <label class="checkbox-container">
                                                <input type="checkbox" bind:checked={camera.enable_motion_gating} />
                                                <span>Enable Motion Gating (Saves CPU)</span>
                                            </label>
                                        </div>

                                         {#if camera.enable_motion_gating}
                                             <div class="form-group span-2" style="margin-top: 0.5rem;">
                                                 <button 
                                                     type="button" 
                                                     onclick={() => openMaskEditor(camera)}
                                                     class="glass-btn mask-btn"
                                                 >
                                                     🎨 Draw Exclusion Masks & Alert Zones
                                                 </button>
                                             </div>
                                         {/if}
                                    </div>
                                </div>
                            {/if}
                        </div>
                    </div>
                {/each}
            </div>
        {/if}
    </section>

    <!-- Interactive polygon SVG canvas editor modal -->
    {#if activeEditorCamera}
        <div class="mask-modal-overlay">
            <div class="mask-modal-content glass-panel">
                <div class="modal-header">
                    <div>
                        <h2>Surveillance Regions Editor</h2>
                        <div class="drawing-mode-selector flex gap-2 mt-2">
                            <button 
                                type="button" 
                                onclick={() => { drawingMode = 'mask'; drawingPoints = []; }}
                                class="mode-btn mode-mask" 
                                class:active={drawingMode === 'mask'}
                            >
                                🔴 Exclusion Mask
                            </button>
                            <button 
                                type="button" 
                                onclick={() => { drawingMode = 'zone'; drawingPoints = []; }}
                                class="mode-btn mode-zone" 
                                class:active={drawingMode === 'zone'}
                            >
                                🟢 Alert Zone
                            </button>
                        </div>
                    </div>
                    <button onclick={closeMaskEditor} class="btn-close-modal" type="button" title="Close Editor">
                        ×
                    </button>
                </div>

                <div class="modal-body flex flex-col gap-4">
                    <div class="mask-editor-canvas-container">
                        {#if activeEditorCamera.source === 'webcam'}
                            <!-- svelte-ignore a11y_media_has_caption -->
                            <video
                                use:bindStream={editorStream}
                                autoplay
                                playsinline
                                muted
                                class="editor-video-feed"
                            ></video>
                        {/if}

                        {#if !editorStream}
                            <div class="hud-grid"></div>
                            <div class="hud-text text-center">
                                <span class="hud-title uppercase">Live Viewport Placeholder</span>
                                {#if drawingMode === 'mask'}
                                    <span class="hud-desc">🔴 Exclusion Mask: Areas where motion is completely ignored.</span>
                                {:else}
                                    <span class="hud-desc">🟢 Alert Zone: Areas where detections scale alerts to full severity.</span>
                                {/if}
                            </div>
                        {/if}

                        <svg
                            bind:this={svgElement}
                            onclick={handleSvgClick}
                            onmousemove={handleSvgMouseMove}
                            class="mask-svg-workspace"
                            viewBox="0 0 1000 1000"
                            preserveAspectRatio="none"
                            role="button"
                            tabindex="0"
                            aria-label="Motion mask canvas editor"
                            onkeydown={(e) => {
                                if (e.key === 'Escape') cancelDrawing();
                                if (e.key === 'Enter') closePolygon();
                            }}
                        >
                            {#if activeEditorCamera.motion_masks}
                                {#each activeEditorCamera.motion_masks as mask, idx}
                                    <polygon
                                        points={mask.map(pt => `${pt[0] * 1000},${pt[1] * 1000}`).join(' ')}
                                        class="closed-mask-poly"
                                    />
                                {/each}
                            {/if}

                            {#if activeEditorCamera.alert_zones}
                                {#each activeEditorCamera.alert_zones as zone, idx}
                                    <polygon
                                        points={zone.map(pt => `${pt[0] * 1000},${pt[1] * 1000}`).join(' ')}
                                        class="closed-zone-poly"
                                    />
                                {/each}
                            {/if}

                            {#if drawingPoints.length > 0}
                                <polyline
                                    points={drawingPoints.map(pt => `${pt[0] * 1000},${pt[1] * 1000}`).join(' ')}
                                    class="drawing-poly-line"
                                    class:drawing-zone-line={drawingMode === 'zone'}
                                />
                                <line
                                    x1={drawingPoints[drawingPoints.length - 1][0] * 1000}
                                    y1={drawingPoints[drawingPoints.length - 1][1] * 1000}
                                    x2={cursorX * 1000}
                                    y2={cursorY * 1000}
                                    class="rubberband-line"
                                    class:rubberband-zone-line={drawingMode === 'zone'}
                                />
                            {/if}

                            {#each drawingPoints as pt, idx}
                                <circle
                                    cx={pt[0] * 1000}
                                    cy={pt[1] * 1000}
                                    r={idx === 0 ? 12 : 8}
                                    class="drawing-node"
                                    class:drawing-zone-node={drawingMode === 'zone'}
                                    class:first-node={idx === 0}
                                />
                            {/each}
                        </svg>
                    </div>

                    <div class="mask-details-container flex flex-col gap-3">
                        <div class="glass-panel p-3">
                            <h4 class="section-title text-xs uppercase tracking-wider mb-2" style="color: var(--accent-rose);">🔴 Exclusion Masks ({activeEditorCamera.motion_masks?.length || 0})</h4>
                            {#if !activeEditorCamera.motion_masks || activeEditorCamera.motion_masks.length === 0}
                                <div class="empty-masks-state text-xs text-white/40 text-center py-2">
                                    No active motion exclusion masks.
                                </div>
                            {:else}
                                <div class="masks-chips flex flex-wrap gap-2">
                                    {#each activeEditorCamera.motion_masks as mask, idx}
                                        <div class="mask-chip flex items-center gap-2">
                                            <span class="chip-color mask-color"></span>
                                            <span class="chip-label text-xs">Mask #{idx + 1}</span>
                                            <button onclick={() => removePolygon(idx, 'mask')} type="button" class="chip-delete">×</button>
                                        </div>
                                    {/each}
                                </div>
                            {/if}
                        </div>

                        <div class="glass-panel p-3">
                            <h4 class="section-title text-xs uppercase tracking-wider mb-2" style="color: var(--accent-emerald);">🟢 Alert Zones ({activeEditorCamera.alert_zones?.length || 0})</h4>
                            {#if !activeEditorCamera.alert_zones || activeEditorCamera.alert_zones.length === 0}
                                <div class="empty-masks-state text-xs text-white/40 text-center py-2">
                                    No active activity alert zones.
                                </div>
                            {:else}
                                <div class="masks-chips flex flex-wrap gap-2">
                                    {#each activeEditorCamera.alert_zones as zone, idx}
                                        <div class="mask-chip flex items-center gap-2">
                                            <span class="chip-color zone-color"></span>
                                            <span class="chip-label text-xs">Zone #{idx + 1}</span>
                                            <button onclick={() => removePolygon(idx, 'zone')} type="button" class="chip-delete">×</button>
                                        </div>
                                    {/each}
                                </div>
                            {/if}
                        </div>
                    </div>
                </div>

                <div class="modal-footer">
                    <div class="flex gap-2">
                        {#if drawingPoints.length >= 3}
                            <button onclick={closePolygon} type="button" class="glass-btn primary text-xs">
                                Close Shape
                            </button>
                        {/if}
                        {#if drawingPoints.length > 0}
                            <button onclick={cancelDrawing} type="button" class="glass-btn text-xs">
                                Cancel Draw
                            </button>
                        {/if}
                    </div>

                    <div class="flex gap-2">
                        {#if (drawingMode === 'mask' && activeEditorCamera.motion_masks && activeEditorCamera.motion_masks.length > 0) || (drawingMode === 'zone' && activeEditorCamera.alert_zones && activeEditorCamera.alert_zones.length > 0)}
                            <button onclick={clearAllPolygons} type="button" class="glass-btn text-xs text-red-400">
                                Reset Shapes
                            </button>
                        {/if}
                        <button onclick={closeMaskEditor} type="button" class="glass-btn primary text-xs">
                            Done
                        </button>
                    </div>
                </div>
            </div>
        </div>
    {/if}
</div>

<style>
    .cameras-layout {
        display: flex;
        flex-direction: column;
        gap: 1rem;
        height: calc(100vh - 88px);
        box-sizing: border-box;
    }

    .cameras-header {
        display: flex;
        justify-content: space-between;
        align-items: center;
        padding: 0.75rem 1.25rem;
    }

    .cameras-header h1 {
        font-family: var(--font-display);
        font-size: 1.2rem;
        font-weight: 700;
        margin: 0;
    }

    .subtitle {
        color: var(--text-muted);
        font-size: 0.75rem;
        margin: 0.15rem 0 0 0;
    }

    .glass-panel {
        background: var(--bg-panel);
        border: 1px solid var(--border-glass);
        border-radius: 12px;
    }

    .glass-btn.primary {
        background: var(--accent-indigo);
        border-color: var(--accent-indigo);
        color: white;
    }

    .add-camera-panel {
        padding: 1rem;
        margin-bottom: 0.5rem;
    }

    .add-camera-panel h3 {
        margin: 0 0 0.75rem 0;
        font-size: 0.85rem;
        text-transform: uppercase;
        letter-spacing: 0.05em;
        color: var(--text-secondary);
    }

    .grid-form {
        display: grid;
        grid-template-columns: repeat(2, 1fr);
        gap: 1rem;
    }

    .form-group {
        display: flex;
        flex-direction: column;
        gap: 0.25rem;
    }

    .form-group.span-2 {
        grid-column: span 2;
    }

    .form-group.flex-row {
        flex-direction: row;
        align-items: center;
    }

    label {
        font-size: 0.65rem;
        font-weight: 600;
        color: var(--text-muted);
        text-transform: uppercase;
        letter-spacing: 0.05em;
    }

    input[type="text"], select {
        padding: 0.45rem 0.65rem;
        background: var(--bg-control);
        border: 1px solid var(--border-color);
        border-radius: 6px;
        color: var(--text-primary);
        font-family: var(--font-sans);
        font-size: 0.75rem;
        outline: none;
        transition: var(--transition-smooth);
    }

    input[type="text"]:focus, select:focus {
        border-color: var(--border-color-hover);
    }

    /* Switch toggle styles */
    .switch {
        position: relative;
        display: inline-block;
        width: 32px;
        height: 18px;
    }

    .switch input {
        opacity: 0;
        width: 0;
        height: 0;
    }

    .slider {
        position: absolute;
        cursor: pointer;
        top: 0;
        left: 0;
        right: 0;
        bottom: 0;
        background-color: rgba(255, 255, 255, 0.1);
        transition: .2s;
        border-radius: 9px;
        border: 1px solid var(--border-glass);
    }

    .slider:before {
        position: absolute;
        content: "";
        height: 12px;
        width: 12px;
        left: 2px;
        bottom: 2px;
        background-color: white;
        transition: .2s;
        border-radius: 50%;
    }

    input:checked + .slider {
        background-color: var(--accent-indigo);
    }

    input:checked + .slider:before {
        transform: translateX(14px);
    }

    /* Cameras stack */
    .cameras-stack {
        flex: 1;
        overflow-y: auto;
        padding-bottom: 1rem;
    }

    .cameras-grid {
        display: grid;
        grid-template-columns: repeat(auto-fill, minmax(260px, 1fr));
        gap: 1.25rem;
    }

    .camera-card {
        background: var(--bg-panel);
        border: 1px solid var(--border-glass);
        border-radius: 12px;
        display: flex;
        flex-direction: column;
        transition: var(--transition-smooth);
    }

    .camera-card.disabled {
        opacity: 0.65;
    }

    .card-header {
        display: flex;
        justify-content: space-between;
        align-items: center;
        padding: 0.75rem 1rem;
        border-bottom: 1px solid var(--border-glass);
    }

    .status-dot {
        width: 6px;
        height: 6px;
        border-radius: 50%;
        background-color: var(--text-muted);
    }

    .status-dot.enabled {
        background-color: var(--accent-emerald);
        box-shadow: 0 0 6px var(--accent-emerald);
    }

    .camera-name {
        margin: 0;
        font-family: var(--font-display);
        font-size: 0.85rem;
        font-weight: 600;
        color: var(--text-primary);
    }

    .btn-delete {
        background: none;
        border: none;
        cursor: pointer;
        padding: 0.15rem;
        font-size: 0.85rem;
        opacity: 0.4;
        transition: opacity var(--transition-smooth);
    }

    .btn-delete:hover {
        opacity: 1;
    }

    .card-body {
        padding: 1rem;
        display: flex;
        flex-direction: column;
        gap: 0.75rem;
    }

    /* Thumbnail placeholder */
    .feed-thumbnail-placeholder {
        background: #020306;
        border: 1px solid var(--border-glass);
        border-radius: 8px;
        height: 120px;
        position: relative;
        display: flex;
        align-items: center;
        justify-content: center;
        overflow: hidden;
    }

    .scanlines {
        position: absolute;
        top: 0;
        left: 0;
        width: 100%;
        height: 100%;
        background: linear-gradient(rgba(18, 16, 16, 0) 50%, rgba(0, 0, 0, 0.25) 50%), linear-gradient(90deg, rgba(255, 0, 0, 0.06), rgba(0, 255, 0, 0.02), rgba(0, 0, 255, 0.06));
        background-size: 100% 4px, 6px 100%;
        pointer-events: none;
    }

    .hud-center {
        display: flex;
        flex-direction: column;
        align-items: center;
        gap: 0.35rem;
        color: var(--text-muted);
    }

    .hud-center .lbl {
        font-size: 0.6rem;
        font-weight: 700;
        letter-spacing: 0.05em;
    }

    .simple-metadata {
        display: flex;
        flex-direction: column;
        gap: 0.35rem;
        border-bottom: 1px solid var(--border-glass);
        padding-bottom: 0.75rem;
    }

    .meta-row {
        display: flex;
        justify-content: space-between;
        font-size: 0.7rem;
    }

    .meta-lbl {
        color: var(--text-muted);
    }

    .meta-val {
        color: var(--text-secondary);
        font-weight: 600;
    }

    .config-btn {
        width: 100%;
        padding: 0.45rem;
        font-size: 0.75rem;
        justify-content: center;
    }

    /* Advanced Config drawer */
    .advanced-config-drawer {
        background: rgba(0, 0, 0, 0.15);
        border: 1px solid var(--border-glass);
        border-radius: 8px;
        padding: 0.75rem;
        margin-top: 0.25rem;
    }

    .advanced-config-drawer .inputs-grid {
        display: grid;
        grid-template-columns: 1fr 1fr;
        gap: 0.75rem;
    }

    .slider-lbl {
        display: flex;
        justify-content: space-between;
        align-items: center;
    }

    .val-badge {
        font-size: 0.6rem;
        font-family: var(--font-mono);
        color: var(--accent-indigo);
        background: rgba(99, 102, 241, 0.08);
        padding: 0.05rem 0.35rem;
        border-radius: 4px;
    }

    .styled-range {
        width: 100%;
        accent-color: var(--accent-indigo);
        cursor: pointer;
    }

    .checkbox-container {
        display: flex;
        align-items: center;
        gap: 0.5rem;
        cursor: pointer;
        font-size: 0.7rem;
        color: var(--text-secondary);
        font-weight: 500;
    }

    .mask-btn {
        width: 100%;
        justify-content: center;
        padding: 0.4rem;
        font-size: 0.7rem;
        border-color: rgba(99, 102, 241, 0.2);
        color: #a5b4fc;
    }

    .mask-btn:hover {
        background: rgba(99, 102, 241, 0.05);
    }

    /* Mask drawer modal */
    .mask-modal-overlay {
        position: fixed;
        top: 0;
        left: 0;
        right: 0;
        bottom: 0;
        background: rgba(3, 5, 10, 0.85);
        backdrop-filter: blur(10px);
        display: flex;
        align-items: center;
        justify-content: center;
        z-index: 500;
    }

    .mask-modal-content {
        width: 90%;
        max-width: 600px;
        padding: 1.25rem;
        border-radius: 16px;
        box-shadow: 0 24px 48px rgba(0,0,0,0.6);
        border: 1px solid var(--border-glass-hover);
    }

    .modal-header {
        display: flex;
        justify-content: space-between;
        align-items: flex-start;
        border-bottom: 1px solid var(--border-glass);
        padding-bottom: 0.5rem;
    }

    .modal-header h2 {
        font-family: var(--font-display);
        font-size: 1rem;
        font-weight: 600;
        margin: 0;
    }

    .btn-close-modal {
        background: none;
        border: none;
        color: var(--text-muted);
        font-size: 1.5rem;
        cursor: pointer;
        line-height: 1;
    }

    .drawing-mode-selector {
        display: flex;
        gap: 0.5rem;
    }

    .mode-btn {
        background: transparent;
        border: 1px solid var(--border-glass);
        color: var(--text-secondary);
        padding: 0.2rem 0.5rem;
        border-radius: 4px;
        font-size: 0.65rem;
        font-weight: 600;
        cursor: pointer;
    }

    .mode-btn.active {
        color: white;
        background: rgba(255,255,255,0.06);
    }

    .mode-btn.mode-mask.active {
        border-color: var(--accent-rose);
        background: rgba(244, 63, 94, 0.05);
    }

    .mode-btn.mode-zone.active {
        border-color: var(--accent-emerald);
        background: rgba(16, 185, 129, 0.05);
    }

    .modal-body {
        padding: 1rem 0;
        display: flex;
        flex-direction: column;
        gap: 1rem;
    }

    .mask-editor-canvas-container {
        position: relative;
        background: #020306;
        border-radius: 8px;
        border: 1px solid var(--border-glass);
        width: 100%;
        aspect-ratio: 16 / 9;
        overflow: hidden;
    }

    .editor-video-feed {
        width: 100%;
        height: 100%;
        object-fit: cover;
    }

    .hud-grid {
        position: absolute;
        top: 0;
        left: 0;
        width: 100%;
        height: 100%;
        background-size: 20px 20px;
        background-image: linear-gradient(to right, rgba(255, 255, 255, 0.02) 1px, transparent 1px), linear-gradient(to bottom, rgba(255, 255, 255, 0.02) 1px, transparent 1px);
    }

    .hud-text {
        position: absolute;
        top: 50%;
        left: 50%;
        transform: translate(-50%, -50%);
        color: var(--text-muted);
        display: flex;
        flex-direction: column;
        gap: 0.25rem;
    }

    .hud-title {
        font-family: var(--font-display);
        font-size: 0.75rem;
        font-weight: 700;
        letter-spacing: 0.05em;
    }

    .hud-desc {
        font-size: 0.65rem;
        max-width: 280px;
        line-height: 1.3;
    }

    .mask-svg-workspace {
        position: absolute;
        top: 0;
        left: 0;
        width: 100%;
        height: 100%;
        cursor: crosshair;
    }

    .closed-mask-poly {
        fill: rgba(244, 63, 94, 0.2);
        stroke: var(--accent-rose);
        stroke-width: 3;
    }

    .closed-zone-poly {
        fill: rgba(16, 185, 129, 0.15);
        stroke: var(--accent-emerald);
        stroke-width: 3;
    }

    .drawing-poly-line {
        fill: none;
        stroke: var(--accent-rose);
        stroke-width: 3;
        stroke-dasharray: 6 4;
    }

    .drawing-poly-line.drawing-zone-line {
        stroke: var(--accent-emerald);
    }

    .rubberband-line {
        stroke: var(--accent-rose);
        stroke-width: 2;
        stroke-dasharray: 4 4;
    }

    .rubberband-line.rubberband-zone-line {
        stroke: var(--accent-emerald);
    }

    .drawing-node {
        fill: white;
        stroke: var(--accent-rose);
        stroke-width: 3;
    }

    .drawing-node.drawing-zone-node {
        stroke: var(--accent-emerald);
    }

    .drawing-node.first-node {
        fill: var(--accent-rose);
        animation: pulse-node 1.5s infinite;
    }

    .drawing-node.drawing-zone-node.first-node {
        fill: var(--accent-emerald);
    }

    @keyframes pulse-node {
        0% { r: 10px; }
        50% { r: 13px; }
        100% { r: 10px; }
    }

    .mask-chip {
        display: inline-flex;
        align-items: center;
        gap: 0.35rem;
        background: rgba(255,255,255,0.02);
        border: 1px solid var(--border-glass);
        padding: 0.15rem 0.5rem;
        border-radius: 4px;
    }

    .chip-color {
        width: 8px;
        height: 8px;
        border-radius: 50%;
    }

    .chip-color.mask-color { background-color: var(--accent-rose); }
    .chip-color.zone-color { background-color: var(--accent-emerald); }

    .chip-delete {
        background: none;
        border: none;
        cursor: pointer;
        color: var(--text-muted);
        font-size: 0.85rem;
        line-height: 1;
        padding: 0;
    }

    .chip-delete:hover {
        color: white;
    }

    .modal-footer {
        display: flex;
        justify-content: space-between;
        border-top: 1px solid var(--border-glass);
        padding-top: 0.75rem;
        margin-top: 0.5rem;
    }

    .empty-masks-state {
        color: var(--text-muted);
    }

    .empty-state {
        border: 1px dashed var(--border-glass);
        border-radius: 12px;
        padding: 4rem 2rem;
    }

    .empty-state h3 {
        font-family: var(--font-display);
        font-size: 1rem;
        margin: 0.5rem 0 0.25rem;
    }

    .empty-state p {
        font-size: 0.75rem;
        color: var(--text-muted);
        margin: 0;
    }
</style>
