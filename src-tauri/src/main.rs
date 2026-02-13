#![cfg_attr(
    all(not(debug_assertions), target_os = "windows"),
    windows_subsystem = "windows"
)]

use tauri::api::process::{Command, CommandEvent, CommandChild};
use serde::{Deserialize, Serialize};
use std::sync::Mutex;

struct AppState {
    child_process: Mutex<Option<CommandChild>>,
}

#[derive(Serialize, Deserialize)]
struct SynthesisParams {
    text: String,
    speaker_wav: String,
    language: String,
    speed: f32,
    temperature: f32,
    top_k: u32,
    top_p: f32,
    repetition_penalty: f32,
    export_srt: bool,
    custom_output_path: Option<String>,
    output_filename: Option<String>,
    device: Option<String>,
}

#[tauri::command]
async fn open_folder(path: String) -> Result<(), String> {
    #[cfg(target_os = "windows")]
    {
        std::process::Command::new("explorer")
            .arg(path)
            .spawn()
            .map_err(|e| e.to_string())?;
    }
    Ok(())
}

#[tauri::command]
async fn get_system_info() -> Result<serde_json::Value, String> {
    let output = std::process::Command::new("powershell")
        .args(["-Command", "Get-CimInstance Win32_Processor | Select-Object -ExpandProperty Name; Get-CimInstance Win32_VideoController | Select-Object -ExpandProperty Name"])
        .output()
        .map_err(|e| e.to_string())?;
    
    let stdout = String::from_utf8_lossy(&output.stdout);
    let mut lines = stdout.lines();
    
    let cpu = lines.next().unwrap_or("Unknown CPU").to_string();
    let gpu = lines.next().unwrap_or("Unknown GPU").to_string();
    
    Ok(serde_json::json!({
        "cpu": cpu,
        "gpu": gpu,
    }))
}

#[tauri::command]
async fn stop_synthesis(state: tauri::State<'_, AppState>) -> Result<(), String> {
    let mut lock = state.child_process.lock().unwrap();
    if let Some(child) = lock.take() {
        child.kill().map_err(|e| format!("Failed to kill process: {}", e))?;
    }
    Ok(())
}

#[tauri::command]
async fn run_synthesis(
    params: SynthesisParams, 
    window: tauri::Window,
    state: tauri::State<'_, AppState>
) -> Result<String, String> {
    // Kill existing process if any
    {
        let mut lock = state.child_process.lock().unwrap();
        if let Some(child) = lock.take() {
            let _ = child.kill();
        }
    }

    let (mut rx, child) = Command::new_sidecar("voice-engine")
        .map_err(|e| format!("Failed to find sidecar: {}", e))?
        .args([
            "--params",
            &serde_json::to_string(&params).map_err(|e| e.to_string())?,
        ])
        .spawn()
        .map_err(|e| format!("Failed to spawn sidecar: {}", e))?;

    // Store child handle
    {
        let mut lock = state.child_process.lock().unwrap();
        *lock = Some(child);
    }

    let mut result_path = String::new();

    while let Some(event) = rx.recv().await {
        match event {
            CommandEvent::Stdout(line) => {
                if line.starts_with("SUCCESS|") {
                    result_path = line.replace("SUCCESS|", "");
                } else {
                    window.emit("sidecar-log", line).unwrap();
                }
            }
            CommandEvent::Stderr(line) => {
                window.emit("sidecar-error", line).unwrap();
            }
            CommandEvent::Terminated(status) => {
                // Clear handle on termination
                {
                    let mut lock = state.child_process.lock().unwrap();
                    *lock = None;
                }
                
                if status.code == Some(0) {
                    return Ok(result_path);
                } else {
                    return Err(format!("Sidecar crashed or stopped with code {:?}", status.code));
                }
            }
            _ => {}
        }
    }

    Ok(result_path)
}

#[tauri::command]
fn read_text_file(path: String) -> Result<String, String> {
    std::fs::read_to_string(path).map_err(|e| e.to_string())
}

fn main() {
    tauri::Builder::default()
        .manage(AppState {
            child_process: Mutex::new(None),
        })
        .invoke_handler(tauri::generate_handler![
            run_synthesis,
            stop_synthesis,
            read_text_file,
            get_system_info,
            open_folder
        ])
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
