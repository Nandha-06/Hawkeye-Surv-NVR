mod routes;
mod ws;
use ws::ws_handler;

use routes::status::{get_status};

use routes::cameras::{get_cameras, save_cameras, camera_ptz};

use routes::skills::{start_skill, stop_skill};

use routes::recorders::{get_recorders, start_recorder, stop_recorder};

use routes::events::{get_events, delete_event, get_event_snapshot};

use routes::identities::{get_identities, save_identity, get_identity_crop};

use routes::recordings::{get_recordings, delete_recording, upload_recording};

use routes::vod::{get_vod_playlist, get_vod_segment, get_vod_thumbnail};

use routes::exports::{get_exports, create_export, download_export};

use crate::recording_manager::RecordingManager;
use crate::services;
use crate::skills_manager::SkillsManager;

use axum::{routing::{delete, get, post}, Router};

use axum::extract::{Request, State};
use axum::middleware::{self, Next};
use axum::response::Response;
use axum::http::StatusCode;

use serde_json::Value;
use std::path::PathBuf;
use std::sync::Arc;

use tokio::sync::{broadcast, RwLock};

mod auth;
mod db;
mod helpers;
mod path_safe;
mod settings;
mod state;

use db::ensure_database;

use state::{ServerState};

async fn check_token(
    State(state): State<Arc<ServerState>>,
    req: Request,
    next: Next,
) -> Result<Response, StatusCode> {
    let provided = req
        .headers()
        .get(auth::TOKEN_HEADER)
        .and_then(|v| v.to_str().ok())
        .map(|s| s.to_string())
        .or_else(|| {
            req.uri()
                .query()
                .and_then(|q| {
                    urlencoding::decode(q)
                        .ok()
                        .and_then(|s| {
                            s.split('&')
                                .filter_map(|kv| kv.split_once('='))
                                .find(|(k, _)| *k == auth::TOKEN_QUERY)
                                .map(|(_, v)| v.to_string())
                        })
                })
        });

    match provided {
        Some(t) if t == state.api_token => Ok(next.run(req).await),
        _ => Err(StatusCode::UNAUTHORIZED),
    }
}

pub async fn start_server(tx: broadcast::Sender<String>) {
    // 1. Resolve the path to the `.data` directory.
    let mut data_dir = PathBuf::from(".data");
    if !data_dir.exists() {
        let parent_data = PathBuf::from("../.data");
        if parent_data.exists() || PathBuf::from("../frontend").exists() {
            data_dir = parent_data;
        }
    }

    // Ensure data folder exists
    if !data_dir.exists() {
        let _ = tokio::fs::create_dir_all(&data_dir).await;
    }

    let api_token = auth::load_or_create_token(&data_dir);
    log::info!("[Server] Local API token loaded (length={})", api_token.len());

    if let Err(err) = ensure_database(&data_dir) {
        eprintln!("Failed to initialize SQLite schema: {}", err);
    }

    // Instantiate RecordingManager and then SkillsManager
    let recording_manager = RecordingManager::new();
    let skills_manager = SkillsManager::new(tx.clone(), recording_manager.clone());

    let mqtt = Arc::new(services::mqtt::MqttManager::new(tx.clone()));
    let hf = Arc::new(services::huggingface::HuggingFaceService::new());
    let ptz = Arc::new(services::ptz::PtzService::new());

    mqtt.subscribe_event_bus(tx.subscribe());

    // Auto-connect MQTT from persisted settings if enabled
    {
        let settings_path = data_dir.join("settings.json");
        if let Ok(text) = tokio::fs::read_to_string(&settings_path).await {
            if let Ok(parsed) = serde_json::from_str::<Value>(&text) {
                if let Some(mqtt_cfg) = parsed.get("mqttConfig") {
                    if let Ok(cfg) =
                        serde_json::from_value::<services::mqtt::MqttConfig>(mqtt_cfg.clone())
                    {
                        if cfg.enabled {
                            let mqtt_clone = mqtt.clone();
                            tokio::spawn(async move {
                                if let Err(e) = mqtt_clone.apply_config(cfg).await {
                                    log::warn!("[MQTT] initial connect failed: {}", e);
                                }
                            });
                        }
                    }
                }
            }
        }
    }

    // Start background threads for storage scavenger and smart retention
    let rec_mgr_clone = recording_manager.clone();
    tokio::spawn(async move {
        rec_mgr_clone.start_workers().await;
    });

    let exports = Arc::new(RwLock::new(Vec::new()));

    let state = Arc::new(ServerState {
        data_dir,
        skills_manager,
        recording_manager,
        tx,
        exports,
        mqtt,
        hf,
        ptz,
        api_token,
    });

    // 2. HTTP router (token-gated) and WebSocket router (auth checked inside handler)
    let http_app = Router::new()
        .route("/api/status", get(get_status))
        .route("/api/v1/cameras", get(get_cameras).post(save_cameras))
        .route("/api/v1/cameras/:camera_id/ptz", post(camera_ptz))
        .route("/api/v1/skills/start", post(start_skill))
        .route("/api/v1/skills/stop", post(stop_skill))
        .route("/api/v1/recorders", get(get_recorders))
        .route("/api/v1/recorders/start", post(start_recorder))
        .route("/api/v1/recorders/stop", post(stop_recorder))
        .route("/api/v1/events", get(get_events).delete(delete_event))
        .route("/api/v1/events/:id/snapshot", get(get_event_snapshot))
        .route(
            "/api/v1/identities",
            get(get_identities).post(save_identity),
        )
        .route("/api/v1/identities/crop", get(get_identity_crop))
        .route(
            "/api/v1/recordings",
            get(get_recordings).post(upload_recording),
        )
        .route("/api/v1/recordings/:id", delete(delete_recording))
        .route("/api/v1/recordings/vod/index.m3u8", get(get_vod_playlist))
        .route("/api/v1/recordings/vod/segment/:id", get(get_vod_segment))
        .route(
            "/api/v1/recordings/vod/thumbnail/:id",
            get(get_vod_thumbnail),
        )
        .route("/api/v1/exports", get(get_exports).post(create_export))
        .route("/api/v1/exports/:id/download", get(download_export))
        .layer(axum::extract::DefaultBodyLimit::max(50 * 1024 * 1024))
        .route_layer(middleware::from_fn_with_state(state.clone(), check_token));

    // 3. Combined app with both routers
    let app = http_app
        .route("/api/ws", get(ws_handler))
        .route("/ws", get(ws_handler))
        .with_state(state);

    // 4. Bind listener to port 8080
    let addr = "127.0.0.1:8080";
    match tokio::net::TcpListener::bind(addr).await {
        Ok(listener) => {
            println!("Embedded Rust Axum Server listening on http://{}", addr);
            if let Err(err) = axum::serve(listener, app).await {
                eprintln!("Axum server run error: {}", err);
            }
        }
        Err(err) => {
            eprintln!("Failed to bind Axum server to {}: {}", addr, err);
        }
    }
}
