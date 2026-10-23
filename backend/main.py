import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from backend.config import settings
from backend.docker_manager import docker_manager
from backend.file_manager import router as files_router
from backend.gpu_telemetry import gpu_telemetry_service
from backend.pty_bridge import pty_bridge

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("connect.main")

FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting Connect GPU Host Daemon...")
    logger.info(f"Workspace Directory: {settings.WORKSPACE_DIR.resolve()}")
    logger.info(f"Hugging Face Cache: {settings.WORKSPACE_DIR.resolve() / settings.HF_CACHE_DIR_NAME}")

    if settings.AUTO_START_CONTAINER and docker_manager.docker_available:
        try:
            logger.info("Initializing Docker sandbox container...")
            docker_manager.get_or_create_container()
        except Exception as e:
            logger.warning(f"Could not auto-start container: {e}")

    yield
    logger.info("Connect Host Daemon shutdown.")


app = FastAPI(
    title="Connect - Remote GPU Development Platform",
    description="Lightweight remote GPU development platform tailored for voice and multimodal AI pipelines.",
    version="1.0.0",
    lifespan=lifespan
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(files_router)


@app.get("/api/container/status")
def get_container_status():
    return docker_manager.get_status()


@app.post("/api/container/restart")
def restart_container():
    success = docker_manager.restart_container()
    return {"status": "success" if success else "failed"}


@app.get("/api/telemetry/snapshot")
def get_telemetry_snapshot():
    return gpu_telemetry_service.get_snapshot()


@app.websocket("/ws/telemetry")
async def websocket_telemetry(websocket: WebSocket):
    await websocket.accept()
    await gpu_telemetry_service.stream_telemetry(websocket)


@app.websocket("/ws/terminal")
async def websocket_terminal(websocket: WebSocket):
    await pty_bridge.handle_websocket(websocket)


if FRONTEND_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(FRONTEND_DIR)), name="static")

    @app.get("/")
    async def serve_index():
        return FileResponse(FRONTEND_DIR / "index.html")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("backend.main:app", host=settings.HOST, port=settings.PORT, reload=True)

