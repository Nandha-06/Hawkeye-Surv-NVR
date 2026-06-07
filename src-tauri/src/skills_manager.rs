use serde_json::Value;
use std::collections::{HashMap, HashSet};
use std::path::PathBuf;
use std::process::Stdio;
use std::sync::Arc;
use tokio::io::{AsyncBufReadExt, AsyncReadExt, AsyncWriteExt, BufReader};
use tokio::process::Command;
use tokio::sync::broadcast;
use tokio::sync::RwLock;

pub struct MotionDetector {
    threshold: u8,
    min_area: usize,
    motion_masks: Vec<Vec<[f64; 2]>>,
    avg_frame: Option<Vec<f32>>,
    width: usize,
    height: usize,
    dw: usize,
    dh: usize,
    gray: Vec<u8>,
    blurred_h: Vec<u8>,
    blurred: Vec<u8>,
    frame_delta: Vec<u8>,
    thresh: Vec<u8>,
    dilated_h: Vec<u8>,
    dilated: Vec<u8>,
    temp_sort: Vec<u8>,
    visited: Vec<bool>,
    stack: Vec<(usize, usize)>,
}

impl MotionDetector {
    pub fn new(
        threshold: u8,
        min_area: usize,
        motion_masks: Vec<Vec<[f64; 2]>>,
        w: usize,
        h: usize,
    ) -> Self {
        let dw = 360;
        let dh = (h * dw) / w;
        Self {
            threshold,
            min_area,
            motion_masks,
            avg_frame: None,
            width: w,
            height: h,
            dw,
            dh,
            gray: vec![0u8; dw * dh],
            blurred_h: vec![0u8; dw * dh],
            blurred: vec![0u8; dw * dh],
            frame_delta: vec![0u8; dw * dh],
            thresh: vec![0u8; dw * dh],
            dilated_h: vec![0u8; dw * dh],
            dilated: vec![0u8; dw * dh],
            temp_sort: vec![0u8; dw * dh],
            visited: vec![false; dw * dh],
            stack: Vec::with_capacity(dw * dh),
        }
    }

    pub fn has_motion(&mut self, frame_bgr: &[u8]) -> bool {
        let dw = self.dw;
        let dh = self.dh;

        for i in 0..(dw * dh) {
            let src_idx = i * 3;
            if src_idx + 2 < frame_bgr.len() {
                let b = frame_bgr[src_idx];
                let g = frame_bgr[src_idx + 1];
                let r = frame_bgr[src_idx + 2];
                self.gray[i] = ((b as u32 * 114 + g as u32 * 587 + r as u32 * 299) / 1000) as u8;
            }
        }

        if !self.motion_masks.is_empty() {
            for dy in 0..dh {
                let py = dy as f64 / dh as f64;
                for dx in 0..dw {
                    let px = dx as f64 / dw as f64;
                    let mut is_masked = false;
                    for poly in &self.motion_masks {
                        if is_inside_polygon((px, py), poly) {
                            is_masked = true;
                            break;
                        }
                    }
                    if is_masked {
                        self.gray[dy * dw + dx] = 0;
                    }
                }
            }
        }

        let radius = 4;
        for y in 0..dh {
            for x in 0..dw {
                let mut sum = 0u32;
                let mut count = 0u32;
                let x_start = if x >= radius { x - radius } else { 0 };
                let x_end = std::cmp::min(x + radius, dw - 1);
                for kx in x_start..=x_end {
                    sum += self.gray[y * dw + kx] as u32;
                    count += 1;
                }
                self.blurred_h[y * dw + x] = (sum / count) as u8;
            }
        }

        for y in 0..dh {
            let y_start = if y >= radius { y - radius } else { 0 };
            let y_end = std::cmp::min(y + radius, dh - 1);
            for x in 0..dw {
                let mut sum = 0u32;
                let mut count = 0u32;
                for ky in y_start..=y_end {
                    sum += self.blurred_h[ky * dw + x] as u32;
                    count += 1;
                }
                self.blurred[y * dw + x] = (sum / count) as u8;
            }
        }

        let alpha = 0.05f32;
        let avg = match &mut self.avg_frame {
            Some(avg) => avg,
            None => {
                let mut first_avg = vec![0f32; dw * dh];
                for i in 0..(dw * dh) {
                    first_avg[i] = self.blurred[i] as f32;
                }
                self.avg_frame = Some(first_avg);
                return false;
            }
        };

        for i in 0..(dw * dh) {
            avg[i] = (1.0 - alpha) * avg[i] + alpha * self.blurred[i] as f32;
            let diff = (self.blurred[i] as f32 - avg[i]).abs();
            self.frame_delta[i] = diff.round() as u8;
        }

        self.temp_sort.copy_from_slice(&self.frame_delta);
        self.temp_sort.sort_unstable();
        let p96 = self.temp_sort[(self.temp_sort.len() * 96) / 100];
        let dynamic_thresh = std::cmp::max(self.threshold, (p96 as f32 * 1.5) as u8);

        for i in 0..(dw * dh) {
            if self.frame_delta[i] > dynamic_thresh {
                self.thresh[i] = 255;
            } else {
                self.thresh[i] = 0;
            }
        }

        let dil_radius = 2;
        for y in 0..dh {
            for x in 0..dw {
                let mut max_val = 0u8;
                let x_start = if x >= dil_radius { x - dil_radius } else { 0 };
                let x_end = std::cmp::min(x + dil_radius, dw - 1);
                for kx in x_start..=x_end {
                    let val = self.thresh[y * dw + kx];
                    if val > max_val {
                        max_val = val;
                    }
                }
                self.dilated_h[y * dw + x] = max_val;
            }
        }
        for y in 0..dh {
            let y_start = if y >= dil_radius { y - dil_radius } else { 0 };
            let y_end = std::cmp::min(y + dil_radius, dh - 1);
            for x in 0..dw {
                let mut max_val = 0u8;
                for ky in y_start..=y_end {
                    let val = self.dilated_h[ky * dw + x];
                    if val > max_val {
                        max_val = val;
                    }
                }
                self.dilated[y * dw + x] = max_val;
            }
        }

        let scale_factor = (dw * dh) as f64 / (self.width * self.height) as f64;
        let scaled_min_area = std::cmp::max(50, (self.min_area as f64 * scale_factor) as usize);

        self.visited.fill(false);
        self.stack.clear();

        for y in 0..dh {
            for x in 0..dw {
                let idx = y * dw + x;
                if self.dilated[idx] == 255 && !self.visited[idx] {
                    let mut area = 0;
                    self.stack.push((x, y));
                    self.visited[idx] = true;

                    while let Some((cx, cy)) = self.stack.pop() {
                        area += 1;

                        let neighbors = [
                            (cx as i32 - 1, cy as i32),
                            (cx as i32 + 1, cy as i32),
                            (cx as i32, cy as i32 - 1),
                            (cx as i32, cy as i32 + 1),
                        ];

                        for &(nx, ny) in &neighbors {
                            if nx >= 0 && nx < dw as i32 && ny >= 0 && ny < dh as i32 {
                                let n_idx = ny as usize * dw + nx as usize;
                                if self.dilated[n_idx] == 255 && !self.visited[n_idx] {
                                    self.visited[n_idx] = true;
                                    self.stack.push((nx as usize, ny as usize));
                                }
                            }
                        }
                    }

                    if area >= scaled_min_area {
                        return true;
                    }
                }
            }
        }

        false
    }
}

