<script lang="ts">
    import { onMount, tick } from 'svelte';
    import { chatsState } from '$lib/chatsState.svelte';
    import { getApiToken, buildWsUrl } from '$lib/apiToken';
    import { fly, fade } from 'svelte/transition';

    let ws: WebSocket | null = $state(null);
    let userInput = $state('');
    let chatContainer = $state<HTMLDivElement | null>(null);
    let wsReconnectTimer: ReturnType<typeof setTimeout> | null = null;
    let wsConnected = $state(false);

    const suggestChips = [
        "Did anyone walk by today?",
        "Show me the last person event",
        "Was a car detected recently?",
        "Give me a summary of camera activity",
        "Show event snapshots"
    ];

    const ALLOWED_IMG_HOSTS = new Set<string>([
        'localhost',
        '127.0.0.1',
    ]);

    function isSafeImageSrc(src: string): boolean {
        if (!src) return false;
        if (src.startsWith('/api/')) return true;
        if (src.startsWith('data:image/')) return true;
        if (src.startsWith('blob:')) return true;
        try {
            const u = new URL(src);
            if (u.protocol !== 'https:' && u.protocol !== 'http:') return false;
            return ALLOWED_IMG_HOSTS.has(u.hostname);
        } catch {
            return false;
        }
    }

    function connectWS() {
        if (typeof window === 'undefined') return;
        void (async () => {
            const token = await getApiToken();
            const wsUrl = buildWsUrl('/api/ws', token);
            try {
                ws = new WebSocket(wsUrl);
            } catch (err) {
                console.error('WebSocket construction failed', err);
                scheduleReconnect();
                return;
            }

            ws.onopen = () => { wsConnected = true; };

            ws.onmessage = (event) => {
                try {
                    const data = JSON.parse(event.data);
                    if (data.event === 'agent_response') {
                        const targetChatId = data.chatId || chatsState.activeChatId;
                        chatsState.addMessage(targetChatId, 'agent', data.message);
                        chatsState.isGeneratingResponse = false;
                        autoScroll();
                    }
                } catch (err) {
                    console.error(err);
                }
            };

            ws.onclose = () => {
                wsConnected = false;
                scheduleReconnect();
            };

            ws.onerror = () => {
                wsConnected = false;
            };
        })();
    }

    function scheduleReconnect() {
        if (wsReconnectTimer) clearTimeout(wsReconnectTimer);
        wsReconnectTimer = setTimeout(connectWS, 3000);
    }

    async function autoScroll() {
        await tick();
        if (chatContainer) {
            chatContainer.scrollTo({
                top: chatContainer.scrollHeight,
                behavior: 'smooth'
            });
        }
    }

    function sendChatMessage(textToSubmit?: string) {
        const queryText = (textToSubmit || userInput).trim();
        if (!queryText || !ws || ws.readyState !== WebSocket.OPEN) return;

        chatsState.addMessage(chatsState.activeChatId, 'user', queryText);
        if (!textToSubmit) userInput = '';
        chatsState.isGeneratingResponse = true;
        autoScroll();

        ws.send(JSON.stringify({
            action: 'agent_chat',
            message: queryText,
            history: chatsState.activeChat?.messages.slice(0, -1) || [],
            chatId: chatsState.activeChatId
        }));
    }

    function selectChip(chip: string) {
        sendChatMessage(chip);
    }

    function parseMessage(text: string) {
        const imgRegex = /!\[(.*?)\]\((.*?)\)/g;
        const parts = [];
        let lastIndex = 0;
        let match;

        while ((match = imgRegex.exec(text)) !== null) {
            const textBefore = text.substring(lastIndex, match.index);
            if (textBefore) {
                parts.push({ type: 'text', content: textBefore });
            }
            parts.push({ type: 'image', alt: match[1], src: match[2] });
            lastIndex = imgRegex.lastIndex;
        }

        const textAfter = text.substring(lastIndex);
        if (textAfter) {
            parts.push({ type: 'text', content: textAfter });
        }

        if (parts.length === 0) {
            parts.push({ type: 'text', content: text });
        }
        return parts;
    }

    function getInitials(name: string): string {
        if (!name) return 'U';
        return name.split(/\s+/).slice(0, 2).map(p => p[0] || '').join('').toUpperCase() || 'U';
    }

    function getChatPreview(chat: any): string {
        const last = chat.messages[chat.messages.length - 1];
        if (!last) return 'No messages yet';
        return last.text.substring(0, 60) + (last.text.length > 60 ? '…' : '');
    }

    onMount(() => {
        connectWS();
        autoScroll();
        return () => {
            if (wsReconnectTimer) clearTimeout(wsReconnectTimer);
            if (ws) ws.close();
        };
    });
