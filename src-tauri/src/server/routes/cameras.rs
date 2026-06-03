use crate::services;

use crate::server::state::ServerState;
use axum::{extract::{Path, State}, response::{IntoResponse}, Json};

use serde_json::Value;

use std::sync::Arc;

pub async fn get_cameras(State(state): State<Arc<ServerState>>) -> impl IntoResponse {

    let config_path = state.data_dir.join("cameras.json");

    if !config_path.exists() {

        return Json(serde_json::json!([]));

    }

    match tokio::fs::read_to_string(&config_path).await {

        Ok(content) => {

            let json: Value = serde_json::from_str(&content).unwrap_or(serde_json::json!([]));

            Json(json)

        }

        Err(err) => {

            eprintln!("Failed to read cameras.json: {}", err);

            Json(serde_json::json!({ "error": err.to_string() }))

        }

    }

}

pub async fn save_cameras(

    State(state): State<Arc<ServerState>>,

    Json(payload): Json<Value>,

) -> impl IntoResponse {

    let config_path = state.data_dir.join("cameras.json");

    if !payload.is_array() {

        return Json(

            serde_json::json!({ "error": "Payload must be a JSON array of camera configurations" }),

        );

    }

    match serde_json::to_string_pretty(&payload) {

        Ok(formatted) => {

            if let Err(err) = tokio::fs::write(&config_path, formatted).await {

                eprintln!("Failed to write cameras.json: {}", err);

                Json(serde_json::json!({ "error": err.to_string() }))

            } else {

                // Register updated cameras with go2rtc

                let root_dir = state.skills_manager.root_dir.clone();

                tokio::spawn(async move {

                    crate::go2rtc_manager::Go2RtcManager::register_all_cameras(&root_dir).await;

                });

                Json(

                    serde_json::json!({ "success": true, "message": "Cameras configuration successfully persisted" }),

                )

            }

        }

        Err(err) => Json(serde_json::json!({ "error": err.to_string() })),

    }

}

// Camera PTZ Control

pub async fn camera_ptz(

    State(state): State<Arc<ServerState>>,

    Path(camera_id): Path<String>,

    Json(payload): Json<Value>,

) -> impl IntoResponse {

    let action = payload["action"].as_str().unwrap_or("");

    let pan = payload["pan"].as_f64().unwrap_or(0.0) as f32;

    let tilt = payload["tilt"].as_f64().unwrap_or(0.0) as f32;

    let zoom = payload["zoom"].as_f64().unwrap_or(0.0) as f32;

    let timeout = payload["timeout"].as_u64().unwrap_or(5) as u32;

    let username = payload["username"].as_str().unwrap_or("").to_string();

    let password = payload["password"].as_str().unwrap_or("").to_string();

    let profile_token = payload["profileToken"].as_str().unwrap_or("MainProfile").to_string();

    let preset_token = payload["presetToken"].as_str().unwrap_or("").to_string();

    let creds = services::ptz::PtzCredentials { username, password };

    let camera_url = {

        let cameras_path = state.data_dir.join("cameras.json");

        let cameras: Value = match tokio::fs::read_to_string(&cameras_path).await {

            Ok(text) => serde_json::from_str(&text).unwrap_or(Value::Array(vec![])),

            Err(_) => Value::Array(vec![]),

        };

        cameras

            .as_array()

            .and_then(|arr| arr.iter().find(|c| {

                c.get("id")

                    .and_then(|v| v.as_str())

                    .map(|id| id == camera_id)

                    .unwrap_or(false)

            }))

            .and_then(|c| c.get("url").and_then(|v| v.as_str()))

            .map(|s| s.to_string())

    };

    let Some(camera_url) = camera_url else {

        return Json(serde_json::json!({ "success": false, "error": "camera not found" }));

    };

    let result = match action {

        "continuous" => {

            state

                .ptz

                .continuous_move(&camera_url, &profile_token, pan, tilt, zoom, timeout, &creds)

                .await

        }

        "stop" => {

            state

                .ptz

                .stop(&camera_url, &profile_token, &creds)

                .await

        }

        "preset" => {

            if preset_token.is_empty() {

                Err("presetToken is required for preset action".to_string())

            } else {

                state

                    .ptz

                    .goto_preset(&camera_url, &profile_token, &preset_token, &creds)

                    .await

            }

        }

        other => Err(format!("unsupported ptz action '{}'", other)),

    };

    match result {

        Ok(()) => Json(serde_json::json!({ "success": true })),

        Err(err) => {

            log::warn!("[PTZ] {} {} failed: {}", camera_id, action, err);

            Json(serde_json::json!({ "success": false, "error": err }))

        }

    }

}
