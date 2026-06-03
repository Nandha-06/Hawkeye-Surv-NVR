import { browser } from '$app/environment';

let cachedToken: string | null = null;
let pendingFetch: Promise<string | null> | null = null;

export async function getApiToken(): Promise<string | null> {
    if (!browser) return null;
    if (cachedToken) return cachedToken;
    if (pendingFetch) return pendingFetch;

    pendingFetch = (async () => {
        try {
            const resp = await fetch('/api/local-token', { credentials: 'include' });
            if (!resp.ok) return null;
            const data = await resp.json();
            cachedToken = typeof data?.token === 'string' ? data.token : null;
            return cachedToken;
        } catch {
            return null;
        } finally {
            pendingFetch = null;
        }
    })();
    return pendingFetch;
}

export function buildWsUrl(path: string, token: string | null): string {
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const base = `${protocol}//${window.location.host}${path}`;
    if (!token) return base;
    return `${base}?token=${encodeURIComponent(token)}`;
}
