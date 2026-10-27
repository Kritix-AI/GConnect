"""Revocable, jobs-only developer access tokens for the shared GPU pilot."""

import hashlib
import json
import logging
import secrets
import threading
import time
from pathlib import Path
from typing import Any, Dict, Optional

from backend.config import settings

logger = logging.getLogger("connect.access_tokens")


class DeveloperTokenStore:
    def __init__(self):
        self._lock = threading.RLock()
        self._tokens: Dict[str, Dict[str, Any]] = {}
        self._load()

    @property
    def path(self) -> Path:
        return settings.RUNTIME_DIR / "developer_tokens.json"

    def _load(self) -> None:
        try:
            if self.path.exists():
                data = json.loads(self.path.read_text(encoding="utf-8"))
                self._tokens = {
                    item["id"]: item for item in data.get("tokens", [])
                    if isinstance(item, dict) and item.get("id") and item.get("token_hash")
                }
        except Exception as exc:
            logger.warning("Could not load developer access tokens: %s", type(exc).__name__)
            self._tokens = {}

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.path.with_suffix(".tmp")
        temp.write_text(json.dumps({"tokens": list(self._tokens.values())}, indent=2), encoding="utf-8")
        temp.replace(self.path)
        try:
            import os
            os.chmod(self.path, 0o600)
        except Exception:
            pass

    def create(self, name: str) -> Dict[str, str]:
        name = (name or "").strip()
        if not name or len(name) > 80:
            raise ValueError("Token name must contain 1 to 80 characters.")
        token_id = secrets.token_hex(8)
        token = "gct_" + secrets.token_urlsafe(32)
        record = {
            "id": token_id,
            "name": name,
            "token_hash": hashlib.sha256(token.encode("utf-8")).hexdigest(),
            "last_four": token[-4:],
            "created_at": time.time(),
            "revoked": False,
        }
        with self._lock:
            self._tokens[token_id] = record
            self._save()
        # Raw value is returned exactly once and is never written to disk.
        return {"id": token_id, "name": name, "token": token}

    def resolve(self, token: Optional[str]) -> Optional[str]:
        if not token or not token.startswith("gct_"):
            return None
        digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
        with self._lock:
            for token_id, record in self._tokens.items():
                if not record.get("revoked") and secrets.compare_digest(digest, record.get("token_hash", "")):
                    return token_id
        return None

    def list(self) -> list[Dict[str, Any]]:
        with self._lock:
            return [
                {k: v for k, v in item.items() if k != "token_hash"}
                for item in sorted(self._tokens.values(), key=lambda item: item.get("created_at", 0), reverse=True)
            ]

    def revoke(self, token_id: str) -> bool:
        with self._lock:
            item = self._tokens.get(token_id)
            if not item or item.get("revoked"):
                return False
            item["revoked"] = True
            item["revoked_at"] = time.time()
            self._save()
            return True


developer_tokens = DeveloperTokenStore()
