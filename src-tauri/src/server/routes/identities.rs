
use crate::server::helpers::serve_file;
use crate::server::routes::events::SaveIdentityPayload;

use crate::server::state::ServerState;
use axum::{body::{Body}, extract::{Query, State}, http::{header, StatusCode}, response::{IntoResponse, Response}, Json};

use serde_json::Value;
use std::path::PathBuf;
use std::sync::Arc;

pub async fn get_identities(State(state): State<Arc<ServerState>>) -> impl IntoResponse {

    let root = state.skills_manager.root_dir.clone();

    let identities_path = root.join("skills/detection/perception-core/data/identities.json");

    if !identities_path.exists() {

        return Response::builder()

            .header(header::CONTENT_TYPE, "application/json")

            .body(Body::from("[]"))

            .unwrap();

    }

    match tokio::fs::read_to_string(&identities_path).await {

        Ok(content) => {

            let json: Value = serde_json::from_str(&content).unwrap_or(serde_json::json!([]));

            let list = json.as_array().cloned().unwrap_or_default();

            let mapped: Vec<Value> = list

                .iter()

                .map(|item| {

                    let name = item["id"].as_str().unwrap_or("").to_string();

                    let last_seen = item["last_seen"].as_str().unwrap_or("").to_string();

                    let has_crops = item["crop_paths"]

                        .as_array()

                        .map(|a| !a.is_empty())

                        .unwrap_or(false);

                    let image_url = if has_crops {

                        Some(format!("/api/v1/identities/crop?id={}", name))

                    } else {

                        None

                    };

                    serde_json::json!({

                        "name": name,

                        "image": image_url,

                        "lastSeen": last_seen,

                    })

                })

                .collect();

            Response::builder()

                .header(header::CONTENT_TYPE, "application/json")

                .body(Body::from(serde_json::to_string(&mapped).unwrap()))

                .unwrap()

        }

        Err(err) => Response::builder()

            .status(StatusCode::INTERNAL_SERVER_ERROR)

            .body(Body::from(err.to_string()))

            .unwrap(),

    }

}

pub async fn save_identity(

    State(state): State<Arc<ServerState>>,

    Json(payload): Json<SaveIdentityPayload>,

) -> impl IntoResponse {

    let root = state.skills_manager.root_dir.clone();

    let data_dir = root.join("skills/detection/perception-core/data");

    let identities_path = data_dir.join("identities.json");

    if !identities_path.exists() {

        return Json(serde_json::json!({ "success": false, "error": "identities.json not found" }));

    }

    let content = match tokio::fs::read_to_string(&identities_path).await {

        Ok(c) => c,

        Err(err) => return Json(serde_json::json!({ "success": false, "error": err.to_string() })),

    };

    let mut json: Value = match serde_json::from_str(&content) {

        Ok(j) => j,

        Err(err) => return Json(serde_json::json!({ "success": false, "error": err.to_string() })),

    };

    let arr = match json.as_array_mut() {

        Some(a) => a,

        None => {

            return Json(

                serde_json::json!({ "success": false, "error": "Invalid identities schema" }),

            )

        }

    };

    let mut found = false;

    for item in arr {

        if item["id"].as_str() == Some(&payload.old_name) {

            item["id"] = serde_json::json!(payload.new_name);

            found = true;

            // Rename crops physically and update their paths

            if let Some(crops) = item["crop_paths"].as_array_mut() {

                let mut new_crops = vec![];

                let crops_root = data_dir.join("crops");

                for crop_val in crops.iter() {

                    if let Some(crop_path_str) = crop_val.as_str() {

                        let original_path = PathBuf::from(crop_path_str);

                        let filename = original_path

                            .file_name()

                            .unwrap_or_default()

                            .to_string_lossy()

                            .to_string();

                        if filename.is_empty() || filename.contains('/') || filename.contains('\\') || filename.contains("..") {
                            new_crops.push(crop_val.clone());
                            continue;
                        }

                        // Check original path and fallback path; both must be inside crops_root
                        let mut source_path = original_path.clone();
                        if !source_path.exists() || !crate::server::path_safe::ensure_under(&source_path, &crops_root) {
                            let fallback = crops_root.join(&filename);
                            if fallback.exists() && crate::server::path_safe::ensure_under(&fallback, &crops_root) {
                                source_path = fallback;
                            } else {
                                new_crops.push(crop_val.clone());
                                continue;
                            }
                        }

                        // Replace oldName with newName in filename
                        let new_filename =
                            filename.replace(&payload.old_name, &payload.new_name);

                        if new_filename.is_empty() || new_filename.contains('/') || new_filename.contains('\\') || new_filename.contains("..") {
                            new_crops.push(serde_json::json!(source_path
                                .to_string_lossy()
                                .to_string()));
                            continue;
                        }

                        let dest_path = crops_root.join(&new_filename);

                        if tokio::fs::rename(&source_path, &dest_path).await.is_ok() {
                            new_crops.push(serde_json::json!(dest_path
                                .to_string_lossy()
                                .to_string()));
                        } else {
                            new_crops.push(serde_json::json!(source_path
                                .to_string_lossy()
                                .to_string()));
                        }

                    }

                }

                *crops = new_crops;

            }

            break;

        }

    }

    if !found {

        return Json(serde_json::json!({ "success": false, "error": "Identity not found" }));

    }

    let pretty = serde_json::to_string_pretty(&json).unwrap();

    if let Err(err) = tokio::fs::write(&identities_path, pretty).await {

        return Json(serde_json::json!({ "success": false, "error": err.to_string() }));

    }

    Json(serde_json::json!({ "success": true }))

}