fn is_inside_polygon(p: (f64, f64), poly: &[[f64; 2]]) -> bool {
    let mut inside = false;
    let mut j = poly.len() - 1;
    for i in 0..poly.len() {
        if (poly[i][1] > p.1) != (poly[j][1] > p.1)
            && (p.0
                < (poly[j][0] - poly[i][0]) * (p.1 - poly[i][1]) / (poly[j][1] - poly[i][1])
                    + poly[i][0])
        {
            inside = !inside;
        }
        j = i;
    }
    inside
}

pub struct ActiveSkillProcess {
    pub skill_id: String,
    pub stdin: Arc<tokio::sync::Mutex<tokio::process::ChildStdin>>,
    pub status: String,
    pub child: Arc<tokio::sync::Mutex<tokio::process::Child>>,
    pub ffmpeg_child: Option<Arc<tokio::sync::Mutex<tokio::process::Child>>>,
}

/// Validate a snapshot file path that the skill reported in its event payload.
///
/// The skill is responsible for writing the JPEG to disk (we just tell it
/// the target directory via `DEEPCAMERA_SNAPSHOTS_DIR`). This helper does a
/// quick existence check so stale/orphaned rows don't end up pointing at
/// missing files. Returns the path on success.
fn validate_event_snapshot(snapshot_path: Option<&str>) -> Option<String> {
    let path = snapshot_path?.trim();
    if path.is_empty() {
        return None;
    }
    // Cheap magic-byte check: 0xFF 0xD8 0xFF (JPEG SOI)
    let p = std::path::Path::new(path);
    if !p.is_file() {
        return None;
    }
    let header_ok = std::fs::read(p)
        .ok()
        .map(|b| b.len() >= 4 && b[0] == 0xFF && b[1] == 0xD8 && b[2] == 0xFF)
        .unwrap_or(false);
    if !header_ok {
        eprintln!(
            "[SkillsManager] Snapshot at {:?} is not a valid JPEG, ignoring",
            p
        );
        return None;
    }
    Some(path.to_string())
}

pub struct SkillsManager {
    // Maps "${skill_id}:${camera_id}" -> Active process
    pub active_processes: Arc<RwLock<HashMap<String, ActiveSkillProcess>>>,
    // Keys currently being spawned. Prevents two concurrent `start_skill`
    // calls from both passing the `active_processes` contains_key check
    // and creating duplicate python processes.
    pub start_reservations: Arc<RwLock<HashSet<String>>>,
    // Keys currently being deployed. Prevents concurrent deploy_skill calls.
    pub deploy_reservations: Arc<RwLock<HashSet<String>>>,
    pub root_dir: PathBuf,
    pub tx: broadcast::Sender<String>,
    pub recording_manager: crate::recording_manager::RecordingManager,
}

