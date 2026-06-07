<script lang="ts">
    import { onMount, onDestroy } from 'svelte';
    import { getApiToken, buildWsUrl } from '$lib/apiToken';
    import { fly, fade } from 'svelte/transition';
    import { browser } from '$app/environment';

    interface Identity {
        name: string;
        image: string | null;
        lastSeen: string;
    }

    let profiles = $state<Identity[]>([]);
    let searchQuery = $state('');
    let activeIdentityTab = $state<'authorized' | 'watchlist' | 'unknown' | 'archived'>('unknown');
    let watchlistNames = $state<string[]>([]);
    let archivedNames = $state<string[]>([]);

    let activeRenameProfile = $state<Identity | null>(null);
    let newProfileName = $state('');
    let isSavingProfile = $state(false);

    let filteredProfiles = $derived(
        profiles
            .filter(p => p.name.toLowerCase().includes(searchQuery.toLowerCase()))
            .filter(p => {
                const name = p.name;
                const isArchived = archivedNames.includes(name);
                const isWatchlist = watchlistNames.includes(name);
                const isStranger = name.startsWith('stranger_');
                if (activeIdentityTab === 'archived') return isArchived;
                if (activeIdentityTab === 'watchlist') return isWatchlist && !isArchived;
                if (activeIdentityTab === 'authorized') return !isStranger && !isWatchlist && !isArchived;
                return isStranger && !isWatchlist && !isArchived;
            })
    );

    let stats = $derived.by(() => {
        let authorized = 0, watchlist = 0, unknown = 0, archived = 0;
        profiles.forEach(p => {
            const isArchived = archivedNames.includes(p.name);
            const isWatchlist = watchlistNames.includes(p.name);
            const isStranger = p.name.startsWith('stranger_');
            if (isArchived) archived++;
            else if (isWatchlist) watchlist++;
            else if (isStranger) unknown++;
            else authorized++;
        });
        return { authorized, watchlist, unknown, archived, total: profiles.length };
    });

    let ws: WebSocket | null = null;
    let wsStatus = $state('disconnected');
    let apiToken = $state('');

    onMount(async () => {
        const token = await getApiToken();
        if (token) apiToken = token;
        await fetchIdentities();
        connectWS();

        try {
            const storedWatchlist = localStorage.getItem('dc_watchlist');
            if (storedWatchlist) watchlistNames = JSON.parse(storedWatchlist);
        } catch (e) {
            console.error('Failed to parse dc_watchlist from localStorage:', e);
        }
        try {
            const storedArchived = localStorage.getItem('dc_archived');
            if (storedArchived) archivedNames = JSON.parse(storedArchived);
        } catch (e) {
            console.error('Failed to parse dc_archived from localStorage:', e);
        }
    });

    async function fetchIdentities() {
        try {
            const res = await fetch('/api/v1/identities');
            profiles = res.ok ? await res.json() : [];
        } catch (err) {
            console.error('[Identities page] Fetch failed:', err);
        }
    }

    let fetchTimeout: ReturnType<typeof setTimeout> | null = null;
    let wsReconnectTimer: ReturnType<typeof setTimeout> | null = null;

    onDestroy(() => {
        if (fetchTimeout) clearTimeout(fetchTimeout);
        if (wsReconnectTimer) clearTimeout(wsReconnectTimer);
        if (ws) {
            ws.onclose = null;
            ws.close();
        }
    });

    function fetchIdentitiesThrottled() {
        if (fetchTimeout) return;
        fetchIdentities();
        fetchTimeout = setTimeout(() => {
            fetchTimeout = null;
        }, 3000);
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
                if (wsReconnectTimer) clearTimeout(wsReconnectTimer);
                wsReconnectTimer = setTimeout(connectWS, 3000);
                return;
            }

            ws.onopen = () => {
                wsStatus = 'connected';
            };

            ws.onclose = () => {
                wsStatus = 'disconnected';
                if (wsReconnectTimer) clearTimeout(wsReconnectTimer);
                wsReconnectTimer = setTimeout(connectWS, 3000);
            };

            ws.onmessage = (event) => {
                try {
                    const data = JSON.parse(event.data);
                    if (data.event === 'detections') {
                        fetchIdentitiesThrottled();
                    }
                } catch (_) {}
            };
        })();
    }

    function saveWatchlist() { localStorage.setItem('dc_watchlist', JSON.stringify(watchlistNames)); }
    function saveArchived() { localStorage.setItem('dc_archived', JSON.stringify(archivedNames)); }

    function toggleWatchlist(name: string) {
        if (watchlistNames.includes(name)) {
            watchlistNames = watchlistNames.filter(n => n !== name);
        } else {
            watchlistNames = [...watchlistNames, name];
            archivedNames = archivedNames.filter(n => n !== name);
            saveArchived();
        }
        saveWatchlist();
    }

    function toggleArchive(name: string) {
        if (archivedNames.includes(name)) {
            archivedNames = archivedNames.filter(n => n !== name);
        } else {
            archivedNames = [...archivedNames, name];
            watchlistNames = watchlistNames.filter(n => n !== name);
            saveWatchlist();
        }
        saveArchived();
    }

    function openRenameModal(profile: Identity) {
        activeRenameProfile = profile;
        newProfileName = profile.name.startsWith('stranger_') ? '' : profile.name;
    }

    async function handleRename() {
        if (!activeRenameProfile) return;
        const nameToSave = newProfileName.trim();
        if (!nameToSave || nameToSave === activeRenameProfile.name) return;

        isSavingProfile = true;
        try {
            const res = await fetch('/api/v1/identities', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ oldName: activeRenameProfile.name, newName: nameToSave })
            });
            if (res.ok) {
                const oldName = activeRenameProfile.name;
                if (watchlistNames.includes(oldName)) {
                    watchlistNames = watchlistNames.filter(n => n !== oldName).concat(nameToSave);
                    saveWatchlist();
                }
                if (archivedNames.includes(oldName)) {
                    archivedNames = archivedNames.filter(n => n !== oldName).concat(nameToSave);
                    saveArchived();
                }
                activeRenameProfile = null;
                await fetchIdentities();
            }
        } finally {
            isSavingProfile = false;
        }
    }

    function formatLastSeen(isoString: string) {
        if (!isoString) return 'Never';
        try {
            const date = new Date(isoString);
            const now = new Date();
            if (isNaN(date.getTime())) return 'Recently';

            const diffMs = now.getTime() - date.getTime();
            const diffMin = Math.floor(diffMs / 60000);

            if (diffMin < 1) return 'Just now';
            if (diffMin < 60) return `${diffMin}m ago`;

            const diffHr = Math.floor(diffMin / 60);
            if (diffHr < 24) return `${diffHr}h ago`;

            const diffDay = Math.floor(diffHr / 24);
            if (diffDay === 1) return 'Yesterday';
            if (diffDay < 7) return `${diffDay}d ago`;

            return date.toLocaleDateString(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
        } catch (_) {
            return 'Recently';
        }
    }

    function getInitials(name: string): string {
        const clean = name.replace(/^stranger_/, '');
        if (clean.includes(' ')) {
            return clean.split(' ').slice(0, 2).map(p => p[0]).join('').toUpperCase();
        }
        return clean.substring(0, 2).toUpperCase();
    }

    function getRelativeBadge(isoString: string): { label: string; tone: 'jade' | 'gold' | 'muted' } {
        if (!isoString) return { label: 'Never', tone: 'muted' };
        const date = new Date(isoString);
        const diffMin = Math.floor((Date.now() - date.getTime()) / 60000);
        if (diffMin < 60) return { label: formatLastSeen(isoString), tone: 'jade' };
        if (diffMin < 60 * 24) return { label: formatLastSeen(isoString), tone: 'gold' };
        return { label: formatLastSeen(isoString), tone: 'muted' };
    }
</script>

<svelte:head>
    <title>Hawkeye — Biometric Database</title>
</svelte:head>

<div class="flex flex-col gap-6 w-full pb-12 page-enter">

    <!-- Header -->
    <header class="flex flex-col lg:flex-row lg:items-end justify-between gap-4">
        <div>
            <div class="flex items-center gap-2.5 mb-2">
                <span class="badge {wsStatus === 'connected' ? 'badge-jade' : 'badge-muted'}">
                    <span class="w-1.5 h-1.5 rounded-full {wsStatus === 'connected' ? 'bg-jade status-pulse' : 'bg-muted-foreground'}"></span>
                    {wsStatus === 'connected' ? 'Live Re-ID' : 'Reconnecting'}
                </span>
                <span class="text-[11px] text-muted-foreground font-mono">{stats.total} profiles in registry</span>
            </div>
            <h1 class="text-2xl md:text-3xl font-display font-bold text-foreground tracking-tight leading-none">Biometric Database</h1>
            <p class="text-sm text-muted-foreground mt-2">Manage strangers, authorized users, and watchlists detected by the active perception network.</p>
        </div>

        <div class="flex items-center gap-2 flex-wrap">
            <div class="flex items-center gap-1.5 px-3 h-9 rounded-lg border border-jade/20 bg-jade/5">
                <span class="w-1.5 h-1.5 rounded-full bg-jade"></span>
                <span class="text-[10px] font-mono font-bold text-foreground tabular-nums">{stats.authorized}</span>
                <span class="text-[10px] text-muted-foreground uppercase tracking-wider">authorized</span>
            </div>
            <div class="flex items-center gap-1.5 px-3 h-9 rounded-lg border border-ember/20 bg-ember/5">
                <span class="w-1.5 h-1.5 rounded-full bg-ember status-pulse"></span>
                <span class="text-[10px] font-mono font-bold text-foreground tabular-nums">{stats.watchlist}</span>
                <span class="text-[10px] text-muted-foreground uppercase tracking-wider">watchlist</span>
            </div>
            <div class="flex items-center gap-1.5 px-3 h-9 rounded-lg border border-iris/20 bg-iris/5">
                <span class="w-1.5 h-1.5 rounded-full bg-iris"></span>
                <span class="text-[10px] font-mono font-bold text-foreground tabular-nums">{stats.unknown}</span>
                <span class="text-[10px] text-muted-foreground uppercase tracking-wider">unknown</span>
            </div>
            <div class="flex items-center gap-1.5 px-3 h-9 rounded-lg border border-border bg-card">
                <span class="w-1.5 h-1.5 rounded-full bg-muted-foreground"></span>
                <span class="text-[10px] font-mono font-bold text-foreground tabular-nums">{stats.archived}</span>
                <span class="text-[10px] text-muted-foreground uppercase tracking-wider">archived</span>
            </div>
            <button onclick={fetchIdentities} class="btn-icon" title="Refresh">
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                    <path d="M3 12a9 9 0 0 1 9-9 9.75 9.75 0 0 1 6.74 2.74L21 8"/>
                    <path d="M21 3v5h-5"/>
                    <path d="M21 12a9 9 0 0 1-9 9 9.75 9.75 0 0 1-6.74-2.74L3 16"/>
                    <path d="M3 21v-5h5"/>
                </svg>
            </button>
        </div>
    </header>

    <!-- Main panel with tabs -->
    <section class="panel !p-0">

        <!-- Tab bar -->
        <div class="flex items-center justify-between gap-3 px-5 py-4 border-b border-border flex-wrap">
            <div class="flex items-center bg-surface-2 border border-border rounded-lg p-0.5 h-9 gap-0.5">
                <button
                    onclick={() => activeIdentityTab = 'unknown'}
                    class="h-7 px-3.5 rounded-md text-[10px] font-bold uppercase tracking-wider transition-all flex items-center gap-1.5 shrink-0"
                    class:bg-iris={activeIdentityTab === 'unknown'}
                    class:text-iris-foreground={activeIdentityTab === 'unknown'}
                    class:text-muted-foreground={activeIdentityTab !== 'unknown'}
                >
                    <span class="w-1.5 h-1.5 rounded-full shrink-0"
                        class:bg-iris-foreground={activeIdentityTab === 'unknown'}
                        class:bg-iris={activeIdentityTab !== 'unknown'}></span>
                    Strangers
                    <span class="font-mono opacity-60">·</span>
                    <span class="font-mono tabular-nums">{stats.unknown}</span>
                </button>
                <button
                    onclick={() => activeIdentityTab = 'authorized'}
                    class="h-7 px-3.5 rounded-md text-[10px] font-bold uppercase tracking-wider transition-all flex items-center gap-1.5 shrink-0"
                    class:bg-jade={activeIdentityTab === 'authorized'}
                    class:text-jade-foreground={activeIdentityTab === 'authorized'}
                    class:text-muted-foreground={activeIdentityTab !== 'authorized'}
                >
                    <span class="w-1.5 h-1.5 rounded-full shrink-0"
                        class:bg-jade-foreground={activeIdentityTab === 'authorized'}
                        class:bg-jade={activeIdentityTab !== 'authorized'}></span>
                    Authorized
                    <span class="font-mono opacity-60">·</span>
                    <span class="font-mono tabular-nums">{stats.authorized}</span>
                </button>
                <button
                    onclick={() => activeIdentityTab = 'watchlist'}
                    class="h-7 px-3.5 rounded-md text-[10px] font-bold uppercase tracking-wider transition-all flex items-center gap-1.5 shrink-0"
                    class:bg-ember={activeIdentityTab === 'watchlist'}
                    class:text-ember-foreground={activeIdentityTab === 'watchlist'}
                    class:text-muted-foreground={activeIdentityTab !== 'watchlist'}
                >
                    <span class="w-1.5 h-1.5 rounded-full shrink-0"
                        class:bg-ember-foreground={activeIdentityTab === 'watchlist'}
                        class:bg-ember={activeIdentityTab !== 'watchlist'}></span>
                    Watchlist
                    <span class="font-mono opacity-60">·</span>
                    <span class="font-mono tabular-nums">{stats.watchlist}</span>
                </button>
                <button
                    onclick={() => activeIdentityTab = 'archived'}
                    class="h-7 px-3.5 rounded-md text-[10px] font-bold uppercase tracking-wider transition-all flex items-center gap-1.5 shrink-0"
                    class:bg-muted-foreground={activeIdentityTab === 'archived'}
                    class:text-background={activeIdentityTab === 'archived'}
                    class:text-muted-foreground={activeIdentityTab !== 'archived'}
                >
                    <span class="w-1.5 h-1.5 rounded-full bg-muted-foreground shrink-0"></span>
                    Archived
                    <span class="font-mono opacity-60">·</span>
                    <span class="font-mono tabular-nums">{stats.archived}</span>
                </button>
            </div>

            <div class="relative w-full sm:w-72">
                <label for="profile-search" class="sr-only">Search profiles</label>
                <input
                    id="profile-search"
                    type="text"
                    bind:value={searchQuery}
                    placeholder="Search profiles…"
                    class="input pl-9"
                />
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" class="absolute left-3 top-1/2 -translate-y-1/2 text-muted-foreground pointer-events-none">
                    <circle cx="11" cy="11" r="8"/><path d="m21 21-4.3-4.3"/>
                </svg>
            </div>
        </div>

        <!-- Profile grid -->
        <div class="p-5 min-h-[400px]">
            {#if filteredProfiles.length === 0}
                <div class="flex flex-col items-center justify-center text-center p-12 border border-dashed border-border rounded-xl text-muted-foreground min-h-[320px]">
                    <div class="w-14 h-14 rounded-xl bg-surface-2 border border-border flex items-center justify-center mb-4">
                        <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" class="text-muted-foreground/60">
                            <path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2"/>
                            <circle cx="9" cy="7" r="4"/>
                            <path d="M22 21v-2a4 4 0 0 0-3-3.87"/>
                            <path d="M16 3.13a4 4 0 0 1 0 7.75"/>
                        </svg>
                    </div>
                    <h3 class="text-sm font-display font-semibold text-foreground">No matching profiles</h3>
                    <p class="text-xs text-muted-foreground mt-1.5 max-w-sm leading-relaxed">
                        {activeIdentityTab === 'unknown' ? 'No stranger profiles detected yet. The perception engine will populate this list.' : `No profiles in the ${activeIdentityTab} category.`}
                    </p>
                </div>
            {:else}
                <div class="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-3 lg:grid-cols-4 xl:grid-cols-5 gap-3.5">
                    {#each filteredProfiles as p (p.name)}
                        {@const isWatchlist = watchlistNames.includes(p.name)}
                        {@const isArchived = archivedNames.includes(p.name)}
                        {@const isStranger = p.name.startsWith('stranger_')}
                        {@const tone = isArchived ? 'muted' : isWatchlist ? 'ember' : isStranger ? 'iris' : 'jade'}
                        {@const seen = getRelativeBadge(p.lastSeen)}
                        <div class="panel !p-0 overflow-hidden transition-all group"
                            class:hover:border-iris={tone === 'iris' && !isArchived}
                            class:hover:border-jade={tone === 'jade' && !isArchived}
                            class:hover:border-ember={tone === 'ember' && !isArchived}
                            class:hover:border-border-strong={tone === 'muted'}
                            transition:fly={{ y: 12, duration: 180 }}>

                            <!-- Header with accent stripe -->
                            <div class="h-1 w-full"
                                class:bg-iris={tone === 'iris' && !isArchived}
                                class:bg-jade={tone === 'jade' && !isArchived}
                                class:bg-ember={tone === 'ember' && !isArchived}
                                class:bg-muted-foreground={isArchived}></div>

                            <div class="p-4 flex flex-col gap-3">
                                <!-- Avatar + Identity -->
                                <div class="flex items-center gap-3 min-w-0">
                                    <div class="relative shrink-0">
                                        {#if p.image}
                                            <img src="{p.image}&token={apiToken}" alt={p.name} class="w-12 h-12 rounded-xl border border-border object-cover" />
                                        {:else}
                                            <div class="w-12 h-12 rounded-xl border flex items-center justify-center font-display font-bold text-sm
                                                {tone === 'iris' ? 'bg-iris/10 border-iris/20 text-iris' : ''}
                                                {tone === 'jade' ? 'bg-jade/10 border-jade/20 text-jade' : ''}
                                                {tone === 'ember' ? 'bg-ember/10 border-ember/20 text-ember' : ''}
                                                {tone === 'muted' ? 'bg-muted border-border text-muted-foreground' : ''}">
                                                {getInitials(p.name)}
                                            </div>
                                        {/if}
                                        {#if isWatchlist && !isArchived}
                                            <span class="absolute -top-1 -right-1 w-3.5 h-3.5 rounded-full bg-ember border-2 border-card flex items-center justify-center">
                                                <span class="w-1 h-1 rounded-full bg-ember-foreground"></span>
                                            </span>
                                        {/if}
                                    </div>

                                    <div class="min-w-0 flex-1 flex flex-col gap-1.5">
                                        <strong class="text-sm font-semibold text-foreground block truncate font-display leading-tight" title={p.name}>
                                            {p.name.replace(/^stranger_/, '')}
                                        </strong>
                                        <span class="badge !h-5 !text-[9px] self-start"
                                            class:bg-iris={tone === 'iris' && !isArchived}
                                            class:text-iris={tone === 'iris' && !isArchived}
                                            class:border-iris={tone === 'iris' && !isArchived}
                                            class:bg-jade={tone === 'jade' && !isArchived}
                                            class:text-jade={tone === 'jade' && !isArchived}
                                            class:border-jade={tone === 'jade' && !isArchived}
                                            class:bg-ember={tone === 'ember' && !isArchived}
                                            class:text-ember={tone === 'ember' && !isArchived}
                                            class:border-ember={tone === 'ember' && !isArchived}
                                            class:bg-muted={isArchived}
                                            class:text-muted-foreground={isArchived}
                                            class:border-border={isArchived}>
                                            {isArchived ? 'archived' : isWatchlist ? 'watchlist' : isStranger ? 'stranger' : 'authorized'}
                                        </span>
                                    </div>
                                </div>

                                <!-- Last seen -->
                                <div class="flex items-center justify-between text-[10px] font-mono pt-2.5 border-t border-border/50">
                                    <span class="text-muted-foreground uppercase tracking-wider">Last seen</span>
                                    <span class="text-foreground font-semibold flex items-center gap-1.5">
                                        <span class="w-1 h-1 rounded-full shrink-0"
                                            class:bg-jade={seen.tone === 'jade'}
                                            class:status-pulse={seen.tone === 'jade'}
                                            class:bg-gold={seen.tone === 'gold'}
                                            class:bg-muted-foreground={seen.tone === 'muted'}></span>
                                        <span class="tabular-nums">{seen.label}</span>
                                    </span>
                                </div>

                                <!-- Actions -->
                                <div class="grid grid-cols-3 gap-2">
                                    <button
                                        onclick={() => openRenameModal(p)}
                                        class="h-7 px-2 text-[10px] font-bold uppercase tracking-wider rounded-md border border-border bg-surface-2 hover:bg-surface-3 hover:border-border-strong text-foreground transition-all flex items-center justify-center gap-1 truncate"
                                    >
                                        <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" class="shrink-0">
                                            <path d="M12 20h9"/>
                                            <path d="M16.5 3.5a2.121 2.121 0 0 1 3 3L7 19l-4 1 1-4Z"/>
                                        </svg>
                                        <span class="truncate">Rename</span>
                                    </button>
                                    <button
                                        onclick={() => toggleWatchlist(p.name)}
                                        class="h-7 px-2 text-[10px] font-bold uppercase tracking-wider rounded-md border transition-all flex items-center justify-center gap-1 truncate
                                        {isWatchlist
                                            ? 'bg-ember text-ember-foreground border-ember'
                                            : 'border-border bg-surface-2 hover:bg-surface-3 text-foreground hover:border-border-strong'}"
                                    >
                                        <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" class="shrink-0">
                                            <path d="M2 12s3-7 10-7 10 7 10 7-3 7-10 7-10-7-10-7Z"/>
                                            <circle cx="12" cy="12" r="3"/>
                                        </svg>
                                        <span class="truncate">{isWatchlist ? 'Listed' : 'Watch'}</span>
                                    </button>
                                    <button
                                        onclick={() => toggleArchive(p.name)}
                                        class="h-7 px-2 text-[10px] font-bold uppercase tracking-wider rounded-md border transition-all flex items-center justify-center gap-1 truncate
                                        {isArchived
                                            ? 'bg-muted-foreground text-background border-muted-foreground'
                                            : 'border-border bg-surface-2 hover:bg-surface-3 text-foreground hover:border-border-strong'}"
                                        title={isArchived ? 'Restore' : 'Archive'}
                                    >
                                        {#if isArchived}
                                            <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" class="shrink-0">
                                                <path d="M3 12a9 9 0 1 0 9-9 9.75 9.75 0 0 0-6.74 2.74L3 8"/>
                                                <path d="M3 3v5h5"/>
                                            </svg>
                                            <span class="truncate">Restore</span>
                                        {:else}
                                            <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" class="shrink-0">
                                                <rect x="2" y="4" width="20" height="5" rx="1"/>
                                                <path d="M4 9v9a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9"/>
                                                <path d="M10 13h4"/>
                                            </svg>
                                            <span class="truncate">Archive</span>
                                        {/if}
                                    </button>
                                </div>
                            </div>
                        </div>
                    {/each}
                </div>
            {/if}
        </div>
    </section>
</div>

<!-- Rename modal -->
{#if activeRenameProfile}
    <div class="fixed inset-0 z-[100] flex items-center justify-center p-4 bg-black/60 backdrop-blur-sm"
        transition:fade={{ duration: 150 }}>
        <div class="panel w-full max-w-md"
            transition:fly={{ y: 20, duration: 200 }}>
            <div class="panel-header">
                <div>
                    <h3 class="text-sm font-display font-semibold text-foreground">Identity Verification</h3>
                    <p class="text-[11px] text-muted-foreground mt-0.5">Associate stranger profile with a known verified name.</p>
                </div>
                <button onclick={() => activeRenameProfile = null} class="btn-icon !w-8 !h-8" aria-label="Close identity verification">
                    <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">
                        <line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/>
                    </svg>
                </button>
            </div>

            <div class="panel-body flex flex-col gap-4">
                <div class="flex items-center gap-3 p-3.5 rounded-lg border border-border bg-surface-2/50">
                    {#if activeRenameProfile.image}
                        <img src="{activeRenameProfile.image}&token={apiToken}" alt={activeRenameProfile.name} class="w-12 h-12 rounded-xl border border-border object-cover shrink-0" />
                    {:else}
                        <div class="w-12 h-12 rounded-xl bg-iris/10 border border-iris/20 text-iris flex items-center justify-center font-display font-bold text-sm shrink-0">
                            {getInitials(activeRenameProfile.name)}
                        </div>
                    {/if}
                    <div class="min-w-0 flex-1">
                        <span class="text-[10px] text-muted-foreground uppercase tracking-wider font-bold block">Current ID</span>
                        <strong class="text-sm font-mono text-foreground block truncate mt-0.5">{activeRenameProfile.name}</strong>
                    </div>
                </div>

                <div class="flex flex-col gap-1.5">
                    <label for="rename-profile-input" class="section-eyebrow">New Profile Name</label>
                    <input
                        type="text"
                        id="rename-profile-input"
                        bind:value={newProfileName}
                        placeholder="e.g. John Doe"
                        class="input"
                        onkeydown={(e) => {
                            if (e.key === 'Enter') handleRename();
                            if (e.key === 'Escape') activeRenameProfile = null;
                        }}
                    />
                </div>
            </div>

            <div class="flex justify-end gap-2 px-5 py-4 border-t border-border">
                <button
                    onclick={() => activeRenameProfile = null}
                    class="btn btn-ghost btn-sm"
                >
                    Cancel
                </button>
                <button
                    disabled={isSavingProfile}
                    onclick={handleRename}
                    class="btn btn-primary btn-sm"
                >
                    {#if isSavingProfile}
                        <span class="w-3 h-3 rounded-full border-2 border-current border-t-transparent animate-spin"></span>
                        Saving…
                    {:else}
                        <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">
                            <path d="M20 6 9 17l-5-5"/>
                        </svg>
                        Rename Profile
                    {/if}
                </button>
            </div>
        </div>
    </div>
{/if}
