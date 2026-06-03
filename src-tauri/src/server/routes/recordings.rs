use rusqlite::Connection;
use tokio::io::AsyncWriteExt;

use crate::server::state::generate_random_id;
use crate::server::routes::identities::RecordingsQuery;

use crate::server::state::ServerState;
use axum::{body::{Body}, extract::{Multipart, Path, Query, State}, http::{header, StatusCode}, response::{IntoResponse, Response}, Json};

use serde_json::Value;
use std::path::PathBuf;
use std::sync::Arc;

pub async fn get_recordings(

    State(state): State<Arc<ServerState>>,

    Query(params): Query<RecordingsQuery>,

) -> impl IntoResponse {

    let root = state.skills_manager.root_dir.clone();

    let db_path = root.join(".data").join("hawkeye.db");

    let conn = match Connection::open(&db_path) {

        Ok(c) => c,

        Err(err) => {

            return Response::builder()

                .status(StatusCode::INTERNAL_SERVER_ERROR)

                .body(Body::from(format!("Failed to open DB: {}", err)))

                .unwrap()

        }

    };

    let mut query =

        "SELECT id, camera_id, start_time, end_time, filepath, type FROM recordings WHERE 1=1"

            .to_string();

    let mut args: Vec<Box<dyn rusqlite::ToSql>> = vec![];

    if let Some(ref cam_id) = params.camera_id {

        if cam_id != "all" {

            query.push_str(" AND camera_id = ?");

            args.push(Box::new(cam_id.clone()));

        }

    }

    if let (Some(start), Some(end)) = (params.start_time.as_ref(), params.end_time.as_ref()) {

        query.push_str(" AND start_time < ? AND end_time > ?");

        args.push(Box::new(end.clone()));

        args.push(Box::new(start.clone()));

    }

    query.push_str(" ORDER BY start_time ASC");

    let mut stmt = match conn.prepare(&query) {

        Ok(s) => s,

        Err(err) => {

            return Response::builder()

                .status(StatusCode::INTERNAL_SERVER_ERROR)

                .body(Body::from(format!("Failed to prepare query: {}", err)))

                .unwrap()

        }

    };

    let params_ref: Vec<&dyn rusqlite::ToSql> = args.iter().map(|b| b.as_ref()).collect();

    let rows = stmt.query_map(&*params_ref, |row| {

        Ok(serde_json::json!({

            "id": row.get::<_, String>(0)?,

            "camera_id": row.get::<_, String>(1)?,

            "start_time": row.get::<_, String>(2)?,

            "end_time": row.get::<_, String>(3)?,

            "filepath": row.get::<_, String>(4)?,

            "type": row.get::<_, String>(5)?,

        }))

    });

    let list: Vec<Value> = match rows {

        Ok(r) => r.filter_map(Result::ok).collect(),

        Err(_) => vec![],

    };

    Response::builder()

        .header(header::CONTENT_TYPE, "application/json")

        .body(Body::from(serde_json::to_string(&list).unwrap()))

        .unwrap()

}

pub async fn delete_recording(

    State(state): State<Arc<ServerState>>,

    Path(id): Path<String>,

) -> impl IntoResponse {

    let root = state.skills_manager.root_dir.clone();

    let db_path = root.join(".data").join("hawkeye.db");

    let conn = match Connection::open(&db_path) {

        Ok(c) => c,

        Err(err) => return Json(serde_json::json!({ "success": false, "error": err.to_string() })),

    };

    let filepath: Option<String> = conn

        .query_row(

            "SELECT filepath FROM recordings WHERE id = ?1",

            rusqlite::params![id],

            |row| row.get(0),

        )

        .unwrap_or(None);

    if let Err(err) = conn.execute(

        "DELETE FROM recordings WHERE id = ?1",

        rusqlite::params![id],

    ) {

        return Json(serde_json::json!({ "success": false, "error": err.to_string() }));

    }

    if let Some(path_str) = filepath {
        let recordings_root = root.join(".data").join("recordings");
        if crate::server::path_safe::ensure_under(std::path::Path::new(&path_str), &recordings_root) {
            let file_path = PathBuf::from(&path_str);
            if file_path.exists() {
                let _ = tokio::fs::remove_file(file_path).await;
            }
        } else {
            log::warn!("[delete_recording] rejected path outside recordings/: {}", path_str);
        }
    }

    Json(serde_json::json!({ "success": true }))

}

