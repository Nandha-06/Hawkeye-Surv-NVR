<script lang="ts">
    import { onMount, tick } from 'svelte';
    import { chatsState } from '$lib/chatsState.svelte';
    import { getApiToken, buildWsUrl } from '$lib/apiToken';

    let ws: WebSocket | null = $state(null);
    let userInput = $state('');
    let chatContainer = $state<HTMLDivElement | null>(null);
    let wsReconnectTimer: ReturnType<typeof setTimeout> | null = null;

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
                return;
            }

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
                if (wsReconnectTimer) clearTimeout(wsReconnectTimer);
                wsReconnectTimer = setTimeout(connectWS, 3000);
            };
        })();
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

    // Helper to parse markdown-like image syntax: ![alt](url)
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

    onMount(() => {
        connectWS();
        autoScroll();
        return () => {
            if (wsReconnectTimer) clearTimeout(wsReconnectTimer);
            if (ws) ws.close();
        };
    });
</script>

<div class="flex h-[calc(100vh-80px)] w-full rounded-2xl border border-zinc-800 bg-zinc-950/90 text-white font-sans overflow-hidden shadow-2xl backdrop-blur-xl">
    <!-- Sidebar - Chat Sessions Manager -->
    <aside class="w-[280px] border-r border-zinc-800 bg-zinc-900/40 p-4 flex flex-col justify-between shrink-0">
        <div class="flex flex-col gap-4 overflow-hidden">
            <div class="flex items-center justify-between">
                <span class="text-xs font-bold text-zinc-400 tracking-wider uppercase">Active Conversations</span>
                <button 
                    onclick={() => chatsState.createNewChat()}
                    class="w-6 h-6 rounded-md bg-zinc-800 hover:bg-zinc-700 border border-zinc-700 flex items-center justify-center text-zinc-300 hover:text-white transition-colors cursor-pointer"
                    title="Start New Chat"
                >
                    +
                </button>
            </div>

            <!-- List of Chats -->
            <div class="flex flex-col gap-1 overflow-y-auto max-h-[400px] pr-1">
                {#each chatsState.chats as chat}
                    {@const isActive = chatsState.activeChatId === chat.id}
                    <div class="flex items-center w-full group">
                        <button
                            onclick={() => { chatsState.activeChatId = chat.id; autoScroll(); }}
                            class="flex-1 text-left px-3 py-2 rounded-xl text-xs font-medium truncate transition-all duration-200 cursor-pointer {isActive ? 'bg-indigo-600 text-white shadow-md' : 'text-zinc-400 hover:text-zinc-200 hover:bg-zinc-800/40'}"
                        >
                            {chat.title}
                        </button>
                        {#if chatsState.chats.length > 1}
                            <button
                                onclick={() => chatsState.deleteChat(chat.id)}
                                class="w-6 h-6 text-zinc-500 hover:text-rose-500 flex items-center justify-center text-xs opacity-0 group-hover:opacity-100 transition-opacity cursor-pointer ml-1"
                                title="Delete conversation"
                            >
                                &times;
                            </button>
                        {/if}
                    </div>
                {/each}
            </div>
        </div>

        <!-- Co-pilot Info Footer -->
        <div class="border-t border-zinc-800 pt-4 flex flex-col gap-2">
            <div class="flex items-center gap-2">
                <div class="w-2 h-2 rounded-full bg-emerald-500 animate-pulse"></div>
                <span class="text-[10px] font-extrabold tracking-wider uppercase text-emerald-400">Assistant Ready</span>
            </div>
            <p class="text-[10px] text-zinc-500 leading-snug">
                Query security events, camera operational status, or search historical snapshots directly.
            </p>
        </div>
    </aside>

    <!-- Main Chat Workspace -->
    <main class="flex-1 flex flex-col overflow-hidden bg-zinc-950/20">
        <!-- Top bar -->
        <div class="px-6 py-4 bg-zinc-900/35 border-b border-zinc-800 flex justify-between items-center backdrop-blur-md">
            <div>
                <h1 class="text-base font-bold bg-gradient-to-r from-indigo-400 to-cyan-400 bg-clip-text text-transparent">Security Assistant</h1>
                <p class="text-[10px] text-zinc-400">Query camera events and history using natural language</p>
            </div>
            <div class="flex gap-2">
                <span class="inline-flex items-center px-2 py-0.5 rounded-full text-[10px] font-semibold bg-indigo-500/10 text-indigo-400 border border-indigo-500/20 shadow-sm">
                    Offline Search Active
                </span>
            </div>
        </div>

        <!-- Messages Journal -->
        <div bind:this={chatContainer} class="flex-1 overflow-y-auto p-6 space-y-4">
            {#if chatsState.activeChat}
                {#each chatsState.activeChat.messages as msg}
                    <div class="flex {msg.role === 'user' ? 'justify-end' : 'justify-start'}">
                        <div class="flex gap-3 max-w-[75%] items-start {msg.role === 'user' ? 'flex-row-reverse' : 'flex-row'}">
                            <div class="w-8 h-8 rounded-full border border-zinc-800 flex items-center justify-center text-xs shadow-md shrink-0 bg-zinc-900 select-none">
                                {msg.role === 'agent' ? '🤖' : '👤'}
                            </div>
                            <div class="flex flex-col gap-1">
                                <span class="text-[9px] font-semibold text-zinc-500 uppercase tracking-wider {msg.role === 'user' ? 'text-right' : 'text-left'}">
                                    {msg.role === 'agent' ? 'Hawkeye Security' : 'Administrator'}
                                </span>
                                <div class="rounded-2xl px-4 py-3 shadow-md border {msg.role === 'user' ? 'bg-indigo-600 border-indigo-500 text-white rounded-tr-none' : 'bg-zinc-900/80 border-zinc-800 text-zinc-100 rounded-tl-none'}">
                                    {#each parseMessage(msg.text) as part}
                                        {#if part.type === 'text'}
                                            <p class="text-xs leading-relaxed whitespace-pre-wrap select-text">{part.content}</p>
                                        {:else if part.type === 'image' && part.src && isSafeImageSrc(part.src)}
                                            <div class="mt-3 overflow-hidden rounded-xl border border-zinc-800 max-w-lg bg-zinc-950">
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
                <div class="flex justify-start">
                    <div class="flex gap-3 items-start">
                        <div class="w-8 h-8 rounded-full border border-zinc-800 flex items-center justify-center text-xs shadow-md bg-zinc-900">
                            🤖
                        </div>
                        <div class="flex flex-col gap-1">
                            <span class="text-[9px] font-semibold text-zinc-500 uppercase tracking-wider">Hawkeye Security</span>
                            <div class="bg-zinc-900/80 border border-zinc-800 rounded-2xl rounded-tl-none px-4 py-3 flex space-x-2 items-center">
                                <span class="w-2 h-2 bg-indigo-400 rounded-full dot-float"></span>
                                <span class="w-2 h-2 bg-indigo-400 rounded-full dot-float dot-delay-1"></span>
                                <span class="w-2 h-2 bg-indigo-400 rounded-full dot-float dot-delay-2"></span>
                            </div>
                        </div>
                    </div>
                </div>
            {/if}
        </div>

        <!-- Suggestion Chips & Input Tray -->
        <div class="p-4 bg-zinc-900/35 border-t border-zinc-800 flex flex-col gap-3 backdrop-blur-md">
            <!-- Chips -->
            <div class="flex gap-2 overflow-x-auto py-1 pr-2 no-scrollbar select-none">
                {#each suggestChips as chip}
                    <button
                        onclick={() => selectChip(chip)}
                        disabled={chatsState.isGeneratingResponse}
                        class="px-3 py-1.5 rounded-full bg-zinc-900 hover:bg-zinc-800 border border-zinc-800 text-[10px] font-medium text-zinc-400 hover:text-zinc-200 transition-all shadow-sm shrink-0 disabled:opacity-50 disabled:cursor-not-allowed cursor-pointer"
                    >
                        {chip}
                    </button>
                {/each}
            </div>

            <!-- Input Bar -->
            <div class="flex space-x-2">
                <input
                    type="text"
                    placeholder="Ask about events, e.g., 'Were there any critical events in the driveway?'"
                    bind:value={userInput}
                    disabled={chatsState.isGeneratingResponse}
                    onkeydown={(e) => e.key === 'Enter' && sendChatMessage()}
                    class="flex-1 bg-zinc-950 border border-zinc-800 rounded-xl px-4 py-3 text-xs text-white focus:outline-none focus:border-indigo-500 transition-colors placeholder-zinc-600 disabled:opacity-50"
                />
                <button
                    onclick={() => sendChatMessage()}
                    disabled={chatsState.isGeneratingResponse || !userInput.trim()}
                    class="bg-gradient-to-r from-indigo-500 to-cyan-500 hover:from-indigo-600 hover:to-cyan-600 font-bold px-6 text-xs text-white rounded-xl transition-all shadow-md active:scale-95 disabled:opacity-50 disabled:cursor-not-allowed cursor-pointer"
                >
                    Search
                </button>
            </div>
        </div>
    </main>
</div>

<style>
    /* Hide scrollbars for suggesion chips track */
    .no-scrollbar::-webkit-scrollbar {
        display: none;
    }
    .no-scrollbar {
        -ms-overflow-style: none;
        scrollbar-width: none;
    }

    /* Premium custom floating dots keyframe animation */
    @keyframes float {
        0%, 100% {
            transform: translateY(0);
            opacity: 0.35;
        }
        50% {
            transform: translateY(-5px);
            opacity: 1;
            filter: drop-shadow(0 0 3px rgba(129, 140, 248, 0.8));
        }
    }
    .dot-float {
        animation: float 1.2s ease-in-out infinite;
    }
    .dot-delay-1 {
        animation-delay: 0.2s;
    }
    .dot-delay-2 {
        animation-delay: 0.4s;
    }
</style>
