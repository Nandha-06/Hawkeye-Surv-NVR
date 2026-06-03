use crate::server::settings::load_settings;
use crate::server::state::ServerState;
use axum::extract::ws::Message;
use rusqlite::Connection;
use serde_json::Value;
use std::sync::Arc;
pub async fn handle_agent_chat(
    state: Arc<ServerState>,
    user_message: String,
    history: Vec<Value>,
    chat_id: String,
    tx_ws: tokio::sync::mpsc::Sender<Message>,
) {
    // Helper to extract search keywords
    fn extract_search_keywords(msg: &str) -> Vec<String> {
        let stopwords = [
            "show",
            "when",
            "what",
            "where",
            "with",
            "from",
            "this",
            "that",
            "there",
            "then",
            "their",
            "them",
            "some",
            "were",
            "have",
            "here",
            "been",
            "into",
            "your",
            "only",
            "about",
            "would",
            "could",
            "should",
            "will",
            "does",
            "did",
            "anyone",
            "walk",
            "detected",
            "recently",
            "give",
            "summary",
            "activity",
            "snapshots",
            "events",
        ];
        msg.split(|c: char| !c.is_alphanumeric())
            .map(|w| w.to_lowercase().trim().to_string())
            .filter(|w| w.len() > 2 && !stopwords.contains(&w.as_str()))
            .collect()
    }

    // 1. Query sqlite events (scoped synchrony to be Send + Sync safe across await)
    struct EventInfo {
        camera_id: String,
        label: String,
        confidence: f64,
        timestamp: String,
        severity: String,
    }

    struct SearchResult {
        id: String,
        camera_id: String,
        label: String,
        confidence: f64,
        timestamp: String,
        severity: String,
        recording_id: Option<String>,
    }

    let db_path = state
        .skills_manager
        .root_dir
        .join(".data")
        .join("hawkeye.db");

    let mut db_events = Vec::new();
    let mut search_results = Vec::new();

    if let Ok(conn) = Connection::open(&db_path) {
        // Query recent 5 events
        let query = "SELECT camera_id, label, confidence, timestamp, severity FROM events ORDER BY timestamp DESC LIMIT 5";
        if let Ok(mut stmt) = conn.prepare(query) {
            let rows = stmt.query_map([], |row| {
                Ok(EventInfo {
                    camera_id: row.get(0)?,
                    label: row.get(1)?,
                    confidence: row.get(2)?,
                    timestamp: row.get(3)?,
                    severity: row.get(4)?,
                })
            });
            if let Ok(r) = rows {
                for item in r.flatten() {
                    db_events.push(item);
                }
            }
        }

        // Query historical matching events using keyword search
        let keywords = extract_search_keywords(&user_message);
        if !keywords.is_empty() {
            let mut search_query =
                "SELECT id, camera_id, label, confidence, timestamp, severity FROM events WHERE "
                    .to_string();
            let mut conditions = Vec::new();
            for _ in &keywords {
                conditions.push("LOWER(label) LIKE ?");
            }
            search_query.push_str(&conditions.join(" OR "));
            search_query.push_str(" ORDER BY timestamp DESC LIMIT 5");

            if let Ok(mut stmt) = conn.prepare(&search_query) {
                let mut params = Vec::new();
                for kw in &keywords {
                    params.push(format!("%{}%", kw));
                }

                let params_ref: Vec<&dyn rusqlite::ToSql> =
                    params.iter().map(|s| s as &dyn rusqlite::ToSql).collect();

                let rows = stmt.query_map(&params_ref[..], |row| {
                    Ok((
                        row.get::<_, String>(0)?,
                        row.get::<_, String>(1)?,
                        row.get::<_, String>(2)?,
                        row.get::<_, f64>(3)?,
                        row.get::<_, String>(4)?,
                        row.get::<_, String>(5)?,
                    ))
                });

                if let Ok(r) = rows {
                    for item in r.flatten() {
                        let (id, camera_id, label, confidence, timestamp, severity) = item;

                        // Query recording segment ID matching or closest to the event timestamp
                        let rec_id: Option<String> = conn.query_row(
                            "SELECT id FROM recordings WHERE camera_id = ?1 AND start_time <= ?2 AND end_time >= ?2 LIMIT 1",
                            rusqlite::params![camera_id, timestamp],
                            |row| row.get(0)
                        ).or_else(|_| {
                            conn.query_row(
                                "SELECT id FROM recordings WHERE camera_id = ?1 ORDER BY ABS(strftime('%s', start_time) - strftime('%s', ?2)) ASC LIMIT 1",
                                rusqlite::params![camera_id, timestamp],
                                |row| row.get(0)
                            )
                        }).ok();

                        search_results.push(SearchResult {
                            id,
                            camera_id,
                            label,
                            confidence,
                            timestamp,
                            severity,
                            recording_id: rec_id,
                        });
                    }
                }
            }
        }
    }

    let mut event_logs = String::new();
    let mut event_table_rows = String::new();
    for event in &db_events {
        event_logs.push_str(&format!(
            "- [{}] Camera: {}, Detection: {} ({:.1}% confidence), Severity: {}\n",
            event.timestamp,
            event.camera_id,
            event.label,
            event.confidence * 100.0,
            event.severity
        ));
        event_table_rows.push_str(&format!(
            "| {} | {} | {} | {:.1}% | {} |\n",
            event.timestamp,
            event.camera_id,
            event.label,
            event.confidence * 100.0,
            event.severity
        ));
    }

    if event_logs.is_empty() {
        event_logs = "No recent security events detected in the database.".to_string();
        event_table_rows = "| N/A | No events found | N/A | N/A | N/A |\n".to_string();
    }

    let mut search_logs = String::new();
    for res in &search_results {
        let rec_link = if let Some(ref r_id) = res.recording_id {
            format!("/api/v1/recordings/vod/segment/{}", r_id)
        } else {
            "No recording segment found".to_string()
        };
        search_logs.push_str(&format!(
            "- Event ID: {}, Time: {}, Camera: {}, Description: {} ({:.1}% confidence), Severity: {}, Snapshot API: /api/v1/events/{}/snapshot, Playback API: {}\n",
            res.id, res.timestamp, res.camera_id, res.label, res.confidence * 100.0, res.severity, res.id, rec_link
        ));
    }

    // 2. Resolve configured LLM settings
    let settings = load_settings(&state.data_dir);
    let active_inference = &settings["activeInference"];
    let llm_config = &active_inference["llm"];

    let inference_type = llm_config["type"].as_str().unwrap_or("local-engine");

    let is_local;
    let target_endpoint;
    let engine_or_provider;
    let model_id;
    let mut api_key = String::new();
    let mut provider_id = String::new();

    if inference_type == "cloud-provider" {
        is_local = false;
        provider_id = llm_config["provider"].as_str().unwrap_or("").to_string();
        model_id = llm_config["cloudModelId"]
            .as_str()
            .unwrap_or("gpt-4o-mini")
            .to_string();

        let provider_info = &settings["providers"][&provider_id];
        let mut key = provider_info["apiKey"]
            .as_str()
            .unwrap_or("")
            .trim()
            .to_string();
        let mut endpoint = provider_info["baseUrl"]
            .as_str()
            .unwrap_or("")
            .trim()
            .to_string();
        engine_or_provider = provider_id.clone();

        if key.is_empty() {
            key = llm_config["apiKey"]
                .as_str()
                .unwrap_or("")
                .trim()
                .to_string();
        }
        if endpoint.is_empty() {
            endpoint = llm_config["baseUrl"]
                .as_str()
                .unwrap_or("")
                .trim()
                .to_string();
        }
        api_key = key;
        target_endpoint = endpoint;
    } else {
        is_local = true;
        let engine_id = llm_config["engineId"]
            .as_str()
            .unwrap_or("llama-cpp")
            .to_string();
        let port = llm_config["port"].as_u64().unwrap_or(5411);
        model_id = llm_config["modelId"]
            .as_str()
            .unwrap_or("local-model")
            .to_string();

        target_endpoint = format!("http://127.0.0.1:{}/v1/chat/completions", port);
        engine_or_provider = engine_id;
    }

    // 3. Compile prompt & messages
    let system_prompt = format!(
        "You are Hawkeye Security Co-pilot, a helpful AI assistant for the DeepCamera/Hawkeye NVR security system.\n\
        You have direct access to the live system logs and sqlite databases.\n\n\
        Here is a list of the most recent 5 camera security events from the system database:\n\
        {}\n\n\
        Here are matching events found from a historical search over the entire database for the user's query:\n\
        {}\n\n\
        Answer the user's questions about security, cameras, system stats, or camera events using this telemetry where relevant.\n\
        If a matching event from the history is found, you MUST provide a snapshot image and a playback link to the user in your response.\n\
        - Use this syntax to embed the snapshot: ![Snapshot Description](snapshot_api_url)\n\
        - Use this syntax to link the playback: [Watch Recording Segment](playback_api_url)\n\
        Keep your answers concise, alert, and helpful.",
        event_logs,
        if search_logs.is_empty() { "No matching historical events found for the query.".to_string() } else { search_logs }
    );

    let is_anthropic =
        !is_local && (provider_id == "anthropic" || target_endpoint.contains("api.anthropic.com"));

    let client = reqwest::Client::builder()
        .timeout(std::time::Duration::from_secs(30))
        .build()
        .unwrap_or_default();

    let mut post_url = target_endpoint.clone();
    if !is_local {
        if post_url.is_empty() {
            post_url = match provider_id.as_str() {
                "openai" => "https://api.openai.com/v1/chat/completions".to_string(),
                "anthropic" => "https://api.anthropic.com/v1/messages".to_string(),
                "gemini" => {
                    "https://generativelanguage.googleapis.com/v1beta/chat/completions".to_string()
                }
                "groq" => "https://api.groq.com/openai/v1/chat/completions".to_string(),
                _ => "https://api.openai.com/v1/chat/completions".to_string(),
            };
        } else {
            if !post_url.ends_with("/chat/completions") && !post_url.ends_with("/messages") {
                let base = if post_url.ends_with('/') {
                    post_url.clone()
                } else {
                    format!("{}/", post_url)
                };
                post_url = if is_anthropic {
                    format!("{}messages", base)
                } else {
                    format!("{}chat/completions", base)
                };
            }
        }
    }

    let mut headers = reqwest::header::HeaderMap::new();
    headers.insert(
        reqwest::header::CONTENT_TYPE,
        reqwest::header::HeaderValue::from_static("application/json"),
    );

    if !is_local && !api_key.is_empty() {
        if is_anthropic {
            if let Ok(val) = reqwest::header::HeaderValue::from_str(&api_key) {
                headers.insert("x-api-key", val);
            }
            headers.insert(
                "anthropic-version",
                reqwest::header::HeaderValue::from_static("2023-06-01"),
            );
        } else {
            if let Ok(val) = reqwest::header::HeaderValue::from_str(&format!("Bearer {}", api_key))
            {
                headers.insert(reqwest::header::AUTHORIZATION, val);
            }
            if provider_id == "gemini" || post_url.contains("googleapis.com") {
                if let Ok(val) = reqwest::header::HeaderValue::from_str(&api_key) {
                    headers.insert("x-goog-api-key", val);
                }
            }
        }
    }

    let mut api_messages = Vec::new();
    for msg in history {
        let role = msg["role"].as_str().unwrap_or("user");
        let text = msg["text"].as_str().unwrap_or("");
        let api_role = if role == "agent" { "assistant" } else { "user" };
        api_messages.push(serde_json::json!({
            "role": api_role,
            "content": text
        }));
    }
    api_messages.push(serde_json::json!({
        "role": "user",
        "content": user_message
    }));

    let payload = if is_anthropic {
        serde_json::json!({
            "model": model_id,
            "max_tokens": 1024,
            "system": system_prompt,
            "messages": api_messages
        })
    } else {
        let mut full_messages = vec![serde_json::json!({
            "role": "system",
            "content": system_prompt
        })];
        full_messages.extend(api_messages);

        serde_json::json!({
            "model": model_id,
            "messages": full_messages
        })
    };

    let res = client
        .post(&post_url)
        .headers(headers)
        .json(&payload)
        .send()
        .await;

    let response_text = match res {
        Ok(resp) => {
            let status = resp.status();
            if status.is_success() {
                if let Ok(val) = resp.json::<Value>().await {
                    if is_anthropic {
                        val["content"][0]["text"]
                            .as_str()
                            .map(|s| s.to_string())
                            .unwrap_or_else(|| {
                                "Received empty response from Anthropic API.".to_string()
                            })
                    } else {
                        val["choices"][0]["message"]["content"]
                            .as_str()
                            .map(|s| s.to_string())
                            .unwrap_or_else(|| "Received empty response from API.".to_string())
                    }
                } else {
                    format!(
                        "Failed to parse response JSON from AI service at `{}`.",
                        post_url
                    )
                }
            } else {
                let err_text = resp.text().await.unwrap_or_default();
                format!(
                    "### ⚠️ AI Service Error (HTTP Status: `{}`)\n\n\
                    **Endpoint:** `{}`\n\n\
                    **Error Details:**\n\
                    ```\n\
                    {}\n\
                    ```\n\n\
                    ---\n\n\
                    ### 📊 Fallback Event Log (SQLite DB)\n\n\
                    | Time | Camera | Event Label | Confidence | Severity |\n\
                    | :--- | :--- | :--- | :--- | :--- |\n\
                    {}",
                    status, post_url, err_text, event_table_rows
                )
            }
        }
        Err(err) => {
            format!(
                "### ⚠️ Connection to AI Inference Engine Failed\n\n\
                I was unable to establish a connection to your configured AI inference engine.\n\n\
                **Configured Engine Details:**\n\
                - **Inference Mode:** `{}`\n\
                - **Engine / Provider:** `{}`\n\
                - **Model ID:** `{}`\n\
                - **Target Endpoint:** `{}`\n\n\
                **Reason for Failure:**\n\
                ```\n\
                {}\n\
                ```\n\n\
                ---\n\n\
                ### 🔧 Troubleshooting Steps\n\n\
                1. **If using Local Engine (Ollama / Llama.cpp):**\n\
                   - Ensure your local inference server is running. (For Ollama, run `ollama serve` in a terminal).\n\
                   - Verify the port configured in **System Config -> Model & Inference Settings** matches the running service.\n\
                   - Make sure you have downloaded the model `{}` (e.g. `ollama pull {}`).\n\
                2. **If using Cloud Provider (OpenAI, Gemini, Groq, Anthropic):**\n\
                   - Check if you have entered a valid API Key in **System Config**.\n\
                   - Verify that your network is connected and can reach `{}`.\n\n\
                ---\n\n\
                ### 📊 Fallback: Recent Security Events from SQLite Database\n\n\
                While the AI co-pilot is offline, here are the most recent 5 security events recorded by your cameras:\n\n\
                | Time | Camera | Event Label | Confidence | Severity |\n\
                | :--- | :--- | :--- | :--- | :--- |\n\
                {}",
                inference_type, engine_or_provider, model_id, post_url, err, model_id, model_id, post_url, event_table_rows
            )
        }
    };

    let reply = serde_json::json!({
        "event": "agent_response",
        "chatId": chat_id,
        "message": response_text
    });

    if let Ok(reply_str) = serde_json::to_string(&reply) {
        let _ = tx_ws.send(Message::Text(reply_str)).await;
    }
}
