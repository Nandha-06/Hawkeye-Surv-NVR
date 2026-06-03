import { json } from '@sveltejs/kit';
import { readFileSync, existsSync } from 'node:fs';
import { resolve } from 'node:path';
import type { RequestHandler } from './$types';

const TOKEN_FILENAME = '.local_api_token';

function readToken(): string | null {
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

export const GET: RequestHandler = async () => {
    const t = readToken();
    if (!t) {
        return json({ token: null }, { status: 503 });
    }
    return json({ token: t });
};
