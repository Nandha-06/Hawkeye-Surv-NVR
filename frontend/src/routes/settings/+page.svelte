<script lang="ts">
    import { onMount, onDestroy } from 'svelte';
    import { getApiToken, buildWsUrl } from '$lib/apiToken';
    import { fade, slide, fly } from 'svelte/transition';
    import type {
        ProviderConfig,
        ActiveInferenceConfig,
        SkillMetadata
    } from '$lib/types';
    import { devMode } from '$lib/devMode.svelte';

    let ws: WebSocket | null = $state(null);
    let wsStatus = $state('disconnected');
    let wsReconnectTimer: ReturnType<typeof setTimeout> | null = null;

    let providers: Record<string, ProviderConfig> = $state({});
    let activeInference: ActiveInferenceConfig = $state({
        llm: { type: 'local-engine', engineId: 'llama-cpp', modelId: null, port: 5411, provider: null, cloudModelId: null },
        vlm: { type: 'local-engine', engineId: 'llama-cpp', modelId: null, port: 5405, provider: null, cloudModelId: null }
    });
    let localModels: { name: string; sizeBytes: number; path: string; isVlm: boolean }[] = $state([]);

    let skills: SkillMetadata[] = $state([]);
    let expandedIntegrationId = $state<string | null>(null);
    let skillConfigValues: Record<string, Record<string, any>> = $state({});
    let isDeployingSkill: Record<string, boolean> = $state({});
    let deployLogs: Record<string, string> = $state({});
    let deployProgress: Record<string, number> = $state({});
    let executionLogs: Record<string, string> = $state({});

    type SettingsTab = 'providers' | 'local' | 'ai-skills' | 'integrations' | 'mqtt';
    let activeTab = $state<SettingsTab>('providers');
    let expandedProviderId = $state<string | null>('openai');

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

    let testingProviders: Record<string, boolean> = $state({});
    let testResults: Record<string, { success: boolean; response?: string; error?: string }> = $state({});

    let hfSearchQuery = $state('');
    let hfSearchResults: { id: string; author: string; downloads: number; likes: number }[] = $state([]);
    let hfSearching = $state(false);
    let selectedRepoId = $state<string | null>(null);
    let repoFiles: string[] = $state([]);
    let loadingFiles = $state(false);
    let downloadVlmFlag = $state(false);
    let activeDownloads: Record<string, { percent: number; bytesDownloaded: number; totalBytes: number; status: string; error?: string }> = $state({});

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

    const tabs: { id: SettingsTab; label: string; icon: string }[] = [
        { id: 'providers', label: 'Inference Routing', icon: 'cpu' },
        { id: 'local', label: 'Local Models', icon: 'box' },
        { id: 'ai-skills', label: 'AI Skills', icon: 'brain' },
        { id: 'integrations', label: 'Integrations', icon: 'plug' },
        { id: 'mqtt', label: 'MQTT Broker', icon: 'radio' }
    ];

    let activeDownloadsCount = $derived(Object.keys(activeDownloads).length);
    let localModelsCount = $derived(localModels.length);
    let totalDiskUsed = $derived(localModels.reduce((acc, m) => acc + m.sizeBytes, 0));

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
        expandedIntegrationId = skillId;
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
        if (bytes === 0) return '0 B';
        const k = 1024;
        const sizes = ['B', 'KB', 'MB', 'GB', 'TB'];
        const i = Math.floor(Math.log(bytes) / Math.log(k));
        return parseFloat((bytes / Math.pow(k, i)).toFixed(1)) + ' ' + sizes[i];
    }
</script>

<svelte:head>
    <title>Hawkeye — Settings &amp; Configuration</title>
</svelte:head>

