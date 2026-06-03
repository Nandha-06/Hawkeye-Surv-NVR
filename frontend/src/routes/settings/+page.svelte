<script lang="ts">
    import { onMount, onDestroy } from 'svelte';
    import { getApiToken, buildWsUrl } from '$lib/apiToken';
    import { fade } from 'svelte/transition';
    import type {
        ProviderConfig,
        ActiveInferenceConfig,
        SkillMetadata
    } from '$lib/types';
    import { devMode } from '$lib/devMode.svelte';

    // WebSocket connection
    let ws: WebSocket | null = $state(null);
    let wsStatus = $state('disconnected');
    let wsReconnectTimer: ReturnType<typeof setTimeout> | null = null;

    // Configuration states
    let providers: Record<string, ProviderConfig> = $state({});
    let activeInference: ActiveInferenceConfig = $state({
        llm: { type: 'local-engine', engineId: 'llama-cpp', modelId: null, port: 5411, provider: null, cloudModelId: null },
        vlm: { type: 'local-engine', engineId: 'llama-cpp', modelId: null, port: 5405, provider: null, cloudModelId: null }
    });
    let localModels: { name: string; sizeBytes: number; path: string; isVlm: boolean }[] = $state([]);

    // Skill Integration States
    let skills: SkillMetadata[] = $state([]);
    let expandedIntegrationId = $state<string | null>(null);
    let skillConfigValues: Record<string, Record<string, any>> = $state({});
    let isDeployingSkill: Record<string, boolean> = $state({});
    let deployLogs: Record<string, string> = $state({});
    let deployProgress: Record<string, number> = $state({});
    let executionLogs: Record<string, string> = $state({});

    // UI state
    let activeTab = $state('providers'); // 'providers' | 'local' | 'integrations' | 'mqtt'
    let expandedProviderId = $state<string | null>('openai');

    // MQTT configuration state
    let mqttConfig = $state({
        enabled: false,
        broker: 'mqtt://localhost',
        port: 1883,
        username: '',
        password: '',
        topicPrefix: 'hawkeye'
    });
    let saveStatus = $state('');
    let showApiKeys: Record<string, boolean> = $state({});

    const aiSkills = $derived(
        skills.filter(s => ['detection', 'analysis', 'transformation', 'privacy'].includes(s.category))
    );

    const integrationSkills = $derived(
        skills.filter(s => ['channels', 'camera-providers', 'automation', 'streaming', 'integrations'].includes(s.category))
    );

    // Provider testing state
    let testingProviders: Record<string, boolean> = $state({});
    let testResults: Record<string, { success: boolean; response?: string; error?: string }> = $state({});

    // HuggingFace search/download state
    let hfSearchQuery = $state('');
    let hfSearchResults: { id: string; author: string; downloads: number; likes: number }[] = $state([]);
    let hfSearching = $state(false);
    let selectedRepoId = $state<string | null>(null);
    let repoFiles: string[] = $state([]);
    let loadingFiles = $state(false);
    let downloadVlmFlag = $state(false);
    let activeDownloads: Record<string, { percent: number; bytesDownloaded: number; totalBytes: number; status: string; error?: string }> = $state({});

    // Curated models for quick click
    const CURATED_MODELS = [
        { name: 'Qwen 2.5 1.5B (Instruct)', repo: 'Qwen/Qwen2.5-1.5B-Instruct-GGUF', file: 'qwen2.5-1.5b-instruct-q4_k_m.gguf', desc: 'Lightweight & fast LLM (1.5B parameters)', isVlm: false, isBundle: false },
        { name: 'DeepSeek R1 1.5B Distill', repo: 'unsloth/deepseek-r1-distill-qwen-1.5b-GGUF', file: 'DeepSeek-R1-Distill-Qwen-1.5B-Q4_K_M.gguf', desc: 'Reasoning model distilled from R1 (1.5B parameters)', isVlm: false, isBundle: false },
        { name: 'Qwen 2.5 7B (Instruct)', repo: 'Qwen/Qwen2.5-7B-Instruct-GGUF', file: 'qwen2.5-7b-instruct-q4_k_m.gguf', desc: 'High accuracy LLM (7B parameters)', isVlm: false, isBundle: false },
        { 
            name: 'Moondream2 0.5B VLM Bundle', 
            repo: 'moondream/moondream2-gguf', 
            files: ['moondream2-text-model-f16.gguf', 'moondream2-mmproj-f16.gguf'], 
            desc: 'Complete local VLM: automatically downloads both LLM Text Model and Vision Projector in one click.', 
            isVlm: true,
            isBundle: true 
        },
        { 
            name: 'Moondream2 0.5B (Vicuna Chat Bundle)', 
            repo: 'ggml-org/moondream2-20250414-GGUF', 
            files: ['moondream2-text-model-f16_ct-vicuna.gguf', 'moondream2-mmproj-f16-20250414.gguf'], 
            desc: 'Complete chat-template local VLM: downloads Vicuna text brain and matching projector.', 
            isVlm: true,
            isBundle: true 
        },
        { name: 'SmolVLM 2.2B Instruct Bundle', repo: 'ggml-org/smolvlm-instruct-gguf', files: ['smolvlm-instruct-q4_k_m.gguf', 'smolvlm-instruct-mmproj-f16.gguf'], desc: 'Lightweight local VLM (2.2B parameters) complete bundle.', isVlm: true, isBundle: true },
        { name: 'LLaVA 1.5 7B Bundle', repo: 'cmp-nct/llava-1.5-gguf', files: ['llava-1.5-7b-q4-k.gguf', 'llava-1.5-7b-mmproj-f16.gguf'], desc: 'Standard local VLM (7B parameters) complete bundle.', isVlm: true, isBundle: true }
    ];

    onMount(() => {
        connectWS();
    });

    onDestroy(() => {
        if (wsReconnectTimer) clearTimeout(wsReconnectTimer);
        if (ws) {
            ws.onclose = null;
            ws.close();
        }
    });

    function connectWS() {
        wsStatus = 'connecting';
        void (async () => {
            const token = await getApiToken();
            const wsUrl = buildWsUrl('/api/ws', token);
            try {
                ws = new WebSocket(wsUrl);
            } catch (err) {
                console.error('WebSocket construction failed', err);
                wsStatus = 'disconnected';
                if (wsReconnectTimer) clearTimeout(wsReconnectTimer);
                wsReconnectTimer = setTimeout(connectWS, 3000);
                return;
            }

            ws.onopen = () => {
                wsStatus = 'connected';
                ws?.send(JSON.stringify({ action: 'get_settings' }));
                ws?.send(JSON.stringify({ action: 'list_skills' }));
            };

            ws.onclose = () => {
                wsStatus = 'disconnected';
                if (wsReconnectTimer) clearTimeout(wsReconnectTimer);
                wsReconnectTimer = setTimeout(connectWS, 3000);
            };

            ws.onmessage = (event) => {
                try {
                const data = JSON.parse(event.data);
                
                switch (data.event) {
                    case 'settings_data':
                        providers = data.providers;
                        activeInference = data.activeInference;
                        localModels = data.localModels;
                        if (data.mqttConfig) mqttConfig = data.mqttConfig;
                        break;

                    case 'settings_saved':
                        providers = data.providers;
                        activeInference = data.activeInference;
                        if (data.mqttConfig) mqttConfig = data.mqttConfig;
                        saveStatus = 'Settings saved successfully!';
                        setTimeout(() => saveStatus = '', 3000);
                        break;

                    case 'test_result':
                        testingProviders[data.providerId] = false;
                        testResults[data.providerId] = {
                            success: data.success,
                            response: data.response,
                            error: data.error
                        };
                        break;

                    case 'hf_search_results':
                        hfSearching = false;
                        if (data.error) {
                            alert(`Error searching HF: ${data.error}`);
                        } else {
                            hfSearchResults = data.models;
                        }
                        break;

                    case 'repo_files':
                        loadingFiles = false;
                        if (data.error) {
                            alert(`Error fetching files: ${data.error}`);
                        } else {
                            repoFiles = data.files;
                        }
                        break;

                    case 'download_progress':
                        activeDownloads = {
                            ...activeDownloads,
                            [data.downloadId]: {
                                percent: data.percent,
                                bytesDownloaded: data.bytesDownloaded,
                                totalBytes: data.totalBytes,
                                status: data.status,
                                error: data.message
                            }
                        };
                        if (data.status === 'completed') {
                            setTimeout(() => {
                                const copy = { ...activeDownloads };
                                delete copy[data.downloadId];
                                activeDownloads = copy;
                                ws?.send(JSON.stringify({ action: 'list_local_models' }));
                            }, 2000);
                        }
                        break;

                    case 'model_deleted':
                        if (data.success) {
                            localModels = data.localModels;
                        } else {
                            alert(`Failed to delete model ${data.filename}`);
                        }
                        break;

                    case 'local_models_list':
                        localModels = data.localModels;
                        break;

                    case 'skills_list':
                        skills = data.skills;
                        skills.forEach(skill => {
                            if (!skillConfigValues[skill.id]) {
                                skillConfigValues[skill.id] = {};
                                skill.configParams.forEach(p => {
                                    skillConfigValues[skill.id][p.name] = p.default;
                                });
                            }
                        });
                        break;

                    case 'deploy_progress':
                        {
                            const sId = data.skillId;
                            if (sId) {
                                isDeployingSkill[sId] = true;
                                if (!deployLogs[sId]) deployLogs[sId] = '';
                                if (data.message) deployLogs[sId] += data.message + '\n';
                                if (data.error) deployLogs[sId] += `[ERROR] ${data.error}\n`;
                                
                                if (data.stage === 'start') {
                                    deployProgress[sId] = 5;
                                } else if (data.stage === 'complete') {
                                    deployProgress[sId] = 100;
                                    isDeployingSkill[sId] = false;
                                    ws?.send(JSON.stringify({ action: 'list_skills' }));
                                } else if (data.stage === 'error') {
                                    deployProgress[sId] = 0;
                                    isDeployingSkill[sId] = false;
                                }
                            }
                        }
                        break;

                    case 'ready':
                        skills = skills.map(s => s.id === data.skillId ? { ...s, isRunning: true, status: 'ready' } : s);
                        break;

                    case 'stopped':
                        skills = skills.map(s => s.id === data.skillId ? { ...s, isRunning: false, status: 'stopped' } : s);
                        break;

                    case 'log':
                        {
                            const sId = data.skillId;
                            if (sId) {
                                if (!executionLogs[sId]) executionLogs[sId] = '';
                                if (data.message) executionLogs[sId] += data.message + '\n';
                            }
                        }
                        break;
                }
            } catch (err) {
                console.error('Error processing settings message:', err);
            }
        };
        })();
    }

    function saveSettings() {
        if (!ws || ws.readyState !== WebSocket.OPEN) return;
        saveStatus = 'Saving settings...';

        // Clean up "null" string selections to native null before saving
        const cleanActiveInference = {
            llm: {
                ...activeInference.llm,
                modelId: (activeInference.llm.modelId === 'null' || activeInference.llm.modelId === '') ? null : activeInference.llm.modelId
            },
            vlm: {
                ...activeInference.vlm,
                modelId: (activeInference.vlm.modelId === 'null' || activeInference.vlm.modelId === '') ? null : activeInference.vlm.modelId
            }
        };

        ws.send(JSON.stringify({
            action: 'save_settings',
            providers,
            activeInference: cleanActiveInference,
            mqttConfig
        }));
    }

    function deploySkill(skillId: string) {
        if (!ws || ws.readyState !== WebSocket.OPEN) return;
        deployLogs[skillId] = 'Deploy started...\n';
        deployProgress[skillId] = 5;
        isDeployingSkill[skillId] = true;
        expandedIntegrationId = skillId; // Auto-expand card to show logs console immediately
        ws.send(JSON.stringify({
            action: 'deploy_skill',
            skillId,
            config: skillConfigValues[skillId] || {}
        }));
    }

    function startSkill(skillId: string) {
        if (!ws || ws.readyState !== WebSocket.OPEN) return;
        executionLogs[skillId] = 'Starting daemon service...\n';
        ws.send(JSON.stringify({
            action: 'start_skill',
            skillId,
            config: skillConfigValues[skillId] || {}
        }));
    }

    function stopSkill(skillId: string) {
        if (!ws || ws.readyState !== WebSocket.OPEN) return;
        ws.send(JSON.stringify({
            action: 'stop_skill',
            skillId
        }));
    }

    function testProvider(id: string, customModel?: string | null) {
        if (!ws || ws.readyState !== WebSocket.OPEN) return;
        testingProviders[id] = true;
        testResults[id] = null as any;

        const config = providers[id];
        ws.send(JSON.stringify({
            action: 'test_provider',
            providerId: id,
            apiKey: config.apiKey,
            baseUrl: config.baseUrl,
            model: customModel || config.defaultModel
        }));
    }

    function searchHuggingFace() {
        if (!hfSearchQuery.trim() || !ws || ws.readyState !== WebSocket.OPEN) return;
        hfSearching = true;
        hfSearchResults = [];
        selectedRepoId = null;
        repoFiles = [];
        ws.send(JSON.stringify({
            action: 'search_huggingface',
            query: hfSearchQuery
        }));
    }

    function selectRepo(repoId: string) {
        selectedRepoId = repoId;
        loadingFiles = true;
        repoFiles = [];
        ws?.send(JSON.stringify({
            action: 'get_repo_files',
            repoId
        }));
    }

    function startDownload(repoId: string, filename: string) {
        if (!ws || ws.readyState !== WebSocket.OPEN) return;
        ws.send(JSON.stringify({
            action: 'download_model',
            repo: repoId,
            filename,
            isVlm: downloadVlmFlag
        }));
    }

    function triggerCuratedDownload(model: any) {
        if (!ws || ws.readyState !== WebSocket.OPEN) return;
        if (model.isBundle) {
            model.files.forEach((file: string) => {
                if (!localModels.some(lm => lm.name === file)) {
                    ws?.send(JSON.stringify({
                        action: 'download_model',
                        repo: model.repo,
                        filename: file,
                        isVlm: model.isVlm
                    }));
                }
            });
        } else {
            ws.send(JSON.stringify({
                action: 'download_model',
                repo: model.repo,
                filename: model.file,
                isVlm: model.isVlm
            }));
        }
    }

    function deleteModel(filename: string, isVlm: boolean) {
        if (!confirm(`Are you sure you want to delete ${filename}?`)) return;
        ws?.send(JSON.stringify({
            action: 'delete_model',
            filename,
            isVlm
        }));
    }

    function formatBytes(bytes: number): string {
        if (bytes === 0) return '0 Bytes';
        const k = 1024;
        const sizes = ['Bytes', 'KB', 'MB', 'GB'];
        const i = Math.floor(Math.log(bytes) / Math.log(k));
        return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + ' ' + sizes[i];
    }
