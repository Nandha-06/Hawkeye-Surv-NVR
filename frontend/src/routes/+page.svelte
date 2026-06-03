<script lang="ts">
    import { onMount } from 'svelte';

    interface CameraConfig {
        id: string;
        name: string;
        source: 'rtsp' | 'webcam';
        enabled: boolean;
        fps?: number;
    }

    interface RecorderStatus {
        cameraId: string;
        cameraName: string;
        state: 'recording' | 'stopped' | 'error' | 'unsupported';
        indexedSegments: number;
        lastSegmentAt?: string;
        message?: string;
    }

    interface CameraEvent {
        id: string;
        camera_id: string;
        label: string;
        confidence: number;
        timestamp: string;
        severity: 'info' | 'warning' | 'critical';
    }

    let cameras = $state<CameraConfig[]>([]);
    let recorders = $state<RecorderStatus[]>([]);
    let events = $state<CameraEvent[]>([]);
    let loading = $state(true);

    const enabledCameras = $derived(cameras.filter(camera => camera.enabled));
    const recordingCount = $derived(recorders.filter(recorder => recorder.state === 'recording').length);
    const errorCount = $derived(recorders.filter(recorder => recorder.state === 'error').length);
    const recentCritical = $derived(events.filter(event => event.severity === 'critical').length);

    onMount(async () => {
        await refresh();
    });

    async function refresh() {
        loading = true;
        try {
            const [cameraRes, recorderRes, eventRes] = await Promise.all([
                fetch('/api/v1/cameras'),
                fetch('/api/v1/recorders'),
                fetch('/api/v1/events?camera_id=all&label=all&limit=20')
            ]);
            cameras = cameraRes.ok ? await cameraRes.json() : [];
            recorders = recorderRes.ok ? await recorderRes.json() : [];
            events = eventRes.ok ? await eventRes.json() : [];
        } finally {
            loading = false;
        }
    }

    async function recorderAction(action: 'start_all' | 'stop_all') {
        const res = await fetch('/api/v1/recorders', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ action })
        });
        if (res.ok) await refresh();
    }

    function formatTime(iso?: string) {
        if (!iso) return 'Never';
        return new Date(iso).toLocaleString();
    }
</script>

<svelte:head>
    <title>Hawkeye Overview</title>
</svelte:head>

