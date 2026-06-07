use rusqlite::Connection;
use crate::server::helpers::serve_file;
use crate::server::routes::recordings::{PlaylistItem, VodPlaylistQuery};

use crate::server::state::ServerState;
use axum::{body::Body, extract::{Path, Query, State}, http::{header, StatusCode}, response::Response};

use std::path::PathBuf;
use std::sync::Arc;

fn parse_time_ms(value: &str) -> Option<i64> {

    chrono::DateTime::parse_from_rfc3339(value)

        .or_else(|_| chrono::DateTime::parse_from_str(value, "%Y-%m-%dT%H:%M:%S.%fZ"))

        .ok()

        .map(|dt| dt.timestamp_millis())

}

// Keyframe function removed as part of VOD TS optimization

pub async fn get_vod_playlist(

    State(state): State<Arc<ServerState>>,

    Query(params): Query<VodPlaylistQuery>,

) -> Response {

    let root = state.skills_manager.root_dir.clone();

    let db_path = root.join(".data").join("hawkeye.db");

    let list: Vec<(String, String, String, String)> = {

        let conn = match Connection::open(&db_path) {

            Ok(c) => c,

            Err(err) => {

                return Response::builder()

                    .status(StatusCode::INTERNAL_SERVER_ERROR)

                    .body(Body::from(err.to_string()))

                    .unwrap()

            }

        };

        let mut stmt = match conn.prepare(

            "SELECT id, start_time, end_time, filepath FROM recordings WHERE camera_id = ?1 AND start_time < ?2 AND end_time > ?3 ORDER BY start_time ASC"

        ) {

            Ok(s) => s,

            Err(err) => return Response::builder().status(StatusCode::INTERNAL_SERVER_ERROR).body(Body::from(err.to_string())).unwrap(),

        };

        let rows = stmt.query_map(

            rusqlite::params![params.camera_id, params.end_time, params.start_time],

            |row| {

                let id: String = row.get(0)?;

                let start: String = row.get(1)?;

                let end: String = row.get(2)?;

                let filepath: String = row.get(3)?;

                Ok((id, start, end, filepath))

            },

        );

        match rows {

            Ok(r) => r.filter_map(Result::ok).collect(),

            Err(_) => vec![],

        }

    };

    let req_start_ms = match parse_time_ms(&params.start_time) {

        Some(v) => v,

        None => {

            return Response::builder()

                .status(StatusCode::BAD_REQUEST)

                .body(Body::from("Invalid start_time"))

                .unwrap()

        }

    };

    let _req_end_ms = match parse_time_ms(&params.end_time) {

        Some(v) => v,

        None => {

            return Response::builder()

                .status(StatusCode::BAD_REQUEST)

                .body(Body::from("Invalid end_time"))

                .unwrap()

        }

    };

    let mut items = Vec::new();

    let mut max_duration = 5.0f64;

    let mut first_start_ms: Option<i64> = None;

    for (id, start, end, _filepath) in list {

        let Some(seg_start_ms) = parse_time_ms(&start) else {

            continue;

        };

        let Some(seg_end_ms) = parse_time_ms(&end) else {

            continue;

        };

        if first_start_ms.is_none() {
            first_start_ms = Some(seg_start_ms);
        }

        let duration = (seg_end_ms - seg_start_ms) as f64 / 1000.0;

        if duration < 0.1 {

            continue;

        }

        max_duration = max_duration.max(duration);

        items.push(PlaylistItem {

            id,

            duration,

        });

    }

    if items.is_empty() {

        return Response::builder()

            .status(StatusCode::NOT_FOUND)

            .body(Body::from("No recording segments found for this range"))

            .unwrap();

    }

    let mut playlist = format!(

        "#EXTM3U\n#EXT-X-VERSION:3\n#EXT-X-PLAYLIST-TYPE:VOD\n#EXT-X-TARGETDURATION:{}\n#EXT-X-MEDIA-SEQUENCE:0\n",

        max_duration.ceil() as u32

    );

    // Add START offset if requested start is inside the first segment
    if let Some(first_seg_start) = first_start_ms {
        if req_start_ms > first_seg_start {
            let offset = (req_start_ms - first_seg_start) as f64 / 1000.0;
            playlist.push_str(&format!("#EXT-X-START:TIME-OFFSET={:.3}\n", offset));
        }
    }

    for (idx, item) in items.iter().enumerate() {

        playlist.push_str(&format!("#EXTINF:{:.3},\n", item.duration));

        let segment_url = format!("/api/v1/recordings/vod/segment/{}", item.id);

        playlist.push_str(&segment_url);

        playlist.push('\n');

        if idx + 1 < items.len() {

            playlist.push_str("#EXT-X-DISCONTINUITY\n");

        }

    }

    playlist.push_str("#EXT-X-ENDLIST\n");

    Response::builder()

        .header(header::CONTENT_TYPE, "application/vnd.apple.mpegurl")

        .header(header::CACHE_CONTROL, "no-store")

        .body(Body::from(playlist))

        .unwrap()

}

