<script lang="ts">
    import { page } from '$app/stores';
    import { cn } from '$lib/utils';

    let { chatOpen = $bindable() } = $props();

    const navItems = [
        {
            group: 'Operations',
            items: [
                { href: '/', label: 'Overview', glyph: 'overview' },
                { href: '/monitoring', label: 'Live Grid', glyph: 'monitor' },
                { href: '/events', label: 'Events', glyph: 'events' }
            ]
        },
        {
            group: 'Intelligence',
            items: [
                { href: '/review', label: 'Review', glyph: 'review' },
                { href: '/identities', label: 'Identities', glyph: 'identities' },
                { href: '/chat', label: 'Agent Chat', glyph: 'chat' }
            ]
        },
        {
            group: 'Platform',
            items: [
                { href: '/cameras', label: 'Cameras', glyph: 'cameras' },
                { href: '/system', label: 'Diagnostics', glyph: 'system' },
                { href: '/settings', label: 'Settings', glyph: 'settings' }
            ]
        }
    ];

    function isActive(href: string, pathname: string) {
        if (href === '/') return pathname === '/';
        return pathname === href || pathname.startsWith(href + '/');
    }
</script>

<aside class="hidden md:flex w-[232px] shrink-0 flex-col border-r border-border bg-card/40 backdrop-blur-sm h-screen sticky top-0">
    <!-- Brand -->
    <div class="h-14 px-4 flex items-center gap-2.5 border-b border-border shrink-0">
        <div class="relative w-8 h-8 rounded-lg overflow-hidden bg-gradient-to-br from-[#0e1a26] to-[#061018] border border-cyan/30 shrink-0">
            <div class="absolute inset-0 bg-[radial-gradient(circle_at_30%_30%,hsl(var(--cyan)/0.4)_0%,transparent_60%)]"></div>
            <svg viewBox="0 0 24 24" class="absolute inset-0 w-full h-full p-1.5 text-cyan drop-shadow-[0_0_4px_hsl(var(--cyan)/0.6)]" fill="none" stroke="currentColor" stroke-width="1.8">
                <path d="M2 12C2 12 5.63636 5 12 5C18.3636 5 22 12 22 12C22 12 18.3636 19 12 19C5.63636 19 2 12 2 12Z" stroke-linecap="round" stroke-linejoin="round"/>
                <circle cx="12" cy="12" r="4.5" stroke="currentColor" stroke-dasharray="3 2" class="opacity-70" style="transform-origin: 12px 12px; animation: spin 20s linear infinite;"/>
                <circle cx="12" cy="12" r="1.8" fill="currentColor"/>
                <circle cx="13.2" cy="10.8" r="0.5" fill="white" class="opacity-90"/>
            </svg>
        </div>
        <div class="min-w-0">
            <p class="text-sm font-bold font-display tracking-tight text-foreground leading-none">HAWKEYE</p>
            <p class="text-[10px] text-muted-foreground uppercase tracking-wider font-semibold mt-0.5 leading-none">Mission Control</p>
        </div>
    </div>

    <!-- Nav -->
    <nav class="flex-1 overflow-y-auto p-3 flex flex-col gap-5">
        {#each navItems as group}
            <div class="flex flex-col gap-1.5">
                <p class="px-2.5 text-[10px] font-bold uppercase tracking-[0.15em] text-muted-foreground/60">{group.group}</p>
                {#each group.items as item}
                    {@const active = isActive(item.href, $page.url.pathname)}
                    <a
                        href={item.href}
                        class={cn(
                            "group relative flex items-center gap-2.5 h-9 px-2.5 rounded-lg text-xs font-semibold transition-all duration-200",
                            active
                                ? "bg-cyan/10 text-cyan"
                                : "text-muted-foreground hover:text-foreground hover:bg-card-hover"
                        )}
                    >
                        {#if active}
                            <span class="absolute left-0 top-1/2 -translate-y-1/2 w-0.5 h-5 bg-cyan rounded-r-full shadow-[0_0_8px_hsl(var(--cyan)/0.6)]"></span>
                        {/if}

                        <!-- Icon glyphs -->
                        {#if item.glyph === 'overview'}
                            <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                                <rect x="3" y="3" width="7" height="9"/><rect x="14" y="3" width="7" height="5"/>
                                <rect x="14" y="12" width="7" height="9"/><rect x="3" y="16" width="7" height="5"/>
                            </svg>
                        {:else if item.glyph === 'monitor'}
                            <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                                <rect x="2" y="3" width="20" height="14" rx="2"/><line x1="8" y1="21" x2="16" y2="21"/><line x1="12" y1="17" x2="12" y2="21"/>
                            </svg>
                        {:else if item.glyph === 'events'}
                            <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                                <path d="M6 8a6 6 0 0 1 12 0c0 7 3 9 3 9H3s3-2 3-9"/>
                                <path d="M10.3 21a1.94 1.94 0 0 0 3.4 0"/>
                            </svg>
                        {:else if item.glyph === 'review'}
                            <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                                <polygon points="23 7 16 12 23 17 23 7"/><rect x="1" y="5" width="15" height="14" rx="2" ry="2"/>
                            </svg>
                        {:else if item.glyph === 'identities'}
                            <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                                <path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"/>
                                <circle cx="12" cy="7" r="4"/>
                            </svg>
                        {:else if item.glyph === 'chat'}
                            <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                                <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/>
                            </svg>
                        {:else if item.glyph === 'cameras'}
                            <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                                <path d="M23 19a2 2 0 0 1-2 2H3a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h4l2-3h6l2 3h4a2 2 0 0 1 2 2z"/>
                                <circle cx="12" cy="13" r="4"/>
                            </svg>
                        {:else if item.glyph === 'system'}
                            <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                                <path d="M22 12h-4l-3 9L9 3l-3 9H2"/>
                            </svg>
                        {:else if item.glyph === 'settings'}
                            <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                                <circle cx="12" cy="12" r="3"/>
                                <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z"/>
                            </svg>
                        {/if}

                        <span>{item.label}</span>
                    </a>
                {/each}
            </div>
        {/each}
    </nav>

    <!-- Bottom: system status -->
    <div class="p-3 border-t border-border shrink-0">
        <div class="rounded-lg bg-surface-2 border border-border p-3 flex flex-col gap-2">
            <div class="flex items-center justify-between">
                <p class="text-[10px] font-bold uppercase tracking-wider text-muted-foreground">Engine</p>
                <span class="badge badge-jade !h-4 !text-[9px]">
                    <span class="w-1 h-1 rounded-full bg-jade status-pulse"></span>
                    Active
                </span>
            </div>
            <p class="text-[11px] text-muted-foreground leading-snug">
                <span class="font-mono font-semibold text-foreground">v0.4.2</span> · perception-core
            </p>
            <div class="flex items-center gap-1.5 mt-1">
                <div class="flex-1 h-1 rounded-full bg-muted overflow-hidden">
                    <div class="h-full bg-gradient-to-r from-cyan to-iris rounded-full" style="width: 78%"></div>
                </div>
                <span class="text-[10px] font-mono font-bold text-muted-foreground">78%</span>
            </div>
        </div>
    </div>
</aside>