</script>

<div class="flex h-[calc(100vh-7rem)] w-full panel !p-0 overflow-hidden">

    <!-- Sidebar: Chat sessions -->
    <aside class="w-[280px] border-r border-border flex flex-col bg-surface-2/30 shrink-0">
        <!-- Sidebar header -->
        <div class="px-4 py-4 border-b border-border flex items-center justify-between">
            <div class="flex items-center gap-2">
                <div class="w-7 h-7 rounded-lg bg-iris/10 border border-iris/20 flex items-center justify-center text-iris">
                    <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                        <path d="M12 8V4H8"/>
                        <rect width="16" height="12" x="4" y="8" rx="2"/>
                        <path d="M2 14h2M20 14h2M15 13v2M9 13v2"/>
                    </svg>
                </div>
                <div>
                    <h2 class="text-sm font-display font-semibold text-foreground">Conversations</h2>
                    <p class="text-[9px] text-muted-foreground font-mono uppercase tracking-wider">{chatsState.chats.length} session{chatsState.chats.length > 1 ? 's' : ''}</p>
                </div>
            </div>
            <button
                onclick={() => chatsState.createNewChat()}
                class="btn-icon !w-8 !h-8"
                title="New chat"
            >
                <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">
                    <line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/>
                </svg>
            </button>
        </div>

        <!-- Chat list -->
        <div class="flex-1 overflow-y-auto p-2">
            <div class="flex flex-col gap-1">
                {#each chatsState.chats as chat (chat.id)}
                    {@const isActive = chatsState.activeChatId === chat.id}
                    <div class="group relative">
                        <button
                            onclick={() => { chatsState.activeChatId = chat.id; autoScroll(); }}
                            class="w-full text-left px-3 py-2.5 rounded-lg transition-all flex flex-col gap-0.5
                            {isActive
                                ? 'bg-iris/10 border border-iris/30'
                                : 'border border-transparent hover:bg-surface-2'}"
                        >
                            <div class="flex items-center gap-2 min-w-0">
                                <span class="w-1 h-1 rounded-full {isActive ? 'bg-iris' : 'bg-muted-foreground'}"></span>
                                <span class="text-[11px] font-semibold text-foreground truncate {isActive ? 'text-iris' : ''}">{chat.title}</span>
                            </div>
                            <span class="text-[10px] text-muted-foreground truncate pl-3">{getChatPreview(chat)}</span>
                        </button>
                        {#if chatsState.chats.length > 1}
                            <button
                                onclick={() => chatsState.deleteChat(chat.id)}
                                class="absolute right-1.5 top-2 w-5 h-5 rounded-md flex items-center justify-center text-muted-foreground hover:text-crimson opacity-0 group-hover:opacity-100 transition-all hover:bg-crimson/10"
                                title="Delete conversation"
                            >
                                <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">
                                    <path d="M3 6h18M19 6v14c0 1-1 2-2 2H7c-1-1-2-1-2-2V6M8 6V4c0-1 1-2 2-2h4c1 0 2 1 2 2v2"/>
                                </svg>
                            </button>
                        {/if}
                    </div>
                {/each}
            </div>
        </div>

        <!-- Status footer -->
        <div class="p-4 border-t border-border flex flex-col gap-2">
            <div class="flex items-center gap-2">
                <span class="w-1.5 h-1.5 rounded-full {wsConnected ? 'bg-jade status-pulse' : 'bg-gold status-pulse'}"></span>
                <span class="text-[10px] font-mono font-bold uppercase tracking-wider {wsConnected ? 'text-jade' : 'text-gold'}">
                    {wsConnected ? 'Assistant Ready' : 'Connecting'}
                </span>
            </div>
            <p class="text-[10px] text-muted-foreground leading-relaxed">
                Query security events, camera operational status, or search historical snapshots.
            </p>
        </div>
    </aside>

    <!-- Main chat workspace -->
    <main class="flex-1 flex flex-col overflow-hidden bg-background">
        <!-- Top bar -->
        <div class="px-6 py-4 border-b border-border flex justify-between items-center bg-card/30 backdrop-blur-sm">
            <div class="flex items-center gap-3">
                <div class="w-9 h-9 rounded-lg bg-gradient-to-br from-iris/20 to-cyan/10 border border-iris/30 flex items-center justify-center text-iris">
                    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                        <path d="M12 8V4H8"/>
                        <rect width="16" height="12" x="4" y="8" rx="2"/>
                        <path d="M2 14h2M20 14h2M15 13v2M9 13v2"/>
                    </svg>
                </div>
                <div>
                    <h1 class="text-base font-display font-semibold text-foreground">Security Assistant</h1>
                    <p class="text-[10px] text-muted-foreground font-mono">Natural language · local perception queries</p>
                </div>
            </div>
            <div class="flex items-center gap-2">
                <span class="badge badge-iris">
                    <span class="w-1.5 h-1.5 rounded-full bg-iris status-pulse"></span>
                    {chatsState.activeChat?.title || 'No session'}
                </span>
                <span class="badge badge-muted font-mono">
                    {chatsState.activeChat?.messages.length || 0} msg
                </span>
            </div>
        </div>

        <!-- Messages -->
        <div bind:this={chatContainer} class="flex-1 overflow-y-auto p-6">
            {#if chatsState.activeChat}
                {#each chatsState.activeChat.messages as msg, i (i)}
                    <div class="mb-5 {msg.role === 'user' ? 'flex justify-end' : 'flex justify-start'}"
                        in:fly={{ y: 8, duration: 200 }}>
                        <div class="flex gap-3 max-w-[75%] items-start {msg.role === 'user' ? 'flex-row-reverse' : 'flex-row'}">
                            <div class="w-8 h-8 rounded-lg border flex items-center justify-center text-[10px] font-display font-bold shrink-0
                                {msg.role === 'agent'
                                    ? 'bg-iris/10 border-iris/20 text-iris'
                                    : 'bg-cyan/10 border-cyan/20 text-cyan'}"
                            >
                                {#if msg.role === 'agent'}
                                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                                        <path d="M12 8V4H8"/>
                                        <rect width="16" height="12" x="4" y="8" rx="2"/>
                                        <path d="M2 14h2M20 14h2M15 13v2M9 13v2"/>
                                    </svg>
                                {:else}
                                    {getInitials('Admin')}
                                {/if}
                            </div>

                            <div class="flex flex-col gap-1.5 min-w-0">
                                <div class="flex items-center gap-1.5 text-[9px] font-mono font-bold uppercase tracking-wider text-muted-foreground
                                    {msg.role === 'user' ? 'justify-end' : 'justify-start'}">
                                    <span>{msg.role === 'agent' ? 'Hawkeye' : 'Administrator'}</span>
                                    <span class="w-1 h-1 rounded-full bg-muted-foreground/40"></span>
                                    <span>{(i === 0 ? '00:00' : new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }))}</span>
                                </div>

                                <div class="rounded-2xl px-4 py-3 border
                                    {msg.role === 'user'
                                        ? 'bg-cyan/10 border-cyan/30 text-foreground rounded-tr-sm'
                                        : 'bg-card border-border text-foreground rounded-tl-sm'}"
                                >
                                    {#each parseMessage(msg.text) as part}
                                        {#if part.type === 'text'}
                                            <p class="text-sm leading-relaxed whitespace-pre-wrap">{part.content}</p>
                                        {:else if part.type === 'image' && part.src && isSafeImageSrc(part.src)}
                                            <div class="mt-3 overflow-hidden rounded-lg border border-border max-w-lg bg-black">
                                                <img src={part.src} alt={part.alt} referrerpolicy="no-referrer" class="max-h-64 object-contain w-full" />
                                            </div>
                                        {/if}
                                    {/each}
                                </div>
                            </div>
                        </div>
                    </div>
                {/each}
            {/if}

            <!-- Typing indicator -->
            {#if chatsState.isGeneratingResponse}
                <div class="flex justify-start mb-5" in:fade={{ duration: 150 }}>
                    <div class="flex gap-3 items-start">
                        <div class="w-8 h-8 rounded-lg border bg-iris/10 border-iris/20 text-iris flex items-center justify-center shrink-0">
                            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                                <path d="M12 8V4H8"/>
                                <rect width="16" height="12" x="4" y="8" rx="2"/>
                                <path d="M2 14h2M20 14h2M15 13v2M9 13v2"/>
                            </svg>
                        </div>
                        <div class="flex flex-col gap-1.5">
                            <span class="text-[9px] font-mono font-bold uppercase tracking-wider text-muted-foreground">Hawkeye</span>
                            <div class="bg-card border border-border rounded-2xl rounded-tl-sm px-4 py-3 flex items-center gap-1.5">
                                <span class="w-1.5 h-1.5 rounded-full bg-iris dot-float"></span>
                                <span class="w-1.5 h-1.5 rounded-full bg-iris dot-float dot-delay-1"></span>
                                <span class="w-1.5 h-1.5 rounded-full bg-iris dot-float dot-delay-2"></span>
                            </div>
                        </div>
                    </div>
                </div>
            {/if}
        </div>

        <!-- Suggestion chips & input -->
        <div class="p-4 border-t border-border bg-card/30 backdrop-blur-sm flex flex-col gap-3">
            <div class="flex items-center gap-2 overflow-x-auto py-1 pr-2 no-scrollbar">
                <span class="text-[10px] font-mono font-bold text-muted-foreground uppercase tracking-wider shrink-0">Try:</span>
                {#each suggestChips as chip}
                    <button
                        onclick={() => selectChip(chip)}
                        disabled={chatsState.isGeneratingResponse}
                        class="px-3 h-7 rounded-full border border-border bg-surface-2 hover:bg-surface-3 hover:border-iris/30 hover:text-iris text-[10px] font-semibold text-muted-foreground transition-all shrink-0 disabled:opacity-50"
                    >
                        {chip}
                    </button>
                {/each}
            </div>

            <div class="flex gap-2">
                <div class="flex-1 relative">
                    <input
                        type="text"
                        placeholder="Ask about events, e.g., 'Were there any critical events in the driveway?'"
                        bind:value={userInput}
                        disabled={chatsState.isGeneratingResponse}
                        onkeydown={(e) => e.key === 'Enter' && sendChatMessage()}
                        class="input !h-11 !pl-11 !text-sm"
                    />
                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" class="absolute left-3.5 top-1/2 -translate-y-1/2 text-muted-foreground">
                        <circle cx="11" cy="11" r="8"/><path d="m21 21-4.3-4.3"/>
                    </svg>
                </div>
                <button
                    onclick={() => sendChatMessage()}
                    disabled={chatsState.isGeneratingResponse || !userInput.trim()}
                    class="btn btn-primary btn-lg !px-6"
                >
                    {#if chatsState.isGeneratingResponse}
                        <span class="w-3 h-3 rounded-full border-2 border-current border-t-transparent animate-spin"></span>
                    {:else}
                        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">
                            <line x1="22" y1="2" x2="11" y2="13"/>
                            <polygon points="22 2 15 22 11 13 2 9 22 2"/>
                        </svg>
                    {/if}
                    Send
                </button>
            </div>
        </div>
    </main>
</div>

<style>
    @keyframes float {
        0%, 100% { transform: translateY(0); opacity: 0.4; }
        50% { transform: translateY(-4px); opacity: 1; }
    }
    .dot-float { animation: float 1.2s ease-in-out infinite; }
    .dot-delay-1 { animation-delay: 0.2s; }
    .dot-delay-2 { animation-delay: 0.4s; }
</style>
