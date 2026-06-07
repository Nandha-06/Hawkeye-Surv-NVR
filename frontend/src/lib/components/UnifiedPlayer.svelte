<script lang="ts">
    import { onMount, onDestroy } from 'svelte';
    import Hls from 'hls.js';
    import { Bell, EyeOff, Radio, RotateCcw, Trash2 } from 'lucide-svelte';

    // Props
    let { camera } = $props<{ camera: any }>();

    // Player Mode: 'live' or 'history'
    let playerMode = $state<'live' | 'history'>('live');
    let isVideoPaused = $state(false);
    
    // Time & Playback States
    let hlsUrl = $state('');
    let hlsInstance: Hls | null = null;
    let videoElement = $state<HTMLVideoElement | null>(null);
    let webRtcPc = $state<RTCPeerConnection | null>(null);
    let streamStatus = $state<'idle' | 'connecting' | 'live' | 'history' | 'error'>('idle');
    let streamError = $state('');

    // Timeline calculations
    const TIMELINE_DURATION_MS = 2 * 60 * 60 * 1000; // 2 hours
    let timelineStart = $state(Date.now() - TIMELINE_DURATION_MS);
    let timelineEnd = $state(Date.now());
    let scrubberPosition = $state(100); // 0 to 100 percentage
    
    let segments = $state<any[]>([]);
    let hoverThumbnail = $state<{ id: string; x: number; y: number; timeString: string } | null>(null);
    let timelineRef = $state<HTMLDivElement | null>(null);

    // Drawing Tool States
    let drawingMode = $state<'none' | 'mask' | 'zone'>('none');
    let newPolygonPoints = $state<{ x: number; y: number }[]>([]);
    let drawnMasks = $state<{ x: number; y: number }[][]>([]);
    let drawnZones = $state<{ name: string; polygon: { x: number; y: number }[]; color: string }[]>([]);
    let newZoneName = $state('Driveway');

    onMount(() => {
        let disposed = false;

        // Load initial masks and zones from camera config
        if (camera.masks) drawnMasks = camera.masks;
        if (camera.zones) drawnZones = camera.zones;

        (async () => {
            await loadSegments();
            if (!disposed) startLiveStream();
        })();

        // Refresh timeline/segments periodically
        const interval = setInterval(async () => {
            if (playerMode === 'live') {
                timelineEnd = Date.now();
                timelineStart = timelineEnd - TIMELINE_DURATION_MS;
            }
            await loadSegments();
        }, 5000);

        return () => {
            disposed = true;
            clearInterval(interval);
            stopLiveStream();
            stopHls();
        };
    });

    // --- Ingestion & Streaming (WebRTC using go2rtc) ---
    function startLiveStream() {
        stopHls();
        stopLiveStream();
        
        if (!videoElement) return;

        playerMode = 'live';
        streamStatus = 'connecting';
        streamError = '';
        scrubberPosition = 100;
        
        console.log(`[UnifiedPlayer] Starting WebRTC stream for: ${camera.id}`);

        // Set up RTCPeerConnection
        const pc = new RTCPeerConnection({
            iceServers: [{ urls: 'stun:stun.l.google.com:19302' }]
        });

        pc.ontrack = (event) => {
            if (videoElement) {
                videoElement.srcObject = event.streams[0];
            }
        };

        pc.addTransceiver('video', { direction: 'recvonly' });
        pc.addTransceiver('audio', { direction: 'recvonly' });
        pc.onconnectionstatechange = () => {
            if (pc.connectionState === 'connected') {
                streamStatus = 'live';
            } else if (pc.connectionState === 'failed' || pc.connectionState === 'disconnected') {
                streamStatus = 'error';
                streamError = 'Live stream disconnected';
            }
        };

        webRtcPc = pc;

        (async () => {
            try {
                const offer = await pc.createOffer();
                await pc.setLocalDescription(offer);

                const streamName = camera.detect_url ? `${camera.id}_sub` : camera.id;
                const response = await fetch(`http://127.0.0.1:1984/api/webrtc?src=${encodeURIComponent(streamName)}`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/sdp' },
                    body: pc.localDescription?.sdp || ''
                });

                if (!response.ok) {
                    throw new Error(`go2rtc returned HTTP ${response.status}`);
                }

                const answer = await response.text();
                await pc.setRemoteDescription(new RTCSessionDescription({ type: 'answer', sdp: answer }));
            } catch (err) {
                console.error('[WebRTC] Signal Error:', err);
                streamStatus = 'error';
                streamError = err instanceof Error ? err.message : 'Unable to start live stream';
                pc.close();
                if (webRtcPc === pc) webRtcPc = null;
            }
        })();
    }

    function stopLiveStream() {
        if (webRtcPc) {
            webRtcPc.close();
            webRtcPc = null;
        }
        if (videoElement) {
            videoElement.srcObject = null;
        }
    }

    // --- Playback & Scrubbing (HLS Transition) ---
    function stopHls() {
        if (hlsInstance) {
            hlsInstance.destroy();
            hlsInstance = null;
        }
        if (videoElement) {
            videoElement.src = '';
        }
    }

    async function loadSegments() {
        try {
            const res = await fetch(`/api/v1/recordings?camera_id=${camera.id}`);
            if (res.ok) {
                segments = await res.json();
            }
        } catch (e) {
            console.error('Failed to load segments:', e);
        }
    }

    function playHistoryAtTime(timeMs: number) {
        stopLiveStream();
        stopHls();

        playerMode = 'history';
        streamStatus = 'history';
        streamError = '';
        
        // Calculate percentages
        const pct = ((timeMs - timelineStart) / TIMELINE_DURATION_MS) * 100;
        scrubberPosition = Math.max(0, Math.min(100, pct));

        const startIso = new Date(timeMs).toISOString();
        const endIso = new Date(Date.now()).toISOString();

        // Feed index.m3u8 playlist with start/end timeframe params
        const vodUrl = `/api/v1/recordings/vod/index.m3u8?camera_id=${camera.id}&start_time=${encodeURIComponent(startIso)}&end_time=${encodeURIComponent(endIso)}`;
        
        if (!videoElement) return;

        console.log(`[UnifiedPlayer] Loading historical HLS URL: ${vodUrl}`);

        if (Hls.isSupported()) {
            const hls = new Hls();
            hls.loadSource(vodUrl);
            hls.attachMedia(videoElement);
            hls.on(Hls.Events.MANIFEST_PARSED, () => {
                videoElement?.play().catch(e => console.warn('Autoplay block:', e));
            });
            hls.on(Hls.Events.ERROR, (_event, data) => {
                if (!data.fatal) return;
                streamStatus = 'error';
                streamError = 'Playback stream failed';
                if (data.type === Hls.ErrorTypes.MEDIA_ERROR) {
                    hls.recoverMediaError();
                }
            });
            hlsInstance = hls;
        } else if (videoElement.canPlayType('application/vnd.apple.mpegurl')) {
            videoElement.src = vodUrl;
            videoElement.play().catch(e => console.warn('Autoplay block:', e));
        }
    }

    function handleTimelineClick(e: MouseEvent) {
        if (!timelineRef) return;
        const rect = timelineRef.getBoundingClientRect();
        const offsetX = e.clientX - rect.left;
        const pct = offsetX / rect.width;
        
        if (pct >= 0.99) {
            startLiveStream();
        } else {
            const targetTime = timelineStart + pct * TIMELINE_DURATION_MS;
            playHistoryAtTime(targetTime);
        }
    }

    function handleTimelineMouseMove(e: MouseEvent) {
        if (!timelineRef || segments.length === 0) return;
        const rect = timelineRef.getBoundingClientRect();
        const offsetX = e.clientX - rect.left;
        const pct = offsetX / rect.width;
        
        const hoveredTime = timelineStart + pct * TIMELINE_DURATION_MS;
        
        // Find segment at hoveredTime
        const hoveredSeg = segments.find(seg => {
            const s = new Date(seg.start_time).getTime();
            const e = new Date(seg.end_time).getTime();
            return hoveredTime >= s && hoveredTime <= e;
        });

        if (hoveredSeg) {
            hoverThumbnail = {
                id: hoveredSeg.id,
                x: e.clientX - rect.left,
                y: rect.top - window.scrollY - 100, // Float above timeline
                timeString: new Date(hoveredTime).toLocaleTimeString()
            };
        } else {
            hoverThumbnail = null;
        }
    }

    function handleTimelineMouseLeave() {
        hoverThumbnail = null;
    }

    // --- Polygonal Masks & Zones Drawing Tool ---
    function handleOverlayClick(e: MouseEvent) {
        if (drawingMode === 'none') return;
        
        const rect = (e.currentTarget as HTMLElement).getBoundingClientRect();
        const x = parseFloat(((e.clientX - rect.left) / rect.width).toFixed(3));
        const y = parseFloat(((e.clientY - rect.top) / rect.height).toFixed(3));

        newPolygonPoints = [...newPolygonPoints, { x, y }];
    }

    function removeLastPoint() {
        if (newPolygonPoints.length > 0) {
            newPolygonPoints = newPolygonPoints.slice(0, -1);
        }
    }

    async function saveDrawnPolygon() {
        if (newPolygonPoints.length < 3) {
            alert('A polygon needs at least 3 points.');
            return;
        }

        if (drawingMode === 'mask') {
            drawnMasks = [...drawnMasks, newPolygonPoints];
        } else if (drawingMode === 'zone') {
            const randomColor = '#' + Math.floor(Math.random()*16777215).toString(16);
            drawnZones = [...drawnZones, {
                name: newZoneName,
                polygon: newPolygonPoints,
                color: randomColor
            }];
        }

        // Persist to cameras.json via API
        try {
            // First fetch all cameras
            const getRes = await fetch('/api/v1/cameras');
            if (getRes.ok) {
                const camerasList = await getRes.json();
                const camIdx = camerasList.findIndex((c: any) => c.id === camera.id);
                if (camIdx !== -1) {
                    camerasList[camIdx].masks = drawnMasks;
                    camerasList[camIdx].zones = drawnZones;
                    
                    // Save back
                    await fetch('/api/v1/cameras', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify(camerasList)
                    });
                    console.log('[Drawing] Successfully saved masks and zones.');
                }
            }
        } catch (err) {
            console.error('Failed to save polygons to cameras config:', err);
        }

        // Reset
        newPolygonPoints = [];
        drawingMode = 'none';
    }

    function cancelDrawing() {
        newPolygonPoints = [];
        drawingMode = 'none';
    }

    function clearAllPolygons() {
        if (confirm('Clear all masks and zones for this camera?')) {
            drawnMasks = [];
            drawnZones = [];
            newPolygonPoints = [];
            drawingMode = 'none';
            // Save empty configurations
            saveDrawnPolygon();
        }
    }