</script>

<div class="flex flex-col gap-6 w-full flex-1">
    <!-- Header with Back Button -->
    <div class="flex flex-col gap-4 border-b border-border pb-6">
        <a href="/" class="inline-flex items-center gap-2 text-xs font-semibold text-muted-foreground hover:text-foreground transition-colors bg-muted/30 hover:bg-muted/50 border border-border px-3 py-1.5 rounded-lg w-fit">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">
                <line x1="19" y1="12" x2="5" y2="12"/>
                <polyline points="12 19 5 12 12 5"/>
            </svg>
            Back to Dashboard
        </a>
        <div class="flex flex-col gap-1">
            <h1 class="text-3xl font-extrabold tracking-tight text-foreground font-display">Model &amp; Inference Settings</h1>
            <p class="text-xs text-muted-foreground">Configure LLM reasoning engines, cloud provider API keys, and local GGUF models.</p>
        </div>
    </div>

    <!-- Navigation Tabs -->
    <div class="flex flex-wrap gap-2 border-b border-border pb-px">
        <button class="px-4 py-2.5 text-xs font-semibold rounded-lg border transition-all cursor-pointer inline-flex items-center gap-2 {activeTab === 'providers' ? 'bg-muted border-border text-foreground shadow-sm' : 'border-transparent text-muted-foreground hover:text-foreground hover:bg-accent/40'}" onclick={() => activeTab = 'providers'}>
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                <path d="M21 16V8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16z"/>
                <polyline points="3.27 6.96 12 12.01 20.73 6.96"/>
                <line x1="12" y1="22.08" x2="12" y2="12"/>
            </svg>
            Active Engines &amp; Cloud APIs
        </button>
        <button class="px-4 py-2.5 text-xs font-semibold rounded-lg border transition-all cursor-pointer inline-flex items-center gap-2 {activeTab === 'local' ? 'bg-muted border-border text-foreground shadow-sm' : 'border-transparent text-muted-foreground hover:text-foreground hover:bg-accent/40'}" onclick={() => activeTab = 'local'}>
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/>
                <polyline points="7 10 12 15 17 10"/>
                <line x1="12" y1="15" x2="12" y2="3"/>
            </svg>
            Local Model Downloader
        </button>
        <button class="px-4 py-2.5 text-xs font-semibold rounded-lg border transition-all cursor-pointer inline-flex items-center gap-2 {activeTab === 'ai-skills' ? 'bg-muted border-border text-foreground shadow-sm' : 'border-transparent text-muted-foreground hover:text-foreground hover:bg-accent/40'}" onclick={() => activeTab = 'ai-skills'}>
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                <path d="M9.5 2A2.5 2.5 0 0 1 12 4.5v15a2.5 2.5 0 0 1-4.96-.44 2.5 2.5 0 0 1 0-3.12 3 3 0 0 1 0-4.88 2.5 2.5 0 0 1 0-3.12A2.5 2.5 0 0 1 9.5 2Z"/>
                <path d="M14.5 2A2.5 2.5 0 0 0 12 4.5v15a2.5 2.5 0 0 0 4.96-.44 2.5 2.5 0 0 0 0-3.12 3 3 0 0 0 0-4.88 2.5 2.5 0 0 0 0-3.12A2.5 2.5 0 0 0 14.5 2Z"/>
            </svg>
            🧠 AI Perception &amp; Detection
        </button>
        <button class="px-4 py-2.5 text-xs font-semibold rounded-lg border transition-all cursor-pointer inline-flex items-center gap-2 {activeTab === 'integrations' ? 'bg-muted border-border text-foreground shadow-sm' : 'border-transparent text-muted-foreground hover:text-foreground hover:bg-accent/40'}" onclick={() => activeTab = 'integrations'}>
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                <rect x="2" y="2" width="20" height="8" rx="2" ry="2"/>
                <rect x="2" y="14" width="20" height="8" rx="2" ry="2"/>
                <line x1="6" y1="6" x2="6.01" y2="6"/>
                <line x1="6" y1="18" x2="6.01" y2="18"/>
            </svg>
            ⚙️ Services &amp; Integrations
        </button>
        <button class="px-4 py-2.5 text-xs font-semibold rounded-lg border transition-all cursor-pointer inline-flex items-center gap-2 {activeTab === 'mqtt' ? 'bg-muted border-border text-foreground shadow-sm' : 'border-transparent text-muted-foreground hover:text-foreground hover:bg-accent/40'}" onclick={() => activeTab = 'mqtt'}>
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                <path d="M5 12h14M12 5v14"/>
            </svg>
            📡 MQTT Broker
        </button>
    </div>

    <!-- MAIN BODY -->
    <div class="flex-grow flex flex-col gap-6">
        {#if activeTab === 'providers'}
            <!-- SECTION 1: ACTIVE INFERENCE ROUTING -->
            <div class="flex flex-col gap-6">
                <div class="flex flex-col gap-1">
                    <h2 class="text-lg font-bold text-foreground">Active Inference Routing</h2>
                    <p class="text-xs text-muted-foreground">Choose whether Hawkeye routes reasoning and scene description tasks to local llama-server instances or third-party cloud API providers.</p>
                </div>

                <div class="grid grid-cols-1 md:grid-cols-2 gap-6">
                    <!-- LLM (Text Reasoning) Config -->
                    <div class="panel flex flex-col gap-4">
                        <div class="flex items-center justify-between border-b border-border pb-3">
                            <div class="flex items-center gap-2">
                                <span class="px-2 py-0.5 text-[9px] font-extrabold tracking-wider rounded uppercase bg-indigo-500/10 text-indigo-400 border border-indigo-500/20 shadow-[0_0_8px_rgba(99,102,241,0.05)]">LLM</span>
                                <h3 class="text-sm font-bold text-foreground">Text Reasoning / Agent Chat</h3>
                            </div>
                            <div>
                                {#if activeInference.llm.type === 'local-engine'}
                                    <span class="px-2 py-0.5 text-[9px] font-extrabold tracking-wider rounded border bg-emerald-500/10 border-emerald-500/20 text-emerald-400">✓ Local</span>
                                {:else if activeInference.llm.provider && providers[activeInference.llm.provider]?.apiKey}
                                    <span class="px-2 py-0.5 text-[9px] font-extrabold tracking-wider rounded border bg-emerald-500/10 border-emerald-500/20 text-emerald-400">✓ Configured</span>
                                {:else}
                                    <span class="px-2 py-0.5 text-[9px] font-extrabold tracking-wider rounded border bg-amber-500/10 border-amber-500/20 text-amber-400">⚠ Key Required</span>
                                {/if}
                            </div>
                        </div>
                        
                        <div class="flex flex-col gap-1.5">
                            <label for="llm-type" class="text-xs font-semibold text-muted-foreground">Inference Engine Type</label>
                            <select id="llm-type" class="select w-full" bind:value={activeInference.llm.type} onchange={() => {
                                if (activeInference.llm.type === 'cloud-provider' && !activeInference.llm.provider) {
                                    activeInference.llm.provider = 'openai';
                                    const p = providers['openai'];
                                    if (p) {
                                        activeInference.llm.cloudModelId = p.defaultModel || p.availableModels[0] || '';
                                    }
                                }
                            }}>
                                <option value="local-engine">Local llama-server (GGUF)</option>
                                <option value="cloud-provider">Cloud API Provider</option>
                            </select>
                        </div>

                        {#if activeInference.llm.type === 'local-engine'}
                            <div class="flex flex-col gap-1.5">
                                <label for="llm-model-select" class="text-xs font-semibold text-muted-foreground">Selected Local LLM Model</label>
                                <select id="llm-model-select" class="select w-full" bind:value={activeInference.llm.modelId}>
                                    <option value={null}>-- Use default local LLM --</option>
                                    {#each localModels.filter(m => !m.isVlm) as model}
                                        <option value={model.name}>{model.name} ({Math.round(model.sizeBytes / (1024 * 1024))} MB)</option>
                                    {/each}
                                </select>
                            </div>
                            <div class="flex flex-col gap-1.5">
                                <label for="llm-port" class="text-xs font-semibold text-muted-foreground">Local llama-server Port</label>
                                <input type="number" id="llm-port" class="input w-full" bind:value={activeInference.llm.port} placeholder="5411" />
                            </div>
                        {:else}
                            <div class="flex flex-col gap-1.5">
                                <label for="llm-provider" class="text-xs font-semibold text-muted-foreground">Active Cloud Provider</label>
                                <select id="llm-provider" class="select w-full" bind:value={activeInference.llm.provider} onchange={() => {
                                    if (activeInference.llm.provider) {
                                        const p = providers[activeInference.llm.provider];
                                        if (p) {
                                            activeInference.llm.cloudModelId = p.defaultModel || p.availableModels[0] || '';
                                        }
                                    }
                                }}>
                                    {#each Object.keys(providers) as pId}
                                        <option value={pId}>{providers[pId]?.label}</option>
                                    {/each}
                                </select>
                            </div>

                            {#if activeInference.llm.provider && providers[activeInference.llm.provider]}
                                {@const pId = activeInference.llm.provider}
                                <div class="flex flex-col gap-1.5">
                                    <label for="api-key-llm" class="text-xs font-semibold text-muted-foreground">API Key</label>
                                    <div class="relative w-full">
                                        <input 
                                            type={showApiKeys['llm-' + pId] ? 'text' : 'password'} 
                                            id="api-key-llm" 
                                            class="input pr-10 w-full"
                                            bind:value={providers[pId].apiKey} 
                                            placeholder="Enter secret API key..."
                                        />
                                        <button type="button" class="absolute right-2 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-foreground text-xs p-1 select-none cursor-pointer" onclick={() => showApiKeys['llm-' + pId] = !showApiKeys['llm-' + pId]}>
                                            {#if showApiKeys['llm-' + pId]}
                                                🙈
                                            {:else}
                                                👁️
                                            {/if}
                                        </button>
                                    </div>
                                </div>

                                <div class="flex flex-col gap-1.5">
                                    <label for="base-url-llm" class="text-xs font-semibold text-muted-foreground">Base API URL</label>
                                    <input type="text" id="base-url-llm" class="input w-full" bind:value={providers[pId].baseUrl} />
                                </div>

                                <div class="flex flex-col gap-1.5">
                                    <label for="available-models-llm" class="text-xs font-semibold text-muted-foreground">Available Models (comma-separated)</label>
                                    <input 
                                        type="text" 
                                        id="available-models-llm" 
                                        class="input w-full"
                                        value={providers[pId].availableModels ? providers[pId].availableModels.join(', ') : ''} 
                                        oninput={(e) => {
                                            const val = (e.target as HTMLInputElement).value;
                                            const list = val.split(',').map(m => m.trim()).filter(Boolean);
                                            providers[pId].availableModels = list;
                                            if (list.length > 0 && !list.includes(providers[pId].defaultModel)) {
                                                providers[pId].defaultModel = list[0];
                                            }
                                            if (list.length > 0 && (!activeInference.llm.cloudModelId || !list.includes(activeInference.llm.cloudModelId))) {
                                                activeInference.llm.cloudModelId = list[0];
                                            }
                                        }}
                                        placeholder="e.g. gpt-4o, gpt-4o-mini"
                                    />
                                </div>

                                <div class="flex flex-col gap-1.5">
                                    <label for="llm-model" class="text-xs font-semibold text-muted-foreground">Model ID</label>
                                    <select id="llm-model" class="select w-full" bind:value={activeInference.llm.cloudModelId}>
                                        {#each providers[pId].availableModels as model}
                                            <option value={model}>{model}</option>
                                        {/each}
                                    </select>
                                </div>

                                <div class="flex flex-col gap-2 mt-2">
                                    <button type="button" class="w-full font-semibold text-xs border border-border bg-background hover:bg-accent hover:text-accent-foreground py-2 rounded-lg transition-colors cursor-pointer flex items-center justify-center gap-1.5 disabled:opacity-50" disabled={testingProviders[pId] || !providers[pId].apiKey} onclick={() => testProvider(pId, activeInference.llm.cloudModelId)}>
                                        {#if testingProviders[pId]}
                                            <span class="w-3.5 h-3.5 border-2 border-primary border-t-transparent rounded-full animate-spin"></span> Testing...
                                        {:else}
                                            Test Connection
                                        {/if}
                                    </button>
                                    
                                    {#if testResults[pId]}
                                        <div class="p-2.5 rounded-lg border text-xs flex items-start gap-2 {testResults[pId].success ? 'bg-emerald-500/10 border-emerald-500/20 text-emerald-400' : 'bg-destructive/10 border-destructive/20 text-destructive'}">
                                            <span class="text-sm shrink-0">{testResults[pId].success ? '✅' : '❌'}</span>
                                            <span class="break-all">{testResults[pId].success ? (testResults[pId].response || 'Connection successful!') : (testResults[pId].error || 'Unexpected error')}</span>
                                        </div>
                                    {/if}
                                </div>
                            {/if}
                        {/if}
                    </div>

                    <!-- VLM (Vision Reasoning) Config -->
                    <div class="panel flex flex-col gap-4">
                        <div class="flex items-center justify-between border-b border-border pb-3">
                            <div class="flex items-center gap-2">
                                <span class="px-2 py-0.5 text-[9px] font-extrabold tracking-wider rounded uppercase bg-pink-500/10 text-pink-400 border border-pink-500/20 shadow-[0_0_8px_rgba(236,72,153,0.05)]">VLM</span>
                                <h3 class="text-sm font-bold text-foreground">Vision Scene Analysis</h3>
                            </div>
                            <div>
                                {#if activeInference.vlm.type === 'local-engine'}
                                    <span class="px-2 py-0.5 text-[9px] font-extrabold tracking-wider rounded border bg-emerald-500/10 border-emerald-500/20 text-emerald-400">✓ Local</span>
                                {:else if activeInference.vlm.provider && providers[activeInference.vlm.provider]?.apiKey}
                                    <span class="px-2 py-0.5 text-[9px] font-extrabold tracking-wider rounded border bg-emerald-500/10 border-emerald-500/20 text-emerald-400">✓ Configured</span>
                                {:else}
                                    <span class="px-2 py-0.5 text-[9px] font-extrabold tracking-wider rounded border bg-amber-500/10 border-amber-500/20 text-amber-400">⚠ Key Required</span>
                                {/if}
                            </div>
                        </div>
                        
                        <div class="flex flex-col gap-1.5">
                            <label for="vlm-type" class="text-xs font-semibold text-muted-foreground">Inference Engine Type</label>
                            <select id="vlm-type" class="select w-full" bind:value={activeInference.vlm.type} onchange={() => {
                                if (activeInference.vlm.type === 'cloud-provider' && !activeInference.vlm.provider) {
                                    activeInference.vlm.provider = 'openai';
                                    const p = providers['openai'];
                                    if (p) {
                                        activeInference.vlm.cloudModelId = p.defaultModel || p.availableModels[0] || '';
                                    }
                                }
                            }}>
                                <option value="local-engine">Local llama-server (GGUF)</option>
                                <option value="cloud-provider">Cloud API Provider</option>
                            </select>
                        </div>

                        {#if activeInference.vlm.type === 'local-engine'}
                            <div class="flex flex-col gap-1.5">
                                <label for="vlm-model-select" class="text-xs font-semibold text-muted-foreground">Selected Local VLM Model</label>
                                <select id="vlm-model-select" class="select w-full" bind:value={activeInference.vlm.modelId}>
                                    <option value={null}>-- Use default local VLM (llava) --</option>
                                    {#each localModels.filter(m => m.isVlm) as model}
                                        <option value={model.name}>{model.name} ({Math.round(model.sizeBytes / (1024 * 1024))} MB)</option>
                                    {/each}
                                </select>
                            </div>
                            <div class="flex flex-col gap-1.5">
                                <label for="vlm-port" class="text-xs font-semibold text-muted-foreground">Local llama-server Port</label>
                                <input type="number" id="vlm-port" class="input w-full" bind:value={activeInference.vlm.port} placeholder="5405" />
                            </div>
                        {:else}
                            <div class="flex flex-col gap-1.5">
                                <label for="vlm-provider" class="text-xs font-semibold text-muted-foreground">Active Cloud Provider</label>
                                <select id="vlm-provider" class="select w-full" bind:value={activeInference.vlm.provider} onchange={() => {
                                    if (activeInference.vlm.provider) {
                                        const p = providers[activeInference.vlm.provider];
                                        if (p) {
                                            activeInference.vlm.cloudModelId = p.defaultModel || p.availableModels[0] || '';
                                        }
                                    }
                                }}>
                                    {#each Object.keys(providers) as pId}
                                        <option value={pId}>{providers[pId]?.label}</option>
                                    {/each}
                                </select>
                            </div>

                            {#if activeInference.vlm.provider && providers[activeInference.vlm.provider]}
                                {@const pId = activeInference.vlm.provider}
                                <div class="flex flex-col gap-1.5">
                                    <label for="api-key-vlm" class="text-xs font-semibold text-muted-foreground">API Key</label>
                                    <div class="relative w-full">
                                        <input 
                                            type={showApiKeys['vlm-' + pId] ? 'text' : 'password'} 
                                            id="api-key-vlm" 
                                            class="input pr-10 w-full"
                                            bind:value={providers[pId].apiKey} 
                                            placeholder="Enter secret API key..."
                                        />
                                        <button type="button" class="absolute right-2 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-foreground text-xs p-1 select-none cursor-pointer" onclick={() => showApiKeys['vlm-' + pId] = !showApiKeys['vlm-' + pId]}>
                                            {#if showApiKeys['vlm-' + pId]}
                                                🙈
                                            {:else}
                                                👁️
                                            {/if}
                                        </button>
                                    </div>
                                </div>

                                <div class="flex flex-col gap-1.5">
                                    <label for="base-url-vlm" class="text-xs font-semibold text-muted-foreground">Base API URL</label>
                                    <input type="text" id="base-url-vlm" class="input w-full" bind:value={providers[pId].baseUrl} />
                                </div>

                                <div class="flex flex-col gap-1.5">
                                    <label for="available-models-vlm" class="text-xs font-semibold text-muted-foreground">Available Models (comma-separated)</label>
                                    <input 
                                        type="text" 
                                        id="available-models-vlm" 
                                        class="input w-full"
                                        value={providers[pId].availableModels ? providers[pId].availableModels.join(', ') : ''} 
                                        oninput={(e) => {
                                            const val = (e.target as HTMLInputElement).value;
                                            const list = val.split(',').map(m => m.trim()).filter(Boolean);
                                            providers[pId].availableModels = list;
                                            if (list.length > 0 && !list.includes(providers[pId].defaultModel)) {
                                                providers[pId].defaultModel = list[0];
                                            }
                                            if (list.length > 0 && (!activeInference.vlm.cloudModelId || !list.includes(activeInference.vlm.cloudModelId))) {
                                                activeInference.vlm.cloudModelId = list[0];
                                            }
                                        }}
                                        placeholder="e.g. gpt-4o, gpt-4o-mini"
                                    />
                                </div>

                                <div class="flex flex-col gap-1.5">
                                    <label for="vlm-model" class="text-xs font-semibold text-muted-foreground">Model ID</label>
                                    <select id="vlm-model" class="select w-full" bind:value={activeInference.vlm.cloudModelId}>
                                        {#each providers[pId].availableModels as model}
                                            <option value={model}>{model}</option>
                                        {/each}
                                    </select>
                                </div>

                                <div class="flex flex-col gap-2 mt-2">
                                    <button type="button" class="w-full font-semibold text-xs border border-border bg-background hover:bg-accent hover:text-accent-foreground py-2 rounded-lg transition-colors cursor-pointer flex items-center justify-center gap-1.5 disabled:opacity-50" disabled={testingProviders[pId] || !providers[pId].apiKey} onclick={() => testProvider(pId, activeInference.vlm.cloudModelId)}>
                                        {#if testingProviders[pId]}
                                            <span class="w-3.5 h-3.5 border-2 border-primary border-t-transparent rounded-full animate-spin"></span> Testing...
                                        {:else}
                                            Test Connection
                                        {/if}
                                    </button>
                                    
                                    {#if testResults[pId]}
                                        <div class="p-2.5 rounded-lg border text-xs flex items-start gap-2 {testResults[pId].success ? 'bg-emerald-500/10 border-emerald-500/20 text-emerald-400' : 'bg-destructive/10 border-destructive/20 text-destructive'}">
                                            <span class="text-sm shrink-0">{testResults[pId].success ? '✅' : '❌'}</span>
                                            <span class="break-all">{testResults[pId].success ? (testResults[pId].response || 'Connection successful!') : (testResults[pId].error || 'Unexpected error')}</span>
                                        </div>
                                    {/if}
                                </div>
                            {/if}
                        {/if}
                    </div>
                </div>
            </div>
        {/if}

        {#if activeTab === 'local'}
            <!-- LOCAL MODEL MANAGER -->
            <div class="flex flex-col gap-6">
                <!-- Curated Section -->
                <div class="flex flex-col gap-4">
                    <div class="flex flex-col gap-1">
                        <h2 class="text-lg font-bold text-foreground">One-Click Curated Downloads</h2>
                        <p class="text-xs text-muted-foreground">Select from our highly recommended GGUF weights to download models directly from Hugging Face Hub.</p>
                    </div>
                    
                    <div class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
                        {#each CURATED_MODELS as model}
                            {@const isDownloaded = model.isBundle
                                ? (model.files || []).every(f => localModels.some(lm => lm.name === f))
                                : localModels.some(lm => lm.name === model.file)}
                            
                            {@const bundleDls = model.isBundle
                                ? (model.files || []).map(f => activeDownloads[`${model.repo}/${f}`]).filter(Boolean)
                                : []}
                            {@const isDownloading = model.isBundle
                                ? bundleDls.length > 0
                                : activeDownloads[`${model.repo}/${model.file}`] !== undefined}
                            {@const activeDlPercent = model.isBundle
                                ? (bundleDls.length > 0 ? Math.round(bundleDls.reduce((acc, d) => acc + d.percent, 0) / (model.files || []).length) : 0)
                                : (activeDownloads[`${model.repo}/${model.file}`]?.percent || 0)}
                            
                            <div class="panel flex flex-col justify-between hover:border-muted-foreground/30 transition-all duration-200 relative gap-3 {isDownloaded ? 'border-emerald-500/20 bg-emerald-500/[0.01]' : ''}">
                                <div class="flex flex-col gap-2">
                                    <div class="flex justify-between items-center">
                                        <span class="px-2 py-0.5 text-[9px] font-extrabold tracking-wider rounded uppercase {model.isVlm ? 'bg-pink-500/10 text-pink-400 border border-pink-500/20 shadow-[0_0_8px_rgba(236,72,153,0.04)]' : 'bg-indigo-500/10 text-indigo-400 border border-indigo-500/20 shadow-[0_0_8px_rgba(99,102,241,0.04)]'}">
                                            {model.isBundle ? 'VLM Bundle' : (model.isVlm ? 'VLM' : 'LLM')}
                                        </span>
                                        {#if isDownloaded}
                                            <span class="inline-flex items-center gap-1 text-[10px] font-bold text-emerald-400">
                                                <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" class="shrink-0"><polyline points="20 6 9 17 4 12"/></svg>
                                                Ready
                                            </span>
                                        {/if}
                                    </div>
                                    <h3 class="font-bold text-sm text-foreground">{model.name}</h3>
                                    <p class="text-xs text-muted-foreground leading-normal line-clamp-2 min-h-[2rem]">{model.desc}</p>
                                    <code class="font-mono text-[9px] bg-muted border border-border/40 px-1.5 py-0.5 rounded text-muted-foreground w-fit max-w-full truncate block select-all">{model.repo}</code>
                                </div>
                                
                                <div class="mt-2 pt-2 border-t border-border/40">
                                    {#if isDownloaded}
                                        <div class="w-full text-center text-xs font-semibold text-emerald-400/80 bg-emerald-500/5 py-1.5 rounded-lg border border-emerald-500/10 select-none">
                                            Downloaded
                                        </div>
                                    {:else}
                                        <button 
                                            class="w-full px-3.5 py-1.5 text-xs font-bold rounded-lg border transition-all cursor-pointer inline-flex items-center justify-center gap-1.5 {isDownloading ? 'bg-indigo-500/10 border-indigo-500/20 text-indigo-400 cursor-not-allowed' : 'bg-primary text-primary-foreground hover:bg-primary/95 shadow-md border-transparent!'}"
                                            disabled={isDownloading}
                                            onclick={() => triggerCuratedDownload(model)}
                                        >
                                            {#if isDownloading}
                                                <span class="w-3 h-3 border-2 border-indigo-400 border-t-transparent rounded-full animate-spin"></span> {activeDlPercent}% Downloading...
                                            {:else}
                                                Download Bundle
                                            {/if}
                                        </button>
                                    {/if}
                                </div>
                            </div>
                        {/each}
                    </div>
                </div>

                <!-- HuggingFace Search Section -->
                <div class="flex flex-col gap-4 border-t border-border pt-6">
                    <div class="flex flex-col gap-1">
                        <h2 class="text-lg font-bold text-foreground">Hugging Face Model Search</h2>
                        <p class="text-xs text-muted-foreground">Search and browse any public Hugging Face model repository to fetch custom GGUF quantization files.</p>
                    </div>

                    <div class="p-3.5 rounded-xl bg-indigo-500/5 border border-indigo-500/15 text-xs text-muted-foreground flex items-start gap-2.5 max-w-3xl">
                        <span class="text-base leading-none">💡</span>
                        <div class="leading-normal">
                            <strong class="text-indigo-400 font-bold">Local Moondream VLM Guide:</strong> The local Moondream vision pipeline requires downloading <strong>both</strong> the text model `.gguf` AND the vision projector `mmproj` `.gguf` file. Search for <code>moondream/moondream2-gguf</code> or browse our quick curated items above to download both components.
                        </div>
                    </div>

                    <div class="flex gap-2 max-w-2xl mt-2">
                        <input 
                            type="text" 
                            class="input flex-1 py-2 text-sm" 
                            bind:value={hfSearchQuery} 
                            placeholder="Search keywords or full repo IDs (e.g. Qwen/Qwen2.5-1.5B-Instruct-GGUF)" 
                            onkeydown={(e) => e.key === 'Enter' && searchHuggingFace()}
                        />
                        <button class="px-5 py-2 text-xs font-semibold rounded-lg bg-primary text-primary-foreground hover:bg-primary/95 transition-all shadow-md border-transparent cursor-pointer shrink-0 disabled:opacity-50" disabled={hfSearching} onclick={searchHuggingFace}>
                            {#if hfSearching}
                                <span class="w-3.5 h-3.5 border-2 border-primary-foreground border-t-transparent rounded-full animate-spin inline-block mr-1"></span> Searching...
                            {:else}
                                Search Repo
                            {/if}
                        </button>
                    </div>

                    {#if hfSearchResults.length > 0}
                        <div class="panel border-border/60 bg-muted/5 flex flex-col gap-3 max-w-3xl mt-2">
                            <h3 class="text-xs font-extrabold uppercase tracking-wider text-muted-foreground pb-2 border-b border-border/40">Search Results</h3>
                            <div class="flex flex-col gap-2 max-h-64 overflow-y-auto pr-1">
                                {#each hfSearchResults as result}
                                    <div class="flex items-center justify-between p-3 rounded-lg border border-border bg-card/50 hover:bg-accent/25 transition-colors gap-4 {selectedRepoId === result.id ? 'border-primary' : ''}">
                                        <div class="flex flex-col gap-1 min-w-0">
                                            <strong class="text-xs font-bold text-foreground truncate select-all">{result.id}</strong>
                                            <span class="text-[10px] text-muted-foreground font-medium">🔽 {result.downloads.toLocaleString()} downloads • ❤️ {result.likes.toLocaleString()} likes</span>
                                        </div>
                                        <button class="px-3 py-1.5 text-xs font-semibold rounded-lg border border-border bg-background hover:bg-accent hover:text-accent-foreground transition-colors cursor-pointer shrink-0" onclick={() => selectRepo(result.id)}>
                                            Browse Files
                                        </button>
                                    </div>
                                {/each}
                            </div>
                        </div>
                    {/if}

                    {#if selectedRepoId}
                        <div class="panel border-indigo-500/20 bg-indigo-500/[0.01] flex flex-col gap-4 mt-2 max-w-3xl">
                            <div class="flex flex-col sm:flex-row sm:items-center justify-between border-b border-border pb-3 gap-3">
                                <div class="flex flex-col gap-0.5">
                                    <span class="text-[9px] font-extrabold uppercase text-indigo-400 tracking-wider">Repository Connected</span>
                                    <h4 class="text-sm font-bold text-foreground font-mono truncate">{selectedRepoId}</h4>
                                </div>
                                <label class="relative inline-flex items-center cursor-pointer select-none">
                                    <input type="checkbox" bind:checked={downloadVlmFlag} class="sr-only peer" />
                                    <div class="w-8 h-4 bg-muted border border-border peer-focus:outline-none rounded-full peer peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:border-gray-300 after:border after:rounded-full after:h-3 after:w-3 after:transition-all peer-checked:bg-pink-500 peer-checked:border-pink-400"></div>
                                    <span class="ml-2 text-xs font-bold text-muted-foreground peer-checked:text-pink-400">Place in <code>vlm_models</code></span>
                                </label>
                            </div>

                            {#if loadingFiles}
                                <div class="flex items-center gap-2 py-4 justify-center text-xs text-muted-foreground font-medium">
                                    <span class="w-4 h-4 border-2 border-primary border-t-transparent rounded-full animate-spin"></span> Fetching repository GGUF file lists...
                                </div>
                            {:else if repoFiles.length === 0}
                                <p class="text-xs text-muted-foreground py-4 text-center">No `.gguf` formatted files detected in the root of this Hugging Face repository.</p>
                            {:else}
                                <div class="flex flex-col gap-2 max-h-72 overflow-y-auto pr-1">
                                    {#each repoFiles as filename}
                                        {@const downloadId = `${selectedRepoId}/${filename}`}
                                        {@const activeDl = activeDownloads[downloadId]}
                                        {@const isDownloaded = localModels.some(m => m.name === filename)}
                                        <div class="flex justify-between items-center p-2.5 rounded-lg border border-border bg-card/30 hover:bg-accent/10 transition-colors gap-4">
                                            <span class="text-xs font-mono text-foreground truncate select-all">{filename}</span>
                                            <div class="shrink-0">
                                                {#if isDownloaded}
                                                    <span class="px-2.5 py-1 text-xs font-semibold text-emerald-400 bg-emerald-500/10 border border-emerald-500/20 rounded-md">Downloaded</span>
                                                {:else if activeDl}
                                                    <span class="text-xs font-semibold text-indigo-400 animate-pulse bg-indigo-500/10 border border-indigo-500/20 px-2.5 py-1 rounded-md">
                                                        {activeDl.percent}% ({formatBytes(activeDl.bytesDownloaded)})
                                                    </span>
                                                {:else}
                                                    <button class="px-3 py-1.5 text-xs font-bold rounded-lg border border-border bg-background hover:bg-accent hover:text-accent-foreground transition-colors cursor-pointer" onclick={() => startDownload(selectedRepoId!, filename)}>
                                                        Download
                                                    </button>
                                                {/if}
                                            </div>
                                        </div>
                                    {/each}
                                </div>
                            {/if}
                        </div>
                    {/if}
                </div>

                <!-- Active Downloads Monitor -->
                {#if Object.keys(activeDownloads).length > 0}
                    <div class="flex flex-col gap-4 border-t border-border pt-6">
                        <div class="flex flex-col gap-1">
                            <h2 class="text-lg font-bold text-foreground">Active Background Downloads</h2>
                            <p class="text-xs text-muted-foreground">Monitor progress, networking stats, and completion estimates for streaming model downloads.</p>
                        </div>

                        <div class="panel border-indigo-500/30 bg-indigo-500/[0.01] shadow-[0_0_15px_rgba(99,102,241,0.02)] flex flex-col gap-4 max-w-3xl">
                            {#each Object.entries(activeDownloads) as [id, dl]}
                                <div class="bg-muted/30 border border-border p-4 rounded-xl flex flex-col gap-2.5">
                                    <div class="flex justify-between items-center text-xs">
                                        <strong class="font-mono text-foreground truncate max-w-[70%] select-all">{id.split('/').pop()}</strong>
                                        <span class="text-muted-foreground font-semibold">{formatBytes(dl.bytesDownloaded)} / {formatBytes(dl.totalBytes)}</span>
                                    </div>
                                    <div class="w-full h-2 bg-muted rounded-full overflow-hidden border border-border/40">
                                        <div class="h-full bg-gradient-to-r from-indigo-500 via-purple-500 to-pink-500 rounded-full transition-all duration-300" style="width: {dl.percent}%"></div>
                                    </div>
                                    <div class="flex justify-between items-center text-[10px]">
                                        <span class="font-bold uppercase tracking-wider text-indigo-400 animate-pulse flex items-center gap-1">
                                            <span class="w-1.5 h-1.5 bg-indigo-400 rounded-full animate-ping"></span>
                                            {dl.status === 'downloading' ? 'Downloading Weights' : dl.status}
                                        </span>
                                        <span class="text-muted-foreground">{dl.percent}% Complete</span>
                                    </div>
                                    {#if dl.error}
                                        <div class="text-[10px] text-destructive font-semibold bg-destructive/10 border border-destructive/20 p-2 rounded-lg mt-1">
                                            Error: {dl.error}
                                        </div>
                                    {/if}
                                </div>
                            {/each}
                        </div>
                    </div>
                {/if}

                <!-- Downloaded Models List -->
                <div class="flex flex-col gap-4 border-t border-border pt-6">
                    <div class="flex flex-col gap-1">
                        <h2 class="text-lg font-bold text-foreground">Downloaded Local Models</h2>
                        <p class="text-xs text-muted-foreground">Manage currently cached local GGUF models and vision projectors on this system.</p>
                    </div>

                    {#if localModels.length === 0}
                        <div class="flex flex-col items-center justify-center p-8 border border-dashed border-border bg-muted/15 rounded-xl text-center max-w-3xl gap-2">
                            <span class="text-2xl">📦</span>
                            <h3 class="text-xs font-bold text-foreground">No Local Models Detected</h3>
                            <p class="text-[11px] text-muted-foreground max-w-xs">Use the quick curate downloads or the Hugging Face search above to pull files to your server core.</p>
                        </div>
                    {:else}
                        <div class="flex flex-col gap-2 max-w-3xl">
                            {#each localModels as model}
                                <div class="flex items-center justify-between border border-border bg-muted/10 hover:bg-muted/20 p-3.5 rounded-xl transition-all duration-200 gap-4">
                                    <div class="flex items-center gap-3 min-w-0">
                                        <span class="px-2 py-0.5 text-[9px] font-extrabold tracking-wider rounded uppercase shrink-0 {model.isVlm ? 'bg-pink-500/10 text-pink-400 border border-pink-500/20 shadow-[0_0_8px_rgba(236,72,153,0.04)]' : 'bg-indigo-500/10 text-indigo-400 border border-indigo-500/20 shadow-[0_0_8px_rgba(99,102,241,0.04)]'}">
                                            {model.isVlm ? 'VLM' : 'LLM'}
                                        </span>
                                        <div class="flex flex-col gap-0.5 min-w-0">
                                            <strong class="text-xs font-bold text-foreground truncate select-all">{model.name}</strong>
                                            <span class="text-[10px] text-muted-foreground truncate">Size: {formatBytes(model.sizeBytes)} • Location: <code>{model.isVlm ? 'vlm_models/' : 'models/'}</code></span>
                                        </div>
                                    </div>
                                    <button class="px-2.5 py-1.5 text-xs font-bold text-destructive hover:bg-destructive/10 border border-destructive/20 hover:border-destructive/30 rounded-lg transition-colors cursor-pointer shrink-0" onclick={() => deleteModel(model.name, model.isVlm)}>
                                        Delete File
                                    </button>
                                </div>
                            {/each}
                        </div>
                    {/if}
                </div>
            </div>
        {/if}

        {#if activeTab === 'ai-skills'}
            <!-- AI PERCEPTION & DETECTION PANEL -->
            <div class="flex flex-col gap-4">
                <div class="flex flex-col gap-1">
                    <h2 class="text-lg font-bold text-foreground">AI Perception &amp; Detection Pipeline</h2>
                    <p class="text-xs text-muted-foreground">Configure, deploy, and select model sizes for real-time computer vision, object detection, and visual language models (VLMs).</p>
                </div>

                <div class="flex flex-col gap-3 max-w-4xl mt-2">
                    {#each aiSkills as skill}
                        {@const isExpanded = expandedIntegrationId === skill.id}
                        {@const isRunning = skill.isRunning}
                        {@const activeDl = isDeployingSkill[skill.id]}
                        
                        <div class="bg-card border rounded-xl overflow-hidden transition-all duration-300 hover:border-muted-foreground/20 {isExpanded ? 'border-primary bg-card/65 shadow-md' : 'border-border'} {isRunning ? 'border-l-4 border-l-emerald-500' : ''}">
                            <button class="w-full text-left p-4 hover:bg-muted/10 transition-colors flex items-center justify-between gap-4 cursor-pointer select-none" onclick={() => expandedIntegrationId = isExpanded ? null : skill.id}>
                                <div class="flex items-center gap-3.5 min-w-0">
                                    <span class="px-2 py-0.5 text-[9px] font-extrabold tracking-wider rounded uppercase shrink-0 bg-indigo-500/10 text-indigo-400 border border-indigo-500/20">
                                        {skill.category}
                                    </span>
                                    <div class="flex flex-col gap-0.5 min-w-0">
                                        <h4 class="text-sm font-bold text-foreground truncate">{skill.name}</h4>
                                        <p class="text-xs text-muted-foreground truncate leading-normal">{skill.description}</p>
                                    </div>
                                </div>
                                <div class="flex items-center gap-4 shrink-0">
                                    <div class="flex items-center gap-1.5">
                                        {#if isRunning}
                                            <span class="w-2 h-2 rounded-full bg-emerald-400 animate-pulse shadow-[0_0_8px_#34d399]"></span>
                                            <span class="text-[10px] font-bold text-emerald-400 uppercase tracking-wider">Running</span>
                                        {:else if skill.isInstalled}
                                            <span class="w-2 h-2 rounded-full bg-indigo-400 shadow-[0_0_8px_#818cf8]"></span>
                                            <span class="text-[10px] font-bold text-indigo-400 uppercase tracking-wider">Ready</span>
                                        {:else}
                                            <span class="w-2 h-2 rounded-full bg-zinc-600"></span>
                                            <span class="text-[10px] font-bold text-muted-foreground uppercase tracking-wider">Deployable</span>
                                        {/if}
                                    </div>
                                    <span class="text-muted-foreground text-xs transition-transform duration-200 {isExpanded ? 'rotate-180' : ''}">▼</span>
                                </div>
                            </button>

                            {#if isExpanded}
                                <div class="p-4 border-t border-border bg-muted/10 flex flex-col gap-4 animate-in fade-in slide-in-from-top-1 duration-150">
                                    {#if skill.configParams && skill.configParams.length > 0}
                                        <div class="grid grid-cols-1 sm:grid-cols-2 gap-4 border-b border-border/40 pb-4">
                                            {#each skill.configParams as param}
                                                <div class="flex flex-col gap-1.5">
                                                    <label for="param-{skill.id}-{param.name}" class="text-xs font-semibold text-muted-foreground">{param.label}</label>
                                                    {#if param.type === 'select'}
                                                        <select id="param-{skill.id}-{param.name}" class="select w-full" bind:value={skillConfigValues[skill.id][param.name]} disabled={isRunning}>
                                                            {#each param.options || [] as opt}
                                                                {#if typeof opt === 'object'}
                                                                    <option value={opt.value}>{opt.label}</option>
                                                                {:else}
                                                                    <option value={opt}>{opt}</option>
                                                                {/if}
                                                            {/each}
                                                        </select>
                                                    {:else if param.type === 'boolean'}
                                                        <label class="relative inline-flex items-center cursor-pointer select-none mt-1">
                                                            <input type="checkbox" bind:checked={skillConfigValues[skill.id][param.name]} class="sr-only peer" disabled={isRunning} />
                                                            <div class="w-9 h-5 bg-muted border border-border peer-focus:outline-none rounded-full peer peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[3px] after:left-[3px] after:bg-white after:border-gray-300 after:border after:rounded-full after:h-3.5 after:w-3.5 after:transition-all peer-checked:bg-indigo-600 peer-checked:border-indigo-500"></div>
                                                            <span class="ml-2.5 text-xs font-semibold text-muted-foreground peer-checked:text-foreground">Enable Parameter</span>
                                                        </label>
                                                    {:else if param.type === 'number'}
                                                        <input 
                                                            type="number" 
                                                            id="param-{skill.id}-{param.name}" 
                                                            class="input w-full"
                                                            bind:value={skillConfigValues[skill.id][param.name]} 
                                                            min={param.min}
                                                            max={param.max}
                                                            disabled={isRunning}
                                                        />
                                                    {:else if param.type === 'password'}
                                                        <input 
                                                            type="password" 
                                                            id="param-{skill.id}-{param.name}" 
                                                            class="input w-full"
                                                            bind:value={skillConfigValues[skill.id][param.name]} 
                                                            disabled={isRunning}
                                                        />
                                                    {:else}
                                                        <input 
                                                            type="text" 
                                                            id="param-{skill.id}-{param.name}" 
                                                            class="input w-full"
                                                            bind:value={skillConfigValues[skill.id][param.name]} 
                                                            placeholder={param.placeholder || ''}
                                                            disabled={isRunning}
                                                        />
                                                    {/if}
                                                    {#if param.description}
                                                        <p class="text-[10px] text-muted-foreground leading-normal mt-0.5">{param.description}</p>
                                                    {/if}
                                                </div>
                                            {/each}
                                        </div>
                                    {/if}

                                    <div class="flex flex-wrap gap-2.5 mt-1">
                                        {#if !skill.isInstalled}
                                            <button class="px-4 py-2 text-xs font-bold rounded-lg bg-indigo-600 hover:bg-indigo-500 text-white shadow-md border-transparent transition-colors cursor-pointer inline-flex items-center gap-1.5 disabled:opacity-50" disabled={activeDl} onclick={() => deploySkill(skill.id)}>
                                                {#if activeDl}
                                                    <span class="w-3.5 h-3.5 border-2 border-white border-t-transparent rounded-full animate-spin"></span> Deploying...
                                                {:else}
                                                    🔧 Deploy &amp; Pre-convert
                                                {/if}
                                            </button>
                                        {:else if isRunning}
                                            <button class="px-4 py-2 text-xs font-bold rounded-lg bg-destructive hover:bg-destructive/90 text-white shadow-sm border-transparent transition-colors cursor-pointer inline-flex items-center gap-1.5" onclick={() => stopSkill(skill.id)}>
                                                ⏹ Stop Pipeline
                                            </button>
                                        {:else}
                                            <button class="px-4 py-2 text-xs font-bold rounded-lg bg-emerald-600 hover:bg-emerald-500 text-white shadow-md border-transparent transition-colors cursor-pointer inline-flex items-center gap-1.5" onclick={() => startSkill(skill.id)}>
                                                ▶ Start Pipeline
                                            </button>
                                        {/if}
                                    </div>

                                    {#if deployLogs[skill.id]}
                                        <div class="border border-border bg-black/85 rounded-xl p-3.5 mt-2 flex flex-col gap-2 relative shadow-inner">
                                            <div class="flex justify-between items-center text-[10px] text-muted-foreground border-b border-border/40 pb-2">
                                                <span class="font-mono text-indigo-400">deploy-process@{skill.id}:~</span>
                                                {#if activeDl}
                                                    <div class="flex items-center gap-1.5 font-semibold text-amber-400">
                                                        <span class="w-1.5 h-1.5 rounded-full bg-amber-400 animate-pulse"></span>
                                                        <span>Compiling &amp; Optimizing...</span>
                                                    </div>
                                                {:else}
                                                    <span class="font-semibold text-emerald-400 flex items-center gap-1">✓ Complete</span>
                                                {/if}
                                            </div>
                                            <pre class="font-mono text-[10px] text-muted-foreground bg-transparent p-0 overflow-x-auto max-h-48 text-left leading-relaxed whitespace-pre-wrap select-all">{deployLogs[skill.id]}</pre>
                                        </div>
                                    {/if}

                                    {#if isRunning && executionLogs[skill.id]}
                                        <div class="border border-border bg-black/85 rounded-xl p-3.5 mt-2 flex flex-col gap-2 relative shadow-inner">
                                            <div class="flex justify-between items-center text-[10px] text-muted-foreground border-b border-border/40 pb-2">
                                                <span class="font-mono text-emerald-400">stdout@{skill.id}:~</span>
                                                <span class="font-semibold text-emerald-400 flex items-center gap-1">
                                                    <span class="w-1.5 h-1.5 rounded-full bg-emerald-400 animate-pulse"></span>
                                                    <span>Live Terminal Console</span>
                                                </span>
                                            </div>
                                            <pre class="font-mono text-[10px] text-muted-foreground bg-transparent p-0 overflow-x-auto max-h-48 text-left leading-relaxed whitespace-pre-wrap select-all">{executionLogs[skill.id]}</pre>
                                        </div>
                                    {/if}
                                </div>
                            {/if}
                        </div>
                    {/each}
                </div>
            </div>
        {/if}

        {#if activeTab === 'integrations'}
            <!-- SERVICES & INTEGRATIONS PANEL -->
            <div class="flex flex-col gap-4">
                <div class="flex flex-col gap-1">
                    <h2 class="text-lg font-bold text-foreground">Daemon Services &amp; Background Integrations</h2>
                    <p class="text-xs text-muted-foreground">Manage camera IP connection providers, streaming targets, notification bot channels, and automation triggers.</p>
                </div>

                <div class="flex flex-col gap-3 max-w-4xl mt-2">
                    {#each integrationSkills as skill}
                        {@const isExpanded = expandedIntegrationId === skill.id}
                        {@const isRunning = skill.isRunning}
                        {@const activeDl = isDeployingSkill[skill.id]}
                        
                        <div class="bg-card border rounded-xl overflow-hidden transition-all duration-300 hover:border-muted-foreground/20 {isExpanded ? 'border-primary bg-card/65 shadow-md' : 'border-border'} {isRunning ? 'border-l-4 border-l-emerald-500' : ''}">
                            <button class="w-full text-left p-4 hover:bg-muted/10 transition-colors flex items-center justify-between gap-4 cursor-pointer select-none" onclick={() => expandedIntegrationId = isExpanded ? null : skill.id}>
                                <div class="flex items-center gap-3.5 min-w-0">
                                    <span class="px-2 py-0.5 text-[9px] font-extrabold tracking-wider rounded uppercase shrink-0 bg-pink-500/10 text-pink-400 border border-pink-500/20">
                                        {skill.category}
                                    </span>
                                    <div class="flex flex-col gap-0.5 min-w-0">
                                        <h4 class="text-sm font-bold text-foreground truncate">{skill.name}</h4>
                                        <p class="text-xs text-muted-foreground truncate leading-normal">{skill.description}</p>
                                    </div>
                                </div>
                                <div class="flex items-center gap-4 shrink-0">
                                    <div class="flex items-center gap-1.5">
                                        {#if isRunning}
                                            <span class="w-2 h-2 rounded-full bg-emerald-400 animate-pulse shadow-[0_0_8px_#34d399]"></span>
                                            <span class="text-[10px] font-bold text-emerald-400 uppercase tracking-wider">Running</span>
                                        {:else if skill.isInstalled}
                                            <span class="w-2 h-2 rounded-full bg-indigo-400 shadow-[0_0_8px_#818cf8]"></span>
                                            <span class="text-[10px] font-bold text-indigo-400 uppercase tracking-wider">Ready</span>
                                        {:else}
                                            <span class="w-2 h-2 rounded-full bg-zinc-600"></span>
                                            <span class="text-[10px] font-bold text-muted-foreground uppercase tracking-wider">Deployable</span>
                                        {/if}
                                    </div>
                                    <span class="text-muted-foreground text-xs transition-transform duration-200 {isExpanded ? 'rotate-180' : ''}">▼</span>
                                </div>
                            </button>

                            {#if isExpanded}
                                <div class="p-4 border-t border-border bg-muted/10 flex flex-col gap-4 animate-in fade-in slide-in-from-top-1 duration-150">
                                    {#if skill.configParams && skill.configParams.length > 0}
                                        <div class="grid grid-cols-1 sm:grid-cols-2 gap-4 border-b border-border/40 pb-4">
                                            {#each skill.configParams as param}
                                                <div class="flex flex-col gap-1.5">
                                                    <label for="param-{skill.id}-{param.name}" class="text-xs font-semibold text-muted-foreground">{param.label}</label>
                                                    {#if param.type === 'select'}
                                                        <select id="param-{skill.id}-{param.name}" class="select w-full" bind:value={skillConfigValues[skill.id][param.name]} disabled={isRunning}>
                                                            {#each param.options || [] as opt}
                                                                {#if typeof opt === 'object'}
                                                                    <option value={opt.value}>{opt.label}</option>
                                                                {:else}
                                                                    <option value={opt}>{opt}</option>
                                                                {/if}
                                                            {/each}
                                                        </select>
                                                    {:else if param.type === 'boolean'}
                                                        <label class="relative inline-flex items-center cursor-pointer select-none mt-1">
                                                            <input type="checkbox" bind:checked={skillConfigValues[skill.id][param.name]} class="sr-only peer" disabled={isRunning} />
                                                            <div class="w-9 h-5 bg-muted border border-border peer-focus:outline-none rounded-full peer peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[3px] after:left-[3px] after:bg-white after:border-gray-300 after:border after:rounded-full after:h-3.5 after:w-3.5 after:transition-all peer-checked:bg-indigo-600 peer-checked:border-indigo-500"></div>
                                                            <span class="ml-2.5 text-xs font-semibold text-muted-foreground peer-checked:text-foreground">Enable Integration</span>
                                                        </label>
                                                    {:else if param.type === 'number'}
                                                        <input 
                                                            type="number" 
                                                            id="param-{skill.id}-{param.name}" 
                                                            class="input w-full"
                                                            bind:value={skillConfigValues[skill.id][param.name]} 
                                                            min={param.min}
                                                            max={param.max}
                                                            disabled={isRunning}
                                                        />
                                                    {:else if param.type === 'password'}
                                                        <input 
                                                            type="password" 
                                                            id="param-{skill.id}-{param.name}" 
                                                            class="input w-full"
                                                            bind:value={skillConfigValues[skill.id][param.name]} 
                                                            disabled={isRunning}
                                                        />
                                                    {:else}
                                                        <input 
                                                            type="text" 
                                                            id="param-{skill.id}-{param.name}" 
                                                            class="input w-full"
                                                            bind:value={skillConfigValues[skill.id][param.name]} 
                                                            placeholder={param.placeholder || ''}
                                                            disabled={isRunning}
                                                        />
                                                    {/if}
                                                    {#if param.description}
                                                        <p class="text-[10px] text-muted-foreground leading-normal mt-0.5">{param.description}</p>
                                                    {/if}
                                                </div>
                                            {/each}
                                        </div>
                                    {/if}

                                    <div class="flex flex-wrap gap-2.5 mt-1">
                                        {#if !skill.isInstalled}
                                            <button class="px-4 py-2 text-xs font-bold rounded-lg bg-indigo-600 hover:bg-indigo-500 text-white shadow-md border-transparent transition-colors cursor-pointer inline-flex items-center gap-1.5 disabled:opacity-50" disabled={activeDl} onclick={() => deploySkill(skill.id)}>
                                                {#if activeDl}
                                                    <span class="w-3.5 h-3.5 border-2 border-white border-t-transparent rounded-full animate-spin"></span> Deploying...
                                                {:else}
                                                    🔧 Deploy Integration
                                                {/if}
                                            </button>
                                        {:else if isRunning}
                                            <button class="px-4 py-2 text-xs font-bold rounded-lg bg-destructive hover:bg-destructive/90 text-white shadow-sm border-transparent transition-colors cursor-pointer inline-flex items-center gap-1.5" onclick={() => stopSkill(skill.id)}>
                                                ⏹ Stop Service
                                            </button>
                                        {:else}
                                            <button class="px-4 py-2 text-xs font-bold rounded-lg bg-emerald-600 hover:bg-emerald-500 text-white shadow-md border-transparent transition-colors cursor-pointer inline-flex items-center gap-1.5" onclick={() => startSkill(skill.id)}>
                                                ▶ Start Service
                                            </button>
                                        {/if}
                                    </div>

                                    {#if deployLogs[skill.id]}
                                        <div class="border border-border bg-black/85 rounded-xl p-3.5 mt-2 flex flex-col gap-2 relative shadow-inner">
                                            <div class="flex justify-between items-center text-[10px] text-muted-foreground border-b border-border/40 pb-2">
                                                <span class="font-mono text-indigo-400">deploy-process@{skill.id}:~</span>
                                                {#if activeDl}
                                                    <div class="flex items-center gap-1.5 font-semibold text-amber-400">
                                                        <span class="w-1.5 h-1.5 rounded-full bg-amber-400 animate-pulse"></span>
                                                        <span>Installing dependencies...</span>
                                                    </div>
                                                {:else}
                                                    <span class="font-semibold text-emerald-400 flex items-center gap-1">✓ Complete</span>
                                                {/if}
                                            </div>
                                            <pre class="font-mono text-[10px] text-muted-foreground bg-transparent p-0 overflow-x-auto max-h-48 text-left leading-relaxed whitespace-pre-wrap select-all">{deployLogs[skill.id]}</pre>
                                        </div>
                                    {/if}

                                    {#if isRunning && executionLogs[skill.id]}
                                        <div class="border border-border bg-black/85 rounded-xl p-3.5 mt-2 flex flex-col gap-2 relative shadow-inner">
                                            <div class="flex justify-between items-center text-[10px] text-muted-foreground border-b border-border/40 pb-2">
                                                <span class="font-mono text-emerald-400">stdout@{skill.id}:~</span>
                                                <span class="font-semibold text-emerald-400 flex items-center gap-1">
                                                    <span class="w-1.5 h-1.5 rounded-full bg-emerald-400 animate-pulse"></span>
                                                    <span>Live Terminal Console</span>
                                                </span>
                                            </div>
                                            <pre class="font-mono text-[10px] text-muted-foreground bg-transparent p-0 overflow-x-auto max-h-48 text-left leading-relaxed whitespace-pre-wrap select-all">{executionLogs[skill.id]}</pre>
                                        </div>
                                    {/if}
                                </div>
                            {/if}
                        </div>
                    {/each}
                </div>
            </div>
        {/if}

        {#if activeTab === 'mqtt'}
            <!-- SECTION: MQTT BROKER INTEGRATION -->
            <div class="flex flex-col gap-6" in:fade={{ duration: 150 }}>
                <div class="flex flex-col gap-1">
                    <h2 class="text-lg font-bold text-foreground">MQTT Broker Configuration</h2>
                    <p class="text-xs text-muted-foreground">Publish security events, alert diagnostics, and active camera object sighting topics to a local or remote MQTT broker for Home Assistant automations.</p>
                </div>

                <div class="panel flex flex-col gap-5 max-w-2xl bg-card border border-border rounded-xl p-5 shadow-sm">
                    <div class="flex items-center justify-between border-b border-border pb-3">
                        <div class="flex items-center gap-2">
                            <span class="w-2.5 h-2.5 rounded-full {mqttConfig.enabled ? 'bg-emerald-500 shadow-[0_0_8px_#10b981]' : 'bg-muted-foreground'}"></span>
                            <span class="text-sm font-bold text-foreground">MQTT Integration Status</span>
                        </div>
                        <label class="relative inline-flex items-center cursor-pointer">
                            <input type="checkbox" bind:checked={mqttConfig.enabled} class="sr-only peer" />
                            <div class="w-9 h-5 bg-muted peer-focus:outline-none rounded-full peer peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:border-gray-300 after:border after:rounded-full after:h-4 after:w-4 after:transition-all peer-checked:bg-primary"></div>
                        </label>
                    </div>

                    <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
                        <div class="form-group flex flex-col gap-1.5 font-sans">
                            <label class="text-xs font-semibold text-muted-foreground" for="mqtt-broker">Broker Host Address</label>
                            <input type="text" id="mqtt-broker" class="input w-full bg-background" bind:value={mqttConfig.broker} placeholder="mqtt://192.168.1.10" />
                        </div>
                        <div class="form-group flex flex-col gap-1.5">
                            <label class="text-xs font-semibold text-muted-foreground" for="mqtt-port">Broker Port</label>
                            <input type="number" id="mqtt-port" class="input w-full bg-background" bind:value={mqttConfig.port} placeholder="1883" />
                        </div>
                        <div class="form-group flex flex-col gap-1.5 font-sans">
                            <label class="text-xs font-semibold text-muted-foreground" for="mqtt-user">Username (Optional)</label>
                            <input type="text" id="mqtt-user" class="input w-full bg-background" bind:value={mqttConfig.username} placeholder="Username" />
                        </div>
                        <div class="form-group flex flex-col gap-1.5 font-sans">
                            <label class="text-xs font-semibold text-muted-foreground" for="mqtt-pass">Password (Optional)</label>
                            <input type="password" id="mqtt-pass" class="input w-full bg-background" bind:value={mqttConfig.password} placeholder="••••••••" />
                        </div>
                        <div class="form-group flex flex-col gap-1.5 md:col-span-2 font-sans">
                            <label class="text-xs font-semibold text-muted-foreground" for="mqtt-prefix">Topic Prefix</label>
                            <input type="text" id="mqtt-prefix" class="input w-full bg-background" bind:value={mqttConfig.topicPrefix} placeholder="hawkeye" />
                            <span class="text-[10px] text-muted-foreground">Binary sensors will publish to: <code>{mqttConfig.topicPrefix}/camera/&lt;camera_id&gt;/&lt;label&gt;</code></span>
                        </div>
                    </div>
                </div>
            </div>
        {/if}
    </div>

    <!-- Save / Save Status Footer -->
    <div class="sticky bottom-0 bg-background/95 backdrop-blur-md border-t border-border py-4 px-6 flex justify-between items-center z-40 mt-auto shrink-0 shadow-[0_-10px_20px_rgba(0,0,0,0.3)] -mx-6">
        <div class="px-6 flex justify-between items-center w-full max-w-[1400px] mx-auto">
            <div class="flex-grow">
                {#if saveStatus}
                    <span class="text-xs font-semibold text-emerald-400 bg-emerald-500/10 border border-emerald-500/20 px-3.5 py-2 rounded-lg animate-fade-in flex items-center gap-1.5 w-fit">
                        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" class="shrink-0"><polyline points="20 6 9 17 4 12"/></svg>
                        {saveStatus}
                    </span>
                {/if}
            </div>
            <button class="px-5 py-2.5 text-xs font-bold rounded-lg bg-primary text-primary-foreground hover:bg-primary/95 transition-all shadow-lg shadow-primary/10 select-none cursor-pointer flex items-center gap-1.5" onclick={saveSettings}>
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" class="shrink-0"><path d="M19 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11l5 5v11a2 2 0 0 1-2 2z"/><polyline points="17 21 17 13 7 13 7 21"/><polyline points="7 3 7 8 15 8"/></svg>
                Save Config Changes
            </button>
        </div>
    </div>
</div>

<style>
    /* Small layout specific enhancements */
    .font-display {
        font-family: 'Outfit', sans-serif;
    }
</style>

