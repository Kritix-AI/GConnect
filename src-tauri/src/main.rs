// Prevents an extra console window on Windows in release builds.
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::io::{Read, Write};
use std::net::{TcpListener, TcpStream};
use std::path::PathBuf;
use std::sync::Mutex;
use std::time::{Duration, Instant};

use serde::{Deserialize, Serialize};
use tauri::menu::{Menu, MenuItem};
use tauri::tray::TrayIconBuilder;
use tauri::{AppHandle, Manager, RunEvent};
use tauri_plugin_shell::process::CommandChild;
use tauri_plugin_shell::ShellExt;

/// Everything the webview needs to reach the daemon.
#[derive(Serialize, Clone)]
struct BackendInfo {
    mode: String, // "local" | "remote" | "unconfigured"
    url: String,  // daemon base URL ("" until a connection is configured)
    token: String,
    port: u16,
    online: bool,
}

/// Persisted connection choice (app_data_dir/connection.json).
/// `mode: "unconfigured"` (missing file) shows the connection screen.
#[derive(Deserialize, Serialize, Clone)]
struct ConnectionConfig {
    mode: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    url: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    token: Option<String>,
}

impl Default for ConnectionConfig {
    fn default() -> Self {
        Self {
            mode: "unconfigured".into(),
            url: None,
            token: None,
        }
    }
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
fn start_backend(app: &AppHandle, host_mode: bool) -> Result<BackendInfo, String> {
    let (workspace, runtime, logs) = data_dirs(app)?;
    let port = find_free_port(8000);
    let bind_host = if host_mode { "0.0.0.0" } else { "127.0.0.1" };
    let allow_lan = if host_mode { "true" } else { "false" };

    let sidecar = app
        .shell()
        .sidecar("gconnect-backend")
        .map_err(|e| format!("sidecar binary not found: {e}"))?;

    let (mut rx, child) = sidecar
        .env("CONNECT_WORKSPACE_DIR", &workspace)
        .env("CONNECT_RUNTIME_DIR", &runtime)
        .env("CONNECT_LOG_DIR", &logs)
        .env("CONNECT_PORT", port.to_string())
        .env("CONNECT_HOST", bind_host)
        .env("CONNECT_ALLOW_LAN", allow_lan)
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

    let mode_str = if host_mode { "share" } else { "local" };
    let info = BackendInfo {
        mode: mode_str.into(),
        url: format!("http://127.0.0.1:{port}"),
        token,
        port,
        online: true,
    };
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
// Connection mode (local sidecar vs remote GPU workstation)
// ----------------------------------------------------------------------

fn connection_file(app: &AppHandle) -> Result<PathBuf, String> {
    let base = app
        .path()
        .app_data_dir()
        .map_err(|e| format!("app data dir unavailable: {e}"))?;
    Ok(base.join("connection.json"))
}

fn load_connection(app: &AppHandle) -> ConnectionConfig {
    let Ok(path) = connection_file(app) else {
        return ConnectionConfig::default();
    };
    match std::fs::read_to_string(path) {
        Ok(text) => serde_json::from_str(&text).unwrap_or_default(),
        Err(_) => ConnectionConfig::default(),
    }
}

fn save_connection(app: &AppHandle, cfg: &ConnectionConfig) -> Result<(), String> {
    let path = connection_file(app)?;
    // Fresh installs (and the picker itself) run before data_dirs() ever
    // created the app-data folder - create it or the first choice fails.
    if let Some(parent) = path.parent() {
        std::fs::create_dir_all(parent).map_err(|e| format!("cannot create app data dir: {e}"))?;
    }
    let text = serde_json::to_string_pretty(cfg).map_err(|e| e.to_string())?;
    std::fs::write(&path, text).map_err(|e| format!("cannot save connection: {e}"))
}

fn unconfigured_info() -> BackendInfo {
    BackendInfo {
        mode: "unconfigured".into(),
        url: String::new(),
        token: String::new(),
        port: 0,
        online: false,
    }
}

fn normalize_remote_url(raw: &str) -> Result<String, String> {
    let url = raw.trim().trim_end_matches('/').to_string();
    let lower = url.to_lowercase();
    if !lower.starts_with("http://") && !lower.starts_with("https://") {
        return Err("URL must start with http:// or https://".into());
    }
    let scheme_len = lower.find("://").unwrap() + 3;
    if url.len() <= scheme_len {
        return Err("URL has no host".into());
    }
    Ok(url)
}

/// Best-effort liveness probe against the remote daemon's public /api/health.
/// Plain TCP for http URLs; https is assumed reachable (verifying TLS without
/// a dependency is out of scope - real errors surface in the UI via fetches).
fn remote_reachable(url: &str) -> bool {
    let lower = url.to_lowercase();
    if lower.starts_with("https://") {
        return true;
    }
    let rest = &url[7..]; // strip http://
    let host_port = rest.split('/').next().unwrap_or("");
    if host_port.is_empty() {
        return false;
    }
    let addr = if host_port.contains(':') {
        host_port.to_string()
    } else {
        format!("{host_port}:80")
    };
    let Ok(mut stream) = TcpStream::connect(&addr) else {
        return false;
    };
    let _ = stream.set_read_timeout(Some(Duration::from_millis(2000)));
    let request = format!("GET /api/health HTTP/1.0\r\nHost: {host_port}\r\nConnection: close\r\n\r\n");
    if stream.write_all(request.as_bytes()).is_err() {
        return false;
    }
    let mut buf = [0u8; 128];
    let n = stream.read(&mut buf).unwrap_or(0);
    let head = String::from_utf8_lossy(&buf[..n]);
    head.starts_with("HTTP/1.") && head.contains(" 200")
}

/// Points the app at a remote daemon and probes it once.
fn apply_remote(app: &AppHandle, url: &str, token: &str) -> BackendInfo {
    let info = BackendInfo {
        mode: "remote".into(),
        url: url.to_string(),
        token: token.to_string(),
        port: 0,
        online: remote_reachable(url),
    };
    *app.state::<BackendState>().info.lock().unwrap() = Some(info.clone());
    info
}

// ----------------------------------------------------------------------
// IPC commands (the ONLY bridge the webview gets)
// ----------------------------------------------------------------------

#[tauri::command]
fn backend_info(app: AppHandle) -> Result<BackendInfo, String> {
    // Local mode may still be starting (sidecar cold start); block briefly
    // rather than failing. Remote/unconfigured modes set info at startup.
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
        .map(|i| i.url)
        .filter(|u| !u.is_empty())
        .ok_or_else(|| "backend daemon is not available".into())
}

/// Persists and activates the connection choice. Returns the new BackendInfo
/// (remote mode is probed synchronously so the UI can warn about an
/// unreachable host; local mode spawns the sidecar in the background and the
/// reloaded webview waits for it via backend_info).
#[tauri::command]
fn set_connection(app: AppHandle, cfg: ConnectionConfig) -> Result<BackendInfo, String> {
    let mut persisted = cfg;

    match persisted.mode.as_str() {
        "local" | "share" => {
            persisted.url = None;
            persisted.token = None;
        }
        "remote" => {
            let url = persisted
                .url
                .as_deref()
                .map(str::trim)
                .filter(|u| !u.is_empty())
                .ok_or("Remote mode requires the daemon URL")?;
            let url = normalize_remote_url(url)?;
            let token = persisted
                .token
                .as_deref()
                .map(str::trim)
                .filter(|t| !t.is_empty())
                .ok_or("Remote mode requires the daemon token")?
                .to_string();
            persisted.url = Some(url);
            persisted.token = Some(token);
        }
        _ => return Err("mode must be 'local', 'remote', or 'share'".into()),
    }

    save_connection(&app, &persisted)?;

    // Stop any running sidecar, clear state, bring up the new mode.
    kill_backend(&app);
    *app.state::<BackendState>().info.lock().unwrap() = None;

    match persisted.mode.as_str() {
        "remote" => Ok(apply_remote(
            &app,
            persisted.url.as_deref().unwrap(),
            persisted.token.as_deref().unwrap(),
        )),
        _ => {
            let handle = app.clone();
            let is_share = persisted.mode == "share";
            tauri::async_runtime::spawn_blocking(move || {
                if let Err(err) = start_backend(&handle, is_share) {
                    eprintln!("[GConnect] backend start failed: {err}");
                }
            });
            let mode_str = if is_share { "share" } else { "local" };
            Ok(BackendInfo {
                mode: mode_str.into(),
                ..unconfigured_info()
            })
        }
    }
}

#[tauri::command]
fn restart_backend(app: AppHandle) -> Result<BackendInfo, String> {
    let cfg = load_connection(&app);
    let is_share = cfg.mode == "share";
    kill_backend(&app);
    *app.state::<BackendState>().info.lock().unwrap() = None;
    std::thread::sleep(Duration::from_millis(800));
    start_backend(&app, is_share)
}

#[tauri::command]
fn request_firewall_rule() -> Result<bool, String> {
    #[cfg(target_os = "windows")]
    {
        use std::os::windows::process::CommandExt;
        let script = "Start-Process netsh -Verb RunAs -ArgumentList 'advfirewall firewall add rule name=\"GConnect Host\" dir=in action=allow protocol=TCP localport=8000,8001'";
        let _ = std::process::Command::new("powershell")
            .args(&["-NoProfile", "-Command", script])
            .creation_flags(0x08000000)
            .spawn()
            .map_err(|e| e.to_string())?;
        Ok(true)
    }
    #[cfg(not(target_os = "windows"))]
    {
        Ok(true)
    }
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
                    let _ = set_clipboard_text(&info.url);
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
            restart_backend,
            set_connection,
            request_firewall_rule
        ])
        .setup(|app| {
            let handle = app.handle().clone();
            // Start the backend off the startup path so the window shows fast.
            tauri::async_runtime::spawn_blocking(move || {
                let cfg = load_connection(&handle);
                match cfg.mode.as_str() {
                    "remote" => match (cfg.url.as_deref(), cfg.token.as_deref()) {
                        (Some(url), Some(token)) => {
                            apply_remote(&handle, url, token);
                        }
                        _ => {
                            // Corrupt config: fall back to the connection screen.
                            *handle.state::<BackendState>().info.lock().unwrap() =
                                Some(unconfigured_info());
                        }
                    },
                    "local" => {
                        if let Err(err) = start_backend(&handle, false) {
                            eprintln!("[GConnect] backend start failed: {err}");
                        }
                    }
                    "share" => {
                        if let Err(err) = start_backend(&handle, true) {
                            eprintln!("[GConnect] backend start failed: {err}");
                        }
                    }
                    _ => {
                        *handle.state::<BackendState>().info.lock().unwrap() =
                            Some(unconfigured_info());
                    }
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