</script>

<div class="flex flex-col gap-4 w-full bg-zinc-950 border border-zinc-800 rounded-2xl p-4 shadow-xl overflow-hidden font-sans text-zinc-100">
    <!-- Header Stream Title & Mode Badge -->
    <div class="flex justify-between items-center border-b border-zinc-800 pb-3">
        <div class="flex items-center gap-3">
            <span class="w-3 h-3 rounded-full {streamStatus === 'error' ? 'bg-rose-500 shadow-[0_0_10px_#f43f5e]' : playerMode === 'live' ? 'bg-emerald-500 shadow-[0_0_10px_#10b981] animate-pulse' : 'bg-amber-500 shadow-[0_0_10px_#f59e0b]'}"></span>
            <div>
                <h3 class="text-sm font-bold tracking-tight text-white">{camera.name}</h3>
                <p class="text-[10px] text-zinc-400 mt-0.5">Stream Source: {camera.source.toUpperCase()}</p>
            </div>
        </div>

        <div class="flex items-center gap-2">
            <span class="badge {streamStatus === 'error' ? 'bg-rose-500/10 border-rose-500/20 text-rose-400' : playerMode === 'live' ? 'bg-emerald-500/10 border-emerald-500/20 text-emerald-400' : 'bg-amber-500/10 border-amber-500/20 text-amber-400'} font-mono uppercase tracking-wider py-1 px-2.5 rounded-lg border text-[10px] font-bold">
                {streamStatus === 'connecting' ? 'connecting' : streamStatus === 'error' ? 'error' : playerMode}
            </span>
            {#if playerMode === 'history'}
                <button onclick={startLiveStream} class="px-2.5 py-1 text-[10px] font-bold bg-emerald-600 hover:bg-emerald-500 text-white rounded-lg transition-colors cursor-pointer shadow-sm">
                    Go Live
                </button>
            {/if}
        </div>
    </div>

    <!-- Video Playback & Canvas Overlay Container -->
    <div class="relative w-full aspect-video bg-black rounded-xl overflow-hidden border border-zinc-800 shadow-inner group">
        <!-- svelte-ignore a11y_media_has_caption -->
        <video 
            bind:this={videoElement}
            autoplay 
            playsinline 
            muted 
            class="w-full h-full object-contain"
            bind:paused={isVideoPaused}
        ></video>

        {#if streamStatus === 'connecting'}
            <div class="absolute inset-0 flex items-center justify-center bg-black/35 text-xs font-bold text-zinc-300">
                Connecting live stream...
            </div>
        {:else if streamStatus === 'error'}
            <div class="absolute inset-0 flex flex-col items-center justify-center gap-2 bg-black/55 text-center px-4">
                <Radio size={22} class="text-rose-400" />
                <p class="text-xs font-semibold text-zinc-200">{streamError || 'Stream unavailable'}</p>
                <button onclick={startLiveStream} class="inline-flex items-center gap-1.5 px-3 py-1.5 text-[11px] font-bold bg-zinc-800 hover:bg-zinc-700 text-white rounded-lg transition-colors cursor-pointer">
                    <RotateCcw size={13} />
                    Retry
                </button>
            </div>
        {/if}

        <!-- SVG overlay for drawing and display polygons (zones/masks) -->
        <!-- svelte-ignore a11y_no_noninteractive_element_interactions -->
        <svg
            class="absolute inset-0 w-full h-full {drawingMode !== 'none' ? 'cursor-crosshair bg-black/35' : 'pointer-events-none'}"
            role="application"
            aria-label="Drawing canvas for surveillance zones and masks"
            tabindex="-1"
            onclick={handleOverlayClick}
            onkeydown={(e) => { if (e.key === 'Enter' || e.key === ' ') handleOverlayClick(e); }}
        >
            <!-- Display Saved Masks -->
            {#each drawnMasks as mask}
                <polygon 
                    points={mask.map(p => `${p.x * 100}%,${p.y * 100}%`).join(' ')} 
                    class="fill-red-500/20 stroke-red-500 stroke-2 stroke-dasharray-[4,2]" 
                />
            {/each}

            <!-- Display Saved Zones -->
            {#each drawnZones as zone}
                <polygon 
                    points={zone.polygon.map(p => `${p.x * 100}%,${p.y * 100}%`).join(' ')} 
                    style="fill: {zone.color}22; stroke: {zone.color}aa; stroke-width: 2;"
                />
                <!-- Label for zone -->
                {#if zone.polygon.length > 0}
                    <text 
                        x="{zone.polygon[0].x * 100}%" 
                        y="{zone.polygon[0].y * 100 - 2}%" 
                        fill="white"
                        font-size="10"
                        font-weight="bold"
                        class="paint-order-stroke stroke-zinc-950 stroke-2 font-mono"
                    >
                        {zone.name}
                    </text>
                {/if}
            {/each}

            <!-- Active Draw Shape -->
            {#if drawingMode !== 'none' && newPolygonPoints.length > 0}
                {#each newPolygonPoints as pt, idx}
                    <circle cx="{pt.x * 100}%" cy="{pt.y * 100}%" r="4" fill="#6366f1" stroke="white" stroke-width="1.5" />
                    {#if idx > 0}
                        <line 
                            x1="{newPolygonPoints[idx-1].x * 100}%" 
                            y1="{newPolygonPoints[idx-1].y * 100}%" 
                            x2="{pt.x * 100}%" 
                            y2="{pt.y * 100}%" 
                            stroke="#6366f1" 
                            stroke-width="2" 
                        />
                    {/if}
                {/each}
                {#if newPolygonPoints.length >= 3}
                    <!-- Closing line hint -->
                    <line 
                        x1="{newPolygonPoints[newPolygonPoints.length - 1].x * 100}%" 
                        y1="{newPolygonPoints[newPolygonPoints.length - 1].y * 100}%" 
                        x2="{newPolygonPoints[0].x * 100}%" 
                        y2="{newPolygonPoints[0].y * 100}%" 
                        stroke="#6366f1" 
                        stroke-width="1.5" 
                        stroke-dasharray="4,2" 
                    />
                {/if}
            {/if}
        </svg>

        <!-- Floating UI controls for drawing polygon -->
        {#if drawingMode !== 'none'}
            <div class="absolute bottom-4 left-4 right-4 bg-zinc-900/90 border border-zinc-700/60 backdrop-blur-md p-3.5 rounded-xl flex flex-wrap items-center justify-between gap-3 shadow-lg z-30 animate-fade-in">
                <div class="flex items-center gap-3">
                    <span class="text-xs font-bold text-indigo-400 font-mono">DRAWING {drawingMode.toUpperCase()}:</span>
                    {#if drawingMode === 'zone'}
                        <input 
                            type="text" 
                            bind:value={newZoneName} 
                            placeholder="Zone Name" 
                            class="bg-zinc-800 border border-zinc-700 px-2 py-1 text-xs rounded text-white font-mono focus:border-indigo-500 focus:outline-none w-28"
                        />
                    {/if}
                    <span class="text-[10px] text-zinc-400">({newPolygonPoints.length} points placed)</span>
                </div>
                <div class="flex items-center gap-2">
                    <button onclick={removeLastPoint} disabled={newPolygonPoints.length === 0} class="px-2.5 py-1 text-[10px] bg-zinc-800 hover:bg-zinc-700 disabled:opacity-50 text-white rounded cursor-pointer font-bold">
                        Undo
                    </button>
                    <button onclick={saveDrawnPolygon} disabled={newPolygonPoints.length < 3} class="px-3.5 py-1 text-[10px] bg-indigo-600 hover:bg-indigo-500 text-white rounded cursor-pointer font-bold disabled:opacity-50 shadow-md">
                        Save Area
                    </button>
                    <button onclick={cancelDrawing} class="px-2.5 py-1 text-[10px] bg-zinc-700 hover:bg-zinc-600 text-zinc-200 rounded cursor-pointer font-bold">
                        Cancel
                    </button>
                </div>
            </div>
        {/if}
    </div>

    <!-- Polygonal Masks Drawing Controls -->
    <div class="flex flex-wrap items-center gap-2 border-b border-zinc-900 pb-3">
        <span class="text-xs text-zinc-400 font-bold font-mono">Zones & Masks Tool:</span>
        <button 
            onclick={() => drawingMode = 'mask'} 
            disabled={drawingMode !== 'none'} 
            class="inline-flex items-center gap-1.5 px-3 py-1.5 text-[11px] font-bold rounded-lg border border-red-500/20 bg-red-950/20 hover:bg-red-900/30 text-red-400 disabled:opacity-50 cursor-pointer transition-colors"
        >
            <EyeOff size={13} />
            Draw Motion Mask
        </button>
        <button 
            onclick={() => drawingMode = 'zone'} 
            disabled={drawingMode !== 'none'} 
            class="inline-flex items-center gap-1.5 px-3 py-1.5 text-[11px] font-bold rounded-lg border border-indigo-500/20 bg-indigo-950/20 hover:bg-indigo-900/30 text-indigo-400 disabled:opacity-50 cursor-pointer transition-colors"
        >
            <Bell size={13} />
            Draw Detect Zone
        </button>
        <button 
            onclick={clearAllPolygons} 
            disabled={drawingMode !== 'none' || (drawnMasks.length === 0 && drawnZones.length === 0)}
            class="inline-flex items-center gap-1.5 px-3 py-1.5 text-[11px] font-bold rounded-lg border border-zinc-700 bg-zinc-800 hover:bg-zinc-700 text-zinc-300 disabled:opacity-50 cursor-pointer transition-colors"
        >
            <Trash2 size={13} />
            Clear Areas
        </button>
    </div>

    <!-- Color-Coded Timeline & Scrubber Bar -->
    <div class="flex flex-col gap-2 relative">
        <div class="flex justify-between items-center text-[10px] font-mono text-zinc-400">
            <span>{new Date(timelineStart).toLocaleTimeString()}</span>
            <span class="text-indigo-400 font-bold select-none">Unified Scrubber</span>
            <span>{new Date(timelineEnd).toLocaleTimeString()}</span>
        </div>

        <!-- Timeline track container -->
        <!-- svelte-ignore a11y_click_events_have_key_events a11y_no_static_element_interactions -->
        <div
            bind:this={timelineRef}
            class="relative w-full h-8 bg-zinc-900 border border-zinc-800 rounded-xl cursor-pointer overflow-hidden shadow-inner group-timeline"
            role="slider"
            aria-label="Recording timeline scrubber"
            aria-valuemin="0"
            aria-valuemax="100"
            aria-valuenow={scrubberPosition}
            tabindex="0"
            onclick={handleTimelineClick}
            onmousemove={handleTimelineMouseMove}
            onmouseleave={handleTimelineMouseLeave}
        >
            <!-- Timeline recording segments (Color-coded) -->
            {#each segments as seg}
                {@const s = new Date(seg.start_time).getTime()}
                {@const e = new Date(seg.end_time).getTime()}
                {@const left = Math.max(0, ((s - timelineStart) / TIMELINE_DURATION_MS) * 100)}
                {@const right = Math.min(100, ((e - timelineStart) / TIMELINE_DURATION_MS) * 100)}
                {@const width = Math.max(0.5, right - left)}
                {#if left < 100 && right > 0}
                    <div 
                        class="absolute top-0 bottom-0 border-r border-zinc-950/20 {seg.type === 'vlm' ? 'bg-red-600' : seg.type === 'motion' ? 'bg-yellow-500' : 'bg-zinc-500'}"
                        style="left: {left}%; width: {width}%"
                    ></div>
                {/if}
            {/each}

            <!-- Scrubber line handler -->
            <div 
                class="absolute top-0 bottom-0 w-0.5 bg-indigo-500 shadow-[0_0_8px_#6366f1] z-20 pointer-events-none"
                style="left: {scrubberPosition}%"
            ></div>
        </div>

        <!-- Legend -->
        <div class="flex gap-4 items-center text-[10px] font-mono text-zinc-400 mt-1 select-none">
            <div class="flex items-center gap-1.5">
                <span class="w-2.5 h-2.5 rounded bg-zinc-500 inline-block"></span>
                <span>Continuous (Gray)</span>
            </div>
            <div class="flex items-center gap-1.5">
                <span class="w-2.5 h-2.5 rounded bg-yellow-500 inline-block"></span>
                <span>Motion (Yellow)</span>
            </div>
            <div class="flex items-center gap-1.5">
                <span class="w-2.5 h-2.5 rounded bg-red-600 inline-block"></span>
                <span>VLM Event (Red)</span>
            </div>
        </div>

        <!-- Floating hover thumbnail preview -->
        {#if hoverThumbnail}
            <div 
                class="absolute bg-zinc-900 border border-zinc-700/60 p-1.5 rounded-lg shadow-2xl flex flex-col gap-1 w-36 pointer-events-none z-50 transition-all backdrop-blur-md animate-fade-in"
                style="left: {hoverThumbnail.x - 72}px; top: {hoverThumbnail.y}px;"
            >
                <img 
                    src="/api/v1/recordings/vod/thumbnail/{hoverThumbnail.id}" 
                    alt="Preview" 
                    class="w-full aspect-video object-cover rounded-md bg-black"
                />
                <span class="text-[9px] font-mono font-bold text-center text-zinc-300 bg-black/40 rounded py-0.5">{hoverThumbnail.timeString}</span>
            </div>
        {/if}
    </div>
</div>

<style>
    .badge {
        font-weight: 700;
        box-shadow: inset 0 1px 0 rgba(255,255,255,0.05);
    }
    
    /* Paint order ensures SVG text borders are drawn behind the text fill */
    .paint-order-stroke {
        paint-order: stroke fill;
    }
</style>
