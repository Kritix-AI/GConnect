import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, WebSocket
from fastapi.responses import FileResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles

from backend import auth
from backend.access_tokens import developer_tokens
from backend.cluster_manager import cluster_manager
from backend.config import settings
from backend.docker_manager import docker_manager
from backend.file_manager import router as files_router
from backend.gpu_telemetry import gpu_telemetry_service
from backend.job_manager import job_manager
from backend.linter import router as lint_router
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

    if settings.AUTO_START_CONTAINER and docker_manager.docker_available:
        try:
            logger.info("Initializing Docker sandbox container...")
            docker_manager.get_or_create_container()
        except Exception as e:
            logger.warning(f"Could not auto-start container: {e}")

    yield

    await gpu_telemetry_service.stop()
    logger.info("GConnect Host Daemon shutdown.")


app = FastAPI(
    title="GConnect - Remote GPU Development Platform",
    description="Lightweight remote GPU development platform tailored for voice and multimodal AI pipelines.",
    version="2.2.0",
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
# GPU cluster (multi-PC / multi-GPU aggregation)
# ----------------------------------------------------------------------

from pydantic import BaseModel, Field  # noqa: E402


class ClusterNodeAddRequest(BaseModel):
    name: str
    url: str
    token: str
    jobs_enabled: bool = False


class ClusterNodeRemoveRequest(BaseModel):
    name: str


class ClusterNodeSharingRequest(BaseModel):
    name: str
    enabled: bool


class DeveloperTokenCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=80)


class GPUJobSubmitRequest(BaseModel):
    name: str = Field(default="GPU job", max_length=80)
    script: str = Field(min_length=1, max_length=1024 * 1024)
    gpu_count: int = Field(default=1, ge=1, le=16)
    node: str | None = Field(default=None, max_length=120)
    timeout_seconds: int = Field(default=3600, ge=30, le=6 * 60 * 60)
    network_access: bool = False
    distributed: bool = False


class GPUWorkerJobRequest(BaseModel):
    id: str = Field(min_length=1, max_length=64)
    name: str = Field(max_length=80)
    script: str = Field(min_length=1, max_length=1024 * 1024)
    gpu_count: int = Field(ge=1, le=16)
    timeout_seconds: int = Field(ge=30, le=6 * 60 * 60)
    network_access: bool = False
    distributed: bool = False


class GPUWorkerJobsStatusRequest(BaseModel):
    ids: list[str] = Field(min_length=1, max_length=100)


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
        node = cluster_manager.add_node(req.name, req.url, req.token, req.jobs_enabled)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"status": "success", "node": node}


@app.post("/api/cluster/nodes/sharing")
def set_cluster_node_sharing(req: ClusterNodeSharingRequest):
    if not cluster_manager.set_jobs_enabled(req.name, req.enabled):
        raise HTTPException(status_code=404, detail="Node not found.")
    cancelled = job_manager.cancel_node_jobs(req.name) if not req.enabled else 0
    return {"status": "success", "name": req.name, "jobs_enabled": req.enabled, "jobs_cancelled": cancelled}


@app.post("/api/cluster/local-sharing")
def set_local_gpu_sharing(req: ClusterNodeSharingRequest):
    if req.name != "this-pc":
        raise HTTPException(status_code=400, detail="Invalid local node name.")
    cluster_manager.set_local_jobs_enabled(req.enabled)
    cancelled = job_manager.cancel_provider_jobs() if not req.enabled else 0
    return {"status": "success", "name": req.name, "jobs_enabled": req.enabled, "jobs_cancelled": cancelled}


@app.get("/api/access-tokens")
def list_developer_tokens():
    return {"tokens": developer_tokens.list()}


@app.post("/api/access-tokens", status_code=201)
def create_developer_token(req: DeveloperTokenCreateRequest):
    try:
        return developer_tokens.create(req.name)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.delete("/api/access-tokens/{token_id}")
def revoke_developer_token(token_id: str):
    if not developer_tokens.revoke(token_id):
        raise HTTPException(status_code=404, detail="Developer token not found or already revoked.")
    stopped = job_manager.cancel_owner_jobs(token_id)
    return {"status": "revoked", "id": token_id, "jobs_cancelled": stopped}


