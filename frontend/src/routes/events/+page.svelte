<script lang="ts">
    import { onMount, onDestroy } from 'svelte';
    import { getApiToken, buildWsUrl } from '$lib/apiToken';
    import { fly, fade } from 'svelte/transition';

    interface CameraConfig {
        id: string;
        name: string;
        source: 'rtsp' | 'webcam' | 'file';
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

    let cameras = $state<CameraConfig[]>([]);
    let events = $state<CameraEvent[]>([]);
    let loading = $state(true);

    let viewMode = $state<'table' | 'grid'>('table');

    let selectedCameraId = $state('all');
    let selectedLabel = $state('all');
    let selectedSeverity = $state<'all' | 'info' | 'warning' | 'critical'>('all');
    let limit = $state(50);
    let searchFilter = $state('');

    let selectedSnapshotEvent = $state<CameraEvent | null>(null);

    let ws: WebSocket | null = $state(null);
    let wsStatus = $state<'connected' | 'connecting' | 'disconnected'>('disconnected');
    let wsReconnectTimer: ReturnType<typeof setTimeout> | null = null;
    let apiToken = $state('');

    let toastMessage = $state('');
    let toastType = $state<'info' | 'success' | 'warning' | 'error'>('info');
    let showToast = $state(false);

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
                counts[ev.severity as keyof typeof counts]++;
            }
        });
        return counts;
    });

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
                fetch('/api/v1/cameras', { cache: 'no-store' }),
                fetch(`/api/v1/events?camera_id=all&label=all&limit=${limit}`, { cache: 'no-store' })
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

            ws.onopen = () => { wsStatus = 'connected'; };

            ws.onclose = () => {
                wsStatus = 'disconnected';
                if (wsReconnectTimer) clearTimeout(wsReconnectTimer);
                wsReconnectTimer = setTimeout(connectWS, 4000);
            };

            ws.onmessage = (msg) => {
                try {
                    const data = JSON.parse(msg.data);
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

                        events = [newEvent, ...events].slice(0, limit * 2);
                        triggerToast(`Live: ${newEvent.label.toUpperCase()} · ${newEvent.camera_id}`, 'info');
                    }
                } catch (err) {
                    console.error('Error handling live WS event:', err);
                }
            };
        })();
    }

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

    onMount(async () => {
        const token = await getApiToken();
        if (token) apiToken = token;
        loadData();
        connectWS();
    });

    onDestroy(() => {
        if (wsReconnectTimer) {
            clearTimeout(wsReconnectTimer);
        }
        if (ws) {
            ws.onclose = null;
            ws.close();
        }
    });

    $effect(() => {
        if (limit) {
            loadData();
        }
    });
</script>

