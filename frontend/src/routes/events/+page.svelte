<script lang="ts">
    import { onMount, onDestroy } from 'svelte';
    import { getApiToken, buildWsUrl } from '$lib/apiToken';
    import { fly, fade } from 'svelte/transition';

    interface CameraConfig {
        id: string;
        name: string;
        source: 'rtsp' | 'webcam';
        enabled: boolean;
        fps?: number;
    }

    interface CameraEvent {
        id: string;
        camera_id: string;
        label: string;
        confidence: number;
        timestamp: string;
        snapshot_path?: string;
        severity: 'info' | 'warning' | 'critical';
    }

    // --- Svelte 5 Reactive State Runes ---
    let cameras = $state<CameraConfig[]>([]);
    let events = $state<CameraEvent[]>([]);
    let loading = $state(true);

    // Layout view mode (default to tabular view)
    let viewMode = $state<'table' | 'grid'>('table');

    // Filters
    let selectedCameraId = $state('all');
    let selectedLabel = $state('all');
    let selectedSeverity = $state<'all' | 'info' | 'warning' | 'critical'>('all');
    let limit = $state(50);
    let searchFilter = $state('');

    // Zoomable Snapshot modal
    let selectedSnapshotEvent = $state<CameraEvent | null>(null);

    // WebSocket state
    let ws: WebSocket | null = $state(null);
    let wsStatus = $state('disconnected');
    let wsReconnectTimer: ReturnType<typeof setTimeout> | null = null;
    let apiToken = $state('');

    // Toast alert
    let toastMessage = $state('');
    let toastType = $state<'info' | 'success' | 'warning' | 'error'>('info');
    let showToast = $state(false);

    // --- Derived States (Svelte 5 Runes) ---
    const filteredEvents = $derived.by(() => {
        return events
            .filter(ev => {
                const matchCam = selectedCameraId === 'all' || ev.camera_id === selectedCameraId;
                const matchLabel = selectedLabel === 'all' || ev.label.toLowerCase() === selectedLabel.toLowerCase();
                const matchSeverity = selectedSeverity === 'all' || ev.severity === selectedSeverity;
                const matchSearch = searchFilter === '' || 
                    ev.label.toLowerCase().includes(searchFilter.toLowerCase()) ||
                    ev.camera_id.toLowerCase().includes(searchFilter.toLowerCase());
                return matchCam && matchLabel && matchSeverity && matchSearch;
            })
            .sort((a, b) => b.timestamp.localeCompare(a.timestamp));
    });

    const uniqueLabels = $derived.by(() => {
        const labels = new Set<string>();
        events.forEach(ev => {
            if (ev.label) labels.add(ev.label.toLowerCase());
        });
        return Array.from(labels).sort();
    });

    const countsBySeverity = $derived.by(() => {
        const counts = { info: 0, warning: 0, critical: 0 };
        filteredEvents.forEach(ev => {
            if (ev.severity in counts) {
                counts[ev.severity]++;
            }
        });
        return counts;
    });

    // --- Methods & Operations ---
    function triggerToast(msg: string, type: typeof toastType = 'info') {
        toastMessage = msg;
        toastType = type;
        showToast = true;
        setTimeout(() => {
            showToast = false;
        }, 4000);
    }

    async function loadData() {
        loading = true;
        try {
            const [camRes, eventRes] = await Promise.all([
                fetch('/api/v1/cameras'),
                fetch(`/api/v1/events?camera_id=all&label=all&limit=${limit}`)
            ]);

            if (camRes.ok) cameras = await camRes.json();
            if (eventRes.ok) events = await eventRes.json();
        } catch (err: any) {
            console.error('[Events page] Load failed:', err);
            triggerToast('Failed to load telemetry databases.', 'error');
        } finally {
            loading = false;
        }
    }

    async function deleteEventItem(id: string, e: MouseEvent) {
        e.stopPropagation();
        
        try {
            const res = await fetch(`/api/v1/events?id=${id}`, { method: 'DELETE' });
            const data = await res.json();
            if (data.success) {
                triggerToast('Event purged successfully.', 'success');
                events = events.filter(ev => ev.id !== id);
                if (selectedSnapshotEvent?.id === id) {
                    selectedSnapshotEvent = null;
                }
            } else {
                triggerToast(data.error || 'Failed to delete event.', 'error');
            }
        } catch (err: any) {
            triggerToast(`Purge error: ${err.message}`, 'error');
        }
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
            };

            ws.onclose = () => {
                wsStatus = 'disconnected';
                if (wsReconnectTimer) clearTimeout(wsReconnectTimer);
                wsReconnectTimer = setTimeout(connectWS, 4000);
            };

            ws.onmessage = (msg) => {
                try {
                    const data = JSON.parse(msg.data);
                // Listen for newly emitted detections
                if (data.event === 'detections') {
                    const newEvent: CameraEvent = {
                        id: data.eventId || Math.random().toString(36).substring(7),
                        camera_id: data.cameraId || 'unknown',
                        label: data.label || 'motion',
                        confidence: data.confidence || 0.8,
                        timestamp: new Date().toISOString(),
                        snapshot_path: data.snapshot_path,
                        severity: data.severity || 'info'
                    };
                    
                    // Prepend new event to state list and keep list bounded to limit * 2
                    events = [newEvent, ...events].slice(0, limit * 2);
                    
                    // Trigger live toast
                    triggerToast(`Live detection: ${newEvent.label.toUpperCase()} spotted on camera ${newEvent.camera_id}!`, 'info');
                }
            } catch (err) {
                console.error('Error handling live WS event:', err);
            }
        };
        })();
    }

    // --- Formatters ---
    function formatTime(isoString: string): string {
        try {
            const date = new Date(isoString);
            return date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
        } catch {
            return isoString;
        }
    }

    function formatDateFriendly(isoString: string): string {
        try {
            const date = new Date(isoString);
            return date.toLocaleDateString([], { month: 'short', day: 'numeric', year: 'numeric' });
        } catch {
            return isoString;
        }
    }

    function getRelativeTime(isoString: string): string {
        try {
            const date = new Date(isoString);
            const now = new Date();
            const diffMs = now.getTime() - date.getTime();
            const diffMins = Math.floor(diffMs / 60000);
            if (diffMins < 1) return 'Just now';
            if (diffMins < 60) return `${diffMins}m ago`;
            const diffHours = Math.floor(diffMins / 60);
            if (diffHours < 24) return `${diffHours}h ago`;
            return date.toLocaleDateString([], { month: 'short', day: 'numeric' });
        } catch {
            return '';
        }
    }

    let handleOutsideClick: (e: MouseEvent) => void;

    onMount(async () => {
        const token = await getApiToken();
        if (token) apiToken = token;
        loadData();
        connectWS();
        
        // Listen to click outside search list
        handleOutsideClick = (e: MouseEvent) => {
            const target = e.target as HTMLElement;
            if (selectedSnapshotEvent && target.closest('.modal-backdrop')) {
                selectedSnapshotEvent = null;
            }
        };
        window.addEventListener('click', handleOutsideClick);
    });

    onDestroy(() => {
        if (handleOutsideClick) {
            window.removeEventListener('click', handleOutsideClick);
        }
        if (wsReconnectTimer) {
            clearTimeout(wsReconnectTimer);
        }
        if (ws) {
            ws.onclose = null;
            ws.close();
        }
    });

    // Fetch new events list when limit filter changes
    $effect(() => {
        if (limit) {
            loadData();
        }
    });
