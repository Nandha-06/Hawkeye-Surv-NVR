<script lang="ts">
    import { fade, slide } from 'svelte/transition';

    let query = $state('');
    let isSearching = $state(false);
    let isIndexing = $state(false);
    let isDownloading = $state(false);
    let searchResults = $state<any[]>([]);
    let errorMessage = $state('');
    let successMessage = $state('');

    async function handleDownload() {
        isDownloading = true;
        errorMessage = '';
        successMessage = '';
        
        try {
            const res = await fetch('/api/v1/sherlock/download', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' }
            });
            
            const data = await res.json();
            
            if (!res.ok || data.error) {
                throw new Error(data.error || "Model download failed");
            }
            
            successMessage = data.message || "Model downloaded successfully!";
        } catch (e: any) {
            errorMessage = e.message || 'An error occurred during download';
        } finally {
            isDownloading = false;
        }
    }

    async function handleIndex() {
        isIndexing = true;
        errorMessage = '';
        successMessage = '';
        
        try {
            const res = await fetch('/api/v1/sherlock/index', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ backend: "local" })
            });
            
            const data = await res.json();
            
            if (!res.ok || data.error) {
                throw new Error(data.error || "Indexing failed");
            }
            
            successMessage = data.message || "Successfully indexed recordings!";
        } catch (e: any) {
            errorMessage = e.message || 'An error occurred during indexing';
        } finally {
            isIndexing = false;
        }
    }

    async function handleSearch() {
        if (!query.trim()) return;
        isSearching = true;
        errorMessage = '';
        successMessage = '';
        searchResults = [];

        try {
            const res = await fetch('/api/v1/sherlock/search', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ query: query.trim(), limit: "5", backend: "local" })
            });
            
            const data = await res.json();
            
            if (!res.ok || data.error) {
                if (data.error && data.error.includes("Model may not be downloaded")) {
                    throw new Error("Model not downloaded. Please click 'Download AI Model' first.");
                }
                throw new Error(data.error || "Search failed");
            }
            
            if (data.results) {
                searchResults = data.results;
            } else if (data.raw_output) {
                // Fallback parsing just in case
                const lines = data.raw_output.split('\n');
                for (const line of lines) {
                    const match = line.match(/#(\d+)\s+\[([\d.]+)\]\s+(.*?\.mp4)\s+@\s+([\d:]+-[\d:]+)/);
                    if (match) {
                        searchResults.push({
                            rank: match[1],
                            score: parseFloat(match[2]),
                            file: match[3],
                            time: match[4]
                        });
                    }
                }
            }
        } catch (e: any) {
            errorMessage = e.message || 'An error occurred during search';
        } finally {
            isSearching = false;
        }
    }
</script>

