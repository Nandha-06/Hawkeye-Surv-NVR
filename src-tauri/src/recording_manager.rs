#![allow(dead_code)]

use rusqlite::Connection;
use serde_json::Value;
use std::collections::{HashMap, VecDeque};
use std::path::PathBuf;
use std::sync::Arc;
use std::time::Instant;
use tokio::process::{Child, Command};
use tokio::sync::RwLock;

#[derive(Clone)]
pub struct BufferedSegment {
    pub filename: String,
    pub filepath: std::path::PathBuf,
    pub start_time: chrono::DateTime<chrono::Utc>,
    pub end_time: chrono::DateTime<chrono::Utc>,
}

pub struct ActiveRecorder {
    pub camera_id: String,
    pub child: Option<Child>,
    pub scan_task: Option<tokio::task::JoinHandle<()>>,
}

#[derive(Clone)]
pub struct RecordingManager {
    // Maps camera_id -> Active recorder
    pub active_recorders: Arc<RwLock<HashMap<String, ActiveRecorder>>>,
    // Maps camera_id -> VecDeque of RAM buffered segments
    pub ram_buffers: Arc<RwLock<HashMap<String, VecDeque<BufferedSegment>>>>,
    // Maps camera_id -> last event detection time and its type ("motion" or "vlm")
    pub active_events: Arc<RwLock<HashMap<String, (Instant, String)>>>,
    pub root_dir: PathBuf,
}

impl RecordingManager {
    pub fn new() -> Self {
        let mut root_dir = PathBuf::from(".");
        if !root_dir.join("skills.json").exists() {
            let parent = PathBuf::from("..");
            if parent.join("skills.json").exists() {
                root_dir = parent;
            }
        }
        // Canonicalize to avoid CWD-relative issues
        let root_dir = std::fs::canonicalize(&root_dir).unwrap_or(root_dir);

        Self {
            active_recorders: Arc::new(RwLock::new(HashMap::new())),
            ram_buffers: Arc::new(RwLock::new(HashMap::new())),
            active_events: Arc::new(RwLock::new(HashMap::new())),
            root_dir,
        }
    }

    /// Open connection to the SQLite database
    fn open_db(&self) -> Result<Connection, rusqlite::Error> {
        let db_path = self.root_dir.join(".data").join("hawkeye.db");
        // Ensure data dir exists
        if let Some(parent) = db_path.parent() {
            let _ = std::fs::create_dir_all(parent);
        }
        let conn = Connection::open(&db_path)?;
        conn.busy_timeout(std::time::Duration::from_secs(5))?;
        // Attempt WAL checkpoint to prevent corruption from unclean shutdowns
        let _ = conn.execute_batch("PRAGMA wal_checkpoint(PASSIVE);");
        Ok(conn)
    }

    /// Read camera config list from cameras.json
    async fn load_cameras(&self) -> Result<Vec<Value>, Box<dyn std::error::Error + Send + Sync>> {
        let path = self.root_dir.join(".data").join("cameras.json");
        if !path.exists() {
            return Ok(vec![]);
        }
        let content = tokio::fs::read_to_string(path).await?;
        let json: Value = serde_json::from_str(&content).unwrap_or(serde_json::json!([]));
        Ok(json.as_array().cloned().unwrap_or_default())
    }

    /// Start background workers for tiered retention and scavenger
    pub async fn start_workers(self) {
        let this = Arc::new(self);

        // Storage scavenger and Smart retention worker loop
        tokio::spawn(async move {
            let mut interval = tokio::time::interval(tokio::time::Duration::from_secs(30));
            loop {
                interval.tick().await;

                // 1. Scavenger (checks if disk hits 95%)
                if let Err(e) = this.run_storage_scavenger().await {
                    eprintln!("[StorageScavenger] Error running scavenger: {:?}", e);
                }

                // 2. Retention (runs check for tiered periods)
                if let Err(e) = this.run_tiered_retention().await {
                    eprintln!("[SmartRetention] Error running tiered retention: {:?}", e);
                }
            }
        });
    }

    /// Trigger an event for a camera, shifting it to active state and flushing pre-roll RAM buffer
    pub async fn trigger_event(&self, camera_id: &str, event_type: &str) {
        // Update active event timestamp and type
        {
            let mut events = self.active_events.write().await;
            events.insert(
                camera_id.to_string(),
                (Instant::now(), event_type.to_string()),
            );
        }

        // Flush RAM buffered segments immediately to disk
        self.flush_ram_buffer(camera_id).await;
    }

