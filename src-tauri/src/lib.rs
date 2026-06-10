mod go2rtc_manager;
mod recording_manager;
mod server;
mod services;
mod skills_manager;

use tauri::Manager;

#[tauri::command]
fn get_api_token() -> Result<String, String> {
    let mut data_dir = std::path::PathBuf::from(".data");
    if !data_dir.exists() {
        let parent_data = std::path::PathBuf::from("../.data");
        if parent_data.exists() || std::path::PathBuf::from("../frontend").exists() {
            data_dir = parent_data;
        }
    }
    std::fs::read_to_string(data_dir.join(".local_api_token"))
        .map(|s| s.trim().to_string())
        .map_err(|e| e.to_string())
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    // Initialize standard Tokio broadcast channel for real-time AI telemetry
    let (tx, _) = tokio::sync::broadcast::channel::<String>(512);
    let server_tx = tx.clone();

    tauri::Builder::default()
        .invoke_handler(tauri::generate_handler![get_api_token])
        .setup(move |app| {
            if cfg!(debug_assertions) {
                app.handle().plugin(
                    tauri_plugin_log::Builder::default()
                        .level(log::LevelFilter::Info)
                        .build(),
                )?;
            }

            // Spawn the embedded Axum web server in a background thread, sharing the event bus
            tauri::async_runtime::spawn(async move {
                server::start_server(server_tx).await;
            });

            // Spawn go2rtc sidecar and register camera streams. Store
            // the child in app state so it lives for the app's lifetime
            // and is killed+reaped on exit.
            let app_handle = app.handle().clone();
            tauri::async_runtime::spawn(async move {
                let mut root_dir = std::path::PathBuf::from(".");
                if !root_dir.join("skills.json").exists() {
                    let parent = std::path::PathBuf::from("..");
                    if parent.join("skills.json").exists() {
                        root_dir = parent;
                    }
                }
                if let Some(child) = go2rtc_manager::Go2RtcManager::spawn(&root_dir) {
                    let owned = go2rtc_manager::OwnedChild::new(child);
                    println!("[go2rtc] Sidecar PID: {:?}", owned.id());
                    app_handle.manage(owned);
                }
                go2rtc_manager::Go2RtcManager::register_all_cameras(&root_dir).await;
            });

            // Spawn real-time hardware telemetry broadcaster
            let telemetry_tx = tx.clone();
            tauri::async_runtime::spawn(async move {
                use sysinfo::{Disks, System};
                let mut sys = System::new();

                loop {
                    tokio::time::sleep(tokio::time::Duration::from_secs(2)).await;

                    sys.refresh_cpu_usage();
                    sys.refresh_memory();

                    let cpu_usage = sys.global_cpu_info().cpu_usage() as u32;
                    let total_mem = sys.total_memory();
                    let used_mem = sys.used_memory();
                    let mem_percent = if total_mem > 0 {
                        ((used_mem as f64 / total_mem as f64) * 100.0) as u32
                    } else {
                        0
                    };

                    let disks = Disks::new_with_refreshed_list();
                    let mut total_storage = 0u64;
                    let mut used_storage = 0u64;
                    for disk in &disks {
                        total_storage += disk.total_space();
                        used_storage += disk.total_space() - disk.available_space();
                    }
                    let storage_percent = if total_storage > 0 {
                        ((used_storage as f64 / total_storage as f64) * 100.0) as u32
                    } else {
                        0
                    };

                    let gpu_usage = 0u32;

                    let stats_event = serde_json::json!({
                      "event": "perf_stats",
                      "cpu": cpu_usage,
                      "gpu": gpu_usage,
                      "memory": {
                        "used": used_mem,
                        "total": total_mem,
                        "percent": mem_percent
                      },
                      "storage": {
                        "used": used_storage,
                        "total": total_storage,
                        "percent": storage_percent
                      }
                    });

                    if let Ok(event_str) = serde_json::to_string(&stats_event) {
                        let _ = telemetry_tx.send(event_str);
                    }
                }
            });

            Ok(())
        })
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
