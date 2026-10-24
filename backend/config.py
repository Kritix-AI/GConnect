from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="CONNECT_",
        case_sensitive=False,
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Network bindings
    # SECURITY: default bind is loopback only. Exposing the daemon to the LAN
    # requires CONNECT_ALLOW_LAN=true and a configured CONNECT_AUTH_TOKEN.
    HOST: str = "127.0.0.1"
    PORT: int = 8000
    INFERENCE_PORT: int = 8001
    ALLOW_LAN: bool = False

    # Authentication
    # If AUTH_TOKEN is set (env/.env), it is used as the shared secret.
    # Otherwise a high-entropy token is generated once and persisted to
    # RUNTIME_DIR/auth_token with owner-only permissions.
    AUTH_TOKEN: str = ""
    AUTH_DISABLED: bool = False  # dev-only escape hatch; forces loopback bind

    # Directory configuration
    BASE_DIR: Path = Path(__file__).resolve().parent.parent
    WORKSPACE_DIR: Path = BASE_DIR / "workspace"
    RUNTIME_DIR: Path = BASE_DIR / ".gconnect"
    LOG_DIR: Path = BASE_DIR / ".gconnect" / "logs"
    HF_CACHE_DIR_NAME: str = ".hf_cache"

    # Ephemeral model cache mode (Google Colab style)
    EPHEMERAL_MODE: bool = False

    # Docker sandbox configuration
    CONTAINER_NAME: str = "connect_dev_sandbox"
    CONTAINER_IMAGE: str = "pytorch/pytorch:latest"
    SHM_SIZE: str = "8g"
    AUTO_START_CONTAINER: bool = True

    # Telemetry configuration (2 Hz for a live feel, like Task Manager)
    TELEMETRY_INTERVAL_SECONDS: float = 0.5

    # Local shell fallback when Docker is unavailable.
    # SECURITY: disabled by default - it runs a shell on the HOST machine.
    ALLOW_LOCAL_FALLBACK: bool = False

    # File API limits
    MAX_CONTENT_READ_BYTES: int = 5 * 1024 * 1024  # 5 MB for /api/files/content

    @property
    def effective_host(self) -> str:
        """Resolved bind host: loopback unless LAN exposure is explicitly enabled."""
        if self.ALLOW_LAN:
            return self.HOST if self.HOST != "127.0.0.1" else "0.0.0.0"
        return "127.0.0.1"


settings = Settings()

# Ensure workspace and hugging face cache directories exist
settings.WORKSPACE_DIR.mkdir(parents=True, exist_ok=True)
if not settings.EPHEMERAL_MODE:
    (settings.WORKSPACE_DIR / settings.HF_CACHE_DIR_NAME).mkdir(parents=True, exist_ok=True)
settings.RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
