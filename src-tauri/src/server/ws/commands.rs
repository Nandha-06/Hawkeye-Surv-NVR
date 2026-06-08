use super::agent::handle_agent_chat;

use crate::server::settings::{list_local_models, load_settings, save_settings};

use crate::server::state::ServerState;
use axum::extract::ws::{Message, WebSocket};

use futures_util::{SinkExt, StreamExt};

use crate::services;

use serde_json::Value;
use std::sync::Arc;
use tokio::io::AsyncWriteExt;

pub async fn handle_socket(socket: WebSocket, state: Arc<ServerState>) {
    let (mut ws_sender, mut ws_receiver) = socket.split();
    let (tx_ws, mut rx_ws) = tokio::sync::mpsc::channel::<Message>(100);
    let mut rx = state.tx.subscribe();

    // Decoupled writer task
    tokio::spawn(async move {
        loop {
            tokio::select! {
                broadcast_msg = rx.recv() => {
                    if let Ok(msg) = broadcast_msg {
                        if ws_sender.send(Message::Text(msg)).await.is_err() {
                            break;
                        }
                    }
                }
                ws_msg = rx_ws.recv() => {
                    if let Some(msg) = ws_msg {
                        if ws_sender.send(msg).await.is_err() {
                            break;
                        }
                    } else {
                        break;
                    }
                }
            }
        }
    });

    // Main command receiver loop
    loop {
        match ws_receiver.next().await {
            Some(Ok(Message::Binary(bin))) => {
                // Format: [4 bytes JSON len, little-endian] + [JSON bytes] + [JPEG bytes]
                if bin.len() >= 4 {
                    let mut len_bytes = [0u8; 4];
                    len_bytes.copy_from_slice(&bin[0..4]);
                    let json_len = u32::from_le_bytes(len_bytes) as usize;
                    
                    if bin.len() >= 4 + json_len {
                        let json_bytes = &bin[4..4 + json_len];
                        let img_bytes = &bin[4 + json_len..];
                        
                        if let Ok(cmd) = serde_json::from_slice::<Value>(json_bytes) {
                            let action = cmd["action"].as_str().unwrap_or("");
                            if action == "feed_frame" {
                                let state_clone = state.clone();
                                let skill_id = cmd["skillId"].as_str().unwrap_or("").to_string();
                                let camera_id = cmd["cameraId"].as_str().unwrap_or("").to_string();
                                let frame_id = cmd["frameId"].as_i64().unwrap_or(0);
                                let timestamp = cmd["timestamp"].as_str().unwrap_or("").to_string();
                                
                                let img_vec = img_bytes.to_vec();
                                
                                tokio::spawn(async move {
                                    let proc_key = format!("{}:{}", skill_id, camera_id);
                                    let active_processes = state_clone.skills_manager.active_processes.read().await;
                                    if let Some(active_proc) = active_processes.get(&proc_key) {
                                        let notify_event = serde_json::json!({
                                            "event": "frame",
                                            "frame_id": frame_id,
                                            "camera_id": camera_id,
                                            "timestamp": timestamp,
                                        });
                                        if let Ok(msg_str) = serde_json::to_string(&notify_event) {
                                            let metadata_bytes = msg_str.as_bytes();
                                            let jl = metadata_bytes.len() as u32;
                                            let il = img_vec.len() as u32;

                                            let mut lock = active_proc.stdin.lock().await;

                                            // Prepare AEGS binary header: Magic (4B) + json_len (4B, Big Endian) + img_len (4B, Big Endian)
                                            let mut header = Vec::with_capacity(12);
                                            header.extend_from_slice(b"AEGS");
                                            header.extend_from_slice(&jl.to_be_bytes());
                                            header.extend_from_slice(&il.to_be_bytes());

                                            let _ = lock.write_all(&header).await;
                                            let _ = lock.write_all(metadata_bytes).await;
                                            let _ = lock.write_all(&img_vec).await;
                                            let _ = lock.flush().await;
                                        }
                                    }
                                });
                            }
                        }
                    }
                }
            }
            Some(Ok(Message::Text(text))) => {
                if let Ok(cmd) = serde_json::from_str::<Value>(&text) {
                    let action = cmd["action"].as_str().unwrap_or("");
                    match action {
                        "list_skills" => {
                            let tx_ws_clone = tx_ws.clone();
                            let state_clone = state.clone();
                            tokio::spawn(async move {
                                if let Ok(skills_catalog) =
                                    state_clone.skills_manager.list_skills().await
                                {
                                    let reply = serde_json::json!({
                                        "event": "skills_list",
                                        "skills": skills_catalog["skills"]
                                    });
                                    if let Ok(reply_str) = serde_json::to_string(&reply) {
                                        let _ = tx_ws_clone.send(Message::Text(reply_str)).await;
                                    }
                                }
                            });
                        }
                        "start_skill" => {
                            let skill_id = cmd["skillId"].as_str().unwrap_or("").to_string();
                            let config = cmd["config"].clone();
                            if !skill_id.is_empty() {
                                let state_clone = state.clone();
                                tokio::spawn(async move {
                                    match state_clone
                                        .skills_manager
                                        .start_skill(&skill_id, config)
                                        .await
                                    {
                                        Ok(_) => {
                                            let reply = serde_json::json!({
                                                "event": "ready",
                                                "skillId": skill_id
                                            });
                                            if let Ok(reply_str) = serde_json::to_string(&reply) {
                                                let _ = state_clone.tx.send(reply_str);
                                            }
                                        }
                                        Err(err) => {
                                            log::error!("Failed to start skill {}: {}", skill_id, err);
                                            let reply = serde_json::json!({
                                                "event": "error",
                                                "skillId": skill_id,
                                                "message": format!("Failed to start skill: {}", err)
                                            });
                                            if let Ok(reply_str) = serde_json::to_string(&reply) {
                                                let _ = state_clone.tx.send(reply_str);
                                            }
                                        }
                                    }
                                });
                            }
                        }
                        "stop_skill" => {
                            let skill_id = cmd["skillId"].as_str().unwrap_or("").to_string();
                            if !skill_id.is_empty() {
                                let state_clone = state.clone();
                                tokio::spawn(async move {
                                    state_clone.skills_manager.stop_skill(&skill_id).await;
                                    let reply = serde_json::json!({
                                        "event": "stopped",
                                        "skillId": skill_id
                                    });
                                    if let Ok(reply_str) = serde_json::to_string(&reply) {
                                        let _ = state_clone.tx.send(reply_str);
                                    }
                                });
                            }
                        }
                        "get_settings" => {
                            let tx_ws_clone = tx_ws.clone();
                            let state_clone = state.clone();
                            tokio::spawn(async move {
                                let settings = load_settings(&state_clone.data_dir);
                                let local_models = list_local_models(&state_clone.data_dir);
                                let reply = serde_json::json!({
                                    "event": "settings_data",
                                    "providers": settings["providers"],
                                    "activeInference": settings["activeInference"],
                                    "localModels": local_models,
                                    "mqttConfig": settings["mqttConfig"]
                                });
                                if let Ok(reply_str) = serde_json::to_string(&reply) {
                                    let _ = tx_ws_clone.send(Message::Text(reply_str)).await;
                                }
                            });
                        }
                        "save_settings" => {
                            let tx_ws_clone = tx_ws.clone();
                            let state_clone = state.clone();
                            let providers = cmd["providers"].clone();
                            let active_inference = cmd["activeInference"].clone();
                            let mqtt_config = cmd["mqttConfig"].clone();

                            tokio::spawn(async move {
                                // Validate MQTT config before persisting
                                let mut mqtt_to_save = mqtt_config.clone();
                                if let Ok(cfg) =
                                    serde_json::from_value::<services::mqtt::MqttConfig>(mqtt_config.clone())
                                {
                                    match services::mqtt::validate_config(cfg) {
                                        Ok(sanitized) => {
                                            if let Ok(v) = serde_json::to_value(&sanitized) {
                                                mqtt_to_save = v;
                                            }
                                        }
                                        Err(e) => {
                                            log::warn!("[save_settings] rejected MQTT config: {}", e);
                                            let _ = tx_ws_clone
                                                .send(Message::Text(
                                                    serde_json::to_string(&serde_json::json!({
                                                        "event": "settings_error",
                                                        "error": format!("Invalid MQTT config: {}", e),
                                                    }))
                                                    .unwrap_or_default(),
                                                ))
                                                .await;
                                            return;
                                        }
                                    }
                                }

                                let saved_val = serde_json::json!({
                                    "providers": providers,
                                    "activeInference": active_inference,
                                    "mqttConfig": mqtt_to_save
                                });
                                let _ = save_settings(&state_clone.data_dir, &saved_val);

                                // Reconnect MQTT if config changed
                                if let Some(mqtt_cfg) = saved_val.get("mqttConfig") {
                                    if let Ok(cfg) =
                                        serde_json::from_value::<services::mqtt::MqttConfig>(
                                            mqtt_cfg.clone(),
                                        )
                                    {
                                        if let Err(e) = state_clone.mqtt.apply_config(cfg).await {
                                            log::warn!("[MQTT] apply_config failed: {}", e);
                                            let _ = tx_ws_clone
                                                .send(Message::Text(
                                                    serde_json::to_string(&serde_json::json!({
                                                        "event": "mqtt_error",
                                                        "error": e,
                                                    }))
                                                    .unwrap_or_default(),
                                                ))
                                                .await;
                                        }
                                    }
                                }

                                let reply = serde_json::json!({
                                    "event": "settings_saved",
                                    "providers": saved_val["providers"],
                                    "activeInference": saved_val["activeInference"],
                                    "mqttConfig": saved_val["mqttConfig"]
                                });
                                if let Ok(reply_str) = serde_json::to_string(&reply) {
                                    let _ = tx_ws_clone.send(Message::Text(reply_str)).await;
                                }
                            });
                        }
                        "list_local_models" => {
                            let tx_ws_clone = tx_ws.clone();
                            let state_clone = state.clone();
                            tokio::spawn(async move {
                                let local_models = list_local_models(&state_clone.data_dir);
                                let reply = serde_json::json!({
                                    "event": "local_models_list",
                                    "localModels": local_models
                                });
                                if let Ok(reply_str) = serde_json::to_string(&reply) {
                                    let _ = tx_ws_clone.send(Message::Text(reply_str)).await;
                                }
                            });
                        }
                        "delete_model" => {
                            let tx_ws_clone = tx_ws.clone();
                            let state_clone = state.clone();
                            let filename = cmd["filename"].as_str().unwrap_or("").to_string();

                            tokio::spawn(async move {
                                let models_dir = state_clone.data_dir.join("models");
                                let success = if !filename.is_empty() {
                                    if let Some(safe_path) =
                                        crate::server::path_safe::safe_join_under(&models_dir, &filename)
                                    {
                                        std::fs::remove_file(safe_path).is_ok()
                                    } else {
                                        log::warn!("[delete_model] rejected unsafe filename: {}", filename);
                                        false
                                    }
                                } else {
                                    false
                                };
                                let local_models = list_local_models(&state_clone.data_dir);
                                let reply = serde_json::json!({
                                    "event": "model_deleted",
                                    "filename": filename,
                                    "success": success,
                                    "localModels": local_models
                                });
                                if let Ok(reply_str) = serde_json::to_string(&reply) {
                                    let _ = tx_ws_clone.send(Message::Text(reply_str)).await;
                                }
                            });
                        }
                        "deploy_skill" => {
                            let skill_id = cmd["skillId"].as_str().unwrap_or("").to_string();
                            let tx_ws_clone = tx_ws.clone();
                            let state_clone = state.clone();
                            tokio::spawn(async move {
                                if let Err(e) = state_clone
                                    .skills_manager
                                    .deploy_skill(&skill_id, tx_ws_clone.clone())
                                    .await
                                {
                                    let _ = tx_ws_clone
                                        .send(Message::Text(
                                            serde_json::to_string(&serde_json::json!({
                                                "event": "deploy_progress",
                                                "skillId": skill_id,
                                                "stage": "error",
                                                "message": format!("Deployment failed: {}", e)
                                            }))
                                            .unwrap(),
                                        ))
                                        .await;
                                }
                            });
                        }
                        "test_provider" => {
                            let provider_id = cmd["providerId"].as_str().unwrap_or("").to_string();
                            let api_key = cmd["apiKey"].as_str().unwrap_or("").trim().to_string();
                            let base_url = cmd["baseUrl"].as_str().unwrap_or("").trim().to_string();
                            let tx_ws_clone = tx_ws.clone();

                            tokio::spawn(async move {
                                if api_key.is_empty() {
                                    let _ = tx_ws_clone
                                        .send(Message::Text(
                                            serde_json::to_string(&serde_json::json!({
                                                "event": "test_result",
                                                "providerId": provider_id,
                                                "success": false,
                                                "error": "API Key is empty."
                                            }))
                                            .unwrap(),
                                        ))
                                        .await;
                                    return;
                                }

                                let client = reqwest::Client::builder()
                                    .timeout(std::time::Duration::from_secs(8))
                                    .build()
                                    .unwrap_or_default();

                                let url = if base_url.is_empty() {
                                    match provider_id.as_str() {
                                        "openai" => "https://api.openai.com/v1/chat/completions".to_string(),
                                        "anthropic" => "https://api.anthropic.com/v1/messages".to_string(),
                                        "gemini" => "https://generativelanguage.googleapis.com/v1beta/chat/completions".to_string(),
                                        "groq" => "https://api.groq.com/openai/v1/chat/completions".to_string(),
                                        _ => "https://api.openai.com/v1/chat/completions".to_string(),
                                    }
                                } else {
                                    if base_url.ends_with('/') {
                                        if provider_id == "anthropic" {
                                            format!("{}messages", base_url)
                                        } else {
                                            format!("{}chat/completions", base_url)
                                        }
                                    } else {
                                        if provider_id == "anthropic" {
                                            format!("{}/messages", base_url)
                                        } else {
                                            format!("{}/chat/completions", base_url)
                                        }
                                    }
                                };

                                let model = match provider_id.as_str() {
                                    "openai" => "gpt-4o-mini",
                                    "anthropic" => "claude-3-5-sonnet-20241022",
                                    "gemini" => "gemini-1.5-flash",
                                    "groq" => "llama3-8b-8192",
                                    _ => "gpt-4o-mini",
                                };

                                let mut req = client.post(&url);
                                if provider_id == "anthropic" {
                                    req = req
                                        .header("x-api-key", &api_key)
                                        .header("anthropic-version", "2023-06-01")
                                        .json(&serde_json::json!({
                                            "model": model,
                                            "max_tokens": 1,
                                            "messages": [{"role": "user", "content": "ping"}]
                                        }));
                                } else {
                                    let auth_val = format!("Bearer {}", api_key);
                                    req = req.header("Authorization", auth_val);
                                    if provider_id == "gemini" {
                                        req = req.header("x-goog-api-key", &api_key);
                                    }
                                    req = req.json(&serde_json::json!({
                                        "model": model,
                                        "max_tokens": 1,
                                        "messages": [{"role": "user", "content": "ping"}]
                                    }));
                                }

                                match req.send().await {
                                    Ok(resp) => {
                                        let status = resp.status();
                                        if status.is_success() {
                                            let _ = tx_ws_clone.send(Message::Text(serde_json::to_string(&serde_json::json!({
                                                "event": "test_result",
                                                "providerId": provider_id,
                                                "success": true,
                                                "response": "Connection successful! API responds correctly."
                                            })).unwrap())).await;
                                        } else {
                                            let err_text = resp.text().await.unwrap_or_default();
                                            let _ = tx_ws_clone.send(Message::Text(serde_json::to_string(&serde_json::json!({
                                                "event": "test_result",
                                                "providerId": provider_id,
                                                "success": false,
                                                "error": format!("API returned HTTP error {}: {}", status, err_text)
                                            })).unwrap())).await;
                                        }
                                    }
                                    Err(e) => {
                                        let _ = tx_ws_clone.send(Message::Text(serde_json::to_string(&serde_json::json!({
                                            "event": "test_result",
                                            "providerId": provider_id,
                                            "success": false,
                                            "error": format!("Network connection failed: {}", e)
                                        })).unwrap())).await;
                                    }
                                }
                            });
                        }
                        "search_huggingface" => {
                            let query = cmd["query"].as_str().unwrap_or("").to_string();
                            let limit = cmd["limit"].as_u64().unwrap_or(20) as u32;
                            let tx_ws_clone = tx_ws.clone();
                            let hf_clone = state.hf.clone();
                            tokio::spawn(async move {
                                let reply = match hf_clone.search(&query, limit).await {
                                    Ok(models) => serde_json::json!({
                                        "event": "hf_search_results",
                                        "models": models,
                                        "query": query,
                                    }),
                                    Err(err) => serde_json::json!({
                                        "event": "hf_search_results",
                                        "models": Vec::<Value>::new(),
                                        "query": query,
                                        "error": err,
                                    }),
                                };
                                let _ = tx_ws_clone
                                    .send(Message::Text(serde_json::to_string(&reply).unwrap()))
                                    .await;
                            });
                        }
                        "get_repo_files" => {
                            let repo_id = cmd["repoId"].as_str().unwrap_or("").to_string();
                            let revision = cmd["revision"].as_str().unwrap_or("main").to_string();
                            let tx_ws_clone = tx_ws.clone();
                            let hf_clone = state.hf.clone();
                            tokio::spawn(async move {
                                let reply = match hf_clone
                                    .list_repo_files(&repo_id, &revision)
                                    .await
                                {
                                    Ok(files) => serde_json::json!({
                                        "event": "repo_files",
                                        "repoId": repo_id,
                                        "files": files,
                                    }),
                                    Err(err) => serde_json::json!({
                                        "event": "repo_files",
                                        "repoId": repo_id,
                                        "files": Vec::<String>::new(),
                                        "error": err,
                                    }),
                                };
                                let _ = tx_ws_clone
                                    .send(Message::Text(serde_json::to_string(&reply).unwrap()))
                                    .await;
                            });
                        }
                        "download_model" => {
                            let download_id = cmd["downloadId"].as_str().unwrap_or("").to_string();
                            let repo_id = cmd["repoId"].as_str().unwrap_or("").to_string();
                            let filename = cmd["filename"].as_str().unwrap_or("").to_string();
                            let revision = cmd["revision"].as_str().unwrap_or("main").to_string();
                            let tx_ws_clone = tx_ws.clone();
                            let data_dir_clone = state.data_dir.clone();
                            let hf_clone = state.hf.clone();
                            let event_tx = state.tx.clone();

                            tokio::spawn(async move {
                                let models_dir = data_dir_clone.join("models");
                                if !models_dir.exists() {
                                    let _ = tokio::fs::create_dir_all(&models_dir).await;
                                }
                                match hf_clone
                                    .download(
                                        &repo_id,
                                        &filename,
                                        &revision,
                                        &models_dir,
                                        &download_id,
                                        &event_tx,
                                    )
                                    .await
                                {
                                    Ok(path) => {
                                        let reply = serde_json::json!({
                                            "event": "download_complete",
                                            "downloadId": download_id,
                                            "path": path.to_string_lossy(),
                                            "status": "completed",
                                        });
                                        let _ = tx_ws_clone
                                            .send(Message::Text(
                                                serde_json::to_string(&reply).unwrap(),
                                            ))
                                            .await;
                                    }
                                    Err(err) => {
                                        let reply = serde_json::json!({
                                            "event": "download_progress",
                                            "downloadId": download_id,
                                            "status": "failed",
                                            "error": err,
                                        });
                                        let _ = tx_ws_clone
                                            .send(Message::Text(
                                                serde_json::to_string(&reply).unwrap(),
                                            ))
                                            .await;
                                    }
                                }
                            });
                        }
                        "agent_chat" => {
                            let tx_ws_clone = tx_ws.clone();
                            let state_clone = state.clone();
                            let message = cmd["message"].as_str().unwrap_or("").to_string();
                            let history = cmd["history"].as_array().cloned().unwrap_or_default();
                            let chat_id = cmd["chatId"].as_str().unwrap_or("").to_string();

                            tokio::spawn(async move {
                                handle_agent_chat(
                                    state_clone,
                                    message,
                                    history,
                                    chat_id,
                                    tx_ws_clone,
                                )
                                .await;
                            });
                        }
                        "feed_frame" => {
                            let state_clone = state.clone();
                            let skill_id = cmd["skillId"].as_str().unwrap_or("").to_string();
                            let camera_id = cmd["cameraId"].as_str().unwrap_or("").to_string();
                            let frame_id = cmd["frameId"].as_i64().unwrap_or(0);
                            let frame_base64 = cmd["frame"].as_str().unwrap_or("").to_string();
                            let timestamp = cmd["timestamp"].as_str().unwrap_or("").to_string();

                            tokio::spawn(async move {
                                if !frame_base64.is_empty() {
                                    let base64_str = if let Some(pos) = frame_base64.find(',') {
                                        &frame_base64[pos + 1..]
                                    } else {
                                        &frame_base64
                                    };

                                    use base64::{Engine as _, engine::general_purpose};

                                    if let Ok(img_bytes) = general_purpose::STANDARD.decode(base64_str) {
                                        let proc_key = format!("{}:{}", skill_id, camera_id);
                                        let active_processes = state_clone.skills_manager.active_processes.read().await;
                                        if let Some(active_proc) = active_processes.get(&proc_key) {
                                            let notify_event = serde_json::json!({
                                                "event": "frame",
                                                "frame_id": frame_id,
                                                "camera_id": camera_id,
                                                "timestamp": timestamp,
                                            });
                                            if let Ok(msg_str) = serde_json::to_string(&notify_event) {
                                                let metadata_bytes = msg_str.as_bytes();
                                                let json_len = metadata_bytes.len() as u32;
                                                let img_len = img_bytes.len() as u32;

                                                let mut lock = active_proc.stdin.lock().await;

                                                // Prepare AEGS binary header: Magic (4B) + json_len (4B, Big Endian) + img_len (4B, Big Endian)
                                                let mut header = Vec::with_capacity(12);
                                                header.extend_from_slice(b"AEGS");
                                                header.extend_from_slice(&json_len.to_be_bytes());
                                                header.extend_from_slice(&img_len.to_be_bytes());

                                                let _ = lock.write_all(&header).await;
                                                let _ = lock.write_all(metadata_bytes).await;
                                                let _ = lock.write_all(&img_bytes).await;
                                                let _ = lock.flush().await;
                                            }
                                        }
                                    }
                                }
                            });
                        }
                        _ => {
                            println!("[WebSocket] Received unhandled command action: {}", action);
                        }
                    }
                }
            }
            Some(Err(_)) | None => {
                break;
            }
            _ => {}
        }
    }
}
