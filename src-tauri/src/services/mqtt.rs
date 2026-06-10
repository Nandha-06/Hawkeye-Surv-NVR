use rumqttc::{AsyncClient, Event, Incoming, MqttOptions, QoS};
use serde::{Deserialize, Serialize};
use serde_json::Value;
use std::sync::Arc;
use std::time::Duration;
use tokio::sync::{RwLock, broadcast};

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct MqttConfig {
    #[serde(default)]
    pub enabled: bool,
    pub broker: String,
    pub port: u16,
    #[serde(default)]
    pub username: String,
    #[serde(default)]
    pub password: String,
    #[serde(default = "default_topic_prefix")]
    pub topic_prefix: String,
    #[serde(default)]
    pub client_id: String,
}

fn default_topic_prefix() -> String {
    "hawkeye".to_string()
}

impl Default for MqttConfig {
    fn default() -> Self {
        Self {
            enabled: false,
            broker: "localhost".to_string(),
            port: 1883,
            username: String::new(),
            password: String::new(),
            topic_prefix: default_topic_prefix(),
            client_id: format!("hawkeye-{}", std::process::id()),
        }
    }
}

/// Validate user-supplied MQTT config before accepting it. Returns the
/// sanitized form (with `topic_prefix` clamped to safe characters and
/// `broker` validated as a host/host:port string).
pub fn validate_config(mut cfg: MqttConfig) -> Result<MqttConfig, String> {
    let mut broker = cfg.broker.trim().to_string();
    if let Some(stripped) = broker.strip_prefix("mqtt://") {
        broker = stripped.to_string();
    }
    if let Some(stripped) = broker.strip_prefix("mqtts://") {
        broker = stripped.to_string();
    }
    if let Some(stripped) = broker.strip_prefix("tcp://") {
        broker = stripped.to_string();
    }
    broker = broker.trim_end_matches('/').to_string();

    if broker.is_empty() {
        return Err("MQTT broker must not be empty".to_string());
    }
    if broker.len() > 253 {
        return Err("MQTT broker hostname is too long".to_string());
    }
    if broker.contains(' ') || broker.contains('\0') || broker.contains('\n') || broker.contains('\r') {
        return Err("MQTT broker contains invalid characters".to_string());
    }
    if broker.contains('/') {
        return Err("MQTT broker must be a host[:port] (no path)".to_string());
    }
    if cfg.port == 0 {
        return Err("MQTT port must be non-zero".to_string());
    }
    cfg.broker = broker;

    let mut prefix = cfg.topic_prefix.trim().to_string();
    if prefix.is_empty() {
        prefix = default_topic_prefix();
    }
    if prefix.len() > 64 {
        return Err("MQTT topic_prefix is too long (max 64)".to_string());
    }
    let safe_chars: std::collections::HashSet<char> =
        "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_-/."
            .chars()
            .collect();
    if !prefix.chars().all(|c| safe_chars.contains(&c)) {
        return Err("MQTT topic_prefix may only contain [A-Za-z0-9_/.-]".to_string());
    }
    if prefix.contains('#') || prefix.contains('+') {
        return Err("MQTT topic_prefix must not contain wildcards (+/#)".to_string());
    }
    if prefix.contains("//") || prefix.starts_with('/') || prefix.ends_with('/') {
        return Err("MQTT topic_prefix must not start/end with '/' or contain '//'".to_string());
    }
    cfg.topic_prefix = prefix;

    if cfg.client_id.trim().is_empty() {
        cfg.client_id = format!("hawkeye-{}", std::process::id());
    }
    if cfg.client_id.len() > 64 {
        return Err("MQTT client_id is too long (max 64)".to_string());
    }
    if !cfg
        .client_id
        .chars()
        .all(|c| c.is_ascii_alphanumeric() || c == '-' || c == '_')
    {
        return Err("MQTT client_id may only contain [A-Za-z0-9_-]".to_string());
    }

    Ok(cfg)
}

