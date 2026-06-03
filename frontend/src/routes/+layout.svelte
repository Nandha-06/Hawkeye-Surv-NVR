<script lang="ts">
    import { onMount } from 'svelte';
    import { page } from '$app/stores';
    import { fly } from 'svelte/transition';
    import { devMode } from '$lib/devMode.svelte';
    import { getApiToken, buildWsUrl } from '$lib/apiToken';
    import favicon from '$lib/assets/favicon.svg';
    import AgentChat from '$lib/components/AgentChat.svelte';
    import AppSidebar from "$lib/components/AppSidebar.svelte";
    import * as Sidebar from "$lib/components/ui/sidebar/index.js";
    import '../app.css';

    let { children } = $props();
    let wsStatus = $state('disconnected');
    let profileOpen = $state(false);

    // Global Agent Chat State
    import { chatsState } from '$lib/chatsState.svelte';

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
                return;
            }

            chatWs.onopen = () => {
                wsStatus = 'connected';
            };

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
        // Connect layout-level chat WS
        connectChatWS();

        // Intercept global fetch to automatically attach X-Local-Token
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
                }
            }
            return originalFetch(input, init);
        };

        const handleStatus = (e: Event) => {
            wsStatus = (e as CustomEvent).detail;
        };
        window.addEventListener('ws-status-change', handleStatus);
        
        // Close profile dropdown on click outside
        const handleOutsideClick = (e: MouseEvent) => {
            const target = e.target as HTMLElement;
            if (!target.closest('.profile-menu-container')) {
                profileOpen = false;
            }
        };
        window.addEventListener('click', handleOutsideClick);

        return () => {
            window.fetch = originalFetch;
            window.removeEventListener('ws-status-change', handleStatus);
            window.removeEventListener('click', handleOutsideClick);
            if (chatReconnectTimer) clearTimeout(chatReconnectTimer);
            if (chatWs) chatWs.close();
        };
    });
</script>

<svelte:head>
    <link rel="icon" href={favicon} />
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin="anonymous">
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&family=Outfit:wght@400;500;600;700;800&family=JetBrains+Mono:wght@400;500&display=swap" rel="stylesheet">
    <title>Hawkeye AI</title>
</svelte:head>