#[derive(serde::Deserialize)]

pub struct VodSegmentQuery {

    pub ss: Option<f64>,

    pub t: Option<f64>,
}

pub async fn get_vod_segment(

    State(state): State<Arc<ServerState>>,

    Path(id): Path<String>,

    Query(params): Query<VodSegmentQuery>,

) -> Response {

    let root = state.skills_manager.root_dir.clone();

    let db_path = root.join(".data").join("hawkeye.db");

    let conn = match Connection::open(&db_path) {

        Ok(c) => c,

        Err(_) => {

            return Response::builder()

                .status(StatusCode::INTERNAL_SERVER_ERROR)

                .body(Body::from(""))

                .unwrap()

        }

    };

    let filepath: Option<String> = conn

        .query_row(

            "SELECT filepath FROM recordings WHERE id = ?1",

            rusqlite::params![id],

            |row| row.get(0),

        )

        .unwrap_or(None);

    if let Some(path_str) = filepath {
        let recordings_root = root.join(".data").join("recordings");
        if !crate::server::path_safe::ensure_under(std::path::Path::new(&path_str), &recordings_root) {
            log::warn!("[get_vod_segment] rejected path outside recordings/: {}", path_str);
            return Response::builder()
                .status(StatusCode::NOT_FOUND)
                .body(Body::from("Segment not found"))
                .unwrap();
        }
        let file_path = PathBuf::from(&path_str);

        if file_path.exists() {
            let is_ts = file_path
                .extension()
                .and_then(|ext| ext.to_str())
                .map(|ext| ext.eq_ignore_ascii_case("ts"))
                .unwrap_or(false);

            if is_ts && params.ss.is_none() && params.t.is_none() {
                let file = match tokio::fs::File::open(&file_path).await {
                    Ok(f) => f,
                    Err(_) => return Response::builder()
                        .status(StatusCode::INTERNAL_SERVER_ERROR)
                        .body(Body::from("Failed to open TS segment"))
                        .unwrap(),
                };
                let stream = tokio_util::io::ReaderStream::new(file);
                return Response::builder()
                    .header(header::CONTENT_TYPE, "video/mp2t")
                    .header(header::CACHE_CONTROL, "public, max-age=86400")
                    .body(Body::from_stream(stream))
                    .unwrap();
            }

            let is_webm = file_path

                .extension()

                .and_then(|ext| ext.to_str())

                .map(|ext| ext.eq_ignore_ascii_case("webm"))

                .unwrap_or(false);

            let mut cmd = tokio::process::Command::new("ffmpeg");

            cmd.arg("-hide_banner")

                .arg("-loglevel")

                .arg("error")

                .arg("-nostdin");

            if let Some(ss) = params.ss {

                cmd.arg("-ss").arg(format!("{:.3}", ss));

            }

            cmd.arg("-i").arg(&file_path);

            if let Some(t) = params.t {

                cmd.arg("-t").arg(format!("{:.3}", t));

            }

            cmd.arg("-map").arg("0:v:0").arg("-map").arg("0:a?");

            if is_webm {

                cmd.arg("-c:v")

                    .arg("libx264")

                    .arg("-preset")

                    .arg("ultrafast")

                    .arg("-tune")

                    .arg("zerolatency")

                    .arg("-c:a")

                    .arg("aac")

                    .arg("-b:a")

                    .arg("128k");

            } else {

                cmd.arg("-c:v").arg("copy").arg("-c:a").arg("copy");

            }

            cmd.arg("-f")

                .arg("mpegts")

                .arg("pipe:1")

                .stdout(std::process::Stdio::piped())

                .stderr(std::process::Stdio::null());

            let mut child = match cmd.spawn() {

                Ok(child) => child,

                Err(err) => {

                    return Response::builder()

                        .status(StatusCode::INTERNAL_SERVER_ERROR)

                        .body(Body::from(format!("Failed to spawn ffmpeg: {}", err)))

                        .unwrap();

                }

            };

            let Some(stdout) = child.stdout.take() else {

                let _ = child.kill().await;

                return Response::builder()

                    .status(StatusCode::INTERNAL_SERVER_ERROR)

                    .body(Body::from("Failed to open ffmpeg stdout"))

                    .unwrap();

            };

            // Hold the child handle in a watcher task. If the response
            // body is dropped (client disconnect), kill ffmpeg so we
            // don't leave a zombie transcoder chewing CPU on a stream
            // nobody is listening to.
            //
            // We use a Drop guard on the stream itself: when the body is
            // dropped (client disconnect, error, etc.) the guard fires
            // and signals the watcher task to `kill().await` the child.
            let (kill_tx, mut kill_rx) = tokio::sync::mpsc::channel::<()>(1);
            tokio::spawn(async move {
                tokio::select! {
                    _ = child.wait() => {}
                    _ = kill_rx.recv() => {
                        let _ = child.kill().await;
                    }
                }
            });

            // tokio_util::io::ReaderStream is Unpin (and works with
            // AsyncRead directly), so we can wrap it in our Drop guard.
            use tokio_util::io::ReaderStream;
            let raw_stream = ReaderStream::new(stdout);

            // Wrap the stream in a guard. When the wrapped stream is
            // dropped (e.g. client disconnect, response body dropped),
            // we fire the kill signal.
            struct KillOnDrop<S> {
                inner: S,
                kill: Option<tokio::sync::mpsc::Sender<()>>,
            }
            impl<S> Drop for KillOnDrop<S> {
                fn drop(&mut self) {
                    if let Some(tx) = self.kill.take() {
                        // Best-effort; if the receiver is gone the
                        // watcher already exited cleanly.
                        let _ = tx.try_send(());
                    }
                }
            }
            impl<S: futures_util::Stream + Unpin> futures_util::Stream for KillOnDrop<S> {
                type Item = S::Item;
                fn poll_next(
                    mut self: std::pin::Pin<&mut Self>,
                    cx: &mut std::task::Context<'_>,
                ) -> std::task::Poll<Option<Self::Item>> {
                    std::pin::Pin::new(&mut self.inner).poll_next(cx)
                }
            }
            let stream = KillOnDrop {
                inner: raw_stream,
                kill: Some(kill_tx),
            };

            return Response::builder()

                .header(header::CONTENT_TYPE, "video/mp2t")

                .header(header::CACHE_CONTROL, "public, max-age=86400")

                .body(Body::from_stream(stream))

                .unwrap();

        }

    }

    Response::builder()

        .status(StatusCode::NOT_FOUND)

        .body(Body::from("Segment not found"))

        .unwrap()

}

