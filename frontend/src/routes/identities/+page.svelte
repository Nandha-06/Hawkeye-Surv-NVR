<script lang="ts">
    import { onMount, onDestroy } from 'svelte';
    import { getApiToken, buildWsUrl } from '$lib/apiToken';
    import { fly } from 'svelte/transition';
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
                return isStranger && !isWatchlist && !isArchived; // 'unknown' strangers
            })
    );

    let ws: WebSocket | null = null;
    let wsStatus = $state('disconnected');
    let apiToken = $state('');

    onMount(async () => {
        const token = await getApiToken();
        if (token) apiToken = token;
        await fetchIdentities();
        connectWS();

        // Load identity categories
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
        }, 3000); // Throttle to at most once per 3 seconds
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
</script>

<svelte:head>
    <title>Hawkeye Face Re-ID Database</title>
</svelte:head>

<div class="flex flex-col gap-6 w-full max-w-[1400px] mx-auto min-h-full">
    <!-- Header row -->
    <header class="flex flex-col md:flex-row justify-between items-start md:items-center gap-4 bg-card border border-border/80 rounded-2xl p-5 shadow-sm shrink-0">
        <div>
            <h1 class="text-xl font-bold tracking-tight text-foreground font-display">Biometric Database</h1>
            <p class="text-xs text-muted-foreground mt-0.5 font-sans">Manage strangers, authorized friendly users, and watchlists detected by active camera networks.</p>
        </div>
        <div class="flex items-center gap-2">
            <div class="flex items-center gap-1.5 px-3 py-1 rounded-full text-xs font-semibold border transition-all duration-200 {wsStatus === 'connected' ? 'bg-emerald-500/10 border-emerald-500/20 text-emerald-400' : 'bg-muted border-border text-muted-foreground'}">
                <span class="w-1.5 h-1.5 rounded-full {wsStatus === 'connected' ? 'bg-emerald-400 animate-pulse' : 'bg-muted-foreground'}"></span>
                <span>{wsStatus === 'connected' ? 'Connected to core' : 'Connecting...'}</span>
            </div>
            <button 
                onclick={fetchIdentities}
                class="px-3 py-1.5 text-xs font-semibold rounded-lg border border-border bg-background hover:bg-accent text-foreground transition-colors cursor-pointer"
            >
                Refresh
            </button>
        </div>
    </header>

    <!-- Main Container -->
    <section class="bg-card border border-border rounded-xl p-5 shadow-sm flex flex-col gap-5 w-full">
        <!-- Controls & Search -->
        <div class="flex flex-col md:flex-row justify-between items-stretch md:items-center gap-4 border-b border-border pb-4 shrink-0">
            <!-- Categories Tabs -->
            <div class="flex bg-muted/60 border border-border p-1 rounded-xl text-xs font-sans self-start">
                <button 
                    class="py-1.5 px-4 rounded-lg font-semibold transition-all duration-200 cursor-pointer {activeIdentityTab === 'unknown' ? 'bg-background text-foreground shadow-sm font-bold' : 'text-muted-foreground hover:text-foreground'}" 
                    onclick={() => activeIdentityTab = 'unknown'}
                >
                    Strangers
                </button>
                <button 
                    class="py-1.5 px-4 rounded-lg font-semibold transition-all duration-200 cursor-pointer {activeIdentityTab === 'authorized' ? 'bg-background text-foreground shadow-sm font-bold' : 'text-muted-foreground hover:text-foreground'}" 
                    onclick={() => activeIdentityTab = 'authorized'}
                >
                    Authorized
                </button>
                <button 
                    class="py-1.5 px-4 rounded-lg font-semibold transition-all duration-200 cursor-pointer {activeIdentityTab === 'watchlist' ? 'bg-background text-foreground shadow-sm font-bold' : 'text-muted-foreground hover:text-foreground'}" 
                    onclick={() => activeIdentityTab = 'watchlist'}
                >
                    Watchlist
                </button>
                <button 
                    class="py-1.5 px-4 rounded-lg font-semibold transition-all duration-200 cursor-pointer {activeIdentityTab === 'archived' ? 'bg-background text-foreground shadow-sm font-bold' : 'text-muted-foreground hover:text-foreground'}" 
                    onclick={() => activeIdentityTab = 'archived'}
                >
                    Archived
                </button>
            </div>

            <!-- Search input -->
            <div class="relative w-full md:w-72">
                <input 
                    type="text" 
                    bind:value={searchQuery} 
                    placeholder="Search profiles by name..." 
                    class="w-full bg-background border border-border rounded-xl px-3 py-1.5 text-xs outline-none focus:border-indigo-500/80 transition-all font-sans text-foreground"
                />
            </div>
        </div>

        <!-- Inventory Grid -->
        <div class="flex-1 min-h-[400px]">
            {#if filteredProfiles.length === 0}
                <div class="flex flex-col items-center justify-center text-center p-12 border border-dashed border-border rounded-xl text-muted-foreground/35 min-h-[300px]">
                    <svg width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" class="mb-3">
                        <path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2"/>
                        <circle cx="9" cy="7" r="4"/>
                        <path d="M22 21v-2a4 4 0 0 0-3-3.87"/>
                        <path d="M16 3.13a4 4 0 0 1 0 7.75"/>
                    </svg>
                    <span class="text-xs font-semibold">No visitor identity profiles matching filter criteria.</span>
                </div>
            {:else}
                <div class="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-3 lg:grid-cols-4 gap-4">
                    {#each filteredProfiles as p (p.name)}
                        {@const isWatchlist = watchlistNames.includes(p.name)}
                        {@const isArchived = archivedNames.includes(p.name)}
                        
                        <div class="bg-muted/5 border border-border/80 rounded-xl p-4 flex flex-col gap-3 shadow-sm hover:border-muted-foreground/30 transition-all duration-200">
                            <div class="flex items-center gap-3">
                                <!-- Profile Snapshot -->
                                {#if p.image}
                                    <img src="{p.image}&token={apiToken}" alt={p.name} class="w-10 h-10 rounded-full border border-border object-cover shrink-0" />
                                {:else}
                                    <div class="w-10 h-10 rounded-full bg-indigo-500/10 border border-indigo-500/25 text-indigo-400 flex items-center justify-center font-black uppercase text-xs shrink-0">
                                        {p.name.substring(0, 2)}
                                    </div>
                                {/if}

                                <div class="min-w-0">
                                    <strong class="text-xs font-bold text-foreground block truncate">{p.name}</strong>
                                    <span class="text-[9px] text-muted-foreground block mt-0.5 font-mono">Seen {formatLastSeen(p.lastSeen)}</span>
                                </div>
                            </div>

                            <!-- Actions Toolbar -->
                            <div class="flex gap-1.5 border-t border-border pt-2.5 mt-auto">
                                <button 
                                    class="px-2 py-1 text-[10px] flex-1 font-semibold rounded border border-border hover:bg-accent text-foreground transition-colors cursor-pointer"
                                    onclick={() => openRenameModal(p)}
                                >
                                    Rename
                                </button>
                                <button 
                                    class="px-2 py-1 text-[10px] flex-1 font-semibold rounded border transition-colors cursor-pointer {isWatchlist ? 'bg-amber-600 border-amber-600 text-white hover:bg-amber-500' : 'border-border hover:bg-accent text-foreground'}"
                                    onclick={() => toggleWatchlist(p.name)}
                                >
                                    {isWatchlist ? 'Watchlisted' : 'Watchlist'}
                                </button>
                                <button 
                                    class="px-2.5 py-1 text-[10px] font-bold rounded border transition-colors cursor-pointer {isArchived ? 'bg-indigo-600 border-indigo-600 text-white hover:bg-indigo-500' : 'border-border hover:bg-accent text-foreground'}"
                                    onclick={() => toggleArchive(p.name)}
                                    title={isArchived ? 'Restore' : 'Archive'}
                                >
                                    {isArchived ? 'Restore' : '×'}
                                </button>
                            </div>
                        </div>
                    {/each}
                </div>
            {/if}
        </div>
    </section>

    <!-- Modal renaming dialog -->
    {#if activeRenameProfile}
        <div class="fixed inset-0 bg-black/60 backdrop-blur-sm flex items-center justify-center z-[100] p-4" transition:fly={{ y: 20, duration: 150 }}>
            <div class="bg-card border border-border rounded-xl shadow-2xl p-6 w-full max-w-sm flex flex-col gap-4">
                <div class="border-b border-border pb-2.5">
                    <h4 class="text-sm font-bold text-foreground tracking-tight">Identity Profile Verification</h4>
                    <p class="text-[10px] text-muted-foreground mt-0.5">Associate stranger profile <strong>"{activeRenameProfile.name}"</strong> with a known verified name.</p>
                </div>

                <div class="flex flex-col gap-1.5">
                    <label class="text-xs font-semibold text-muted-foreground" for="rename-profile-input">New Profile Name</label>
                    <input 
                        type="text" 
                        id="rename-profile-input" 
                        bind:value={newProfileName} 
                        placeholder="e.g. John Doe"
                        class="bg-background border border-border rounded-xl px-3 py-1.5 text-xs outline-none focus:border-indigo-500/80 transition-all font-sans text-foreground w-full"
                        autofocus
                        onkeydown={(e) => {
                            if (e.key === 'Enter') handleRename();
                            if (e.key === 'Escape') activeRenameProfile = null;
                        }}
                    />
                </div>

                <div class="flex justify-end gap-2 border-t border-border pt-3">
                    <button 
                        class="px-3 py-1.5 text-xs font-semibold rounded-lg border border-border bg-background hover:bg-accent text-foreground transition-colors cursor-pointer"
                        onclick={() => activeRenameProfile = null}
                    >
                        Cancel
                    </button>
                    <button 
                        class="px-4 py-1.5 text-xs font-bold rounded-lg bg-primary text-primary-foreground hover:bg-primary/90 transition-colors disabled:opacity-50 cursor-pointer"
                        disabled={isSavingProfile}
                        onclick={handleRename}
                    >
                        {isSavingProfile ? 'Saving...' : 'Rename Profile'}
                    </button>
                </div>
            </div>
        </div>
    {/if}
</div>