    /// Flush all segments currently in the RAM buffer for this camera to the permanent disk and index them
    pub async fn flush_ram_buffer(&self, camera_id: &str) {
        if !crate::server::path_safe::is_safe_component(camera_id) {
            log::warn!("[RecordingManager] rejected unsafe camera id: {}", camera_id);
            return;
        }

        let segments: Vec<BufferedSegment> = {
            let mut buffers = self.ram_buffers.write().await;
            if let Some(queue) = buffers.get_mut(camera_id) {
                queue.drain(..).collect()
            } else {
                vec![]
            }
        };

        if segments.is_empty() {
            return;
        }

        let event_type = {
            let events = self.active_events.read().await;
            events
                .get(camera_id)
                .map(|(_, t)| t.clone())
                .unwrap_or_else(|| "motion".to_string())
        };

        println!(
            "[RecordingManager] Flushing {} RAM buffered segments for camera '{}' (type: {})",
            segments.len(),
            camera_id,
            event_type
        );

        let mut written_segments = Vec::new();

        for seg in segments {
            let recordings_dir = self
                .root_dir
                .join(".data")
                .join("recordings")
                .join(camera_id);
            if let Err(e) = tokio::fs::create_dir_all(&recordings_dir).await {
                eprintln!("[RecordingManager] Failed to create recordings dir: {}", e);
                continue;
            }
            let dest_path = recordings_dir.join(&seg.filename);
            let dest_path_str = dest_path.to_string_lossy().to_string();

            // Only move the file if it still exists (prevents race with scan task)
            if !seg.filepath.exists() {
                println!(
                    "[RecordingManager] Segment already moved, skipping: {}",
                    seg.filename
                );
                continue;
            }

            if let Err(e) = tokio::fs::rename(&seg.filepath, &dest_path).await {
                eprintln!(
                    "[RecordingManager] Failed to rename RAM buffered segment: {}",
                    e
                );
                let mut buffers = self.ram_buffers.write().await;
                if let Some(queue) = buffers.get_mut(camera_id) {
                    queue.push_front(seg);
                }
                continue;
            }

            // Extract low-resolution preview thumbnail
            self.extract_thumbnail(&dest_path);

            written_segments.push((seg, dest_path_str));
        }

        if !written_segments.is_empty() {
            if let Ok(conn) = self.open_db() {
                for (seg, dest_path_str) in written_segments {
                    let id = generate_random_id();
                    if let Err(e) = conn.execute(
                        "INSERT OR IGNORE INTO recordings (id, camera_id, start_time, end_time, filepath, type) VALUES (?1, ?2, ?3, ?4, ?5, ?6)",
                        rusqlite::params![
                            id,
                            camera_id,
                            seg.start_time.to_rfc3339(),
                            seg.end_time.to_rfc3339(),
                            dest_path_str,
                            event_type
                        ]
                    ) {
                            eprintln!("[RecordingManager] Failed to index pre-roll segment in DB: {}", e);
                        } else {
                            println!(
                                "[RecordingManager] Flushed and indexed pre-roll segment: {}",
                                seg.filename
                            );
                        }
                }
            }
        }
    }

    /// Insert an event record into the events table
    pub fn insert_event(
        &self,
        camera_id: &str,
        label: &str,
        confidence: f64,
        snapshot_path: Option<&str>,
        severity: &str,
    ) -> Result<(), rusqlite::Error> {
        let conn = self.open_db()?;
        let id = generate_random_id();
        let timestamp = chrono::Utc::now().to_rfc3339();

        conn.execute(
            "INSERT INTO events (id, camera_id, label, confidence, timestamp, snapshot_path, severity) VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7)",
            rusqlite::params![id, camera_id, label, confidence, timestamp, snapshot_path, severity]
        )?;
        Ok(())
    }

