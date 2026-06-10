<script lang="ts">
    import { onMount, onDestroy, tick } from 'svelte';
    import { getApiToken } from '$lib/apiToken';
    import { fly, fade, slide } from 'svelte/transition';
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

    interface ExportJob {
        id: string;
        status: 'pending' | 'processing' | 'completed' | 'failed';
        progress?: number;
        startTime: string;
        endTime: string;
        message?: string;
    }

    // ─── Core state ───
    let cameras = $state<CameraConfig[]>([]);
    let selectedCameraId = $state<string>('');
    let recordings = $state<Recording[]>([]);
    let events = $state<CameraEvent[]>([]);
    let selectedDate = $state<string>(new Date().toISOString().split('T')[0]);
    let apiToken = $state<string>('');

    // ─── Video playback state ───
    import Hls from 'hls.js';
    let videoElement = $state<HTMLVideoElement | null>(null);
    let hlsInstance = $state<Hls | null>(null);
    let isVideoPaused = $state(true);
    let currentSrc = $state<string>('');
    let videoCurrentTime = $state(0);
    let videoDuration = $state(0);
    let videoVolume = $state(0.5);
    let isMuted = $state(false);
    let isFullscreen = $state(false);
    let playbackRate = $state(1);
    let showControls = $state(true);
    let controlsTimer = $state<any>(null);
    let isVideoPlaying = $derived(!isVideoPaused);
    let isVideoLoaded = $state(false);

    // ─── Timeline state ───
    let timelineEl = $state<HTMLDivElement | null>(null);
    let isDragging = $state(false);
    let hoverTime = $state<Date | null>(null);
    let hoverX = $state(0);
    let currentPlaybackTime = $state<Date | null>(null);
    let activeSegmentStartTime = $state<Date | null>(null);

    // ─── Export state ───
    let showExportPanel = $state(false);
    let exportStartTime = $state<string>('');
    let exportEndTime = $state<string>('');
    let activeExports = $state<ExportJob[]>([]);
    let isExporting = $state(false);
    let pollingInterval = $state<any>(null);

    // ─── Toast state ───
    let toastMessage = $state<string>('');
    let toastType = $state<'info' | 'success' | 'warning' | 'error'>('info');
    let showToast = $state<boolean>(false);

    // ─── Events panel ───
    let showEventsPanel = $state(false);

    // ─── Derived data ───
    let availableDates = $derived.by(() => {
        const dates = new Set<string>();
        recordings.forEach(rec => {
            if (rec.start_time) {
                dates.add(rec.start_time.split('T')[0]);
            }
        });
        return Array.from(dates).sort((a, b) => b.localeCompare(a));
    });

    let filteredRecordings = $derived.by(() => {
        return recordings
            .filter(rec => rec.start_time.split('T')[0] === selectedDate)
            .sort((a, b) => a.start_time.localeCompare(b.start_time));
    });

    let filteredEvents = $derived.by(() => {
        return events
            .filter(ev => {
                try {
                    return ev.timestamp.split('T')[0] === selectedDate;
                } catch {
                    return false;
                }
            })
            .sort((a, b) => a.timestamp.localeCompare(b.timestamp));
    });

    let selectedCamera = $derived(cameras.find(c => c.id === selectedCameraId) || null);

    // Timeline: full 24h for the selected date
    let dayStartMs = $derived(new Date(selectedDate + 'T00:00:00').getTime());
    let dayEndMs = $derived(new Date(selectedDate + 'T23:59:59.999').getTime());
    let dayDurationMs = $derived(dayEndMs - dayStartMs);

    // Convert recordings to timeline segments
    let timelineSegments = $derived.by(() => {
        return filteredRecordings.map(rec => {
            const startMs = new Date(rec.start_time).getTime();
            const endMs = new Date(rec.end_time).getTime();
            return {
                ...rec,
                startMs,
                endMs,
                startPct: Math.max(0, ((startMs - dayStartMs) / dayDurationMs) * 100),
                widthPct: Math.max(0.15, ((endMs - startMs) / dayDurationMs) * 100),
            };
        });
    });

    // Convert events to timeline markers
    let eventMarkers = $derived.by(() => {
        return filteredEvents.map(ev => {
            const timeMs = new Date(ev.timestamp).getTime();
            return {
                ...ev,
                timeMs,
                pct: Math.max(0, Math.min(100, ((timeMs - dayStartMs) / dayDurationMs) * 100)),
            };
        });
    });

    // Playback cursor position
    let cursorPct = $derived.by(() => {
        if (!currentPlaybackTime) return -1;
        const ms = currentPlaybackTime.getTime();
        return Math.max(0, Math.min(100, ((ms - dayStartMs) / dayDurationMs) * 100));
    });

    // Hour markers for the timeline
    let hourMarkers = $derived.by(() => {
        return Array.from({ length: 25 }, (_, i) => ({
            hour: i,
            label: i.toString().padStart(2, '0') + ':00',
            pct: (i / 24) * 100,
        }));
    });

    // Statistics
    let totalRecordedSeconds = $derived.by(() => {
        return filteredRecordings.reduce((sum, rec) => {
            const start = new Date(rec.start_time).getTime();
            const end = new Date(rec.end_time).getTime();
            return sum + Math.max(0, (end - start) / 1000);
        }, 0);
    });

    // ─── Functions ───

    function triggerToast(msg: string, type: typeof toastType = 'info') {
        toastMessage = msg;
        toastType = type;
        showToast = true;
        setTimeout(() => { showToast = false; }, 4000);
    }

    async function loadInitialData() {
        try {
            const camRes = await fetch('/api/v1/cameras', { cache: 'no-store' });
            cameras = await camRes.json();
            if (cameras.length > 0) {
                const defaultCam = cameras.find(c => c.enabled) || cameras[0];
                selectedCameraId = defaultCam.id;
            }
        } catch (e) {
            console.error('[Review] Init failed:', e);
            triggerToast('Failed to load cameras list.', 'error');
        }
    }

    async function fetchRecordings(cameraId: string) {
        if (!cameraId) return;
        try {
            const res = await fetch(`/api/v1/recordings?camera_id=${cameraId}`, { cache: 'no-store' });
            if (res.ok) {
                recordings = await res.json();
                const hasCurrentDateRecs = recordings.some(r => r.start_time.split('T')[0] === selectedDate);
                if (!hasCurrentDateRecs && recordings.length > 0) {
                    selectedDate = recordings[recordings.length - 1].start_time.split('T')[0];
                }
            }
        } catch (e) {
            console.error('[Review] Recordings fetch failed:', e);
            triggerToast('Failed to fetch video segments.', 'error');
        }
    }

    async function fetchEvents(cameraId: string) {
        if (!cameraId) return;
        try {
            const res = await fetch(`/api/v1/events?camera_id=${cameraId}&limit=500`, { cache: 'no-store' });
            if (res.ok) {
                events = await res.json();
            }
        } catch (e) {
            console.error('[Review] Events fetch failed:', e);
        }
    }

    // ─── HLS Playback ───

    function playHls(src: string, seekToSec?: number) {
        if (hlsInstance) {
            hlsInstance.destroy();
            hlsInstance = null;
        }
        if (!videoElement) return;
        videoElement.src = '';
        isVideoLoaded = false;

        if (videoElement.canPlayType('application/vnd.apple.mpegurl')) {
            videoElement.src = src;
            if (seekToSec && seekToSec > 0) {
                const onLoaded = () => {
                    if (videoElement) videoElement.currentTime = seekToSec;
                    videoElement?.removeEventListener('loadedmetadata', onLoaded);
                };
                videoElement.addEventListener('loadedmetadata', onLoaded);
            }
            videoElement.play().catch(err => console.log('Autoplay blocked:', err));
        } else if (Hls.isSupported()) {
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
                isVideoLoaded = true;
                if (seekToSec && seekToSec > 0 && videoElement) {
                    videoElement.currentTime = seekToSec;
                }
                videoElement?.play().catch(err => console.log('HLS autoplay blocked:', err));
            });

            hls.on(Hls.Events.ERROR, (_event, data) => {
                if (data.fatal) {
                    switch (data.type) {
                        case Hls.ErrorTypes.NETWORK_ERROR:
                            console.error('Fatal network error, recovering...', data);
                            hls.startLoad();
                            break;
                        case Hls.ErrorTypes.MEDIA_ERROR:
                            console.error('Fatal media error, recovering...', data);
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
            videoElement.src = src;
            videoElement.play().catch(err => console.log('Autoplay blocked:', err));
        }
    }

    async function playTimeRange(startIso: string, endIso: string, cameraId: string, seekOffsetSec?: number) {
        const token = await getApiToken();
        let src = `/api/v1/recordings/vod/index.m3u8?camera_id=${cameraId}&start_time=${startIso}&end_time=${endIso}`;
        if (token) {
            src += `&token=${encodeURIComponent(token)}`;
        }
        currentSrc = src;
        activeSegmentStartTime = new Date(startIso);

        tick().then(() => {
            playHls(src, seekOffsetSec);
        });
    }

    function seekToTime(targetTime: Date) {
        if (!selectedCameraId) return;

        // Find the recording session that contains this time
        const targetMs = targetTime.getTime();
        const containingRec = filteredRecordings.find(rec => {
            const start = new Date(rec.start_time).getTime();
            const end = new Date(rec.end_time).getTime();
            return targetMs >= start && targetMs <= end;
        });

        if (containingRec) {
            // Find the contiguous session (merge segments within 15s gaps)
            let sessionStart = containingRec;
            let sessionEnd = containingRec;
            const sorted = [...filteredRecordings].sort((a, b) => a.start_time.localeCompare(b.start_time));
            const idx = sorted.findIndex(r => r.id === containingRec.id);

            // Expand session backwards
            for (let i = idx - 1; i >= 0; i--) {
                const prevEnd = new Date(sorted[i].end_time).getTime();
                const currStart = new Date(sorted[i + 1].start_time).getTime();
                if (currStart - prevEnd <= 15000) {
                    sessionStart = sorted[i];
                } else break;
            }

            // Expand session forwards
            for (let i = idx + 1; i < sorted.length; i++) {
                const prevEnd = new Date(sorted[i - 1].end_time).getTime();
                const currStart = new Date(sorted[i].start_time).getTime();
                if (currStart - prevEnd <= 15000) {
                    sessionEnd = sorted[i];
                } else break;
            }

            const sessionStartMs = new Date(sessionStart.start_time).getTime();
            const seekOffset = Math.max(0, (targetMs - sessionStartMs) / 1000);
            playTimeRange(sessionStart.start_time, sessionEnd.end_time, selectedCameraId, seekOffset);

            exportStartTime = toDatetimeLocalString(sessionStart.start_time);
            exportEndTime = toDatetimeLocalString(sessionEnd.end_time);
        } else {
            // Find nearest recording
            let nearest: Recording | null = null;
            let nearestDist = Infinity;
            for (const rec of filteredRecordings) {
                const start = new Date(rec.start_time).getTime();
                const end = new Date(rec.end_time).getTime();
                const dist = Math.min(Math.abs(targetMs - start), Math.abs(targetMs - end));
                if (dist < nearestDist) {
                    nearestDist = dist;
                    nearest = rec;
                }
            }
            if (nearest && nearestDist < 60000) {
                seekToTime(new Date(nearest.start_time));
            } else {
                triggerToast('No recording at this time', 'warning');
            }
        }
    }

    // ─── Timeline interaction ───

    function getTimeFromPosition(clientX: number): Date | null {
        if (!timelineEl) return null;
        const rect = timelineEl.getBoundingClientRect();
        const pct = Math.max(0, Math.min(1, (clientX - rect.left) / rect.width));
        const timeMs = dayStartMs + pct * dayDurationMs;
        return new Date(timeMs);
    }

    function handleTimelineMouseDown(e: MouseEvent) {
        isDragging = true;
        const time = getTimeFromPosition(e.clientX);
        if (time) {
            seekToTime(time);
        }
    }

    function handleTimelineMouseMove(e: MouseEvent) {
        const time = getTimeFromPosition(e.clientX);
        hoverTime = time;
        if (timelineEl) {
            const rect = timelineEl.getBoundingClientRect();
            hoverX = e.clientX - rect.left;
        }
        if (isDragging && time) {
            seekToTime(time);
        }
    }

    function handleTimelineMouseUp() {
        isDragging = false;
    }

    function handleTimelineMouseLeave() {
        hoverTime = null;
        isDragging = false;
    }

    // ─── Video events ───

    function handleTimeUpdate() {
        if (!videoElement) return;
        videoCurrentTime = videoElement.currentTime;
        if (videoElement.duration && isFinite(videoElement.duration)) {
            videoDuration = videoElement.duration;
        }

        // Update the absolute playback time for the timeline cursor
        if (activeSegmentStartTime && videoElement.currentTime >= 0) {
            currentPlaybackTime = new Date(activeSegmentStartTime.getTime() + videoElement.currentTime * 1000);
        }
    }

    function handleLoadedMetadata() {
        if (videoElement && videoElement.duration && isFinite(videoElement.duration)) {
            videoDuration = videoElement.duration;
            isVideoLoaded = true;
        }
    }

    // ─── Video controls ───

    function togglePlay() {
        if (!videoElement) return;
        if (videoElement.paused) {
            videoElement.play().catch(() => {});
        } else {
            videoElement.pause();
        }
    }

    function toggleMute() {
        if (!videoElement) return;
        isMuted = !isMuted;
        videoElement.muted = isMuted;
    }

    function setVolume(e: Event) {
        const target = e.target as HTMLInputElement;
        videoVolume = parseFloat(target.value);
        if (videoElement) {
            videoElement.volume = videoVolume;
            if (videoVolume > 0) isMuted = false;
        }
    }

    function setPlaybackRate(rate: number) {
        playbackRate = rate;
        if (videoElement) videoElement.playbackRate = rate;
    }

    function toggleFullscreen() {
        const container = document.getElementById('review-player-wrapper');
        if (!container) return;
        if (!document.fullscreenElement) {
            container.requestFullscreen().then(() => { isFullscreen = true; }).catch(() => {});
        } else {
            document.exitFullscreen().then(() => { isFullscreen = false; }).catch(() => {});
        }
    }

    function skipSeconds(sec: number) {
        if (!videoElement) return;
        videoElement.currentTime = Math.max(0, Math.min(videoElement.duration || 0, videoElement.currentTime + sec));
    }

    function handleMouseMovePlayer() {
        showControls = true;
        if (controlsTimer) clearTimeout(controlsTimer);
        controlsTimer = setTimeout(() => {
            if (isVideoPlaying) showControls = false;
        }, 3000);
    }

    // ─── Progress bar scrub ───

    function handleProgressClick(e: MouseEvent) {
        if (!videoElement || !videoDuration) return;
        const target = e.currentTarget as HTMLElement;
        const rect = target.getBoundingClientRect();
        const pct = (e.clientX - rect.left) / rect.width;
        videoElement.currentTime = pct * videoDuration;
    }

    // ─── Helpers ───

    function formatTimeOnly(isoString: string): string {
        try {
            const date = new Date(isoString);
            return date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false });
        } catch { return ''; }
    }

    function formatTimeHMS(date: Date): string {
        const h = date.getHours().toString().padStart(2, '0');
        const m = date.getMinutes().toString().padStart(2, '0');
        const s = date.getSeconds().toString().padStart(2, '0');
        return `${h}:${m}:${s}`;
    }

    function formatDuration(seconds: number): string {
        if (seconds < 60) return `${Math.round(seconds)}s`;
        const h = Math.floor(seconds / 3600);
        const m = Math.floor((seconds % 3600) / 60);
        const s = Math.round(seconds % 60);
        if (h > 0) return `${h}h ${m}m`;
        if (s === 0) return `${m}m`;
        return `${m}m ${s}s`;
    }

    function formatDateFriendly(isoString: string): string {
        try {
            const date = new Date(isoString);
            return date.toLocaleDateString([], { weekday: 'short', month: 'short', day: 'numeric' });
        } catch { return isoString; }
    }

    function formatVideoTime(seconds: number): string {
        if (!isFinite(seconds) || seconds < 0) return '0:00';
        const h = Math.floor(seconds / 3600);
        const m = Math.floor((seconds % 3600) / 60);
        const s = Math.floor(seconds % 60);
        if (h > 0) return `${h}:${m.toString().padStart(2, '0')}:${s.toString().padStart(2, '0')}`;
        return `${m}:${s.toString().padStart(2, '0')}`;
    }

    function toDatetimeLocalString(isoString: string): string {
        if (!isoString) return '';
        const d = new Date(isoString);
        if (isNaN(d.getTime())) return '';
        const pad = (n: number) => n.toString().padStart(2, '0');
        return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
    }

    function getFilenameFromPath(filepath: string): string {
        if (!filepath) return 'segment.mp4';
        return filepath.split(/[\\/]/).pop() || 'segment.mp4';
    }

    function getSeverityColor(severity: string): string {
        switch (severity) {
            case 'critical': return 'bg-crimson';
            case 'warning': return 'bg-gold';
            default: return 'bg-cyan';
        }
    }

    // ─── Delete ───

    async function deleteRecordingAtCursor() {
        if (!currentPlaybackTime || !selectedCameraId) return;
        const targetMs = currentPlaybackTime.getTime();
        const rec = filteredRecordings.find(r => {
            const s = new Date(r.start_time).getTime();
            const e = new Date(r.end_time).getTime();
            return targetMs >= s && targetMs <= e;
        });
        if (!rec) {
            triggerToast('No segment at current time.', 'warning');
            return;
        }
        if (!confirm('Delete this recording segment permanently?')) return;
        try {
            const res = await fetch(`/api/v1/recordings/${rec.id}`, { method: 'DELETE' });
            const data = await res.json();
            if (data.success) {
                triggerToast('Segment deleted.', 'success');
                await fetchRecordings(selectedCameraId);
            } else {
                triggerToast(data.error || 'Failed to delete.', 'error');
            }
        } catch (err: any) {
            triggerToast(`Error: ${err.message}`, 'error');
        }
    }

    // ─── Export ───

    async function fetchExports() {
        try {
            const res = await fetch('/api/v1/exports');
            if (res.ok) activeExports = await res.json();
        } catch (e) { console.error('[Review] Exports fetch failed:', e); }
    }

    async function downloadExport(jobId: string) {
        const url = `/api/v1/exports/${jobId}/download`;
        const token = await getApiToken();
        const headers: Record<string, string> = {};
        if (token) headers['X-Local-Token'] = token;
        
        try {
            const res = await fetch(url, { headers });
            if (res.ok) {
                const blob = await res.blob();
                const blobUrl = URL.createObjectURL(blob);
                const a = document.createElement('a');
                a.href = blobUrl;
                a.download = `${jobId}.mp4`;
                document.body.appendChild(a);
                a.click();
                document.body.removeChild(a);
                URL.revokeObjectURL(blobUrl);
            } else {
                triggerToast('Failed to download export.', 'error');
            }
        } catch (e) {
            triggerToast('Download error', 'error');
        }
    }

    async function triggerCustomExport() {
        if (!selectedCameraId) { triggerToast('Select a camera first.', 'error'); return; }
        if (!exportStartTime || !exportEndTime) { triggerToast('Set start and end times.', 'error'); return; }
        const start = new Date(exportStartTime);
        const end = new Date(exportEndTime);
        if (isNaN(start.getTime()) || isNaN(end.getTime())) { triggerToast('Invalid time format.', 'error'); return; }
        if (start >= end) { triggerToast('Start must be before end.', 'error'); return; }

        isExporting = true;
        try {
            const res = await fetch('/api/v1/exports', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    camera_id: selectedCameraId,
                    start_time: start.toISOString(),
                    end_time: end.toISOString()
                })
            });
            const data = await res.json();
            if (res.ok && data.success) {
                triggerToast('Export job started!', 'success');
                await fetchExports();
            } else {
                triggerToast(data.error || 'Export failed.', 'error');
            }
        } catch (e: any) {
            triggerToast(`Error: ${e.message}`, 'error');
        } finally {
            isExporting = false;
        }
    }

    // ─── Quick seek helpers ───

    function playFirstRecording() {
        if (filteredRecordings.length > 0) {
            seekToTime(new Date(filteredRecordings[0].start_time));
        }
    }

    function jumpToEvent(ev: CameraEvent) {
        const eventDate = ev.timestamp.split('T')[0];
        if (eventDate !== selectedDate) {
            selectedDate = eventDate;
        }
        setTimeout(() => {
            seekToTime(new Date(ev.timestamp));
            triggerToast(`Jumped to ${ev.label} detection`, 'success');
        }, 100);
    }

    // ─── Keyboard shortcuts ───

    function handleKeyDown(e: KeyboardEvent) {
        if (e.target instanceof HTMLInputElement || e.target instanceof HTMLTextAreaElement || e.target instanceof HTMLSelectElement) return;

        switch (e.key) {
            case ' ':
            case 'k':
                e.preventDefault();
                togglePlay();
                break;
            case 'ArrowLeft':
                e.preventDefault();
                skipSeconds(e.shiftKey ? -30 : -5);
                break;
            case 'ArrowRight':
                e.preventDefault();
                skipSeconds(e.shiftKey ? 30 : 5);
                break;
            case 'm':
                e.preventDefault();
                toggleMute();
                break;
            case 'f':
                e.preventDefault();
                toggleFullscreen();
                break;
            case ',':
                e.preventDefault();
                if (videoElement) videoElement.currentTime = Math.max(0, videoElement.currentTime - (1 / 30));
                break;
            case '.':
                e.preventDefault();
                if (videoElement) videoElement.currentTime += (1 / 30);
                break;
        }
    }

    // ─── Lifecycle ───

    onMount(async () => {
        const token = await getApiToken();
        if (token) apiToken = token;
        await loadInitialData();
        await fetchExports();

        window.addEventListener('keydown', handleKeyDown);
        window.addEventListener('mouseup', handleTimelineMouseUp);
        document.addEventListener('fullscreenchange', () => {
            isFullscreen = !!document.fullscreenElement;
        });

        // Handle URL params for deep linking
        const params = $page.url.searchParams;
        const paramCameraId = params.get('camera_id');
        const paramTimestamp = params.get('timestamp');

        if (paramCameraId) selectedCameraId = paramCameraId;

        if (paramTimestamp) {
            setTimeout(async () => {
                if (selectedCameraId) {
                    await fetchRecordings(selectedCameraId);
                    const eventDate = paramTimestamp.split('T')[0];
                    selectedDate = eventDate;
                    setTimeout(() => {
                        seekToTime(new Date(paramTimestamp));
                        triggerToast('Loaded recording at event timestamp.', 'success');
                    }, 300);
                }
            }, 600);
        }
    });

    $effect(() => {
        if (selectedCameraId) {
            if (hlsInstance) { hlsInstance.destroy(); hlsInstance = null; }
            currentPlaybackTime = null;
            activeSegmentStartTime = null;
            isVideoLoaded = false;
            fetchRecordings(selectedCameraId);
            fetchEvents(selectedCameraId);
        }
    });

    $effect(() => {
        if (videoElement) {
            videoElement.volume = videoVolume;
        }
    });

    $effect(() => {
        const hasActive = activeExports.some(j => j.status === 'pending' || j.status === 'processing');
        if (hasActive) {
            if (!pollingInterval) pollingInterval = setInterval(fetchExports, 2000);
        } else {
            if (pollingInterval) { clearInterval(pollingInterval); pollingInterval = null; }
        }
    });

    onDestroy(() => {
        if (pollingInterval) clearInterval(pollingInterval);
        if (hlsInstance) hlsInstance.destroy();
        if (controlsTimer) clearTimeout(controlsTimer);
        window.removeEventListener('keydown', handleKeyDown);
        window.removeEventListener('mouseup', handleTimelineMouseUp);
    });