<div class="dark min-h-screen bg-background text-foreground font-sans select-none overflow-hidden hover:opacity-100 flex flex-col items-stretch">
  <Sidebar.Provider>
    <AppSidebar bind:chatOpen={chatOpen} />
    
    <Sidebar.Inset>
        <!-- Top Navigation Bar -->
        <header class="h-14 border-b border-border bg-card/85 backdrop-blur-md flex justify-between items-center px-6 z-50 shrink-0">
            <div class="flex items-center gap-4">
                <Sidebar.Trigger />
            </div>

            <div class="flex items-center gap-3">
                <div class="flex items-center gap-1.5 px-3 py-1 rounded-full text-xs font-semibold border transition-all duration-200 {wsStatus === 'connected' ? 'bg-indigo-500/10 border-indigo-500/20 text-indigo-400' : 'bg-muted border-border text-muted-foreground'}">
                    <span class="w-1.5 h-1.5 rounded-full {wsStatus === 'connected' ? 'bg-indigo-500 shadow-[0_0_4px_#6366f1] animate-pulse' : 'bg-muted-foreground'}"></span>
                    <span>{wsStatus === 'connected' ? 'Core Active' : 'Offline'}</span>
                </div>
                <span class="text-xs font-medium px-2 py-0.5 rounded-md border {devMode.enabled ? 'bg-indigo-500/10 border-indigo-500/20 text-indigo-400' : 'bg-muted border-border text-muted-foreground'}">
                    {devMode.enabled ? 'Developer Mode' : 'User Mode'}
                </span>

                <div class="relative profile-menu-container">
                    <button class="flex items-center gap-2 hover:bg-accent hover:text-accent-foreground px-3 py-1.5 rounded-lg text-sm transition-colors cursor-pointer" onclick={() => profileOpen = !profileOpen} aria-label="Toggle user menu">
                        <div class="w-6 h-6 rounded-full bg-muted border border-border flex items-center justify-center text-muted-foreground shrink-0">
                            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">
                                <path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"/>
                                <circle cx="12" cy="7" r="4"/>
                            </svg>
                        </div>
                        <span class="font-medium">Admin</span>
                        <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" class="text-muted-foreground transition-transform duration-200 {profileOpen ? 'rotate-180' : ''}">
                            <polyline points="6 9 12 15 18 9"/>
                        </svg>
                    </button>

                    {#if profileOpen}
                        <div class="absolute right-0 top-[calc(100%+8px)] w-56 bg-card border border-border rounded-xl shadow-xl p-1 z-50 animate-in fade-in slide-in-from-top-2 duration-150">
                            <div class="px-3 py-2 flex flex-col">
                                <span class="text-xs font-semibold text-foreground">admin@hawkeye.ai</span>
                                <span class="text-[10px] text-muted-foreground uppercase tracking-wider font-bold">Administrator</span>
                            </div>
                            <div class="h-px bg-border my-1"></div>
                            <div class="flex items-center justify-between px-3 py-2 text-xs text-muted-foreground hover:text-foreground hover:bg-accent rounded-lg transition-colors">
                                <span>Developer Mode</span>
                                <label class="relative inline-flex items-center cursor-pointer">
                                    <input type="checkbox" bind:checked={devMode.enabled} class="sr-only peer" />
                                    <div class="w-8 h-4 bg-muted peer-focus:outline-none rounded-full peer peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:border-gray-300 after:border after:rounded-full after:h-3 after:w-3 after:transition-all peer-checked:bg-primary"></div>
                                </label>
                            </div>
                            <div class="h-px bg-border my-1"></div>
                            <a href="/settings" class="flex items-center gap-2 px-3 py-2 text-xs text-muted-foreground hover:text-foreground hover:bg-accent rounded-lg transition-colors" onclick={() => profileOpen = false}>
                                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z"/></svg>
                                Settings
                            </a>
                        </div>
                    {/if}
                </div>
            </div>
        </header>

        <div class="flex flex-1 h-[calc(100vh-56px)] overflow-hidden relative">
            <main class="flex-1 h-full overflow-y-auto bg-background">
                <div class="p-6 max-w-[1400px] mx-auto min-h-full flex flex-col">
                    {@render children()}
                </div>
            </main>

            {#if chatOpen}
                <aside class="fixed md:static top-0 right-0 h-full w-[340px] border-l border-border bg-card flex flex-col shadow-2xl z-50 shrink-0" transition:fly={{ x: 340, duration: 250 }}>
                    <div class="flex justify-between items-center px-4 py-3 border-b border-border bg-muted/20">
                        <div class="flex items-center gap-2">
                            <div class="w-6 h-6 rounded-full bg-primary text-primary-foreground flex items-center justify-center shadow-sm">
                                <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">
                                    <path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/>
                                </svg>
                            </div>
                            <div>
                                <h3 class="text-xs font-bold text-foreground">Hawkeye Assistant</h3>
                                <span class="text-[9px] text-muted-foreground uppercase font-bold tracking-wider">Security Console</span>
                            </div>
                        </div>
                        <button class="text-muted-foreground hover:text-foreground text-lg cursor-pointer" onclick={() => chatOpen = false}>&times;</button>
                    </div>

                    <div class="flex gap-1.5 p-2 border-b border-border bg-muted/10 items-center">
                        <select bind:value={chatsState.activeChatId} class="flex-1 bg-background text-foreground border border-border rounded-md px-2 py-1 text-xs outline-none cursor-pointer focus:border-ring">
                            {#each chatsState.chats as chat}
                                <option value={chat.id}>{chat.title}</option>
                            {/each}
                        </select>
                        <button onclick={() => chatsState.createNewChat()} class="w-7 h-7 bg-background border border-border hover:bg-accent rounded-md flex items-center justify-center text-xs cursor-pointer text-muted-foreground hover:text-foreground">+</button>
                        {#if chatsState.chats.length > 1}
                            <button onclick={() => chatsState.deleteChat(chatsState.activeChatId)} class="w-7 h-7 bg-destructive/10 border border-destructive/20 hover:bg-destructive/20 text-destructive rounded-md flex items-center justify-center text-xs cursor-pointer">&times;</button>
                        {/if}
                    </div>

                    <div class="flex-1 flex flex-col overflow-hidden p-2">
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
    </Sidebar.Inset>
  </Sidebar.Provider>
</div>