pub async fn upload_recording(

    State(state): State<Arc<ServerState>>,

    mut multipart: Multipart,

) -> impl IntoResponse {

    const MAX_UPLOAD_BYTES: u64 = 2 * 1024 * 1024 * 1024;

    let mut camera_id = "".to_string();

    let mut start_time = "".to_string();

    let mut end_time = "".to_string();

    let mut rec_type = "continuous".to_string();

    let root = state.skills_manager.root_dir.clone();

    let upload_dir = root.join(".temp").join("uploads");

    let _ = tokio::fs::create_dir_all(&upload_dir).await;

    let temp_id = generate_random_id();

    let temp_webm_path = upload_dir.join(format!("upload_{}.webm", temp_id));

    let mut video_size: u64 = 0;

    while let Ok(Some(mut field)) = multipart.next_field().await {

        let name = field.name().unwrap_or("").to_string();

        if name == "video" {

            match tokio::fs::File::create(&temp_webm_path).await {

                Ok(mut file) => {

                    while let Ok(Some(chunk)) = field.chunk().await {

                        if video_size.saturating_add(chunk.len() as u64) > MAX_UPLOAD_BYTES {
                            let _ = tokio::fs::remove_file(&temp_webm_path).await;
                            return Json(serde_json::json!({
                                "success": false,
                                "error": format!("Upload exceeds maximum size of {} bytes", MAX_UPLOAD_BYTES)
                            }));
                        }

                        video_size += chunk.len() as u64;

                        if let Err(err) = file.write_all(&chunk).await {

                            let _ = tokio::fs::remove_file(&temp_webm_path).await;

                            return Json(serde_json::json!({

                                "success": false,

                                "error": format!("Failed to stream upload to disk: {}", err)

                            }));

                        }

                    }

                }

                Err(err) => {

                    return Json(serde_json::json!({

                        "success": false,

                        "error": format!("Failed to create upload temp file: {}", err)

                    }));

                }

            }

        } else if name == "camera_id" {

            if let Ok(val) = field.text().await {

                camera_id = val.trim().to_string();

            }

        } else if name == "start_time" {

            if let Ok(val) = field.text().await {

                start_time = val.trim().to_string();

            }

        } else if name == "end_time" {

            if let Ok(val) = field.text().await {

                end_time = val.trim().to_string();

            }

        } else if name == "type" {

            if let Ok(val) = field.text().await {

                rec_type = val.trim().to_string();

            }

        }

    }

    if video_size == 0 || camera_id.is_empty() || start_time.is_empty() || end_time.is_empty() {

        let _ = tokio::fs::remove_file(&temp_webm_path).await;

        return Json(serde_json::json!({ "success": false, "error": "Missing required fields" }));

    }

    if camera_id.contains('/') || camera_id.contains('\\') || camera_id.contains("..") || camera_id.contains('\0') {
        let _ = tokio::fs::remove_file(&temp_webm_path).await;
        return Json(serde_json::json!({ "success": false, "error": "Invalid camera_id" }));
    }

    let recordings_root = root.join(".data").join("recordings");
    let recordings_dir = match crate::server::path_safe::safe_join_under(&recordings_root, &camera_id) {
        Some(p) => p,
        None => {
            let _ = tokio::fs::remove_file(&temp_webm_path).await;
            return Json(serde_json::json!({ "success": false, "error": "Invalid camera_id" }));
        }
    };

    let _ = tokio::fs::create_dir_all(&recordings_dir).await;

    // Format output filename: segment_YYYY-MM-DDTHH-MM-SS.mp4

    let parsed_start = chrono::DateTime::parse_from_rfc3339(&start_time)

        .or_else(|_| chrono::DateTime::parse_from_str(&start_time, "%Y-%m-%dT%H:%M:%S.%fZ"));

    let formatted_time = match parsed_start {

        Ok(dt) => dt.format("%Y-%m-%dT%H-%M-%S").to_string(),

        Err(_) => temp_id.clone(),

    };

    let output_filename = format!("segment_{}.mp4", formatted_time);

    let output_mp4_path = recordings_dir.join(&output_filename);

    // Asynchronously transcode webm to mp4 using FFmpeg

    println!(

        "[Server] Transcoding uploaded webm chunk: ffmpeg -i {:?} -> {:?}",

        temp_webm_path, output_mp4_path

    );

    let mut child = match tokio::process::Command::new("ffmpeg")

        .arg("-y")

        .arg("-i")

        .arg(&temp_webm_path)

        .arg("-c:v")

        .arg("libx264")

        .arg("-preset")

        .arg("ultrafast")

        .arg("-tune")

        .arg("zerolatency")

        .arg("-c:a")

        .arg("aac")

        .arg("-b:a")

        .arg("128k")

        .arg("-f")

        .arg("mp4")

        .arg(&output_mp4_path)

        .stdout(std::process::Stdio::null())

        .stderr(std::process::Stdio::null())

        .spawn()

    {

        Ok(c) => c,

        Err(err) => {

            let _ = tokio::fs::remove_file(&temp_webm_path).await;

            return Json(

                serde_json::json!({ "success": false, "error": format!("Failed to spawn ffmpeg: {}", err) }),

            );

        }

    };

    match child.wait().await {

        Ok(status) if status.success() => {

            let _ = tokio::fs::remove_file(&temp_webm_path).await;

            // Insert into SQLite database

            let db_path = root.join(".data").join("hawkeye.db");

            if let Ok(conn) = Connection::open(&db_path) {

                let id = generate_random_id();

                let filepath_str = output_mp4_path.to_string_lossy().to_string();

                let insert_res = conn.execute(

                    "INSERT OR IGNORE INTO recordings (id, camera_id, start_time, end_time, filepath, type) VALUES (?1, ?2, ?3, ?4, ?5, ?6)",

                    rusqlite::params![

                        id,

                        camera_id,

                        start_time,

                        end_time,

                        filepath_str,

                        rec_type

                    ]

                );

                if let Err(err) = insert_res {

                    eprintln!(

                        "[Server] SQLite insertion error for uploaded chunk: {}",

                        err

                    );

                    return Json(serde_json::json!({ "success": false, "error": err.to_string() }));

                } else {

                    println!(

                        "[Server] Successfully transcoded & indexed segment: {}",

                        output_filename

                    );

                }

            }

            Json(serde_json::json!({ "success": true }))

        }

        _ => {

            let _ = tokio::fs::remove_file(&temp_webm_path).await;

            Json(serde_json::json!({ "success": false, "error": "FFmpeg transcoding failed" }))

        }

    }

}

#[derive(serde::Deserialize)]

pub struct VodPlaylistQuery {

    pub camera_id: String,

    pub start_time: String,

    pub end_time: String,
}

pub struct PlaylistItem {

    pub id: String,

    pub duration: f64,

    pub seek_start: Option<f64>,
}