@app.delete("/api/cluster/nodes")
def remove_cluster_node(req: ClusterNodeRemoveRequest):
    removed = cluster_manager.remove_node(req.name)
    if not removed:
        raise HTTPException(status_code=404, detail="Node not found.")
    return {"status": "success", "removed": req.name}


@app.get("/api/jobs")
def list_gpu_jobs(request: Request):
    """Coordinator job list. Provider secrets are removed from every response."""
    return job_manager.list_jobs(auth.job_owner_id(request))


@app.post("/api/jobs", status_code=202)
def submit_gpu_job(req: GPUJobSubmitRequest, request: Request):
    """Schedule a bounded Python job on the local or a registered GPU node."""
    try:
        job = job_manager.submit(
            name=req.name,
            script=req.script,
            gpu_count=req.gpu_count,
            node_name=req.node,
            timeout_seconds=req.timeout_seconds,
            network_access=req.network_access,
            distributed=req.distributed,
            owner_id=auth.job_owner_id(request),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"status": "accepted", "job": job}


@app.get("/api/jobs/{job_id}")
def get_gpu_job(job_id: str, request: Request):
    job = job_manager.get_job(job_id, auth.job_owner_id(request))
    if not job:
        raise HTTPException(status_code=404, detail="Job not found.")
    return job


@app.get("/api/jobs/{job_id}/artifacts")
def download_gpu_job_artifacts(job_id: str, request: Request):
    try:
        archive = job_manager.artifact_bytes(job_id, auth.job_owner_id(request))
    except KeyError:
        raise HTTPException(status_code=404, detail="Job not found.")
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=413, detail=str(exc))
    except Exception as exc:
        logger.warning("Could not collect artifacts for job %s: %s", job_id, type(exc).__name__)
        raise HTTPException(status_code=502, detail="Could not retrieve job artifacts.") from exc
    return Response(
        content=archive,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="gconnect-{job_id[:12]}-output.zip"'},
    )


@app.post("/api/jobs/{job_id}/cancel")
def cancel_gpu_job(job_id: str, request: Request):
    if not job_manager.cancel(job_id, auth.job_owner_id(request)):
        raise HTTPException(status_code=409, detail="Job is not running or could not be reached.")
    return {"status": "cancelling", "id": job_id}


@app.post("/api/worker/jobs", status_code=202)
def start_worker_job(req: GPUWorkerJobRequest):
    """Internal coordinator-to-provider dispatch endpoint."""
    if not cluster_manager.local_jobs_enabled:
        raise HTTPException(status_code=403, detail="This provider has not enabled local GPU job sharing.")
    if not req.id.isalnum():
        raise HTTPException(status_code=400, detail="Invalid job id.")
    try:
        job_manager.start_worker(req.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        logger.exception("Could not start worker job")
        raise HTTPException(status_code=503, detail="Provider could not start the job.") from exc
    return {"status": "accepted", "id": req.id}


@app.post("/api/worker/jobs/status")
def get_worker_jobs_status(req: GPUWorkerJobsStatusRequest):
    if any(not job_id.isalnum() or len(job_id) > 64 for job_id in req.ids):
        raise HTTPException(status_code=400, detail="Invalid job id in status request.")
    return job_manager.worker_jobs(req.ids)


@app.get("/api/worker/status")
def worker_status():
    return {"jobs_enabled": cluster_manager.local_jobs_enabled}


@app.get("/api/worker/jobs/{job_id}")
def get_worker_job(job_id: str):
    try:
        return job_manager.worker_job(job_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Job not found.")


@app.get("/api/worker/jobs/{job_id}/artifacts")
def download_worker_job_artifacts(job_id: str):
    try:
        archive = job_manager.artifact_bytes(job_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Job not found.")
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=413, detail=str(exc))
    return Response(content=archive, media_type="application/zip")


@app.post("/api/worker/jobs/{job_id}/cancel")
def cancel_worker_job(job_id: str):
    if not job_manager.cancel_worker(job_id):
        raise HTTPException(status_code=409, detail="Job is not running.")
    return {"status": "cancelling", "id": job_id}


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