    /// Delete continuous recordings if storage usage hits 95%
    async fn run_storage_scavenger(&self) -> Result<(), Box<dyn std::error::Error + Send + Sync>> {
        use sysinfo::Disks;
        let disks = Disks::new_with_refreshed_list();

        let mut total_storage = 0u64;
        let mut available_storage = 0u64;
        for disk in &disks {
            total_storage += disk.total_space();
            available_storage += disk.available_space();
        }

        if total_storage == 0 {
            return Ok(());
        }

        let used_percent =
            ((total_storage - available_storage) as f64 / total_storage as f64) * 100.0;

        if used_percent >= 95.0 {
            println!("[StorageScavenger] Disk capacity at {:.1}%. Scavenging oldest continuous segments...", used_percent);

            let mut disks_check = Disks::new_with_refreshed_list();
            let mut loop_count = 0;
            // Open a single DB connection for the entire scavenger cycle
            let mut conn = self.open_db()?;
            // Loop deletion in chunks of 5 files until storage is below 90% or no continuous segments remain
            loop {
                loop_count += 1;
                if loop_count > 100 {
                    println!("[StorageScavenger] Max iterations reached (100). Aborting scavenger to prevent infinite loop.");
                    break;
                }
                
                disks_check.refresh_list();
                let mut avail_check = 0u64;
                for d in &disks_check {
                    avail_check += d.available_space();
                }

                let current_used_pct =
                    if total_storage > avail_check {
                        ((total_storage - avail_check) as f64 / total_storage as f64) * 100.0
                    } else {
                        0.0
                    };
                if current_used_pct < 90.0 {
                    println!(
                        "[StorageScavenger] Space recovered. Disk usage now at {:.1}%",
                        current_used_pct
                    );
                    break;
                }

                let to_delete: Vec<(String, String)> = {
                    let mut stmt = conn.prepare(
                        "SELECT id, filepath FROM recordings WHERE type = 'continuous' ORDER BY start_time ASC LIMIT 5"
                    )?;
                    let rows = stmt.query_map([], |row| {
                        Ok((row.get::<_, String>(0)?, row.get::<_, String>(1)?))
                    })?;
                    rows.flatten().collect()
                };

                if to_delete.is_empty() {
                    println!("[StorageScavenger] Scavenging complete: No more continuous segments available to purge.");
                    break;
                }

                for (_, filepath) in &to_delete {
                    let path = PathBuf::from(filepath);
                    if path.exists() {
                        let _ = tokio::fs::remove_file(&path).await;
                    }
                }

                {
                    let tx = conn.transaction()?;
                    for (id, filepath) in to_delete {
                        if let Err(e) = tx.execute(
                            "DELETE FROM recordings WHERE id = ?1",
                            rusqlite::params![id],
                        ) {
                            eprintln!("[StorageScavenger] Failed to delete DB record for {}: {}", filepath, e);
                        } else {
                            println!("[StorageScavenger] Deleted segment from disk: {}", filepath);
                        }
                    }
                    tx.commit()?;
                }
            }
        }

        Ok(())
    }

    /// Implement Smart Tiered Retention
    /// Rule 1: Keep 24/7 continuous footage for 3 days
    /// Rule 2: Keep standard motion recordings for 7 days
    /// Rule 3: Keep VLM event recordings for 60 days
    async fn run_tiered_retention(&self) -> Result<(), Box<dyn std::error::Error + Send + Sync>> {
        let now = chrono::Utc::now();
        let three_days_ago = (now - chrono::Duration::days(3)).to_rfc3339();
        let seven_days_ago = (now - chrono::Duration::days(7)).to_rfc3339();
        let sixty_days_ago = (now - chrono::Duration::days(60)).to_rfc3339();

        self.delete_expired_recordings("continuous", &three_days_ago)
            .await?;
        self.delete_expired_recordings("motion", &seven_days_ago)
            .await?;
        self.delete_expired_recordings("vlm", &sixty_days_ago)
            .await?;

        Ok(())
    }

    async fn delete_expired_recordings(
        &self,
        rec_type: &str,
        cutoff_iso: &str,
    ) -> Result<(), Box<dyn std::error::Error + Send + Sync>> {
        let to_delete: Vec<(String, String)> = {
            let conn = self.open_db()?;
            let mut stmt = conn
                .prepare("SELECT id, filepath FROM recordings WHERE type = ?1 AND end_time < ?2")?;
            let rows = stmt.query_map(rusqlite::params![rec_type, cutoff_iso], |row| {
                Ok((row.get::<_, String>(0)?, row.get::<_, String>(1)?))
            })?;
            rows.flatten().collect()
        };

        if to_delete.is_empty() {
            return Ok(());
        }

        for (_, filepath) in &to_delete {
            let path = PathBuf::from(filepath);
            if path.exists() {
                let _ = tokio::fs::remove_file(&path).await;
            }
        }

        {
            let conn = self.open_db()?;
            for (id, filepath) in to_delete {
                if let Err(e) = conn.execute(
                    "DELETE FROM recordings WHERE id = ?1",
                    rusqlite::params![id],
                ) {
                    eprintln!("[SmartRetention] Failed to delete expired DB record for {}: {}", filepath, e);
                } else {
                    println!(
                        "[SmartRetention] Expired {} segment purged: {}",
                        rec_type, filepath
                    );
                }
            }
        }

        Ok(())
    }

