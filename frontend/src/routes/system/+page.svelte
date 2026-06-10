<script lang="ts">
    import { onMount, onDestroy } from 'svelte';
    import { getApiToken, buildWsUrl } from '$lib/apiToken';
    import { fly } from 'svelte/transition';

    let ws: WebSocket | null = $state(null);
    let wsStatus = $state('disconnected');
    let wsReconnectTimer: ReturnType<typeof setTimeout> | null = null;

    let latencyStats = $state<Record<string, any>>({
        file_read: { avg: 0.0, p50: 0.0, p95: 0.0, min: 0.0 },
        inference: { avg: 0.0, p50: 0.0, p95: 0.0, min: 0.0 },
        tracking: { avg: 0.0, p50: 0.0, p95: 0.0, min: 0.0 },
        face_recog: { avg: 0.0, p50: 0.0, p95: 0.0, min: 0.0 },
        fusion: { avg: 0.0, p50: 0.0, p95: 0.0, min: 0.0 },
        emit: { avg: 0.0, p50: 0.0, p95: 0.0, min: 0.0 },
        total: { avg: 0.0, p50: 0.0, p95: 0.0, min: 0.0 }
    });

    let totalFrames = $state(0);
    let errorCount = $state(0);
    let activeCamerasCount = $state(0);
    let processLogs = $state<string[]>([]);

    let hardwareStats = $state({
        cpu: 0,
        gpu: 0,
        memory: { used: 0.0, total: 0.0, percent: 0 },
        storage: { used: 0.0, total: 0.0, percent: 0 }
    });

    // Sparkline history (last 30 samples)
    let cpuHistory = $state<number[]>(Array(30).fill(0));
    let gpuHistory = $state<number[]>(Array(30).fill(0));
    let memHistory = $state<number[]>(Array(30).fill(0));
    let fpsHistory = $state<number[]>(Array(30).fill(0));

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
                wsReconnectTimer = setTimeout(connectWS, 4000);
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

                        if (data.cpu !== undefined) {
                            hardwareStats.cpu = data.cpu;
                            cpuHistory = [...cpuHistory.slice(1), data.cpu];
                        }
                        if (data.gpu !== undefined) {
                            hardwareStats.gpu = data.gpu;
                            gpuHistory = [...gpuHistory.slice(1), data.gpu];
                        }
                        if (data.memory) {
                            hardwareStats.memory = {
                                used: parseFloat((data.memory.used / 1024 / 1024 / 1024).toFixed(1)),
                                total: parseFloat((data.memory.total / 1024 / 1024 / 1024).toFixed(1)),
                                percent: data.memory.percent
                            };
                            memHistory = [...memHistory.slice(1), data.memory.percent];
                        }
                        if (data.storage) {
                            hardwareStats.storage = {
                                used: parseFloat((data.storage.used / 1024 / 1024 / 1024).toFixed(1)),
                                total: parseFloat((data.storage.total / 1024 / 1024 / 1024).toFixed(1)),
                                percent: data.storage.percent
                            };
                        }
                        if (latencyStats.total?.avg) {
                            const fps = Math.min(120, Math.round(1000 / latencyStats.total.avg));
                            fpsHistory = [...fpsHistory.slice(1), fps];
                        }
                    } else if (data.event === 'log') {
                        const ts = new Date().toLocaleTimeString([], { hour12: false });
                        const logLine = `[${ts}] [${data.cameraId || 'system'}] ${data.message}`;
                        processLogs = [logLine, ...processLogs].slice(0, 100);
                    } else if (data.event === 'detections') {
                        totalFrames++;
                    }
                } catch (_) {}
            };
        })();
    }

    let avgInference = $derived(latencyStats.inference?.avg || 0);
    let avgTotal = $derived(latencyStats.total?.avg || 0);
    let avgTracking = $derived(latencyStats.tracking?.avg || 0);
    let avgFaceRecog = $derived(latencyStats.face_recog?.avg || 0);
    let avgFileRead = $derived(latencyStats.file_read?.avg || 0);
    let avgFusion = $derived(latencyStats.fusion?.avg || 0);
    let processedFps = $derived(avgTotal ? Math.round(1000 / avgTotal) : 0);

    let pipelineStages = $derived([
        { id: 'file_read', label: 'Frame Ingestion', sublabel: 'Decrypt + Decode', avg: avgFileRead, tone: 'cyan' as const, total: avgTotal },
        { id: 'inference', label: 'YOLO Inference', sublabel: 'Object detection', avg: avgInference, tone: 'iris' as const, total: avgTotal },
        { id: 'tracking', label: 'ByteTrack MOT', sublabel: 'Multi-object tracking', avg: avgTracking, tone: 'jade' as const, total: avgTotal },
        { id: 'face_recog', label: 'Facial Re-ID', sublabel: 'Identity matching', avg: avgFaceRecog, tone: 'ember' as const, total: avgTotal },
        { id: 'fusion', label: 'Event Emission', sublabel: 'IPC broadcast', avg: avgFusion, tone: 'gold' as const, total: avgTotal }
    ]);

    function buildSparklinePath(values: number[], max: number = 100, width: number = 80, height: number = 24): string {
        if (values.length === 0) return '';
        const step = width / (values.length - 1 || 1);
        const points = values.map((v, i) => {
            const x = i * step;
            const y = height - (Math.min(max, v) / max) * height;
            return `${x},${y}`;
        });
        return `M ${points.join(' L ')}`;
    }

    function buildSparklineFill(values: number[], max: number = 100, width: number = 80, height: number = 24): string {
        if (values.length === 0) return '';
        const step = width / (values.length - 1 || 1);
        const points = values.map((v, i) => {
            const x = i * step;
            const y = height - (Math.min(max, v) / max) * height;
            return `${x},${y}`;
        });
        return `M 0,${height} L ${points.join(' L ')} L ${width},${height} Z`;
    }

    function getLoadTone(pct: number): 'jade' | 'gold' | 'crimson' {
        if (pct < 60) return 'jade';
        if (pct < 85) return 'gold';
        return 'crimson';
    }

    const cpuTone = $derived(getLoadTone(hardwareStats.cpu));
    const gpuTone = $derived(getLoadTone(hardwareStats.gpu));
    const memTone = $derived(getLoadTone(hardwareStats.memory.percent));
    const storageTone = $derived(getLoadTone(hardwareStats.storage.percent));
