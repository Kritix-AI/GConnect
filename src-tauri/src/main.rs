// Prevents an extra console window on Windows in release builds.
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::io::{Read, Write};
use std::net::{TcpListener, TcpStream};
use std::path::PathBuf;
use std::sync::Mutex;
use std::time::{Duration, Instant};

use serde::Serialize;
use tauri::menu::{Menu, MenuItem};
use tauri::tray::TrayIconBuilder;
use tauri::{AppHandle, Manager, RunEvent};
use tauri_plugin_shell::process::CommandChild;
use tauri_plugin_shell::ShellExt;

/// Everything the webview needs to reach the daemon.
#[derive(Serialize, Clone)]
struct BackendInfo {
    port: u16,
    token: String,
}

struct BackendState {
    info: Mutex<Option<BackendInfo>>,
    child: Mutex<Option<CommandChild>>,
}

impl BackendState {
    fn new() -> Self {
        Self {
            info: Mutex::new(None),
            child: Mutex::new(None),
        }
    }
}

// ----------------------------------------------------------------------
// Helpers
// ----------------------------------------------------------------------

fn find_free_port(preferred: u16) -> u16 {
    // 8001 is reserved for the sandbox container's voice-inference stream
    // (published by Docker on the host). Never hand it to the daemon: the
    // container starts after the daemon binds, and Docker would fail to
    // publish the port.
    const RESERVED: &[u16] = &[8001];

    let mut port = preferred;
    loop {
        if !RESERVED.contains(&port) && TcpListener::bind(("127.0.0.1", port)).is_ok() {
            return port;
        }
        port = port.saturating_add(1);
        if port == 0 {
            panic!("no free port available");
        }
    }
}

/// Minimal dependency-free HTTP/1.0 status check against 127.0.0.1.
fn http_ok(port: u16, path: &str) -> bool {
    let addr = format!("127.0.0.1:{port}");
    let Ok(mut stream) = TcpStream::connect(&addr) else {
        return false;
    };
    let _ = stream.set_read_timeout(Some(Duration::from_millis(1500)));
    let request = format!("GET {path} HTTP/1.0\r\nHost: {addr}\r\nConnection: close\r\n\r\n");
    if stream.write_all(request.as_bytes()).is_err() {
        return false;
    }
    let mut buf = [0u8; 128];
    let n = stream.read(&mut buf).unwrap_or(0);
    let head = String::from_utf8_lossy(&buf[..n]);
    head.starts_with("HTTP/1.") && head.contains(" 200")
}

fn data_dirs(app: &AppHandle) -> Result<(PathBuf, PathBuf, PathBuf), String> {
    let base = app
        .path()
        .app_data_dir()
        .map_err(|e| format!("app data dir unavailable: {e}"))?;
    let workspace = base.join("workspace");
    let runtime = base.join("runtime");
    let logs = base.join("logs");
    for dir in [&workspace, &runtime, &logs] {
        std::fs::create_dir_all(dir).map_err(|e| format!("cannot create {:?}: {e}", dir))?;
    }
    Ok((workspace, runtime, logs))
}