    /// Extract a low-resolution thumbnail from a saved segment using FFmpeg
    fn extract_thumbnail(&self, mp4_path: &std::path::PathBuf) {
        let mp4_clone = mp4_path.clone();
        tokio::spawn(async move {
            let thumb_path = mp4_clone.with_extension("jpg");
            println!(
                "[RecordingManager] Extracting thumbnail for: {:?}",
                mp4_clone
            );

            // Optimized key-frame-only extraction: seek instantly to start, skip non-keyframes, and exit
            let mut cmd = Command::new("ffmpeg");
            cmd.kill_on_drop(true);
            cmd.arg("-y")
                .arg("-skip_frame")
                .arg("nokey")
                .arg("-i")
                .arg(&mp4_clone)
                .arg("-vframes")
                .arg("1")
                .arg("-vf")
                .arg("scale=160:90")
                .arg(&thumb_path)
                .stdout(std::process::Stdio::null())
                .stderr(std::process::Stdio::null());

            // Set low priority class on Windows to avoid CPU starvation of ingestion thread
            #[cfg(windows)]
            {
                #[allow(unused_imports)]
                use std::os::windows::process::CommandExt;
                // BELOW_NORMAL_PRIORITY_CLASS = 0x00004000
                cmd.creation_flags(0x00004000);
            }

            let cmd_res = cmd.spawn();

            match cmd_res {
                Ok(mut child) => {
                    let _ = child.wait().await;
                }
                Err(e) => {
                    eprintln!(
                        "[RecordingManager] Failed to spawn FFmpeg for thumbnail: {}",
                        e
                    );
                }
            }
        });
    }

