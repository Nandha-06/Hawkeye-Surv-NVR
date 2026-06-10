use axum::body::Body;
use axum::http::{header, StatusCode};
use axum::response::Response;
use std::path::PathBuf;

pub async fn serve_file(path: PathBuf, content_type: &str) -> Response {
    match tokio::fs::File::open(&path).await {
        Ok(file) => Response::builder()
            .header(header::CONTENT_TYPE, content_type)
            .body(Body::from_stream(tokio_util::io::ReaderStream::new(file)))
            .unwrap(),
        Err(_) => Response::builder()
            .status(StatusCode::NOT_FOUND)
            .body(Body::from("File not found"))
            .unwrap(),
    }
}

pub async fn download_file_attachment(path: PathBuf, filename: &str) -> Response {
    match tokio::fs::File::open(&path).await {
        Ok(file) => Response::builder()
            .header(header::CONTENT_TYPE, "video/mp4")
            .header(
                header::CONTENT_DISPOSITION,
                format!("attachment; filename=\"{}\"", filename),
            )
            .body(Body::from_stream(tokio_util::io::ReaderStream::new(file)))
            .unwrap(),
        Err(_) => Response::builder()
            .status(StatusCode::NOT_FOUND)
            .body(Body::from("File not found"))
            .unwrap(),
    }
}