impl SkillsManager {
    pub fn new(
        tx: broadcast::Sender<String>,
        recording_manager: crate::recording_manager::RecordingManager,
    ) -> Self {
        // Resolve workspace root dir (parent of `src-tauri` during dev)
        let mut root_dir = PathBuf::from(".");
        if !root_dir.join("skills.json").exists() {
            let parent = PathBuf::from("..");
            if parent.join("skills.json").exists() {
                root_dir = parent;
            }
        }

        Self {
            active_processes: Arc::new(RwLock::new(HashMap::new())),
            start_reservations: Arc::new(RwLock::new(HashSet::new())),
            deploy_reservations: Arc::new(RwLock::new(HashSet::new())),
            root_dir,
            tx,
            recording_manager,
        }
    }

    /// Scan `skills.json` and verify what skills are registered, enriching with SKILL.md frontmatter parameters
    pub async fn list_skills(&self) -> Result<Value, Box<dyn std::error::Error + Send + Sync>> {
        let path = self.root_dir.join("skills.json");
        if !path.exists() {
            return Err("skills.json not found".into());
        }
        let content = tokio::fs::read_to_string(path).await?;
        let mut json: Value = serde_json::from_str(&content)?;

        if let Some(skills_arr) = json["skills"].as_array_mut() {
            for skill in skills_arr {
                let skill_id = skill["id"].as_str().unwrap_or("").to_string();
                let relative_path = skill["path"].as_str().unwrap_or("").to_string();

                // Set default status/isRunning
                let is_running = {
                    let map = self.active_processes.read().await;
                    map.keys().any(|k| k.starts_with(&format!("{}:", skill_id)))
                };

                skill["isRunning"] = serde_json::json!(is_running);
                skill["status"] = serde_json::json!(if is_running { "ready" } else { "stopped" });

                let is_deploying = {
                    let res = self.deploy_reservations.read().await;
                    res.contains(&skill_id)
                };
                skill["isDeploying"] = serde_json::json!(is_deploying);

                // Check if installed
                let mut is_installed = true;
                if !relative_path.is_empty() {
                    let skill_abs_path = self.root_dir.join(&relative_path);
                    if skill_abs_path.join("package.json").exists() || skill_abs_path.join("requirements.txt").exists() {
                        is_installed = skill_abs_path.join(".deployed").exists();
                    }
                }
                skill["isInstalled"] = serde_json::json!(is_installed);

                // Try to load parameters from SKILL.md
                let mut config_params = serde_json::json!([]);
                if !relative_path.is_empty() {
                    let skill_md_path = self.root_dir.join(relative_path).join("SKILL.md");
                    if skill_md_path.exists() {
                        if let Ok(md_content) = tokio::fs::read_to_string(&skill_md_path).await {
                            if md_content.starts_with("---") {
                                let rest = &md_content[3..];
                                if let Some(idx) = rest.find("---") {
                                    let frontmatter = &rest[..idx];
                                    if let Ok(yaml_val) = serde_yaml::from_str::<Value>(frontmatter)
                                    {
                                        if let Some(params) = yaml_val.get("parameters") {
                                            config_params = params.clone();
                                        }
                                    }
                                }
                            }
                        }
                    }
                }
                skill["configParams"] = config_params;
            }
        }

        Ok(json)
    }

