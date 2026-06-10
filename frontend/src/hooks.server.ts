import type { Handle, HandleFetch } from '@sveltejs/kit';
import { readFileSync, existsSync } from 'node:fs';
import { resolve } from 'node:path';

const TOKEN_FILENAME = '.local_api_token';

function readApiToken(): string | null {
    if (process.env.HAWKEYE_API_TOKEN) {
        return process.env.HAWKEYE_API_TOKEN;
    }
    const candidates = [
        resolve(process.cwd(), '.data', TOKEN_FILENAME),
        resolve(process.cwd(), '..', '.data', TOKEN_FILENAME),
        resolve(process.cwd(), '..', '..', '.data', TOKEN_FILENAME),
    ];
    for (const p of candidates) {
        if (existsSync(p)) {
            try {
                const t = readFileSync(p, 'utf-8').trim();
                if (t && t.length >= 32) return t;
            } catch {
                continue;
            }
        }
    }
    return null;
}

const apiToken = readApiToken();

export const handle: Handle = async ({ event, resolve }) => {
    if (apiToken) {
        event.request.headers.set('X-Local-Token', apiToken);
    }
    return resolve(event);
};

export const handleFetch: HandleFetch = async ({ request, fetch }) => {
    const port = process.env.HAWKEYE_PORT || '8080';
    if (apiToken && request.url.startsWith(`http://127.0.0.1:${port}`)) {
        const headers = new Headers(request.headers);
        if (!headers.has('X-Local-Token')) {
            headers.set('X-Local-Token', apiToken);
        }
        request = new Request(request, { headers });
    }
    return fetch(request);
};