pub async fn get_vod_thumbnail(

    State(state): State<Arc<ServerState>>,

    Path(id): Path<String>,

) -> Response {

    let root = state.skills_manager.root_dir.clone();

    let db_path = root.join(".data").join("hawkeye.db");

    let conn = match Connection::open(&db_path) {

        Ok(c) => c,

        Err(_) => {

            return Response::builder()

                .status(StatusCode::INTERNAL_SERVER_ERROR)

                .body(Body::from(""))

                .unwrap()

        }

    };

    let filepath: Option<String> = conn

        .query_row(

            "SELECT filepath FROM recordings WHERE id = ?1",

            rusqlite::params![id],

            |row| row.get(0),

        )

        .unwrap_or(None);

    if let Some(path_str) = filepath {
        let recordings_root = root.join(".data").join("recordings");
        if !crate::server::path_safe::ensure_under(std::path::Path::new(&path_str), &recordings_root) {
            log::warn!("[get_vod_thumbnail] rejected path outside recordings/: {}", path_str);
            return Response::builder()
                .status(StatusCode::NOT_FOUND)
                .body(Body::from("Thumbnail not found"))
                .unwrap();
        }
        let file_path = std::path::PathBuf::from(&path_str).with_extension("jpg");

        if file_path.exists() {

            return serve_file(file_path, "image/jpeg").await;

        }

    }

    Response::builder()

        .status(StatusCode::NOT_FOUND)

        .body(Body::from("Thumbnail not found"))

        .unwrap()

}
