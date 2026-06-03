use serde_json::Value;
use std::path::Path;

pub fn load_settings(data_dir: &Path) -> Value {
    let settings_path = data_dir.join("settings.json");
    if settings_path.exists() {
        if let Ok(content) = std::fs::read_to_string(&settings_path) {
            if let Ok(val) = serde_json::from_str::<Value>(&content) {
                return val;
            }
        }
    }

    // Default initial settings matching frontend state exactly
    serde_json::json!({
        "providers": {
            "openai": {
                "id": "openai",
                "name": "OpenAI",
                "enabled": false,
                "apiKey": "",
                "baseUrl": "https://api.openai.com/v1"
            },
            "anthropic": {
                "id": "anthropic",
                "name": "Anthropic",
                "enabled": false,
                "apiKey": "",
                "baseUrl": "https://api.anthropic.com/v1"
            },
            "gemini": {
                "id": "gemini",
                "name": "Google Gemini",
                "enabled": false,
                "apiKey": "",
                "baseUrl": "https://generativelanguage.googleapis.com/v1beta"
            },
            "groq": {
                "id": "groq",
                "name": "Groq",
                "enabled": false,
                "apiKey": "",
                "baseUrl": "https://api.groq.com/openai/v1"
            }
        },
        "activeInference": {
            "llm": {
                "type": "local-engine",
                "engineId": "llama-cpp",
                "modelId": null,
                "port": 5411,
                "provider": null,
                "cloudModelId": null
            },
            "vlm": {
                "type": "local-engine",
                "engineId": "llama-cpp",
                "modelId": null,
                "port": 5405,
                "provider": null,
                "cloudModelId": null
            }
        },
        "mqttConfig": {
            "enabled": false,
            "broker": "mqtt://localhost",
            "port": 1883,
            "username": "",
            "password": "",
            "topicPrefix": "hawkeye"
        },
        "localModels": []
    })
}

pub fn save_settings(data_dir: &Path, settings: &Value) -> Result<(), std::io::Error> {
    let settings_path = data_dir.join("settings.json");
    let content = serde_json::to_string_pretty(settings)?;
    std::fs::write(settings_path, content)?;
    Ok(())
}

pub fn list_local_models(data_dir: &Path) -> Vec<Value> {
    let models_dir = data_dir.join("models");
    if !models_dir.exists() {
        let _ = std::fs::create_dir_all(&models_dir);
    }

    let mut list = Vec::new();
    if let Ok(entries) = std::fs::read_dir(&models_dir) {
        for entry in entries.flatten() {
            let path = entry.path();
            if path.is_file() {
                let name = path
                    .file_name()
                    .and_then(|n| n.to_str())
                    .unwrap_or("")
                    .to_string();
                if name.ends_with(".gguf") {
                    if let Ok(metadata) = entry.metadata() {
                        let is_vlm = name.contains("mmproj")
                            || name.contains("vlm")
                            || name.contains("moondream");
                        list.push(serde_json::json!({
                            "name": name,
                            "sizeBytes": metadata.len(),
                            "path": path.to_string_lossy().to_string(),
                            "isVlm": is_vlm
                        }));
                    }
                }
            }
        }
    }
    list
}
