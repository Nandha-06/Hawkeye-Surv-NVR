<script lang="ts">
  import * as Sidebar from "$lib/components/ui/sidebar/index.js";
  import { page } from "$app/stores";
  import { LayoutDashboard, Activity, Bell, FileText, Users, MessageSquareShare, Video, ActivitySquare, Settings } from "lucide-svelte";
  import { cn } from "$lib/utils.js";
  import { useSidebar } from "$lib/components/ui/sidebar/context.svelte.js";
  
  let { chatOpen = $bindable() } = $props();
  const sidebar = useSidebar();

  const navItems = [
    { href: '/', label: 'Overview', icon: LayoutDashboard },
    { href: '/monitoring', label: 'Live Grid', icon: Activity },
    { href: '/events', label: 'Event Detections', icon: Bell },
    { href: '/review', label: 'Review Center', icon: FileText },
    { href: '/identities', label: 'Face Re-ID Database', icon: Users },
    { href: '/chat', label: 'Security Chat', icon: MessageSquareShare },
    { href: '/cameras', label: 'Cameras Config', icon: Video },
    { href: '/system', label: 'System Diagnostics', icon: ActivitySquare },
    { href: '/settings', label: 'System Config', icon: Settings }
  ];
</script>

<Sidebar.Root collapsible="icon">
  <Sidebar.Header class={sidebar.state === "expanded" ? "py-3 px-3" : "py-3 flex justify-center"}>
    <Sidebar.Menu>
      <Sidebar.MenuItem>
        <Sidebar.MenuButton size="lg" class="hover:bg-transparent! active:bg-transparent! flex items-center justify-center">
          <!-- Custom Hawkeye Brand Eye -->
          <div class="relative flex aspect-square size-8 items-center justify-center rounded-lg bg-gradient-to-br from-indigo-950/60 to-slate-900 border border-indigo-500/25 shadow-md shadow-indigo-950/40 overflow-hidden group/eye shrink-0">
            <!-- Animated radial scanner pulse -->
            <div class="absolute inset-0 bg-[radial-gradient(circle_at_center,rgba(99,102,241,0.2)_0%,transparent_75%)]"></div>
            
            <svg viewBox="0 0 24 24" class="size-5 text-indigo-400 drop-shadow-[0_0_6px_rgba(99,102,241,0.65)] group-hover/eye:text-indigo-300 transition-colors duration-300" fill="none" stroke="currentColor" stroke-width="2">
              <!-- Stylized Cyber Eye contour -->
              <path d="M2 12C2 12 5.63636 5 12 5C18.3636 5 22 12 22 12C22 12 18.3636 19 12 19C5.63636 19 2 12 2 12Z" stroke-linecap="round" stroke-linejoin="round" />
              <!-- Radar Dotted ring -->
              <circle cx="12" cy="12" r="4.5" stroke="currentColor" stroke-dasharray="3 2" class="opacity-60 animate-[spin_20s_linear_infinite]" />
              <!-- Central Pupil -->
              <circle cx="12" cy="12" r="2" fill="currentColor" class="text-indigo-400" />
              <!-- Glint -->
              <circle cx="13.2" cy="10.8" r="0.6" fill="white" class="opacity-90" />
            </svg>
          </div>
          <div class="grid flex-1 text-left text-sm leading-tight ml-2 group-data-[collapsible=icon]:hidden">
            <span class="truncate font-bold bg-gradient-to-r from-indigo-100 to-indigo-300 bg-clip-text text-transparent font-display tracking-wide">HAWKEYE</span>
            <span class="truncate text-[10px] text-muted-foreground font-mono uppercase tracking-wider font-semibold">Security AI</span>
          </div>
        </Sidebar.MenuButton>
      </Sidebar.MenuItem>
    </Sidebar.Menu>
  </Sidebar.Header>
  <Sidebar.Content>
    <Sidebar.Group>
      <Sidebar.GroupLabel class="px-4 text-[9px] uppercase tracking-widest font-bold text-muted-foreground/60 select-none">Platform</Sidebar.GroupLabel>
      <Sidebar.Menu>
        {#each navItems as item}
          {@const isActive = $page.url.pathname === item.href || ($page.url.pathname.startsWith(item.href) && item.href !== '/')}
          {@const Icon = item.icon}
          <Sidebar.MenuItem class={sidebar.state === "expanded" ? "px-2 my-0.5" : "px-0 my-0.5"}>
            <Sidebar.MenuButton 
              isActive={isActive} 
              tooltipContent={item.label}
              class="rounded-full px-4 h-9.5 border border-transparent transition-all duration-200
                     data-[active=true]:bg-indigo-500/10 data-[active=true]:border-indigo-500/20 data-[active=true]:text-indigo-400 data-[active=true]:shadow-[inset_0_1.5px_0_rgba(255,255,255,0.06),0_2px_8px_-1px_rgba(99,102,241,0.18)]
                     hover:rounded-full hover:bg-muted/50 hover:text-foreground
                     data-[active=true]:hover:bg-indigo-500/15 data-[active=true]:hover:text-indigo-300"
            >
              {#snippet child({ props }: { props: any })}
                <a href={item.href} {...props} class={cn("flex items-center gap-3 w-full h-full justify-start group-data-[collapsible=icon]:justify-center", props.class)}>
                  <Icon class="size-4 shrink-0 transition-transform duration-200 group-hover/menu-button:scale-110" />
                  <span class="text-xs font-semibold tracking-wide font-sans group-data-[collapsible=icon]:hidden">{item.label}</span>
                </a>
              {/snippet}
            </Sidebar.MenuButton>
          </Sidebar.MenuItem>
        {/each}
      </Sidebar.Menu>
    </Sidebar.Group>
  </Sidebar.Content>
  <Sidebar.Footer>
    <Sidebar.Menu>
      <Sidebar.MenuItem class={sidebar.state === "expanded" ? "px-2 my-2" : "px-0 my-2"}>
        <!-- The global Ask Hawkeye AI Chat Sidebar Item -->
        <Sidebar.MenuButton 
          tooltipContent="Security Assistant" 
          onclick={() => chatOpen = !chatOpen}
          class="rounded-full px-4 h-9.5 border border-indigo-500/15 bg-indigo-950/20 text-indigo-400 hover:bg-indigo-950/35 hover:border-indigo-500/30 hover:text-indigo-300 hover:rounded-full shadow-sm transition-all duration-300"
        >
          {#snippet child({ props }: { props: any })}
             <button {...props} class={cn("w-full flex items-center gap-3 justify-start group-data-[collapsible=icon]:justify-center", props.class)}>
               <!-- Custom AI Chat Symbol -->
               <div class="relative flex items-center justify-center shrink-0">
                 <svg viewBox="0 0 24 24" class="size-4 text-indigo-400 drop-shadow-[0_0_3px_rgba(99,102,241,0.55)] transition-colors duration-300" fill="none" stroke="currentColor" stroke-width="2">
                   <!-- Rounded Chat Bubble -->
                   <path d="M21 11.5C21 15.642 16.97 19 12 19C10.237 19 8.601 18.423 7.25 17.425L3 19L4.35 15.175C3.13 14.12 2.38 12.631 2.38 11C2.38 6.858 6.41 3.5 11.38 3.5C16.35 3.5 20.38 6.858 20.38 11" stroke-linecap="round" stroke-linejoin="round" class="opacity-90" />
                   <!-- Sparkle 1 -->
                   <path d="M9.5 8c0 0.414-0.336 0.75-0.75 0.75c0.414 0 0.75 0.336 0.75 0.75c0-0.414 0.336-0.75 0.75-0.75c-0.414 0-0.75-0.336-0.75-0.75Z" fill="currentColor" stroke="none" />
                   <!-- Sparkle 2 -->
                   <path d="M14.5 11.5c0 0.69-0.56 1.25-1.25 1.25c0.69 0 1.25 0.56 1.25 1.25c0-0.69 0.56-1.25 1.25-1.25c-0.69 0-1.25-0.56-1.25-1.25Z" fill="currentColor" stroke="none" />
                 </svg>
                 <!-- Pulsing indigo active dot in corner -->
                 <span class="absolute -top-0.5 -right-0.5 w-1.5 h-1.5 rounded-full bg-indigo-500 shadow-[0_0_4px_#6366f1] animate-pulse"></span>
               </div>
               <span class="text-xs font-bold font-sans tracking-wide group-data-[collapsible=icon]:hidden">Ask Assistant</span>
             </button>
          {/snippet}
        </Sidebar.MenuButton>
      </Sidebar.MenuItem>
    </Sidebar.Menu>
  </Sidebar.Footer>
  <Sidebar.Rail />
</Sidebar.Root>