</script>

<div class="flex flex-col w-full h-full text-foreground page-enter" style="min-height: calc(100vh - 64px);">

    <!-- Toast -->
    {#if showToast}
        <div class="fixed bottom-6 right-6 z-[100] max-w-sm" transition:fly={{ y: 20, duration: 200 }}>
            <div class="px-4 py-3 rounded-xl border backdrop-blur-md flex items-center gap-3
                {toastType === 'info' ? 'bg-cyan/10 border-cyan/30' : ''}
                {toastType === 'success' ? 'bg-jade/10 border-jade/30' : ''}
                {toastType === 'warning' ? 'bg-gold/10 border-gold/30' : ''}
                {toastType === 'error' ? 'bg-crimson/10 border-crimson/30' : ''}">
                <span class="w-2 h-2 rounded-full status-pulse"
                    class:bg-cyan={toastType === 'info'}
                    class:bg-jade={toastType === 'success'}
                    class:bg-gold={toastType === 'warning'}
                    class:bg-crimson={toastType === 'error'}></span>
                <span class="text-xs font-semibold text-foreground">{toastMessage}</span>
            </div>
        </div>
    {/if}

    <!-- ═══════════════════ TOP BAR ═══════════════════ -->
    <div class="flex items-center justify-between gap-3 px-5 py-3 border-b border-border bg-card/60 backdrop-blur-sm shrink-0">
        <div class="flex items-center gap-3">
            <div class="flex items-center gap-2">
                <span class="badge badge-iris">
                    <span class="w-1.5 h-1.5 rounded-full bg-iris status-pulse"></span>
                    History
                </span>
                {#if selectedCamera}
                    <span class="text-[11px] text-muted-foreground font-mono flex items-center gap-1.5">
                        <span class="w-1 h-1 rounded-full bg-cyan"></span>
                        {selectedCamera.name}
                        <span class="text-muted-foreground/50">·</span>
                        <span class="uppercase tracking-wider">{selectedCamera.source}</span>
                    </span>
                {/if}
            </div>
        </div>

        <div class="flex items-center gap-2">
            <!-- Stats -->
            <div class="flex items-center gap-1.5 px-2.5 h-7 rounded-md border border-border bg-surface-2/50">
                <span class="text-[10px] font-mono font-bold text-foreground tabular-nums">{filteredRecordings.length}</span>
                <span class="text-[9px] text-muted-foreground uppercase tracking-wider">seg</span>
            </div>
            <div class="flex items-center gap-1.5 px-2.5 h-7 rounded-md border border-border bg-surface-2/50">
                <span class="text-[10px] font-mono font-bold text-foreground tabular-nums">{formatDuration(totalRecordedSeconds)}</span>
                <span class="text-[9px] text-muted-foreground uppercase tracking-wider">recorded</span>
            </div>
            <div class="flex items-center gap-1.5 px-2.5 h-7 rounded-md border border-iris/20 bg-iris/5">
                <span class="w-1.5 h-1.5 rounded-full bg-iris"></span>
                <span class="text-[10px] font-mono font-bold text-foreground tabular-nums">{filteredEvents.length}</span>
                <span class="text-[9px] text-muted-foreground uppercase tracking-wider">events</span>
            </div>

            <div class="h-5 w-px bg-border"></div>

            <!-- Export panel toggle -->
            <button onclick={() => showExportPanel = !showExportPanel}
                class="btn-icon !w-7 !h-7 !rounded-md {showExportPanel ? '!bg-jade/20 !border-jade/40 !text-jade' : ''}"
                title="Toggle export panel">
                <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">
                    <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4M7 10l5 5 5-5M12 15V3"/>
                </svg>
            </button>
        </div>
    </div>

    <!-- ═══════════════════ MAIN CONTENT ═══════════════════ -->
    <div class="flex-1 flex overflow-hidden relative">

        <!-- DVR Sidebar -->
        <div class="w-64 border-r border-border bg-card/40 flex flex-col shrink-0 overflow-y-auto">
            <!-- Camera Tree -->
            <div class="px-4 py-2 border-b border-border bg-card/60 flex items-center justify-between sticky top-0 z-10">
                <span class="font-semibold text-[10px] text-muted-foreground uppercase tracking-wider">Device List</span>
            </div>
            <div class="flex-1 p-2 flex flex-col gap-1">
                {#each cameras as cam}
                    <button onclick={() => selectedCameraId = cam.id}
                        class="text-left px-3 py-2 text-xs rounded border border-transparent hover:bg-card-hover {selectedCameraId === cam.id ? '!bg-cyan/10 !border-cyan/30 !text-cyan' : ''}">
                        <div class="flex items-center gap-2">
                            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M23 19a2 2 0 0 1-2 2H3a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h4l2-3h6l2 3h4a2 2 0 0 1 2 2z"/><circle cx="12" cy="13" r="4"/></svg>
                            {cam.name}
                        </div>
                    </button>
                {/each}
                {#if cameras.length === 0}
                    <p class="text-xs text-muted-foreground p-3 text-center">No cameras available</p>
                {/if}
            </div>
            
            <!-- Calendar / Date Select -->
            <div class="px-4 py-2 border-y border-border bg-card/60 flex items-center justify-between sticky top-0 z-10">
                <span class="font-semibold text-[10px] text-muted-foreground uppercase tracking-wider">Playback Date</span>
            </div>
            <div class="p-3 bg-surface-1">
                <input type="date" bind:value={selectedDate} class="w-full bg-background border border-border rounded text-xs p-2 text-foreground focus:border-cyan outline-none" />
            </div>


        </div>

        <!-- ─── Video player area ─── -->
        <!-- svelte-ignore a11y_no_static_element_interactions -->
        <div class="flex-1 flex flex-col min-w-0" id="review-player-wrapper"
            onmousemove={handleMouseMovePlayer}
        >
            <div class="flex-1 relative bg-black overflow-hidden">
                {#if isVideoLoaded || currentSrc}
                    <!-- svelte-ignore a11y_media_has_caption -->
                    <video
                        bind:this={videoElement}
                        autoplay
                        playsinline
                        class="w-full h-full object-contain"
                        bind:paused={isVideoPaused}
                        ontimeupdate={handleTimeUpdate}
                        onloadedmetadata={handleLoadedMetadata}
                    ></video>

                    <!-- Recording info overlay (top) -->
                    <div class="absolute top-0 left-0 right-0 p-3 flex items-start justify-between pointer-events-none z-10
                        bg-gradient-to-b from-black/60 via-black/20 to-transparent transition-opacity duration-300"
                        style:opacity={showControls ? 1 : 0}
                    >
                        <div class="flex items-center gap-2 pointer-events-auto">
                            <div class="flex items-center gap-1.5 px-2.5 h-6 rounded-md border border-crimson/40 bg-black/50 backdrop-blur-sm">
                                <span class="w-1.5 h-1.5 rounded-full bg-crimson {isVideoPlaying ? 'status-pulse' : ''}"></span>
                                <span class="text-[10px] font-mono font-bold text-white uppercase tracking-wider">{isVideoPlaying ? 'Playing' : 'Paused'}</span>
                            </div>
                            {#if playbackRate !== 1}
                                <div class="px-2 h-6 rounded-md border border-gold/40 bg-black/50 backdrop-blur-sm flex items-center">
                                    <span class="text-[10px] font-mono font-bold text-gold">{playbackRate}x</span>
                                </div>
                            {/if}
                        </div>

                        {#if currentPlaybackTime}
                            <div class="px-2.5 h-6 rounded-md border border-cyan/40 bg-black/50 backdrop-blur-sm flex items-center pointer-events-auto">
                                <span class="text-[10px] font-mono text-cyan font-bold tabular-nums">{formatTimeHMS(currentPlaybackTime)}</span>
                            </div>
                        {/if}
                    </div>

                    <!-- Custom controls overlay (bottom) -->
                    <div class="absolute bottom-0 left-0 right-0 z-10 transition-opacity duration-300
                        bg-gradient-to-t from-black/80 via-black/40 to-transparent"
                        style:opacity={showControls ? 1 : 0}
                    >
                        <!-- Progress bar -->
                        <div class="px-3 pt-4 pb-1 group/progress">
                            <!-- svelte-ignore a11y_click_events_have_key_events -->
                            <div class="relative h-1 group-hover/progress:h-1.5 transition-all cursor-pointer rounded-full overflow-hidden bg-white/20"
                                onclick={handleProgressClick}
                                role="slider"
                                tabindex="-1"
                                aria-label="Video progress"
                                aria-valuenow={videoDuration > 0 ? Math.round((videoCurrentTime / videoDuration) * 100) : 0}
                                aria-valuemin="0"
                                aria-valuemax="100"
                            >
                                <!-- Buffered -->
                                <div class="absolute inset-y-0 left-0 bg-white/15 rounded-full"
                                    style:width="{videoDuration > 0 ? (videoCurrentTime / videoDuration) * 100 + 10 : 0}%"></div>
                                <!-- Played -->
                                <div class="absolute inset-y-0 left-0 bg-cyan rounded-full transition-all"
                                    style:width="{videoDuration > 0 ? (videoCurrentTime / videoDuration) * 100 : 0}%"></div>
                                <!-- Thumb -->
                                <div class="absolute top-1/2 -translate-y-1/2 w-3 h-3 rounded-full bg-cyan shadow-[0_0_6px_hsl(187_75%_58%/0.6)] opacity-0 group-hover/progress:opacity-100 transition-opacity"
                                    style:left="{videoDuration > 0 ? (videoCurrentTime / videoDuration) * 100 : 0}%"
                                    style:margin-left="-6px"></div>
                            </div>
                        </div>

                        <!-- Controls row -->
                        <div class="flex items-center justify-between px-3 pb-3 pt-1">
                            <div class="flex items-center gap-1.5">
                                <!-- Play/pause -->
                                <button onclick={togglePlay} class="review-ctrl-btn" title="{isVideoPlaying ? 'Pause' : 'Play'} (Space)">
                                    {#if isVideoPlaying}
                                        <svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor"><rect x="6" y="4" width="4" height="16"/><rect x="14" y="4" width="4" height="16"/></svg>
                                    {:else}
                                        <svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor"><polygon points="6 4 20 12 6 20 6 4"/></svg>
                                    {/if}
                                </button>

                                <!-- Skip back -->
                                <button onclick={() => skipSeconds(-5)} class="review-ctrl-btn" title="Back 5s (←)">
                                    <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">
                                        <path d="M12 2a10 10 0 1 0 10 10"/><polyline points="12 8 12 2 18 2"/>
                                    </svg>
                                </button>

                                <!-- Skip forward -->
                                <button onclick={() => skipSeconds(5)} class="review-ctrl-btn" title="Forward 5s (→)">
                                    <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">
                                        <path d="M12 2a10 10 0 1 1-10 10"/><polyline points="12 8 12 2 6 2"/>
                                    </svg>
                                </button>

                                <!-- Volume -->
                                <button onclick={toggleMute} class="review-ctrl-btn ml-1" title="Mute (M)">
                                    {#if isMuted || videoVolume === 0}
                                        <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><polygon points="11 5 6 9 2 9 2 15 6 15 11 19 11 5"/><line x1="23" y1="9" x2="17" y2="15"/><line x1="17" y1="9" x2="23" y2="15"/></svg>
                                    {:else}
                                        <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><polygon points="11 5 6 9 2 9 2 15 6 15 11 19 11 5"/><path d="M15.54 8.46a5 5 0 0 1 0 7.07"/></svg>
                                    {/if}
                                </button>
                                <input type="range" min="0" max="1" step="0.05" bind:value={videoVolume} oninput={setVolume}
                                    class="w-16 h-1 bg-white/20 rounded-full appearance-none cursor-pointer accent-cyan
                                    [&::-webkit-slider-thumb]:appearance-none [&::-webkit-slider-thumb]:w-2.5 [&::-webkit-slider-thumb]:h-2.5 [&::-webkit-slider-thumb]:bg-white [&::-webkit-slider-thumb]:rounded-full" />

                                <span class="text-[10px] font-mono text-white/70 tabular-nums ml-2">
                                    {formatVideoTime(videoCurrentTime)} / {formatVideoTime(videoDuration)}
                                </span>
                            </div>

                            <div class="flex items-center gap-1.5">
                                <!-- Speed -->
                                <div class="flex items-center gap-0.5 bg-white/10 rounded-md p-0.5">
                                    {#each [0.5, 1, 1.5, 2, 4] as rate}
                                        <button onclick={() => setPlaybackRate(rate)}
                                            class="px-1.5 h-5 rounded text-[9px] font-mono font-bold transition-all
                                            {playbackRate === rate ? 'bg-cyan text-black' : 'text-white/60 hover:text-white'}"
                                        >{rate}x</button>
                                    {/each}
                                </div>

                                <!-- Delete segment -->
                                <button onclick={deleteRecordingAtCursor} class="review-ctrl-btn" title="Delete current segment">
                                    <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">
                                        <path d="M3 6h18M19 6v14c0 1-1 2-2 2H7c-1 0-2-1-2-2V6M8 6V4c0-1 1-2 2-2h4c1 0 2 1 2 2v2"/>
                                    </svg>
                                </button>

                                <!-- Fullscreen -->
                                <button onclick={toggleFullscreen} class="review-ctrl-btn" title="Fullscreen (F)">
                                    {#if isFullscreen}
                                        <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><path d="M8 3v3a2 2 0 0 1-2 2H3m18 0h-3a2 2 0 0 1-2-2V3m0 18v-3a2 2 0 0 1 2-2h3M3 16h3a2 2 0 0 1 2 2v3"/></svg>
                                    {:else}
                                        <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><path d="M8 3H5a2 2 0 0 0-2 2v3m18 0V5a2 2 0 0 0-2-2h-3m0 18h3a2 2 0 0 0 2-2v-3M3 16v3a2 2 0 0 0 2 2h3"/></svg>
                                    {/if}
                                </button>
                            </div>
                        </div>
                    </div>

                {:else}
                    <!-- Empty state -->
                    <div class="absolute inset-0 surface-grid flex flex-col items-center justify-center p-8 text-center">
                        <div class="w-20 h-20 rounded-2xl bg-surface-2 border border-border flex items-center justify-center text-muted-foreground mb-5">
                            <svg width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">
                                <polygon points="5 3 19 12 5 21 5 3"/>
                            </svg>
                        </div>
                        <h3 class="text-base font-display font-semibold text-foreground">Select a point on the timeline</h3>
                        <p class="text-xs text-muted-foreground mt-2 max-w-md leading-relaxed">
                            Click anywhere on the timeline below to start playback. Colored segments indicate available recordings.
                        </p>
                        <div class="flex items-center gap-2 mt-4">
                            {#if filteredRecordings.length > 0}
                                <button onclick={playFirstRecording} class="btn btn-primary btn-sm">
                                    <svg width="12" height="12" viewBox="0 0 24 24" fill="currentColor"><polygon points="5 3 19 12 5 21 5 3"/></svg>
                                    Play First Segment
                                </button>
                            {/if}
                        </div>
                        <!-- Keyboard shortcuts hint -->
                        <div class="flex flex-wrap items-center justify-center gap-3 mt-6 text-[10px] text-muted-foreground">
                            <span class="flex items-center gap-1"><kbd>Space</kbd> Play/Pause</span>
                            <span class="flex items-center gap-1"><kbd>←</kbd><kbd>→</kbd> Seek ±5s</span>
                            <span class="flex items-center gap-1"><kbd>F</kbd> Fullscreen</span>
                            <span class="flex items-center gap-1"><kbd>M</kbd> Mute</span>
                        </div>
                    </div>
                {/if}
            </div>

            <!-- ═══════════════════ SCRUBABLE TIMELINE ═══════════════════ -->
            <div class="shrink-0 bg-card border-t border-border select-none">
                <!-- Timeline controls header -->
                <div class="flex items-center justify-between px-4 py-1.5 border-b border-border/50">
                    <div class="flex items-center gap-3">
                        <span class="section-eyebrow flex items-center gap-1.5">
                            <span class="w-1 h-1 rounded-full bg-cyan"></span>
                            Timeline
                        </span>
                        <span class="text-[10px] font-mono text-muted-foreground">{formatDateFriendly(selectedDate + 'T00:00:00')}</span>
                    </div>
                    <div class="flex items-center gap-3">
                        <!-- Legend -->
                        <div class="flex items-center gap-3 text-[9px] text-muted-foreground uppercase tracking-wider">
                            <span class="flex items-center gap-1"><span class="w-2 h-2 rounded-sm bg-cyan/70"></span> Continuous</span>
                            <span class="flex items-center gap-1"><span class="w-2 h-2 rounded-sm bg-ember/70"></span> Event</span>
                            <span class="flex items-center gap-1"><span class="w-1.5 h-1.5 rounded-full bg-iris"></span> AI Detection</span>
                        </div>
                    </div>
                </div>

                <!-- Timeline bar -->
                <div class="relative px-4 py-3">
                    <!-- Hour labels -->
                    <div class="relative w-full h-3 mb-1">
                        {#each hourMarkers as marker}
                            {#if marker.hour % 2 === 0 && marker.hour < 24}
                                <span class="absolute text-[8px] font-mono text-muted-foreground/60 -translate-x-1/2 tabular-nums"
                                    style:left="{marker.pct}%"
                                >{marker.label}</span>
                            {/if}
                        {/each}
                    </div>

                    <!-- Main timeline track -->
                    <!-- svelte-ignore a11y_click_events_have_key_events -->
                    <div
                        bind:this={timelineEl}
                        class="relative w-full h-10 rounded-md bg-surface-2 border border-border/50 cursor-crosshair overflow-hidden"
                        onmousedown={handleTimelineMouseDown}
                        onmousemove={handleTimelineMouseMove}
                        onmouseleave={handleTimelineMouseLeave}
                        role="slider"
                        tabindex="-1"
                        aria-label="Recording timeline"
                        aria-valuenow={videoDuration > 0 ? Math.round((videoCurrentTime / videoDuration) * 100) : 0}
                        aria-valuemin="0"
                        aria-valuemax="100"
                    >
                        <!-- Hour grid lines -->
                        {#each hourMarkers as marker}
                            <div class="absolute top-0 bottom-0 w-px {marker.hour % 6 !== 0 ? 'bg-border/40' : 'bg-border'}"
                                style:left="{marker.pct}%"></div>
                        {/each}

                        <!-- Recording segments -->
                        {#each timelineSegments as seg}
                            <div class="absolute top-1 bottom-1 rounded-sm transition-opacity
                                {seg.type === 'event' ? 'bg-ember/60 hover:bg-ember/80' : 'bg-cyan/40 hover:bg-cyan/60'}"
                                style:left="{seg.startPct}%"
                                style:width="{seg.widthPct}%"
                                title="{formatTimeOnly(seg.start_time)} — {formatTimeOnly(seg.end_time)}"
                            ></div>
                        {/each}

                        <!-- Event markers -->
                        {#each eventMarkers as marker}
                            <button class="absolute top-0 w-0.5 h-full group/marker z-10"
                                style:left="{marker.pct}%"
                                onclick={() => jumpToEvent(marker)}
                                title="{marker.label} — {formatTimeOnly(marker.timestamp)}"
                            >
                                <div class="absolute top-0 left-1/2 -translate-x-1/2 w-2 h-2 rounded-full {getSeverityColor(marker.severity)} opacity-80 group-hover/marker:opacity-100 group-hover/marker:scale-150 transition-all shadow-[0_0_4px_currentColor]"></div>
                            </button>
                        {/each}

                        <!-- Hover indicator -->
                        {#if hoverTime && !isDragging}
                            <div class="absolute top-0 bottom-0 w-px bg-white/40 pointer-events-none z-20"
                                style:left="{((hoverTime.getTime() - dayStartMs) / dayDurationMs) * 100}%">
                            </div>
                        {/if}

                        <!-- Playback cursor -->
                        {#if cursorPct >= 0}
                            <div class="absolute top-0 bottom-0 z-30 pointer-events-none"
                                style:left="{cursorPct}%">
                                <div class="absolute top-0 bottom-0 w-0.5 bg-crimson shadow-[0_0_6px_hsl(0_78%_64%/0.6)] -translate-x-1/2"></div>
                                <div class="absolute -top-0.5 left-1/2 -translate-x-1/2 w-2 h-2 bg-crimson rounded-full shadow-[0_0_6px_hsl(0_78%_64%/0.5)]"></div>
                            </div>
                        {/if}
                    </div>

                    <!-- Hover tooltip -->
                    {#if hoverTime}
                        <div class="absolute -top-1 pointer-events-none z-40 -translate-x-1/2 transition-all"
                            style:left="{hoverX + 16}px"
                        >
                            <div class="px-2 py-1 rounded-md bg-black/80 border border-white/10 backdrop-blur-sm">
                                <span class="text-[10px] font-mono font-bold text-white tabular-nums">{formatTimeHMS(hoverTime)}</span>
                            </div>
                        </div>
                    {/if}

                    <!-- Bottom hour labels (tick marks) -->
                    <div class="relative w-full h-2 mt-1">
                        {#each hourMarkers as marker}
                            {#if marker.hour < 24}
                                <div class="absolute top-0 w-px h-1.5 -translate-x-1/2 {marker.hour % 6 !== 0 ? 'bg-border/60' : 'bg-border-strong'}"
                                    style:left="{marker.pct}%"></div>
                            {/if}
                        {/each}
                    </div>
                </div>
            </div>
        </div>

        <!-- Events side panel removed to favor timeline scrubbing (Frigate style) -->
    </div>

    <!-- ═══════════════════ EXPORT DRAWER ═══════════════════ -->
    {#if showExportPanel}
        <div class="border-t border-border bg-card shrink-0" transition:slide={{ duration: 200 }}>
            <div class="px-5 py-4 flex flex-col gap-4">
                <div class="flex items-center justify-between">
                    <div class="flex items-center gap-2.5">
                        <div class="w-7 h-7 rounded-lg bg-iris/10 border border-iris/20 flex items-center justify-center text-iris">
                            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                                <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="17 8 12 3 7 8"/><line x1="12" y1="3" x2="12" y2="15"/>
                            </svg>
                        </div>
                        <div>
                            <h2 class="text-sm font-display font-semibold text-foreground">Slice & Export</h2>
                            <p class="text-[10px] text-muted-foreground font-mono">FFmpeg · Zero-reencode · Lossless stream copy</p>
                        </div>
                    </div>
                    <button onclick={() => showExportPanel = false} class="btn-icon !w-7 !h-7" aria-label="Close export panel">
                        <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><path d="M18 6L6 18M6 6l12 12"/></svg>
                    </button>
                </div>

                <div class="flex flex-wrap items-end gap-4">
                    <div class="flex flex-col gap-1.5 min-w-[200px]">
                        <label for="export-start" class="section-eyebrow">Start Time</label>
                        <input id="export-start" type="datetime-local" bind:value={exportStartTime} class="input font-mono text-xs !h-8" />
                    </div>
                    <div class="flex flex-col gap-1.5 min-w-[200px]">
                        <label for="export-end" class="section-eyebrow">End Time</label>
                        <input id="export-end" type="datetime-local" bind:value={exportEndTime} class="input font-mono text-xs !h-8" />
                    </div>
                    <button
                        onclick={triggerCustomExport}
                        disabled={isExporting || !exportStartTime || !exportEndTime}
                        class="btn btn-iris btn-sm disabled:opacity-50"
                    >
                        {#if isExporting}
                            <span class="w-3 h-3 rounded-full border-2 border-current border-t-transparent animate-spin"></span>
                            Preparing…
                        {:else}
                            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4M7 10l5 5 5-5M12 15V3"/></svg>
                            Start Export
                        {/if}
                    </button>

                    <!-- Export queue inline -->
                    {#if activeExports.length > 0}
                        <div class="flex items-center gap-2 ml-auto">
                            {#each activeExports.slice(0, 3) as job (job.id)}
                                <div class="flex items-center gap-2 px-3 py-1.5 rounded-lg border border-border bg-surface-2/50">
                                    <span class="w-1.5 h-1.5 rounded-full {job.status === 'processing' ? 'status-pulse' : ''}"
                                        class:bg-jade={job.status === 'completed'}
                                        class:bg-crimson={job.status === 'failed'}
                                        class:bg-iris={job.status === 'processing'}
                                        class:bg-muted-foreground={job.status === 'pending'}></span>
                                    <span class="text-[10px] font-mono text-muted-foreground">{job.id.slice(0, 6)}</span>
                                    <span class="text-[10px] font-mono font-bold text-foreground uppercase">{job.status}</span>
                                    {#if job.status === 'completed'}
                                        <button onclick={() => downloadExport(job.id)} class="text-[10px] font-bold text-jade hover:text-jade/80 cursor-pointer border-none bg-transparent p-0 m-0 leading-none">↓</button>
                                    {/if}
                                </div>
                            {/each}
                        </div>
                    {/if}
                </div>
            </div>
        </div>
    {/if}
</div>

<style>
    /* Custom control button */
    .review-ctrl-btn {
        display: inline-flex;
        align-items: center;
        justify-content: center;
        width: 32px;
        height: 32px;
        border-radius: 6px;
        color: rgba(255, 255, 255, 0.8);
        transition: all 0.15s;
        cursor: pointer;
        border: none;
        background: transparent;
    }
    .review-ctrl-btn:hover {
        color: white;
        background: rgba(255, 255, 255, 0.1);
    }

    /* Timeline cursor grab */
    :global([role="slider"]) {
        cursor: crosshair;
    }
    :global([role="slider"]:active) {
        cursor: grabbing;
    }

    /* Volume slider styling */
    input[type="range"]::-webkit-slider-thumb {
        -webkit-appearance: none;
        width: 10px;
        height: 10px;
        border-radius: 50%;
        background: white;
        cursor: pointer;
    }
    input[type="range"]::-moz-range-thumb {
        width: 10px;
        height: 10px;
        border-radius: 50%;
        background: white;
        border: none;
        cursor: pointer;
    }

    /* Fullscreen fixes */
    :global(#review-player-wrapper:fullscreen) {
        background: black;
    }
    :global(#review-player-wrapper:fullscreen video) {
        max-height: 100vh;
    }
</style>
