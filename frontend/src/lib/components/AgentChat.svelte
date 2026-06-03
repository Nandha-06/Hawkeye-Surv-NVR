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

    function sendChatMessage() {
        if (!userInput.trim() || !ws || ws.readyState !== WebSocket.OPEN) return;

        const messageText = userInput.trim();
        chatMessages = [...chatMessages, { role: 'user', text: messageText }];
        userInput = '';
        isGeneratingResponse = true;

        ws.send(JSON.stringify({
            action: 'agent_chat',
            message: messageText,
            history: chatMessages.slice(0, -1),
            chatId: chatId
        }));
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
</script>

<div class="chat-viewport-box">
    <div class="chat-messages-container">
        {#each chatMessages as msg}
            <div class="chat-message-bubble {msg.role}">
                <div class="avatar">{msg.role === 'agent' ? '🤖' : '👤'}</div>
                <div class="message-content-wrapper">
                    <span class="sender-label">{msg.role === 'agent' ? 'Hawkeye Security' : 'You'}</span>
                    <div class="message-text">
                        {#each parseMessage(msg.text) as part}
                            {#if part.type === 'text'}
                                <p style="white-space: pre-wrap; margin: 0;">{part.content}</p>
                            {:else if part.type === 'image'}
                                <div class="chat-image-wrapper">
                                    <img src={part.src} alt={part.alt} class="chat-embedded-img" />
                                </div>
                            {/if}
                        {/each}
                    </div>
                </div>
            </div>
        {/each}
        {#if isGeneratingResponse}
            <div class="chat-message-bubble agent">
                <div class="avatar">🤖</div>
                <div class="message-content-wrapper">
                    <span class="sender-label">Hawkeye Security</span>
                    <div class="message-text">
                        <div class="typing-indicator">
                            <span></span><span></span><span></span>
                        </div>
                    </div>
                </div>
            </div>
        {/if}
    </div>
    <form class="chat-input-bar" onsubmit={(e) => { e.preventDefault(); sendChatMessage(); }}>
        <input 
            type="text" 
            bind:value={userInput} 
            placeholder="Ask Hawkeye about camera events..." 
            disabled={isGeneratingResponse}
            class="chat-input"
        />
        <button type="submit" class="btn btn-accent btn-send" disabled={isGeneratingResponse || !userInput.trim()}>
            Send
        </button>
    </form>
</div>

<style>
    .chat-viewport-box {
        display: flex;
        flex-direction: column;
        height: 100%;
        background: var(--bg-control);
        border-radius: 8px;
        border: 1px solid var(--border-color);
        overflow: hidden;
    }
    .chat-messages-container {
        flex: 1;
        overflow-y: auto;
        padding: 1rem;
        display: flex;
        flex-direction: column;
        gap: 0.75rem;
    }
    .chat-message-bubble {
        display: flex;
        gap: 0.5rem;
        max-width: 90%;
        align-items: flex-start;
    }
    .chat-message-bubble.user {
        align-self: flex-end;
        flex-direction: row-reverse;
    }
    .chat-message-bubble.agent {
        align-self: flex-start;
    }
    .chat-message-bubble .avatar {
        width: 28px;
        height: 28px;
        border-radius: 50%;
        background: rgba(255, 255, 255, 0.04);
        display: flex;
        align-items: center;
        justify-content: center;
        font-size: 1rem;
        border: 1px solid var(--border-color);
        flex-shrink: 0;
    }
    .chat-message-bubble.user .avatar {
        background: rgba(99, 102, 241, 0.15);
        border-color: var(--accent-indigo);
    }
    .chat-message-bubble.agent .avatar {
        background: rgba(6, 182, 212, 0.15);
        border-color: var(--accent-cyan);
    }
    .message-content-wrapper {
        display: flex;
        flex-direction: column;
        gap: 0.15rem;
    }
    .chat-message-bubble.user .message-content-wrapper {
        align-items: flex-end;
    }
    .sender-label {
        font-size: 0.65rem;
        font-weight: 600;
        color: var(--text-muted);
        text-transform: uppercase;
        letter-spacing: 0.05em;
    }
    .message-text {
        padding: 0.5rem 0.75rem;
        border-radius: 10px;
        font-size: 0.8rem;
        line-height: 1.4;
    }
    .chat-message-bubble.user .message-text {
        background: var(--accent-indigo);
        color: #ffffff;
        border-top-right-radius: 2px;
    }
    .chat-message-bubble.agent .message-text {
        background: rgba(255, 255, 255, 0.02);
        color: var(--text-primary);
        border: 1px solid var(--border-color);
        border-top-left-radius: 2px;
    }
    
    .chat-image-wrapper {
        margin: 0.5rem 0;
        border-radius: 6px;
        overflow: hidden;
        border: 1px solid var(--border-glass);
        max-width: 100%;
        background: #020306;
    }
    .chat-embedded-img {
        max-width: 100%;
        max-height: 180px;
        object-fit: contain;
        display: block;
    }

    .chat-input-bar {
        display: flex;
        gap: 0.5rem;
        padding: 0.65rem;
        background: rgba(0, 0, 0, 0.2);
        border-top: 1px solid var(--border-color);
    }
    .chat-input {
        flex: 1;
        background: rgba(255, 255, 255, 0.02);
        border: 1px solid var(--border-color);
        border-radius: 6px;
        color: var(--text-primary);
        padding: 0.45rem 0.75rem;
        font-size: 0.8rem;
        transition: var(--transition-smooth);
    }
    .chat-input:focus {
        outline: none;
        border-color: var(--border-color-hover);
        background: rgba(255, 255, 255, 0.04);
    }
    .btn {
        display: inline-flex;
        align-items: center;
        justify-content: center;
        font-family: var(--font-sans);
        font-size: 0.75rem;
        font-weight: 600;
        padding: 0.45rem 0.85rem;
        border-radius: 6px;
        border: 1px solid transparent;
        cursor: pointer;
        transition: all var(--transition-smooth);
        gap: 0.35rem;
    }
    .btn-accent {
        background: var(--accent-indigo);
        color: white;
    }
    .btn:disabled {
        opacity: 0.5;
        cursor: not-allowed;
    }
    .btn-send {
        padding: 0 1rem;
    }
    .typing-indicator {
        display: flex;
        align-items: center;
        gap: 0.35rem;
        padding: 0.25rem 0.1rem;
    }
    .typing-indicator span {
        width: 6px;
        height: 6px;
        background-color: #818cf8; /* Bright indigo */
        border-radius: 50%;
        display: inline-block;
        animation: float 1.2s ease-in-out infinite;
    }
    .typing-indicator span:nth-child(2) { animation-delay: .2s; }
    .typing-indicator span:nth-child(3) { animation-delay: .4s; }

    @keyframes float {
        0%, 100% {
            transform: translateY(0);
            opacity: 0.4;
        }
        50% {
            transform: translateY(-5px);
            opacity: 1;
            filter: drop-shadow(0 0 3px rgba(129, 140, 248, 0.8));
        }
    }
</style>
