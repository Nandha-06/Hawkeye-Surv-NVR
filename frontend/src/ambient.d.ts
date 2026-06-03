declare module 'node:fs' {
    export function readFileSync(path: string, encoding: string): string;
    export function existsSync(path: string): boolean;
}

declare module 'node:path' {
    export function resolve(...parts: string[]): string;
    export function join(...parts: string[]): string;
}

declare const process: {
    env: { [key: string]: string | undefined };
    cwd(): string;
};
