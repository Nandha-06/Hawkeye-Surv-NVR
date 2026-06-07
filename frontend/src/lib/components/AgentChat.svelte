<script lang="ts">
    let {
        chatId = '',
        chatMessages = $bindable([]),
        isGeneratingResponse = $bindable(false),
        ws = null
    } = $props<{
        chatId?: string;
        chatMessages: { role: 'user' | 'agent'; text: string }[];
        isGeneratingResponse: boolean;
        ws: WebSocket | null;
    }>();

    let userInput = $state('');
    let scrollContainer = $state<HTMLDivElement | null>(null);

    const ALLOWED_IMG_HOSTS = new Set<string>(['localhost', '127.0.0.1']);

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

    function sendChatMessage() {
        if (!userInput.trim() || !ws || ws.readyState !== WebSocket.OPEN) return;

        const messageText = userInput.trim();
        chatMessages = [...chatMessages, { role: 'user', text: messageText }];
        userInput = '';
        isGeneratingResponse = true;
        autoScroll();

        ws.send(JSON.stringify({
            action: 'agent_chat',
            message: messageText,
            history: chatMessages.slice(0, -1),
            chatId: chatId
        }));
    }

    async function autoScroll() {
        await new Promise(r => setTimeout(r, 50));
        if (scrollContainer) {
            scrollContainer.scrollTo({
                top: scrollContainer.scrollHeight,
                behavior: 'smooth'
            });
        }
    }

    $effect(() => {
        chatMessages.length;
        autoScroll();
    });

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
</script>

<div class="flex flex-col h-full bg-surface-2/30">
    <div bind:this={scrollContainer} class="flex-1 overflow-y-auto p-4 flex flex-col gap-3">
        {#each chatMessages as msg}
            <div class="flex gap-2.5 items-start {msg.role === 'user' ? 'flex-row-reverse' : 'flex-row'}">
                <div class="w-7 h-7 rounded-lg border flex items-center justify-center text-[10px] font-display font-bold shrink-0
                    {msg.role === 'agent'
                        ? 'bg-iris/10 border-iris/20 text-iris'
                        : 'bg-cyan/10 border-cyan/20 text-cyan'}"
                >
                    {#if msg.role === 'agent'}
                        <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                            <path d="M12 8V4H8"/>
                            <rect width="16" height="12" x="4" y="8" rx="2"/>
                            <path d="M2 14h2M20 14h2M15 13v2M9 13v2"/>
                        </svg>
                    {:else}
                        A
                    {/if}
                </div>
                <div class="flex flex-col gap-1 min-w-0 max-w-[85%] {msg.role === 'user' ? 'items-end' : 'items-start'}">
                    <div class="text-[9px] font-mono font-bold text-muted-foreground uppercase tracking-wider">
                        {msg.role === 'agent' ? 'Hawkeye' : 'You'}
                    </div>
                    <div class="rounded-xl px-3 py-2 border text-xs leading-relaxed
                        {msg.role === 'user'
                            ? 'bg-cyan/10 border-cyan/30 text-foreground rounded-tr-sm'
                            : 'bg-card border-border text-foreground rounded-tl-sm'}"
                    >
                        {#each parseMessage(msg.text) as part}
                            {#if part.type === 'text'}
                                <p class="whitespace-pre-wrap m-0">{part.content}</p>
                            {:else if part.type === 'image' && part.src && isSafeImageSrc(part.src)}
                                <div class="mt-2 overflow-hidden rounded-md border border-border bg-black">
                                    <img src={part.src} alt={part.alt} referrerpolicy="no-referrer" class="max-h-44 object-contain w-full" />
                                </div>
                            {/if}
                        {/each}
                    </div>
                </div>
            </div>
        {/each}

        {#if isGeneratingResponse}
            <div class="flex gap-2.5 items-start">
                <div class="w-7 h-7 rounded-lg border bg-iris/10 border-iris/20 text-iris flex items-center justify-center shrink-0">
                    <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                        <path d="M12 8V4H8"/>
                        <rect width="16" height="12" x="4" y="8" rx="2"/>
                        <path d="M2 14h2M20 14h2M15 13v2M9 13v2"/>
                    </svg>
                </div>
                <div class="flex flex-col gap-1">
                    <div class="text-[9px] font-mono font-bold text-muted-foreground uppercase tracking-wider">Hawkeye</div>
                    <div class="bg-card border border-border rounded-xl rounded-tl-sm px-3 py-2.5 flex items-center gap-1.5">
                        <span class="w-1 h-1 rounded-full bg-iris dot-float"></span>
                        <span class="w-1 h-1 rounded-full bg-iris dot-float dot-delay-1"></span>
                        <span class="w-1 h-1 rounded-full bg-iris dot-float dot-delay-2"></span>
                    </div>
                </div>
            </div>
        {/if}
    </div>

    <form class="p-3 border-t border-border bg-card/40" onsubmit={(e) => { e.preventDefault(); sendChatMessage(); }}>
        <div class="flex gap-2">
            <input
                type="text"
                bind:value={userInput}
                placeholder="Ask about events…"
                disabled={isGeneratingResponse}
                class="input flex-1 !h-9 !text-xs"
            />
            <button
                type="submit"
                disabled={isGeneratingResponse || !userInput.trim()}
                class="btn btn-primary btn-sm !px-3"
                title="Send"
            >
                <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">
                    <line x1="22" y1="2" x2="11" y2="13"/>
                    <polygon points="22 2 15 22 11 13 2 9 22 2"/>
                </svg>
            </button>
        </div>
    </form>
</div>

<style>
    @keyframes float {
        0%, 100% { transform: translateY(0); opacity: 0.4; }
        50% { transform: translateY(-3px); opacity: 1; }
    }
    .dot-float { animation: float 1.2s ease-in-out infinite; }
    .dot-delay-1 { animation-delay: 0.2s; }
    .dot-delay-2 { animation-delay: 0.4s; }
</style>
