<script lang="ts">
    import type { SkillMetadata } from '$lib/types';

    let {
        selectedSkill,
        configValues = $bindable({}),
        activeSkillStatus = 'stopped',
        isDeploying = false,
        latencyStats = {},
        deploySkill,
        startSkill,
        stopSkill
    } = $props<{
        selectedSkill: SkillMetadata | null;
        configValues: Record<string, any>;
        activeSkillStatus: string;
        isDeploying: boolean;
        latencyStats: Record<string, any>;
        deploySkill: () => void;
        startSkill: () => void;
        stopSkill: () => void;
    }>();

    let activeGroups = $state({
        model: true,
        display: true,
        performance: false
    });

    function getParamGroup(paramName: string): 'model' | 'display' | 'performance' {
        if (['model_size', 'model', 'confidence', 'variant', 'enable_motion_gating', 'motion_threshold', 'min_motion_area'].includes(paramName)) {
            return 'model';
        }
        if (['blend_mode', 'opacity', 'colormap', 'classes'].includes(paramName)) {
            return 'display';
        }
        return 'performance';
    }

    function getPercentWidth(stageKey: string): number {
        const val = latencyStats[stageKey]?.p50;
        const total = latencyStats.total?.p50 || 1;
        if (val === undefined) return 0;
        return Math.max(2, Math.round((val / total) * 100));
    }
</script>

