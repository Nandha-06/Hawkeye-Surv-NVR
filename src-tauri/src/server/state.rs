use crate::recording_manager::RecordingManager;
use crate::services;
use crate::skills_manager::SkillsManager;
use std::path::PathBuf;
use std::sync::Arc;
use tokio::sync::{broadcast, RwLock};

#[derive(serde::Serialize, Clone)]
pub struct ExportJob {
    pub id: String,
    pub camera_id: String,
    pub status: String, // "pending", "processing", "completed", "failed"
    pub progress: u32,
    #[serde(rename = "startTime")]
    pub start_time: String,
    #[serde(rename = "endTime")]
    pub end_time: String,
    pub message: Option<String>,
}

pub struct ServerState {
    pub data_dir: PathBuf,
    pub skills_manager: SkillsManager,
    pub recording_manager: RecordingManager,
    pub tx: broadcast::Sender<String>,
    pub exports: Arc<RwLock<Vec<ExportJob>>>,
    pub mqtt: Arc<services::mqtt::MqttManager>,
    pub hf: Arc<services::huggingface::HuggingFaceService>,
    pub ptz: Arc<services::ptz::PtzService>,
    pub api_token: String,
}

pub(crate) fn generate_random_id() -> String {
    use rand::RngCore;
    let mut bytes = [0u8; 16];
    rand::rngs::OsRng.fill_bytes(&mut bytes);
    // 16 random bytes = 128 bits of entropy, formatted as 32 hex chars.
    hex::encode(bytes)
}
