use futures_util::StreamExt;
use reqwest::Client;
use serde_json::Value;
use std::path::{Path, PathBuf};
use tokio::sync::broadcast::Sender;

const HF_API_BASE: &str = "https://huggingface.co/api";
const HF_RESOLVE_BASE: &str = "https://huggingface.co";

#[derive(Clone)]
pub struct HuggingFaceService {
    client: Client,
}

impl HuggingFaceService {
    pub fn new() -> Self {
        let client = Client::builder()
            .user_agent("hawkeye-ai/0.1")
            .build()
            .expect("failed to build reqwest client");
        Self { client }
    }

    pub async fn search(&self, query: &str, limit: u32) -> Result<Vec<Value>, String> {
        let limit = limit.clamp(1, 50);
        let url = format!(
            "{}/models?search={}&limit={}&full=false",
            HF_API_BASE,
            urlencoding::encode(query),
            limit * 2
        );
        let resp = self
            .client
            .get(&url)
            .send()
            .await
            .map_err(|e| format!("huggingface search request failed: {}", e))?;
        if !resp.status().is_success() {
            return Err(format!("huggingface search returned {}", resp.status()));
        }
        let arr: Vec<Value> = resp
            .json()
            .await
            .map_err(|e| format!("huggingface search parse failed: {}", e))?;

        let mut results: Vec<Value> = arr
            .into_iter()
            .filter(|m| {
                let id = m.get("id").and_then(|v| v.as_str()).unwrap_or("");
                let tags = m
                    .get("tags")
                    .and_then(|v| v.as_array())
                    .map(|a| a.iter().filter_map(|t| t.as_str()).any(|s| s == "gguf"))
                    .unwrap_or(false);
                id.to_lowercase().contains("gguf") || tags
            })
            .take(limit as usize)
            .map(|m| {
                serde_json::json!({
                    "id": m.get("id").and_then(|v| v.as_str()).unwrap_or(""),
                    "author": m.get("author").and_then(|v| v.as_str()).unwrap_or(""),
                    "downloads": m.get("downloads").and_then(|v| v.as_i64()).unwrap_or(0),
                    "likes": m.get("likes").and_then(|v| v.as_i64()).unwrap_or(0),
                })
            })
            .collect();

        if results.is_empty() && !query.is_empty() {
            results = self
                .search_with_gguf_filter(query, limit)
                .await
                .unwrap_or_default();
        }
        Ok(results)
    }

    async fn search_with_gguf_filter(&self, query: &str, limit: u32) -> Result<Vec<Value>, String> {
        let url = format!(
            "{}/models?search={}&limit={}&filter=gguf",
            HF_API_BASE,
            urlencoding::encode(query),
            limit
        );
        let resp = self
            .client
            .get(&url)
            .send()
            .await
            .map_err(|e| format!("huggingface filtered search request failed: {}", e))?;
        if !resp.status().is_success() {
            return Err(format!(
                "huggingface filtered search returned {}",
                resp.status()
            ));
        }
        let arr: Vec<Value> = resp
            .json()
            .await
            .map_err(|e| format!("huggingface filtered search parse failed: {}", e))?;
        Ok(arr
            .into_iter()
            .take(limit as usize)
            .map(|m| {
                serde_json::json!({
                    "id": m.get("id").and_then(|v| v.as_str()).unwrap_or(""),
                    "author": m.get("author").and_then(|v| v.as_str()).unwrap_or(""),
                    "downloads": m.get("downloads").and_then(|v| v.as_i64()).unwrap_or(0),
                    "likes": m.get("likes").and_then(|v| v.as_i64()).unwrap_or(0),
                })
            })
            .collect())
    }

