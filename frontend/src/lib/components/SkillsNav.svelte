<script lang="ts">
    import type { SkillMetadata } from '$lib/types';
    
    let { 
        skills = [], 
        selectedSkillId = $bindable(), 
        activeSkillStatus = 'stopped' 
    } = $props<{
        skills: SkillMetadata[],
        selectedSkillId: string,
        activeSkillStatus: string
    }>();

    let filteredSkills = $derived(skills.filter((s: SkillMetadata) => 
        ['detection', 'analysis', 'privacy'].includes(s.category)
    ));
</script>

<aside class="skills-nav-panel glass-panel">
    <div class="panel-header">
        <h2>AI Engines</h2>
        <span class="badge">{filteredSkills.length}</span>
    </div>
    <div class="skills-list">
        {#each filteredSkills as skill}
            <button 
                onclick={() => { if (activeSkillStatus === 'stopped') selectedSkillId = skill.id; }}
                class="skill-card" 
                class:selected={selectedSkillId === skill.id}
                disabled={activeSkillStatus !== 'stopped'}
            >
                <div class="skill-info">
                    <span class="skill-name">{skill.name}</span>
                    <span class="skill-category category-{skill.category}">{skill.category}</span>
                </div>
                <div class="skill-meta">
                    <span class="skill-version">v{skill.version}</span>
                    <div class="skill-status-indicator">
                        {#if activeSkillStatus !== 'stopped' && selectedSkillId === skill.id}
                            <span class="status-dot pulsing {activeSkillStatus}"></span>
                            <span class="status-text active">{activeSkillStatus}</span>
                        {:else if skill.isInstalled}
                            <span class="status-badge ready">Ready</span>
                        {:else}
                            <span class="status-badge deployable">Deployable</span>
                        {/if}
                    </div>
                </div>
            </button>
        {/each}
    </div>
</aside>

<style>
    .skills-nav-panel {
        display: flex;
        flex-direction: column;
        height: 100%;
        overflow: hidden;
    }

    .panel-header {
        display: flex;
        justify-content: space-between;
        align-items: center;
        padding: 1.25rem 1.5rem;
        border-bottom: 1px solid var(--border-glass);
    }

    .panel-header h2 {
        font-family: var(--font-display);
        font-size: 1.1rem;
        font-weight: 600;
        margin: 0;
        color: var(--text-primary);
    }

    .badge {
        background: rgba(99, 102, 241, 0.15);
        color: var(--accent-indigo);
        padding: 0.2rem 0.6rem;
        border-radius: 12px;
        font-size: 0.75rem;
        font-weight: 600;
    }

    .skills-list {
        flex: 1;
        overflow-y: auto;
        padding: 1rem;
        display: flex;
        flex-direction: column;
        gap: 0.75rem;
    }

    .skill-card {
        background: rgba(255, 255, 255, 0.02);
        border: 1px solid var(--border-glass);
        border-radius: 12px;
        padding: 1rem;
        display: flex;
        flex-direction: column;
        gap: 0.75rem;
        cursor: pointer;
        transition: var(--transition-smooth);
        text-align: left;
        width: 100%;
    }

    .skill-card:hover:not(:disabled) {
        background: rgba(255, 255, 255, 0.05);
        border-color: var(--border-glass-hover);
        transform: translateY(-2px);
    }

    .skill-card.selected {
        background: rgba(99, 102, 241, 0.1);
        border-color: var(--accent-indigo);
        box-shadow: 0 0 15px rgba(99, 102, 241, 0.15);
    }

    .skill-card:disabled {
        opacity: 0.6;
        cursor: not-allowed;
    }

    .skill-info {
        display: flex;
        justify-content: space-between;
        align-items: center;
    }

    .skill-name {
        font-weight: 600;
        font-size: 0.95rem;
        color: var(--text-primary);
    }

    .skill-category {
        font-size: 0.65rem;
        text-transform: uppercase;
        letter-spacing: 0.05em;
        padding: 0.2rem 0.5rem;
        border-radius: 4px;
        font-weight: 700;
    }

    .category-detection { background: rgba(16, 185, 129, 0.15); color: var(--accent-emerald); }
    .category-analysis { background: rgba(6, 182, 212, 0.15); color: var(--accent-cyan); }
    .category-privacy { background: rgba(244, 63, 94, 0.15); color: var(--accent-rose); }
    
    .skill-meta {
        display: flex;
        justify-content: space-between;
        align-items: center;
        font-size: 0.8rem;
    }

    .skill-version {
        color: var(--text-muted);
        font-family: var(--font-mono);
    }

    .skill-status-indicator {
        display: flex;
        align-items: center;
        gap: 0.4rem;
    }

    .status-badge {
        padding: 0.15rem 0.5rem;
        border-radius: 4px;
        font-weight: 600;
        font-size: 0.7rem;
    }

    .status-badge.ready {
        background: rgba(16, 185, 129, 0.1);
        color: var(--accent-emerald);
        border: 1px solid rgba(16, 185, 129, 0.2);
    }

    .status-badge.deployable {
        background: rgba(255, 255, 255, 0.05);
        color: var(--text-secondary);
        border: 1px solid var(--border-glass);
    }

    .status-dot {
        width: 8px;
        height: 8px;
        border-radius: 50%;
    }

    .status-dot.pulsing {
        background: var(--accent-indigo);
        box-shadow: 0 0 8px var(--accent-indigo);
        animation: pulse 1.5s infinite;
    }

    .status-text.active {
        color: var(--accent-indigo);
        font-weight: 600;
        text-transform: capitalize;
    }

    @keyframes pulse {
        0% { opacity: 0.5; transform: scale(0.9); }
        50% { opacity: 1; transform: scale(1.1); }
        100% { opacity: 0.5; transform: scale(0.9); }
    }
</style>
