import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, WebSocket
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from backend import auth
from backend.cluster_manager import cluster_manager
from backend.config import settings
from backend.docker_manager import docker_manager
from backend.file_manager import router as files_router
from backend.gpu_telemetry import gpu_telemetry_service
from backend.linter import router as lint_router
from backend.network_utils import get_host_network_addresses
from backend.pty_bridge import pty_bridge


def _setup_file_logging() -> None:
    """Mirror daemon logs into <repo>/.gconnect/logs/ for diagnosability when
    running hidden (tray/sidecar). Never logs query strings."""
    try:
        settings.LOG_DIR.mkdir(parents=True, exist_ok=True)
        handler = logging.FileHandler(settings.LOG_DIR / "daemon.log", encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s"))
        logging.getLogger().addHandler(handler)
    except Exception as e:
        logging.getLogger("connect.main").warning(f"File logging unavailable: {e}")


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("connect.main")
_setup_file_logging()


def _frontend_dir() -> Path:
    """Locates the static frontend: repo layout in dev, bundled dir when the
    daemon is frozen by PyInstaller (sys._MEIPASS)."""
    import sys

    if getattr(sys, "frozen", False):
        base = Path(getattr(sys, "_MEIPASS", Path(sys.executable).resolve().parent))
        return base / "frontend"
    return Path(__file__).resolve().parent.parent / "frontend"


FRONTEND_DIR = _frontend_dir()


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting GConnect Host Daemon...")
    logger.info(f"Workspace Directory: {settings.WORKSPACE_DIR.resolve()}")
    logger.info(f"Hugging Face Cache: {settings.WORKSPACE_DIR.resolve() / settings.HF_CACHE_DIR_NAME}")
    logger.info(f"Binding: {settings.effective_host}:{settings.PORT} (ALLOW_LAN={settings.ALLOW_LAN})")

    # Resolve/persist the auth token eagerly so supervised launchers (the
    # desktop app) can read the token file as soon as /api/health responds.
    auth.get_token()

    if settings.ALLOW_LAN and not settings.AUTH_TOKEN and not settings.AUTH_DISABLED:
        logger.warning(
            "CONNECT_ALLOW_LAN=true without CONNECT_AUTH_TOKEN - using an auto-generated "
            "token persisted in .gconnect/auth_token. Set CONNECT_AUTH_TOKEN for a stable secret."
        )

    # Single shared NVML sampler for all telemetry clients.
    await gpu_telemetry_service.start()

    async def _init_container_background():
        import asyncio
        loop = asyncio.get_running_loop()
        try:
            logger.info("Initializing Docker sandbox container in background...")
            await loop.run_in_executor(None, docker_manager.get_or_create_container)
        except Exception as e:
            logger.warning(f"Could not auto-start container: {e}")

    if settings.AUTO_START_CONTAINER and docker_manager.docker_available:
        import asyncio
        asyncio.create_task(_init_container_background())

    yield

    await gpu_telemetry_service.stop()
    logger.info("GConnect Host Daemon shutdown.")


app = FastAPI(
    title="GConnect - Universal Remote GPU Development Platform",
    description="Universal, turnkey remote GPU sharing platform and Web IDE for PyTorch, LLMs, Computer Vision, Voice AI, and Data Science.",
    version="2.2.1",
    lifespan=lifespan,
    # API surface is not public; docs would only help attackers.
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)

# NOTE: CORS middleware intentionally removed (audit SEC-04). The frontend is
# same-origin (served by this daemon) or bundled in the desktop app. Cross-origin
# browser requests are rejected inside the auth middleware.

app.middleware("http")(auth.enforce_http_auth)

app.include_router(files_router)
app.include_router(lint_router)


@app.get("/api/health")
def health():
    """Unauthenticated liveness probe (no information beyond status)."""
    return {"status": "ok"}


@app.get("/api/container/status")
def get_container_status():
    return docker_manager.get_status()


@app.post("/api/container/restart")
def restart_container():
    success = docker_manager.restart_container()
    return {"status": "success" if success else "failed"}


@app.post("/api/cache/wipe")
def wipe_cache():
    result = docker_manager.wipe_model_cache()
    if result["status"] == "error":
        raise HTTPException(status_code=500, detail=result["message"])
    return result


@app.get("/api/telemetry/snapshot")
async def get_telemetry_snapshot():
    import asyncio
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, gpu_telemetry_service.get_snapshot)


# ----------------------------------------------------------------------
# Host GPU Sharing
# ----------------------------------------------------------------------

from pydantic import BaseModel  # noqa: E402


class ConfigureTokenRequest(BaseModel):
    token: str


