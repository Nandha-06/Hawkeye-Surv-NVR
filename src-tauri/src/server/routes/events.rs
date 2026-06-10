use rusqlite::Connection;
use crate::server::helpers::serve_file;
use crate::server::routes::recorders::EventsQuery;

use crate::server::state::ServerState;
use axum::{body::{Body}, extract::{Path, Query, State}, http::{header, StatusCode}, response::{IntoResponse, Response}, Json};

use serde_json::Value;
use std::path::PathBuf;
use std::sync::Arc;

pub async fn get_events(

    State(state): State<Arc<ServerState>>,

    Query(params): Query<EventsQuery>,

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

    let mut query = "SELECT id, camera_id, label, confidence, timestamp, snapshot_path, severity FROM events WHERE 1=1".to_string();

    let mut args: Vec<Box<dyn rusqlite::ToSql>> = vec![];

    if let Some(id) = params.id {

        query.push_str(" AND id = ?");

        args.push(Box::new(id));

    } else {

        if let Some(ref cam_id) = params.camera_id {

            if cam_id != "all" {

                query.push_str(" AND camera_id = ?");

                args.push(Box::new(cam_id.clone()));

            }

        }

        if let Some(ref label) = params.label {

            if label != "all" {

                query.push_str(" AND LOWER(label) = ?");

                args.push(Box::new(label.to_lowercase()));

            }

        }

    }

    query.push_str(" ORDER BY timestamp DESC");

    let limit = params.limit.unwrap_or(50).clamp(1, 1_000);
    let offset = params.offset.unwrap_or(0).min(1_000_000);

    query.push_str(" LIMIT ?");
    args.push(Box::new(limit));

    if params.offset.is_some() {
        query.push_str(" OFFSET ?");
        args.push(Box::new(offset));
    }

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

            "label": row.get::<_, String>(2)?,

            "confidence": row.get::<_, f64>(3)?,

            "timestamp": row.get::<_, String>(4)?,

            "snapshot_path": row.get::<_, Option<String>>(5)?,

            "severity": row.get::<_, String>(6)?,

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

#[derive(serde::Deserialize)]

pub struct DeleteEventQuery {

    pub id: String,
}

pub async fn delete_event(

    State(state): State<Arc<ServerState>>,

    Query(params): Query<DeleteEventQuery>,

) -> impl IntoResponse {

    let root = state.skills_manager.root_dir.clone();

    let db_path = root.join(".data").join("hawkeye.db");

    let conn = match Connection::open(&db_path) {

        Ok(c) => c,

        Err(err) => return Json(serde_json::json!({ "success": false, "error": err.to_string() })),

    };

    // Query snapshot path first

    let snapshot_path: Option<String> = conn

        .query_row(

            "SELECT snapshot_path FROM events WHERE id = ?1",

            rusqlite::params![params.id],

            |row| row.get(0),

        )

        .unwrap_or(None);

    // Delete row

    if let Err(err) = conn.execute(

        "DELETE FROM events WHERE id = ?1",

        rusqlite::params![params.id],

    ) {

        return Json(serde_json::json!({ "success": false, "error": err.to_string() }));

    }

    // Delete file from disk if it exists
    if let Some(path_str) = snapshot_path {
        let snapshots_root = root.join(".data").join("snapshots");
        let frontend_snapshots_root = root.join("frontend").join(".data").join("snapshots");
        let path = std::path::Path::new(&path_str);
        let safe = crate::server::path_safe::ensure_under(path, &snapshots_root)
            || crate::server::path_safe::ensure_under(path, &frontend_snapshots_root);
        if safe {
            let file_path = PathBuf::from(&path_str);
            if file_path.exists() {
                let _ = tokio::fs::remove_file(file_path).await;
            }
        } else {
            log::warn!("[delete_event] rejected snapshot path outside snapshots/: {}", path_str);
        }
    }

    Json(serde_json::json!({ "success": true }))

}

pub async fn get_event_snapshot(

    State(state): State<Arc<ServerState>>,

    Path(id): Path<String>,

) -> Response {

    let root = state.skills_manager.root_dir.clone();

    let db_path = root.join(".data").join("hawkeye.db");

    let conn = match Connection::open(&db_path) {

        Ok(c) => c,

        Err(_) => {

            return Response::builder()

                .status(StatusCode::INTERNAL_SERVER_ERROR)

                .body(Body::from(""))

                .unwrap()

        }

    };

    let snapshot_path: Option<String> = conn

        .query_row(

            "SELECT snapshot_path FROM events WHERE id = ?1",

            rusqlite::params![id],

            |row| row.get(0),

        )

        .unwrap_or(None);

    if let Some(path_str) = snapshot_path {
        let snapshots_root = root.join(".data").join("snapshots");
        let frontend_snapshots_root = root.join("frontend").join(".data").join("snapshots");
        let path = std::path::Path::new(&path_str);
        let safe = crate::server::path_safe::ensure_under(path, &snapshots_root)
            || crate::server::path_safe::ensure_under(path, &frontend_snapshots_root);
        if !safe {
            log::warn!("[get_event_snapshot] rejected path outside snapshots/: {}", path_str);
            return Response::builder()
                .status(StatusCode::NOT_FOUND)
                .body(Body::from("Snapshot not found"))
                .unwrap();
        }
        let file_path = PathBuf::from(&path_str);

        if file_path.exists() {

            return serve_file(file_path, "image/jpeg").await;

        }

    }

    Response::builder()

        .status(StatusCode::NOT_FOUND)

        .body(Body::from("Snapshot not found"))

        .unwrap()

}

#[derive(serde::Deserialize)]

pub struct SaveIdentityPayload {

    #[serde(rename = "oldName")]

    pub old_name: String,

    #[serde(rename = "newName")]

    pub new_name: String,
}