    /// Start continuous recording for a camera (RAM-buffered)
    pub async fn start_recorder(
        &self,
        camera_id: &str,
    ) -> Result<(), Box<dyn std::error::Error + Send + Sync>> {
        if !crate::server::path_safe::is_safe_component(camera_id) {
            return Err(format!("Invalid camera id '{}'", camera_id).into());
        }

        let cameras = self.load_cameras().await?;
        let camera = cameras
            .iter()
            .find(|c| c["id"].as_str() == Some(camera_id))
            .ok_or(format!("Camera '{}' not found in configuration", camera_id))?;

        if camera["enabled"].as_bool() != Some(true) {
            return Err("Camera is disabled".into());
        }

        // Atomically reserve the slot. Hold the write lock for the entire
        // check-and-insert to prevent two concurrent calls from spawning
        // duplicate ffmpeg processes.
        {
            let mut map = self.active_recorders.write().await;
            if map.contains_key(camera_id) {
                println!(
                    "Continuous recorder already running for camera {}",
                    camera_id
                );
                return Ok(());
            }
            // Insert a placeholder so any concurrent caller sees the
            // reservation. We'll overwrite with the real recorder after spawn.
            map.insert(
                camera_id.to_string(),
                ActiveRecorder {
                    camera_id: camera_id.to_string(),
                    child: None,
                    scan_task: None,
                },
            );
        }

        let source = camera["source"].as_str().unwrap_or("rtsp");
        let url_opt = camera["url"].as_str().or(camera["detect_url"].as_str());

        if source == "webcam" && url_opt.is_none() {
            // No URL provided for webcam — spawn a local FFmpeg capture using
            // the platform-native input device (dshow on Windows, v4l2 on Linux,
            // avfoundation on macOS) with device index 0.
            let input_format = if std::env::consts::OS == "windows" {
                "dshow"
            } else if std::env::consts::OS == "linux" {
                "v4l2"
            } else {
                "avfoundation"
            };
            let webcam_device = if std::env::consts::OS == "windows" {
                // dshow device string; index 0 opens the first available webcam.
                // The camera config may optionally carry a "webcam_index" integer.
                let idx = camera
                    .get("webcam_index")
                    .and_then(|v| v.as_u64())
                    .unwrap_or(0);
                format!("video={}", idx)
            } else if std::env::consts::OS == "linux" {
                let idx = camera
                    .get("webcam_index")
                    .and_then(|v| v.as_u64())
                    .unwrap_or(0);
                format!("/dev/video{}", idx)
            } else {
                // avfoundation: use device index as string
                let idx = camera
                    .get("webcam_index")
                    .and_then(|v| v.as_u64())
                    .unwrap_or(0);
                format!("{}", idx)
            };
            // Override url_opt so the rest of the FFmpeg path is reused below.
            // We do this by constructing local variables and jumping past the
            // "webcam with no url" early-return.
            println!(
                "[RecordingManager] Webcam '{}' has no URL — using local FFmpeg {} capture (device: {})",
                camera_id, input_format, webcam_device
            );
            // Create RAM buffer directory
            let ram_buffer_dir = self
                .root_dir
                .join(".temp")
                .join("ram_buffer")
                .join(camera_id);
            if ram_buffer_dir.exists() {
                if let Ok(entries) = std::fs::read_dir(&ram_buffer_dir) {
                    for entry in entries.flatten() {
                        let name = entry.file_name();
                        let name_str = name.to_string_lossy();
                        if name_str.starts_with("segment_") && name_str.ends_with(".mp4") {
                            let _ = std::fs::remove_file(entry.path());
                        }
                    }
                }
            }
            tokio::fs::create_dir_all(&ram_buffer_dir).await?;

            let output_pattern = ram_buffer_dir.join("segment_%Y-%m-%dT%H-%M-%S.mp4");
            let args = vec![
                "-hide_banner".to_string(),
                "-loglevel".to_string(),
                "warning".to_string(),
                "-f".to_string(),
                input_format.to_string(),
                "-i".to_string(),
                webcam_device,
                "-map".to_string(),
                "0:v:0".to_string(),
                "-c:v".to_string(),
                "libx264".to_string(),
                "-preset".to_string(),
                "ultrafast".to_string(),
                "-tune".to_string(),
                "zerolatency".to_string(),
                "-f".to_string(),
                "segment".to_string(),
                "-segment_time".to_string(),
                "5".to_string(),
                "-reset_timestamps".to_string(),
                "1".to_string(),
                "-strftime".to_string(),
                "1".to_string(),
                "-segment_format".to_string(),
                "mp4".to_string(),
                "-movflags".to_string(),
                "empty_moov+frag_keyframe+default_base_moof".to_string(),
                output_pattern.to_string_lossy().to_string(),
            ];

            println!(
                "[RecordingManager] Launching local FFmpeg webcam recorder for {}: ffmpeg {:?}",
                camera_id, args
            );

            let child = match Command::new("ffmpeg")
                .kill_on_drop(true)
                .args(&args)
                .stdout(std::process::Stdio::null())
                .stderr(std::process::Stdio::null())
                .spawn()
            {
                Ok(c) => c,
                Err(e) => {
                    self.active_recorders.write().await.remove(camera_id);
                    return Err(Box::new(e));
                }
            };

            // Reuse the same scan task path as RTSP/URL sources.
            let camera_id_str = camera_id.to_string();
            let ram_buffer_dir_clone = ram_buffer_dir.clone();
            let root_dir_clone = self.root_dir.clone();
            let ram_buffers_clone = self.ram_buffers.clone();
            let active_events_clone = self.active_events.clone();
            let this_clone = self.clone();
            let scan_task = spawn_scan_task(
                camera_id_str,
                ram_buffer_dir_clone,
                root_dir_clone,
                ram_buffers_clone,
                active_events_clone,
                this_clone,
            );

            let recorder = ActiveRecorder {
                camera_id: camera_id.to_string(),
                child: Some(child),
                scan_task: Some(scan_task),
            };
            self.active_recorders
                .write()
                .await
                .insert(camera_id.to_string(), recorder);
            println!(
                "[RecordingManager] Webcam '{}' recorder started (local FFmpeg capture)",
                camera_id
            );
            return Ok(());
        }

        let url = url_opt.ok_or("Camera streaming URL is missing")?;

        // Create temporary RAM buffer directory for segment dump
        let ram_buffer_dir = self
            .root_dir
            .join(".temp")
            .join("ram_buffer")
            .join(camera_id);
        
        // Clean up orphaned segments from previous crash
        if ram_buffer_dir.exists() {
            if let Ok(entries) = std::fs::read_dir(&ram_buffer_dir) {
                for entry in entries.flatten() {
                    let name = entry.file_name();
                    let name_str = name.to_string_lossy();
                    if name_str.starts_with("segment_") && name_str.ends_with(".mp4") {
                        let _ = std::fs::remove_file(entry.path());
                        println!(
                            "[RecordingManager] Cleaned up orphaned RAM segment: {}",
                            name_str
                        );
                    }
                }
            }
        }
        
        tokio::fs::create_dir_all(&ram_buffer_dir).await?;

        // 1. Build FFmpeg command args to write to RAM buffer with low latency options
        let input_args = if source == "rtsp" {
            vec![
                "-rtsp_transport".to_string(),
                "tcp".to_string(),
                "-fflags".to_string(),
                "nobuffer".to_string(),
                "-flags".to_string(),
                "low_delay".to_string(),
                "-max_delay".to_string(),
                "500000".to_string(),
                "-i".to_string(),
                url.to_string(),
            ]
        } else {
            let format = if std::env::consts::OS == "windows" {
                "dshow"
            } else if std::env::consts::OS == "linux" {
                "v4l2"
            } else if std::env::consts::OS == "macos" {
                "avfoundation"
            } else {
                "avfoundation"
            };
            vec![
                "-f".to_string(),
                format.to_string(),
                "-i".to_string(),
                url.to_string(),
            ]
        };

        let output_pattern = ram_buffer_dir.join("segment_%Y-%m-%dT%H-%M-%S.mp4");

        let mut args = vec![
            "-hide_banner".to_string(),
            "-loglevel".to_string(),
            "warning".to_string(),
        ];
        args.extend(input_args);

        let (c_v, extra_args) = if source == "rtsp" {
            ("copy".to_string(), vec![])
        } else {
            (
                "libx264".to_string(),
                vec![
                    "-preset".to_string(),
                    "ultrafast".to_string(),
                    "-tune".to_string(),
                    "zerolatency".to_string(),
                ],
            )
        };

        // Support video copy and optional audio copy (will not fail if audio stream is missing due to ?)
        args.extend(vec![
            "-map".to_string(),
            "0:v:0".to_string(),
            "-map".to_string(),
            "0:a?".to_string(),
            "-c:v".to_string(),
            c_v,
            "-c:a".to_string(),
            "copy".to_string(),
        ]);
        args.extend(extra_args);
        args.extend(vec![
            "-f".to_string(),
            "segment".to_string(),
            "-segment_time".to_string(),
            "5".to_string(),
            "-reset_timestamps".to_string(),
            "1".to_string(),
            "-strftime".to_string(),
            "1".to_string(),
            "-segment_format".to_string(),
            "mp4".to_string(),
            "-movflags".to_string(),
            "empty_moov+frag_keyframe+default_base_moof".to_string(),
            output_pattern.to_string_lossy().to_string(),
        ]);

        println!(
            "[RecordingManager] Launching FFmpeg for camera {}: ffmpeg {:?}",
            camera_id, args
        );

        // 2. Spawn the FFmpeg process. If spawn fails, release the slot.
        let child = match Command::new("ffmpeg")
            .kill_on_drop(true)
            .args(&args)
            .stdout(std::process::Stdio::null())
            .stderr(std::process::Stdio::null())
            .spawn()
        {
            Ok(c) => c,
            Err(e) => {
                // Release the placeholder so retries are not blocked.
                self.active_recorders.write().await.remove(camera_id);
                return Err(Box::new(e));
            }
        };

        // 3. Start directory scanner background task to monitor segment creation
        let camera_id_str = camera_id.to_string();
        let ram_buffer_dir_clone = ram_buffer_dir.clone();
        let root_dir_clone = self.root_dir.clone();
        let ram_buffers_clone = self.ram_buffers.clone();
        let active_events_clone = self.active_events.clone();
        let this_clone = self.clone();

        let scan_task = spawn_scan_task(
            camera_id_str,
            ram_buffer_dir_clone,
            root_dir_clone,
            ram_buffers_clone,
            active_events_clone,
            this_clone,
        );

        // 4. Save active recorder
        let recorder = ActiveRecorder {
            camera_id: camera_id.to_string(),
            child: Some(child),
            scan_task: Some(scan_task),
        };

        self.active_recorders
            .write()
            .await
            .insert(camera_id.to_string(), recorder);
        Ok(())
    }