<div class="flex flex-col gap-6 w-full">
    <!-- Header row -->
    <header class="flex flex-col md:flex-row justify-between items-start md:items-center gap-4 bg-card border border-border rounded-xl p-5 shadow-sm">
        <div>
            <h1 class="text-xl font-bold tracking-tight">Overview</h1>
            <p class="text-sm text-muted-foreground mt-0.5">Operational status for cameras, recording, and recent detections.</p>
        </div>
        <div class="flex flex-wrap gap-2 justify-end w-full md:w-auto">
            <button 
                onclick={refresh} 
                disabled={loading} 
                class="px-3.5 py-1.5 text-xs font-semibold rounded-lg border border-border bg-background hover:bg-accent hover:text-accent-foreground disabled:opacity-50 transition-colors cursor-pointer"
            >
                {loading ? 'Refreshing...' : 'Refresh'}
            </button>
            <button 
                onclick={() => recorderAction('start_all')} 
                class="px-3.5 py-1.5 text-xs font-semibold rounded-lg bg-emerald-600 hover:bg-emerald-500 text-white font-medium shadow-sm transition-colors cursor-pointer"
            >
                Start recording
            </button>
            <button 
                onclick={() => recorderAction('stop_all')} 
                class="px-3.5 py-1.5 text-xs font-semibold rounded-lg bg-zinc-800 hover:bg-zinc-700 text-zinc-300 border border-zinc-700 shadow-sm transition-colors cursor-pointer"
            >
                Stop recording
            </button>
            <a 
                href="/monitoring" 
                class="px-3.5 py-1.5 text-xs font-semibold rounded-lg bg-primary text-primary-foreground font-medium shadow-md hover:bg-primary/95 transition-colors cursor-pointer flex items-center justify-center"
            >
                Open live view
            </a>
        </div>
    </header>

    <!-- Metrics Cards Grid -->
    <section class="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4 w-full">
        <article class="bg-card border border-border rounded-xl p-5 shadow-sm flex flex-col gap-1 hover:border-accent-foreground/20 transition-colors">
            <span class="text-[10px] font-bold text-muted-foreground uppercase tracking-wider">Enabled Cameras</span>
            <strong class="text-3xl font-extrabold tracking-tight text-foreground">{enabledCameras.length}</strong>
            <p class="text-xs text-muted-foreground">{cameras.length} configured</p>
        </article>
        <article class="bg-card border border-border rounded-xl p-5 shadow-sm flex flex-col gap-1 hover:border-accent-foreground/20 transition-colors">
            <span class="text-[10px] font-bold text-muted-foreground uppercase tracking-wider">Recording</span>
            <strong class="text-3xl font-extrabold tracking-tight text-foreground">{recordingCount}</strong>
            <p class="text-xs text-muted-foreground">{errorCount} recorder errors</p>
        </article>
        <article class="bg-card border border-border rounded-xl p-5 shadow-sm flex flex-col gap-1 hover:border-accent-foreground/20 transition-colors">
            <span class="text-[10px] font-bold text-muted-foreground uppercase tracking-wider">Recent Events</span>
            <strong class="text-3xl font-extrabold tracking-tight text-foreground">{events.length}</strong>
            <p class="text-xs text-muted-foreground">{recentCritical} critical</p>
        </article>
        <article class="bg-card border border-border rounded-xl p-5 shadow-sm flex flex-col gap-1 hover:border-accent-foreground/20 transition-colors">
            <span class="text-[10px] font-bold text-muted-foreground uppercase tracking-wider">Storage Index</span>
            <strong class="text-3xl font-extrabold tracking-tight text-foreground">
                {recorders.reduce((sum, recorder) => sum + recorder.indexedSegments, 0)}
            </strong>
            <p class="text-xs text-muted-foreground">segments this session</p>
        </article>
    </section>

    <!-- Details Panels -->
    <div class="grid grid-cols-1 lg:grid-cols-12 gap-6 w-full">
        <!-- Cameras Status panel -->
        <section class="lg:col-span-7 bg-card border border-border rounded-xl p-5 shadow-sm flex flex-col gap-4 min-h-[380px]">
            <div class="flex justify-between items-center border-b border-border pb-3">
                <h2 class="text-sm font-bold tracking-tight text-foreground uppercase">Cameras</h2>
                <a href="/cameras" class="text-xs font-semibold text-primary hover:underline">Manage</a>
            </div>
            
            {#if cameras.length === 0}
                <div class="flex-1 flex flex-col items-center justify-center text-center p-6 border border-dashed border-border rounded-lg">
                    <p class="text-xs text-muted-foreground">No cameras configured.</p>
                </div>
            {:else}
                <div class="flex flex-col gap-2">
                    {#each cameras as camera}
                        {@const recorder = recorders.find(item => item.cameraId === camera.id)}
                        <div class="flex items-center justify-between border border-border bg-muted/5 rounded-lg p-3 hover:bg-muted/10 transition-colors">
                            <div class="flex items-center gap-3">
                                <span class="w-2.5 h-2.5 rounded-full shrink-0 {camera.enabled ? 'bg-emerald-500 shadow-[0_0_8px_#10b981]' : 'bg-muted-foreground'}"></span>
                                <div>
                                    <strong class="text-xs font-bold text-foreground block">{camera.name}</strong>
                                    <p class="text-[10px] text-muted-foreground mt-0.5">{camera.source} · {camera.fps || 5} FPS target</p>
                                </div>
                            </div>
                            <div class="text-right shrink-0">
                                <span class="text-[10px] font-bold uppercase tracking-wider px-2 py-0.5 rounded border inline-block {recorder?.state === 'recording' ? 'bg-emerald-500/10 border-emerald-500/25 text-emerald-400' : 'bg-muted border-border text-muted-foreground'}">
                                    {recorder?.state || 'stopped'}
                                </span>
                                <p class="text-[9px] text-muted-foreground mt-1">{formatTime(recorder?.lastSegmentAt)}</p>
                            </div>
                        </div>
                    {/each}
                </div>
            {/if}
        </section>

        <!-- Recent Events panel -->
        <section class="lg:col-span-5 bg-card border border-border rounded-xl p-5 shadow-sm flex flex-col gap-4 min-h-[380px]">
            <div class="flex justify-between items-center border-b border-border pb-3">
                <h2 class="text-sm font-bold tracking-tight text-foreground uppercase">Recent Events</h2>
                <a href="/events" class="text-xs font-semibold text-primary hover:underline">Explore</a>
            </div>
            
            {#if events.length === 0}
                <div class="flex-1 flex flex-col items-center justify-center text-center p-6 border border-dashed border-border rounded-lg">
                    <p class="text-xs text-muted-foreground">No recent detections.</p>
                </div>
            {:else}
                <div class="flex flex-col gap-2 overflow-y-auto max-h-[320px] pr-1">
                    {#each events as event}
                        <div class="flex flex-col gap-1 border border-border bg-muted/5 rounded-lg p-3 hover:bg-muted/10 transition-colors">
                            <div class="flex justify-between items-start">
                                <strong class="text-xs font-bold text-foreground">{event.label}</strong>
                                <span class="text-[9px] font-semibold text-muted-foreground shrink-0">{new Date(event.timestamp).toLocaleTimeString()}</span>
                            </div>
                            <p class="text-[10px] text-muted-foreground mt-0.5">
                                Camera: {event.camera_id} · Confidence: {Math.round(event.confidence * 100)}%
                            </p>
                        </div>
                    {/each}
                </div>
            {/if}
        </section>
    </div>
</div>
