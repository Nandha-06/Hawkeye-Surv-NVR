use rusqlite::Connection;
use std::path::Path;

pub fn ensure_database(data_dir: &Path) -> Result<(), rusqlite::Error> {
    let _ = std::fs::create_dir_all(data_dir);
    let db_path = data_dir.join("hawkeye.db");
    let conn = Connection::open(db_path)?;
    conn.busy_timeout(std::time::Duration::from_secs(5))?;
    conn.execute_batch(
        r#"
        PRAGMA journal_mode = WAL;
        PRAGMA synchronous = NORMAL;

        CREATE TABLE IF NOT EXISTS events (
            id TEXT PRIMARY KEY,
            camera_id TEXT,
            label TEXT,
            confidence REAL,
            timestamp TEXT,
            snapshot_path TEXT,
            severity TEXT
        );

        CREATE TABLE IF NOT EXISTS session_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT,
            camera_id TEXT,
            message TEXT
        );

        CREATE TABLE IF NOT EXISTS recordings (
            id TEXT PRIMARY KEY,
            camera_id TEXT,
            start_time TEXT,
            end_time TEXT,
            filepath TEXT,
            type TEXT
        );

        CREATE INDEX IF NOT EXISTS idx_events_camera_timestamp
            ON events(camera_id, timestamp);

        CREATE INDEX IF NOT EXISTS idx_recordings_camera_time
            ON recordings(camera_id, start_time, end_time);

        CREATE UNIQUE INDEX IF NOT EXISTS idx_recordings_camera_filepath
            ON recordings(camera_id, filepath);
        "#,
    )?;
    Ok(())
}