</script>

<svelte:head>
    <title>Hawkeye Event Detections</title>
</svelte:head>

<div class="flex flex-col gap-6 w-full text-foreground pb-12">
    <!-- Toast alerts -->
    {#if showToast}
        <div class="fixed bottom-6 right-6 z-[100] px-4 py-3 rounded-xl border shadow-2xl flex items-center gap-3 animate-in slide-in-from-bottom-5 duration-300"
            class:bg-indigo-950={toastType === 'info'}
            class:border-indigo-500={toastType === 'info'}
            class:text-indigo-200={toastType === 'info'}
            class:bg-emerald-950={toastType === 'success'}
            class:border-emerald-500={toastType === 'success'}
            class:text-emerald-200={toastType === 'success'}
            class:bg-amber-950={toastType === 'warning'}
            class:border-amber-500={toastType === 'warning'}
            class:text-amber-200={toastType === 'warning'}
            class:bg-rose-950={toastType === 'error'}
            class:border-rose-500={toastType === 'error'}
            class:text-rose-200={toastType === 'error'}
            transition:fly={{ y: 20, duration: 200 }}>
            <span class="w-2.5 h-2.5 rounded-full animate-pulse"
                class:bg-indigo-400={toastType === 'info'}
                class:bg-emerald-400={toastType === 'success'}
                class:bg-amber-400={toastType === 'warning'}
                class:bg-rose-400={toastType === 'error'}></span>
            <span class="text-xs font-semibold">{toastMessage}</span>
        </div>
    {/if}

    <!-- Header bar -->
    <header class="flex flex-col md:flex-row md:items-center justify-between gap-4">
        <div>
            <h1 class="text-3xl font-extrabold tracking-tight text-white flex items-center gap-2">
                <svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" class="text-primary animate-pulse">
                    <circle cx="12" cy="12" r="10"/>
                    <path d="m15 9-6 6"/>
                    <path d="m9 9 6 6"/>
                </svg>
                Event Detections
            </h1>
            <p class="text-sm text-muted-foreground mt-1">Browse, query, and download event snapshots, face sightings, and critical event triggers.</p>
        </div>

        <div class="flex items-center gap-2">
            <span class="badge bg-indigo-500/10 border-indigo-500/20 text-indigo-400 py-1 px-3">
                <span class="w-1.5 h-1.5 rounded-full bg-indigo-400 mr-1.5 inline-block"></span>
                {countsBySeverity.info} Info
            </span>
            <span class="badge bg-amber-500/10 border-amber-500/20 text-amber-400 py-1 px-3">
                <span class="w-1.5 h-1.5 rounded-full bg-amber-400 mr-1.5 inline-block animate-pulse"></span>
                {countsBySeverity.warning} Warning
            </span>
            <span class="badge bg-rose-500/10 border-rose-500/20 text-rose-400 py-1 px-3">
                <span class="w-1.5 h-1.5 rounded-full bg-rose-500 mr-1.5 inline-block animate-ping"></span>
                {countsBySeverity.critical} Critical
            </span>
            <div class="flex items-center gap-1.5 px-3 py-1 rounded-full text-xs font-semibold border transition-all duration-200 {wsStatus === 'connected' ? 'bg-emerald-500/10 border-emerald-500/20 text-emerald-400' : 'bg-muted border-border text-muted-foreground'}">
                <span class="w-1.5 h-1.5 rounded-full {wsStatus === 'connected' ? 'bg-emerald-400 animate-pulse' : 'bg-muted-foreground'}"></span>
                <span>{wsStatus === 'connected' ? 'WS Stream Active' : 'WS Reconnecting...'}</span>
            </div>
        </div>
    </header>

    <!-- Filters Panel -->
    <section class="glass-panel p-4 bg-card/65 backdrop-blur-md flex flex-wrap items-center justify-between gap-4">
        <div class="flex flex-wrap items-center gap-4 w-full lg:w-auto">
            <!-- Camera filter -->
            <div class="flex flex-col gap-1">
                <label for="camera-select" class="text-[10px] font-bold text-muted-foreground tracking-wider uppercase">Filter Camera</label>
                <select id="camera-select" bind:value={selectedCameraId} class="select min-w-[160px] bg-background">
                    <option value="all">All Cameras</option>
                    {#each cameras as cam}
                        <option value={cam.id}>{cam.name}</option>
                    {/each}
                </select>
            </div>

            <!-- AI Label Filter -->
            <div class="flex flex-col gap-1">
                <label for="label-select" class="text-[10px] font-bold text-muted-foreground tracking-wider uppercase">AI Detections</label>
                <select id="label-select" bind:value={selectedLabel} class="select min-w-[150px] bg-background">
                    <option value="all">All Labels</option>
                    {#each uniqueLabels as lbl}
                        <option value={lbl}>{lbl.toUpperCase()}</option>
                    {/each}
                </select>
            </div>

            <!-- Severity Filter -->
            <div class="flex flex-col gap-1">
                <label for="severity-select" class="text-[10px] font-bold text-muted-foreground tracking-wider uppercase">Event Severity</label>
                <select id="severity-select" bind:value={selectedSeverity} class="select min-w-[140px] bg-background">
                    <option value="all">All Severities</option>
                    <option value="info">Info Only</option>
                    <option value="warning">Warning Only</option>
                    <option value="critical">Critical Only</option>
                </select>
            </div>

            <!-- Layout View Mode Toggle -->
            <div class="flex flex-col gap-1">
                <span class="text-[10px] font-bold text-muted-foreground tracking-wider uppercase">Layout View</span>
                <div class="flex items-center bg-background border border-border rounded-lg p-0.5 h-[2.25rem]">
                    <button 
                        type="button" 
                        onclick={() => viewMode = 'table'} 
                        class="px-3 py-1 rounded-md text-[10px] font-semibold transition-all cursor-pointer flex items-center gap-1.5 {viewMode === 'table' ? 'bg-indigo-600 text-white shadow-sm font-bold' : 'text-muted-foreground hover:text-foreground'}"
                    >
                        <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">
                            <line x1="3" y1="6" x2="21" y2="6"/>
                            <line x1="3" y1="12" x2="21" y2="12"/>
                            <line x1="3" y1="18" x2="21" y2="18"/>
                        </svg>
                        Table
                    </button>
                    <button 
                        type="button" 
                        onclick={() => viewMode = 'grid'} 
                        class="px-3 py-1 rounded-md text-[10px] font-semibold transition-all cursor-pointer flex items-center gap-1.5 {viewMode === 'grid' ? 'bg-indigo-600 text-white shadow-sm font-bold' : 'text-muted-foreground hover:text-foreground'}"
                    >
                        <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">
                            <rect x="3" y="3" width="7" height="7"/>
                            <rect x="14" y="3" width="7" height="7"/>
                            <rect x="14" y="14" width="7" height="7"/>
                            <rect x="3" y="14" width="7" height="7"/>
                        </svg>
                        Grid
                    </button>
                </div>
            </div>
        </div>

        <!-- Search Bar -->
        <div class="flex flex-col gap-1 w-full lg:w-72">
            <label for="search-input" class="text-[10px] font-bold text-muted-foreground tracking-wider uppercase">Dynamic Search</label>
            <div class="relative">
                <input id="search-input" type="text" placeholder="Search events..." bind:value={searchFilter} class="input w-full bg-background pl-8 text-xs py-1.5" />
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" class="absolute left-2.5 top-3 text-muted-foreground">
                    <circle cx="11" cy="11" r="8"/><path d="m21 21-4.3-4.3"/>
                </svg>
            </div>
        </div>
    </section>

    <!-- Main Content Grid -->
    {#if loading}
        <div class="flex flex-col items-center justify-center min-h-[400px] gap-3">
            <div class="w-8 h-8 rounded-full border-2 border-primary border-t-transparent animate-spin"></div>
            <p class="text-sm text-muted-foreground font-semibold">Compiling security telemetry feed...</p>
        </div>
    {:else if filteredEvents.length === 0}
        <div class="flex flex-col items-center justify-center min-h-[400px] text-center border border-dashed border-border rounded-xl p-12 bg-card/10">
            <svg width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" class="text-muted-foreground mb-4">
                <rect width="18" height="18" x="3" y="3" rx="2" ry="2"/>
                <line x1="9" x2="15" y1="9" y2="15"/>
                <line x1="15" x2="9" y1="9" y2="15"/>
            </svg>
            <h3 class="text-base font-bold text-foreground">No Event Detections</h3>
            <p class="text-xs text-muted-foreground mt-1 max-w-sm">No recorded detections match the current filter parameters. Verify if the YOLO detection engine skill is running.</p>
            <button onclick={() => { selectedCameraId = 'all'; selectedLabel = 'all'; selectedSeverity = 'all'; searchFilter = ''; }} class="btn mt-4 bg-primary text-primary-foreground text-xs py-1.5 px-4 font-semibold rounded-lg cursor-pointer">
                Reset Filter Settings
            </button>
        </div>
    {:else}
        {#if viewMode === 'table'}
            <div class="glass-panel overflow-hidden border border-border bg-card/45 backdrop-blur-md" in:fade={{ duration: 150 }}>
                <div class="overflow-x-auto w-full">
                    <table class="w-full text-left border-collapse">
                        <thead>
                            <tr class="border-b border-border bg-muted/40 text-muted-foreground font-sans text-[10px] uppercase font-bold tracking-wider">
                                <th class="px-5 py-3 w-24">Snapshot</th>
                                <th class="px-5 py-3">Camera</th>
                                <th class="px-5 py-3">AI Label</th>
                                <th class="px-5 py-3">Confidence</th>
                                <th class="px-5 py-3">Severity</th>
                                <th class="px-5 py-3">Time Sighted</th>
                                <th class="px-5 py-3 text-right">Actions</th>
                            </tr>
                        </thead>
                        <tbody class="divide-y divide-border/60">
                            {#each filteredEvents as ev (ev.id)}
                                <tr 
                                    class="group hover:bg-indigo-500/5 transition-colors duration-150 cursor-pointer"
                                    onclick={() => selectedSnapshotEvent = ev}
                                >
                                    <!-- Snapshot -->
                                    <td class="px-5 py-2.5">
                                        <div class="relative w-16 aspect-video rounded-lg overflow-hidden bg-black border border-border/80 shadow-sm shrink-0">
                                            {#if ev.snapshot_path && apiToken}
                                                <img
                                                    src={`/api/v1/events/${ev.id}/snapshot?token=${apiToken}`}
                                                    alt="Thumbnail"
                                                    class="w-full h-full object-cover group-hover:scale-110 transition-transform duration-300"
                                                    loading="lazy"
                                                    onerror={(e) => { (e.currentTarget as HTMLImageElement).style.display = 'none'; (e.currentTarget.nextElementSibling as HTMLElement | null)?.style && ((e.currentTarget.nextElementSibling as HTMLElement).style.display = 'flex'); }}
                                                />
                                                <div class="w-full h-full hidden items-center justify-center text-muted-foreground/30" style="display:none">
                                                    <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5">
                                                        <path d="M23 19a2 2 0 0 1-2 2H3a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h4l2-3h6l2 3h4a2 2 0 0 1 2 2z"/>
                                                    </svg>
                                                </div>
                                            {:else}
                                                <div class="w-full h-full flex items-center justify-center text-muted-foreground/30">
                                                    <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5">
                                                        <path d="M23 19a2 2 0 0 1-2 2H3a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h4l2-3h6l2 3h4a2 2 0 0 1 2 2z"/>
                                                    </svg>
                                                </div>
                                            {/if}
                                        </div>
                                    </td>

                                    <!-- Camera -->
                                    <td class="px-5 py-2.5 align-middle">
                                        <span class="text-xs font-bold text-white tracking-wide">{ev.camera_id}</span>
                                    </td>

                                    <!-- Detection -->
                                    <td class="px-5 py-2.5 align-middle">
                                        <span class="inline-flex items-center text-[10px] font-bold px-2 py-0.5 rounded bg-indigo-500/10 border border-indigo-500/20 text-indigo-400 uppercase tracking-wider">
                                            {ev.label}
                                        </span>
                                    </td>

                                    <!-- Confidence -->
                                    <td class="px-5 py-2.5 align-middle">
                                        <div class="flex flex-col gap-1 max-w-[120px] w-24">
                                            <div class="flex justify-between items-center text-[10px] font-bold">
                                                <span class="text-white">{Math.round(ev.confidence * 100)}%</span>
                                            </div>
                                            <div class="w-full bg-border rounded-full h-1">
                                                <div class="bg-indigo-500 h-1 rounded-full transition-all" style="width: {ev.confidence * 100}%"></div>
                                            </div>
                                        </div>
                                    </td>

                                    <!-- Severity -->
                                    <td class="px-5 py-2.5 align-middle">
                                        <span class="inline-flex items-center gap-1 text-[9px] font-bold px-2 py-0.5 rounded-full border
                                            {ev.severity === 'info' ? 'bg-indigo-500/10 border-indigo-500/20 text-indigo-400' : ''}
                                            {ev.severity === 'warning' ? 'bg-amber-500/10 border-amber-500/20 text-amber-400' : ''}
                                            {ev.severity === 'critical' ? 'bg-rose-500/10 border-rose-500/20 text-rose-400' : ''}"
                                        >
                                            <span class="w-1 h-1 rounded-full
                                                {ev.severity === 'info' ? 'bg-indigo-400' : ''}
                                                {ev.severity === 'warning' ? 'bg-amber-400' : ''}
                                                {ev.severity === 'critical' ? 'bg-rose-400' : ''}"
                                            ></span>
                                            {ev.severity.toUpperCase()}
                                        </span>
                                    </td>

                                    <!-- Time Sighted -->
                                    <td class="px-5 py-2.5 align-middle">
                                        <div class="flex flex-col text-xs">
                                            <span class="text-white font-medium">{formatDateFriendly(ev.timestamp)}</span>
                                            <span class="text-muted-foreground text-[10px]">{formatTime(ev.timestamp)} <span class="text-indigo-400">({getRelativeTime(ev.timestamp)})</span></span>
                                        </div>
                                    </td>

                                    <!-- Actions -->
                                    <td class="px-5 py-2.5 align-middle text-right">
                                        <div class="flex items-center justify-end gap-2" onclick={(e) => e.stopPropagation()}>
                                            <!-- Play Footage link -->
                                            <a 
                                                href="/review?camera_id={ev.camera_id}&timestamp={ev.timestamp}&play=true"
                                                class="bg-indigo-600 hover:bg-indigo-700 text-white p-2 rounded-lg transition-colors cursor-pointer inline-flex items-center justify-center shadow"
                                                title="Play Footage"
                                            >
                                                <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round">
                                                    <polygon points="5 3 19 12 5 21 5 3"/>
                                                </svg>
                                            </a>
                                            <!-- Download Snapshot link -->
                                            {#if ev.snapshot_path}
                                                <a 
                                                    href="/api/v1/events/{ev.id}/snapshot?token={apiToken}" 
                                                    download="snapshot_{ev.camera_id}_{ev.id}.jpg"
                                                    class="bg-muted hover:bg-accent border border-border p-2 rounded-lg text-muted-foreground hover:text-foreground transition-all cursor-pointer inline-flex items-center justify-center shadow"
                                                    title="Download Snapshot"
                                                >
                                                    <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">
                                                        <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4M7 10l5 5 5-5M12 15V3"/>
                                                    </svg>
                                                </a>
                                            {/if}
                                            <!-- Purge Event -->
                                            <button 
                                                onclick={(e) => deleteEventItem(ev.id, e)} 
                                                class="bg-zinc-900 hover:bg-rose-950/30 border border-border hover:border-rose-500/25 p-2 rounded-lg text-muted-foreground hover:text-rose-400 transition-all cursor-pointer inline-flex items-center justify-center shadow"
                                                title="Purge Sighting"
                                            >
                                                <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">
                                                    <path d="M3 6h18M19 6v14c0 1-1 2-2 2H7c-1 0-2-1-2-2V6M8 6V4c0-1 1-2 2-2h4c1 0 2 1 2 2v2M10 11v6M14 11v6"/>
                                                </svg>
                                            </button>
                                        </div>
                                    </td>
                                </tr>
                            {/each}
                        </tbody>
                    </table>
                </div>
            </div>
        {:else}
            <!-- Render original beautiful card grid -->
            <div class="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-3 lg:grid-cols-4 gap-5" in:fade={{ duration: 150 }}>
                {#each filteredEvents as ev (ev.id)}
                    <div 
                        role="button"
                        tabindex="0"
                        onclick={() => selectedSnapshotEvent = ev}
                        onkeydown={(e) => e.key === 'Enter' && (selectedSnapshotEvent = ev)}
                        class="group relative border bg-card hover:bg-card-hover border-border hover:border-primary/40 rounded-xl overflow-hidden shadow-md transition-all duration-300 hover:-translate-y-1 cursor-pointer flex flex-col justify-between"
                    >
                        <!-- Event Snapshot Visual Area -->
                        <div class="relative aspect-video bg-black overflow-hidden shrink-0">
                            {#if ev.snapshot_path && apiToken}
                                <img
                                    src={`/api/v1/events/${ev.id}/snapshot?token=${apiToken}`}
                                    alt="{ev.label} event capture"
                                    class="w-full h-full object-cover group-hover:scale-105 transition-transform duration-500"
                                    loading="lazy"
                                    onerror={(e) => { (e.currentTarget as HTMLImageElement).style.display = 'none'; (e.currentTarget.nextElementSibling as HTMLElement | null)?.style && ((e.currentTarget.nextElementSibling as HTMLElement).style.display = 'flex'); }}
                                />
                                <div class="w-full h-full hidden flex-col items-center justify-center text-muted-foreground/30 gap-2" style="display:none">
                                    <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                                        <path d="M23 19a2 2 0 0 1-2 2H3a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h4l2-3h6l2 3h4a2 2 0 0 1 2 2z"/>
                                        <circle cx="12" cy="13" r="4"/>
                                    </svg>
                                    <span class="text-[9px] uppercase tracking-wider font-semibold">Snapshot Unavailable</span>
                                </div>
                            {:else}
                                <div class="w-full h-full flex flex-col items-center justify-center text-muted-foreground/30 gap-2">
                                    <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                                        <path d="M23 19a2 2 0 0 1-2 2H3a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h4l2-3h6l2 3h4a2 2 0 0 1 2 2z"/>
                                        <circle cx="12" cy="13" r="4"/>
                                    </svg>
                                    <span class="text-[9px] uppercase tracking-wider font-semibold">{!ev.snapshot_path ? 'No Snapshot Captured' : 'Loading Snapshot...'}</span>
                                </div>
                            {/if}

                            <!-- Camera badge absolute overlay -->
                            <div class="absolute top-2 left-2 z-10">
                                <span class="text-[9px] font-bold px-2 py-0.5 rounded shadow-sm bg-black/75 backdrop-blur-sm text-foreground">
                                    {ev.camera_id}
                                </span>
                            </div>

                            <!-- Trash Purge button absolute overlay -->
                            <button 
                                onclick={(e) => deleteEventItem(ev.id, e)} 
                                class="absolute top-2 right-2 p-1.5 rounded-lg bg-black/60 hover:bg-zinc-800 text-muted-foreground hover:text-rose-400 transition-all shadow-sm opacity-0 group-hover:opacity-100 cursor-pointer"
                                title="Delete Event"
                            >
                                <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">
                                    <path d="M3 6h18M19 6v14c0 1-1 2-2 2H7c-1 0-2-1-2-2V6M8 6V4c0-1 1-2 2-2h4c1 0 2 1 2 2v2M10 11v6M14 11v6"/>
                                </svg>
                            </button>

                            <div class="absolute bottom-2 right-2 bg-black/65 backdrop-blur-sm px-2 py-0.5 rounded text-[10px] text-muted-foreground font-semibold">
                                {getRelativeTime(ev.timestamp)}
                            </div>
                        </div>

                        <!-- Meta Data Section -->
                        <div class="p-3.5 flex flex-col gap-2 flex-1 justify-between">
                            <div>
                                <div class="flex justify-between items-center">
                                    <strong class="text-sm font-extrabold text-white tracking-tight uppercase">
                                        {ev.label}
                                    </strong>
                                    <span class="text-xs font-bold text-primary">
                                        {Math.round(ev.confidence * 100)}%
                                    </span>
                                </div>
                                <div class="text-[10px] text-muted-foreground mt-1 flex flex-col gap-0.5">
                                    <span>Date: {formatDateFriendly(ev.timestamp)}</span>
                                    <span>Time: {formatTime(ev.timestamp)}</span>
                                </div>
                            </div>

                            <!-- Actions row -->
                            <div class="flex gap-2 mt-2 pt-2.5 border-t border-border/60 shrink-0">
                                <!-- Play footage cross link -->
                                <a 
                                    href="/review?camera_id={ev.camera_id}&timestamp={ev.timestamp}&play=true"
                                    onclick={(e) => e.stopPropagation()}
                                    class="flex-1 text-center bg-primary hover:bg-primary/90 text-primary-foreground text-[10px] font-bold py-1.5 px-2.5 rounded-md shadow-md transition-colors cursor-pointer flex items-center justify-center gap-1"
                                >
                                    <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round">
                                        <polygon points="5 3 19 12 5 21 5 3"/>
                                    </svg>
                                    Play Footage
                                </a>
                                <!-- Download snapshot link if exists -->
                                {#if ev.snapshot_path}
                                    <a 
                                        href="/api/v1/events/{ev.id}/snapshot?token={apiToken}" 
                                        download="snapshot_{ev.camera_id}_{ev.id}.jpg"
                                        onclick={(e) => e.stopPropagation()}
                                        class="p-1.5 bg-muted hover:bg-accent border border-border rounded-md text-muted-foreground hover:text-foreground transition-all cursor-pointer flex items-center justify-center"
                                        title="Download Snapshot"
                                    >
                                        <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">
                                            <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4M7 10l5 5 5-5M12 15V3"/>
                                        </svg>
                                    </a>
                                {/if}
                            </div>
                        </div>
                    </div>
                {/each}
            </div>
        {/if}
    {/if}

    <!-- High-res Snapshot Modal Overlay Detail View -->
    {#if selectedSnapshotEvent}
        <div class="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/85 backdrop-blur-md animate-in fade-in duration-200 modal-backdrop">
            <div class="bg-card border border-border rounded-xl shadow-2xl overflow-hidden max-w-4xl w-full flex flex-col max-h-[90vh]" in:fly={{ y: 20, duration: 250 }}>
                <!-- Header -->
                <div class="flex justify-between items-center px-5 py-4 border-b border-border bg-muted/20">
                    <div>
                        <h3 class="text-sm font-extrabold text-white tracking-tight uppercase flex items-center gap-2">
                            <span class="w-2 h-2 rounded-full"
                                class:bg-indigo-500={selectedSnapshotEvent.severity === 'info'}
                                class:bg-amber-500={selectedSnapshotEvent.severity === 'warning'}
                                class:bg-rose-500={selectedSnapshotEvent.severity === 'critical'}></span>
                            {selectedSnapshotEvent.label} Sighting
                        </h3>
                        <p class="text-[10px] text-muted-foreground mt-0.5">Camera: {selectedSnapshotEvent.camera_id} · Recorded: {formatDateFriendly(selectedSnapshotEvent.timestamp)} {formatTime(selectedSnapshotEvent.timestamp)}</p>
                    </div>
                    <button class="text-muted-foreground hover:text-foreground text-xl cursor-pointer p-1" onclick={() => selectedSnapshotEvent = null}>
                        &times;
                    </button>
                </div>

                <!-- High-res photo window -->
                <div class="flex-1 bg-black overflow-hidden flex items-center justify-center p-2 min-h-[300px]">
                    {#if selectedSnapshotEvent.snapshot_path && apiToken}
                        <img 
                            src={`/api/v1/events/${selectedSnapshotEvent.id}/snapshot?token=${apiToken}`} 
                            alt="{selectedSnapshotEvent.label} event capture detail" 
                            class="max-w-full max-h-[60vh] object-contain rounded border border-border"
                        />
                    {:else}
                        <div class="flex flex-col items-center justify-center text-muted-foreground/30 gap-3 py-16">
                            <svg width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">
                                <path d="M23 19a2 2 0 0 1-2 2H3a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h4l2-3h6l2 3h4a2 2 0 0 1 2 2z"/>
                                <circle cx="12" cy="13" r="4"/>
                            </svg>
                            <span class="text-xs uppercase tracking-wider font-extrabold">{!selectedSnapshotEvent.snapshot_path ? 'Detailed Snapshot Unavailable' : 'Loading Detailed Snapshot...'}</span>
                        </div>
                    {/if}
                </div>

                <!-- Metrics & Control Footer -->
                <div class="px-5 py-4 border-t border-border bg-muted/10 flex flex-col sm:flex-row justify-between items-center gap-3">
                    <div class="flex gap-4 text-xs font-semibold text-muted-foreground">
                        <div>Severity: <strong class="text-white capitalize">{selectedSnapshotEvent.severity}</strong></div>
                        <div>Confidence: <strong class="text-white">{Math.round(selectedSnapshotEvent.confidence * 100)}%</strong></div>
                    </div>
                    <div class="flex gap-2.5 w-full sm:w-auto justify-end">
                        <button onclick={(e) => { deleteEventItem(selectedSnapshotEvent!.id, e); }} class="btn bg-destructive hover:bg-destructive/90 text-destructive-foreground text-xs py-1.5 px-4 font-semibold rounded-lg cursor-pointer">
                            Purge Alert
                        </button>
                        {#if selectedSnapshotEvent.snapshot_path}
                            <a href="/api/v1/events/{selectedSnapshotEvent.id}/snapshot?token={apiToken}" download="snapshot_{selectedSnapshotEvent.camera_id}_{selectedSnapshotEvent.id}.jpg" class="btn bg-muted hover:bg-accent border border-border text-foreground text-xs py-1.5 px-4 font-semibold rounded-lg cursor-pointer">
                                Download JPG
                            </a>
                        {/if}
                        <a href="/review?camera_id={selectedSnapshotEvent.camera_id}&timestamp={selectedSnapshotEvent.timestamp}&play=true" onclick={() => selectedSnapshotEvent = null} class="btn bg-primary text-primary-foreground text-xs py-1.5 px-4.5 font-bold rounded-lg cursor-pointer shadow-md flex items-center justify-center gap-1.5">
                            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3.5" stroke-linecap="round" stroke-linejoin="round">
                                <polygon points="5 3 19 12 5 21 5 3"/>
                            </svg>
                            Stream Recording Footage
                        </a>
                    </div>
                </div>
            </div>
        </div>
    {/if}
</div>

<style>
    /* Styling adjustments to match Hawkeye's dark premium aesthetic */
    .select {
        height: 2.25rem;
        font-size: 0.75rem;
        font-weight: 500;
        border-radius: 0.5rem;
        border: 1px solid var(--border);
        padding-left: 0.75rem;
        padding-right: 2rem;
        outline: none;
        transition: all 0.2s;
    }
    .select:focus {
        border-color: var(--primary);
        box-shadow: 0 0 0 2px rgba(99, 102, 241, 0.15);
    }
    .input {
        font-size: 0.75rem;
        font-weight: 500;
        border-radius: 0.5rem;
        border: 1px solid var(--border);
        outline: none;
        transition: all 0.2s;
    }
    .input:focus {
        border-color: var(--primary);
        box-shadow: 0 0 0 2px rgba(99, 102, 241, 0.15);
    }
    .badge {
        font-size: 10px;
        font-weight: 700;
        border-radius: 9999px;
        border-width: 1px;
    }
</style>
