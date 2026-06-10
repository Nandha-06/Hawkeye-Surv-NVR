use rusqlite::Connection;
use crate::server::helpers::download_file_attachment;
use crate::server::state::{generate_random_id, ExportJob};

use crate::server::state::ServerState;
use axum::{body::{Body}, extract::{Path, State}, http::{StatusCode}, response::{IntoResponse, Response}, Json};

use std::sync::Arc;
use std::path::Path as FsPath;

pub async fn get_exports(State(state): State<Arc<ServerState>>) -> impl IntoResponse {

    let list = state.exports.read().await.clone();

    Json(list)

}

#[derive(serde::Deserialize)]

pub struct CreateExportPayload {

    pub camera_id: String,

    pub start_time: String,

    pub end_time: String,
}

pub async fn create_export(

    State(state): State<Arc<ServerState>>,

    Json(payload): Json<CreateExportPayload>,

) -> impl IntoResponse {

    let id = format!("export_{}", generate_random_id());

    let job = ExportJob {

        id: id.clone(),

        camera_id: payload.camera_id.clone(),

        status: "pending".to_string(),

        progress: 0,

        start_time: payload.start_time.clone(),

        end_time: payload.end_time.clone(),

        message: None,

    };

    state.exports.write().await.push(job);

    // Spawn background worker to perform lossless FFmpeg slice copy concatenation!

    let state_clone = state.clone();

    let job_id = id.clone();

    tokio::spawn(async move {

        // Update to processing

        {

            let mut list = state_clone.exports.write().await;

            if let Some(j) = list.iter_mut().find(|j| j.id == job_id) {

                j.status = "processing".to_string();

                j.progress = 10;

            }

        }

        let root = state_clone.skills_manager.root_dir.clone();

        let db_path = root.join(".data").join("hawkeye.db");

        let filepaths_res: Result<Vec<String>, String> = {

            let conn = Connection::open(&db_path)

                .map_err(|e| format!("Failed to connect to SQLite database: {}", e));

            conn.and_then(|c| {

                let mut stmt = c.prepare(

                    "SELECT filepath FROM recordings WHERE camera_id = ?1 AND start_time < ?2 AND end_time > ?3 ORDER BY start_time ASC"

                ).map_err(|e| format!("Failed to prepare database statement: {}", e))?;

                let rows = stmt.query_map(rusqlite::params![payload.camera_id, payload.end_time, payload.start_time], |row| {

                    row.get::<_, String>(0)

                }).map_err(|e| format!("Query failed: {}", e))?;

                let paths: Vec<String> = rows.filter_map(Result::ok).collect();

                Ok(paths)

            })

        };

        let filepaths = match filepaths_res {

            Ok(paths) => paths,

            Err(err_msg) => {

                fail_job(state_clone, &job_id, &err_msg).await;

                return;

            }

        };

        if filepaths.is_empty() {

            fail_job(

                state_clone,

                &job_id,

                "No continuous recordings found for specified timeline",

            )

            .await;

            return;

        }

        // Update progress

        {

            let mut list = state_clone.exports.write().await;

            if let Some(j) = list.iter_mut().find(|j| j.id == job_id) {

                j.progress = 40;

            }

        }

        // Create concat file

        let exports_dir = root.join(".data").join("exports");

        let _ = tokio::fs::create_dir_all(&exports_dir).await;

        let concat_txt_path = exports_dir.join(format!("concat_{}.txt", job_id));

        let mut concat_content = String::new();

        let recordings_dir = root.join(".data").join("recordings");
        let safe_filepaths: Vec<String> = filepaths
            .into_iter()
            .filter(|path| {
                let candidate = FsPath::new(path);
                !path.chars().any(|c| c == '\r' || c == '\n' || c == '\'')
                    && candidate.is_file()
                    && crate::server::path_safe::ensure_under(candidate, &recordings_dir)
            })
            .collect();

        if safe_filepaths.is_empty() {
            fail_job(state_clone, &job_id, "No safe recording files found for export").await;
            return;
        }

        for path in safe_filepaths {

            // Absolute path formatted for FFmpeg concat demuxer
            // Escape single quotes to prevent path injection
            let cleaned_path = path.replace("\\", "/").replace('\'', "'\\''");

            concat_content.push_str(&format!("file '{}'\n", cleaned_path));

        }

        if let Err(err) = tokio::fs::write(&concat_txt_path, concat_content).await {

            fail_job(

                state_clone,

                &job_id,

                &format!("Failed to generate concat manifest: {}", err),

            )

            .await;

            return;

        }

        {

            let mut list = state_clone.exports.write().await;

            if let Some(j) = list.iter_mut().find(|j| j.id == job_id) {

                j.progress = 60;

            }

        }

        let output_mp4 = exports_dir.join(format!("{}.mp4", job_id));

        // Spawn FFmpeg concat copy

        println!(

            "[Export] Concatenating continuous segments into: {:?}",

            output_mp4

        );

        let ffmpeg_res = tokio::process::Command::new("ffmpeg")

            .arg("-y")

            .arg("-f")

            .arg("concat")

            .arg("-safe")

            .arg("0")

            .arg("-i")

            .arg(&concat_txt_path)

            .arg("-c")

            .arg("copy")

            .arg(&output_mp4)

            .stdout(std::process::Stdio::null())

            .stderr(std::process::Stdio::null())

            .spawn();

        let mut child = match ffmpeg_res {

            Ok(c) => c,

            Err(err) => {

                let _ = tokio::fs::remove_file(&concat_txt_path).await;

                fail_job(

                    state_clone,

                    &job_id,

                    &format!("Failed to execute FFmpeg: {}", err),

                )

                .await;

                return;

            }

        };

        match tokio::time::timeout(std::time::Duration::from_secs(300), child.wait()).await {

            Ok(Ok(status)) if status.success() => {

                let _ = tokio::fs::remove_file(&concat_txt_path).await;

                // Complete job

                let mut list = state_clone.exports.write().await;

                if let Some(j) = list.iter_mut().find(|j| j.id == job_id) {

                    j.status = "completed".to_string();

                    j.progress = 100;

                }

                println!("[Export] Export job '{}' completed successfully!", job_id);

            }

            Ok(_) => {

                let _ = tokio::fs::remove_file(&concat_txt_path).await;

                fail_job(state_clone, &job_id, "FFmpeg concatenation failed").await;

            }

            Err(_) => {
                
                let _ = child.kill().await;

                let _ = tokio::fs::remove_file(&concat_txt_path).await;

                fail_job(state_clone, &job_id, "FFmpeg concatenation timed out after 5 minutes").await;

            }

        }

    });

    Json(serde_json::json!({ "success": true, "jobId": id }))

}

async fn fail_job(state: Arc<ServerState>, job_id: &str, msg: &str) {

    eprintln!("[Export] Job '{}' failed: {}", job_id, msg);

    let mut list = state.exports.write().await;

    if let Some(j) = list.iter_mut().find(|j| j.id == job_id) {

        j.status = "failed".to_string();

        j.progress = 0;

        j.message = Some(msg.to_string());

    }

}

pub async fn download_export(

    State(state): State<Arc<ServerState>>,

    Path(id): Path<String>,

) -> Response {

    let root = state.skills_manager.root_dir.clone();

    let exports_dir = root.join(".data").join("exports");
    let filename = format!("{}.mp4", id);
    let Some(file_path) = crate::server::path_safe::safe_join_under(&exports_dir, &filename) else {
        return Response::builder()
            .status(StatusCode::BAD_REQUEST)
            .body(Body::from("Invalid export id"))
            .unwrap();
    };

    if file_path.exists() {

        return download_file_attachment(file_path, &format!("export_{}.mp4", id)).await;

    }

    Response::builder()

        .status(StatusCode::NOT_FOUND)

        .body(Body::from("Export file not found"))

        .unwrap()

}