/// Spawns the Python daemon sidecar, waits for /api/health, reads the token
/// file the daemon generated, and publishes the info to the webview.
fn start_backend(app: &AppHandle) -> Result<BackendInfo, String> {
    let (workspace, runtime, logs) = data_dirs(app)?;
    let port = find_free_port(8000);

    let sidecar = app
        .shell()
        .sidecar("gconnect-backend")
        .map_err(|e| format!("sidecar binary not found: {e}"))?;

    let (mut rx, child) = sidecar
        .env("CONNECT_WORKSPACE_DIR", &workspace)
        .env("CONNECT_RUNTIME_DIR", &runtime)
        .env("CONNECT_LOG_DIR", &logs)
        .env("CONNECT_PORT", port.to_string())
        .env("CONNECT_HOST", "127.0.0.1")
        // The sidecar watches this PID and exits if the app dies without a
        // chance to run cleanup (force-kill, crash).
        .env("GCONNECT_PARENT_PID", std::process::id().to_string())
        .spawn()
        .map_err(|e| format!("failed to spawn daemon: {e}"))?;

    // Drain sidecar stdout/stderr so it never blocks; full logs go to the
    // daemon's own log file, this is just a safety valve.
    tauri::async_runtime::spawn(async move {
        while let Some(_event) = rx.recv().await {}
    });

    // Wait for the daemon to come up (cold start of the bundled binary).
    let deadline = Instant::now() + Duration::from_secs(45);
    while Instant::now() < deadline {
        if http_ok(port, "/api/health") {
            break;
        }
        std::thread::sleep(Duration::from_millis(250));
    }
    if !http_ok(port, "/api/health") {
        // Best effort: clean up the child before failing.
        kill_backend(app);
        return Err("daemon did not become healthy within 45s".into());
    }

    // Token file appears once the daemon resolves the secret.
    let token_path = runtime.join("auth_token");
    let mut token = String::new();
    let deadline = Instant::now() + Duration::from_secs(10);
    while Instant::now() < deadline {
        if let Ok(content) = std::fs::read_to_string(&token_path) {
            token = content.trim().to_string();
            if !token.is_empty() {
                break;
            }
        }
        std::thread::sleep(Duration::from_millis(200));
    }
    if token.is_empty() {
        kill_backend(app);
        return Err("daemon did not publish an auth token".into());
    }

    let info = BackendInfo { port, token };
    *app.state::<BackendState>().info.lock().unwrap() = Some(info.clone());
    *app.state::<BackendState>().child.lock().unwrap() = Some(child);

    // Diagnostics file: which port/pid the daemon actually got. Prevents
    // confusion when port 8000 is taken by another process (e.g. a stale
    // daemon) and this app's sidecar moved to a different port.
    let _ = std::fs::write(
        runtime.join("backend.json"),
        serde_json::json!({ "port": port, "pid": std::process::id() }).to_string(),
    );

    Ok(info)
}

fn kill_backend(app: &AppHandle) {
    if let Some(child) = app.state::<BackendState>().child.lock().unwrap().take() {
        let _ = child.kill();
    }
}

// ----------------------------------------------------------------------
// IPC commands (the ONLY bridge the webview gets)
// ----------------------------------------------------------------------

#[tauri::command]
fn backend_info(app: AppHandle) -> Result<BackendInfo, String> {
    // The daemon may still be starting; block briefly rather than failing.
    let deadline = Instant::now() + Duration::from_secs(50);
    loop {
        if let Some(info) = app.state::<BackendState>().info.lock().unwrap().clone() {
            return Ok(info);
        }
        if Instant::now() > deadline {
            return Err("backend daemon is not available".into());
        }
        std::thread::sleep(Duration::from_millis(250));
    }
}

#[tauri::command]
fn backend_url(app: AppHandle) -> Result<String, String> {
    let state = app.state::<BackendState>();
    let guard = state.info.lock().unwrap();
    guard
        .clone()
        .map(|i| format!("http://127.0.0.1:{}", i.port))
        .ok_or_else(|| "backend daemon is not available".into())
}

#[tauri::command]
fn restart_backend(app: AppHandle) -> Result<BackendInfo, String> {
    kill_backend(&app);
    *app.state::<BackendState>().info.lock().unwrap() = None;
    std::thread::sleep(Duration::from_millis(800));
    start_backend(&app)
}

// ----------------------------------------------------------------------
// Tray
// ----------------------------------------------------------------------