#[derive(serde::Deserialize)]

pub struct IdentityCropQuery {

    pub id: String,
}

pub async fn get_identity_crop(

    State(state): State<Arc<ServerState>>,

    Query(params): Query<IdentityCropQuery>,

) -> Response {

    let root = state.skills_manager.root_dir.clone();

    let data_dir = root.join("skills/detection/perception-core/data");

    let identities_path = data_dir.join("identities.json");

    if !identities_path.exists() {

        return Response::builder()

            .status(StatusCode::NOT_FOUND)

            .body(Body::from(""))

            .unwrap();

    }

    let content = match tokio::fs::read_to_string(&identities_path).await {

        Ok(c) => c,

        Err(_) => {

            return Response::builder()

                .status(StatusCode::INTERNAL_SERVER_ERROR)

                .body(Body::from(""))

                .unwrap()

        }

    };

    let json: Value = serde_json::from_str(&content).unwrap_or(serde_json::json!([]));

    let list = json.as_array().cloned().unwrap_or_default();

    for item in list {

        if item["id"].as_str() == Some(&params.id) {

            if let Some(crops) = item["crop_paths"].as_array() {

                if let Some(first_crop) = crops.get(0).and_then(|v| v.as_str()) {

                    let crops_root = data_dir.join("crops");

                    let original_path = PathBuf::from(first_crop);

                    if !original_path.as_os_str().is_empty()
                        && crate::server::path_safe::ensure_under(&original_path, &crops_root)
                        && original_path.exists()
                    {
                        return serve_file(original_path, "image/jpeg").await;

                    }

                    // Fallback: treat the basename as a child of data_dir/crops

                    let filename = original_path

                        .file_name()

                        .unwrap_or_default()

                        .to_string_lossy()

                        .to_string();

                    if filename.is_empty() || filename.contains('/') || filename.contains('\\') || filename.contains("..") {
                        continue;
                    }

                    let fallback_path = crops_root.join(&filename);

                    if fallback_path.exists() {

                        return serve_file(fallback_path, "image/jpeg").await;

                    }

                }

            }

        }

    }

    Response::builder()

        .status(StatusCode::NOT_FOUND)

        .body(Body::from("Crop image not found"))

        .unwrap()

}

#[derive(serde::Deserialize)]

pub struct RecordingsQuery {

    pub camera_id: Option<String>,

    pub start_time: Option<String>,

    pub end_time: Option<String>,

    pub limit: Option<usize>,

    pub offset: Option<usize>,
}
