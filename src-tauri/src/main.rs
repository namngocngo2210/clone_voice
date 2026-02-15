#![cfg_attr(
    all(not(debug_assertions), target_os = "windows"),
    windows_subsystem = "windows"
)]

use tauri::api::process::{Command, CommandEvent, CommandChild};
use serde::{Deserialize, Serialize};
use std::sync::Mutex;
use std::path::PathBuf;

struct SidecarRuntime {
    child: CommandChild,
    rx: tauri::async_runtime::Receiver<CommandEvent>,
}

struct AppState {
    sidecar: Mutex<Option<SidecarRuntime>>,
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
    pause_sentence: Option<f32>,
    pause_paragraph: Option<f32>,
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

fn spawn_sidecar_daemon() -> Result<SidecarRuntime, String> {
    // Some model loaders rely on pickle objects; torch>=2.6 defaults to weights_only=true.
    // Force legacy behavior for this sidecar process.
    std::env::set_var("TORCH_FORCE_NO_WEIGHTS_ONLY_LOAD", "1");

    #[cfg(debug_assertions)]
    let (rx, child) = {
        let project_root = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("..");
        let python_exe = project_root
            .join("sidecar")
            .join("venv")
            .join("Scripts")
            .join("python.exe");
        let script_path = project_root.join("sidecar").join("main.py");

        let python_exe_str = python_exe.to_string_lossy().to_string();
        let script_path_str = script_path.to_string_lossy().to_string();

        Command::new(python_exe_str)
            .args(["-u", &script_path_str, "--daemon"])
            .spawn()
            .map_err(|e| format!("Failed to spawn python sidecar daemon in dev mode: {}", e))?
    };

    #[cfg(not(debug_assertions))]
    let (rx, child) = Command::new_sidecar("voice-engine")
        .map_err(|e| format!("Failed to find sidecar: {}", e))?
        .args(["--daemon"])
        .spawn()
        .map_err(|e| format!("Failed to spawn sidecar daemon: {}", e))?;

    Ok(SidecarRuntime { child, rx })
}

fn send_sidecar_request(
    state: tauri::State<'_, AppState>,
    window: tauri::Window,
    request_json: String,
) -> Result<String, String> {
    let mut lock = state.sidecar.lock().unwrap();
    if lock.is_none() {
        *lock = Some(spawn_sidecar_daemon()?);
    }

    let runtime = lock.as_mut().unwrap();
    let payload = format!("{request_json}\n");
    runtime
        .child
        .write(payload.as_bytes())
        .map_err(|e| format!("Failed to send request to sidecar: {}", e))?;

    loop {
        match tauri::async_runtime::block_on(runtime.rx.recv()) {
            Some(CommandEvent::Stdout(line)) => {
                if let Some(rest) = line.strip_prefix("SUCCESS|") {
                    return Ok(rest.trim().to_string());
                }
                if let Some(rest) = line.strip_prefix("ERROR|") {
                    return Err(rest.trim().to_string());
                }
                let _ = window.emit("sidecar-log", line);
            }
            Some(CommandEvent::Stderr(line)) => {
                let _ = window.emit("sidecar-error", line);
            }
            Some(CommandEvent::Terminated(status)) => {
                *lock = None;
                return Err(format!("Sidecar crashed or stopped with code {:?}", status.code));
            }
            Some(CommandEvent::Error(err)) => {
                return Err(format!("Sidecar I/O error: {}", err));
            }
            Some(_) => {}
            None => {
                *lock = None;
                return Err("Sidecar channel closed unexpectedly".to_string());
            }
        }
    }
}

#[tauri::command]
fn stop_synthesis(state: tauri::State<'_, AppState>) -> Result<(), String> {
    let mut lock = state.sidecar.lock().unwrap();
    if let Some(runtime) = lock.take() {
        runtime
            .child
            .kill()
            .map_err(|e| format!("Failed to kill process: {}", e))?;
    }
    Ok(())
}

#[tauri::command]
fn run_synthesis(
    params: SynthesisParams,
    window: tauri::Window,
    state: tauri::State<'_, AppState>,
) -> Result<String, String> {
    let request = serde_json::json!({
        "action": "synthesize",
        "params": params
    })
    .to_string();
    let result = send_sidecar_request(state, window, request)?.trim().to_string();
    if result == "WARMUP" || result == "SHUTDOWN" {
        Err("Unexpected sidecar response for synthesis".to_string())
    } else {
        Ok(result)
    }
}

#[tauri::command]
fn warmup_models(
    device: Option<String>,
    window: tauri::Window,
    state: tauri::State<'_, AppState>,
) -> Result<(), String> {
    let request = serde_json::json!({
        "action": "warmup",
        "params": {
            "warmup_only": true,
            "preload_all_tts": false,
            "device": device.unwrap_or_else(|| "auto".to_string()),
            "export_srt": false
        }
    })
    .to_string();
    let result = send_sidecar_request(state, window, request)?.trim().to_string();
    if result == "WARMUP" {
        Ok(())
    } else {
        Err(format!("Unexpected sidecar warmup response: {}", result))
    }
}

#[tauri::command]
fn read_text_file(path: String) -> Result<String, String> {
    std::fs::read_to_string(path).map_err(|e| e.to_string())
}

fn main() {
    tauri::Builder::default()
        .manage(AppState {
            sidecar: Mutex::new(None),
        })
        .invoke_handler(tauri::generate_handler![
            run_synthesis,
            warmup_models,
            stop_synthesis,
            read_text_file,
            get_system_info,
            open_folder
        ])
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
