<script lang="ts">
    import { onMount, onDestroy } from 'svelte';
    import { getApiToken, buildWsUrl } from '$lib/apiToken';

    // WebSocket state
    let ws: WebSocket | null = $state(null);
    let wsStatus = $state('disconnected');
    let wsReconnectTimer: ReturnType<typeof setTimeout> | null = null;

    // Performance Stats state
    let latencyStats = $state<Record<string, any>>({
        file_read: { avg: 0.0, p50: 0.0, p95: 0.0 },
        inference: { avg: 0.0, p50: 0.0, p95: 0.0 },
        tracking: { avg: 0.0, p50: 0.0, p95: 0.0 },
        face_recog: { avg: 0.0, p50: 0.0, p95: 0.0 },
        fusion: { avg: 0.0, p50: 0.0, p95: 0.0 },
        emit: { avg: 0.0, p50: 0.0, p95: 0.0 },
        total: { avg: 0.0, p50: 0.0, p95: 0.0 }
    });

    let totalFrames = $state(0);
    let errorCount = $state(0);
    let activeCamerasCount = $state(0);
    let processLogs = $state<string[]>([]);

    // Hardware load stats state
    let hardwareStats = $state({
        cpu: 0,
        gpu: 0,
        memory: { used: 0.0, total: 0.0, percent: 0 },
        storage: { used: 0.0, total: 0.0, percent: 0 }
    });

    onMount(() => {
        connectWS();
        fetchActiveCamerasCount();
    });

    onDestroy(() => {
        if (wsReconnectTimer) clearTimeout(wsReconnectTimer);
        if (ws) {
            ws.onclose = null;
            ws.close();
        }
    });

    async function fetchActiveCamerasCount() {
        try {
            const res = await fetch('/api/v1/cameras');
            if (res.ok) {
                const cams = await res.json();
                activeCamerasCount = cams.filter((c: any) => c.enabled).length;
            }
        } catch (_) {}
    }

    function connectWS() {
        wsStatus = 'connecting';
        void (async () => {
            const token = await getApiToken();
            const wsUrl = buildWsUrl('/api/ws', token);
            try {
                ws = new WebSocket(wsUrl);
            } catch (err) {
                console.error('WebSocket construction failed', err);
                wsStatus = 'disconnected';
                if (wsReconnectTimer) clearTimeout(wsReconnectTimer);
                wsReconnectTimer = setTimeout(connectWS, 4000);
                return;
            }

            ws.onopen = () => {
                wsStatus = 'connected';
                ws?.send(JSON.stringify({ action: 'list_skills' }));
            };

            ws.onclose = () => {
                wsStatus = 'disconnected';
                if (wsReconnectTimer) clearTimeout(wsReconnectTimer);
                wsReconnectTimer = setTimeout(connectWS, 4000); // Auto-reconnect
            };

            ws.onmessage = (event) => {
            try {
                const data = JSON.parse(event.data);
                
                if (data.event === 'perf_stats') {
                    if (data.timings_ms) {
                        latencyStats = data.timings_ms;
                    }
                    totalFrames = data.total_frames || totalFrames;
                    errorCount = data.errors || errorCount;

                    // Dynamically map incoming true system telemetry
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
                } else if (data.event === 'log') {
                    const logLine = `[Camera: ${data.cameraId || 'system'}] ${data.message}`;
                    processLogs = [logLine, ...processLogs].slice(0, 100);
                } else if (data.event === 'detections') {
                    totalFrames++;
                }
            } catch (_) {}
        };
        })();
    }

    // Computed totals derived values
    let avgInference = $derived(latencyStats.inference?.avg || 0);
    let avgTotal = $derived(latencyStats.total?.avg || 0);
    let avgTracking = $derived(latencyStats.tracking?.avg || 0);
    let avgFaceRecog = $derived(latencyStats.face_recog?.avg || 0);
    let avgFileRead = $derived(latencyStats.file_read?.avg || 0);
    let avgFusion = $derived(latencyStats.fusion?.avg || 0);
</script>

<svelte:head>
    <title>Hawkeye Diagnostics</title>
</svelte:head>

