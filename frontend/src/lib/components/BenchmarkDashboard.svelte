<script lang="ts">
    interface BenchmarkSuite {
        name: string;
        passed: number;
        failed: number;
        tests: { name: string; status: string; ms: number }[];
    }

    let {
        benchmarkSuites = [],
        benchmarkProgress = { passed: 0, total: 0, timeMs: 0, done: false, reportPath: '' }
    } = $props<{
        benchmarkSuites: BenchmarkSuite[],
        benchmarkProgress: { passed: number; total: number; timeMs: number; done: boolean; reportPath: string }
    }>();
</script>

<div class="benchmark-viewport-box">
    {#if benchmarkSuites.length === 0}
        <div class="benchmark-empty-state">
            <div class="empty-icon glow-indigo">
                <svg width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5">
                    <line x1="18" y1="20" x2="18" y2="10"/>
                    <line x1="12" y1="20" x2="12" y2="4"/>
                    <line x1="6" y1="20" x2="6" y2="14"/>
                </svg>
            </div>
            <h3>Dashboard Ready</h3>
            <p>Launch the <strong>Home Security AI Benchmark</strong> in the right panel to compile local VLM test metrics.</p>
        </div>
    {:else}
        <div class="benchmark-dashboard-container">
            <div class="benchmark-progress-header glass-panel">
                <div class="progress-details">
                    <h3>Model Evaluation Metrics</h3>
                    {#if benchmarkProgress.done}
                        <span class="progress-stat success">
                            PASSED: {benchmarkProgress.passed} / {benchmarkProgress.total} 
                            ({Math.round(benchmarkProgress.passed / benchmarkProgress.total * 100)}%)
                        </span>
                    {:else}
                        <span class="progress-stat loading">Evaluating suites...</span>
                    {/if}
                </div>
                {#if benchmarkProgress.done && benchmarkProgress.reportPath}
                    <div class="report-box-callout">
                        <span class="report-text">Report: <code class="code-filepath">{benchmarkProgress.reportPath.split(/[\\/]/).pop()}</code></span>
                    </div>
                {/if}
            </div>

            <div class="benchmark-suites-grid">
                {#each benchmarkSuites as suite}
                    <div class="suite-card glass-panel">
                        <div class="suite-card-header">
                            <span class="suite-name">{suite.name}</span>
                            <span class="suite-score {suite.failed === 0 ? 'installed' : 'uninstalled'}">
                                {suite.passed} / {suite.passed + suite.failed} Pass
                            </span>
                        </div>
                        <div class="suite-progress-track">
                            <div class="suite-progress-fill {suite.failed > 0 ? 'failed' : ''}" 
                                 style="width: {Math.round(suite.passed / (suite.passed + suite.failed || 1) * 100)}%">
                            </div>
                        </div>
                        <div class="suite-test-list">
                            {#each suite.tests.slice(-4) as test}
                                <div class="test-item-row">
                                    <span class="test-icon">{test.status === 'pass' ? '✅' : '❌'}</span>
                                    <span class="test-name">{test.name}</span>
                                    <span class="test-time">{test.ms}ms</span>
                                </div>
                            {/each}
                        </div>
                    </div>
                {/each}
            </div>
        </div>
    {/if}
</div>

<style>
    .benchmark-viewport-box {
        flex: 1;
        display: flex;
        flex-direction: column;
        padding: 1.5rem;
        background: rgba(0, 0, 0, 0.2);
        border-radius: 12px;
        overflow-y: auto;
    }

    .benchmark-empty-state {
        display: flex;
        flex-direction: column;
        align-items: center;
        justify-content: center;
        flex: 1;
        text-align: center;
        gap: 1rem;
    }

    .empty-icon {
        background: rgba(99, 102, 241, 0.1);
        color: var(--accent-indigo);
        padding: 1.5rem;
        border-radius: 50%;
    }

    .glow-indigo {
        box-shadow: 0 0 24px rgba(99, 102, 241, 0.3);
    }

    .benchmark-progress-header {
        display: flex;
        justify-content: space-between;
        align-items: center;
        padding: 1.25rem;
        margin-bottom: 1.5rem;
    }

    .progress-details h3 {
        margin: 0 0 0.5rem 0;
        font-family: var(--font-display);
        color: var(--text-primary);
    }

    .progress-stat {
        font-size: 0.85rem;
        font-weight: 600;
        padding: 0.25rem 0.75rem;
        border-radius: 8px;
    }

    .progress-stat.success {
        background: rgba(16, 185, 129, 0.15);
        color: var(--accent-emerald);
    }

    .progress-stat.loading {
        background: rgba(99, 102, 241, 0.15);
        color: var(--accent-indigo);
    }

    .report-box-callout {
        background: rgba(255, 255, 255, 0.05);
        padding: 0.5rem 1rem;
        border-radius: 8px;
        border: 1px solid var(--border-glass);
    }

    .code-filepath {
        font-family: var(--font-mono);
        color: var(--accent-cyan);
        font-size: 0.85rem;
    }

    .benchmark-suites-grid {
        display: grid;
        grid-template-columns: repeat(auto-fill, minmax(300px, 1fr));
        gap: 1.25rem;
    }

    .suite-card {
        padding: 1.25rem;
        display: flex;
        flex-direction: column;
        gap: 1rem;
    }

    .suite-card-header {
        display: flex;
        justify-content: space-between;
        align-items: center;
    }

    .suite-name {
        font-weight: 600;
        font-size: 0.95rem;
    }

    .suite-score {
        font-size: 0.75rem;
        font-weight: 700;
        padding: 0.2rem 0.6rem;
        border-radius: 6px;
    }

    .suite-score.installed {
        background: rgba(16, 185, 129, 0.15);
        color: var(--accent-emerald);
    }

    .suite-score.uninstalled {
        background: rgba(244, 63, 94, 0.15);
        color: var(--accent-rose);
    }

    .suite-progress-track {
        height: 6px;
        background: rgba(255, 255, 255, 0.05);
        border-radius: 3px;
        overflow: hidden;
    }

    .suite-progress-fill {
        height: 100%;
        background: var(--accent-emerald);
        transition: width 0.3s;
    }

    .suite-progress-fill.failed {
        background: var(--accent-rose);
    }

    .test-item-row {
        display: flex;
        align-items: center;
        gap: 0.75rem;
        font-size: 0.8rem;
        padding: 0.4rem;
        background: rgba(0, 0, 0, 0.2);
        border-radius: 6px;
        margin-bottom: 0.25rem;
    }

    .test-name {
        flex: 1;
        color: var(--text-secondary);
        white-space: nowrap;
        overflow: hidden;
        text-overflow: ellipsis;
    }

    .test-time {
        font-family: var(--font-mono);
        color: var(--text-muted);
    }
</style>