    /// Start the AI process (one subprocess per enabled camera)
    pub async fn start_skill(
        &self,
        skill_id: &str,
        config: Value,
    ) -> Result<(), Box<dyn std::error::Error + Send + Sync>> {
        let skills_list = self.list_skills().await?;
        let skills_arr = skills_list["skills"]
            .as_array()
            .ok_or("Invalid skills catalog schema")?;

        // Find skill definition
        let skill_def = skills_arr
            .iter()
            .find(|s| s["id"].as_str() == Some(skill_id))
            .ok_or(format!("Skill '{}' not found in catalog", skill_id))?;

        let relative_path = skill_def["path"]
            .as_str()
            .ok_or("Missing skill path in definition")?;
        let skill_abs_path = self.root_dir.join(relative_path);

        // Read cameras.json to spawn a process for each enabled camera
        let cameras_path = self.root_dir.join(".data").join("cameras.json");
        let mut cameras = vec![];
        if cameras_path.exists() {
            if let Ok(content) = tokio::fs::read_to_string(&cameras_path).await {
                if let Ok(parsed) = serde_json::from_str::<Value>(&content) {
                    if let Some(arr) = parsed.as_array() {
                        cameras = arr
                            .iter()
                            .filter(|c| c["enabled"].as_bool() == Some(true))
                            .cloned()
                            .collect();
                    }
                }
            }
        }

        // Fallback to local webcam if no cameras are configured/enabled
        if cameras.is_empty() {
            cameras.push(serde_json::json!({
                "id": "webcam_local",
                "name": "Local Webcam",
                "source": "webcam",
                "enabled": true,
                "fps": 5,
                "confidence": 0.8,
                "enable_motion_gating": false
            }));
        }

        let is_windows = cfg!(target_os = "windows");

        // Resolve python executable in the skill's virtual environment
        let python_exec = if is_windows {
            skill_abs_path
                .join(".venv")
                .join("Scripts")
                .join("python.exe")
        } else {
            skill_abs_path.join(".venv").join("bin").join("python")
        };

        let python_cmd = if python_exec.exists() {
            python_exec.to_string_lossy().to_string()
        } else {
            // Fallback to system python
            if is_windows {
                "python".to_string()
            } else {
                "python3".to_string()
            }
        };

        for camera in cameras {
            let camera_id = camera["id"].as_str().unwrap_or("unknown").to_string();
            let camera_name = camera["name"].as_str().unwrap_or("Camera").to_string();
            let active_key = format!("{}:{}", skill_id, camera_id);

            // Atomically reserve the slot. Hold the write lock for the
            // check-and-insert so two concurrent callers can't both pass
            // the contains_key check and spawn duplicate processes.
            //
            // We also check `active_processes` here (under the same write
            // lock we just dropped, so a brief race window is possible,
            // but if a process IS already running we'll see it the next
            // time through and skip). The reservation prevents the
            // double-spawn race.
            {
                let mut reserved = self.start_reservations.write().await;
                let processes = self.active_processes.read().await;
                if processes.contains_key(&active_key) {
                    drop(processes);
                    drop(reserved);
                    println!("Skill process {} is already running.", active_key);
                    continue;
                }
                if !reserved.insert(active_key.clone()) {
                    drop(processes);
                    drop(reserved);
                    println!("Skill process {} is already being started.", active_key);
                    continue;
                }
            }

            println!(
                "[SkillsManager] Spawning process for skill {} on camera: {}",
                skill_id, camera_name
            );

            // Merge configs
            let mut merged_params = config.clone();
            let rtsp_url = camera["detect_url"]
                .as_str()
                .or(camera["url"].as_str())
                .unwrap_or("")
                .to_string();
            let is_rtsp = rtsp_url.starts_with("rtsp://")
                || rtsp_url.starts_with("http://")
                || rtsp_url.starts_with("https://");
            let use_shm = is_rtsp;

            if let Some(obj) = merged_params.as_object_mut() {
                obj.insert("camera_id".to_string(), serde_json::json!(camera_id));
                obj.insert("camera_name".to_string(), serde_json::json!(camera_name));
                obj.insert("source".to_string(), camera["source"].clone());
                obj.insert("confidence".to_string(), camera["confidence"].clone());
                obj.insert("fps".to_string(), camera["fps"].clone());
                obj.insert("url".to_string(), serde_json::json!(rtsp_url.clone()));
                obj.insert("rtsp_url".to_string(), serde_json::json!(rtsp_url.clone()));
                obj.insert("use_shm".to_string(), serde_json::json!(use_shm));
            }

            // Spawn the python process
            let entry_script = skill_def["entry"].as_str().unwrap_or("scripts/detect.py");

            let params_json = match serde_json::to_string(&merged_params) {
                Ok(s) => s,
                Err(e) => {
                    self.start_reservations.write().await.remove(&active_key);
                    return Err(Box::new(e));
                }
            };

            let child = Command::new(&python_cmd)
                .arg(entry_script)
                .current_dir(&skill_abs_path)
                .env("PYTHONUNBUFFERED", "1")
                .env("HAWKEYE_SKILL_PARAMS", params_json)
                .env(
                    "DEEPCAMERA_SNAPSHOTS_DIR",
                    self.root_dir
                        .join(".data")
                        .join("snapshots")
                        .to_string_lossy()
                        .to_string(),
                )
                .stdin(Stdio::piped())
                .stdout(Stdio::piped())
                .stderr(Stdio::piped())
                .spawn();
            let mut child = match child {
                Ok(c) => c,
                Err(e) => {
                    // Release the reservation so a retry can proceed.
                    self.start_reservations.write().await.remove(&active_key);
                    return Err(Box::new(e));
                }
            };

            let raw_stdin = child.stdin.take();
            let raw_stdin = match raw_stdin {
                Some(s) => s,
                None => {
                    let _ = child.kill().await;
                    self.start_reservations.write().await.remove(&active_key);
                    return Err("Failed to open stdin for subprocess".into());
                }
            };
            let stdout = child.stdout.take();
            let stdout = match stdout {
                Some(s) => s,
                None => {
                    let _ = child.kill().await;
                    self.start_reservations.write().await.remove(&active_key);
                    return Err("Failed to open stdout for subprocess".into());
                }
            };
            let stderr = child.stderr.take();
            let stderr = match stderr {
                Some(s) => s,
                None => {
                    let _ = child.kill().await;
                    self.start_reservations.write().await.remove(&active_key);
                    return Err("Failed to open stderr for subprocess".into());
                }
            };
            let stdin = Arc::new(tokio::sync::Mutex::new(raw_stdin));

            // Extract motion gating configurations for the Rust loop
            let enable_motion_gating = camera["enable_motion_gating"].as_bool().unwrap_or(false);
            let motion_threshold = camera["motion_threshold"].as_u64().unwrap_or(15) as u8;
            let min_motion_area = camera["min_motion_area"].as_u64().unwrap_or(500) as usize;

            let mut motion_masks = Vec::new();
            if let Some(masks_val) = camera.get("motion_masks").and_then(|v| v.as_array()) {
                for mask_val in masks_val {
                    if let Some(poly_arr) = mask_val.as_array() {
                        let mut poly = Vec::new();
                        for pt_val in poly_arr {
                            if let Some(pt_arr) = pt_val.as_array() {
                                if pt_arr.len() >= 2 {
                                    let x = pt_arr[0].as_f64().unwrap_or(0.0);
                                    let y = pt_arr[1].as_f64().unwrap_or(0.0);
                                    poly.push([x, y]);
                                }
                            }
                        }
                        if !poly.is_empty() {
                            motion_masks.push(poly);
                        }
                    }
                }
            }

            let mut ffmpeg_child_opt = None;
            // Start the FFmpeg frame copy background loop for motion gating (low-res, 360p at 5fps)
            if use_shm {
                let camera_id_clone = camera_id.clone();
                let rtsp_url_clone = rtsp_url.clone();
                let stdin_clone = stdin.clone();

                let mut ffmpeg_cmd = {
                    let bundled_ffmpeg = self.root_dir.join(".data").join("bin").join("ffmpeg.exe");
                    if bundled_ffmpeg.exists() {
                        Command::new(bundled_ffmpeg)
                    } else {
                        Command::new("ffmpeg")
                    }
                };
                
                if rtsp_url_clone.starts_with("rtsp://") {
                    ffmpeg_cmd.args(&["-rtsp_transport", "tcp"]);
                }
                
                ffmpeg_cmd
                    .args(&[
                        "-fflags",
                        "nobuffer",
                        "-flags",
                        "low_delay",
                        "-max_delay",
                        "500000",
                        "-i",
                        &rtsp_url_clone,
                        "-vf",
                        "scale=360:202,fps=5",
                        "-f",
                        "rawvideo",
                        "-pix_fmt",
                        "bgr24",
                        "-",
                    ])
                    .stdout(Stdio::piped())
                    .stderr(Stdio::null());

                if let Ok(mut ffmpeg_child) = ffmpeg_cmd.spawn() {
                    let mut ffmpeg_stdout = ffmpeg_child.stdout.take().unwrap();
                    let ffmpeg_child_arc = Arc::new(tokio::sync::Mutex::new(ffmpeg_child));
                    ffmpeg_child_opt = Some(ffmpeg_child_arc.clone());

                    tokio::spawn(async move {
                        let dw = 360;
                        let dh = 202;
                        let frame_size = dw * dh * 3;

                        let mut detector = if enable_motion_gating {
                            Some(MotionDetector::new(
                                motion_threshold,
                                min_motion_area,
                                motion_masks,
                                1280,
                                720,
                            ))
                        } else {
                            None
                        };

                        let mut buffer = vec![0u8; frame_size];
                        let mut last_event_sent =
                            std::time::Instant::now() - std::time::Duration::from_secs(5);

                        while let Ok(_) = ffmpeg_stdout.read_exact(&mut buffer).await {
                            let has_motion = match &mut detector {
                                Some(det) => det.has_motion(&buffer),
                                None => true,
                            };

                            if has_motion
                                && last_event_sent.elapsed()
                                    >= std::time::Duration::from_millis(500)
                            {
                                last_event_sent = std::time::Instant::now();

                                // Notify Python via JSON stdin message
                                let notify_event = serde_json::json!({
                                    "event": "motion_detected",
                                    "camera_id": camera_id_clone.clone(),
                                    "timestamp": chrono::Utc::now().timestamp()
                                });

                                if let Ok(msg_str) = serde_json::to_string(&notify_event) {
                                    let mut lock = stdin_clone.lock().await;
                                    let _ =
                                        lock.write_all(format!("{}\n", msg_str).as_bytes()).await;
                                    let _ = lock.flush().await;
                                }
                            }
                        }
                        let mut lock = ffmpeg_child_arc.lock().await;
                        let _ = lock.kill().await;
                    });
                }
            }

            // Track active process
            let active_proc = ActiveSkillProcess {
                skill_id: skill_id.to_string(),
                stdin,
                status: "starting".to_string(),
                child: Arc::new(tokio::sync::Mutex::new(child)),
                ffmpeg_child: ffmpeg_child_opt,
            };

            self.active_processes
                .write()
                .await
                .insert(active_key.clone(), active_proc);
            // Successfully spawned - release the reservation so future
            // stop_skill / re-start flows are unblocked.
            self.start_reservations.write().await.remove(&active_key);

            // Start async read stdout loop
            let tx = self.tx.clone();
            let processes_map = self.active_processes.clone();
            let active_key_clone = active_key.clone();
            let skill_id_str = skill_id.to_string();
            let camera_id_str = camera_id.clone();
            let recording_manager_clone = self.recording_manager.clone();

            // Start threads to read stdout and stderr
            let (tx_out, mut rx_out) = tokio::sync::mpsc::channel::<String>(100);
            
            // Read stdout
            let tx_out_clone1 = tx_out.clone();
            tokio::spawn(async move {
                let mut reader = tokio::io::BufReader::new(stdout).lines();
                while let Ok(Some(line)) = reader.next_line().await {
                    let _ = tx_out_clone1.send(line).await;
                }
            });

            // Read stderr
            let tx_out_clone2 = tx_out.clone();
            tokio::spawn(async move {
                let mut reader = tokio::io::BufReader::new(stderr).lines();
                while let Ok(Some(line)) = reader.next_line().await {
                    let _ = tx_out_clone2.send(format!("ERR: {}", line)).await;
                }
            });

            // Process output lines
            tokio::spawn(async move {
                while let Some(line) = rx_out.recv().await {
                    let trimmed = line.trim();
                    if trimmed.is_empty() {
                        continue;
                    }

                    if let Ok(mut parsed) = serde_json::from_str::<Value>(trimmed) {
                        // Enriched payload for WebSocket broadcast
                        if let Some(obj) = parsed.as_object_mut() {
                            obj.insert("skillId".to_string(), serde_json::json!(skill_id_str));
                            obj.insert("cameraId".to_string(), serde_json::json!(camera_id_str));

                            // Update internal state if ready event received
                            if obj.get("event").and_then(|v| v.as_str()) == Some("ready") {
                                if let Some(proc) =
                                    processes_map.write().await.get_mut(&active_key_clone)
                                {
                                    proc.status = "ready".to_string();
                                }
                            }
                        }

                        // Inspect event to trigger recording and DB events
                        if let Some(evt) = parsed.get("event").and_then(|v| v.as_str()) {
                            match evt {
                                "threat_analysis" => {
                                    let msg = parsed
                                        .get("message")
                                        .and_then(|v| v.as_str())
                                        .unwrap_or("Threat Detected")
                                        .to_string();
                                    let alert_type = parsed
                                        .get("alert_type")
                                        .and_then(|v| v.as_str())
                                        .unwrap_or("warning")
                                        .to_string();
                                    let conf = parsed
                                        .get("confidence")
                                        .and_then(|v| v.as_f64())
                                        .unwrap_or(1.0);

                                    // Trigger VLM recording clip
                                    let rec_mgr = recording_manager_clone.clone();
                                    let cam = camera_id_str.clone();
                                    tokio::spawn(async move {
                                        rec_mgr.trigger_event(&cam, "vlm").await;
                                    });

                                    // Validate skill-reported snapshot path; strip from broadcast
                                    let snapshot_path = parsed
                                        .get("snapshot_path")
                                        .and_then(|v| v.as_str())
                                        .map(|s| s.to_string());
                                    let snap_path = validate_event_snapshot(snapshot_path.as_deref());
                                    if let Some(obj) = parsed.as_object_mut() {
                                        obj.remove("snapshot_path");
                                    }

                                    // Write event to relational DB (SQLite)
                                    let _ = recording_manager_clone.insert_event(
                                        &camera_id_str,
                                        &msg,
                                        conf,
                                        snap_path.as_deref(),
                                        &alert_type,
                                    );
                                }
                                "detections" => {
                                    if let Some(objs) =
                                        parsed.get("objects").and_then(|v| v.as_array())
                                    {
                                        if !objs.is_empty() {
                                            let mut best_label = String::from("Motion Event");
                                            let mut best_conf = 0.0;
                                            for o in objs {
                                                let l = o
                                                    .get("label")
                                                    .and_then(|v| v.as_str())
                                                    .unwrap_or("")
                                                    .to_string();
                                                let c = o
                                                    .get("confidence")
                                                    .and_then(|v| v.as_f64())
                                                    .unwrap_or(0.0);
                                                if c > best_conf {
                                                    best_conf = c;
                                                    best_label = l;
                                                }
                                            }

                                            // Trigger motion recording clip
                                            let rec_mgr = recording_manager_clone.clone();
                                            let cam = camera_id_str.clone();
                                            tokio::spawn(async move {
                                                rec_mgr.trigger_event(&cam, "motion").await;
                                            });

                                            // Validate skill-reported snapshot path; strip from broadcast
                                            let snapshot_path = parsed
                                                .get("snapshot_path")
                                                .and_then(|v| v.as_str())
                                                .map(|s| s.to_string());
                                            let snap_path = validate_event_snapshot(snapshot_path.as_deref());
                                            if let Some(obj) = parsed.as_object_mut() {
                                                obj.remove("snapshot_path");
                                            }

                                            // Write event to relational DB (SQLite)
                                            let _ = recording_manager_clone.insert_event(
                                                &camera_id_str,
                                                &best_label,
                                                best_conf,
                                                snap_path.as_deref(),
                                                "info",
                                            );
                                        }
                                    }
                                }
                                _ => {}
                            }
                        }

                        // Broadcast JSON event to all connected clients
                        if let Ok(event_str) = serde_json::to_string(&parsed) {
                            let _ = tx.send(event_str);
                        }
                    } else {
                        // Forward raw stdout log as event
                        let log_event = serde_json::json!({
                            "event": "log",
                            "skillId": skill_id_str,
                            "cameraId": camera_id_str,
                            "message": trimmed
                        });
                        if let Ok(event_str) = serde_json::to_string(&log_event) {
                            let _ = tx.send(event_str);
                        }
                    }
                }

                // Process exited, clean up
                processes_map.write().await.remove(&active_key_clone);
                println!(
                    "[SkillsManager] Subprocess {} finished execution.",
                    active_key_clone
                );
            });
        }

        Ok(())
    }

