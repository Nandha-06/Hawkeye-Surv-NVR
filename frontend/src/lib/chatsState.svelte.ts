import { browser } from '$app/environment';

export interface ChatMessage {
    role: 'user' | 'agent';
    text: string;
}

export interface ChatSession {
    id: string;
    title: string;
    messages: ChatMessage[];
}

class ChatsState {
    #chats = $state<ChatSession[]>([]);
    #activeChatId = $state<string>('');
    #isGeneratingResponse = $state(false);

    constructor() {
        if (browser) {
            this.load();
        }
    }

    get chats() {
        return this.#chats;
    }

    set chats(val: ChatSession[]) {
        this.#chats = val;
        this.save();
    }

    get activeChatId() {
        return this.#activeChatId;
    }

    set activeChatId(val: string) {
        this.#activeChatId = val;
        this.save();
    }

    get activeChat() {
        return this.#chats.find(c => c.id === this.#activeChatId) || null;
    }

    get isGeneratingResponse() {
        return this.#isGeneratingResponse;
    }

    set isGeneratingResponse(val: boolean) {
        this.#isGeneratingResponse = val;
    }

    load() {
        if (!browser) return;
        const storedChats = localStorage.getItem('dc_chats');
        if (storedChats) {
            try {
                this.#chats = JSON.parse(storedChats);
                const lastActive = localStorage.getItem('dc_active_chat_id');
                this.#activeChatId = lastActive && this.#chats.some(c => c.id === lastActive) ? lastActive : this.#chats[0]?.id || '';
            } catch (e) {
                console.error('Failed to parse stored chats:', e);
                this.seedDefault();
            }
        } else {
            this.seedDefault();
        }
    }

    save() {
        if (!browser) return;

        const maxSessions = 20;
        const maxMessages = 100;

        // Bound number of sessions, ensuring we keep the active chat session
        if (this.#chats.length > maxSessions) {
            const activeChat = this.#chats.find(c => c.id === this.#activeChatId);
            let otherChats = this.#chats.filter(c => c.id !== this.#activeChatId);
            if (otherChats.length > maxSessions - 1) {
                // Keep the most recent sessions
                otherChats = otherChats.slice(otherChats.length - (maxSessions - 1));
            }
            if (activeChat) {
                // Keep active chat at the end (or preserve relative order if preferred, but appending is simple)
                this.#chats = [...otherChats, activeChat];
            } else {
                this.#chats = otherChats;
            }
        }

        // Bound messages per session, keeping the original greeting (first message) and recent history
        for (const chat of this.#chats) {
            if (chat.messages.length > maxMessages) {
                const firstMsg = chat.messages[0];
                const recentMsgs = chat.messages.slice(chat.messages.length - (maxMessages - 1));
                chat.messages = [firstMsg, ...recentMsgs];
            }
        }

        localStorage.setItem('dc_chats', JSON.stringify(this.#chats));
        localStorage.setItem('dc_active_chat_id', this.#activeChatId);
    }

    seedDefault() {
        const defaultChat: ChatSession = {
            id: Math.random().toString(),
            title: 'General Security Chat',
            messages: [
                { role: 'agent', text: 'Hello! I am Hawkeye, your local AI security agent. Ask me anything about events detected during this camera session.' }
            ]
        };
        this.#chats = [defaultChat];
        this.#activeChatId = defaultChat.id;
        this.save();
    }

    createNewChat() {
        const newId = Math.random().toString();
        const newChat: ChatSession = {
            id: newId,
            title: `Chat Session ${this.#chats.length + 1}`,
            messages: [
                { role: 'agent', text: 'Hello! I am Hawkeye, your local AI security agent. Ask me anything about events detected during this camera session.' }
            ]
        };
        this.#chats = [...this.#chats, newChat];
        this.#activeChatId = newId;
        this.save();
    }

    deleteChat(id: string) {
        if (this.#chats.length <= 1) {
            alert('Cannot delete the last remaining chat session.');
            return;
        }
        if (!confirm('Are you sure you want to delete this chat session?')) return;
        this.#chats = this.#chats.filter(c => c.id !== id);
        this.#activeChatId = this.#chats[0].id;
        this.save();
    }

    addMessage(chatId: string, role: 'user' | 'agent', text: string) {
        const chatIdx = this.#chats.findIndex(c => c.id === chatId);
        if (chatIdx >= 0) {
            const updatedMessages = [...this.#chats[chatIdx].messages, { role, text }];
            let updatedTitle = this.#chats[chatIdx].title;
            
            // Auto-generate title from first user message if generic
            if ((updatedTitle.startsWith('Chat Session') || updatedTitle === 'General Security Chat') && updatedMessages.length >= 2) {
                const firstUserMsg = updatedMessages.find(m => m.role === 'user');
                if (firstUserMsg) {
                    updatedTitle = firstUserMsg.text.substring(0, 24) + (firstUserMsg.text.length > 24 ? '...' : '');
                }
            }

            this.#chats = [
                ...this.#chats.slice(0, chatIdx),
                { ...this.#chats[chatIdx], title: updatedTitle, messages: updatedMessages },
                ...this.#chats.slice(chatIdx + 1)
            ];
            this.save();
        }
    }
}

export const chatsState = new ChatsState();