@app.get("/api/host/share-info")
async def get_share_info():
    """Live specs, auto-detected IPs, and connection parameters for sharing this GPU."""
    import asyncio
    loop = asyncio.get_running_loop()
    telemetry = await loop.run_in_executor(None, gpu_telemetry_service.get_snapshot)
    net_info = await loop.run_in_executor(None, get_host_network_addresses)

    effective_h = settings.effective_host
    is_sharing = settings.ALLOW_LAN or effective_h in ("0.0.0.0", "::")

    return {
        "status": "ready",
        "network": net_info,
        "host": effective_h,
        "port": settings.PORT,
        "inference_port": settings.INFERENCE_PORT,
        "token": auth.get_token(),
        "allow_lan": settings.ALLOW_LAN,
        "is_sharing": is_sharing,
        "telemetry": telemetry,
        "docker_status": docker_manager.get_status(),
    }


@app.post("/api/host/configure-token")
def configure_token(req: ConfigureTokenRequest):
    """Sets a custom access token for teammate sharing."""
    new_token = req.token.strip()
    if not new_token or len(new_token) < 4:
        raise HTTPException(status_code=400, detail="Token must be at least 4 characters.")
    auth._token = new_token
    token_file = settings.RUNTIME_DIR / "auth_token"
    auth._write_token_file(token_file, new_token)
    return {"status": "success", "token": new_token}


@app.post("/api/host/firewall-rule")
def trigger_firewall_rule():
    """Triggers an elevated Windows prompt to allow inbound traffic on ports 8000 & 8001."""
    import sys
    if sys.platform != "win32":
        return {"status": "skipped", "message": "Firewall configuration only needed on Windows."}
    import subprocess
    cmd = (
        'Start-Process netsh -Verb RunAs -ArgumentList '
        '\'advfirewall firewall add rule name="GConnect Host" dir=in action=allow protocol=TCP localport=8000,8001\''
    )
    try:
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        subprocess.Popen(["powershell", "-NoProfile", "-Command", cmd], creationflags=flags)
        return {"status": "success", "message": "Windows Firewall prompt launched."}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/host/firewall-status")
def get_firewall_status():
    """Queries Windows Firewall to check if inbound rules for ports 8000 & 8001 exist."""
    import sys
    if sys.platform != "win32":
        return {"status": "ok", "allowed": True, "message": "Non-Windows platform - firewall check skipped"}
    import subprocess
    try:
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        res = subprocess.run(
            ["netsh", "advfirewall", "firewall", "show", "rule", 'name="GConnect Host"'],
            capture_output=True,
            text=True,
            timeout=2.0,
            creationflags=flags,
        )
        allowed = res.returncode == 0 and "Action: Allow" in res.stdout or "Allow" in res.stdout
        return {
            "status": "ok",
            "allowed": allowed,
            "rule_found": res.returncode == 0,
            "message": "Firewall rule active" if allowed else "Inbound traffic rule not found",
        }
    except Exception as e:
        return {"status": "unknown", "allowed": False, "message": str(e)}


# ----------------------------------------------------------------------
# GPU cluster (multi-PC / multi-GPU aggregation)
# ----------------------------------------------------------------------

from pydantic import BaseModel  # noqa: E402


class ClusterNodeAddRequest(BaseModel):
    name: str
    url: str
    token: str


class ClusterNodeRemoveRequest(BaseModel):
    name: str


@app.get("/api/cluster/status")
def get_cluster_status():
    """Live status of this PC plus every registered remote GConnect node:
    per-node GPUs, pooled VRAM totals."""
    return cluster_manager.get_cluster_status()


@app.get("/api/cluster/nodes")
def list_cluster_nodes():
    """Registered nodes (tokens are never included)."""
    return {"nodes": cluster_manager.list_nodes()}


@app.post("/api/cluster/nodes")
def add_cluster_node(req: ClusterNodeAddRequest):
    try:
        node = cluster_manager.add_node(req.name, req.url, req.token)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"status": "success", "node": node}


@app.delete("/api/cluster/nodes")
def remove_cluster_node(req: ClusterNodeRemoveRequest):
    removed = cluster_manager.remove_node(req.name)
    if not removed:
        raise HTTPException(status_code=404, detail="Node not found.")
    return {"status": "success", "removed": req.name}


@app.websocket("/ws/telemetry")
async def websocket_telemetry(websocket: WebSocket):
    if not await auth.authorize_websocket(websocket):
        return
    await websocket.accept()
    await gpu_telemetry_service.stream_telemetry(websocket)


@app.websocket("/ws/terminal")
async def websocket_terminal(websocket: WebSocket):
    await pty_bridge.handle_websocket(websocket)


if FRONTEND_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(FRONTEND_DIR)), name="static")

    @app.get("/")
    async def serve_index():
        # Relative asset paths inside index.html work from /static/index.html
        # both when served by the daemon and when bundled in the desktop app.
        return RedirectResponse(url="/static/index.html")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "backend.main:app",
        host=settings.effective_host,
        port=settings.PORT,
        reload=True,
        access_log=False,  # query strings carry auth tokens - keep them out of logs
    )
