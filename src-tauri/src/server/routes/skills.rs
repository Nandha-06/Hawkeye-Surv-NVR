use crate::server::state::ServerState;
use axum::{extract::{State}, response::{IntoResponse}, Json};

use serde_json::Value;

use std::sync::Arc;

// Start AI Skill Worker

pub async fn start_skill(

    State(state): State<Arc<ServerState>>,

    Json(payload): Json<Value>,

) -> impl IntoResponse {

    let skill_id = payload["skillId"].as_str().unwrap_or("");

    let config = payload["config"].clone();

    if skill_id.is_empty() {

        return Json(serde_json::json!({ "error": "Missing skillId in payload" }));

    }

    match state.skills_manager.start_skill(skill_id, config).await {

        Ok(_) => Json(serde_json::json!({

            "success": true,

            "message": format!("AI Skill '{}' initiated successfully", skill_id)

        })),

        Err(err) => {

            eprintln!("Failed to start skill '{}': {}", skill_id, err);

            Json(serde_json::json!({ "error": err.to_string() }))

        }

    }

}

// Stop AI Skill Worker

pub async fn stop_skill(

    State(state): State<Arc<ServerState>>,

    Json(payload): Json<Value>,

) -> impl IntoResponse {

    let skill_id = payload["skillId"].as_str().unwrap_or("");

    if skill_id.is_empty() {

        return Json(serde_json::json!({ "error": "Missing skillId in payload" }));

    }

    state.skills_manager.stop_skill(skill_id).await;

    Json(serde_json::json!({

        "success": true,

        "message": format!("AI Skill '{}' processes terminated", skill_id)

    }))

}