</script>

<svelte:head>
    <title>Hawkeye — System Diagnostics</title>
</svelte:head>

<div class="flex flex-col gap-6 w-full pb-12 page-enter">

    <!-- Header -->
    <header class="flex flex-col lg:flex-row lg:items-end justify-between gap-4">
        <div>
            <div class="flex items-center gap-2.5 mb-2">
                <span class="badge {wsStatus === 'connected' ? 'badge-jade' : 'badge-muted'}">
                    <span class="w-1.5 h-1.5 rounded-full {wsStatus === 'connected' ? 'bg-jade status-pulse' : 'bg-muted-foreground'}"></span>
                    {wsStatus === 'connected' ? 'Telemetry Live' : 'Connecting Telemetry'}
                </span>
                <span class="text-[11px] text-muted-foreground font-mono">{totalFrames.toLocaleString()} frames processed</span>
            </div>
            <h1 class="text-2xl md:text-3xl font-display font-bold text-foreground tracking-tight leading-none">System Diagnostics</h1>
            <p class="text-sm text-muted-foreground mt-2">Real-time hardware load, NVR storage metrics, and YOLO perception pipeline profiling.</p>
        </div>

        <div class="flex items-center gap-2 flex-wrap">
            <div class="flex items-center gap-1.5 px-3 h-9 rounded-lg border border-jade/20 bg-jade/5">
                <span class="w-1.5 h-1.5 rounded-full bg-jade status-pulse"></span>
                <span class="text-[10px] font-mono font-bold text-foreground tabular-nums">{processedFps}</span>
                <span class="text-[10px] text-muted-foreground uppercase tracking-wider">fps</span>
            </div>
            <div class="flex items-center gap-1.5 px-3 h-9 rounded-lg border border-border bg-card">
                <span class="text-[10px] font-mono font-bold text-foreground tabular-nums">{activeCamerasCount}</span>
                <span class="text-[10px] text-muted-foreground uppercase tracking-wider">active</span>
            </div>
            <div class="flex items-center gap-1.5 px-3 h-9 rounded-lg border border-crimson/20 bg-crimson/5">
                <span class="w-1.5 h-1.5 rounded-full bg-crimson {errorCount > 0 ? 'status-pulse' : ''}"></span>
                <span class="text-[10px] font-mono font-bold text-foreground tabular-nums">{errorCount}</span>
                <span class="text-[10px] text-muted-foreground uppercase tracking-wider">errors</span>
            </div>
        </div>
    </header>

    <!-- Hardware metrics row -->
    <section class="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
        <!-- CPU -->
        <div class="panel p-5 page-enter stagger-1">
            <div class="flex items-start justify-between gap-3 mb-4 min-w-0">
                <div class="min-w-0 flex-1">
                    <span class="section-eyebrow flex items-center gap-1.5">
                        <span class="w-1 h-1 rounded-full bg-jade"></span>
                        CPU
                    </span>
                    <h3 class="text-3xl font-display font-bold text-foreground tabular-nums mt-1.5 leading-none">
                        {hardwareStats.cpu}<span class="text-lg text-muted-foreground font-normal">%</span>
                    </h3>
                </div>
                <svg width="80" height="24" class="shrink-0 mt-1">
                    <path d={buildSparklineFill(cpuHistory, 100, 80, 24)} fill="hsl(187 75% 58% / 0.1)" />
                    <path d={buildSparklinePath(cpuHistory, 100, 80, 24)} fill="none" stroke="hsl(187 75% 58%)" stroke-width="1.5" stroke-linejoin="round" />
                </svg>
            </div>
            <div class="flex flex-col gap-2">
                <div class="h-1.5 w-full bg-border rounded-full overflow-hidden">
                    <div class="h-full transition-all duration-300 rounded-full"
                        class:bg-jade={cpuTone === 'jade'}
                        class:bg-gold={cpuTone === 'gold'}
                        class:bg-crimson={cpuTone === 'crimson'}
                        style="width: {hardwareStats.cpu}%"></div>
                </div>
                <div class="flex items-center justify-between text-[10px] font-mono">
                    <span class="text-muted-foreground uppercase tracking-wider">Motion Gating Active</span>
                    <span class="text-foreground font-semibold tabular-nums">{cpuTone === 'crimson' ? 'CRITICAL' : cpuTone === 'gold' ? 'WARN' : 'OK'}</span>
                </div>
            </div>
        </div>

        <!-- GPU -->
        <div class="panel p-5 page-enter stagger-2">
            <div class="flex items-start justify-between gap-3 mb-4 min-w-0">
                <div class="min-w-0 flex-1">
                    <span class="section-eyebrow flex items-center gap-1.5">
                        <span class="w-1 h-1 rounded-full bg-iris"></span>
                        GPU
                    </span>
                    <h3 class="text-3xl font-display font-bold text-foreground tabular-nums mt-1.5 leading-none">
                        {hardwareStats.gpu}<span class="text-lg text-muted-foreground font-normal">%</span>
                    </h3>
                </div>
                <svg width="80" height="24" class="shrink-0 mt-1">
                    <path d={buildSparklineFill(gpuHistory, 100, 80, 24)} fill="hsl(252 78% 72% / 0.1)" />
                    <path d={buildSparklinePath(gpuHistory, 100, 80, 24)} fill="none" stroke="hsl(252 78% 72%)" stroke-width="1.5" stroke-linejoin="round" />
                </svg>
            </div>
            <div class="flex flex-col gap-2">
                <div class="h-1.5 w-full bg-border rounded-full overflow-hidden">
                    <div class="h-full transition-all duration-300 rounded-full"
                        class:bg-jade={gpuTone === 'jade'}
                        class:bg-gold={gpuTone === 'gold'}
                        class:bg-crimson={gpuTone === 'crimson'}
                        style="width: {hardwareStats.gpu}%"></div>
                </div>
                <div class="flex items-center justify-between text-[10px] font-mono">
                    <span class="text-muted-foreground uppercase tracking-wider">CUDA / DirectML</span>
                    <span class="text-foreground font-semibold tabular-nums">{gpuTone === 'crimson' ? 'CRITICAL' : gpuTone === 'gold' ? 'WARN' : 'OK'}</span>
                </div>
            </div>
        </div>

        <!-- Memory -->
        <div class="panel p-5 page-enter stagger-3">
            <div class="flex items-start justify-between gap-3 mb-4 min-w-0">
                <div class="min-w-0 flex-1">
                    <span class="section-eyebrow flex items-center gap-1.5">
                        <span class="w-1 h-1 rounded-full bg-cyan"></span>
                        Memory
                    </span>
                    <h3 class="text-3xl font-display font-bold text-foreground tabular-nums mt-1.5 leading-none">
                        {hardwareStats.memory.used}<span class="text-base text-muted-foreground font-normal">/{hardwareStats.memory.total}<span class="text-sm">GB</span></span>
                    </h3>
                </div>
                <svg width="80" height="24" class="shrink-0 mt-1">
                    <path d={buildSparklineFill(memHistory, 100, 80, 24)} fill="hsl(187 75% 58% / 0.1)" />
                    <path d={buildSparklinePath(memHistory, 100, 80, 24)} fill="none" stroke="hsl(187 75% 58%)" stroke-width="1.5" stroke-linejoin="round" />
                </svg>
            </div>
            <div class="flex flex-col gap-2">
                <div class="h-1.5 w-full bg-border rounded-full overflow-hidden">
                    <div class="h-full transition-all duration-300 rounded-full"
                        class:bg-jade={memTone === 'jade'}
                        class:bg-gold={memTone === 'gold'}
                        class:bg-crimson={memTone === 'crimson'}
                        style="width: {hardwareStats.memory.percent}%"></div>
                </div>
                <div class="flex items-center justify-between text-[10px] font-mono">
                    <span class="text-muted-foreground uppercase tracking-wider">Heap Footprint</span>
                    <span class="text-foreground font-semibold tabular-nums">{hardwareStats.memory.percent.toFixed(1)}%</span>
                </div>
            </div>
        </div>

        <!-- Storage -->
        <div class="panel p-5 page-enter stagger-4">
            <div class="flex items-start justify-between gap-3 mb-4 min-w-0">
                <div class="min-w-0 flex-1">
                    <span class="section-eyebrow flex items-center gap-1.5">
                        <span class="w-1 h-1 rounded-full bg-gold"></span>
                        Storage
                    </span>
                    <h3 class="text-3xl font-display font-bold text-foreground tabular-nums mt-1.5 leading-none">
                        {hardwareStats.storage.used}<span class="text-base text-muted-foreground font-normal">/{hardwareStats.storage.total}<span class="text-sm">GB</span></span>
                    </h3>
                </div>
                <div class="shrink-0 mt-1 flex items-center gap-1.5">
                    <span class="badge !h-5 !text-[9px] badge-gold font-mono">WAL</span>
                </div>
            </div>
            <div class="flex flex-col gap-2">
                <div class="h-1.5 w-full bg-border rounded-full overflow-hidden">
                    <div class="h-full transition-all duration-300 rounded-full"
                        class:bg-jade={storageTone === 'jade'}
                        class:bg-gold={storageTone === 'gold'}
                        class:bg-crimson={storageTone === 'crimson'}
                        style="width: {hardwareStats.storage.percent}%"></div>
                </div>
                <div class="flex items-center justify-between text-[10px] font-mono">
                    <span class="text-muted-foreground uppercase tracking-wider">SQLite NVR</span>
                    <span class="text-foreground font-semibold tabular-nums">{hardwareStats.storage.percent.toFixed(1)}%</span>
                </div>
            </div>
        </div>
    </section>

    <!-- Pipeline + Console -->
    <div class="grid grid-cols-1 lg:grid-cols-12 gap-5">

        <!-- Latency profiler -->
        <section class="lg:col-span-7 panel page-enter stagger-3">
            <div class="panel-header">
                <div class="flex items-center gap-2.5">
                    <div class="w-8 h-8 rounded-lg bg-iris/10 border border-iris/20 flex items-center justify-center text-iris">
                        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                            <path d="M3 3v18h18"/>
                            <path d="M7 12l4-4 4 4 5-5"/>
                        </svg>
                    </div>
                    <div>
                        <h2 class="text-sm font-display font-semibold text-foreground">YOLO Pipeline Latency</h2>
                        <p class="text-[10px] text-muted-foreground font-mono">Per-stage profiling · milliseconds per frame</p>
                    </div>
                </div>
                <div class="flex items-center gap-2 shrink-0">
                    <svg width="72" height="22" class="shrink-0" viewBox="0 0 72 22" preserveAspectRatio="none">
                        <path d={buildSparklinePath(fpsHistory, 120, 72, 22)} fill="none" stroke="hsl(152 68% 52%)" stroke-width="1.5" stroke-linejoin="round" />
                    </svg>
                    <span class="badge badge-jade font-mono tabular-nums">
                        {processedFps} FPS
                    </span>
                </div>
            </div>

            <div class="panel-body flex flex-col gap-5">
                <!-- Total loop summary -->
                <div class="flex items-center justify-between gap-4 p-3.5 rounded-lg border border-border bg-surface-2/40">
                    <div class="min-w-0">
                        <span class="section-eyebrow block">Total Pipeline Loop</span>
                        <h3 class="text-2xl font-display font-bold text-foreground tabular-nums mt-0.5 leading-none">
                            {avgTotal.toFixed(1)}<span class="text-base font-normal text-muted-foreground"> ms / frame</span>
                        </h3>
                    </div>
                    <div class="flex gap-4 text-[10px] text-muted-foreground font-mono shrink-0">
                        <div class="flex flex-col items-end gap-0.5">
                            <span class="uppercase tracking-wider text-[9px]">p50</span>
                            <span class="text-sm font-bold text-foreground tabular-nums leading-none">{(latencyStats.total?.p50 || 0).toFixed(1)}ms</span>
                        </div>
                        <div class="w-px h-7 bg-border"></div>
                        <div class="flex flex-col items-end gap-0.5">
                            <span class="uppercase tracking-wider text-[9px]">p95</span>
                            <span class="text-sm font-bold text-foreground tabular-nums leading-none">{(latencyStats.total?.p95 || 0).toFixed(1)}ms</span>
                        </div>
                        <div class="w-px h-7 bg-border"></div>
                        <div class="flex flex-col items-end gap-0.5">
                            <span class="uppercase tracking-wider text-[9px]">min</span>
                            <span class="text-sm font-bold text-foreground tabular-nums leading-none">{(latencyStats.total?.min || 0).toFixed(1)}ms</span>
                        </div>
                    </div>
                </div>

                <!-- Pipeline waterfall -->
                <div class="flex flex-col gap-3.5">
                    {#each pipelineStages as stage, i (stage.id)}
                        {@const pct = stage.total > 0 ? (stage.avg / stage.total) * 100 : 0}
                        <div class="flex flex-col gap-1.5" in:fly={{ y: 6, duration: 200, delay: i * 50 }}>
                            <div class="flex items-center justify-between gap-3 text-xs">
                                <div class="flex items-center gap-2.5 min-w-0 flex-1">
                                    <span class="shrink-0 w-5 h-5 rounded-md bg-surface-2 border border-border text-[9px] font-mono font-bold text-muted-foreground flex items-center justify-center">{i + 1}</span>
                                    <div class="min-w-0 truncate">
                                        <span class="font-semibold text-foreground">{stage.label}</span>
                                        <span class="text-[10px] text-muted-foreground font-mono ml-2 hidden sm:inline">{stage.sublabel}</span>
                                    </div>
                                </div>
                                <div class="flex items-baseline gap-2.5 text-[10px] font-mono shrink-0">
                                    <span class="text-foreground font-bold tabular-nums leading-none">{stage.avg.toFixed(1)}<span class="text-muted-foreground font-normal ml-0.5">ms</span></span>
                                    <span class="text-muted-foreground uppercase tracking-wider tabular-nums leading-none w-10 text-right">{pct.toFixed(0)}%</span>
                                </div>
                            </div>
                            <div class="relative h-1.5 w-full bg-border rounded-full overflow-hidden">
                                <div
                                    class="absolute inset-y-0 left-0 rounded-full transition-all duration-300
                                    {stage.tone === 'cyan' ? 'bg-cyan' : ''}
                                    {stage.tone === 'iris' ? 'bg-iris' : ''}
                                    {stage.tone === 'jade' ? 'bg-jade' : ''}
                                    {stage.tone === 'ember' ? 'bg-ember' : ''}
                                    {stage.tone === 'gold' ? 'bg-gold' : ''}"
                                    style="width: {pct}%"
                                ></div>
                            </div>
                        </div>
                    {/each}
                </div>
            </div>
        </section>

        <!-- Console -->
        <section class="lg:col-span-5 panel page-enter stagger-4 !p-0 overflow-hidden flex flex-col">
            <div class="panel-header">
                <div class="flex items-center gap-2.5">
                    <div class="w-8 h-8 rounded-lg bg-cyan/10 border border-cyan/20 flex items-center justify-center text-cyan">
                        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                            <polyline points="4 17 10 11 4 5"/>
                            <line x1="12" y1="19" x2="20" y2="19"/>
                        </svg>
                    </div>
                    <div>
                        <h2 class="text-sm font-display font-semibold text-foreground">Pipeline Console</h2>
                        <p class="text-[10px] text-muted-foreground font-mono">Live perception engine output</p>
                    </div>
                </div>
                <div class="flex items-center gap-1.5">
                    <span class="w-1.5 h-1.5 rounded-full bg-jade status-pulse"></span>
                    <span class="text-[10px] font-mono text-muted-foreground uppercase tracking-wider">stdout</span>
                </div>
            </div>

            <div class="flex-1 bg-black/60 border-y border-border min-h-[380px] max-h-[480px] overflow-y-auto p-4 font-mono text-[10px]">
                {#if processLogs.length === 0}
                    <div class="flex-1 flex flex-col items-center justify-center text-center text-muted-foreground/40 p-8 min-h-[340px] select-none">
                        <div class="w-10 h-10 rounded-lg bg-surface-2/40 border border-border flex items-center justify-center mb-3">
                            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">
                                <polyline points="4 17 10 11 4 5"/>
                                <line x1="12" y1="19" x2="20" y2="19"/>
                            </svg>
                        </div>
                        <span class="text-[10px] font-mono">&gt; Listening for live perception engine stdout...</span>
                    </div>
                {:else}
                    <div class="flex flex-col gap-1">
                        {#each processLogs as log (log)}
                            <div class="flex items-start gap-2 leading-relaxed break-all text-zinc-300">
                                <span class="text-jade/60 font-bold shrink-0">&gt;</span>
                                <span class="text-zinc-300 flex-1 min-w-0">{log}</span>
                            </div>
                        {/each}
                    </div>
                {/if}
            </div>

            <div class="px-5 py-3 flex items-center justify-between gap-3 text-[10px] font-mono text-muted-foreground border-t border-border bg-surface-2/30">
                <span class="flex items-center gap-1.5 min-w-0">
                    <span class="w-1 h-1 rounded-full bg-cyan shrink-0"></span>
                    <span class="truncate">Active processes: <strong class="text-foreground tabular-nums">{activeCamerasCount}</strong></span>
                </span>
                <span class="shrink-0">Total frames: <strong class="text-foreground tabular-nums">{totalFrames.toLocaleString()}</strong></span>
            </div>
        </section>
    </div>
</div>
