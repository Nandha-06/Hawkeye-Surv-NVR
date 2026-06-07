<script lang="ts">
    import { onMount, onDestroy } from 'svelte';
    import { getApiToken, buildWsUrl } from '$lib/apiToken';
    import { fly } from 'svelte/transition';

    interface CameraConfig {
        id: string;
        name: string;
        source: 'rtsp' | 'webcam' | 'file';
        url?: string;
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
    let now = $state(new Date());
    let tickInterval: ReturnType<typeof setInterval> | null = null;

    const enabledCameras = $derived(cameras.filter(c => c.enabled));
    const recordingCount = $derived(recorders.filter(r => r.state === 'recording').length);
    const errorCount = $derived(recorders.filter(r => r.state === 'error').length);
    const criticalCount = $derived(events.filter(e => e.severity === 'critical').length);
    const warningCount = $derived(events.filter(e => e.severity === 'warning').length);
    const infoCount = $derived(events.filter(e => e.severity === 'info').length);
    const totalSegments = $derived(recorders.reduce((sum, r) => sum + r.indexedSegments, 0));

    onMount(async () => {
        await refresh();
        tickInterval = setInterval(() => { now = new Date(); }, 1000);
    });

    onDestroy(() => {
        if (tickInterval) clearInterval(tickInterval);
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

    function relativeTime(iso: string) {
        const diff = Date.now() - new Date(iso).getTime();
        const min = Math.floor(diff / 60000);
        if (min < 1) return 'just now';
        if (min < 60) return `${min}m ago`;
        const hr = Math.floor(min / 60);
        if (hr < 24) return `${hr}h ago`;
        return new Date(iso).toLocaleDateString();
    }

    const timeStr = $derived(now.toLocaleTimeString('en-US', { hour12: false }));
    const dateStr = $derived(now.toLocaleDateString('en-US', { weekday: 'long', month: 'long', day: 'numeric', year: 'numeric' }));
</script>

<svelte:head>
    <title>Hawkeye · Overview</title>
</svelte:head>

<div class="max-w-[1400px] mx-auto p-6 md:p-8 flex flex-col gap-6">
    <!-- Page header -->
    <header class="flex flex-col md:flex-row md:items-end md:justify-between gap-4 page-enter">
        <div class="flex flex-col gap-1.5">
            <div class="flex items-center gap-2.5">
                <span class="badge badge-cyan">
                    <span class="w-1.5 h-1.5 rounded-full bg-cyan status-pulse"></span>
                    Live
                </span>
                <span class="text-[11px] text-muted-foreground font-mono">SESSION_2026.06.07</span>
            </div>
            <h1 class="text-3xl md:text-4xl font-display font-bold text-foreground tracking-tight leading-none">
                Mission Control
            </h1>
            <p class="text-sm text-muted-foreground max-w-xl">
                Real-time operational status across cameras, recording pipelines, and AI detection events.
            </p>
        </div>

        <div class="flex flex-col items-end gap-1">
            <div class="font-mono text-3xl font-bold text-foreground tabular-nums tracking-tight">{timeStr}</div>
            <div class="text-[11px] text-muted-foreground uppercase tracking-wider font-semibold">{dateStr}</div>
        </div>
    </header>

    <!-- Action bar -->
    <div class="flex flex-wrap items-center justify-between gap-3 page-enter stagger-1">
        <div class="flex items-center gap-2">
            <button onclick={refresh} disabled={loading} class="btn">
                <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" class:animate-spin={loading}>
                    <path d="M21 12a9 9 0 1 1-9-9c2.52 0 4.93 1 6.74 2.74L21 8"/>
                    <path d="M21 3v5h-5"/>
                </svg>
                {loading ? 'Refreshing…' : 'Refresh'}
            </button>
            {#if recordingCount > 0}
                <button onclick={() => recorderAction('stop_all')} class="btn btn-crimson">
                    <span class="w-1.5 h-1.5 rounded-full bg-white animate-pulse"></span>
                    Stop recording
                </button>
            {:else}
                <button onclick={() => recorderAction('start_all')} class="btn btn-jade">
                    <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5">
                        <circle cx="12" cy="12" r="10"/>
                        <polygon points="10 8 16 12 10 16 10 8" fill="currentColor"/>
                    </svg>
                    Start recording
                </button>
            {/if}
        </div>
        <a href="/monitoring" class="btn btn-primary">
            Open Live Grid
            <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5">
                <line x1="5" y1="12" x2="19" y2="12"/><polyline points="12 5 19 12 12 19"/>
            </svg>
        </a>
    </div>

    <!-- Primary metrics -->
    <section class="grid grid-cols-2 lg:grid-cols-4 gap-4 page-enter stagger-2">
        <!-- Cameras -->
        <a href="/cameras" class="panel p-5 group hover:border-cyan/40 transition-all">
            <div class="flex items-start justify-between mb-3">
                <span class="section-eyebrow">Cameras</span>
                <div class="w-7 h-7 rounded-md bg-cyan/10 flex items-center justify-center text-cyan">
                    <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                        <path d="M23 19a2 2 0 0 1-2 2H3a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h4l2-3h6l2 3h4a2 2 0 0 1 2 2z"/><circle cx="12" cy="13" r="4"/>
                    </svg>
                </div>
            </div>
            <div class="flex items-baseline gap-1.5">
                <span class="text-4xl font-display font-bold text-foreground tabular-nums tracking-tight">{enabledCameras.length}</span>
                <span class="text-sm text-muted-foreground">/ {cameras.length}</span>
            </div>
            <p class="text-[11px] text-muted-foreground mt-1">
                <span class="text-cyan font-semibold">{enabledCameras.length} active</span> · {cameras.length - enabledCameras.length} offline
            </p>
            <div class="mt-3 h-1 rounded-full bg-muted overflow-hidden">
                <div class="h-full bg-cyan transition-all duration-500" style="width: {cameras.length ? (enabledCameras.length / cameras.length) * 100 : 0}%"></div>
            </div>
        </a>

        <!-- Recording -->
        <a href="/review" class="panel p-5 group hover:border-crimson/40 transition-all">
            <div class="flex items-start justify-between mb-3">
                <span class="section-eyebrow">Recording</span>
                <div class="w-7 h-7 rounded-md flex items-center justify-center"
                    class:bg-crimson={recordingCount > 0}
                    class:bg-opacity-10={recordingCount > 0}
                    class:text-crimson={recordingCount > 0}
                    class:bg-muted={recordingCount === 0}
                    class:text-muted-foreground={recordingCount === 0}>
                    <span class="w-2 h-2 rounded-full"
                        class:bg-crimson={recordingCount > 0}
                        class:status-pulse={recordingCount > 0}
                        class:bg-muted-foreground={recordingCount === 0}></span>
                </div>
            </div>
            <div class="flex items-baseline gap-1.5">
                <span class="text-4xl font-display font-bold text-foreground tabular-nums tracking-tight">{recordingCount}</span>
                <span class="text-sm text-muted-foreground">streams</span>
            </div>
            <p class="text-[11px] text-muted-foreground mt-1">
                {#if errorCount > 0}
                    <span class="text-crimson font-semibold">{errorCount} recorder error{errorCount > 1 ? 's' : ''}</span>
                {:else if recordingCount > 0}
                    <span class="text-jade font-semibold">All channels capturing</span>
                {:else}
                    <span>No active captures</span>
                {/if}
            </p>
            <div class="mt-3 h-1 rounded-full bg-muted overflow-hidden">
                <div class="h-full bg-crimson transition-all duration-500" style="width: {enabledCameras.length ? (recordingCount / enabledCameras.length) * 100 : 0}%"></div>
            </div>
        </a>

        <!-- Events -->
        <a href="/events" class="panel p-5 group hover:border-ember/40 transition-all">
            <div class="flex items-start justify-between mb-3">
                <span class="section-eyebrow">Events</span>
                <div class="w-7 h-7 rounded-md bg-ember/10 flex items-center justify-center text-ember">
                    <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                        <path d="M6 8a6 6 0 0 1 12 0c0 7 3 9 3 9H3s3-2 3-9"/><path d="M10.3 21a1.94 1.94 0 0 0 3.4 0"/>
                    </svg>
                </div>
            </div>
            <div class="flex items-baseline gap-1.5">
                <span class="text-4xl font-display font-bold text-foreground tabular-nums tracking-tight">{events.length}</span>
                <span class="text-sm text-muted-foreground">recent</span>
            </div>
            <div class="flex items-center gap-3 mt-1 text-[10px] font-mono font-semibold">
                {#if criticalCount > 0}
                    <span class="flex items-center gap-1 text-crimson">
                        <span class="w-1 h-1 rounded-full bg-crimson"></span>
                        {criticalCount} critical
                    </span>
                {/if}
                {#if warningCount > 0}
                    <span class="flex items-center gap-1 text-gold">
                        <span class="w-1 h-1 rounded-full bg-gold"></span>
                        {warningCount} warn
                    </span>
                {/if}
                {#if infoCount > 0}
                    <span class="flex items-center gap-1 text-cyan">
                        <span class="w-1 h-1 rounded-full bg-cyan"></span>
                        {infoCount} info
                    </span>
                {/if}
            </div>
            <div class="mt-3 h-1 rounded-full bg-muted overflow-hidden flex">
                <div class="h-full bg-crimson" style="width: {events.length ? (criticalCount / events.length) * 100 : 0}%"></div>
                <div class="h-full bg-gold" style="width: {events.length ? (warningCount / events.length) * 100 : 0}%"></div>
                <div class="h-full bg-cyan" style="width: {events.length ? (infoCount / events.length) * 100 : 0}%"></div>
            </div>
        </a>

        <!-- Storage -->
        <a href="/system" class="panel p-5 group hover:border-iris/40 transition-all">
            <div class="flex items-start justify-between mb-3">
                <span class="section-eyebrow">Index</span>
                <div class="w-7 h-7 rounded-md bg-iris/10 flex items-center justify-center text-iris">
                    <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                        <ellipse cx="12" cy="5" rx="9" ry="3"/><path d="M21 12c0 1.66-4 3-9 3s-9-1.34-9-3"/><path d="M3 5v14c0 1.66 4 3 9 3s9-1.34 9-3V5"/>
                    </svg>
                </div>
            </div>
            <div class="flex items-baseline gap-1.5">
                <span class="text-4xl font-display font-bold text-foreground tabular-nums tracking-tight">{totalSegments.toLocaleString()}</span>
                <span class="text-sm text-muted-foreground">segments</span>
            </div>
            <p class="text-[11px] text-muted-foreground mt-1">
                <span class="text-iris font-semibold">Session total</span> · SQLite WAL
            </p>
            <div class="mt-3 h-1 rounded-full bg-muted overflow-hidden">
                <div class="h-full bg-iris" style="width: {Math.min(100, totalSegments / 50)}%"></div>
            </div>
        </a>
    </section>

    <!-- Two column layout: Cameras + Events -->
    <div class="grid grid-cols-1 lg:grid-cols-12 gap-5 page-enter stagger-3">
        <!-- Camera status panel -->
        <section class="lg:col-span-7 panel flex flex-col">
            <div class="panel-header">
                <div class="flex items-center gap-3">
                    <h2 class="section-title">Channel Status</h2>
                    <span class="badge">{enabledCameras.length}/{cameras.length}</span>
                </div>
                <a href="/cameras" class="text-[11px] font-semibold text-muted-foreground hover:text-foreground transition-colors flex items-center gap-1">
                    Manage channels
                    <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5">
                        <polyline points="9 18 15 12 9 6"/>
                    </svg>
                </a>
            </div>

            <div class="flex-1 p-3">
                {#if cameras.length === 0}
                    <div class="flex flex-col items-center justify-center py-16 text-center border border-dashed border-border rounded-xl">
                        <div class="w-12 h-12 rounded-xl bg-surface-2 border border-border flex items-center justify-center text-muted-foreground mb-3">
                            <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5">
                                <path d="M23 19a2 2 0 0 1-2 2H3a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h4l2-3h6l2 3h4a2 2 0 0 1 2 2z"/><circle cx="12" cy="13" r="4"/>
                            </svg>
                        </div>
                        <p class="text-sm font-semibold text-foreground">No channels registered</p>
                        <p class="text-xs text-muted-foreground mt-1 mb-3 max-w-xs">Connect a webcam or RTSP stream to begin surveillance</p>
                        <a href="/cameras" class="btn btn-primary btn-sm">Add channel</a>
                    </div>
                {:else}
                    <div class="flex flex-col gap-1.5">
                        {#each cameras as camera, i (camera.id)}
                            {@const recorder = recorders.find(r => r.cameraId === camera.id)}
                            <a
                                href="/monitoring"
                                class="flex items-center gap-3 px-3 py-2.5 rounded-lg border border-border bg-surface-2/50 hover:bg-card-hover hover:border-border-strong transition-all group"
                            >
                                <div class="w-1.5 h-10 rounded-full transition-colors"
                                    class:bg-jade={recorder?.state === 'recording' && camera.enabled}
                                    class:bg-crimson={recorder?.state === 'error'}
                                    class:bg-muted-foreground={!camera.enabled}
                                    class:bg-ember={camera.enabled && recorder?.state !== 'recording' && recorder?.state !== 'error'}></div>

                                <div class="w-8 h-8 rounded-md bg-surface-3 border border-border flex items-center justify-center text-muted-foreground group-hover:text-foreground transition-colors shrink-0">
                                    {#if camera.source === 'webcam'}
                                        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                                            <rect x="2" y="6" width="14" height="12" rx="2"/><polygon points="22 8 16 12 22 16 22 8"/>
                                        </svg>
                                    {:else if camera.source === 'rtsp'}
                                        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                                            <path d="M2 12a10 10 0 0 1 20 0"/><path d="M2 12a10 10 0 0 0 20 0"/><line x1="2" y1="12" x2="22" y2="12"/>
                                        </svg>
                                    {:else}
                                        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                                            <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/>
                                        </svg>
                                    {/if}
                                </div>

                                <div class="flex-1 min-w-0">
                                    <p class="text-sm font-semibold text-foreground truncate">{camera.name}</p>
                                    <p class="text-[10px] text-muted-foreground font-mono uppercase tracking-wider mt-0.5">
                                        {camera.source} · {camera.fps || 5} FPS · ID {camera.id}
                                    </p>
                                </div>

                                <div class="flex items-center gap-2 shrink-0">
                                    {#if recorder?.state === 'recording'}
                                        <span class="badge badge-crimson">
                                            <span class="w-1 h-1 rounded-full bg-crimson status-pulse"></span>
                                            REC
                                        </span>
                                    {:else if recorder?.state === 'error'}
                                        <span class="badge badge-crimson">ERROR</span>
                                    {:else if camera.enabled}
                                        <span class="badge badge-ember">STANDBY</span>
                                    {:else}
                                        <span class="badge badge-muted">OFFLINE</span>
                                    {/if}
                                    <span class="text-[10px] text-muted-foreground font-mono hidden sm:block">
                                        {recorder?.lastSegmentAt ? relativeTime(recorder.lastSegmentAt) : '—'}
                                    </span>
                                </div>
                            </a>
                        {/each}
                    </div>
                {/if}
            </div>
        </section>

        <!-- Recent events feed -->
        <section class="lg:col-span-5 panel flex flex-col">
            <div class="panel-header">
                <div class="flex items-center gap-3">
                    <h2 class="section-title">Detection Feed</h2>
                    <span class="badge badge-cyan">LIVE</span>
                </div>
                <a href="/events" class="text-[11px] font-semibold text-muted-foreground hover:text-foreground transition-colors flex items-center gap-1">
                    Open events
                    <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5">
                        <polyline points="9 18 15 12 9 6"/>
                    </svg>
                </a>
            </div>

            <div class="flex-1 p-3 max-h-[520px] overflow-y-auto">
                {#if events.length === 0}
                    <div class="flex flex-col items-center justify-center py-16 text-center border border-dashed border-border rounded-xl">
                        <div class="w-12 h-12 rounded-xl bg-surface-2 border border-border flex items-center justify-center text-muted-foreground mb-3">
                            <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5">
                                <circle cx="12" cy="12" r="10"/><path d="m9 9 6 6"/><path d="m15 9-6 6"/>
                            </svg>
                        </div>
                        <p class="text-sm font-semibold text-foreground">No detections yet</p>
                        <p class="text-xs text-muted-foreground mt-1">AI events will stream here in real time</p>
                    </div>
                {:else}
                    <div class="flex flex-col gap-1.5">
                        {#each events.slice(0, 12) as event, i (event.id)}
                            <div
                                class="flex items-start gap-3 px-3 py-2.5 rounded-lg border border-border bg-surface-2/30 hover:bg-card-hover transition-colors group"
                            >
                                <div class="w-7 h-7 rounded-md flex items-center justify-center shrink-0 mt-0.5
                                    {event.severity === 'critical' ? 'bg-crimson/10 text-crimson' : ''}
                                    {event.severity === 'warning' ? 'bg-gold/10 text-gold' : ''}
                                    {event.severity === 'info' ? 'bg-cyan/10 text-cyan' : ''}">
                                    <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5">
                                        <circle cx="12" cy="12" r="3"/>
                                        <path d="M12 1v6m0 10v6m11-11h-6M7 12H1"/>
                                    </svg>
                                </div>

                                <div class="flex-1 min-w-0">
                                    <div class="flex items-baseline gap-2">
                                        <p class="text-xs font-semibold text-foreground truncate">{event.label}</p>
                                        <span class="text-[10px] text-muted-foreground font-mono shrink-0">· {Math.round(event.confidence * 100)}%</span>
                                    </div>
                                    <p class="text-[10px] text-muted-foreground font-mono mt-0.5">
                                        <span class="text-foreground/70">{event.camera_id}</span> · {relativeTime(event.timestamp)}
                                    </p>
                                </div>

                                <span class="text-[9px] font-bold uppercase tracking-wider px-1.5 h-4 inline-flex items-center rounded shrink-0
                                    {event.severity === 'critical' ? 'bg-crimson/10 text-crimson' : ''}
                                    {event.severity === 'warning' ? 'bg-gold/10 text-gold' : ''}
                                    {event.severity === 'info' ? 'bg-cyan/10 text-cyan' : ''}">
                                    {event.severity}
                                </span>
                            </div>
                        {/each}
                    </div>
                {/if}
            </div>
        </section>
    </div>
</div>
