<script lang="ts">
    import { getApiToken } from '$lib/apiToken';

    let { src, alt = "", class: className = "" } = $props<{ src: string, alt?: string, class?: string }>();

    let objectUrl = $state<string | null>(null);

    $effect(() => {
        let currentSrc = src;
        if (!currentSrc) return;

        let active = true;

        (async () => {
            try {
                const token = await getApiToken();
                const headers: Record<string, string> = {};
                if (token) headers['X-Local-Token'] = token;

                const cleanUrl = currentSrc.split('&token=')[0].split('?token=')[0];

                const response = await fetch(cleanUrl, { headers });
                if (!active) return;

                if (response.ok) {
                    const blob = await response.blob();
                    if (!active) return;
                    if (objectUrl) URL.revokeObjectURL(objectUrl);
                    objectUrl = URL.createObjectURL(blob);
                }
            } catch (e) {
                console.error('Failed to load image:', e);
            }
        })();

        return () => {
            active = false;
            if (objectUrl) {
                URL.revokeObjectURL(objectUrl);
                objectUrl = null;
            }
        };
    });
</script>

{#if objectUrl}
    <img src={objectUrl} {alt} class={className} />
{:else}
    <div class="bg-muted flex items-center justify-center animate-pulse {className}">
        <!-- placeholder -->
    </div>
{/if}
