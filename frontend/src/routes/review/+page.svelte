<script lang="ts">
    import { onMount, onDestroy, tick } from 'svelte';
    import { getApiToken } from '$lib/apiToken';
    import { fly, fade } from 'svelte/transition';
    import { page } from '$app/stores';

    interface CameraConfig {
        id: string;
        name: string;
        source: 'rtsp' | 'webcam';
        enabled: boolean;
        fps?: number;
    }

    interface Recording {
        id: string;
        camera_id: string;
        start_time: string;
        end_time: string;
        filepath: string;
        type: 'continuous' | 'event';
    }

    interface CameraEvent {
        id: string;
        camera_id: string;
        label: string;
        confidence: number;
        timestamp: string;
        snapshot_path?: string;
        severity: 'info' | 'warning' | 'critical';
    }

    interface RecordingSession {
        id: string;
        camera_id: string;
        start_time: string;
        end_time: string;
        segments: Recording[];
        durationSeconds: number;
    }

    // --- Svelte 5 Reactive States ---
    let cameras = $state<CameraConfig[]>([]);
    let selectedCameraId = $state<string>('');
    let activeTab = $state<'recordings' | 'events'>('recordings');
    
    let recordings = $state<Recording[]>([]);
    let events = $state<CameraEvent[]>([]);
    let selectedDate = $state<string>(new Date().toISOString().split('T')[0]); // YYYY-MM-DD
    
    let activeRecording = $state<Recording | null>(null);
    
    import Hls from 'hls.js';

    let videoElement = $state<HTMLVideoElement | null>(null);
    let isVideoPaused = $state(true);
    let currentSrc = $state<string>('');
    let hlsInstance = $state<Hls | null>(null);

    let isVideoPlaying = $derived(!isVideoPaused);
    
    let videoVolume = $state<number>(0.5);

    let eventFilterLabel = $state<string>('all');
    let toastMessage = $state<string>('');
    let toastType = $state<'info' | 'success' | 'warning' | 'error'>('info');
    let showToast = $state<boolean>(false);

    // --- Custom Video Clip Exporter States ---
    let exportStartTime = $state<string>('');
    let exportEndTime = $state<string>('');
    let activeExports = $state<any[]>([]);
    let isExporting = $state<boolean>(false);
    let pollingInterval = $state<any>(null);
    let apiToken = $state<string>('');

    // --- Derived States (Svelte 5 Runes) ---
    // Group all recordings of the selected camera by date list
    let availableDates = $derived.by(() => {
        const dates = new Set<string>();
        recordings.forEach(rec => {
            if (rec.start_time) {
                dates.add(rec.start_time.split('T')[0]);
            }
        });
        return Array.from(dates).sort((a, b) => b.localeCompare(a));
    });

    // Filter recordings by selected camera and date
    let filteredRecordings = $derived.by(() => {
        return recordings
            .filter(rec => rec.start_time.split('T')[0] === selectedDate)
            .sort((a, b) => a.start_time.localeCompare(b.start_time));
    });

    // Group filtered recordings into continuous sessions
    let recordingSessions = $derived.by(() => {
        const sessions: RecordingSession[] = [];
        let currentSession: RecordingSession | null = null;

        filteredRecordings.forEach(rec => {
            if (!currentSession) {
                currentSession = {
                    id: rec.id,
                    camera_id: rec.camera_id,
                    start_time: rec.start_time,
                    end_time: rec.end_time,
                    segments: [rec],
                    durationSeconds: 5
                };
            } else {
                const prevEnd = new Date(currentSession.end_time).getTime();
                const currStart = new Date(rec.start_time).getTime();
                const gapMs = currStart - prevEnd;

                // If gap is <= 15 seconds, group them into the same continuous session
                if (gapMs <= 15000) {
                    currentSession.segments.push(rec);
                    currentSession.end_time = rec.end_time;
                } else {
                    const startMs = new Date(currentSession.start_time).getTime();
                    const endMs = new Date(currentSession.end_time).getTime();
                    currentSession.durationSeconds = Math.max(5, Math.round((endMs - startMs) / 1000));
                    sessions.push(currentSession);

                    currentSession = {
                        id: rec.id,
                        camera_id: rec.camera_id,
                        start_time: rec.start_time,
                        end_time: rec.end_time,
                        segments: [rec],
                        durationSeconds: 5
                    };
                }
            }
        });

        if (currentSession) {
            const finalSession = currentSession as RecordingSession;
            const startMs = new Date(finalSession.start_time).getTime();
            const endMs = new Date(finalSession.end_time).getTime();
            finalSession.durationSeconds = Math.max(5, Math.round((endMs - startMs) / 1000));
            sessions.push(finalSession);
        }

        return sessions;
    });

    // Group recording sessions by Hour
    let sessionsByHour = $derived.by(() => {
        const groups: Record<string, RecordingSession[]> = {};
        recordingSessions.forEach(session => {
            try {
                const hour = new Date(session.start_time).getHours().toString().padStart(2, '0') + ':00';
                if (!groups[hour]) groups[hour] = [];
                groups[hour].push(session);
            } catch (e) {
                if (!groups['00:00']) groups['00:00'] = [];
                groups['00:00'].push(session);
            }
        });
        // Sort hours keys chronologically
        return Object.keys(groups)
            .sort()
            .map(hour => ({ hour, sessions: groups[hour] }));
    });

    // Filter events by selected camera and label
    let filteredEvents = $derived.by(() => {
        return events
            .filter(ev => eventFilterLabel === 'all' || ev.label.toLowerCase() === eventFilterLabel.toLowerCase())
            .sort((a, b) => b.timestamp.localeCompare(a.timestamp));
    });

    // Get list of unique event labels
    let uniqueLabels = $derived.by(() => {
        const labels = new Set<string>();
        events.forEach(ev => labels.add(ev.label.toLowerCase()));
        return Array.from(labels).sort();
    });

    // --- Methods & API Operations ---
    
    function triggerToast(msg: string, type: typeof toastType = 'info') {
        toastMessage = msg;
        toastType = type;
        showToast = true;
        setTimeout(() => {
            showToast = false;
        }, 4000);
    }

    async function loadInitialData() {
        try {
            const camRes = await fetch('/api/v1/cameras');
            cameras = await camRes.json();
            
            if (cameras.length > 0) {
                const defaultCam = cameras.find(c => c.enabled) || cameras[0];
                selectedCameraId = defaultCam.id;
            }
        } catch (e) {
            console.error('[Review Dashboard] Init failed:', e);
            triggerToast('Failed to load cameras list.', 'error');
        }
    }

    async function fetchRecordings(cameraId: string) {
        if (!cameraId) return;
        try {
            const res = await fetch(`/api/v1/recordings?camera_id=${cameraId}`);
            if (res.ok) {
                recordings = await res.json();
                
                // If there are recordings and current selected date has none, auto-select the latest date
                const hasCurrentDateRecs = recordings.some(r => r.start_time.split('T')[0] === selectedDate);
                if (!hasCurrentDateRecs && recordings.length > 0) {
                    selectedDate = recordings[recordings.length - 1].start_time.split('T')[0];
                }

                // If playing recording belongs to another camera, reset it
                if (activeRecording && activeRecording.camera_id !== cameraId) {
                    activeRecording = null;
                }
            }
        } catch (e) {
            console.error('[Review Dashboard] Recordings fetch failed:', e);
            triggerToast('Failed to fetch video segments.', 'error');
        }
    }

    async function fetchEvents(cameraId: string) {
        if (!cameraId) return;
        try {
            const res = await fetch(`/api/v1/events?camera_id=${cameraId}&limit=200`);
            if (res.ok) {
                events = await res.json();
            }
        } catch (e) {
            console.error('[Review Dashboard] Events fetch failed:', e);
            triggerToast('Failed to fetch camera events.', 'error');
        }
    }

    function toDatetimeLocalString(isoString: string): string {
        if (!isoString) return '';
        const d = new Date(isoString);
        if (isNaN(d.getTime())) return '';
        // Format as YYYY-MM-DDTHH:mm:ss
        const pad = (n: number) => n.toString().padStart(2, '0');
        return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
    }

    function playHls(src: string, seekToSec?: number) {
        if (hlsInstance) {
            hlsInstance.destroy();
            hlsInstance = null;
        }

        if (!videoElement) return;

        // Reset native src
        videoElement.src = '';

        if (videoElement.canPlayType('application/vnd.apple.mpegurl')) {
            // Safari Native HLS
            videoElement.src = src;
            
            if (seekToSec && seekToSec > 0) {
                const onLoadedMetadata = () => {
                    if (videoElement) {
                        videoElement.currentTime = seekToSec;
                    }
                    videoElement?.removeEventListener('loadedmetadata', onLoadedMetadata);
                };
                videoElement.addEventListener('loadedmetadata', onLoadedMetadata);
            }
            
            videoElement.play().catch(err => {
                console.log('Video autoplay blocked or interrupted:', err);
            });
        } else if (Hls.isSupported()) {
            // Chrome / Firefox / Edge Hls.js
            const hls = new Hls({
                maxMaxBufferLength: 30,
                enableWorker: true,
                lowLatencyMode: true,
                backBufferLength: 60
            });
            hlsInstance = hls;
            hls.loadSource(src);
            hls.attachMedia(videoElement);
            
            hls.on(Hls.Events.MANIFEST_PARSED, () => {
                if (seekToSec && seekToSec > 0 && videoElement) {
                    videoElement.currentTime = seekToSec;
                }
                videoElement?.play().catch(err => {
                    console.log('Hls.js autoplay blocked:', err);
                });
            });

            // Handle errors
            hls.on(Hls.Events.ERROR, function (event, data) {
                if (data.fatal) {
                    switch (data.type) {
                        case Hls.ErrorTypes.NETWORK_ERROR:
                            console.error('Fatal network error, trying to recover...', data);
                            hls.startLoad();
                            break;
                        case Hls.ErrorTypes.MEDIA_ERROR:
                            console.error('Fatal media error, trying to recover...', data);
                            hls.recoverMediaError();
                            break;
                        default:
                            console.error('Fatal HLS error, destroying...', data);
                            hls.destroy();
                            hlsInstance = null;
                            break;
                    }
                }
            });
        } else {
            // Fallback to progressive
            videoElement.src = src;
            videoElement.play().catch(err => {
                console.log('Video autoplay blocked:', err);
            });
        }
    }

    function selectRecording(rec: Recording, session?: RecordingSession, seekToSeconds?: number) {
        activeRecording = rec;
        
        const resolvedSession = session || recordingSessions.find(s => s.segments.some(seg => seg.id === rec.id));
        
        let startIso = rec.start_time;
        let endIso = rec.end_time;
        let offsetSec = 0;

        if (resolvedSession) {
            startIso = resolvedSession.start_time;
            endIso = resolvedSession.end_time;
            
            if (seekToSeconds === undefined) {
                const sessionStart = new Date(resolvedSession.start_time).getTime();
                const recStart = new Date(rec.start_time).getTime();
                offsetSec = Math.max(0, (recStart - sessionStart) / 1000);
            } else {
                offsetSec = seekToSeconds;
            }

            exportStartTime = toDatetimeLocalString(resolvedSession.start_time);
            exportEndTime = toDatetimeLocalString(resolvedSession.end_time);
        } else {
            exportStartTime = toDatetimeLocalString(rec.start_time);
            exportEndTime = toDatetimeLocalString(rec.end_time);
            offsetSec = seekToSeconds || 0;
        }

        const src = `/api/v1/recordings/vod/index.m3u8?camera_id=${rec.camera_id}&start_time=${startIso}&end_time=${endIso}`;
        currentSrc = src;
        
        // Wait for Svelte DOM tick to ensure the videoElement is rendered and bound before Hls initializes
        tick().then(() => {
            playHls(src, offsetSec);
        });
    }

    function handleTimeUpdate() {
        if (!videoElement || !activeRecording) return;
        const resolvedSession = recordingSessions.find(s => s.segments.some(seg => seg.id === activeRecording?.id));
        if (!resolvedSession) return;

        const currentTime = videoElement.currentTime;
        const sessionStart = new Date(resolvedSession.start_time).getTime();

        // Find the segment matching the current playback time
        for (const seg of resolvedSession.segments) {
            const start = new Date(seg.start_time).getTime();
            const end = new Date(seg.end_time).getTime();
            const duration = (end - start) / 1000;

            const segOffset = (start - sessionStart) / 1000;
            if (currentTime >= segOffset && currentTime < segOffset + duration) {
                if (activeRecording.id !== seg.id) {
                    activeRecording = seg;
                }
                break;
            }
        }
    }

    async function deleteRecordingSegment(id: string, e: MouseEvent) {
        e.stopPropagation();
        if (!confirm('Are you sure you want to delete this recording segment permanently from disk?')) return;
        
        try {
            const res = await fetch(`/api/v1/recordings/${id}`, { method: 'DELETE' });
            const data = await res.json();
            if (data.success) {
                triggerToast('Video segment successfully purged.', 'success');
                if (activeRecording?.id === id) {
                    activeRecording = null;
                }
                // Refresh list
                await fetchRecordings(selectedCameraId);
            } else {
                triggerToast(data.error || 'Failed to delete segment.', 'error');
            }
        } catch (err: any) {
            triggerToast(`Error: ${err.message}`, 'error');
        }
    }

    async function deleteRecordingSession(session: RecordingSession, e: MouseEvent) {
        e.stopPropagation();
        if (!confirm(`Are you sure you want to delete this continuous recording session (${formatDuration(session.durationSeconds)}) permanently from disk?`)) return;
        
        try {
            let deletedCount = 0;
            let firstError: string | null = null;
            
            // Delete all segments belonging to the session
            for (const rec of session.segments) {
                const res = await fetch(`/api/v1/recordings/${rec.id}`, { method: 'DELETE' });
                const data = await res.json();
                if (data.success) {
                    deletedCount++;
                    if (activeRecording?.id === rec.id) {
                        activeRecording = null;
                    }
                } else {
                    firstError = data.error || 'Failed to delete some segments';
                }
            }
            
            if (deletedCount > 0) {
                triggerToast(`Continuous Session: Purged ${deletedCount} recording segment(s).`, 'success');
            }
            if (firstError) {
                triggerToast(`Warning: ${firstError}`, 'warning');
            }
            
            // Refresh list
            await fetchRecordings(selectedCameraId);
        } catch (err: any) {
            triggerToast(`Error: ${err.message}`, 'error');
        }
    }

    async function deleteEventItem(id: string, e: MouseEvent) {
        e.stopPropagation();
        
        try {
            const res = await fetch(`/api/v1/events?id=${id}`, { method: 'DELETE' });
            const data = await res.json();
            if (data.success) {
                triggerToast('Event successfully deleted.', 'success');
                // Refresh list
                await fetchEvents(selectedCameraId);
            } else {
                triggerToast(data.error || 'Failed to delete event.', 'error');
            }
        } catch (err: any) {
            triggerToast(`Error: ${err.message}`, 'error');
        }
    }

    function crossLinkEventToRecording(ev: CameraEvent) {
        const eventTime = new Date(ev.timestamp).getTime();
        
        // Find overlapping recording segment
        const overlapSegment = recordings.find(rec => {
            const start = new Date(rec.start_time).getTime();
            const end = new Date(rec.end_time).getTime();
            // Let's add 5 seconds buffer around segment in case of minor timestamp drift
            return eventTime >= (start - 5000) && eventTime <= (end + 5000);
        });

        if (overlapSegment) {
            selectedDate = overlapSegment.start_time.split('T')[0];
            activeTab = 'recordings';
            
            // Find session starting time to compute offset
            const resolvedSession = recordingSessions.find(s => s.segments.some(seg => seg.id === overlapSegment.id));
            let seekOffset = 0;
            if (resolvedSession) {
                const sessionStart = new Date(resolvedSession.start_time).getTime();
                seekOffset = Math.max(0, (eventTime - sessionStart) / 1000);
            }
            
            selectRecording(overlapSegment, resolvedSession, seekOffset);
            triggerToast(`Linked to continuous recording at ${formatTimeOnly(overlapSegment.start_time)}!`, 'success');
            
            // Scroll to video player
            setTimeout(() => {
                const player = document.getElementById('recordings-player-container');
                player?.scrollIntoView({ behavior: 'smooth', block: 'center' });
            }, 100);
        } else {
            triggerToast('No overlapping continuous recording segment found for this event timestamp.', 'warning');
        }
    }

    // --- Utility Formatters ---
    
    function formatTimeOnly(isoString: string): string {
        try {
            const date = new Date(isoString);
            return date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false });
        } catch {
            return '';
        }
    }

    function formatDuration(seconds: number): string {
        if (seconds < 60) return `${seconds}s`;
        const minutes = Math.floor(seconds / 60);
        const remainingSeconds = seconds % 60;
        if (remainingSeconds === 0) return `${minutes}m`;
        return `${minutes}m ${remainingSeconds}s`;
    }

    function formatDateFriendly(isoString: string): string {
        try {
            const date = new Date(isoString);
            return date.toLocaleDateString([], { weekday: 'short', month: 'short', day: 'numeric' });
        } catch {
            return isoString;
        }
    }

    function getRelativeTime(timestampStr: string): string {
        try {
            const date = new Date(timestampStr);
            const now = new Date();
            const diffMs = now.getTime() - date.getTime();
            const diffMins = Math.floor(diffMs / 60000);
            if (diffMins < 1) return 'Just now';
            if (diffMins < 60) return `${diffMins}m ago`;
            const diffHours = Math.floor(diffMins / 60);
            if (diffHours < 24) return `${diffHours}h ago`;
            return date.toLocaleDateString();
        } catch {
            return '';
        }
    }

    function getFilenameFromPath(filepath: string): string {
        if (!filepath) return 'segment.mp4';
        return filepath.split(/[\\/]/).pop() || 'segment.mp4';
    }

    // --- Life cycle & Reactive Effects ---
    
    async function fetchExports() {
        try {
            const res = await fetch('/api/v1/exports');
            if (res.ok) {
                activeExports = await res.json();
            }
        } catch (e) {
            console.error('[Review Dashboard] Exports fetch failed:', e);
        }
    }

    async function triggerCustomExport() {
        if (!selectedCameraId) {
            triggerToast('Please select a camera.', 'error');
            return;
        }
        if (!exportStartTime || !exportEndTime) {
            triggerToast('Please select start and end times.', 'error');
            return;
        }

        const start = new Date(exportStartTime);
        const end = new Date(exportEndTime);

        if (isNaN(start.getTime()) || isNaN(end.getTime())) {
            triggerToast('Invalid start or end time format.', 'error');
            return;
        }

        if (start >= end) {
            triggerToast('Start time must be before end time.', 'error');
            return;
        }

        isExporting = true;
        try {
            const res = await fetch('/api/v1/exports', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify({
                    camera_id: selectedCameraId,
                    start_time: start.toISOString(),
                    end_time: end.toISOString()
                })
            });

            const data = await res.json();
            if (res.ok && data.success) {
                triggerToast('Video slice & export job initiated!', 'success');
                await fetchExports();
            } else {
                triggerToast(data.error || 'Failed to trigger video export.', 'error');
            }
        } catch (e: any) {
            console.error('[Export Form] Failed to trigger export:', e);
            triggerToast(`Error: ${e.message}`, 'error');
        } finally {
            isExporting = false;
        }
    }

    onMount(async () => {
        const token = await getApiToken();
        if (token) apiToken = token;
        await loadInitialData();
        await fetchExports();

        // Handle search parameters for seamless event cross-linking
        const params = $page.url.searchParams;
        const paramCameraId = params.get('camera_id');
        const paramTab = params.get('tab');
        const paramTimestamp = params.get('timestamp');

        if (paramCameraId) {
            selectedCameraId = paramCameraId;
        }

        if (paramTab === 'events') {
            activeTab = 'events';
        } else if (paramTab === 'recordings') {
            activeTab = 'recordings';
        }

        if (paramTimestamp) {
            // Give reactive effects a brief tick to load initial states
            setTimeout(async () => {
                if (selectedCameraId) {
                    await fetchRecordings(selectedCameraId);

                    const eventTime = new Date(paramTimestamp).getTime();
                    const overlapSegment = recordings.find(rec => {
                        const start = new Date(rec.start_time).getTime();
                        const end = new Date(rec.end_time).getTime();
                        // 5 seconds tolerance buffer for network jitter/drift
                        return eventTime >= (start - 5000) && eventTime <= (end + 5000);
                    });

                    if (overlapSegment) {
                        selectedDate = overlapSegment.start_time.split('T')[0];
                        activeTab = 'recordings';
                        
                        const resolvedSession = recordingSessions.find(s => s.segments.some(seg => seg.id === overlapSegment.id));
                        let seekOffset = 0;
                        if (resolvedSession) {
                            const sessionStart = new Date(resolvedSession.start_time).getTime();
                            seekOffset = Math.max(0, (eventTime - sessionStart) / 1000);
                        }
                        
                        selectRecording(overlapSegment, resolvedSession, seekOffset);
                        triggerToast(`Successfully loaded overlapping recording at ${formatTimeOnly(overlapSegment.start_time)}!`, 'success');

                        setTimeout(() => {
                            const player = document.getElementById('recordings-player-container');
                            player?.scrollIntoView({ behavior: 'smooth', block: 'center' });
                        }, 250);
                    } else {
                        triggerToast('No overlapping continuous recording segment found for this event timestamp.', 'warning');
                    }
                }
            }, 600);
        }
    });

    // Reactive effect: fetch recordings & events whenever selected camera changes
    $effect(() => {
        if (selectedCameraId) {
            if (hlsInstance) {
                hlsInstance.destroy();
                hlsInstance = null;
            }
            activeRecording = null;
            fetchRecordings(selectedCameraId);
            fetchEvents(selectedCameraId);
        }
    });

    // Set video element volume when reactive volume state changes
    $effect(() => {
        if (videoElement) {
            videoElement.volume = videoVolume;
        }
    });

    // Polling setup for active exports
    $effect(() => {
        const hasActiveJobs = activeExports.some(job => job.status === 'pending' || job.status === 'processing');
        if (hasActiveJobs) {
            if (!pollingInterval) {
                pollingInterval = setInterval(fetchExports, 2000);
            }
        } else {
            if (pollingInterval) {
                clearInterval(pollingInterval);
                pollingInterval = null;
            }
        }
    });

    onDestroy(() => {
        if (pollingInterval) {
            clearInterval(pollingInterval);
        }
        if (hlsInstance) {
            hlsInstance.destroy();
        }
    });