    /// Stop continuous recording for a camera
    pub async fn stop_recorder(&self, camera_id: &str) {
        let recorder = {
            let mut map = self.active_recorders.write().await;
            map.remove(camera_id)
        };

        if let Some(active) = recorder {
            println!(
                "[RecordingManager] Stopping continuous recorder for camera {}",
                camera_id
            );
            
            // Flush any existing segments in the RAM buffer to disk before shutting down
            self.flush_ram_buffer(camera_id).await;

            if let Some(task) = active.scan_task {
                task.abort();
            }
            if let Some(mut child) = active.child {
                let _ = child.kill().await;
            }
        }
    }

    /// Start recorders for all enabled cameras
    pub async fn start_all(&self) {
        if let Ok(cameras) = self.load_cameras().await {
            for camera in cameras {
                if camera["enabled"].as_bool() == Some(true) {
                    if let Some(id) = camera["id"].as_str() {
                        let _ = self.start_recorder(id).await;
                    }
                }
            }
        }
    }

    /// Stop all running recorders
    pub async fn stop_all(&self) {
        let mut keys = vec![];
        {
            let map = self.active_recorders.read().await;
            for key in map.keys() {
                keys.push(key.clone());
            }
        }

        for key in keys {
            self.stop_recorder(&key).await;
        }
    }
}

