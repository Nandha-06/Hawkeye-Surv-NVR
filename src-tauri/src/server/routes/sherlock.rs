use crate::server::state::ServerState;
use axum::{extract::State, response::IntoResponse, Json};
use serde_json::Value;
use std::sync::Arc;
use tokio::process::{Command, ChildStdin, ChildStdout};
use tokio::io::{AsyncWriteExt, AsyncBufReadExt, BufReader, Lines};
use std::sync::OnceLock;
use tokio::sync::Mutex;

struct DaemonProcess {
    stdin: ChildStdin,
    stdout: Lines<BufReader<ChildStdout>>,
}

static DAEMON: OnceLock<Mutex<Option<DaemonProcess>>> = OnceLock::new();

pub async fn search_sherlock(
    State(state): State<Arc<ServerState>>,
    Json(payload): Json<Value>,
) -> impl IntoResponse {
    let query = payload["query"].as_str().unwrap_or("");
    let limit = payload["limit"]
        .as_u64()
        .or_else(|| payload["limit"].as_str().and_then(|v| v.parse().ok()))
        .unwrap_or(5)
        .clamp(1, 100);

    if query.is_empty() {
        return Json(serde_json::json!({ "error": "Missing query in payload" }));
    }

    let sherlock_dir = std::env::current_dir().unwrap_or_default().join(state.skills_manager.root_dir.join("skills/analysis/sherlock"));
    let sherlock_dir = std::fs::canonicalize(&sherlock_dir).unwrap_or(sherlock_dir);

    let daemon_mutex = DAEMON.get_or_init(|| Mutex::new(None));
    let mut daemon_opt = daemon_mutex.lock().await;

    // Restart daemon if it doesn't exist or has shut down (checked via testing stdout briefly? no, just try writing and if it fails, restart)
    let mut needs_restart = daemon_opt.is_none();

    if !needs_restart {
        let process = daemon_opt.as_mut().unwrap();
        // Ping to check if alive
        let msg = "{\"action\": \"ping\"}\n";
        if process.stdin.write_all(msg.as_bytes()).await.is_err() {
            needs_restart = true;
        }
    }

    if needs_restart {
        match Command::new("uv")
            .arg("run")
            .arg("--directory")
            .arg(&sherlock_dir)
            .arg("python")
            .arg("scripts/daemon.py")
            .env("HF_HUB_OFFLINE", "1") // Prevent auto-downloading here
            .stdin(std::process::Stdio::piped())
            .stdout(std::process::Stdio::piped())
            .spawn()
        {
            Ok(mut child) => {
                let stdin = child.stdin.take().unwrap();
                let stdout = child.stdout.take().unwrap();
                let mut lines = BufReader::new(stdout).lines();
                
                // Read the 'ready' or 'error' event
                if let Ok(Some(line)) = lines.next_line().await {
                    if line.contains("\"error\"") {
                        let err_msg = serde_json::from_str::<Value>(&line).ok()
                            .and_then(|v| v["error"].as_str().map(String::from))
                            .unwrap_or(line);
                        *daemon_opt = None;
                        return Json(serde_json::json!({ "error": format!("Daemon init failed: {}", err_msg) }));
                    }
                }

                *daemon_opt = Some(DaemonProcess { stdin, stdout: lines });
            }
            Err(err) => {
                return Json(serde_json::json!({ "error": format!("Failed to spawn daemon: {}", err) }));
            }
        }
    }

    let process = daemon_opt.as_mut().unwrap();
    let req_id = rand::random::<u64>().to_string();
    let request = serde_json::json!({
        "action": "search",
        "id": req_id,
        "query": query,
        "limit": limit
    });

    let msg = format!("{}\n", request);
    if let Err(e) = process.stdin.write_all(msg.as_bytes()).await {
        *daemon_opt = None; // clear bad process
        return Json(serde_json::json!({ "error": format!("Failed to send to daemon: {}", e) }));
    }
    if let Err(e) = process.stdin.flush().await {
        *daemon_opt = None;
        return Json(serde_json::json!({ "error": format!("Failed to flush daemon: {}", e) }));
    }

    // Wait for the specific response
    while let Ok(Some(line)) = process.stdout.next_line().await {
        if let Ok(resp) = serde_json::from_str::<Value>(&line) {
            if resp["event"].as_str() == Some("search_results") && resp["id"].as_str() == Some(&req_id) {
                return Json(serde_json::json!({
                    "success": true,
                    "results": resp["results"],
                }));
            } else if resp["event"].as_str() == Some("error") {
                if resp["error"].as_str().unwrap_or("").contains("Daemon shut down") {
                    *daemon_opt = None;
                }
                return Json(serde_json::json!({ "error": resp["error"].as_str().unwrap_or("Unknown error") }));
            } else if resp["event"].as_str() == Some("pong") {
                continue; // Ignore pong events
            }
        }
    }

    *daemon_opt = None;
    Json(serde_json::json!({ "error": "Daemon closed unexpectedly" }))
}

pub async fn download_sherlock_model(
    State(state): State<Arc<ServerState>>,
) -> impl IntoResponse {
    let sherlock_dir = std::env::current_dir().unwrap_or_default().join(state.skills_manager.root_dir.join("skills/analysis/sherlock"));
    let sherlock_dir = std::fs::canonicalize(&sherlock_dir).unwrap_or(sherlock_dir);

    match Command::new("uv")
        .arg("run")
        .arg("--directory")
        .arg(&sherlock_dir)
        .arg("python")
        .arg("scripts/download.py")
        .output()
        .await
    {
        Ok(output) => {
            let stdout = String::from_utf8_lossy(&output.stdout);
            let stderr = String::from_utf8_lossy(&output.stderr);
            if !output.status.success() {
                return Json(serde_json::json!({ "error": format!("Download failed: {}\n{}", stderr, stdout) }));
            }
            Json(serde_json::json!({ "success": true, "message": "Model downloaded successfully." }))
        }
        Err(err) => Json(serde_json::json!({ "error": format!("Failed to run download script: {}", err) })),
    }
}

pub async fn index_sherlock(
    State(state): State<Arc<ServerState>>,
    Json(payload): Json<Value>,
) -> impl IntoResponse {
    let sherlock_dir = std::env::current_dir().unwrap_or_default().join(state.skills_manager.root_dir.join("skills/analysis/sherlock"));
    let sherlock_dir = std::fs::canonicalize(&sherlock_dir).unwrap_or(sherlock_dir);
    
    let recordings_dir = std::env::current_dir().unwrap_or_default().join(state.data_dir.join("recordings"));
    let recordings_dir = std::fs::canonicalize(&recordings_dir).unwrap_or(recordings_dir);

    let backend = payload["backend"].as_str().unwrap_or("local");

    match Command::new("uv")
        .arg("run")
        .arg("--directory")
        .arg(&sherlock_dir)
        .arg("sentrysearch")
        .arg("index")
        .arg(&recordings_dir)
        .arg("--backend")
        .arg(backend)
        .output()
        .await
    {
        Ok(output) => {
            let stdout_str = String::from_utf8_lossy(&output.stdout).to_string();
            let stderr_str = String::from_utf8_lossy(&output.stderr).to_string();

            if !output.status.success() {
                return Json(serde_json::json!({ "error": format!("Indexing failed: {}\nstdout: {}", stderr_str, stdout_str) }));
            }

            Json(serde_json::json!({
                "success": true,
                "message": "Indexing complete",
                "raw_output": stdout_str
            }))
        }
        Err(err) => Json(serde_json::json!({ "error": format!("Failed to execute sentrysearch: {}", err) })),
    }
}
