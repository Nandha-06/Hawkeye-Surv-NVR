<script lang="ts">
    interface TimelineItem {
        id: string;
        timestamp: string;
        camera: string;
        message: string;
        type: string;
    }

    let { 
        vlmTimeline = $bindable([]), 
        isCameraActive = false 
    } = $props<{
        vlmTimeline: TimelineItem[];
        isCameraActive: boolean;
    }>();

    function resetFeed() {
        vlmTimeline = [];
    }
</script>

{#if isCameraActive && vlmTimeline.length > 0}
    <div class="panel glass vlm-timeline-panel">
        <div class="timeline-panel-header">
            <h3>VLM Event Triage Feed</h3>
            <button onclick={resetFeed} class="text-link-btn">Reset Feed</button>
        </div>
        <div class="timeline-items-scroll">
            {#each vlmTimeline as item (item.id)}
                <div class="timeline-item-card {item.type}">
                    <div class="item-meta">
                        <span class="item-time">{item.timestamp}</span>
                        <span class="item-camera">[{item.camera}]</span>
                    </div>
                    <div class="item-body">
                        <span class="item-bullet"></span>
                        <p class="item-msg">{item.message}</p>
                    </div>
                </div>
            {/each}
        </div>
    </div>
{/if}

<style>
    .vlm-timeline-panel {
        padding: 1.25rem;
        background: var(--bg-panel);
        border: 1px solid var(--border-color);
        border-radius: 12px;
        backdrop-filter: var(--glass-blur);
        box-shadow: var(--glass-shadow);
        margin-top: 1.5rem;
    }
    .timeline-panel-header {
        display: flex;
        justify-content: space-between;
        align-items: center;
        margin-bottom: 0.85rem;
    }
    .timeline-panel-header h3 {
        font-family: var(--font-display);
        font-size: 0.85rem;
        font-weight: 700;
        text-transform: uppercase;
        color: var(--text-secondary);
        margin: 0;
        letter-spacing: 0.05em;
    }
    .timeline-items-scroll {
        display: flex;
        flex-direction: column;
        gap: 0.65rem;
        max-height: 160px;
        overflow-y: auto;
        padding-right: 0.25rem;
    }
    .timeline-item-card {
        position: relative;
        padding: 0.65rem 0.85rem;
        background: rgba(255, 255, 255, 0.01);
        border: 1px solid var(--border-color);
        border-radius: 6px;
        display: flex;
        flex-direction: column;
        gap: 0.35rem;
        transition: var(--transition-fast);
    }
    .timeline-item-card:hover {
        background: rgba(255, 255, 255, 0.02);
        border-color: var(--border-color-hover);
    }
    .timeline-item-card.detection {
        border-left: 3px solid var(--accent-secondary);
    }
    .timeline-item-card.transform {
        border-left: 3px solid var(--accent-primary);
    }
    .timeline-item-card.info {
        border-left: 3px solid var(--accent-success);
    }
    .timeline-item-card.warning {
        border-left: 3px solid var(--accent-warning);
    }
    .timeline-item-card.critical {
        border-left: 3px solid var(--accent-error);
        background: rgba(244, 63, 94, 0.04);
        box-shadow: inset 0 0 12px rgba(244, 63, 94, 0.06);
        animation: pulse-critical-card 3s infinite;
    }
    .item-meta {
        display: flex;
        justify-content: space-between;
        font-size: 0.7rem;
        font-family: var(--font-mono);
        color: var(--text-muted);
    }
    .item-camera {
        color: var(--text-secondary);
    }
    .item-body {
        display: flex;
        align-items: center;
        gap: 0.5rem;
    }
    .item-bullet {
        width: 6px;
        height: 6px;
        border-radius: 50%;
        flex-shrink: 0;
    }
    .timeline-item-card.detection .item-bullet { background-color: var(--accent-secondary); }
    .timeline-item-card.transform .item-bullet { background-color: var(--accent-primary); }
    .timeline-item-card.info .item-bullet { background-color: var(--accent-success); }
    .timeline-item-card.warning .item-bullet { background-color: var(--accent-warning); }
    .timeline-item-card.critical .item-bullet {
        background-color: var(--accent-error);
        box-shadow: 0 0 6px var(--accent-error);
        animation: pulse-critical-bullet 1.5s infinite;
    }

    .item-msg {
        font-size: 0.8rem;
        color: var(--text-primary);
        margin: 0;
        line-height: 1.35;
    }

    .text-link-btn {
        background: none;
        border: none;
        color: var(--accent-primary);
        font-size: 0.75rem;
        font-weight: 500;
        cursor: pointer;
        padding: 0;
        transition: color var(--transition-fast);
    }
    .text-link-btn:hover {
        color: var(--text-primary);
        text-decoration: underline;
    }

    @keyframes pulse-critical-card {
        0% { border-color: rgba(244, 63, 94, 0.4); }
        50% { border-color: var(--accent-error); }
        100% { border-color: rgba(244, 63, 94, 0.4); }
    }
    @keyframes pulse-critical-bullet {
        0% { transform: scale(0.9); opacity: 0.6; }
        50% { transform: scale(1.3); opacity: 1; }
        100% { transform: scale(0.9); opacity: 0.6; }
    }
</style>