<div class="p-8 max-w-5xl mx-auto min-h-screen flex flex-col gap-8" in:fade={{ duration: 200 }}>
    
    <div class="flex flex-col gap-2 text-center mt-12">
        <h1 class="text-4xl font-extrabold tracking-tight text-transparent bg-clip-text bg-gradient-to-r from-cyan to-iris mb-2">
            Sherlock Search
        </h1>
        <p class="text-muted-foreground text-lg max-w-2xl mx-auto">
            Natural language video search powered by SentrySearch. Describe what you're looking for, and AI will find it in your footage.
        </p>
    </div>

    <!-- Search Box -->
    <div class="relative max-w-3xl mx-auto w-full group mt-8">
        <div class="absolute -inset-1 bg-gradient-to-r from-cyan to-iris rounded-2xl blur opacity-25 group-hover:opacity-50 transition duration-1000 group-hover:duration-200"></div>
        <div class="relative bg-surface-2/80 backdrop-blur-xl border border-border/50 rounded-2xl shadow-2xl p-2 flex items-center">
            <div class="p-3 text-cyan">
                {#if isSearching}
                    <svg class="animate-spin h-6 w-6" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24">
                        <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle>
                        <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
                    </svg>
                {:else}
                    <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                        <circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/>
                    </svg>
                {/if}
            </div>
            
            <input 
                type="text" 
                bind:value={query} 
                onkeydown={(e) => e.key === 'Enter' && handleSearch()}
                placeholder="e.g. 'Red truck running a stop sign'" 
                class="flex-1 bg-transparent border-none outline-none text-foreground px-2 py-3 text-lg placeholder:text-muted-foreground/50"
                disabled={isSearching || isIndexing}
            />
            
            <button 
                onclick={handleSearch}
                disabled={isSearching || isIndexing || !query.trim()}
                class="ml-2 px-6 py-3 rounded-xl bg-gradient-to-r from-cyan/20 to-iris/20 text-cyan border border-cyan/30 font-semibold hover:bg-cyan/20 hover:text-white transition-all disabled:opacity-50 disabled:cursor-not-allowed"
            >
                Search
            </button>
        </div>
    </div>

    <!-- Index Actions -->
    <div class="flex justify-center mt-2">
        <button 
            onclick={handleIndex}
            disabled={isIndexing || isSearching}
            class="px-5 py-2 rounded-lg bg-surface flex items-center gap-2 text-sm hover:bg-card-hover border border-border transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
            title="Index all new footage in the Hawkeye recordings directory"
        >
            {#if isIndexing}
                <svg class="animate-spin h-4 w-4 text-cyan" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24">
                    <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle>
                    <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
                </svg>
                Indexing Footage...
            {:else}
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" class="text-iris">
                    <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4M17 8l-5-5-5 5M12 3v12"/>
                </svg>
                Sync & Index Data
            {/if}
        </button>
    </div>

    <!-- Feedback Messages -->
    {#if errorMessage}
        <div transition:slide class="max-w-3xl mx-auto w-full p-4 rounded-xl bg-crimson/10 border border-crimson/30 text-crimson flex items-center gap-3">
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="12"/><line x1="12" y1="16" x2="12.01" y2="16"/></svg>
            <span class="whitespace-pre-line text-sm">{errorMessage}</span>
        </div>
    {/if}
    {#if successMessage}
        <div transition:slide class="max-w-3xl mx-auto w-full p-4 rounded-xl bg-jade/10 border border-jade/30 text-jade flex items-center gap-3">
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"/><polyline points="22 4 12 14.01 9 11.01"/></svg>
            <span class="text-sm">{successMessage}</span>
        </div>
    {/if}

    <!-- Results Area -->
    {#if searchResults.length > 0}
        <div class="max-w-4xl mx-auto w-full mt-8" transition:fade>
            <h3 class="text-xl font-semibold mb-6 flex items-center gap-2">
                <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" class="text-cyan"><polygon points="12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2"/></svg>
                Top Matches
            </h3>
            
            <div class="grid grid-cols-1 gap-4">
                {#each searchResults as result (result.rank)}
                    <div class="group relative overflow-hidden rounded-2xl bg-surface-2 border border-border/50 hover:border-cyan/50 hover:shadow-[0_0_20px_hsl(var(--cyan)/0.15)] transition-all p-4 flex flex-col sm:flex-row gap-6 items-center">
                        <!-- Video Thumbnail Placeholder -->
                        <div class="w-full sm:w-48 h-32 rounded-xl bg-black/40 border border-border flex items-center justify-center shrink-0 relative overflow-hidden">
                            <svg width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" class="text-muted-foreground/50"><polygon points="5 3 19 12 5 21 5 3"/></svg>
                            <div class="absolute bottom-2 right-2 bg-black/60 backdrop-blur-md px-2 py-0.5 rounded text-[10px] font-mono text-white">
                                {result.time}
                            </div>
                        </div>
                        
                        <!-- Info -->
                        <div class="flex-1 min-w-0">
                            <div class="flex items-center gap-2 mb-2">
                                <span class="bg-iris/20 text-iris px-2 py-0.5 rounded text-xs font-bold">#{result.rank}</span>
                                <span class="text-sm font-mono text-muted-foreground">Score: <span class={result.score > 0.5 ? 'text-jade' : 'text-amber'}>{result.score.toFixed(2)}</span></span>
                            </div>
                            <h4 class="text-lg font-medium text-foreground truncate" title={result.file}>{result.file}</h4>
                            <p class="text-sm text-muted-foreground mt-1 line-clamp-2">Clip extracted from footage matching your query.</p>
                            
                            <div class="mt-4 flex gap-3">
                                <button class="px-4 py-2 rounded-lg bg-surface flex items-center gap-2 text-sm hover:bg-card-hover border border-border transition-colors">
                                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M5 12h14M12 5l7 7-7 7"/></svg>
                                    Play Clip
                                </button>
                                <button class="px-4 py-2 rounded-lg bg-surface flex items-center gap-2 text-sm hover:bg-card-hover border border-border transition-colors">
                                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4M7 10l5 5 5-5M12 15V3"/></svg>
                                    Export
                                </button>
                            </div>
                        </div>
                    </div>
                {/each}
            </div>
        </div>
    {/if}

</div>
