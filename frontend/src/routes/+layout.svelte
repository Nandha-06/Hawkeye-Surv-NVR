<script lang="ts">
    import '../app.css';
    import { onMount } from 'svelte';
    import { page } from '$app/stores';
    import { devMode } from '$lib/devMode.svelte';
    import { getApiToken, buildWsUrl } from '$lib/apiToken';
    import favicon from '$lib/assets/favicon.svg';
    import AgentChat from '$lib/components/AgentChat.svelte';
    import AppSidebar from "$lib/components/AppSidebar.svelte";
    import { chatsState } from '$lib/chatsState.svelte';
    import { fly, fade } from 'svelte/transition';

    let { children } = $props();

    let wsStatus = $state<'connected' | 'connecting' | 'disconnected'>('disconnected');
    let profileOpen = $state(false);
    let commandPaletteOpen = $state(false);
    let chatOpen = $state(false);
    let chatWs: WebSocket | null = $state(null);
    let chatReconnectTimer: ReturnType<typeof setTimeout> | null = null;

    function connectChatWS() {
        if (typeof window === 'undefined') return;
        void (async () => {
            const token = await getApiToken();
            const wsUrl = buildWsUrl('/api/ws', token);
            try {
                chatWs = new WebSocket(wsUrl);
            } catch (err) {
                console.error('WebSocket construction failed', err);
                wsStatus = 'disconnected';
                chatReconnectTimer = setTimeout(connectChatWS, 3000);
                return;
            }

            chatWs.onopen = () => { wsStatus = 'connected'; };
            chatWs.onmessage = (event) => {
                try {
                    const data = JSON.parse(event.data);
                    if (data.event === 'agent_response') {
                        const targetChatId = data.chatId || chatsState.activeChatId;
                        chatsState.addMessage(targetChatId, 'agent', data.message);
                        chatsState.isGeneratingResponse = false;
                    }
                } catch (err) {
                    console.error(err);
                }
            };
            chatWs.onclose = () => {
                wsStatus = 'disconnected';
                if (chatReconnectTimer) clearTimeout(chatReconnectTimer);
                chatReconnectTimer = setTimeout(connectChatWS, 3000);
            };
        })();
    }

    onMount(() => {
        connectChatWS();

        const originalFetch = window.fetch;
        window.fetch = async (input, init) => {
            let url = '';
            if (typeof input === 'string') {
                url = input;
            } else if (input instanceof URL) {
                url = input.toString();
            } else {
                url = input.url;
            }

            if (url.includes('/api/') && !url.includes('/api/local-token')) {
                const token = await getApiToken();
                if (token) {
                    init = init || {};
                    const headers = new Headers(init.headers || {});
                    if (!headers.has('X-Local-Token')) {
                        headers.set('X-Local-Token', token);
                    }
                    init.headers = headers;
                    init.cache = init.cache || 'no-store'; // Globally prevent API caching
                }
            }
            return originalFetch(input, init);
        };

        const handleStatus = (e: Event) => {
            wsStatus = (e as CustomEvent).detail;
        };
        window.addEventListener('ws-status-change', handleStatus);

        const handleKeydown = (e: KeyboardEvent) => {
            if ((e.metaKey || e.ctrlKey) && e.key === 'k') {
                e.preventDefault();
                commandPaletteOpen = !commandPaletteOpen;
            }
            if (e.key === 'Escape') {
                commandPaletteOpen = false;
                profileOpen = false;
            }
        };
        window.addEventListener('keydown', handleKeydown);

        return () => {
            window.fetch = originalFetch;
            window.removeEventListener('ws-status-change', handleStatus);
            window.removeEventListener('keydown', handleKeydown);
            if (chatReconnectTimer) clearTimeout(chatReconnectTimer);
            if (chatWs) chatWs.close();
        };
    });

    const navItems = [
        { href: '/', label: 'Overview', desc: 'Operational status & telemetry' },
        { href: '/monitoring', label: 'Live Grid', desc: 'Real-time AI surveillance' },
        { href: '/events', label: 'Event Detections', desc: 'AI catches & alerts' },
        { href: '/review', label: 'Review Center', desc: 'Recordings & forensics' },
        { href: '/identities', label: 'Face Database', desc: 'Strangers & known identities' },
        { href: '/chat', label: 'Security Chat', desc: 'Conversational AI agent' },
        { href: '/cameras', label: 'Cameras', desc: 'Channels & region masks' },
        { href: '/system', label: 'Diagnostics', desc: 'Performance & telemetry' },
        { href: '/settings', label: 'Settings', desc: 'System configuration' }
    ];

    let commandQuery = $state('');
    const filteredNav = $derived(
        commandQuery.trim()
            ? navItems.filter(n =>
                n.label.toLowerCase().includes(commandQuery.toLowerCase()) ||
                n.desc.toLowerCase().includes(commandQuery.toLowerCase())
            )
            : navItems
    );

    const pageMeta = $derived.by(() => {
        const path = $page.url.pathname;
        const match = navItems.find(n => n.href === path || (n.href !== '/' && path.startsWith(n.href)));
        return match ?? navItems[0];
    });