</script>

<!-- Header Viewport -->
<div class="flex flex-col gap-6 w-full text-foreground pb-12">
    
    <!-- Toast Message Alert System -->
    {#if showToast}
        <div class="fixed bottom-6 right-6 z-[100] px-4 py-3 rounded-xl border shadow-2xl flex items-center gap-3 animate-in slide-in-from-bottom-5 duration-300"
            class:bg-indigo-950={toastType === 'info'}
            class:border-indigo-500={toastType === 'info'}
            class:text-indigo-200={toastType === 'info'}
            class:bg-emerald-950={toastType === 'success'}
            class:border-emerald-500={toastType === 'success'}
            class:text-emerald-200={toastType === 'success'}
            class:bg-amber-950={toastType === 'warning'}
            class:border-amber-500={toastType === 'warning'}
            class:text-amber-200={toastType === 'warning'}
            class:bg-rose-950={toastType === 'error'}
            class:border-rose-500={toastType === 'error'}
            class:text-rose-200={toastType === 'error'}
            transition:fly={{ y: 20, duration: 200 }}>
            <span class="w-2.5 h-2.5 rounded-full animate-pulse"
                class:bg-indigo-400={toastType === 'info'}
                class:bg-emerald-400={toastType === 'success'}
                class:bg-amber-400={toastType === 'warning'}
                class:bg-rose-400={toastType === 'error'}></span>
            <span class="text-xs font-semibold">{toastMessage}</span>
        </div>
    {/if}

    <!-- Main Title & Metrics Panel -->
    <div class="flex flex-col md:flex-row md:items-center justify-between gap-4">
        <div>
            <h1 class="text-3xl font-extrabold tracking-tight text-white flex items-center gap-2">
                <svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" class="text-primary">
                    <rect width="18" height="18" x="3" y="3" rx="2" ry="2"/>
                    <path d="M12 8v8M8 12h8"/>
                </svg>
                Recordings & Events Review
            </h1>
            <p class="text-sm text-muted-foreground mt-1">Explore, seek, playback, and export localized rolling recording segments and intelligent AI event catches.</p>
        </div>

        <div class="flex items-center gap-2">
            <span class="badge flex items-center gap-1 bg-primary/10 border-primary/20 text-foreground py-1 px-3.5">
                <span class="w-1.5 h-1.5 rounded-full bg-indigo-400"></span>
                {recordings.length} Segments
            </span>
            <span class="badge flex items-center gap-1 bg-primary/10 border-primary/20 text-foreground py-1 px-3.5">
                <span class="w-1.5 h-1.5 rounded-full bg-cyan-400"></span>
                {events.length} AI Events
            </span>
        </div>
    </div>

    <!-- Interactive Navigation Control Bar -->
    <div class="glass-panel p-4 flex flex-col md:flex-row items-center justify-between gap-4 bg-card/65 backdrop-blur-md">
        
        <div class="flex flex-wrap items-center gap-3 w-full md:w-auto">
            <!-- Camera Selection -->
            <div class="flex flex-col gap-1">
                <span class="text-[10px] font-bold text-muted-foreground tracking-wider uppercase">Select Camera</span>
                <select bind:value={selectedCameraId} class="select min-w-[180px] bg-background">
                    {#each cameras as cam}
                        <option value={cam.id}>{cam.name} ({cam.source})</option>
                    {/each}
                    {#if cameras.length === 0}
                        <option value="">No registered cameras</option>
                    {/if}
                </select>
            </div>

            <!-- Date Select (Only for continuous recordings) -->
            {#if activeTab === 'recordings'}
                <div class="flex flex-col gap-1" transition:fade>
                    <span class="text-[10px] font-bold text-muted-foreground tracking-wider uppercase">Select Date</span>
                    <select bind:value={selectedDate} class="select min-w-[160px] bg-background">
                        {#each availableDates as dt}
                            <option value={dt}>{formatDateFriendly(dt + 'T00:00:00')}</option>
                        {/each}
                        {#if availableDates.length === 0}
                            <option value={selectedDate}>{formatDateFriendly(selectedDate + 'T00:00:00')}</option>
                        {/if}
                    </select>
                </div>
            {/if}

            <!-- AI Event Label filter -->
            {#if activeTab === 'events'}
                <div class="flex flex-col gap-1" transition:fade>
                    <span class="text-[10px] font-bold text-muted-foreground tracking-wider uppercase">Filter by Object</span>
                    <select bind:value={eventFilterLabel} class="select min-w-[140px] bg-background">
                        <option value="all">All Objects ({uniqueLabels.length})</option>
                        {#each uniqueLabels as label}
                            <option value={label}>{label.charAt(0).toUpperCase() + label.slice(1)}</option>
                        {/each}
                    </select>
                </div>
            {/if}
        </div>

        <!-- Sleek Custom Tabs -->
        <div class="bg-muted p-1 rounded-xl flex items-center self-end md:self-center">
            <button 
                onclick={() => activeTab = 'recordings'} 
                class="tab-btn flex items-center gap-1.5 px-4 py-2" 
                class:tab-btn-active={activeTab === 'recordings'}>
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                    <polygon points="23 7 16 12 23 17 23 7"/>
                    <rect width="14" height="12" x="1" y="6" rx="2" ry="2"/>
                </svg>
                Continuous Recordings
            </button>
            <button 
                onclick={() => activeTab = 'events'} 
                class="tab-btn flex items-center gap-1.5 px-4 py-2" 
                class:tab-btn-active={activeTab === 'events'}>
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                    <path d="M12 22c5.523 0 10-4.477 10-10S17.523 2 12 2 2 6.477 2 12s4.477 10 10 10z"/>
                    <path d="M12 8v4"/>
                    <path d="M12 16h.01"/>
                </svg>
                AI Event Captures
            </button>
        </div>
    </div>

    <!-- MAIN DASHBOARD CONTENT AREA -->
    {#if activeTab === 'recordings'}
        <div class="grid grid-cols-1 lg:grid-cols-5 gap-6" transition:fade>
            
            <!-- Left Side Video Player & Controls (3 cols span) -->
            <div class="lg:col-span-3 flex flex-col gap-4">
                
                <!-- Black Sleek Video Viewport Wrapper -->
                <div id="recordings-player-container" class="relative w-full aspect-video bg-black border border-border rounded-2xl overflow-hidden shadow-2xl flex items-center justify-center group">
                    {#if activeRecording}
                        <!-- svelte-ignore a11y_media_has_caption -->
                        <video 
                            bind:this={videoElement} 
                            controls
                            autoplay
                            class="w-full h-full object-contain"
                            bind:paused={isVideoPaused}
                            ontimeupdate={handleTimeUpdate}
                        ></video>

                        <!-- Subtle Custom Meta Floating Header Overlay -->
                        <div class="absolute top-4 left-4 right-4 flex items-center justify-between opacity-0 group-hover:opacity-100 transition-opacity duration-300 pointer-events-none">
                            <div class="bg-black/75 backdrop-blur-md border border-border px-3 py-1.5 rounded-lg flex items-center gap-2 text-[11px] font-mono text-zinc-300 pointer-events-auto">
                                <span class="w-1.5 h-1.5 bg-emerald-500 rounded-full"></span>
                                <span>PLAYING: {formatTimeOnly(activeRecording.start_time)} - {formatTimeOnly(activeRecording.end_time)}</span>
                            </div>

                            <div class="bg-black/75 backdrop-blur-md border border-border px-3 py-1.5 rounded-lg text-[11px] font-mono text-zinc-300 pointer-events-auto">
                                <span>Type: {activeRecording.type.toUpperCase()}</span>
                            </div>
                        </div>
                    {:else}
                        <div class="flex flex-col items-center justify-center p-8 text-center max-w-sm">
                            <div class="w-16 h-16 rounded-full bg-zinc-900 border border-border flex items-center justify-center text-muted-foreground shadow-inner mb-4">
                                <svg width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">
                                    <polygon points="5 3 19 12 5 21 5 3"/>
                                </svg>
                            </div>
                            <h3 class="text-sm font-bold text-white">No Segment Selected</h3>
                            <p class="text-xs text-muted-foreground mt-2 leading-relaxed">Select a recording block from the right timeline to stream. Continuous autoplay is enabled by default.</p>
                            {#if filteredRecordings.length > 0}
                                <button onclick={() => selectRecording(filteredRecordings[0])} class="glass-btn primary mt-4 w-full">
                                    Play Latest Segment
                                </button>
                            {/if}
                        </div>
                    {/if}
                </div>

                <!-- Video Metadata Details Card -->
                {#if activeRecording}
                    <div class="panel flex flex-col gap-4 bg-card/45 backdrop-blur-sm" transition:fly={{ y: 10, duration: 150 }}>
                        <div class="flex items-center justify-between border-b border-border pb-3">
                            <div class="flex flex-col">
                                <span class="text-xs font-bold text-white flex items-center gap-1.5">
                                    <span class="w-2.5 h-2.5 rounded bg-indigo-500 inline-block"></span>
                                    Segment properties
                                </span>
                                <span class="text-[10px] text-muted-foreground mt-0.5 font-mono">{activeRecording.id}</span>
                            </div>
                            <span class="badge bg-indigo-500/10 border-indigo-500/20 text-indigo-300 py-0.5 px-2 font-mono">
                                {activeRecording.type === 'continuous' ? 'Continuous Roll' : 'Trigger Clip'}
                            </span>
                        </div>

                        <div class="grid grid-cols-2 md:grid-cols-4 gap-4 text-xs font-mono">
                            <div class="flex flex-col gap-0.5">
                                <span class="text-[9px] text-muted-foreground font-bold uppercase tracking-wider">Start Time</span>
                                <span class="text-white">{formatTimeOnly(activeRecording.start_time)}</span>
                            </div>
                            <div class="flex flex-col gap-0.5">
                                <span class="text-[9px] text-muted-foreground font-bold uppercase tracking-wider">End Time</span>
                                <span class="text-white">{formatTimeOnly(activeRecording.end_time)}</span>
                            </div>
                            <div class="flex flex-col gap-0.5">
                                <span class="text-[9px] text-muted-foreground font-bold uppercase tracking-wider">File Name</span>
                                <span class="text-white truncate" title={getFilenameFromPath(activeRecording.filepath)}>
                                    {getFilenameFromPath(activeRecording.filepath)}
                                </span>
                            </div>
                            <div class="flex flex-col gap-0.5">
                                <span class="text-[9px] text-muted-foreground font-bold uppercase tracking-wider">Action</span>
                                <a href="/api/v1/recordings/{activeRecording.id}" download={getFilenameFromPath(activeRecording.filepath)} class="text-indigo-400 hover:text-indigo-300 font-bold flex items-center gap-1">
                                    <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                                        <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4M7 10l5 5 5-5M12 15V3"/>
                                    </svg>
                                    Export MP4
                                </a>
                            </div>
                        </div>

                        <!-- Actions footer -->
                        <div class="flex items-center justify-between border-t border-border pt-3 mt-1">
                            <button onclick={(e) => deleteRecordingSegment(activeRecording!.id, e)} class="glass-btn danger py-1 px-3 text-[11px]">
                                <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">
                                    <path d="M3 6h18M19 6v14c0 1-1 2-2 2H7c-1 0-2-1-2-2V6M8 6V4c0-1 1-2 2-2h4c1 0 2 1 2 2v2M10 11v6M14 11v6"/>
                                </svg>
                                Delete Segment
                            </button>
                            
                            <span class="text-[10px] text-muted-foreground italic">Full HTTP 206 partial range streaming active</span>
                        </div>
                    </div>
                {/if}

                <!-- 🎬 Slice & Export Custom Video Clip Control Panel -->
                <div class="panel flex flex-col gap-5 bg-card/45 backdrop-blur-sm relative overflow-hidden" transition:fade>
                    <!-- Sleek Glow Header -->
                    <div class="flex items-center justify-between border-b border-border pb-3">
                        <div class="flex flex-col">
                            <span class="text-xs font-bold text-white flex items-center gap-1.5">
                                <span class="w-2.5 h-2.5 rounded bg-emerald-500 inline-block animate-pulse"></span>
                                🎬 Slice & Export Custom Video Clip
                            </span>
                            <span class="text-[10px] text-muted-foreground mt-0.5 font-mono">High-speed zero-reencoding lossless FFmpeg stream copy</span>
                        </div>
                    </div>

                    <!-- Input Controls Grid -->
                    <div class="grid grid-cols-1 sm:grid-cols-2 gap-4">
                        <div class="flex flex-col gap-1.5">
                            <label for="export-start" class="text-[10px] font-bold text-muted-foreground uppercase tracking-wider font-mono">Start Time</label>
                            <input 
                                id="export-start"
                                type="datetime-local" 
                                bind:value={exportStartTime}
                                class="input bg-background font-mono text-xs"
                            />
                        </div>
                        <div class="flex flex-col gap-1.5">
                            <label for="export-end" class="text-[10px] font-bold text-muted-foreground uppercase tracking-wider font-mono">End Time</label>
                            <input 
                                id="export-end"
                                type="datetime-local" 
                                bind:value={exportEndTime}
                                class="input bg-background font-mono text-xs"
                            />
                        </div>
                    </div>

                    <!-- Trigger Button -->
                    <div class="flex flex-col sm:flex-row justify-between items-start sm:items-center gap-3">
                        <span class="text-[10px] text-muted-foreground italic font-mono">Overlapping segments are merged automatically</span>
                        <button 
                            onclick={triggerCustomExport}
                            disabled={isExporting || !exportStartTime || !exportEndTime}
                            class="glass-btn primary py-2 px-4 text-xs font-bold disabled:opacity-50 flex items-center gap-1.5 w-full sm:w-auto"
                        >
                            {#if isExporting}
                                <svg class="animate-spin -ml-1 mr-2 h-4 w-4 text-white" fill="none" viewBox="0 0 24 24">
                                    <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle>
                                    <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
                                </svg>
                                Preparing FFmpeg...
                            {:else}
                                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                                    <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/>
                                    <polyline points="7 10 12 15 17 10"/>
                                    <line x1="12" x2="12" y1="15" y2="3"/>
                                </svg>
                                Start Export
                            {/if}
                        </button>
                    </div>

                    <!-- Render active/pending exports list -->
                    {#if activeExports.length > 0}
                        <div class="border-t border-border/80 pt-4 flex flex-col gap-3">
                            <span class="text-[10px] font-bold text-muted-foreground uppercase tracking-wider font-mono">Export Queue & History</span>
                            <div class="flex flex-col gap-2 max-h-[220px] overflow-y-auto pr-1">
                                {#each activeExports as job (job.id)}
                                    <div class="flex flex-col gap-2 p-2.5 rounded-lg border border-border/50 bg-muted/5 hover:bg-muted/10 transition-all text-xs font-mono">
                                        <div class="flex justify-between items-center gap-2">
                                            <span class="font-bold truncate text-[11px] max-w-[150px]">{job.id}</span>
                                            
                                            <!-- Status Badge -->
                                            <span class="badge uppercase text-[8px] font-bold py-0.5 px-1.5
                                                {job.status === 'completed' ? 'bg-emerald-500/10 border-emerald-500/35 text-emerald-300' : ''}
                                                {job.status === 'failed' ? 'bg-rose-500/10 border-rose-500/35 text-rose-300' : ''}
                                                {job.status === 'processing' ? 'bg-amber-500/10 border-amber-500/35 text-amber-300 animate-pulse' : ''}
                                                {job.status === 'pending' ? 'bg-zinc-800 border-zinc-700 text-zinc-400' : ''}"
                                            >
                                                {job.status}
                                            </span>
                                        </div>

                                        <!-- Progress Bar -->
                                        {#if job.status === 'processing' || job.status === 'pending'}
                                            <div class="w-full h-1 bg-zinc-800 rounded-full overflow-hidden mt-1">
                                                <div class="h-full bg-gradient-to-r from-amber-500 to-indigo-500 rounded-full transition-all duration-300" style="width: {job.progress}%"></div>
                                            </div>
                                        {/if}

                                        <!-- Details -->
                                        <div class="flex flex-col gap-0.5 text-[9px] text-muted-foreground leading-normal font-mono">
                                            <span>Start: {new Date(job.startTime).toLocaleString()}</span>
                                            <span>End: {new Date(job.endTime).toLocaleString()}</span>
                                            {#if job.message}
                                                <span class="text-rose-400 mt-1 italic">{job.message}</span>
                                            {/if}
                                        </div>

                                        <!-- Download/Success -->
                                        {#if job.status === 'completed'}
                                            <div class="flex justify-end mt-1">
                                                <a 
                                                    href="/api/v1/exports/{job.id}/download" 
                                                    download 
                                                    class="glass-btn primary py-1 px-2.5 text-[10px] font-bold inline-flex items-center gap-1"
                                                >
                                                    <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                                                        <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4M7 10l5 5 5-5M12 15V3"/>
                                                    </svg>
                                                    Download Clip
                                                </a>
                                            </div>
                                        {/if}
                                    </div>
                                {/each}
                            </div>
                        </div>
                    {/if}
                </div>
            </div>

            <!-- Right Side Scrollable Timeline Segment List (2 cols span) -->
            <div class="lg:col-span-2 flex flex-col gap-3">
                <div class="panel bg-card/65 backdrop-blur-md flex flex-col gap-4 h-[550px] overflow-hidden">
                    
                    <div class="flex justify-between items-center border-b border-border pb-3">
                        <div class="flex items-center gap-2">
                            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" class="text-primary">
                                <circle cx="12" cy="12" r="10"/>
                                <polyline points="12 6 12 12 16 14"/>
                            </svg>
                            <h3 class="text-sm font-bold text-white">Recordings Timeline</h3>
                        </div>
                        <span class="badge text-[10px] bg-indigo-500/10 border-indigo-500/20 text-indigo-300 font-mono">
                            {recordingSessions.length} session{recordingSessions.length > 1 ? 's' : ''} ({filteredRecordings.length} blocks)
                        </span>
                    </div>

                    <!-- Scrollable list of hours -->
                    <div class="flex-1 overflow-y-auto pr-1 flex flex-col gap-4">
                        {#each sessionsByHour as group}
                            <div class="flex flex-col gap-2">
                                <!-- Hourly header -->
                                <div class="text-[10px] font-bold text-muted-foreground bg-muted/30 border border-border/40 rounded px-2.5 py-1 uppercase tracking-wider self-start font-mono">
                                    {group.hour}
                                </div>

                                <!-- Sessions list -->
                                <div class="flex flex-col gap-1.5 pl-1.5 border-l border-border/40 ml-4">
                                    {#each group.sessions as session}
                                        <div 
                                            role="button"
                                            tabindex="0"
                                            onclick={() => selectRecording(session.segments[0], session)}
                                            onkeydown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); selectRecording(session.segments[0], session); } }}
                                            class="w-full text-left px-3 py-2.5 rounded-xl border transition-all duration-200 cursor-pointer flex items-center justify-between gap-3 group/item
                                            {activeRecording && session.segments.some(s => s.id === activeRecording?.id)
                                                ? 'bg-indigo-500/15 border-indigo-500/40 text-indigo-200 shadow-[0_0_15px_rgba(99,102,241,0.08)]' 
                                                : 'bg-muted/10 border-border/50 hover:bg-muted/20 hover:border-zinc-700'}"
                                        >
                                            <div class="flex items-center gap-2 overflow-hidden">
                                                <!-- Play Icon status -->
                                                <div class="shrink-0 w-6 h-6 rounded-full flex items-center justify-center transition-colors
                                                    {activeRecording && session.segments.some(s => s.id === activeRecording?.id)
                                                        ? 'bg-indigo-500 text-white animate-pulse' 
                                                        : 'bg-zinc-800 text-zinc-400 group-hover/item:bg-zinc-700 group-hover/item:text-white'}"
                                                >
                                                    <svg width="10" height="10" viewBox="0 0 24 24" fill="currentColor">
                                                        <polygon points="5 3 19 12 5 21 5 3"/>
                                                    </svg>
                                                </div>

                                                <div class="flex flex-col overflow-hidden font-mono text-xs">
                                                    <strong class="text-zinc-200 group-hover/item:text-white transition-colors">
                                                        {formatTimeOnly(session.start_time)} - {formatTimeOnly(session.end_time)}
                                                    </strong>
                                                    <span class="text-[10px] text-muted-foreground mt-0.5 truncate">
                                                        Continuous Footage ({session.segments.length} block{session.segments.length > 1 ? 's' : ''})
                                                    </span>
                                                </div>
                                            </div>

                                            <div class="flex items-center gap-2 font-mono text-[10px] text-muted-foreground shrink-0">
                                                <span>{formatDuration(session.durationSeconds)}</span>
                                                
                                                <!-- Delete button -->
                                                <button 
                                                    onclick={(e) => deleteRecordingSession(session, e)}
                                                    class="opacity-0 group-hover/item:opacity-100 hover:text-rose-400 p-1 rounded hover:bg-rose-500/10 transition-all cursor-pointer"
                                                    title="Delete session permanently"
                                                >
                                                    <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">
                                                        <path d="M3 6h18M19 6v14c0 1-1 2-2 2H7c-1 0-2-1-2-2V6M8 6V4c0-1 1-2 2-2h4c1 0 2 1 2 2v2M10 11v6M14 11v6"/>
                                                    </svg>
                                                </button>
                                            </div>
                                        </div>
                                    {/each}
                                </div>
                            </div>
                        {/each}

                        {#if filteredRecordings.length === 0}
                            <div class="flex flex-col items-center justify-center py-12 text-center text-muted-foreground h-full">
                                <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" class="mb-2 text-zinc-700">
                                    <rect width="18" height="18" x="3" y="3" rx="2" ry="2"/>
                                    <path d="M9 17v-5M15 17V9"/>
                                </svg>
                                <span class="text-xs font-semibold">No recordings found</span>
                                <span class="text-[10px] mt-1 text-zinc-600">No continuous segments on this date.</span>
                            </div>
                        {/if}
                    </div>

                </div>
            </div>

        </div>
    {/if}

    <!-- EVENTS GRID TAB -->
    {#if activeTab === 'events'}
        <div class="flex flex-col gap-4" transition:fade>
            
            <div class="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-6">
                {#each filteredEvents as ev (ev.id)}
                    <div class="glass-panel overflow-hidden bg-card/65 backdrop-blur-md flex flex-col group/card hover:border-zinc-700 hover:shadow-xl transition-all duration-300"
                        transition:fly={{ y: 20, duration: 250 }}>
                        
                        <!-- Event Snapshot Image Container -->
                        <div class="relative aspect-video bg-black/90 overflow-hidden border-b border-border">
                            {#if ev.snapshot_path && apiToken}
                                <img 
                                    src={`/api/v1/events/${ev.id}/snapshot?token=${apiToken}`} 
                                    alt="AI Object Snapshot" 
                                    class="w-full h-full object-contain group-hover/card:scale-[1.03] transition-transform duration-300"
                                    loading="lazy"
                                />
                            {:else if ev.snapshot_path}
                                <div class="w-full h-full flex flex-col items-center justify-center text-muted-foreground text-xs p-4">
                                    <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" class="mb-1 text-zinc-800 animate-pulse">
                                        <rect width="18" height="18" x="3" y="3" rx="2"/>
                                        <circle cx="9" cy="9" r="2"/>
                                        <path d="m21 15-3.086-3.086a2 2 0 0 0-2.828 0L6 21"/>
                                    </svg>
                                    <span>Loading Snapshot...</span>
                                </div>
                            {:else}
                                <div class="w-full h-full flex flex-col items-center justify-center text-muted-foreground text-xs p-4">
                                    <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" class="mb-1 text-zinc-800">
                                        <rect width="18" height="18" x="3" y="3" rx="2"/>
                                        <circle cx="9" cy="9" r="2"/>
                                        <path d="m21 15-3.086-3.086a2 2 0 0 0-2.828 0L6 21"/>
                                    </svg>
                                    <span>No Event Snapshot Saved</span>
                                </div>
                            {/if}

                            <!-- Hover Overlay with Cross-Link play shortcut -->
                            <div class="absolute inset-0 bg-black/60 opacity-0 group-hover/card:opacity-100 transition-opacity duration-250 flex items-center justify-center gap-2">
                                <button onclick={() => crossLinkEventToRecording(ev)} class="btn primary flex items-center gap-1 py-1.5 px-3">
                                    <svg width="12" height="12" viewBox="0 0 24 24" fill="currentColor">
                                        <polygon points="5 3 19 12 5 21 5 3"/>
                                    </svg>
                                    Play Footage
                                </button>
                            </div>

                            <!-- Bounding Box Class Badge -->
                            <span class="absolute top-3 left-3 text-[10px] font-extrabold tracking-wider uppercase px-2 py-0.5 rounded shadow bg-zinc-950/85 border border-zinc-800 text-zinc-300 backdrop-blur-sm flex items-center gap-1.5">
                                <span class="w-1.5 h-1.5 bg-zinc-500 rounded-full"></span>
                                {ev.label}
                            </span>

                            <!-- Camera ID overlay -->
                            <span class="absolute top-3 right-3 text-[9px] font-extrabold tracking-widest uppercase px-2 py-0.5 rounded shadow border bg-zinc-950/85 border-zinc-800 text-zinc-400 backdrop-blur-sm">
                                {ev.camera_id}
                            </span>
                        </div>

                        <!-- Card Body details -->
                        <div class="p-4 flex flex-col gap-3 flex-grow">
                            <div class="flex items-center justify-between text-xs text-muted-foreground font-mono">
                                <span class="flex items-center gap-1">
                                    <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">
                                        <circle cx="12" cy="12" r="10"/>
                                        <polyline points="12 6 12 12 16 14"/>
                                    </svg>
                                    {formatTimeOnly(ev.timestamp)}
                                </span>
                                <span>{getRelativeTime(ev.timestamp)}</span>
                            </div>

                            <!-- Confidence Score Progress tracker -->
                            <div class="flex flex-col gap-1.5 mt-1">
                                <div class="flex items-center justify-between text-[10px] font-bold tracking-wider text-muted-foreground uppercase font-mono">
                                    <span>AI CONFIDENCE</span>
                                    <span class="text-white">{(ev.confidence * 100).toFixed(0)}%</span>
                                </div>
                                <div class="w-full h-1 bg-zinc-800 rounded-full overflow-hidden">
                                    <div class="h-full bg-gradient-to-r from-indigo-500 to-cyan-400 rounded-full" style="width: {ev.confidence * 100}%"></div>
                                </div>
                            </div>
                        </div>

                        <!-- Card Footer actions -->
                        <div class="px-4 py-3 bg-muted/20 border-t border-border flex items-center justify-between gap-2 shrink-0">
                            <button onclick={() => crossLinkEventToRecording(ev)} class="glass-btn hover:text-indigo-400 py-1 px-2.5 text-[11px] cursor-pointer">
                                Play Clip
                            </button>

                            <button onclick={(e) => deleteEventItem(ev.id, e)} class="glass-btn danger py-1 px-2 text-[11px]" title="Purge event snapshot and database row">
                                <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">
                                    <path d="M3 6h18M19 6v14c0 1-1 2-2 2H7c-1 0-2-1-2-2V6M8 6V4c0-1 1-2 2-2h4c1 0 2 1 2 2v2M10 11v6M14 11v6"/>
                                </svg>
                                Delete
                            </button>
                        </div>

                    </div>
                {/each}

                {#if filteredEvents.length === 0}
                    <div class="col-span-full py-16 flex flex-col items-center justify-center text-center text-muted-foreground bg-card/45 border border-border/80 rounded-2xl">
                        <div class="w-12 h-12 rounded-full bg-zinc-900 border border-border flex items-center justify-center mb-3">
                            <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" class="text-zinc-700">
                                <path d="M12 22c5.523 0 10-4.477 10-10S17.523 2 12 2 2 6.477 2 12s4.477 10 10 10z"/>
                                <path d="M12 8v4"/>
                                <path d="M12 16h.01"/>
                            </svg>
                        </div>
                        <h4 class="text-sm font-bold text-white">No AI captures found</h4>
                        <p class="text-xs text-zinc-500 mt-1 max-w-xs">No AI detections have been logged for this camera or filtered object class yet.</p>
                    </div>
                {/if}
            </div>

        </div>
    {/if}
</div>

<style>
    /* Scrollbar overrides for timeline container */
    ::-webkit-scrollbar {
        width: 4px;
        height: 4px;
    }
    
</style>
