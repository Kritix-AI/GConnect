from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="CONNECT_", case_sensitive=False)

    # Network bindings
    HOST: str = "0.0.0.0"
    PORT: int = 8000
    INFERENCE_PORT: int = 8001

    # Directory configuration
    BASE_DIR: Path = Path(__file__).resolve().parent.parent
    WORKSPACE_DIR: Path = BASE_DIR / "workspace"
    HF_CACHE_DIR_NAME: str = ".hf_cache"

    # Ephemeral model cache mode (Google Colab style)
    EPHEMERAL_MODE: bool = False

    # Docker sandbox configuration
    CONTAINER_NAME: str = "connect_dev_sandbox"
    CONTAINER_IMAGE: str = "pytorch/pytorch:latest"
    SHM_SIZE: str = "8g"
    AUTO_START_CONTAINER: bool = True

    # Telemetry configuration
    TELEMETRY_INTERVAL_SECONDS: float = 1.0


settings = Settings()

# Ensure workspace and hugging face cache directories exist
settings.WORKSPACE_DIR.mkdir(parents=True, exist_ok=True)
if not settings.EPHEMERAL_MODE:
    (settings.WORKSPACE_DIR / settings.HF_CACHE_DIR_NAME).mkdir(parents=True, exist_ok=True)