fn build_tray(app: &AppHandle) -> tauri::Result<()> {
    let open = MenuItem::with_id(app, "open", "Open GConnect", true, None::<&str>)?;
    let url = MenuItem::with_id(app, "url", "Copy daemon URL", true, None::<&str>)?;
    let restart = MenuItem::with_id(app, "restart", "Restart daemon", true, None::<&str>)?;
    let quit = MenuItem::with_id(app, "quit", "Quit GConnect", true, None::<&str>)?;
    let menu = Menu::with_items(app, &[&open, &url, &restart, &quit])?;

    let tray = TrayIconBuilder::with_id("gconnect-tray")
        .icon(app.default_window_icon().unwrap().clone())
        .menu(&menu)
        .show_menu_on_left_click(false)
        .on_menu_event(|app, event| match event.id.as_ref() {
            "open" => {
                if let Some(window) = app.get_webview_window("main") {
                    let _ = window.show();
                    let _ = window.set_focus();
                }
            }
            "url" => {
                if let Some(info) = app.state::<BackendState>().info.lock().unwrap().clone() {
                    let _ = set_clipboard_text(&format!("http://127.0.0.1:{}", info.port));
                }
            }
            "restart" => {
                let app = app.clone();
                tauri::async_runtime::spawn_blocking(move || {
                    let _ = restart_backend(app);
                });
            }
            "quit" => {
                app.exit(0);
            }
            _ => {}
        })
        .on_tray_icon_event(|tray, event| {
            if let tauri::tray::TrayIconEvent::DoubleClick { .. } = event {
                let app = tray.app_handle().clone();
                if let Some(window) = app.get_webview_window("main") {
                    let _ = window.show();
                    let _ = window.set_focus();
                }
            }
        })
        .build(app)?;

    let _ = tray.set_tooltip(Some("GConnect - Remote GPU Platform"));
    Ok(())
}

fn set_clipboard_text(text: &str) -> Result<(), String> {
    // Keep the binary lean: use the OS tool instead of a clipboard crate.
    #[cfg(target_os = "windows")]
    {
        use std::process::{Command, Stdio};
        let mut child = Command::new("clip")
            .stdin(Stdio::piped())
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .spawn()
            .map_err(|e| e.to_string())?;
        if let Some(stdin) = child.stdin.as_mut() {
            let _ = stdin.write_all(text.as_bytes());
        }
        let _ = child.wait();
        Ok(())
    }
    #[cfg(not(target_os = "windows"))]
    {
        use std::process::{Command, Stdio};
        let mut child = Command::new("pbcopy")
            .or_else(|_| {
                let mut c = Command::new("xclip");
                c.args(["-selection", "clipboard"]);
                Ok::<_, std::io::Error>(c)
            })
            .map_err(|e| e.to_string())?
            .stdin(Stdio::piped())
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .spawn()
            .map_err(|e| e.to_string())?;
        if let Some(stdin) = child.stdin.as_mut() {
            let _ = stdin.write_all(text.as_bytes());
        }
        let _ = child.wait();
        Ok(())
    }
}

// ----------------------------------------------------------------------
// Main
// ----------------------------------------------------------------------

fn main() {
    tauri::Builder::default()
        // Single instance: focus the existing window instead of a second app.
        .plugin(tauri_plugin_single_instance::init(|app, _args, _cwd| {
            if let Some(window) = app.get_webview_window("main") {
                let _ = window.show();
                let _ = window.set_focus();
            }
        }))
        .plugin(tauri_plugin_shell::init())
        .manage(BackendState::new())
        .invoke_handler(tauri::generate_handler![
            backend_info,
            backend_url,
            restart_backend
        ])
        .setup(|app| {
            let handle = app.handle().clone();
            // Start the daemon off the startup path so the window shows fast.
            tauri::async_runtime::spawn_blocking(move || {
                if let Err(err) = start_backend(&handle) {
                    eprintln!("[GConnect] backend start failed: {err}");
                }
            });

            build_tray(app.handle())?;
            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("error while building GConnect")
        .run(|app, event| {
            if let RunEvent::ExitRequested { .. } | RunEvent::Exit = event {
                kill_backend(app);
            }
        });
}
