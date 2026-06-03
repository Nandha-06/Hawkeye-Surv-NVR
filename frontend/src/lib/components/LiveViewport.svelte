<script lang="ts">
    import type { SkillMetadata } from '$lib/types';

    let {
        enabledCameras = [],
        webcamVideos = $bindable({}),
        cameraCanvases = $bindable({}),
        overlayCanvases = $bindable({}),
        videoElement = $bindable(null),
        canvasElement = $bindable(null),
        activeSkillStatus = 'stopped',
        selectedSkill = null as SkillMetadata | null,
        isCameraActive = false,
        isDeploying = false,
        deployStage = '',
        deployProgressPercent = 0,
        fps = 0,
        startSkill,
        stopCamera,
        startCamera
    } = $props<{
        enabledCameras: any[],
        webcamVideos: Record<string, HTMLVideoElement>,
        cameraCanvases: Record<string, HTMLCanvasElement>,
        overlayCanvases: Record<string, HTMLCanvasElement>,
        videoElement: HTMLVideoElement | null,
        canvasElement: HTMLCanvasElement | null,
        activeSkillStatus: string,
        selectedSkill: SkillMetadata | null,
        isCameraActive: boolean,
        isDeploying: boolean,
        deployStage: string,
        deployProgressPercent: number,
        fps: number,
        startSkill: () => void,
        stopCamera: () => void,
        startCamera: () => void
    }>();
</script>