<div class="flex flex-col gap-6 w-full">
    <!-- Header -->
    <header class="flex justify-between items-center bg-card border border-border rounded-xl p-5 shadow-sm">
        <div>
            <h1 class="text-xl font-bold tracking-tight">System Diagnostics</h1>
            <p class="text-sm text-muted-foreground mt-0.5">Real-time GPU/CPU resource load, NVR storage metrics, and YOLO perception pipeline profiling.</p>
        </div>
        
        <div class="flex items-center gap-1.5 px-3 py-1 rounded-full text-xs font-semibold border shrink-0 transition-all duration-200 {wsStatus === 'connected' ? 'bg-emerald-500/10 border-emerald-500/20 text-emerald-400' : 'bg-muted border-border text-muted-foreground'}">
            <span class="w-1.5 h-1.5 rounded-full {wsStatus === 'connected' ? 'bg-emerald-400 animate-pulse' : 'bg-muted-foreground'}"></span>
            <span>{wsStatus === 'connected' ? 'Telemetry Live' : 'Connecting Telemetry...'}</span>
        </div>
    </header>

    <!-- Top Row Hardware Metrics -->
    <section class="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4 w-full">
        <!-- CPU Card -->
        <div class="bg-card border border-border rounded-xl p-5 shadow-sm flex flex-col gap-3 hover:border-accent-foreground/10 transition-colors">
            <div>
                <span class="text-[10px] font-bold text-muted-foreground uppercase tracking-wider">CPU Usage</span>
                <h3 class="text-2xl font-black mt-1">{hardwareStats.cpu}%</h3>
                <span class="text-[10px] font-medium text-emerald-400 mt-1 block">Cheap Motion-Gating Active</span>
            </div>
            <div class="h-1.5 w-full bg-muted rounded-full overflow-hidden">
                <div class="h-full bg-emerald-500 rounded-full transition-all duration-300" style="width: {hardwareStats.cpu}%"></div>
            </div>
        </div>

        <!-- GPU Card -->
        <div class="bg-card border border-border rounded-xl p-5 shadow-sm flex flex-col gap-3 hover:border-accent-foreground/10 transition-colors">
            <div>
                <span class="text-[10px] font-bold text-muted-foreground uppercase tracking-wider">GPU Acceleration</span>
                <h3 class="text-2xl font-black mt-1">{hardwareStats.gpu}%</h3>
                <span class="text-[10px] font-medium text-indigo-400 mt-1 block">DirectML/CUDA Core Load</span>
            </div>
            <div class="h-1.5 w-full bg-muted rounded-full overflow-hidden">
                <div class="h-full bg-indigo-500 rounded-full transition-all duration-300" style="width: {hardwareStats.gpu}%"></div>
            </div>
        </div>

        <!-- System Memory Card -->
        <div class="bg-card border border-border rounded-xl p-5 shadow-sm flex flex-col gap-3 hover:border-accent-foreground/10 transition-colors">
            <div>
                <span class="text-[10px] font-bold text-muted-foreground uppercase tracking-wider">System Memory</span>
                <h3 class="text-2xl font-black mt-1">{hardwareStats.memory.used} GB <span class="text-xs text-muted-foreground font-normal">/ {hardwareStats.memory.total} GB</span></h3>
                <span class="text-[10px] font-medium text-blue-400 mt-1 block">Low Heap Footprint</span>
            </div>
            <div class="h-1.5 w-full bg-muted rounded-full overflow-hidden">
                <div class="h-full bg-blue-500 rounded-full transition-all duration-300" style="width: {hardwareStats.memory.percent}%"></div>
            </div>
        </div>

        <!-- Surveillance Storage Card -->
        <div class="bg-card border border-border rounded-xl p-5 shadow-sm flex flex-col gap-3 hover:border-accent-foreground/10 transition-colors">
            <div>
                <span class="text-[10px] font-bold text-muted-foreground uppercase tracking-wider">Surveillance Storage</span>
                <h3 class="text-2xl font-black mt-1">{hardwareStats.storage.used} GB <span class="text-xs text-muted-foreground font-normal">/ {hardwareStats.storage.total} GB</span></h3>
                <span class="text-[10px] font-medium text-amber-400 mt-1 block">WAL Mode SQLite Persisted</span>
            </div>
            <div class="h-1.5 w-full bg-muted rounded-full overflow-hidden">
                <div class="h-full bg-amber-500 rounded-full transition-all duration-300" style="width: {hardwareStats.storage.percent}%"></div>
            </div>
        </div>
    </section>

    <!-- Latency Profiler and Pipeline Diagnostics -->
    <div class="grid grid-cols-1 lg:grid-cols-12 gap-6 w-full">
        <!-- Latency stage meters (Profiler) -->
        <section class="lg:col-span-7 bg-card border border-border rounded-xl p-5 shadow-sm flex flex-col gap-4">
            <div class="flex justify-between items-center border-b border-border pb-3">
                <h3 class="text-sm font-bold tracking-tight text-foreground uppercase">YOLO Pipeline Stage Latency</h3>
                <span class="text-[10px] font-bold uppercase tracking-wider px-2.5 py-0.5 rounded-full border bg-emerald-500/10 border-emerald-500/25 text-emerald-400 shrink-0">
                    {avgTotal ? Math.round(1000 / avgTotal) : 0} Processed FPS
                </span>
            </div>
            
            <div class="flex flex-col gap-5 mt-2">
                <!-- Total Latency Card -->
                <div class="flex items-center justify-between border border-border bg-muted/5 rounded-lg p-3">
                    <div>
                        <span class="text-[9px] font-bold uppercase text-muted-foreground">Total Pipeline Loop</span>
                        <h4 class="text-lg font-black mt-0.5">{avgTotal.toFixed(1)} ms <span class="text-xs font-normal text-muted-foreground">/ frame</span></h4>
                    </div>
                    <div class="flex gap-3 text-[10px] text-muted-foreground shrink-0">
                        <span>Min: {latencyStats.total?.min || 0}ms</span>
                        <span>p95: {latencyStats.total?.p95 || 0}ms</span>
                    </div>
                </div>

                <!-- Individual stages -->
                <div class="flex flex-col gap-4">
                    <!-- Stage 1 -->
                    <div class="flex flex-col gap-1.5">
                        <div class="flex justify-between text-xs font-medium">
                            <span class="text-muted-foreground">1. Frame Ingestion & Decryption</span>
                            <span class="text-foreground">{avgFileRead.toFixed(1)} ms</span>
                        </div>
                        <div class="h-1.5 w-full bg-muted rounded-full overflow-hidden">
                            <div class="h-full bg-blue-500 rounded-full" style="width: {Math.min(100, (avgFileRead / (avgTotal || 1)) * 100)}%"></div>
                        </div>
                    </div>

                    <!-- Stage 2 -->
                    <div class="flex flex-col gap-1.5">
                        <div class="flex justify-between text-xs font-medium">
                            <span class="text-muted-foreground">2. YOLO Inference Pass</span>
                            <span class="text-foreground">{avgInference.toFixed(1)} ms</span>
                        </div>
                        <div class="h-1.5 w-full bg-muted rounded-full overflow-hidden">
                            <div class="h-full bg-cyan-500 rounded-full" style="width: {Math.min(100, (avgInference / (avgTotal || 1)) * 100)}%"></div>
                        </div>
                    </div>

                    <!-- Stage 3 -->
                    <div class="flex flex-col gap-1.5">
                        <div class="flex justify-between text-xs font-medium">
                            <span class="text-muted-foreground">3. Multi-Object ByteTrack</span>
                            <span class="text-foreground">{avgTracking.toFixed(1)} ms</span>
                        </div>
                        <div class="h-1.5 w-full bg-muted rounded-full overflow-hidden">
                            <div class="h-full bg-emerald-500 rounded-full" style="width: {Math.min(100, (avgTracking / (avgTotal || 1)) * 100)}%"></div>
                        </div>
                    </div>

                    <!-- Stage 4 -->
                    <div class="flex flex-col gap-1.5">
                        <div class="flex justify-between text-xs font-medium">
                            <span class="text-muted-foreground">4. Facial Re-ID & Identity Matching</span>
                            <span class="text-foreground">{avgFaceRecog.toFixed(1)} ms</span>
                        </div>
                        <div class="h-1.5 w-full bg-muted rounded-full overflow-hidden">
                            <div class="h-full bg-violet-500 rounded-full" style="width: {Math.min(100, (avgFaceRecog / (avgTotal || 1)) * 100)}%"></div>
                        </div>
                    </div>

                    <!-- Stage 5 -->
                    <div class="flex flex-col gap-1.5">
                        <div class="flex justify-between text-xs font-medium">
                            <span class="text-muted-foreground">5. IPC Event Emission</span>
                            <span class="text-foreground">{avgFusion.toFixed(1)} ms</span>
                        </div>
                        <div class="h-1.5 w-full bg-muted rounded-full overflow-hidden">
                            <div class="h-full bg-amber-500 rounded-full" style="width: {Math.min(100, (avgFusion / (avgTotal || 1)) * 100)}%"></div>
                        </div>
                    </div>
                </div>
            </div>
        </section>

        <!-- Process log listener -->
        <section class="lg:col-span-5 bg-card border border-border rounded-xl p-5 shadow-sm flex flex-col gap-4">
            <div class="flex justify-between items-center border-b border-border pb-3">
                <h3 class="text-sm font-bold tracking-tight text-foreground uppercase">Active Backend Pipeline Console</h3>
            </div>
            <div class="flex items-center justify-between text-[10px] text-muted-foreground bg-muted/20 border border-border px-3 py-2 rounded-lg -mt-1 font-bold tracking-tight">
                <span>Active Processes: <strong class="text-foreground">{activeCamerasCount}</strong></span>
                <span>Total Frames: <strong class="text-foreground">{totalFrames}</strong></span>
            </div>
            
            <div class="flex-1 bg-black border border-border rounded-lg p-3 font-mono text-[10px] text-muted-foreground min-h-[320px] max-h-[380px] overflow-y-auto flex flex-col gap-1">
                {#if processLogs.length === 0}
                    <div class="flex-1 flex items-center justify-center text-center text-muted-foreground/30 p-8 select-none font-sans text-xs">
                        &gt; Listening for live perception engine stdout stream events...
                    </div>
                {:else}
                    {#each processLogs as log}
                        <div class="leading-normal break-all select-text selection:bg-muted selection:text-foreground">
                            <span class="text-emerald-500/70 font-semibold">&gt; {new Date().toLocaleTimeString()}</span>
                            <span class="text-zinc-300 ml-1">{log}</span>
                        </div>
                    {/each}
                {/if}
            </div>
        </section>
    </div>
</div>