#[derive(Clone)]
pub struct MqttManager {
    config: Arc<RwLock<MqttConfig>>,
    client: Arc<RwLock<Option<AsyncClient>>>,
    loop_handle: Arc<RwLock<Option<tokio::task::JoinHandle<()>>>>,
    event_tx: broadcast::Sender<String>,
}

impl MqttManager {
    pub fn new(event_tx: broadcast::Sender<String>) -> Self {
        Self {
            config: Arc::new(RwLock::new(MqttConfig::default())),
            client: Arc::new(RwLock::new(None)),
            loop_handle: Arc::new(RwLock::new(None)),
            event_tx,
        }
    }

    #[allow(dead_code)]
    pub async fn config(&self) -> MqttConfig {
        self.config.read().await.clone()
    }

    #[allow(dead_code)]
    pub async fn is_connected(&self) -> bool {
        self.client.read().await.is_some()
    }

    pub async fn apply_config(&self, new_config: MqttConfig) -> Result<(), String> {
        let validated = validate_config(new_config)?;
        self.disconnect().await;
        if validated.enabled {
            self.connect(validated.clone()).await?;
        } else {
            *self.config.write().await = validated;
        }
        Ok(())
    }

    pub async fn connect(&self, config: MqttConfig) -> Result<(), String> {
        let config = validate_config(config)?;
        let mut opts = MqttOptions::new(&config.client_id, &config.broker, config.port);
        opts.set_keep_alive(Duration::from_secs(30));
        opts.set_clean_session(true);
        if !config.username.is_empty() {
            opts.set_credentials(&config.username, &config.password);
        }
        opts.set_max_packet_size(1024 * 1024, 1024 * 1024);

        let (client, mut eventloop) = AsyncClient::new(opts, 32);
        let topic_prefix = config.topic_prefix.clone();
        let subscribe_topic = format!("{}/+/+/set", topic_prefix);
        let event_tx = self.event_tx.clone();
        let loop_client = client.clone();

        let handle = tokio::spawn(async move {
            let mut backoff_ms = 500u64;
            loop {
                match eventloop.poll().await {
                    Ok(Event::Incoming(Incoming::ConnAck(_))) => {
                        log::info!("[MQTT] connected; subscribing to {}", subscribe_topic);
                        if let Err(e) =
                            loop_client.subscribe(&subscribe_topic, QoS::AtMostOnce).await
                        {
                            log::warn!("[MQTT] subscribe failed: {}", e);
                        }
                        backoff_ms = 500;
                    }
                    Ok(Event::Incoming(Incoming::Publish(p))) => {
                        let topic = p.topic.clone();
                        let payload = String::from_utf8_lossy(&p.payload).to_string();
                        log::debug!("[MQTT] received {}: {}", topic, payload);
                        let _ = event_tx.send(
                            serde_json::to_string(&serde_json::json!({
                                "event": "mqtt_command",
                                "topic": topic,
                                "payload": payload,
                            }))
                            .unwrap_or_default(),
                        );
                    }
                    Ok(_) => {}
                    Err(e) => {
                        log::warn!("[MQTT] eventloop error: {} (backoff {}ms)", e, backoff_ms);
                        tokio::time::sleep(Duration::from_millis(backoff_ms)).await;
                        backoff_ms = (backoff_ms * 2).min(15_000);
                    }
                }
            }
        });

        *self.client.write().await = Some(client);
        *self.loop_handle.write().await = Some(handle);
        *self.config.write().await = config;
        Ok(())
    }

    pub async fn disconnect(&self) {
        if let Some(handle) = self.loop_handle.write().await.take() {
            handle.abort();
        }
        let mut guard = self.client.write().await;
        if let Some(client) = guard.take() {
            let _ = client.disconnect().await;
        }
    }

