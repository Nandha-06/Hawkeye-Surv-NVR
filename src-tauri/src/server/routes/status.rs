
use axum::{response::{IntoResponse}, Json};

pub async fn get_status() -> impl IntoResponse {

    Json(serde_json::json!({

        "status": "online",

        "backend": "Rust Axum + Tokio",

        "version": "1.0.0"

    }))

}
