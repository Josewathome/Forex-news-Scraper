"""
auth_manager.py
───────────────
Three independent components:

  EncryptionManager  – securepipe or Fernet fallback (unchanged)
  APIKeyManager      – now async; uses DatabaseManager for storage
  RateLimiter        – in-memory sliding-window per key (unchanged)
"""

from __future__ import annotations

import base64
import hashlib
import logging
import secrets
import time
from collections import defaultdict, deque
from typing import TYPE_CHECKING, Dict, List, Optional

if TYPE_CHECKING:
    from db_manager import DatabaseManager

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Encryption  (securepipe → Fernet fallback)
# ─────────────────────────────────────────────────────────────────────────────

try:
    from securepipe import SecurePipe as _SecurePipe  # type: ignore

    class EncryptionManager:
        """Uses securepipe when installed."""

        def __init__(self, secret_key: str) -> None:
            self._pipe = _SecurePipe(secret_key=secret_key)
            logger.info("EncryptionManager: securepipe")

        def encrypt(self, plaintext: str) -> str:
            return self._pipe.encrypt(plaintext)

        def decrypt(self, ciphertext: str) -> str:
            return self._pipe.decrypt(ciphertext)

except ImportError:
    logger.warning(
        "securepipe not installed – using cryptography.Fernet as fallback. "
        "Install for production: pip install securepipe"
    )
    from cryptography.fernet import Fernet  # type: ignore

    class EncryptionManager:  # type: ignore[no-redef]
        """Fernet fallback (SHA-256 key derivation from the secret string)."""

        def __init__(self, secret_key: str) -> None:
            raw = hashlib.sha256(secret_key.encode()).digest()
            self._fernet = Fernet(base64.urlsafe_b64encode(raw))
            logger.info("EncryptionManager: Fernet fallback")

        def encrypt(self, plaintext: str) -> str:
            return self._fernet.encrypt(plaintext.encode()).decode()

        def decrypt(self, ciphertext: str) -> str:
            return self._fernet.decrypt(ciphertext.encode()).decode()


# ─────────────────────────────────────────────────────────────────────────────
# API Key Manager  (async – backed by DatabaseManager)
# ─────────────────────────────────────────────────────────────────────────────

class APIKeyManager:
    """
    Manages encrypted API keys stored in SQLite via DatabaseManager.
    All public methods are async because they delegate to the async DB layer.
    """

    def __init__(self, db: "DatabaseManager", encryption_key: str) -> None:
        self._db  = db
        self._enc = EncryptionManager(encryption_key)

    async def generate_key(self, name: str, rate_limit: int = 60) -> str:
        """
        Generate a new key, encrypt it, persist it.
        Returns the raw sk-... key. Show once, never store in plaintext.
        Raises ValueError if a key with that name already exists.
        """
        raw = f"sk-{secrets.token_urlsafe(32)}"
        await self._db.add_key(name, self._enc.encrypt(raw), rate_limit)
        return raw

    async def list_keys(self) -> List[Dict]:
        """Return key metadata (name, rate_limit, created_at). Never raw keys."""
        return await self._db.list_keys()

    async def delete_key(self, name: str) -> bool:
        """Delete a key by name. Returns True if deleted."""
        return await self._db.delete_key(name)

    async def verify_key(self, raw_key: str) -> Optional[Dict]:
        """
        Decrypt every stored key and compare.
        Returns {"name": ..., "rate_limit": ...} on match, else None.
        """
        for row in await self._db.get_all_encrypted_keys():
            try:
                if self._enc.decrypt(row["encrypted_key"]) == raw_key:
                    return {
                        "name":       row["name"],
                        "rate_limit": row["rate_limit"],
                    }
            except Exception:
                continue
        return None


# ─────────────────────────────────────────────────────────────────────────────
# Rate Limiter  (in-memory sliding-window, per key name)
# ─────────────────────────────────────────────────────────────────────────────

class RateLimiter:
    """
    Per-key sliding-window rate limiter backed by in-process memory.
    State resets on container restart. Use single Uvicorn worker.
    """

    def __init__(self) -> None:
        self._windows: Dict[str, deque] = defaultdict(deque)

    def is_allowed(self, key_name: str, limit: int, window: int = 60) -> bool:
        now = time.monotonic()
        dq  = self._windows[key_name]
        while dq and dq[0] < now - window:
            dq.popleft()
        if len(dq) >= limit:
            return False
        dq.append(now)
        return True

    def get_remaining(self, key_name: str, limit: int, window: int = 60) -> int:
        now    = time.monotonic()
        active = sum(1 for ts in self._windows[key_name] if ts >= now - window)
        return max(0, limit - active)