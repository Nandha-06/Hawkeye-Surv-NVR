use reqwest::Client;
use serde_json::Value;
use std::path::{Path, PathBuf};
use std::time::Duration;
use std::process::{Child, Command};

pub struct Go2RtcManager;

/// A standard library `Child` that, when dropped, attempts to `kill()` the underlying
/// process and `wait()` to reap it. Prevents go2rtc.exe from being left
/// running after the Tauri app exits.
pub struct OwnedChild(pub Child);

impl OwnedChild {
    pub fn new(child: Child) -> Self {
        Self(child)
    }

    pub fn id(&self) -> Option<u32> {
        Some(self.0.id())
    }
}

impl Drop for OwnedChild {
    fn drop(&mut self) {
        // Best-effort kill. We use blocking std APIs because Drop is sync;
        // the process is going down anyway.
        let _ = self.0.kill();
        // Wait blockingly to reap it. Since it's std::process::Child, wait() is synchronous.
        let _ = self.0.wait();
    }
}

impl std::ops::Deref for OwnedChild {
    type Target = Child;
    fn deref(&self) -> &Child {
        &self.0
    }
}

impl std::ops::DerefMut for OwnedChild {
    fn deref_mut(&mut self) -> &mut Child {
        &mut self.0
    }
}

impl Go2RtcManager {
    /// Write the default go2rtc config if it doesn't exist
    pub fn ensure_config(root_dir: &Path) -> std::io::Result<PathBuf> {
        let data_dir = root_dir.join(".data");
        std::fs::create_dir_all(&data_dir)?;

        let yaml_path = data_dir.join("go2rtc.yaml");
        if !yaml_path.exists() {
            let default_yaml = r#"# go2rtc configuration
api:
  listen: "127.0.0.1:1984"
  origin: "*"

rtsp:
  listen: "127.0.0.1:8554"

webrtc:
  listen: "127.0.0.1:8555"
  ice_servers:
    - urls: [stun:stun.l.google.com:19302]
  candidates:
    - "127.0.0.1:8555"

log:
  level: info

streams: {}
"#;
            std::fs::write(&yaml_path, default_yaml)?;
            println!("[go2rtc] Created default configuration at {:?}", yaml_path);
        }
        Ok(yaml_path)
    }

    /// Spawn the go2rtc.exe binary as a sidecar process
    pub fn spawn(root_dir: &Path) -> Option<Child> {
        let bin_path = root_dir.join(".data").join("bin").join("go2rtc.exe");
        if !bin_path.exists() {
            eprintln!("[go2rtc] Binary not found at {:?}", bin_path);
            return None;
        }

        let yaml_path = match Self::ensure_config(root_dir) {
            Ok(p) => p,
            Err(e) => {
                eprintln!("[go2rtc] Failed to configure go2rtc: {}", e);
                return None;
            }
        };

        println!("[go2rtc] Spawning sidecar process: {:?}", bin_path);
        match Command::new(&bin_path)
            .arg("-config")
            .arg(&yaml_path)
            .stdout(std::process::Stdio::null())
            .stderr(std::process::Stdio::null())
            .spawn()
        {
            Ok(child) => {
                println!("[go2rtc] Process spawned successfully.");
                Some(child)
            }
            Err(e) => {
                eprintln!("[go2rtc] Failed to spawn go2rtc.exe: {}", e);
                None
            }
        }
    }

    /// Register a camera's RTSP stream with the running go2rtc instance via the PUT API
    pub async fn register_stream(camera_id: &str, url: &str) -> bool {
        let client = Client::builder()
            .timeout(Duration::from_secs(3))
            .build()
            .unwrap_or_default();

        // Encode parameters
        let encoded_url = urlencoding::encode(url);
        let encoded_id = urlencoding::encode(camera_id);

        let api_url = format!(
            "http://127.0.0.1:1984/api/streams?src={}&name={}",
            encoded_url, encoded_id
        );

        match client.put(&api_url).send().await {
            Ok(resp) => {
                if resp.status().is_success() {
                    println!("[go2rtc] Registered stream: {}", camera_id);
                    true
                } else {
                    eprintln!(
                        "[go2rtc] Failed to register stream {} (HTTP status {})",
                        camera_id,
                        resp.status()
                    );
                    false
                }
            }
            Err(e) => {
                eprintln!("[go2rtc] Error calling go2rtc API for {}: {}", camera_id, e);
                false
            }
        }
    }

    /// Read cameras.json and register all enabled streams with go2rtc
    pub async fn register_all_cameras(root_dir: &Path) {
        let cameras_path = root_dir.join(".data").join("cameras.json");
        if !cameras_path.exists() {
            return;
        }

        let content = match tokio::fs::read_to_string(&cameras_path).await {
            Ok(c) => c,
            Err(_) => return,
        };

        let cameras: Value = match serde_json::from_str(&content) {
            Ok(j) => j,
            Err(_) => return,
        };

        let Some(arr) = cameras.as_array() else {
            return;
        };

        // Wait a brief moment to ensure go2rtc API is up
        tokio::time::sleep(Duration::from_millis(1000)).await;

        for camera in arr {
            let id = camera["id"].as_str().unwrap_or("");
            let enabled = camera["enabled"].as_bool().unwrap_or(false);
            let source = camera["source"].as_str().unwrap_or("");

            if !enabled || id.is_empty() || source != "rtsp" {
                continue;
            }

            // Register main stream (url)
            if let Some(main_url) = camera["url"].as_str() {
                if !main_url.is_empty() {
                    Self::register_stream(id, main_url).await;
                }
            }

            // Register sub stream (detect_url) if present
            if let Some(sub_url) = camera["detect_url"].as_str() {
                if !sub_url.is_empty() {
                    let sub_id = format!("{}_sub", id);
                    Self::register_stream(&sub_id, sub_url).await;
                }
            }
        }
    }
}