<svelte:head>
    <title>Hawkeye · Events</title>
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

    <!-- Header -->
    <header class="flex flex-col md:flex-row md:items-end justify-between gap-4 page-enter">
        <div>
            <div class="flex items-center gap-2.5 mb-2">
                <span class="badge {wsStatus === 'connected' ? 'badge-cyan' : 'badge-muted'}">
                    <span class="w-1.5 h-1.5 rounded-full {wsStatus === 'connected' ? 'bg-cyan status-pulse' : 'bg-muted-foreground'}"></span>
                    {wsStatus === 'connected' ? 'Live stream' : 'Reconnecting'}
                </span>
                <span class="text-[11px] text-muted-foreground font-mono">{events.length} events</span>
            </div>
            <h1 class="text-2xl md:text-3xl font-display font-bold text-foreground tracking-tight leading-none">Event Detections</h1>
            <p class="text-sm text-muted-foreground mt-2">Browse, query, and download AI event captures, snapshots, and sightings.</p>
        </div>

        <div class="flex items-center gap-2 flex-wrap">
            <div class="flex items-center gap-1 px-2.5 h-8 rounded-lg border border-border bg-card">
                <span class="w-1.5 h-1.5 rounded-full bg-cyan"></span>
                <span class="text-[10px] font-mono font-bold text-foreground tabular-nums">{countsBySeverity.info}</span>
                <span class="text-[10px] text-muted-foreground uppercase tracking-wider">info</span>
            </div>
            <div class="flex items-center gap-1 px-2.5 h-8 rounded-lg border border-gold/20 bg-gold/5">
                <span class="w-1.5 h-1.5 rounded-full bg-gold animate-pulse"></span>
                <span class="text-[10px] font-mono font-bold text-foreground tabular-nums">{countsBySeverity.warning}</span>
                <span class="text-[10px] text-muted-foreground uppercase tracking-wider">warn</span>
            </div>
            <div class="flex items-center gap-1 px-2.5 h-8 rounded-lg border border-crimson/20 bg-crimson/5">
                <span class="w-1.5 h-1.5 rounded-full bg-crimson status-pulse"></span>
                <span class="text-[10px] font-mono font-bold text-foreground tabular-nums">{countsBySeverity.critical}</span>
                <span class="text-[10px] text-muted-foreground uppercase tracking-wider">crit</span>
            </div>
        </div>
    </header>

    <!-- Filters -->
    <section class="panel page-enter stagger-1">
        <div class="p-4 flex flex-wrap items-end gap-4">
            <div class="flex flex-col gap-1.5">
                <span class="section-eyebrow">Camera</span>
                <select bind:value={selectedCameraId} class="select min-w-[160px]">
                    <option value="all">All cameras</option>
                    {#each cameras as cam}
                        <option value={cam.id}>{cam.name}</option>
                    {/each}
                </select>
            </div>
            <div class="flex flex-col gap-1.5">
                <span class="section-eyebrow">AI Label</span>
                <select bind:value={selectedLabel} class="select min-w-[150px]">
                    <option value="all">All labels</option>
                    {#each uniqueLabels as lbl}
                        <option value={lbl}>{lbl.toUpperCase()}</option>
                    {/each}
                </select>
            </div>
            <div class="flex flex-col gap-1.5">
                <span class="section-eyebrow">Severity</span>
                <select bind:value={selectedSeverity} class="select min-w-[140px]">
                    <option value="all">All</option>
                    <option value="info">Info</option>
                    <option value="warning">Warning</option>
                    <option value="critical">Critical</option>
                </select>
            </div>
            <div class="flex flex-col gap-1.5">
                <span class="section-eyebrow">Limit</span>
                <select bind:value={limit} class="select min-w-[100px]">
                    <option value={50}>50</option>
                    <option value={100}>100</option>
                    <option value={200}>200</option>
                    <option value={500}>500</option>
                </select>
            </div>

            <div class="flex-1 min-w-[200px] flex flex-col gap-1.5">
                <span class="section-eyebrow">Search</span>
                <div class="relative">
                    <input
                        type="text"
                        placeholder="Search label or camera…"
                        bind:value={searchFilter}
                        class="input pl-9"
                    />
                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" class="absolute left-3 top-1/2 -translate-y-1/2 text-muted-foreground">
                        <circle cx="11" cy="11" r="8"/><path d="m21 21-4.3-4.3"/>
                    </svg>
                </div>
            </div>

            <div class="flex flex-col gap-1.5">
                <span class="section-eyebrow">View</span>
                <div class="flex items-center bg-surface-2 border border-border rounded-lg p-0.5 h-9">
                    <button
                        onclick={() => viewMode = 'table'}
                        class="h-7 px-3 rounded-md text-[10px] font-bold uppercase tracking-wider transition-all flex items-center gap-1.5"
                        class:bg-primary={viewMode === 'table'}
                        class:text-primary-foreground={viewMode === 'table'}
                        class:text-muted-foreground={viewMode !== 'table'}
                    >
                        <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><line x1="3" y1="6" x2="21" y2="6"/><line x1="3" y1="12" x2="21" y2="12"/><line x1="3" y1="18" x2="21" y2="18"/></svg>
                        Table
                    </button>
                    <button
                        onclick={() => viewMode = 'grid'}
                        class="h-7 px-3 rounded-md text-[10px] font-bold uppercase tracking-wider transition-all flex items-center gap-1.5"
                        class:bg-primary={viewMode === 'grid'}
                        class:text-primary-foreground={viewMode === 'grid'}
                        class:text-muted-foreground={viewMode !== 'grid'}
                    >
                        <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><rect x="3" y="3" width="7" height="7"/><rect x="14" y="3" width="7" height="7"/><rect x="14" y="14" width="7" height="7"/><rect x="3" y="14" width="7" height="7"/></svg>
                        Grid
                    </button>
                </div>
            </div>
        </div>
    </section>

    <!-- Content -->
    <div class="page-enter stagger-2">
        {#if loading}
            <div class="flex flex-col items-center justify-center min-h-[420px] gap-3 panel">
                <span class="w-8 h-8 rounded-full border-2 border-primary border-t-transparent animate-spin"></span>
                <p class="text-xs font-semibold text-muted-foreground uppercase tracking-wider">Compiling telemetry feed…</p>
            </div>
        {:else if filteredEvents.length === 0}
            <div class="flex flex-col items-center justify-center min-h-[420px] text-center border border-dashed border-border rounded-2xl p-12">
                <div class="w-14 h-14 rounded-xl bg-surface-2 border border-border flex items-center justify-center text-muted-foreground mb-4">
                    <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5">
                        <rect width="18" height="18" x="3" y="3" rx="2" ry="2"/>
                        <line x1="9" x2="15" y1="9" y2="15"/>
                        <line x1="15" x2="9" y1="9" y2="15"/>
                    </svg>
                </div>
                <h3 class="text-base font-semibold text-foreground">No event detections</h3>
                <p class="text-xs text-muted-foreground mt-1 max-w-sm">No recordings match the current filters. Verify the perception engine is running.</p>
                <button onclick={() => { selectedCameraId = 'all'; selectedLabel = 'all'; selectedSeverity = 'all'; searchFilter = ''; }} class="btn btn-primary btn-sm mt-4">
                    Reset filters
                </button>
            </div>
        {:else if viewMode === 'table'}
            <section class="panel !p-0 overflow-hidden" in:fade={{ duration: 150 }}>
                <div class="overflow-x-auto">
                    <table class="w-full text-left border-collapse">
                        <thead>
                            <tr class="bg-surface-2/50 border-b border-border text-[10px] uppercase tracking-wider font-bold text-muted-foreground">
                                <th class="px-5 py-3 w-24">Snapshot</th>
                                <th class="px-5 py-3">Camera</th>
                                <th class="px-5 py-3">AI Label</th>
                                <th class="px-5 py-3">Confidence</th>
                                <th class="px-5 py-3">Severity</th>
                                <th class="px-5 py-3">Time Sighted</th>
                                <th class="px-5 py-3 text-right">Actions</th>
                            </tr>
                        </thead>
                        <tbody class="divide-y divide-border/50">
                            {#each filteredEvents as ev (ev.id)}
                                <tr
                                    class="hover:bg-card-hover transition-colors cursor-pointer group"
                                    onclick={() => selectedSnapshotEvent = ev}
                                >
                                    <td class="px-5 py-2.5">
                                        <div class="relative w-16 aspect-video rounded-lg overflow-hidden bg-black border border-border shrink-0">
                                            {#if ev.snapshot_path && apiToken}
                                                <img
                                                    src={`/api/v1/events/${ev.id}/snapshot?token=${apiToken}`}
                                                    alt="Thumbnail"
                                                    class="w-full h-full object-cover group-hover:scale-110 transition-transform duration-500"
                                                    loading="lazy"
                                                    onerror={(e) => { (e.currentTarget as HTMLImageElement).style.display = 'none'; }}
                                                />
                                            {:else}
                                                <div class="w-full h-full flex items-center justify-center text-muted-foreground/30">
                                                    <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5">
                                                        <path d="M23 19a2 2 0 0 1-2 2H3a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h4l2-3h6l2 3h4a2 2 0 0 1 2 2z"/>
                                                    </svg>
                                                </div>
                                            {/if}
                                        </div>
                                    </td>
                                    <td class="px-5 py-2.5">
                                        <span class="text-xs font-semibold text-foreground font-mono">{ev.camera_id}</span>
                                    </td>
                                    <td class="px-5 py-2.5">
                                        <span class="tag text-[10px] uppercase tracking-wider">{ev.label}</span>
                                    </td>
                                    <td class="px-5 py-2.5">
                                        <div class="flex flex-col gap-1 max-w-[120px]">
                                            <span class="text-[10px] font-mono font-bold text-foreground">{Math.round(ev.confidence * 100)}%</span>
                                            <div class="h-1 w-24 rounded-full bg-muted overflow-hidden">
                                                <div class="h-full bg-cyan transition-all" style="width: {ev.confidence * 100}%"></div>
                                            </div>
                                        </div>
                                    </td>
                                    <td class="px-5 py-2.5">
                                        <span class="badge {ev.severity === 'info' ? 'badge-cyan' : ev.severity === 'warning' ? 'badge-gold' : 'badge-crimson'}">
                                            <span class="w-1 h-1 rounded-full {ev.severity === 'info' ? 'bg-cyan' : ev.severity === 'warning' ? 'bg-gold' : 'bg-crimson'} {ev.severity !== 'info' ? 'status-pulse' : ''}"></span>
                                            {ev.severity}
                                        </span>
                                    </td>
                                    <td class="px-5 py-2.5">
                                        <div class="flex flex-col text-xs">
                                            <span class="text-foreground font-medium">{formatDateFriendly(ev.timestamp)}</span>
                                            <span class="text-muted-foreground text-[10px] font-mono">{formatTime(ev.timestamp)} · {getRelativeTime(ev.timestamp)}</span>
                                        </div>
                                    </td>
                                    <td class="px-5 py-2.5 text-right">
                                        <!-- svelte-ignore a11y_click_events_have_key_events a11y_no_static_element_interactions -->
                                        <div class="flex items-center justify-end gap-1" onclick={(e) => e.stopPropagation()}>
                                            <a
                                                href="/review?camera_id={ev.camera_id}&timestamp={ev.timestamp}&play=true"
                                                class="btn-icon !w-8 !h-8 hover:!text-cyan hover:!border-cyan/30"
                                                title="Play footage"
                                            >
                                                <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><polygon points="5 3 19 12 5 21 5 3"/></svg>
                                            </a>
                                            {#if ev.snapshot_path}
                                                <a
                                                    href="/api/v1/events/{ev.id}/snapshot?token={apiToken}"
                                                    download="snapshot_{ev.camera_id}_{ev.id}.jpg"
                                                    class="btn-icon !w-8 !h-8"
                                                    title="Download snapshot"
                                                >
                                                    <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" x2="12" y1="15" y2="3"/></svg>
                                                </a>
                                            {/if}
                                            <button
                                                onclick={(e) => deleteEventItem(ev.id, e)}
                                                class="btn-icon !w-8 !h-8 hover:!text-crimson hover:!border-crimson/30"
                                                title="Purge event"
                                            >
                                                <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><polyline points="3 6 5 6 21 6"/><path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"/><path d="M10 11v6M14 11v6"/></svg>
                                            </button>
                                        </div>
                                    </td>
                                </tr>
                            {/each}
                        </tbody>
                    </table>
                </div>
            </section>
        {:else}
            <div class="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-3 xl:grid-cols-4 gap-4" in:fade={{ duration: 150 }}>
                {#each filteredEvents as ev (ev.id)}
                    <div
                        role="button"
                        tabindex="0"
                        onclick={() => selectedSnapshotEvent = ev}
                        onkeydown={(e) => e.key === 'Enter' && (selectedSnapshotEvent = ev)}
                        class="panel !p-0 overflow-hidden group cursor-pointer hover:!border-cyan/40 transition-all"
                    >
                        <div class="relative aspect-video bg-black overflow-hidden">
                            {#if ev.snapshot_path && apiToken}
                                <img
                                    src={`/api/v1/events/${ev.id}/snapshot?token=${apiToken}`}
                                    alt="{ev.label} event capture"
                                    class="w-full h-full object-cover group-hover:scale-105 transition-transform duration-500"
                                    loading="lazy"
                                    onerror={(e) => { (e.currentTarget as HTMLImageElement).style.display = 'none'; }}
                                />
                            {:else}
                                <div class="w-full h-full flex flex-col items-center justify-center text-muted-foreground/30 gap-1.5">
                                    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5">
                                        <path d="M23 19a2 2 0 0 1-2 2H3a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h4l2-3h6l2 3h4a2 2 0 0 1 2 2z"/><circle cx="12" cy="13" r="4"/>
                                    </svg>
                                    <span class="text-[9px] uppercase tracking-wider font-semibold">No snapshot</span>
                                </div>
                            {/if}

                            <div class="absolute top-2 left-2 flex items-center gap-1.5">
                                <span class="badge {ev.severity === 'info' ? 'badge-cyan' : ev.severity === 'warning' ? 'badge-gold' : 'badge-crimson'} backdrop-blur-sm">
                                    <span class="w-1 h-1 rounded-full {ev.severity === 'info' ? 'bg-cyan' : ev.severity === 'warning' ? 'bg-gold' : 'bg-crimson'} {ev.severity !== 'info' ? 'status-pulse' : ''}"></span>
                                    {ev.severity}
                                </span>
                            </div>

                            <div class="absolute top-2 right-2">
                                <span class="badge !bg-black/60 !border-white/10 backdrop-blur-sm font-mono">{ev.camera_id}</span>
                            </div>

                            <div class="absolute bottom-2 right-2">
                                <button
                                    onclick={(e) => deleteEventItem(ev.id, e)}
                                    class="w-7 h-7 rounded-md bg-black/60 hover:bg-crimson/30 border border-white/10 text-muted-foreground hover:text-crimson transition-all opacity-0 group-hover:opacity-100"
                                    title="Delete"
                                >
                                    <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" class="mx-auto"><polyline points="3 6 5 6 21 6"/><path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"/></svg>
                                </button>
                            </div>

                            <div class="absolute bottom-2 left-2">
                                <span class="text-[10px] font-mono px-1.5 h-5 inline-flex items-center rounded bg-black/60 backdrop-blur-sm text-foreground border border-white/10">
                                    {getRelativeTime(ev.timestamp)}
                                </span>
                            </div>
                        </div>

                        <div class="p-3.5 flex items-center justify-between gap-3">
                            <div class="min-w-0 flex-1">
                                <p class="text-sm font-semibold text-foreground uppercase tracking-tight truncate">{ev.label}</p>
                                <p class="text-[10px] text-muted-foreground font-mono mt-0.5">{formatDateFriendly(ev.timestamp)} · {formatTime(ev.timestamp)}</p>
                            </div>
                            <div class="text-right shrink-0">
                                <p class="text-base font-mono font-bold text-cyan tabular-nums">{Math.round(ev.confidence * 100)}<span class="text-xs">%</span></p>
                            </div>
                        </div>
                    </div>
                {/each}
            </div>
        {/if}
    </div>

    <!-- Snapshot modal -->
    {#if selectedSnapshotEvent}
        <div
            class="fixed inset-0 z-50 flex items-center justify-center p-4 bg-background/85 backdrop-blur-md"
            transition:fade={{ duration: 150 }}
            onclick={() => selectedSnapshotEvent = null}
            role="presentation"
        >
            <!-- svelte-ignore a11y_no_noninteractive_element_interactions -->
            <div
                class="panel !p-0 max-w-4xl w-full max-h-[90vh] overflow-hidden flex flex-col"
                in:fly={{ y: 16, duration: 220 }}
                onclick={(e) => e.stopPropagation()}
                onkeydown={(e) => { if (e.key === 'Escape') selectedSnapshotEvent = null; }}
                role="dialog"
                tabindex="-1"
            >
                <div class="px-5 h-14 border-b border-border flex items-center justify-between">
                    <div class="flex items-center gap-3 min-w-0">
                        <span class="badge {selectedSnapshotEvent.severity === 'info' ? 'badge-cyan' : selectedSnapshotEvent.severity === 'warning' ? 'badge-gold' : 'badge-crimson'}">
                            <span class="w-1 h-1 rounded-full {selectedSnapshotEvent.severity === 'info' ? 'bg-cyan' : selectedSnapshotEvent.severity === 'warning' ? 'bg-gold' : 'bg-crimson'} {selectedSnapshotEvent.severity !== 'info' ? 'status-pulse' : ''}"></span>
                            {selectedSnapshotEvent.severity}
                        </span>
                        <div class="min-w-0">
                            <p class="text-sm font-semibold text-foreground uppercase tracking-tight">{selectedSnapshotEvent.label} Sighting</p>
                            <p class="text-[10px] text-muted-foreground font-mono mt-0.5">{selectedSnapshotEvent.camera_id} · {formatDateFriendly(selectedSnapshotEvent.timestamp)} {formatTime(selectedSnapshotEvent.timestamp)}</p>
                        </div>
                    </div>
                    <button class="btn-icon" onclick={() => selectedSnapshotEvent = null} aria-label="Close snapshot viewer">
                        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
                    </button>
                </div>

                <div class="flex-1 bg-black overflow-hidden flex items-center justify-center p-4 min-h-[300px]">
                    {#if selectedSnapshotEvent.snapshot_path && apiToken}
                        <img
                            src={`/api/v1/events/${selectedSnapshotEvent.id}/snapshot?token=${apiToken}`}
                            alt="{selectedSnapshotEvent.label} capture"
                            class="max-w-full max-h-[60vh] object-contain rounded border border-border"
                        />
                    {:else}
                        <div class="flex flex-col items-center justify-center text-muted-foreground/30 gap-3 py-16">
                            <svg width="40" height="40" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5">
                                <path d="M23 19a2 2 0 0 1-2 2H3a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h4l2-3h6l2 3h4a2 2 0 0 1 2 2z"/><circle cx="12" cy="13" r="4"/>
                            </svg>
                            <span class="text-xs uppercase tracking-wider font-semibold">Snapshot unavailable</span>
                        </div>
                    {/if}
                </div>

                <div class="px-5 py-4 border-t border-border flex flex-col sm:flex-row justify-between items-center gap-3">
                    <div class="flex items-center gap-5 text-xs">
                        <div class="flex flex-col">
                            <span class="text-[10px] text-muted-foreground uppercase tracking-wider font-bold">Severity</span>
                            <span class="text-sm font-semibold text-foreground capitalize">{selectedSnapshotEvent.severity}</span>
                        </div>
                        <div class="flex flex-col">
                            <span class="text-[10px] text-muted-foreground uppercase tracking-wider font-bold">Confidence</span>
                            <span class="text-sm font-mono font-bold text-cyan">{Math.round(selectedSnapshotEvent.confidence * 100)}%</span>
                        </div>
                    </div>
                    <div class="flex items-center gap-2 w-full sm:w-auto justify-end">
                        <button onclick={(e) => { deleteEventItem(selectedSnapshotEvent!.id, e); }} class="btn btn-sm hover:!text-crimson hover:!border-crimson/30">
                            <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><polyline points="3 6 5 6 21 6"/><path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"/></svg>
                            Purge
                        </button>
                        {#if selectedSnapshotEvent.snapshot_path}
                            <a href="/api/v1/events/{selectedSnapshotEvent.id}/snapshot?token={apiToken}" download="snapshot_{selectedSnapshotEvent.camera_id}_{selectedSnapshotEvent.id}.jpg" class="btn btn-sm">
                                <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" x2="12" y1="15" y2="3"/></svg>
                                Download
                            </a>
                        {/if}
                        <a href="/review?camera_id={selectedSnapshotEvent.camera_id}&timestamp={selectedSnapshotEvent.timestamp}&play=true" onclick={() => selectedSnapshotEvent = null} class="btn btn-primary btn-sm">
                            <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3"><polygon points="5 3 19 12 5 21 5 3"/></svg>
                            Play recording
                        </a>
                    </div>
                </div>
            </div>
        </div>
    {/if}
</div>