</script>

<svelte:head>
    <link rel="icon" href={favicon} />
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin="anonymous">
    <link href="https://fonts.googleapis.com/css2?family=Bricolage+Grotesque:opsz,wght@12..96,400;12..96,500;12..96,600;12..96,700;12..96,800&family=JetBrains+Mono:wght@400;500;600;700&family=Sora:wght@300;400;500;600;700&display=swap" rel="stylesheet">
    <title>Hawkeye · Mission Control</title>
</svelte:head>

<div class="dark min-h-screen bg-background text-foreground flex overflow-hidden">
    <AppSidebar />

    <div class="flex-1 flex flex-col min-w-0 h-screen overflow-hidden">
        <!-- Top Bar -->
        <header class="h-14 shrink-0 border-b border-border bg-card/60 backdrop-blur-xl flex items-center justify-between gap-4 px-5 z-30">
            <!-- Page breadcrumb -->
            <div class="flex items-center gap-3 min-w-0">
                <div class="flex items-center gap-2 min-w-0">
                    <span class="section-eyebrow shrink-0">Hawkeye</span>
                    <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" class="text-muted-foreground/40 shrink-0">
                        <polyline points="9 18 15 12 9 6"/>
                    </svg>
                    <h1 class="text-sm font-semibold text-foreground font-display tracking-tight truncate">{pageMeta.label}</h1>
                </div>
            </div>

            <!-- Center command hint -->
            <button
                onclick={() => commandPaletteOpen = true}
                class="hidden md:flex items-center gap-2.5 h-8 px-3 rounded-lg border border-border bg-surface-2 hover:bg-card-hover transition-colors group min-w-[260px]"
            >
                <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" class="text-muted-foreground group-hover:text-foreground transition-colors">
                    <circle cx="11" cy="11" r="8"/><path d="m21 21-4.3-4.3"/>
                </svg>
                <span class="text-xs text-muted-foreground group-hover:text-foreground transition-colors flex-1 text-left">Quick search…</span>
                <span class="kbd">⌘K</span>
            </button>

            <!-- Right side -->
            <div class="flex items-center gap-2 shrink-0">
                <!-- Connection status -->
                <div class="flex items-center gap-2 h-8 px-3 rounded-lg border border-border bg-surface-2"
                    class:border-jade={wsStatus === 'connected'}
                    class:bg-jade={wsStatus === 'connected'}
                    class:bg-opacity-10={wsStatus === 'connected'}>
                    <span class="w-1.5 h-1.5 rounded-full"
                        class:bg-jade={wsStatus === 'connected'}
                        class:status-pulse={wsStatus === 'connected'}
                        class:bg-muted-foreground={wsStatus !== 'connected'}></span>
                    <span class="text-[11px] font-semibold tracking-wide"
                        class:text-jade={wsStatus === 'connected'}
                        class:text-muted-foreground={wsStatus !== 'connected'}>
                        {wsStatus === 'connected' ? 'Core online' : wsStatus === 'connecting' ? 'Connecting…' : 'Offline'}
                    </span>
                </div>

                <!-- Dev mode pill -->
                <button
                    onclick={() => devMode.enabled = !devMode.enabled}
                    class="hidden sm:flex items-center gap-1.5 h-8 px-2.5 rounded-lg border transition-all"
                    class:border-iris={devMode.enabled}
                    class:bg-iris={devMode.enabled}
                    class:bg-opacity-10={devMode.enabled}
                    class:text-iris={devMode.enabled}
                    class:border-border={!devMode.enabled}
                    class:bg-surface-2={!devMode.enabled}
                    class:text-muted-foreground={!devMode.enabled}
                    title="Toggle developer mode"
                >
                    <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5">
                        <polyline points="16 18 22 12 16 6"/><polyline points="8 6 2 12 8 18"/>
                    </svg>
                    <span class="text-[10px] font-bold uppercase tracking-wider">{devMode.enabled ? 'Dev' : 'User'}</span>
                </button>

                <!-- AI assistant button -->
                <button
                    onclick={() => chatOpen = !chatOpen}
                    class="flex items-center gap-2 h-8 px-3 rounded-lg border border-iris/20 bg-iris/10 text-iris hover:bg-iris/15 hover:border-iris/30 transition-all"
                    title="Open Hawkeye assistant"
                >
                    <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5">
                        <path d="M12 2a4 4 0 0 0-4 4v2H6a2 2 0 0 0-2 2v10a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V10a2 2 0 0 0-2-2h-2V6a4 4 0 0 0-4-4z" stroke-linecap="round" stroke-linejoin="round"/>
                        <circle cx="12" cy="13" r="2.5"/>
                    </svg>
                    <span class="text-xs font-semibold">Ask Hawkeye</span>
                    <span class="w-1.5 h-1.5 rounded-full bg-iris status-pulse"></span>
                </button>

                <!-- Profile -->
                <div class="relative">
                    <button
                        onclick={() => profileOpen = !profileOpen}
                        class="flex items-center gap-2 h-8 pl-1.5 pr-2.5 rounded-lg border border-border bg-surface-2 hover:bg-card-hover transition-colors"
                    >
                        <div class="w-5 h-5 rounded-md bg-gradient-to-br from-iris to-cyan flex items-center justify-center text-[10px] font-bold text-iris-foreground">A</div>
                        <span class="text-xs font-semibold">Admin</span>
                        <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" class="text-muted-foreground transition-transform" class:rotate-180={profileOpen}>
                            <polyline points="6 9 12 15 18 9"/>
                        </svg>
                    </button>

                    {#if profileOpen}
                        <div
                            class="absolute right-0 top-[calc(100%+8px)] w-64 panel !p-0 overflow-hidden z-50"
                            transition:fly={{ y: -8, duration: 180 }}
                        >
                            <div class="px-4 py-3.5 border-b border-border bg-surface-2">
                                <div class="flex items-center gap-2.5">
                                    <div class="w-9 h-9 rounded-lg bg-gradient-to-br from-iris to-cyan flex items-center justify-center text-sm font-bold text-iris-foreground">A</div>
                                    <div class="min-w-0">
                                        <p class="text-sm font-semibold text-foreground truncate">Administrator</p>
                                        <p class="text-[11px] text-muted-foreground truncate">admin@hawkeye.local</p>
                                    </div>
                                </div>
                            </div>
                            <div class="p-1.5 flex flex-col gap-0.5">
                                <a href="/settings" onclick={() => profileOpen = false} class="flex items-center gap-2.5 h-9 px-2.5 rounded-md text-xs text-foreground hover:bg-accent transition-colors">
                                    <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z"/></svg>
                                    <span>Settings</span>
                                </a>
                                <a href="/system" onclick={() => profileOpen = false} class="flex items-center gap-2.5 h-9 px-2.5 rounded-md text-xs text-foreground hover:bg-accent transition-colors">
                                    <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M22 12h-4l-3 9L9 3l-3 9H2"/></svg>
                                    <span>Diagnostics</span>
                                </a>
                                <div class="h-px bg-border my-1 mx-1"></div>
                                <div class="flex items-center justify-between px-2.5 h-9 rounded-md hover:bg-accent">
                                    <span class="text-xs text-foreground">Developer mode</span>
                                    <button
                                        type="button"
                                        onclick={() => devMode.enabled = !devMode.enabled}
                                        aria-label="Toggle developer mode"
                                        aria-pressed={devMode.enabled}
                                        class="relative w-8 h-4 rounded-full transition-colors"
                                        class:bg-iris={devMode.enabled}
                                        class:bg-muted={!devMode.enabled}
                                    >
                                        <span class="absolute top-0.5 left-0.5 w-3 h-3 rounded-full bg-foreground transition-transform"
                                            class:translate-x-4={devMode.enabled}></span>
                                    </button>
                                </div>
                            </div>
                        </div>
                    {/if}
                </div>
            </div>
        </header>

        <!-- Main content area -->
        <div class="flex-1 flex min-h-0 overflow-hidden relative">
            <main class="flex-1 min-w-0 overflow-y-auto" id="main-scroll">
                <div class="page-enter">
                    {@render children()}
                </div>
            </main>

            <!-- AI Chat side panel -->
            {#if chatOpen}
                <aside
                    class="w-[380px] shrink-0 border-l border-border bg-card flex flex-col h-full z-20"
                    transition:fly={{ x: 380, duration: 280, opacity: 0 }}
                >
                    <div class="h-14 shrink-0 px-4 flex items-center justify-between border-b border-border">
                        <div class="flex items-center gap-2.5">
                            <div class="w-7 h-7 rounded-lg bg-gradient-to-br from-iris to-cyan flex items-center justify-center">
                                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" class="text-iris-foreground">
                                    <path d="M12 2a4 4 0 0 0-4 4v2H6a2 2 0 0 0-2 2v10a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V10a2 2 0 0 0-2-2h-2V6a4 4 0 0 0-4-4z" stroke-linecap="round" stroke-linejoin="round"/>
                                    <circle cx="12" cy="13" r="2.5"/>
                                </svg>
                            </div>
                            <div>
                                <p class="text-sm font-semibold font-display tracking-tight">Hawkeye Assistant</p>
                                <p class="text-[10px] text-muted-foreground uppercase tracking-wider font-semibold flex items-center gap-1.5">
                                    <span class="w-1 h-1 rounded-full bg-jade"></span> Online · Local LLM
                                </p>
                            </div>
                        </div>
                        <button onclick={() => chatOpen = false} class="btn-icon" title="Close assistant">
                            <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5">
                                <line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/>
                            </svg>
                        </button>
                    </div>

                    <div class="flex gap-1.5 p-3 border-b border-border bg-surface-2 items-center">
                        <select
                            bind:value={chatsState.activeChatId}
                            class="select flex-1"
                        >
                            {#each chatsState.chats as chat}
                                <option value={chat.id}>{chat.title}</option>
                            {/each}
                        </select>
                        <button
                            onclick={() => chatsState.createNewChat()}
                            class="btn-icon"
                            title="New conversation"
                        >
                            <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5">
                                <line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/>
                            </svg>
                        </button>
                        {#if chatsState.chats.length > 1}
                            <button
                                onclick={() => chatsState.deleteChat(chatsState.activeChatId)}
                                class="btn-icon hover:!text-crimson hover:!border-crimson/30"
                                title="Delete conversation"
                            >
                                <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5">
                                    <polyline points="3 6 5 6 21 6"/>
                                    <path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"/>
                                    <path d="M10 11v6M14 11v6"/>
                                </svg>
                            </button>
                        {/if}
                    </div>

                    <div class="flex-1 min-h-0 overflow-hidden flex flex-col">
                        {#if chatsState.activeChat}
                            <AgentChat
                                chatId={chatsState.activeChatId}
                                bind:chatMessages={chatsState.activeChat.messages}
                                bind:isGeneratingResponse={chatsState.isGeneratingResponse}
                                ws={chatWs}
                            />
                        {/if}
                    </div>
                </aside>
            {/if}
        </div>
    </div>

    <!-- Command Palette -->
    {#if commandPaletteOpen}
        <!-- svelte-ignore a11y_no_static_element_interactions -->
        <div
            class="fixed inset-0 z-50 bg-background/80 backdrop-blur-sm flex items-start justify-center pt-[18vh]"
            transition:fade={{ duration: 150 }}
            role="presentation"
            onclick={() => commandPaletteOpen = false}
            onkeydown={(e) => { if (e.key === 'Escape') commandPaletteOpen = false; }}
        >
            <!-- svelte-ignore a11y_no_static_element_interactions -->
            <div
                class="w-full max-w-xl panel !p-0 overflow-hidden"
                role="dialog"
                tabindex="-1"
                aria-modal="true"
                aria-label="Command palette"
                onclick={(e) => e.stopPropagation()}
                onkeydown={(e) => e.stopPropagation()}
            >
                <div class="flex items-center gap-3 px-4 h-14 border-b border-border">
                    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" class="text-muted-foreground" aria-hidden="true">
                        <circle cx="11" cy="11" r="8"/><path d="m21 21-4.3-4.3"/>
                    </svg>
                    <label for="command-palette-input" class="sr-only">Command palette</label>
                    <input
                        id="command-palette-input"
                        bind:value={commandQuery}
                        placeholder="Jump to page, action, or setting…"
                        class="flex-1 bg-transparent text-sm outline-none placeholder:text-muted-foreground/60"
                    />
                    <span class="kbd" aria-hidden="true">ESC</span>
                </div>
                <div class="max-h-[60vh] overflow-y-auto p-2">
                    {#each filteredNav as item}
                        <a
                            href={item.href}
                            onclick={() => commandPaletteOpen = false}
                            class="flex items-center justify-between gap-3 px-3 h-12 rounded-lg hover:bg-accent transition-colors group"
                        >
                            <div class="min-w-0">
                                <p class="text-sm font-semibold text-foreground">{item.label}</p>
                                <p class="text-[11px] text-muted-foreground truncate">{item.desc}</p>
                            </div>
                            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" class="text-muted-foreground/40 group-hover:text-foreground transition-colors">
                                <polyline points="9 18 15 12 9 6"/>
                            </svg>
                        </a>
                    {/each}
                </div>
                <div class="flex items-center justify-between px-4 h-9 border-t border-border bg-surface-2">
                    <div class="flex items-center gap-3 text-[10px] text-muted-foreground">
                        <span class="flex items-center gap-1.5"><span class="kbd">↑</span><span class="kbd">↓</span> navigate</span>
                        <span class="flex items-center gap-1.5"><span class="kbd">↵</span> select</span>
                    </div>
                    <span class="text-[10px] text-muted-foreground">Hawkeye Mission Control</span>
                </div>
            </div>
        </div>
    {/if}
</div>