    pub async fn publish_event(&self, camera_id: &str, event: &str, payload: &Value) {
        let guard = self.client.read().await;
        let Some(client) = guard.as_ref() else {
            return;
        };
        let prefix = self.config.read().await.topic_prefix.clone();
        let topic = format!("{}/cameras/{}/events/{}", prefix, camera_id, event);
        let body = serde_json::to_string(payload).unwrap_or_default();
        if let Err(e) = client
            .publish(&topic, QoS::AtMostOnce, false, body.as_bytes())
            .await
        {
            log::warn!("[MQTT] publish event {} failed: {}", topic, e);
        }
    }

    #[allow(dead_code)]
    pub async fn publish_state(&self, camera_id: &str, control: &str, value: &str) {
        let guard = self.client.read().await;
        let Some(client) = guard.as_ref() else {
            return;
        };
        let prefix = self.config.read().await.topic_prefix.clone();
        let topic = format!("{}/cameras/{}/state/{}", prefix, camera_id, control);
        if let Err(e) = client
            .publish(&topic, QoS::AtMostOnce, true, value.as_bytes())
            .await
        {
            log::warn!("[MQTT] publish state {} failed: {}", topic, e);
        }
    }

    #[allow(dead_code)]
    pub async fn publish_avail(&self, camera_id: &str, available: bool) {
        let value = if available { "online" } else { "offline" };
        self.publish_state(camera_id, "availability", value).await;
    }

    pub async fn publish_detection(
        &self,
        camera_id: &str,
        label: &str,
        confidence: f32,
        skill_id: &str,
    ) {
        let payload = serde_json::json!({
            "camera": camera_id,
            "label": label,
            "confidence": confidence,
            "skill": skill_id,
            "timestamp": chrono::Utc::now().to_rfc3339(),
        });
        self.publish_event(camera_id, "detection", &payload).await;
    }

    /// Subscribe to the application event bus and republish detection events to MQTT.
    pub fn subscribe_event_bus(self: &Arc<Self>, mut rx: broadcast::Receiver<String>) {
        let mqtt = self.clone();
        tokio::spawn(async move {
            loop {
                match rx.recv().await {
                    Ok(event_str) => {
                        if let Ok(parsed) = serde_json::from_str::<Value>(&event_str) {
                            let event_type = parsed
                                .get("event")
                                .and_then(|v| v.as_str())
                                .unwrap_or("");
                            if event_type == "detections" {
                                let camera = parsed
                                    .get("cameraId")
                                    .or_else(|| parsed.get("camera_id"))
                                    .and_then(|v| v.as_str())
                                    .unwrap_or("unknown")
                                    .to_string();
                                let skill = parsed
                                    .get("skillId")
                                    .or_else(|| parsed.get("skill_id"))
                                    .and_then(|v| v.as_str())
                                    .unwrap_or("");
                                if let Some(objs) =
                                    parsed.get("objects").and_then(|v| v.as_array())
                                {
                                    if let Some(top) = objs.iter().max_by(|a, b| {
                                        let ca = a
                                            .get("confidence")
                                            .and_then(|v| v.as_f64())
                                            .unwrap_or(0.0);
                                        let cb = b
                                            .get("confidence")
                                            .and_then(|v| v.as_f64())
                                            .unwrap_or(0.0);
                                        ca.partial_cmp(&cb).unwrap_or(std::cmp::Ordering::Equal)
                                    }) {
                                        let label = top
                                            .get("label")
                                            .and_then(|v| v.as_str())
                                            .unwrap_or("object");
                                        let conf = top
                                            .get("confidence")
                                            .and_then(|v| v.as_f64())
                                            .unwrap_or(0.0)
                                            as f32;
                                        mqtt.publish_detection(&camera, label, conf, skill).await;
                                    }
                                }
                            }
                        }
                    }
                    Err(broadcast::error::RecvError::Lagged(_)) => continue,
                    Err(broadcast::error::RecvError::Closed) => break,
                }
            }
        });
    }
}