<div class="viewport-box">
    {#if activeSkillStatus === 'ready'}
        <!-- Multi-Camera Grid -->
        <div class="cameras-grid" class:single-camera={enabledCameras.length === 1}>
            {#each enabledCameras as camera}
                <div class="camera-card">
                    <div class="camera-viewport-container">
                        <!-- svelte-ignore a11y_media_has_caption -->
                        <video 
                            bind:this={webcamVideos[camera.id]} 
                            autoplay 
                            playsinline 
                            muted 
                            class="camera-feed"
                        ></video>
                        
                        <canvas 
                            bind:this={overlayCanvases[camera.id]} 
                            class="detection-overlay-canvas"
                        ></canvas>
                    </div>
                </div>
            {/each}
        </div>
    {:else}
        <!-- Fallback Video/Canvas -->
        <!-- svelte-ignore a11y_media_has_caption -->
        <video 
            bind:this={videoElement} 
            autoplay 
            playsinline 
            muted 
            class="camera-feed fallback-feed"
            class:hidden={!isCameraActive}
        ></video>
        <canvas 
            bind:this={canvasElement} 
            class="detection-overlay fallback-overlay"
            class:hidden={!isCameraActive}
        ></canvas>

        <!-- On-Screen Assistant: Camera Off -->
        {#if !isCameraActive && !isDeploying}
            <div class="assistant-overlay text-center">
                <div class="assistant-icon glow-cyan">
                    <svg width="40" height="40" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">
                        <path d="m22 8-6 4 6 4V8Z"/>
                        <rect width="14" height="12" x="2" y="6" rx="2" ry="2"/>
                    </svg>
                </div>
                <h3>Camera Feeds Offline</h3>
                <p>Enable your webcam or RTSP feeds to begin analysis.</p>
                <button onclick={() => { startCamera(); startSkill(); }} class="glass-btn primary-btn">
                    Enable Video Feed
                </button>
            </div>
        {/if}

        <!-- On-Screen Assistant: Camera On, AI Stopped -->
        {#if isCameraActive && activeSkillStatus === 'stopped' && !isDeploying}
            <div class="assistant-overlay glass-assistant text-center">
                <button onclick={startSkill} class="play-btn glow-indigo" aria-label="Launch AI">
                    <svg width="32" height="32" viewBox="0 0 24 24" fill="currentColor">
                        <polygon points="5 3 19 12 5 21 5 3"/>
                    </svg>
                </button>
                <h3>Launch AI Engine</h3>
                <p>Initialize <strong>{selectedSkill?.name || 'Model'}</strong> to process camera feeds.</p>
                <button onclick={startSkill} class="glass-btn accent-btn">
                    Start Processing
                </button>
            </div>
        {/if}

        <!-- On-Screen Assistant: AI Starting -->
        {#if isCameraActive && activeSkillStatus === 'starting' && !isDeploying}
            <div class="assistant-overlay glass-assistant text-center">
                <div class="spinner glow-cyan"></div>
                <h3>Starting detection</h3>
                <p>Loading the selected model.</p>
            </div>
        {/if}
    {/if}

    <!-- Installation Progress Overlay -->
    {#if isDeploying}
        <div class="assistant-overlay glass-assistant installation-overlay text-center">
            <div class="spinner glow-indigo"></div>
            <h3>Installing Dependencies</h3>
            <p class="install-sub">Setting up python virtualenv and fetching models...</p>
            
            <div class="installation-steps">
                <div class="step" class:active={deployStage === 'python'} class:done={deployProgressPercent > 20}>1. Python</div>
                <div class="step" class:active={deployStage === 'venv'} class:done={deployProgressPercent > 40}>2. Venv</div>
                <div class="step" class:active={deployStage === 'gpu' || deployStage === 'install'} class:done={deployProgressPercent > 60}>3. Packages</div>
                <div class="step" class:active={deployStage === 'optimize'} class:done={deployProgressPercent > 80}>4. Weights</div>
            </div>

            <div class="progress-bar-container">
                <div class="progress-fill" style="width: {deployProgressPercent}%"></div>
            </div>
        </div>
    {/if}
</div>

<!-- Feed Statistics Overlay -->
<div class="viewport-footer-metrics">
    <div class="metric">
        <span class="m-label">MODEL</span>
        <span class="m-value">{selectedSkill?.name || 'None'}</span>
    </div>
    <div class="metric">
        <span class="m-label">RATE</span>
        <span class="m-value highlight">{activeSkillStatus === 'ready' ? `${fps} FPS` : '0 FPS'}</span>
    </div>
    <div class="metric">
        <span class="m-label">ACCELERATION</span>
        <span class="m-value">{activeSkillStatus === 'ready' ? 'DirectML (GPU)' : '--'}</span>
    </div>
</div>

<style>
    .viewport-box {
        position: relative;
        flex: 1;
        background: #000;
        border-radius: 12px;
        overflow: hidden;
        display: flex;
        align-items: center;
        justify-content: center;
        min-height: 400px;
        box-shadow: inset 0 0 40px rgba(0,0,0,0.8);
    }

    .cameras-grid {
        display: grid;
        grid-template-columns: repeat(auto-fit, minmax(300px, 1fr));
        gap: 0.5rem;
        width: 100%;
        height: 100%;
        padding: 0.5rem;
    }

    .cameras-grid.single-camera {
        grid-template-columns: 1fr;
    }

    .camera-card {
        background: #0a0a0a;
        border-radius: 8px;
        overflow: hidden;
        position: relative;
    }

    .camera-viewport-container {
        position: relative;
        width: 100%;
        height: 100%;
        display: flex;
        align-items: center;
        justify-content: center;
    }

    .camera-feed, .camera-canvas, .detection-overlay-canvas, .fallback-feed, .fallback-overlay {
        position: absolute;
        top: 0;
        left: 0;
        width: 100%;
        height: 100%;
        object-fit: contain;
    }
    
    .hidden {
        display: none !important;
    }

    /* Assistant Overlays */
    .assistant-overlay {
        position: absolute;
        display: flex;
        flex-direction: column;
        align-items: center;
        justify-content: center;
        gap: 1rem;
        z-index: 10;
        padding: 2rem;
        border-radius: 16px;
    }

    .glass-assistant {
        background: rgba(14, 16, 25, 0.7);
        backdrop-filter: blur(12px);
        border: 1px solid rgba(255, 255, 255, 0.1);
        box-shadow: 0 8px 32px rgba(0,0,0,0.5);
    }

    .assistant-icon {
        background: rgba(6, 182, 212, 0.1);
        color: var(--accent-cyan);
        padding: 1rem;
        border-radius: 50%;
        display: flex;
        align-items: center;
        justify-content: center;
    }

    .glow-cyan {
        box-shadow: 0 0 20px rgba(6, 182, 212, 0.4);
    }

    .glow-indigo {
        box-shadow: 0 0 20px rgba(99, 102, 241, 0.4);
    }

    .play-btn {
        background: var(--accent-indigo);
        color: white;
        border: none;
        border-radius: 50%;
        width: 64px;
        height: 64px;
        display: flex;
        align-items: center;
        justify-content: center;
        cursor: pointer;
        transition: transform 0.2s;
    }

    .play-btn:hover {
        transform: scale(1.1);
    }

    .text-center { text-align: center; }

    .primary-btn {
        background: rgba(16, 185, 129, 0.2);
        color: var(--accent-emerald);
        border-color: rgba(16, 185, 129, 0.4);
    }
    
    .primary-btn:hover {
        background: rgba(16, 185, 129, 0.3);
    }

    .accent-btn {
        background: rgba(99, 102, 241, 0.2);
        color: #a5b4fc;
        border-color: rgba(99, 102, 241, 0.4);
    }

    .spinner {
        width: 40px;
        height: 40px;
        border: 3px solid rgba(255,255,255,0.1);
        border-radius: 50%;
        border-top-color: var(--accent-cyan);
        animation: spin 1s ease-in-out infinite;
    }

    @keyframes spin {
        to { transform: rotate(360deg); }
    }

    .installation-steps {
        display: flex;
        gap: 1rem;
        margin: 1rem 0;
        font-size: 0.8rem;
    }

    .step {
        color: var(--text-muted);
        transition: color 0.3s;
    }

    .step.active {
        color: var(--text-primary);
        font-weight: 600;
    }

    .step.done {
        color: var(--accent-emerald);
    }

    .progress-bar-container {
        width: 100%;
        height: 6px;
        background: rgba(255,255,255,0.1);
        border-radius: 3px;
        overflow: hidden;
    }

    .progress-fill {
        height: 100%;
        background: linear-gradient(90deg, var(--accent-indigo), var(--accent-cyan));
        transition: width 0.3s ease;
    }

    /* Metrics Footer */
    .viewport-footer-metrics {
        display: flex;
        gap: 2rem;
        padding: 1rem 1.5rem;
        background: var(--bg-glass);
        border: 1px solid var(--border-glass);
        border-radius: 12px;
        margin-top: 1rem;
    }

    .metric {
        display: flex;
        flex-direction: column;
        gap: 0.25rem;
    }

    .m-label {
        font-size: 0.65rem;
        color: var(--text-muted);
        font-weight: 700;
        letter-spacing: 0.05em;
    }

    .m-value {
        font-size: 0.9rem;
        font-weight: 600;
        color: var(--text-secondary);
        font-family: var(--font-mono);
    }

    .m-value.highlight {
        color: var(--accent-cyan);
    }
</style>