<div class="flex flex-col gap-6 w-full pb-32 page-enter">

    <!-- Header -->
    <header class="flex flex-col lg:flex-row lg:items-end justify-between gap-4">
        <div>
            <div class="flex items-center gap-2.5 mb-2">
                <span class="badge {wsStatus === 'connected' ? 'badge-jade' : 'badge-muted'}">
                    <span class="w-1.5 h-1.5 rounded-full {wsStatus === 'connected' ? 'bg-jade status-pulse' : 'bg-muted-foreground'}"></span>
                    {wsStatus === 'connected' ? 'Config Sync' : 'Offline'}
                </span>
                <span class="text-[11px] text-muted-foreground font-mono">{Object.keys(providers).length} providers · {localModelsCount} local models</span>
            </div>
            <h1 class="text-2xl md:text-3xl font-display font-bold text-foreground tracking-tight leading-none">Model &amp; Inference Settings</h1>
            <p class="text-sm text-muted-foreground mt-2">Configure LLM reasoning engines, cloud provider API keys, and local GGUF models.</p>
        </div>

        <div class="flex items-center gap-2 flex-wrap">
            {#if activeDownloadsCount > 0}
                <div class="flex items-center gap-1.5 px-3 h-9 rounded-lg border border-iris/20 bg-iris/5">
                    <span class="w-1.5 h-1.5 rounded-full bg-iris status-pulse"></span>
                    <span class="text-[10px] font-mono font-bold text-foreground tabular-nums">{activeDownloadsCount}</span>
                    <span class="text-[10px] text-muted-foreground uppercase tracking-wider">downloading</span>
                </div>
            {/if}
            <div class="flex items-center gap-1.5 px-3 h-9 rounded-lg border border-border bg-card">
                <span class="text-[10px] font-mono font-bold text-foreground tabular-nums">{formatBytes(totalDiskUsed)}</span>
                <span class="text-[10px] text-muted-foreground uppercase tracking-wider">on disk</span>
            </div>
        </div>
    </header>

    <!-- Tab navigation -->
    <nav class="panel !p-0 overflow-x-auto">
        <div class="flex items-center px-2 py-2 gap-1">
            {#each tabs as tab (tab.id)}
                <button
                    onclick={() => activeTab = tab.id}
                    class="h-9 px-4 rounded-lg text-[11px] font-bold uppercase tracking-wider transition-all flex items-center gap-2 whitespace-nowrap
                    {activeTab === tab.id
                        ? 'bg-primary text-primary-foreground'
                        : 'text-muted-foreground hover:bg-surface-2 hover:text-foreground'}"
                >
                    {#if tab.icon === 'cpu'}
                        <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="4" y="4" width="16" height="16" rx="2"/><rect x="9" y="9" width="6" height="6"/><line x1="9" y1="1" x2="9" y2="4"/><line x1="15" y1="1" x2="15" y2="4"/><line x1="9" y1="20" x2="9" y2="23"/><line x1="15" y1="20" x2="15" y2="23"/><line x1="20" y1="9" x2="23" y2="9"/><line x1="20" y1="14" x2="23" y2="14"/><line x1="1" y1="9" x2="4" y2="9"/><line x1="1" y1="14" x2="4" y2="14"/></svg>
                    {:else if tab.icon === 'box'}
                        <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 16V8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16z"/><polyline points="3.27 6.96 12 12.01 20.73 6.96"/><line x1="12" y1="22.08" x2="12" y2="12"/></svg>
                    {:else if tab.icon === 'brain'}
                        <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M9.5 2A2.5 2.5 0 0 1 12 4.5v15a2.5 2.5 0 0 1-4.96-.44 2.5 2.5 0 0 1 0-3.12 3 3 0 0 1 0-4.88 2.5 2.5 0 0 1 0-3.12A2.5 2.5 0 0 1 9.5 2Z"/><path d="M14.5 2A2.5 2.5 0 0 0 12 4.5v15a2.5 2.5 0 0 0 4.96-.44 2.5 2.5 0 0 0 0-3.12 3 3 0 0 0 0-4.88 2.5 2.5 0 0 0 0-3.12A2.5 2.5 0 0 0 14.5 2Z"/></svg>
                    {:else if tab.icon === 'plug'}
                        <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 22v-5"/><path d="M9 8V2"/><path d="M15 8V2"/><path d="M18 8v4a4 4 0 0 1-4 4h-4a4 4 0 0 1-4-4V8z"/></svg>
                    {:else if tab.icon === 'radio'}
                        <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="2"/><path d="M16.24 7.76a6 6 0 0 1 0 8.49m-8.48-.01a6 6 0 0 1 0-8.49m11.31-2.82a10 10 0 0 1 0 14.14m-14.14 0a10 10 0 0 1 0-14.14"/></svg>
                    {/if}
                    {tab.label}
                </button>
            {/each}
        </div>
    </nav>

    <!-- Main body -->
    <div class="flex flex-col gap-5">
        {#if activeTab === 'providers'}
            <div class="flex flex-col gap-2">
                <h2 class="text-lg font-display font-semibold text-foreground">Active Inference Routing</h2>
                <p class="text-sm text-muted-foreground">Choose whether Hawkeye routes reasoning and scene description tasks to local llama-server instances or third-party cloud API providers.</p>
            </div>

            <div class="grid grid-cols-1 lg:grid-cols-2 gap-5">
                <!-- LLM Card -->
                <section class="panel page-enter">
                    <div class="panel-header">
                        <div class="flex items-center gap-2.5">
                            <div class="w-8 h-8 rounded-lg bg-iris/10 border border-iris/20 flex items-center justify-center text-iris">
                                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="3"/><path d="M12 1v6m0 6v6m11-7h-6m-6 0H1"/></svg>
                            </div>
                            <div>
                                <h2 class="text-sm font-display font-semibold text-foreground">Text Reasoning / Agent Chat</h2>
                                <p class="text-[10px] text-muted-foreground font-mono">LLM inference pipeline</p>
                            </div>
                        </div>
                        {#if activeInference.llm.type === 'local-engine'}
                            <span class="badge badge-jade">✓ Local</span>
                        {:else if activeInference.llm.provider && providers[activeInference.llm.provider]?.apiKey}
                            <span class="badge badge-jade">✓ Configured</span>
                        {:else}
                            <span class="badge badge-gold">⚠ Key Required</span>
                        {/if}
                    </div>

                    <div class="panel-body flex flex-col gap-4">
                        <div class="flex flex-col gap-1.5">
                            <label for="llm-type" class="section-eyebrow">Inference Engine Type</label>
                            <select id="llm-type" class="select" bind:value={activeInference.llm.type} onchange={() => {
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
                                <label for="llm-model-select" class="section-eyebrow">Selected Local LLM Model</label>
                                <select id="llm-model-select" class="select" bind:value={activeInference.llm.modelId}>
                                    <option value={null}>-- Use default local LLM --</option>
                                    {#each localModels.filter(m => !m.isVlm) as model}
                                        <option value={model.name}>{model.name} ({Math.round(model.sizeBytes / (1024 * 1024))} MB)</option>
                                    {/each}
                                </select>
                            </div>
                            <div class="flex flex-col gap-1.5">
                                <label for="llm-port" class="section-eyebrow">Local llama-server Port</label>
                                <input type="number" id="llm-port" class="input font-mono" bind:value={activeInference.llm.port} placeholder="5411" />
                            </div>
                        {:else}
                            {@const pId = activeInference.llm.provider}
                            <div class="flex flex-col gap-1.5">
                                <label for="llm-provider" class="section-eyebrow">Active Cloud Provider</label>
                                <select id="llm-provider" class="select" bind:value={activeInference.llm.provider} onchange={() => {
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

                            {#if pId && providers[pId]}
                                <div class="flex flex-col gap-1.5">
                                    <label for="api-key-llm" class="section-eyebrow">API Key</label>
                                    <div class="relative">
                                        <input
                                            type={showApiKeys['llm-' + pId] ? 'text' : 'password'}
                                            id="api-key-llm"
                                            class="input pr-10 font-mono text-[11px]"
                                            bind:value={providers[pId].apiKey}
                                            placeholder="Enter secret API key..."
                                        />
                                        <button type="button" class="absolute right-2 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-foreground p-1.5 rounded hover:bg-surface-2" onclick={() => showApiKeys['llm-' + pId] = !showApiKeys['llm-' + pId]}>
                                            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                                                {#if showApiKeys['llm-' + pId]}
                                                    <path d="M17.94 17.94A10.07 10.07 0 0 1 12 20c-7 0-11-8-11-8a18.45 18.45 0 0 1 5.06-5.94M9.9 4.24A9.12 9.12 0 0 1 12 4c7 0 11 8 11 8a18.5 18.5 0 0 1-2.16 3.19m-6.72-1.07a3 3 0 1 1-4.24-4.24"/>
                                                    <line x1="1" y1="1" x2="23" y2="23"/>
                                                {:else}
                                                    <path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><circle cx="12" cy="12" r="3"/>
                                                {/if}
                                            </svg>
                                        </button>
                                    </div>
                                </div>

                                <div class="flex flex-col gap-1.5">
                                    <label for="base-url-llm" class="section-eyebrow">Base API URL</label>
                                    <input type="text" id="base-url-llm" class="input font-mono text-[11px]" bind:value={providers[pId].baseUrl} />
                                </div>

                                <div class="flex flex-col gap-1.5">
                                    <label for="available-models-llm" class="section-eyebrow">Available Models (comma-separated)</label>
                                    <input
                                        type="text"
                                        id="available-models-llm"
                                        class="input font-mono text-[11px]"
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
                                    <label for="llm-model" class="section-eyebrow">Model ID</label>
                                    <select id="llm-model" class="select" bind:value={activeInference.llm.cloudModelId}>
                                        {#each providers[pId].availableModels as model}
                                            <option value={model}>{model}</option>
                                        {/each}
                                    </select>
                                </div>

                                <button type="button" class="btn btn-primary btn-sm mt-2" disabled={testingProviders[pId] || !providers[pId].apiKey} onclick={() => testProvider(pId, activeInference.llm.cloudModelId)}>
                                    {#if testingProviders[pId]}
                                        <span class="w-3 h-3 rounded-full border-2 border-current border-t-transparent animate-spin"></span>
                                        Testing…
                                    {:else}
                                        <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"/><polyline points="22 4 12 14.01 9 11.01"/></svg>
                                        Test Connection
                                    {/if}
                                </button>

                                {#if testResults[pId]}
                                    <div class="p-2.5 rounded-lg border text-xs flex items-start gap-2
                                        {testResults[pId].success ? 'bg-jade/10 border-jade/30 text-jade' : 'bg-crimson/10 border-crimson/30 text-crimson'}">
                                        <span class="shrink-0 mt-0.5">
                                            {#if testResults[pId].success}
                                                <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><polyline points="20 6 9 17 4 12"/></svg>
                                            {:else}
                                                <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
                                            {/if}
                                        </span>
                                        <span class="break-all">{testResults[pId].success ? (testResults[pId].response || 'Connection successful!') : (testResults[pId].error || 'Unexpected error')}</span>
                                    </div>
                                {/if}
                            {/if}
                        {/if}
                    </div>
                </section>

                <!-- VLM Card -->
                <section class="panel page-enter stagger-1">
                    <div class="panel-header">
                        <div class="flex items-center gap-2.5">
                            <div class="w-8 h-8 rounded-lg bg-ember/10 border border-ember/20 flex items-center justify-center text-ember">
                                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><circle cx="12" cy="12" r="3"/></svg>
                            </div>
                            <div>
                                <h2 class="text-sm font-display font-semibold text-foreground">Vision Scene Analysis</h2>
                                <p class="text-[10px] text-muted-foreground font-mono">VLM inference pipeline</p>
                            </div>
                        </div>
                        {#if activeInference.vlm.type === 'local-engine'}
                            <span class="badge badge-jade">✓ Local</span>
                        {:else if activeInference.vlm.provider && providers[activeInference.vlm.provider]?.apiKey}
                            <span class="badge badge-jade">✓ Configured</span>
                        {:else}
                            <span class="badge badge-gold">⚠ Key Required</span>
                        {/if}
                    </div>

                    <div class="panel-body flex flex-col gap-4">
                        <div class="flex flex-col gap-1.5">
                            <label for="vlm-type" class="section-eyebrow">Inference Engine Type</label>
                            <select id="vlm-type" class="select" bind:value={activeInference.vlm.type} onchange={() => {
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
                                <label for="vlm-model-select" class="section-eyebrow">Selected Local VLM Model</label>
                                <select id="vlm-model-select" class="select" bind:value={activeInference.vlm.modelId}>
                                    <option value={null}>-- Use default local VLM (llava) --</option>
                                    {#each localModels.filter(m => m.isVlm) as model}
                                        <option value={model.name}>{model.name} ({Math.round(model.sizeBytes / (1024 * 1024))} MB)</option>
                                    {/each}
                                </select>
                            </div>
                            <div class="flex flex-col gap-1.5">
                                <label for="vlm-port" class="section-eyebrow">Local llama-server Port</label>
                                <input type="number" id="vlm-port" class="input font-mono" bind:value={activeInference.vlm.port} placeholder="5405" />
                            </div>
                        {:else}
                            {@const pId = activeInference.vlm.provider}
                            <div class="flex flex-col gap-1.5">
                                <label for="vlm-provider" class="section-eyebrow">Active Cloud Provider</label>
                                <select id="vlm-provider" class="select" bind:value={activeInference.vlm.provider} onchange={() => {
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

                            {#if pId && providers[pId]}
                                <div class="flex flex-col gap-1.5">
                                    <label for="api-key-vlm" class="section-eyebrow">API Key</label>
                                    <div class="relative">
                                        <input
                                            type={showApiKeys['vlm-' + pId] ? 'text' : 'password'}
                                            id="api-key-vlm"
                                            class="input pr-10 font-mono text-[11px]"
                                            bind:value={providers[pId].apiKey}
                                            placeholder="Enter secret API key..."
                                        />
                                        <button type="button" class="absolute right-2 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-foreground p-1.5 rounded hover:bg-surface-2" onclick={() => showApiKeys['vlm-' + pId] = !showApiKeys['vlm-' + pId]}>
                                            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                                                {#if showApiKeys['vlm-' + pId]}
                                                    <path d="M17.94 17.94A10.07 10.07 0 0 1 12 20c-7 0-11-8-11-8a18.45 18.45 0 0 1 5.06-5.94M9.9 4.24A9.12 9.12 0 0 1 12 4c7 0 11 8 11 8a18.5 18.5 0 0 1-2.16 3.19m-6.72-1.07a3 3 0 1 1-4.24-4.24"/>
                                                    <line x1="1" y1="1" x2="23" y2="23"/>
                                                {:else}
                                                    <path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><circle cx="12" cy="12" r="3"/>
                                                {/if}
                                            </svg>
                                        </button>
                                    </div>
                                </div>

                                <div class="flex flex-col gap-1.5">
                                    <label for="base-url-vlm" class="section-eyebrow">Base API URL</label>
                                    <input type="text" id="base-url-vlm" class="input font-mono text-[11px]" bind:value={providers[pId].baseUrl} />
                                </div>

                                <div class="flex flex-col gap-1.5">
                                    <label for="available-models-vlm" class="section-eyebrow">Available Models (comma-separated)</label>
                                    <input
                                        type="text"
                                        id="available-models-vlm"
                                        class="input font-mono text-[11px]"
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
                                    <label for="vlm-model" class="section-eyebrow">Model ID</label>
                                    <select id="vlm-model" class="select" bind:value={activeInference.vlm.cloudModelId}>
                                        {#each providers[pId].availableModels as model}
                                            <option value={model}>{model}</option>
                                        {/each}
                                    </select>
                                </div>

                                <button type="button" class="btn btn-ember btn-sm mt-2" disabled={testingProviders[pId] || !providers[pId].apiKey} onclick={() => testProvider(pId, activeInference.vlm.cloudModelId)}>
                                    {#if testingProviders[pId]}
                                        <span class="w-3 h-3 rounded-full border-2 border-current border-t-transparent animate-spin"></span>
                                        Testing…
                                    {:else}
                                        <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"/><polyline points="22 4 12 14.01 9 11.01"/></svg>
                                        Test Connection
                                    {/if}
                                </button>

                                {#if testResults[pId]}
                                    <div class="p-2.5 rounded-lg border text-xs flex items-start gap-2
                                        {testResults[pId].success ? 'bg-jade/10 border-jade/30 text-jade' : 'bg-crimson/10 border-crimson/30 text-crimson'}">
                                        <span class="shrink-0 mt-0.5">
                                            {#if testResults[pId].success}
                                                <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><polyline points="20 6 9 17 4 12"/></svg>
                                            {:else}
                                                <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
                                            {/if}
                                        </span>
                                        <span class="break-all">{testResults[pId].success ? (testResults[pId].response || 'Connection successful!') : (testResults[pId].error || 'Unexpected error')}</span>
                                    </div>
                                {/if}
                            {/if}
                        {/if}
                    </div>
                </section>
            </div>
        {/if}

        {#if activeTab === 'local'}
            <div class="flex flex-col gap-2">
                <h2 class="text-lg font-display font-semibold text-foreground">Local Model Downloader</h2>
                <p class="text-sm text-muted-foreground">Download curated GGUF weights or search Hugging Face Hub for custom quantizations.</p>
            </div>

            <!-- Curated -->
            <section>
                <h3 class="section-eyebrow mb-3">Curated Downloads</h3>
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

                        <div class="panel flex flex-col gap-3 transition-all relative
                            {isDownloaded ? 'border-jade/30' : ''}">

                            <div class="flex flex-col gap-2">
                                <div class="flex justify-between items-center">
                                    <span class="badge {model.isBundle ? 'badge-ember' : model.isVlm ? 'badge-ember' : 'badge-iris'}">
                                        {model.isBundle ? 'VLM Bundle' : (model.isVlm ? 'VLM' : 'LLM')}
                                    </span>
                                    {#if isDownloaded}
                                        <span class="badge badge-jade">
                                            <svg width="9" height="9" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3"><polyline points="20 6 9 17 4 12"/></svg>
                                            Ready
                                        </span>
                                    {/if}
                                </div>
                                <h3 class="text-sm font-display font-semibold text-foreground">{model.name}</h3>
                                <p class="text-xs text-muted-foreground leading-relaxed line-clamp-2 min-h-[2rem]">{model.desc}</p>
                                <code class="font-mono text-[9px] bg-surface-2 border border-border px-1.5 py-0.5 rounded text-muted-foreground w-fit max-w-full truncate block">{model.repo}</code>
                            </div>

                            <div class="pt-2 border-t border-border">
                                {#if isDownloaded}
                                    <div class="w-full text-center text-xs font-bold text-jade bg-jade/5 py-2 rounded-md border border-jade/20">
                                        <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" class="inline -mt-0.5 mr-1"><polyline points="20 6 9 17 4 12"/></svg>
                                        Downloaded
                                    </div>
                                {:else}
                                    <button
                                        class="btn {isDownloading ? 'btn-ghost' : 'btn-primary'} btn-sm w-full"
                                        disabled={isDownloading}
                                        onclick={() => triggerCuratedDownload(model)}
                                    >
                                        {#if isDownloading}
                                            <span class="w-3 h-3 rounded-full border-2 border-current border-t-transparent animate-spin"></span>
                                            {activeDlPercent}%
                                        {:else}
                                            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">
                                                <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/>
                                                <polyline points="7 10 12 15 17 10"/>
                                                <line x1="12" y1="15" x2="12" y2="3"/>
                                            </svg>
                                            Download Bundle
                                        {/if}
                                    </button>
                                {/if}
                            </div>
                        </div>
                    {/each}
                </div>
            </section>

            <!-- HuggingFace search -->
            <section class="flex flex-col gap-3">
                <h3 class="section-eyebrow">Hugging Face Repository Search</h3>

                <div class="p-3.5 rounded-lg border border-iris/20 bg-iris/5 text-xs text-muted-foreground flex items-start gap-2.5 max-w-3xl">
                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" class="text-iris shrink-0 mt-0.5">
                        <circle cx="12" cy="12" r="10"/><path d="M12 16v-4M12 8h.01"/>
                    </svg>
                    <div class="leading-relaxed">
                        <strong class="text-iris font-bold">Local Moondream VLM Guide:</strong> The local Moondream vision pipeline requires downloading <strong>both</strong> the text model `.gguf` AND the vision projector `mmproj` `.gguf` file. Search for <code>moondream/moondream2-gguf</code> or browse our quick curated items above to download both components.
                    </div>
                </div>

                <div class="flex gap-2 max-w-3xl">
                    <div class="relative flex-1">
                        <input
                            type="text"
                            class="input !h-10 pl-9"
                            bind:value={hfSearchQuery}
                            placeholder="Search keywords or full repo IDs…"
                            onkeydown={(e) => e.key === 'Enter' && searchHuggingFace()}
                        />
                        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" class="absolute left-3 top-1/2 -translate-y-1/2 text-muted-foreground">
                            <circle cx="11" cy="11" r="8"/><path d="m21 21-4.3-4.3"/>
                        </svg>
                    </div>
                    <button class="btn btn-primary" disabled={hfSearching} onclick={searchHuggingFace}>
                        {#if hfSearching}
                            <span class="w-3.5 h-3.5 border-2 border-primary-foreground border-t-transparent rounded-full animate-spin"></span>
                            Searching…
                        {:else}
                            Search Repo
                        {/if}
                    </button>
                </div>

                {#if hfSearchResults.length > 0}
                    <div class="panel !p-0 overflow-hidden max-w-3xl">
                        <div class="px-4 py-2.5 border-b border-border flex items-center justify-between">
                            <span class="section-eyebrow">Search Results</span>
                            <span class="text-[10px] font-mono text-muted-foreground">{hfSearchResults.length} repos</span>
                        </div>
                        <div class="flex flex-col gap-1 p-2 max-h-64 overflow-y-auto no-scrollbar">
                            {#each hfSearchResults as result}
                                <div class="flex items-center justify-between p-2.5 rounded-md border border-border bg-surface-2/30 hover:bg-surface-2 transition-colors gap-4 {selectedRepoId === result.id ? 'border-iris/40 bg-iris/5' : ''}">
                                    <div class="flex flex-col gap-0.5 min-w-0">
                                        <strong class="text-xs font-bold text-foreground truncate font-mono">{result.id}</strong>
                                        <span class="text-[10px] text-muted-foreground font-mono">↓ {result.downloads.toLocaleString()} · ♥ {result.likes.toLocaleString()}</span>
                                    </div>
                                    <button class="btn btn-sm" onclick={() => selectRepo(result.id)}>
                                        Browse Files
                                    </button>
                                </div>
                            {/each}
                        </div>
                    </div>
                {/if}

                {#if selectedRepoId}
                    <div class="panel max-w-3xl">
                        <div class="panel-header">
                            <div class="flex flex-col gap-0.5 min-w-0">
                                <span class="section-eyebrow">Repository Connected</span>
                                <h4 class="text-sm font-bold text-foreground font-mono truncate">{selectedRepoId}</h4>
                            </div>
                            <label class="relative inline-flex items-center cursor-pointer">
                                <input type="checkbox" bind:checked={downloadVlmFlag} class="sr-only peer" />
                                <div class="w-9 h-5 rounded-full bg-muted border border-border peer-checked:bg-ember peer-checked:border-ember transition-all relative
                                    after:content-[''] after:absolute after:top-0.5 after:left-0.5 after:w-3.5 after:h-3.5 after:rounded-full after:bg-foreground after:transition-transform peer-checked:after:translate-x-4
                                    peer-focus-visible:ring-2 peer-focus-visible:ring-ring peer-focus-visible:ring-offset-2 peer-focus-visible:ring-offset-background"></div>
                                <span class="ml-2.5 text-[10px] font-bold uppercase tracking-wider {downloadVlmFlag ? 'text-ember' : 'text-muted-foreground'}">
                                    vlm_models/
                                </span>
                            </label>
                        </div>

                        <div class="panel-body">
                            {#if loadingFiles}
                                <div class="flex items-center gap-2 py-4 justify-center text-xs text-muted-foreground font-mono">
                                    <span class="w-4 h-4 border-2 border-primary border-t-transparent rounded-full animate-spin"></span>
                                    Fetching repository GGUF file lists...
                                </div>
                            {:else if repoFiles.length === 0}
                                <p class="text-xs text-muted-foreground py-4 text-center">No `.gguf` files detected in this repository root.</p>
                            {:else}
                                <div class="flex flex-col gap-1.5 max-h-72 overflow-y-auto no-scrollbar">
                                    {#each repoFiles as filename}
                                        {@const downloadId = `${selectedRepoId}/${filename}`}
                                        {@const activeDl = activeDownloads[downloadId]}
                                        {@const isDownloaded = localModels.some(m => m.name === filename)}
                                        <div class="flex justify-between items-center p-2.5 rounded-md border border-border bg-surface-2/30 hover:bg-surface-2 transition-colors gap-4">
                                            <span class="text-xs font-mono text-foreground truncate">{filename}</span>
                                            <div class="shrink-0">
                                                {#if isDownloaded}
                                                    <span class="badge badge-jade">Downloaded</span>
                                                {:else if activeDl}
                                                    <span class="badge badge-iris">
                                                        <span class="w-1 h-1 rounded-full bg-iris status-pulse"></span>
                                                        {activeDl.percent}% · {formatBytes(activeDl.bytesDownloaded)}
                                                    </span>
                                                {:else}
                                                    <button class="btn btn-sm" onclick={() => startDownload(selectedRepoId!, filename)}>
                                                        Download
                                                    </button>
                                                {/if}
                                            </div>
                                        </div>
                                    {/each}
                                </div>
                            {/if}
                        </div>
                    </div>
                {/if}
            </section>

            <!-- Active Downloads -->
            {#if Object.keys(activeDownloads).length > 0}
                <section class="flex flex-col gap-3">
                    <h3 class="section-eyebrow flex items-center gap-1.5">
                        <span class="w-1.5 h-1.5 rounded-full bg-iris status-pulse"></span>
                        Active Background Downloads
                    </h3>
                    <div class="flex flex-col gap-2 max-w-3xl">
                        {#each Object.entries(activeDownloads) as [id, dl]}
                            <div class="p-3.5 rounded-lg border border-iris/20 bg-iris/5 flex flex-col gap-2">
                                <div class="flex justify-between items-center text-xs">
                                    <strong class="font-mono text-foreground truncate max-w-[70%]">{id.split('/').pop()}</strong>
                                    <span class="text-muted-foreground font-mono tabular-nums">{formatBytes(dl.bytesDownloaded)} / {formatBytes(dl.totalBytes)}</span>
                                </div>
                                <div class="h-1.5 w-full bg-border rounded-full overflow-hidden">
                                    <div class="h-full bg-gradient-to-r from-iris to-cyan transition-all duration-300" style="width: {dl.percent}%"></div>
                                </div>
                                <div class="flex justify-between items-center text-[10px] font-mono">
                                    <span class="font-bold uppercase tracking-wider text-iris flex items-center gap-1.5">
                                        <span class="w-1.5 h-1.5 rounded-full bg-iris status-pulse"></span>
                                        {dl.status === 'downloading' ? 'Downloading Weights' : dl.status}
                                    </span>
                                    <span class="text-foreground font-bold tabular-nums">{dl.percent}%</span>
                                </div>
                                {#if dl.error}
                                    <div class="text-[10px] text-crimson font-mono bg-crimson/10 border border-crimson/20 p-2 rounded-md mt-1">
                                        Error: {dl.error}
                                    </div>
                                {/if}
                            </div>
                        {/each}
                    </div>
                </section>
            {/if}

            <!-- Downloaded Models -->
            <section class="flex flex-col gap-3">
                <h3 class="section-eyebrow">Downloaded Local Models · {localModels.length}</h3>
                {#if localModels.length === 0}
                    <div class="flex flex-col items-center justify-center p-10 border border-dashed border-border rounded-2xl text-center max-w-3xl gap-2">
                        <div class="w-12 h-12 rounded-xl bg-surface-2 border border-border flex items-center justify-center mb-2">
                            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" class="text-muted-foreground/60">
                                <path d="M21 16V8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16z"/>
                            </svg>
                        </div>
                        <h4 class="text-sm font-display font-semibold text-foreground">No Local Models Detected</h4>
                        <p class="text-[11px] text-muted-foreground max-w-xs">Use the quick curated downloads or the Hugging Face search above to pull files to your server core.</p>
                    </div>
                {:else}
                    <div class="flex flex-col gap-2 max-w-3xl">
                        {#each localModels as model}
                            <div class="flex items-center justify-between p-3.5 rounded-lg border border-border bg-surface-2/30 hover:bg-surface-2 transition-colors gap-4">
                                <div class="flex items-center gap-3 min-w-0">
                                    <span class="badge {model.isVlm ? 'badge-ember' : 'badge-iris'} shrink-0">{model.isVlm ? 'VLM' : 'LLM'}</span>
                                    <div class="flex flex-col gap-0.5 min-w-0">
                                        <strong class="text-xs font-bold text-foreground truncate font-mono">{model.name}</strong>
                                        <span class="text-[10px] text-muted-foreground font-mono">Size: {formatBytes(model.sizeBytes)} · <code>{model.isVlm ? 'vlm_models/' : 'models/'}</code></span>
                                    </div>
                                </div>
                                <button class="btn-icon !w-7 !h-7 hover:!text-crimson hover:!border-crimson/30" onclick={() => deleteModel(model.name, model.isVlm)} title="Delete file">
                                    <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">
                                        <path d="M3 6h18M19 6v14c0 1-1 2-2 2H7c-1-1-2-1-2-2V6M8 6V4c0-1 1-2 2-2h4c1 0 2 1 2 2v2M10 11v6M14 11v6"/>
                                    </svg>
                                </button>
                            </div>
                        {/each}
                    </div>
                {/if}
            </section>
        {/if}

        {#if activeTab === 'ai-skills' || activeTab === 'integrations'}
            {@const skillList = activeTab === 'ai-skills' ? aiSkills : integrationSkills}
            <div class="flex flex-col gap-2">
                <h2 class="text-lg font-display font-semibold text-foreground">
                    {activeTab === 'ai-skills' ? 'AI Perception & Detection' : 'Daemon Services & Integrations'}
                </h2>
                <p class="text-sm text-muted-foreground">
                    {activeTab === 'ai-skills'
                        ? 'Configure, deploy, and select model sizes for real-time computer vision, object detection, and VLMs.'
                        : 'Manage camera IP connection providers, streaming targets, notification channels, and automation triggers.'}
                </p>
            </div>

            <div class="flex flex-col gap-3 max-w-4xl">
                {#each skillList as skill (skill.id)}
                    {@const isExpanded = expandedIntegrationId === skill.id}
                    {@const isRunning = skill.isRunning}
                    {@const activeDl = isDeployingSkill[skill.id]}

                    <div class="panel !p-0 overflow-hidden transition-all
                        {isExpanded ? 'shadow-[0_0_0_1px_hsl(var(--iris)/0.3)]' : ''}
                        {isRunning ? 'border-l-2 border-l-jade' : ''}">

                        <button class="w-full text-left p-4 hover:bg-surface-2/30 transition-colors flex items-center justify-between gap-4 cursor-pointer" onclick={() => expandedIntegrationId = isExpanded ? null : skill.id}>
                            <div class="flex items-center gap-3.5 min-w-0">
                                <div class="w-9 h-9 rounded-lg flex items-center justify-center shrink-0
                                    {isRunning ? 'bg-jade/10 border border-jade/20 text-jade' : skill.isInstalled ? 'bg-iris/10 border border-iris/20 text-iris' : 'bg-surface-2 border border-border text-muted-foreground'}">
                                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                                        <path d="M12 2L2 7l10 5 10-5-10-5z"/><path d="M2 17l10 5 10-5M2 12l10 5 10-5"/>
                                    </svg>
                                </div>
                                <div class="flex flex-col gap-0.5 min-w-0">
                                    <div class="flex items-center gap-2">
                                        <span class="badge {activeTab === 'ai-skills' ? 'badge-iris' : 'badge-ember'} uppercase">{skill.category}</span>
                                        <h4 class="text-sm font-semibold text-foreground truncate">{skill.name}</h4>
                                    </div>
                                    <p class="text-xs text-muted-foreground truncate leading-relaxed">{skill.description}</p>
                                </div>
                            </div>
                            <div class="flex items-center gap-3 shrink-0">
                                <div class="flex items-center gap-1.5">
                                    {#if isRunning}
                                        <span class="w-1.5 h-1.5 rounded-full bg-jade status-pulse"></span>
                                        <span class="text-[10px] font-mono font-bold text-jade uppercase tracking-wider">Running</span>
                                    {:else if skill.isInstalled}
                                        <span class="w-1.5 h-1.5 rounded-full bg-iris"></span>
                                        <span class="text-[10px] font-mono font-bold text-iris uppercase tracking-wider">Ready</span>
                                    {:else}
                                        <span class="w-1.5 h-1.5 rounded-full bg-muted-foreground"></span>
                                        <span class="text-[10px] font-mono font-bold text-muted-foreground uppercase tracking-wider">Deployable</span>
                                    {/if}
                                </div>
                                <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"
                                    class="text-muted-foreground transition-transform {isExpanded ? 'rotate-180' : ''}">
                                    <polyline points="6 9 12 15 18 9"/>
                                </svg>
                            </div>
                        </button>

                        {#if isExpanded}
                            <div class="px-4 py-4 border-t border-border bg-surface-2/30 flex flex-col gap-4" transition:slide={{ duration: 200 }}>
                                {#if skill.configParams && skill.configParams.length > 0}
                                    <div class="grid grid-cols-1 sm:grid-cols-2 gap-4 pb-4 border-b border-border/40">
                                        {#each skill.configParams as param}
                                            <div class="flex flex-col gap-1.5">
                                                <label for="param-{skill.id}-{param.name}" class="section-eyebrow">{param.label}</label>
                                                {#if param.type === 'select'}
                                                    <select id="param-{skill.id}-{param.name}" class="select" bind:value={skillConfigValues[skill.id][param.name]} disabled={isRunning}>
                                                        {#each param.options || [] as opt}
                                                            {#if typeof opt === 'object'}
                                                                <option value={opt.value}>{opt.label}</option>
                                                            {:else}
                                                                <option value={opt}>{opt}</option>
                                                            {/if}
                                                        {/each}
                                                    </select>
                                                {:else if param.type === 'boolean'}
                                                    <label class="flex items-center gap-2.5 p-2.5 rounded-md border border-border bg-surface-2 cursor-pointer hover:bg-surface-3 transition-colors">
                                                        <input type="checkbox" bind:checked={skillConfigValues[skill.id][param.name]} class="checkbox" disabled={isRunning} />
                                                        <span class="text-xs font-semibold text-foreground">Enable</span>
                                                    </label>
                                                {:else if param.type === 'number'}
                                                    <input
                                                        type="number"
                                                        id="param-{skill.id}-{param.name}"
                                                        class="input font-mono"
                                                        bind:value={skillConfigValues[skill.id][param.name]}
                                                        min={param.min}
                                                        max={param.max}
                                                        disabled={isRunning}
                                                    />
                                                {:else if param.type === 'password'}
                                                    <input
                                                        type="password"
                                                        id="param-{skill.id}-{param.name}"
                                                        class="input font-mono"
                                                        bind:value={skillConfigValues[skill.id][param.name]}
                                                        disabled={isRunning}
                                                    />
                                                {:else}
                                                    <input
                                                        type="text"
                                                        id="param-{skill.id}-{param.name}"
                                                        class="input"
                                                        bind:value={skillConfigValues[skill.id][param.name]}
                                                        placeholder={param.placeholder || ''}
                                                        disabled={isRunning}
                                                    />
                                                {/if}
                                                {#if param.description}
                                                    <p class="text-[10px] text-muted-foreground leading-relaxed mt-0.5">{param.description}</p>
                                                {/if}
                                            </div>
                                        {/each}
                                    </div>
                                {/if}

                                <div class="flex flex-wrap gap-2.5">
                                    {#if !skill.isInstalled}
                                        <button class="btn btn-iris btn-sm" disabled={activeDl} onclick={() => deploySkill(skill.id)}>
                                            {#if activeDl}
                                                <span class="w-3 h-3 rounded-full border-2 border-current border-t-transparent animate-spin"></span>
                                                Deploying…
                                            {:else}
                                                <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">
                                                    <path d="M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.77-3.77a6 6 0 0 1-7.94 7.94l-6.91 6.91a2.12 2.12 0 0 1-3-3l6.91-6.91a6 6 0 0 1 7.94-7.94l-3.76 3.76z"/>
                                                </svg>
                                                Deploy &amp; Pre-convert
                                            {/if}
                                        </button>
                                    {:else if isRunning}
                                        <button class="btn btn-crimson btn-sm" onclick={() => stopSkill(skill.id)}>
                                            <svg width="12" height="12" viewBox="0 0 24 24" fill="currentColor"><rect x="6" y="6" width="12" height="12" rx="1"/></svg>
                                            Stop Pipeline
                                        </button>
                                    {:else}
                                        <button class="btn btn-jade btn-sm" onclick={() => startSkill(skill.id)} style="background-color: hsl(var(--jade)); color: hsl(var(--jade-foreground)); border-color: hsl(var(--jade));">
                                            <svg width="12" height="12" viewBox="0 0 24 24" fill="currentColor"><polygon points="6 4 20 12 6 20 6 4"/></svg>
                                            Start Pipeline
                                        </button>
                                    {/if}
                                </div>

                                {#if deployLogs[skill.id]}
                                    <div class="rounded-lg border border-border bg-black/70 overflow-hidden">
                                        <div class="flex justify-between items-center text-[10px] text-muted-foreground border-b border-border/40 px-3 py-2">
                                            <span class="font-mono text-iris">deploy-process@{skill.id}:~</span>
                                            {#if activeDl}
                                                <div class="flex items-center gap-1.5 font-bold text-gold">
                                                    <span class="w-1.5 h-1.5 rounded-full bg-gold status-pulse"></span>
                                                    <span>Compiling &amp; Optimizing…</span>
                                                </div>
                                            {:else}
                                                <span class="font-bold text-jade flex items-center gap-1">
                                                    <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3"><polyline points="20 6 9 17 4 12"/></svg>
                                                    Complete
                                                </span>
                                            {/if}
                                        </div>
                                        <pre class="font-mono text-[10px] text-zinc-300 bg-transparent p-3 overflow-x-auto max-h-48 text-left leading-relaxed whitespace-pre-wrap">{deployLogs[skill.id]}</pre>
                                    </div>
                                {/if}

                                {#if isRunning && executionLogs[skill.id]}
                                    <div class="rounded-lg border border-jade/30 bg-black/70 overflow-hidden">
                                        <div class="flex justify-between items-center text-[10px] text-muted-foreground border-b border-border/40 px-3 py-2">
                                            <span class="font-mono text-jade">stdout@{skill.id}:~</span>
                                            <span class="font-bold text-jade flex items-center gap-1.5">
                                                <span class="w-1.5 h-1.5 rounded-full bg-jade status-pulse"></span>
                                                Live Terminal
                                            </span>
                                        </div>
                                        <pre class="font-mono text-[10px] text-zinc-300 bg-transparent p-3 overflow-x-auto max-h-48 text-left leading-relaxed whitespace-pre-wrap">{executionLogs[skill.id]}</pre>
                                    </div>
                                {/if}
                            </div>
                        {/if}
                    </div>
                {/each}
            </div>
        {/if}

        {#if activeTab === 'mqtt'}
            <div class="flex flex-col gap-2">
                <h2 class="text-lg font-display font-semibold text-foreground">MQTT Broker Configuration</h2>
                <p class="text-sm text-muted-foreground">Publish security events, alert diagnostics, and active camera object sighting topics to a local or remote MQTT broker.</p>
            </div>

            <section class="panel max-w-2xl" in:fade={{ duration: 150 }}>
                <div class="panel-header">
                    <div class="flex items-center gap-2.5">
                        <div class="w-8 h-8 rounded-lg flex items-center justify-center
                            {mqttConfig.enabled ? 'bg-jade/10 border border-jade/20 text-jade' : 'bg-surface-2 border border-border text-muted-foreground'}">
                            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                                <circle cx="12" cy="12" r="2"/><path d="M16.24 7.76a6 6 0 0 1 0 8.49m-8.48-.01a6 6 0 0 1 0-8.49m11.31-2.82a10 10 0 0 1 0 14.14m-14.14 0a10 10 0 0 1 0-14.14"/>
                            </svg>
                        </div>
                        <div>
                            <h3 class="text-sm font-display font-semibold text-foreground">MQTT Integration</h3>
                            <p class="text-[10px] text-muted-foreground font-mono">{mqttConfig.enabled ? 'Active' : 'Disabled'} · {mqttConfig.broker}:{mqttConfig.port}</p>
                        </div>
                    </div>
                    <label class="relative inline-flex items-center cursor-pointer">
                        <input type="checkbox" bind:checked={mqttConfig.enabled} class="sr-only peer" />
                        <div class="w-10 h-5 rounded-full bg-muted border border-border peer-checked:bg-jade peer-checked:border-jade transition-all relative
                            after:content-[''] after:absolute after:top-0.5 after:left-0.5 after:w-3.5 after:h-3.5 after:rounded-full after:bg-foreground after:transition-transform peer-checked:after:translate-x-5
                            peer-focus-visible:ring-2 peer-focus-visible:ring-ring peer-focus-visible:ring-offset-2 peer-focus-visible:ring-offset-background"></div>
                    </label>
                </div>

                <div class="panel-body">
                    <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
                        <div class="flex flex-col gap-1.5">
                            <label class="section-eyebrow" for="mqtt-broker">Broker Host Address</label>
                            <input type="text" id="mqtt-broker" class="input font-mono" bind:value={mqttConfig.broker} placeholder="mqtt://192.168.1.10" />
                        </div>
                        <div class="flex flex-col gap-1.5">
                            <label class="section-eyebrow" for="mqtt-port">Broker Port</label>
                            <input type="number" id="mqtt-port" class="input font-mono" bind:value={mqttConfig.port} placeholder="1883" />
                        </div>
                        <div class="flex flex-col gap-1.5">
                            <label class="section-eyebrow" for="mqtt-user">Username (Optional)</label>
                            <input type="text" id="mqtt-user" class="input" bind:value={mqttConfig.username} placeholder="Username" />
                        </div>
                        <div class="flex flex-col gap-1.5">
                            <label class="section-eyebrow" for="mqtt-pass">Password (Optional)</label>
                            <input type="password" id="mqtt-pass" class="input font-mono" bind:value={mqttConfig.password} placeholder="••••••••" />
                        </div>
                        <div class="flex flex-col gap-1.5 md:col-span-2">
                            <label class="section-eyebrow" for="mqtt-prefix">Topic Prefix</label>
                            <input type="text" id="mqtt-prefix" class="input font-mono" bind:value={mqttConfig.topicPrefix} placeholder="hawkeye" />
                            <span class="text-[10px] text-muted-foreground font-mono">Binary sensors publish to: <code>{mqttConfig.topicPrefix}/camera/&lt;camera_id&gt;/&lt;label&gt;</code></span>
                        </div>
                    </div>
                </div>
            </section>
        {/if}
    </div>
</div>

<!-- Sticky save bar -->
<div class="fixed bottom-0 left-0 right-0 z-40 border-t border-border bg-card/95 backdrop-blur-md" style="box-shadow: 0 -10px 30px -10px rgba(0,0,0,0.4);">
    <div class="px-6 py-3.5 flex items-center justify-between gap-3">
        <div class="flex items-center gap-3">
            {#if saveStatus}
                <div class="flex items-center gap-2 px-3 h-9 rounded-lg border border-jade/30 bg-jade/10 text-jade" in:fly={{ y: 4, duration: 200 }}>
                    {#if saveStatus.includes('Saving')}
                        <span class="w-3 h-3 rounded-full border-2 border-current border-t-transparent animate-spin"></span>
                    {:else}
                        <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3"><polyline points="20 6 9 17 4 12"/></svg>
                    {/if}
                    <span class="text-xs font-semibold">{saveStatus}</span>
                </div>
            {/if}
        </div>
        <button class="btn btn-primary" onclick={saveSettings}>
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">
                <path d="M19 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11l5 5v11a2 2 0 0 1-2 2z"/><polyline points="17 21 17 13 7 13 7 21"/><polyline points="7 3 7 8 15 8"/>
            </svg>
            Save Config Changes
        </button>
    </div>
</div>