<aside class="panel-right">
    <!-- Section A: Skill Control / Lifecycle -->
    <div class="panel glass section-card">
        <div class="panel-header-sub">
            <h3>Skill Operations</h3>
        </div>
        
        {#if selectedSkill}
            <div class="lifecycle-box">
                {#if !selectedSkill.isInstalled}
                    <button onclick={deploySkill} class="btn btn-accent btn-full" disabled={isDeploying}>
                        Deploy Dependencies
                    </button>
                {:else}
                    <div class="operations-grid">
                        {#if activeSkillStatus === 'stopped' || activeSkillStatus === 'error'}
                            <button onclick={startSkill} class="btn btn-accent btn-full">
                                Start Engine
                            </button>
                        {:else}
                            <button onclick={stopSkill} class="btn btn-rose btn-full" disabled={activeSkillStatus === 'starting'}>
                                {#if activeSkillStatus === 'starting'}Launching...{:else}Stop Engine{/if}
                            </button>
                        {/if}
                    </div>
                {/if}
            </div>
        {/if}
    </div>

    <!-- Section B: Collapsible Settings Accordions -->
    {#if selectedSkill && selectedSkill.isInstalled}
        <div class="panel glass section-card">
            <div class="panel-header-sub">
                <h3>Parameters Inspector</h3>
            </div>

            <div class="accordion-container">
                <!-- Group 1: Model Settings -->
                <div class="accordion-item" class:open={activeGroups.model}>
                    <button class="accordion-header" onclick={() => activeGroups.model = !activeGroups.model}>
                        <span>⚙️ Model & Inference</span>
                        <span class="chevron">{activeGroups.model ? '▼' : '▶'}</span>
                    </button>
                    
                    {#if activeGroups.model}
                        <div class="accordion-content">
                            {#each selectedSkill.configParams.filter((p: any) => getParamGroup(p.name) === 'model') as param}
                                <div class="config-input-group">
                                    <div class="input-label-row">
                                        <span class="input-title">{param.label}</span>
                                        {#if param.type === 'number'}
                                            <span class="badge-value">{configValues[param.name] ?? param.default}</span>
                                        {/if}
                                    </div>

                                    {#if param.type === 'select'}
                                        <select bind:value={configValues[param.name]} class="styled-select" disabled={activeSkillStatus !== 'stopped'}>
                                            {#each param.options || [] as opt}
                                                {#if typeof opt === 'object'}
                                                    <option value={opt.value}>{opt.label}</option>
                                                {:else}
                                                    <option value={opt}>{opt}</option>
                                                {/if}
                                            {/each}
                                        </select>
                                    {:else if param.type === 'boolean'}
                                        <label class="toggle-switch-label">
                                            <input type="checkbox" bind:checked={configValues[param.name]} class="styled-toggle" disabled={activeSkillStatus !== 'stopped'} />
                                            <span class="toggle-lbl">Enable</span>
                                        </label>
                                    {:else if param.type === 'number'}
                                        <input 
                                            type="range" 
                                            min={param.min ?? 0.1} 
                                            max={param.max ?? 1.0} 
                                            step="0.05"
                                            bind:value={configValues[param.name]}
                                            class="styled-range"
                                            disabled={activeSkillStatus !== 'stopped'}
                                        />
                                    {/if}
                                    
                                    <p class="help-desc">{param.description}</p>
                                </div>
                            {/each}
                        </div>
                    {/if}
                </div>

                <!-- Group 2: Display & Overlay -->
                {#if selectedSkill.configParams.some((p: any) => getParamGroup(p.name) === 'display')}
                    <div class="accordion-item" class:open={activeGroups.display}>
                        <button class="accordion-header" onclick={() => activeGroups.display = !activeGroups.display}>
                            <span>📺 Display & Overlay</span>
                            <span class="chevron">{activeGroups.display ? '▼' : '▶'}</span>
                        </button>
                        
                        {#if activeGroups.display}
                            <div class="accordion-content">
                                {#each selectedSkill.configParams.filter((p: any) => getParamGroup(p.name) === 'display') as param}
                                    <div class="config-input-group">
                                        <div class="input-label-row">
                                            <span class="input-title">{param.label}</span>
                                            {#if param.type === 'number'}
                                                <span class="badge-value">{configValues[param.name] ?? param.default}</span>
                                            {/if}
                                        </div>

                                        {#if param.type === 'select'}
                                            <select bind:value={configValues[param.name]} class="styled-select" disabled={activeSkillStatus !== 'stopped'}>
                                                {#each param.options || [] as opt}
                                                    {#if typeof opt === 'object'}
                                                        <option value={opt.value}>{opt.label}</option>
                                                    {:else}
                                                        <option value={opt}>{opt}</option>
                                                    {/if}
                                                {/each}
                                            </select>
                                        {:else if param.type === 'number'}
                                            <input 
                                                type="range" 
                                                min={param.min ?? 0.1} 
                                                max={param.max ?? 1.0} 
                                                step="0.05"
                                                bind:value={configValues[param.name]}
                                                class="styled-range"
                                                disabled={activeSkillStatus !== 'stopped'}
                                            />
                                        {:else if param.type === 'boolean'}
                                            <label class="toggle-switch-label">
                                                <input type="checkbox" bind:checked={configValues[param.name]} class="styled-toggle" disabled={activeSkillStatus !== 'stopped'} />
                                                <span class="toggle-lbl">Enable</span>
                                            </label>
                                        {:else}
                                            <input 
                                                type="text" 
                                                bind:value={configValues[param.name]}
                                                class="styled-input"
                                                disabled={activeSkillStatus !== 'stopped'}
                                            />
                                        {/if}
                                        
                                        <p class="help-desc">{param.description}</p>
                                    </div>
                                  {/each}
                            </div>
                        {/if}
                    </div>
                {/if}

                <!-- Group 3: Settings & Integration -->
                {#if selectedSkill.configParams.some((p: any) => getParamGroup(p.name) === 'performance')}
                    <div class="accordion-item" class:open={activeGroups.performance}>
                        <button class="accordion-header" onclick={() => activeGroups.performance = !activeGroups.performance}>
                            <span>⚙️ Settings & Integration</span>
                            <span class="chevron">{activeGroups.performance ? '▼' : '▶'}</span>
                        </button>
                        
                        {#if activeGroups.performance}
                            <div class="accordion-content">
                                {#each selectedSkill.configParams.filter((p: any) => getParamGroup(p.name) === 'performance') as param}
                                    <div class="config-input-group">
                                        <div class="input-label-row">
                                            <span class="input-title">{param.label}</span>
                                        </div>

                                        {#if param.type === 'select'}
                                            <select bind:value={configValues[param.name]} class="styled-select" disabled={activeSkillStatus !== 'stopped'}>
                                                {#each param.options || [] as opt}
                                                    {#if typeof opt === 'object'}
                                                        <option value={opt.value}>{opt.label}</option>
                                                    {:else}
                                                        <option value={opt}>{opt}</option>
                                                    {/if}
                                                {/each}
                                            </select>
                                        {:else if param.type === 'boolean'}
                                            <label class="toggle-switch-label">
                                                <input type="checkbox" bind:checked={configValues[param.name]} class="styled-toggle" disabled={activeSkillStatus !== 'stopped'} />
                                                <span class="toggle-lbl">Enable</span>
                                            </label>
                                        {:else if param.type === 'number'}
                                            <input 
                                                type="number" 
                                                bind:value={configValues[param.name]}
                                                class="styled-input"
                                                min={param.min}
                                                max={param.max}
                                                disabled={activeSkillStatus !== 'stopped'}
                                            />
                                        {:else if param.type === 'password'}
                                            <input 
                                                type="password" 
                                                bind:value={configValues[param.name]}
                                                class="styled-input"
                                                disabled={activeSkillStatus !== 'stopped'}
                                            />
                                        {:else}
                                            <input 
                                                type="text" 
                                                bind:value={configValues[param.name]}
                                                class="styled-input"
                                                placeholder={param.placeholder || ''}
                                                disabled={activeSkillStatus !== 'stopped'}
                                            />
                                        {/if}
                                        
                                        <p class="help-desc">{param.description}</p>
                                    </div>
                                {/each}
                            </div>
                        {/if}
                    </div>
                {/if}
            </div>
        </div>
    {/if}

    <!-- Section C: Visual Performance timelines -->
    <div class="panel glass section-card timing-profile-card">
        <div class="panel-header-sub">
            <h3>Computation Timeline</h3>
        </div>
        
        {#if latencyStats.total?.p50 !== undefined}
            <div class="timeline-visualization">
                <!-- Segmented timing bar -->
                <div class="segmented-track-bar">
                    <div class="bar-segment file-read-segment" style="width: {getPercentWidth('file_read')}%" title="File Read: {latencyStats.file_read?.p50}ms"></div>
                    <div class="bar-segment inference-segment" style="width: {getPercentWidth('inference')}%" title="Inference: {latencyStats.inference?.p50}ms"></div>
                    <div class="bar-segment postprocess-segment" style="width: {getPercentWidth('postprocess')}%" title="Postprocess: {latencyStats.postprocess?.p50}ms"></div>
                </div>

                <!-- Custom Legends with values -->
                <div class="timeline-legend">
                    <div class="legend-row">
                        <span class="legend-dot file-read"></span>
                        <span class="legend-label">File I/O</span>
                        <span class="legend-val">{latencyStats.file_read?.p50} ms</span>
                    </div>
                    <div class="legend-row">
                        <span class="legend-dot inference"></span>
                        <span class="legend-label">AI Inference</span>
                        <span class="legend-val">{latencyStats.inference?.p50} ms</span>
                    </div>
                    <div class="legend-row">
                        <span class="legend-dot postprocess"></span>
                        <span class="legend-label">Postprocess</span>
                        <span class="legend-val">{latencyStats.postprocess?.p50} ms</span>
                    </div>
                </div>

                <!-- Overall latency summary -->
                <div class="total-latency-banner">
                    <span class="label">Total P95 Delay:</span>
                    <span class="value">{latencyStats.total?.p95} ms</span>
                </div>
            </div>
        {:else}
            <div class="empty-profile-placeholder">
                <p>Start AI execution to display visual computation diagnostics.</p>
            </div>
        {/if}
    </div>
</aside>

<style>
    .panel-right {
        display: flex;
        flex-direction: column;
        gap: 1.25rem;
        overflow-y: auto;
    }

    .panel {
        background: var(--bg-panel);
        border: 1px solid var(--border-color);
        border-radius: 12px;
        backdrop-filter: var(--glass-blur);
        box-shadow: var(--glass-shadow);
        transition: border-color var(--transition-fast);
    }

    .section-card {
        padding: 1.25rem;
        display: flex;
        flex-direction: column;
        gap: 0.85rem;
    }

    .panel-header-sub h3 {
        font-family: var(--font-display);
        font-size: 0.85rem;
        font-weight: 700;
        text-transform: uppercase;
        letter-spacing: 0.05em;
        color: var(--text-secondary);
        margin: 0;
        border-bottom: 1px solid var(--border-color);
        padding-bottom: 0.5rem;
    }

    /* Accordion Groups */
    .accordion-container {
        display: flex;
        flex-direction: column;
        gap: 0.75rem;
    }

    .accordion-item {
        border: 1px solid var(--border-color);
        border-radius: 8px;
        overflow: hidden;
        background-color: rgba(255, 255, 255, 0.01);
        transition: var(--transition-fast);
    }

    .accordion-item.open {
        border-color: rgba(255, 255, 255, 0.08);
    }

    .accordion-header {
        width: 100%;
        background: none;
        border: none;
        color: var(--text-primary);
        font-weight: 600;
        font-size: 0.8rem;
        display: flex;
        justify-content: space-between;
        align-items: center;
        padding: 0.75rem 0.9rem;
        cursor: pointer;
        outline: none;
    }

    .accordion-header:hover {
        background-color: rgba(255, 255, 255, 0.02);
    }

    .chevron {
        font-size: 0.65rem;
        color: var(--text-muted);
    }

    .accordion-content {
        padding: 0.9rem;
        border-top: 1px solid var(--border-color);
        background-color: rgba(0, 0, 0, 0.15);
        display: flex;
        flex-direction: column;
        gap: 0.95rem;
    }

    .config-input-group {
        display: flex;
        flex-direction: column;
        gap: 0.35rem;
    }

    .input-label-row {
        display: flex;
        justify-content: space-between;
        align-items: center;
    }

    .input-title {
        font-size: 0.75rem;
        font-weight: 550;
        color: var(--text-secondary);
    }

    .badge-value {
        font-size: 0.7rem;
        font-family: var(--font-mono);
        color: var(--accent-primary);
        background-color: rgba(99, 102, 241, 0.12);
        padding: 0.1rem 0.35rem;
        border-radius: 4px;
        font-weight: bold;
    }

    .help-desc {
        font-size: 0.65rem;
        color: var(--text-muted);
        line-height: 1.35;
        margin: 0;
    }

    /* Form Fields */
    .styled-select, .styled-input {
        background-color: var(--bg-control);
        color: var(--text-primary);
        border: 1px solid var(--border-color);
        border-radius: 6px;
        padding: 0.5rem 0.6rem;
        font-family: var(--font-sans);
        font-size: 0.8rem;
        outline: none;
        width: 100%;
        transition: var(--transition-fast);
    }

    .styled-select:focus, .styled-input:focus {
        border-color: var(--border-focus);
    }

    .styled-range {
        -webkit-appearance: none;
        appearance: none;
        width: 100%;
        height: 4px;
        background: var(--border-color);
        border-radius: 2px;
        outline: none;
    }

    .styled-range::-webkit-slider-thumb {
        -webkit-appearance: none;
        appearance: none;
        width: 14px;
        height: 14px;
        border-radius: 50%;
        background: var(--accent-primary);
        cursor: pointer;
        box-shadow: 0 0 6px var(--accent-primary);
        transition: var(--transition-fast);
    }

    .styled-range::-webkit-slider-thumb:hover {
        transform: scale(1.15);
    }

    .toggle-switch-label {
        display: flex;
        align-items: center;
        gap: 0.45rem;
        cursor: pointer;
    }

    .styled-toggle {
        accent-color: var(--accent-primary);
        cursor: pointer;
    }

    .toggle-lbl {
        font-size: 0.75rem;
        color: var(--text-primary);
    }

    /* Buttons */
    .btn {
        display: inline-flex;
        align-items: center;
        justify-content: center;
        font-family: var(--font-display);
        font-size: 0.85rem;
        font-weight: 600;
        padding: 0.55rem 1.25rem;
        border-radius: 6px;
        border: 1px solid transparent;
        cursor: pointer;
        transition: all var(--transition-smooth);
        gap: 0.45rem;
    }

    .btn-accent {
        background: linear-gradient(135deg, var(--accent-primary) 0%, var(--accent-secondary) 100%);
        color: white;
        border: none;
        box-shadow: 0 4px 15px rgba(99, 102, 241, 0.25);
    }
    .btn-accent:hover:not(:disabled) {
        transform: translateY(-1px);
        box-shadow: 0 6px 20px rgba(99, 102, 241, 0.35);
    }

    .btn-rose {
        background: rgba(244, 63, 94, 0.1);
        color: var(--accent-error);
        border: 1px solid rgba(244, 63, 94, 0.2);
    }
    .btn-rose:hover:not(:disabled) {
        background: rgba(244, 63, 94, 0.2);
        box-shadow: 0 4px 12px rgba(244, 63, 94, 0.15);
    }

    .btn-full {
        width: 100%;
    }

    .btn:disabled {
        opacity: 0.5;
        cursor: not-allowed;
    }

    .lifecycle-box {
        display: flex;
        flex-direction: column;
        gap: 0.75rem;
    }

    .operations-grid {
        display: flex;
        gap: 0.75rem;
    }

    /* Visual latency timeline */
    .timeline-visualization {
        display: flex;
        flex-direction: column;
        gap: 0.85rem;
        margin-top: 0.25rem;
    }

    .segmented-track-bar {
        display: flex;
        width: 100%;
        height: 8px;
        background-color: rgba(255, 255, 255, 0.03);
        border-radius: 4px;
        overflow: hidden;
        border: 1px solid var(--border-color);
    }

    .bar-segment {
        height: 100%;
        transition: width 0.35s ease;
    }

    .file-read-segment { background-color: var(--accent-primary); }
    .inference-segment { background-color: var(--accent-secondary); }
    .postprocess-segment { background-color: #d946ef; } /* Fuchsia */

    .timeline-legend {
        display: flex;
        flex-direction: column;
        gap: 0.45rem;
    }

    .legend-row {
        display: flex;
        align-items: center;
        font-size: 0.75rem;
    }

    .legend-dot {
        width: 6px;
        height: 6px;
        border-radius: 50%;
        margin-right: 0.5rem;
    }

    .legend-dot.file-read { background-color: var(--accent-primary); }
    .legend-dot.inference { background-color: var(--accent-secondary); }
    .legend-dot.postprocess { background-color: #d946ef; }

    .legend-label {
        color: var(--text-secondary);
        flex: 1;
    }

    .legend-val {
        font-family: var(--font-mono);
        color: var(--text-primary);
        font-weight: 550;
    }

    .total-latency-banner {
        display: flex;
        justify-content: space-between;
        align-items: center;
        background: rgba(255, 255, 255, 0.02);
        border: 1px solid var(--border-color);
        padding: 0.5rem 0.65rem;
        border-radius: 6px;
        font-size: 0.75rem;
    }

    .total-latency-banner .label {
        color: var(--text-secondary);
        font-weight: 550;
    }

    .total-latency-banner .value {
        font-family: var(--font-mono);
        color: var(--accent-secondary);
    }

    .empty-profile-placeholder {
        padding: 1.5rem;
        background-color: var(--bg-control);
        border-radius: 8px;
        text-align: center;
        color: var(--text-secondary);
        font-size: 0.8rem;
    }
</style>