// ── spawn_scan_task ──────────────────────────────────────────────────────────
/// Spawn the background directory-watcher task that moves FFmpeg segments to
/// permanent storage.
///
/// **Continuous recording model (Issue 3 fix)**
/// Every 5-second segment written by FFmpeg is moved to the permanent
/// recordings directory *unconditionally* as `type = "continuous"`.  When
/// a detection event is active the type is overridden to the event type
/// ("motion" or "vlm") so tiered retention can apply different rules.
/// Pre-roll buffering is retained for event enrichment but is no longer the
/// sole path to disk — segments always reach permanent storage.
fn spawn_scan_task(
    camera_id_str: String,
    ram_buffer_dir: std::path::PathBuf,
    root_dir: std::path::PathBuf,
    ram_buffers: Arc<RwLock<HashMap<String, VecDeque<BufferedSegment>>>>,
    active_events: Arc<RwLock<HashMap<String, (Instant, String)>>>,
    this: RecordingManager,
) -> tokio::task::JoinHandle<()> {
    tokio::spawn(async move {
        use notify::{RecursiveMode, Watcher};

        let _ = tokio::fs::create_dir_all(&ram_buffer_dir).await;

        let (tx, mut rx) = tokio::sync::mpsc::channel::<notify::Event>(256);
        let mut watcher = match notify::recommended_watcher(
            move |res: Result<notify::Event, notify::Error>| {
                if let Ok(event) = res {
                    let _ = tx.blocking_send(event);
                }
            },
        ) {
            Ok(w) => w,
            Err(e) => {
                eprintln!("[RecordingManager] Failed to create directory watcher: {}", e);
                return;
            }
        };

        if let Err(e) = watcher.watch(&ram_buffer_dir, RecursiveMode::NonRecursive) {
            eprintln!("[RecordingManager] Failed to watch directory: {}", e);
            return;
        }

        println!(
            "[RecordingManager] Started scan task for camera '{}' watching: {:?}",
            camera_id_str, ram_buffer_dir
        );

        while let Some(event) = rx.recv().await {
            for filepath in event.paths {
                let filename = match filepath.file_name() {
                    Some(name) => name.to_string_lossy().to_string(),
                    None => continue,
                };

                if !filename.starts_with("segment_") || !filename.ends_with(".mp4") {
                    continue;
                }

                // Confirm the file is closed (non-empty and not exclusively locked).
                let is_ready = if let Ok(meta) = std::fs::metadata(&filepath) {
                    if meta.len() == 0 {
                        false
                    } else {
                        std::fs::OpenOptions::new()
                            .write(true)
                            .open(&filepath)
                            .is_ok()
                    }
                } else {
                    false
                };

                if !is_ready {
                    continue;
                }

                let meta = match std::fs::metadata(&filepath) {
                    Ok(m) if m.len() > 0 => m,
                    _ => continue,
                };
                let _ = meta; // size already confirmed above

                let start_time = match parse_timestamp_from_filename(&filename) {
                    Some(t) => t,
                    None => continue,
                };
                let end_time = start_time + chrono::Duration::seconds(5);

                // Check if a detection event is active (overrides type tag).
                let event_type = {
                    let events = active_events.read().await;
                    if let Some((last_time, t)) = events.get(&camera_id_str) {
                        if last_time.elapsed() < std::time::Duration::from_secs(10) {
                            t.clone()
                        } else {
                            "continuous".to_string()
                        }
                    } else {
                        "continuous".to_string()
                    }
                };

                // If a detection event is active, also flush the pre-roll buffer
                // so those segments get the event tag instead of "continuous".
                if event_type != "continuous" {
                    this.flush_ram_buffer(&camera_id_str).await;
                }

                if !filepath.exists() {
                    continue;
                }

                // ── Write segment to permanent storage unconditionally ──────
                let recordings_dir = root_dir
                    .join(".data")
                    .join("recordings")
                    .join(&camera_id_str);
                let _ = tokio::fs::create_dir_all(&recordings_dir).await;
                let dest_path = recordings_dir.join(&filename);
                let dest_path_str = dest_path.to_string_lossy().to_string();

                match tokio::fs::rename(&filepath, &dest_path).await {
                    Ok(_) => {
                        // Remove segment from RAM buffer tracking if present
                        {
                            let mut buffers = ram_buffers.write().await;
                            if let Some(queue) = buffers.get_mut(&camera_id_str) {
                                queue.retain(|s| s.filename != filename);
                            }
                        }

                        this.extract_thumbnail(&dest_path);

                        if let Ok(conn) = Connection::open(
                            root_dir.join(".data").join("hawkeye.db"),
                        ) {
                            let id = generate_random_id();
                            if let Err(e) = conn.execute(
                                "INSERT OR IGNORE INTO recordings \
                                 (id, camera_id, start_time, end_time, filepath, type) \
                                 VALUES (?1, ?2, ?3, ?4, ?5, ?6)",
                                rusqlite::params![
                                    id,
                                    camera_id_str,
                                    start_time.to_rfc3339(),
                                    end_time.to_rfc3339(),
                                    dest_path_str,
                                    event_type
                                ],
                            ) {
                                eprintln!(
                                    "[RecordingManager] Failed to index segment in DB: {}",
                                    e
                                );
                            } else {
                                println!(
                                    "[RecordingManager] Saved segment '{}' as type='{}' for camera '{}'",
                                    filename, event_type, camera_id_str
                                );
                            }
                        }
                    }
                    Err(e) => {
                        eprintln!(
                            "[RecordingManager] Failed to move segment '{}' to permanent storage: {}",
                            filename, e
                        );
                    }
                }
            }
        }

        // Hold watcher alive until the task exits.
        let _watcher = watcher;
    })
}

