mod agent;
mod commands;

pub use commands::handle_socket;

use crate::server::state::ServerState;
use axum::extract::ws::WebSocketUpgrade;
use axum::extract::{Query, State};
use axum::http::StatusCode;
use axum::response::IntoResponse;
use serde::Deserialize;
use std::sync::Arc;

#[derive(Deserialize)]
pub struct WsConnectQuery {
    #[serde(default)]
    pub token: Option<String>,
}

pub async fn ws_handler(
    ws: WebSocketUpgrade,
    State(state): State<Arc<ServerState>>,
    Query(q): Query<WsConnectQuery>,
) -> impl IntoResponse {
    fn constant_time_eq(a: &str, b: &str) -> bool {
        if a.len() != b.len() {
            return false;
        }
        let mut result = 0;
        for (x, y) in a.bytes().zip(b.bytes()) {
            result |= x ^ y;
        }
        result == 0
    }

    let provided = q.token.as_deref().unwrap_or("");
    if !constant_time_eq(provided, &state.api_token) {
        return (StatusCode::UNAUTHORIZED, "Unauthorized").into_response();
    }
    ws.on_upgrade(|socket| handle_socket(socket, state))
}
