use crate::server::state::ServerState;
use axum::{extract::{State}, response::{IntoResponse}, Json};

use serde_json::Value;

use std::sync::Arc;

// Get continuous recorders statuses

pub async fn get_recorders(State(state): State<Arc<ServerState>>) -> impl IntoResponse {

    let recorders = state.recording_manager.active_recorders.read().await;

    let list: Vec<Value> = recorders

        .iter()

        .map(|(id, _)| {

            serde_json::json!({

                "cameraId": id,

                "state": "recording",

                "segmentSeconds": 5

            })

        })

        .collect();

    Json(serde_json::json!(list))

}

// Start Continuous Recorder for Camera

pub async fn start_recorder(

    State(state): State<Arc<ServerState>>,

    Json(payload): Json<Value>,

) -> impl IntoResponse {

    let camera_id = payload["cameraId"].as_str().unwrap_or("");

    if camera_id.is_empty() {

        return Json(serde_json::json!({ "error": "Missing cameraId in payload" }));

    }

    match state.recording_manager.start_recorder(camera_id).await {

        Ok(_) => Json(serde_json::json!({

            "success": true,

            "message": format!("Continuous recorder started for camera '{}'", camera_id)

        })),

        Err(err) => {

            eprintln!(

                "Failed to start recorder for camera '{}': {}",

                camera_id, err

            );

            Json(serde_json::json!({ "error": err.to_string() }))

        }

    }

}

// Stop Continuous Recorder for Camera

pub async fn stop_recorder(

    State(state): State<Arc<ServerState>>,

    Json(payload): Json<Value>,

) -> impl IntoResponse {

    let camera_id = payload["cameraId"].as_str().unwrap_or("");

    if camera_id.is_empty() {

        return Json(serde_json::json!({ "error": "Missing cameraId in payload" }));

    }

    state.recording_manager.stop_recorder(camera_id).await;

    Json(serde_json::json!({

        "success": true,

        "message": format!("Continuous recorder stopped for camera '{}'", camera_id)

    }))

}

// Handle bulk recorder actions (start_all / stop_all) from the frontend toggle
pub async fn handle_recorders_action(
    State(state): State<Arc<ServerState>>,
    Json(payload): Json<Value>,
) -> impl IntoResponse {
    let action = payload["action"].as_str().unwrap_or("");

    match action {
        "start_all" => {
            state.recording_manager.start_all().await;
            Json(serde_json::json!({ "success": true, "message": "All recorders started" }))
        }
        "stop_all" => {
            state.recording_manager.stop_all().await;
            Json(serde_json::json!({ "success": true, "message": "All recorders stopped" }))
        }
        _ => {
            Json(serde_json::json!({ "error": format!("Unknown action: {}", action) }))
        }
    }
}

#[derive(serde::Deserialize)]

pub struct EventsQuery {

    pub camera_id: Option<String>,

    pub label: Option<String>,

    pub limit: Option<usize>,

    pub offset: Option<usize>,

    pub id: Option<String>,
}