// Helpers
fn parse_timestamp_from_filename(filename: &str) -> Option<chrono::DateTime<chrono::Utc>> {
    // Expected format: segment_YYYY-MM-DDTHH-MM-SS.mp4
    if !filename.starts_with("segment_") || !filename.ends_with(".mp4") {
        return None;
    }

    let clean = filename
        .trim_start_matches("segment_")
        .trim_end_matches(".mp4");
    // format: YYYY-MM-DDTHH-MM-SS
    let parts: Vec<&str> = clean.split('T').collect();
    if parts.len() != 2 {
        return None;
    }

    let date_parts: Vec<&str> = parts[0].split('-').collect();
    let time_parts: Vec<&str> = parts[1].split('-').collect();

    if date_parts.len() != 3 || time_parts.len() != 3 {
        return None;
    }

    let y = date_parts[0].parse::<i32>().ok()?;
    let mo = date_parts[1].parse::<u32>().ok()?;
    let d = date_parts[2].parse::<u32>().ok()?;
    let h = time_parts[0].parse::<u32>().ok()?;
    let mi = time_parts[1].parse::<u32>().ok()?;
    let s = time_parts[2].parse::<u32>().ok()?;

    use chrono::TimeZone;
    chrono::Utc.with_ymd_and_hms(y, mo, d, h, mi, s).single()
}

fn generate_random_id() -> String {
    use rand::RngCore;
    let mut bytes = [0u8; 16];
    rand::rngs::OsRng.fill_bytes(&mut bytes);
    hex::encode(bytes)
}

#[cfg(test)]
mod tests {
    use super::*;
    use chrono::TimeZone;

    #[test]
    fn test_parse_timestamp_from_filename() {
        let filename = "segment_2026-06-08T02-51-06.mp4";
        let parsed = parse_timestamp_from_filename(filename);
        assert!(parsed.is_some(), "Expected Some(DateTime), got None");
        let expected = chrono::Utc.with_ymd_and_hms(2026, 6, 8, 2, 51, 6).single().unwrap();
        assert_eq!(parsed.unwrap(), expected);

        // Test invalid extensions/formats
        assert!(parse_timestamp_from_filename("segment_2026-06-08T02-51-06.ts").is_none());
        assert!(parse_timestamp_from_filename("invalid_filename.mp4").is_none());
        println!("Verification test passed successfully!");
    }
}