    pub async fn list_repo_files(
        &self,
        repo_id: &str,
        revision: &str,
    ) -> Result<Vec<String>, String> {
        let url = format!(
            "{}/models/{}/tree/{}",
            HF_API_BASE,
            repo_id,
            urlencoding::encode(revision)
        );
        let resp = self
            .client
            .get(&url)
            .send()
            .await
            .map_err(|e| format!("huggingface list request failed: {}", e))?;
        if !resp.status().is_success() {
            return Err(format!("huggingface list returned {}", resp.status()));
        }
        let arr: Vec<Value> = resp
            .json()
            .await
            .map_err(|e| format!("huggingface list parse failed: {}", e))?;
        let files: Vec<String> = arr
            .into_iter()
            .filter_map(|e| {
                let path = e.get("path").and_then(|v| v.as_str())?;
                let type_ = e.get("type").and_then(|v| v.as_str()).unwrap_or("");
                if type_ == "file" && path.to_lowercase().ends_with(".gguf") {
                    Some(path.to_string())
                } else {
                    None
                }
            })
            .collect();
        Ok(files)
    }

    pub async fn download(
        &self,
        repo_id: &str,
        filename: &str,
        revision: &str,
        dest_dir: &Path,
        download_id: &str,
        tx: &Sender<String>,
    ) -> Result<PathBuf, String> {
        if !crate::server::path_safe::is_safe_component(filename)
            || !filename.to_ascii_lowercase().ends_with(".gguf")
        {
            return Err("invalid model filename".to_string());
        }

        let url = format!(
            "{}/{}/resolve/{}/{}",
            HF_RESOLVE_BASE,
            repo_id,
            revision,
            urlencoding::encode(filename)
        );

        let resp = self
            .client
            .get(&url)
            .send()
            .await
            .map_err(|e| format!("huggingface download request failed: {}", e))?;
        if !resp.status().is_success() {
            return Err(format!("huggingface download returned {}", resp.status()));
        }

        let total_size = resp.content_length().unwrap_or(0);
        tokio::fs::create_dir_all(dest_dir)
            .await
            .map_err(|e| format!("create model dir failed: {}", e))?;
        let dest_path = crate::server::path_safe::safe_join_under(dest_dir, filename)
            .ok_or_else(|| "model filename escapes destination directory".to_string())?;

        // Download to temp file first, then atomic rename
        let tmp_path = dest_path.with_extension(format!(
            "{}.tmp",
            dest_path.extension().and_then(|e| e.to_str()).unwrap_or("download")
        ));
        let mut file = tokio::fs::File::create(&tmp_path)
            .await
            .map_err(|e| format!("create model file failed: {}", e))?;

        let mut downloaded: u64 = 0;
        let mut last_reported_percent: u32 = 0;
        let mut stream = resp.bytes_stream();
        use sha1::{Sha1, Digest};
        let mut sha1_hasher = Sha1::new();

        use tokio::io::AsyncWriteExt;
        while let Some(chunk) = stream.next().await {
            let chunk = chunk.map_err(|e| format!("huggingface stream error: {}", e))?;
            sha1_hasher.update(&chunk);
            file.write_all(&chunk)
                .await
                .map_err(|e| format!("file write failed: {}", e))?;
            downloaded += chunk.len() as u64;

            if total_size > 0 {
                let percent = ((downloaded as f64 / total_size as f64) * 100.0) as u32;
                if percent >= last_reported_percent + 5 || percent == 100 {
                    last_reported_percent = percent;
                    let _ = tx.send(
                        serde_json::to_string(&serde_json::json!({
                            "event": "download_progress",
                            "downloadId": download_id,
                            "percent": percent,
                            "bytesDownloaded": downloaded,
                            "totalBytes": total_size,
                            "status": if percent == 100 { "completed" } else { "downloading" }
                        }))
                        .unwrap_or_default(),
                    );
                }
            }
        }
        file.flush()
            .await
            .map_err(|e| format!("file flush failed: {}", e))?;
        drop(file);

        // Log SHA1 hash for audit trail (HuggingFace uses SHA1 for LFS)
        let computed_hash = format!("{:x}", sha1_hasher.finalize());
        log::info!(
            "[HuggingFace] Downloaded {} ({} bytes, sha1={})",
            filename, downloaded, computed_hash
        );

        // Atomic rename from temp to final path
        tokio::fs::rename(&tmp_path, &dest_path)
            .await
            .map_err(|e| format!("rename temp file failed: {}", e))?;

        Ok(dest_path)
    }
}