    /// Stop running AI process for a specific skill
    pub async fn stop_skill(&self, skill_id: &str) {
        let mut keys_to_stop = vec![];
        {
            let map = self.active_processes.read().await;
            for (key, proc) in map.iter() {
                if proc.skill_id == skill_id {
                    keys_to_stop.push(key.clone());
                }
            }
        }

        for key in keys_to_stop {
            let proc = {
                let mut map = self.active_processes.write().await;
                map.remove(&key)
            };

            if let Some(active) = proc {
                println!("[SkillsManager] Stopping AI process {}", key);
                // Attempt graceful shutdown command
                let stop_cmd = serde_json::json!({ "command": "stop" });
                if let Ok(payload) = serde_json::to_string(&stop_cmd) {
                    let mut payload_nl = payload;
                    payload_nl.push('\n');
                    let mut lock = active.stdin.lock().await;
                    let _ = lock.write_all(payload_nl.as_bytes()).await;
                    let _ = lock.flush().await;
                }

                // Wait a moment, then kill if not exited
                let child_mutex = active.child.clone();
                let ffmpeg_child_mutex = active.ffmpeg_child.clone();
                tokio::spawn(async move {
                    tokio::time::sleep(std::time::Duration::from_millis(500)).await;
                    // Kill python child
                    {
                        let mut lock = child_mutex.lock().await;
                        let _ = lock.kill().await;
                    }
                    // Kill ffmpeg child if present
                    if let Some(ff_child) = ffmpeg_child_mutex {
                        let mut lock = ff_child.lock().await;
                        let _ = lock.kill().await;
                    }
                });
            }
        }
    }

    /// Deploy a skill, executing its deploy script or installing dependencies
    pub async fn deploy_skill(
        &self,
        skill_id: &str,
        tx_ws: tokio::sync::mpsc::Sender<axum::extract::ws::Message>,
    ) -> Result<(), Box<dyn std::error::Error + Send + Sync>> {
        let skills_list = self.list_skills().await?;
        let skills_arr = skills_list["skills"]
            .as_array()
            .ok_or("Invalid skills catalog schema")?;

        let skill_def = skills_arr
            .iter()
            .find(|s| s["id"].as_str() == Some(skill_id))
            .ok_or(format!("Skill '{}' not found in catalog", skill_id))?;

        let relative_path = skill_def["path"]
            .as_str()
            .ok_or("Missing skill path in definition")?;
        let skill_abs_path = self.root_dir.join(relative_path);

        let is_windows = cfg!(target_os = "windows");
        let script_name = if is_windows { "deploy.bat" } else { "deploy.sh" };
        let script_path = skill_abs_path.join(script_name);

        let tx_ws_clone = tx_ws.clone();
        let skill_id_str = skill_id.to_string();

        let send_progress = move |stage: &str, message: &str| {
            let tx = tx_ws_clone.clone();
            let s_id = skill_id_str.clone();
            let msg = message.to_string();
            let stg = stage.to_string();
            tokio::spawn(async move {
                let payload = serde_json::json!({
                    "event": "deploy_progress",
                    "skillId": s_id,
                    "stage": stg,
                    "message": msg
                });
                if let Ok(payload_str) = serde_json::to_string(&payload) {
                    let _ = tx.send(axum::extract::ws::Message::Text(payload_str)).await;
                }
            });
        };

        {
            let mut res = self.deploy_reservations.write().await;
            if !res.insert(skill_id.to_string()) {
                send_progress("error", "Deployment is already running for this skill.");
                return Err("Deployment already running".into());
            }
        }

        let skill_id_clone_for_drop = skill_id.to_string();
        let deploy_reservations = self.deploy_reservations.clone();
        
        let result = async {
            send_progress("start", "Initializing deployment environment...");

        // Clean up any incomplete previous deployment
        if !skill_abs_path.join(".deployed").exists() {
            let venv_path = skill_abs_path.join(".venv");
            if venv_path.exists() {
                if let Err(_e) = tokio::fs::remove_dir_all(&venv_path).await {
                    send_progress("progress", "Cleaning up locked files...");
                    // If locked, attempt to kill any processes running inside this .venv
                    if cfg!(target_os = "windows") {
                        let path_match = format!("{}*", venv_path.to_string_lossy());
                        let script = format!("Get-Process | Where-Object {{ $_.Path -like '{}' }} | Stop-Process -Force -ErrorAction SilentlyContinue", path_match.replace("\\", "\\\\"));
                        let mut c = Command::new("powershell");
                        let _ = c.args(&["-NoProfile", "-Command", &script]).output().await;
                        tokio::time::sleep(tokio::time::Duration::from_millis(500)).await;
                    }
                    if let Err(e2) = tokio::fs::remove_dir_all(&venv_path).await {
                        send_progress("error", &format!("Failed to clear old .venv folder even after killing orphans. Please restart the app. ({})", e2));
                        deploy_reservations.write().await.remove(&skill_id_clone_for_drop);
                        return Err(format!("Cleanup failed: {}", e2).into());
                    }
                }
            }
            let node_path = skill_abs_path.join("node_modules");
            if node_path.exists() {
                if let Err(e) = tokio::fs::remove_dir_all(&node_path).await {
                    send_progress("error", &format!("Failed to clear old node_modules folder. ({})", e));
                    return Err(format!("Cleanup failed: {}", e).into());
                }
            }
        }

        let mut child = if script_path.exists() {
            let mut cmd = if is_windows {
                let mut c = Command::new("cmd");
                c.arg("/C").arg(script_name);
                c
            } else {
                let mut c = Command::new("bash");
                c.arg(script_name);
                c
            };
            cmd.current_dir(&skill_abs_path)
                .env("PYTHONUNBUFFERED", "1")
                .stdout(Stdio::piped())
                .stderr(Stdio::piped())
                .spawn()?
        } else {
            // Fallback implementation in Rust
            if skill_abs_path.join("package.json").exists() {
                send_progress("install", "Running npm install...");
                let mut cmd = if is_windows {
                    let mut c = Command::new("npm.cmd");
                    c.arg("install");
                    c
                } else {
                    let mut c = Command::new("npm");
                    c.arg("install");
                    c
                };
                cmd.current_dir(&skill_abs_path)
                    .stdout(Stdio::piped())
                    .stderr(Stdio::piped())
                    .spawn()?
            } else {
                // Python fallback
                send_progress("venv", "Setting up Python virtual environment...");
                let python_cmd = if is_windows { "python" } else { "python3" };
                let mut venv_cmd = Command::new(python_cmd);
                venv_cmd
                    .args(&["-m", "venv", ".venv"])
                    .current_dir(&skill_abs_path)
                    .stdout(Stdio::null())
                    .stderr(Stdio::null());
                
                let venv_status = venv_cmd.status().await?;
                if !venv_status.success() {
                    send_progress("error", "Failed to create virtual environment.");
                    return Err("Failed to create venv".into());
                }

                send_progress("install", "Installing Python packages...");
                let pip_exec = if is_windows {
                    skill_abs_path.join(".venv").join("Scripts").join("pip.exe")
                } else {
                    skill_abs_path.join(".venv").join("bin").join("pip")
                };

                let mut cmd = Command::new(pip_exec);
                cmd.args(&["install", "-r", "requirements.txt"])
                    .current_dir(&skill_abs_path)
                    .stdout(Stdio::piped())
                    .stderr(Stdio::piped())
                    .spawn()?
            }
        };

        let stdout = child.stdout.take().ok_or("Failed to open stdout")?;
        let stderr = child.stderr.take().ok_or("Failed to open stderr")?;

        let tx_ws_stdout = tx_ws.clone();
        let skill_id_stdout = skill_id.to_string();
        tokio::spawn(async move {
            let mut reader = BufReader::new(stdout).lines();
            while let Ok(Some(line)) = reader.next_line().await {
                let trimmed = line.trim();
                if trimmed.is_empty() {
                    continue;
                }
                if trimmed.starts_with('{') && trimmed.ends_with('}') {
                    if let Ok(parsed) = serde_json::from_str::<Value>(trimmed) {
                        let stage = parsed.get("stage").and_then(|v| v.as_str()).unwrap_or("installing").to_string();
                        let message = parsed.get("message").and_then(|v| v.as_str()).unwrap_or("").to_string();
                        let payload = serde_json::json!({
                            "event": "deploy_progress",
                            "skillId": skill_id_stdout,
                            "stage": stage,
                            "message": message
                        });
                        if let Ok(payload_str) = serde_json::to_string(&payload) {
                            let _ = tx_ws_stdout.send(axum::extract::ws::Message::Text(payload_str)).await;
                        }
                        continue;
                    }
                }
                // Send raw stdout line
                let payload = serde_json::json!({
                    "event": "deploy_progress",
                    "skillId": skill_id_stdout,
                    "stage": "log",
                    "message": trimmed
                });
                if let Ok(payload_str) = serde_json::to_string(&payload) {
                    let _ = tx_ws_stdout.send(axum::extract::ws::Message::Text(payload_str)).await;
                }
            }
        });

        let tx_ws_stderr = tx_ws.clone();
        let skill_id_stderr = skill_id.to_string();
        tokio::spawn(async move {
            let mut reader = BufReader::new(stderr).lines();
            while let Ok(Some(line)) = reader.next_line().await {
                let trimmed = line.trim();
                if trimmed.is_empty() {
                    continue;
                }
                let payload = serde_json::json!({
                    "event": "deploy_progress",
                    "skillId": skill_id_stderr,
                    "stage": "log",
                    "message": format!("[err] {}", trimmed)
                });
                if let Ok(payload_str) = serde_json::to_string(&payload) {
                    let _ = tx_ws_stderr.send(axum::extract::ws::Message::Text(payload_str)).await;
                }
            }
        });

        let status = child.wait().await?;
        if status.success() {
            let _ = tokio::fs::File::create(skill_abs_path.join(".deployed")).await;
            send_progress("complete", "Deployment completed successfully! Ready to start.");
            Ok(())
        } else {
            // Clean up unwanted things on failure
            let _ = tokio::fs::remove_dir_all(skill_abs_path.join(".venv")).await;
            let _ = tokio::fs::remove_dir_all(skill_abs_path.join("node_modules")).await;
            let _ = tokio::fs::remove_file(skill_abs_path.join(".deployed")).await;
            send_progress("error", &format!("Deployment failed with exit status: {}", status));
            Err("Deployment failed".into())
        }
        }.await;

        deploy_reservations.write().await.remove(&skill_id_clone_for_drop);
        result
    }
}
