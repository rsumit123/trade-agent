"""Symmetric encryption for per-user API keys at rest.

Uses Fernet (cryptography lib). The key lives in env var SESSION_KEY_FERNET.
If not set, we generate one at startup and write to ./.fernet-key for
dev convenience — but this means previously-encrypted values won't decrypt
across restarts unless the key is persisted in the env. In production, set
SESSION_KEY_FERNET to a stable value.
"""

import os
from pathlib import Path

try:
    from cryptography.fernet import Fernet
except ImportError:
    Fernet = None  # type: ignore


_FERNET_FILE = Path(__file__).parent.parent / ".fernet-key"
_cached_fernet = None


def _get_fernet():
    global _cached_fernet
    if _cached_fernet is not None:
        return _cached_fernet
    if Fernet is None:
        raise RuntimeError("cryptography is not installed. pip install cryptography")
    key = os.environ.get("SESSION_KEY_FERNET", "").strip()
    if not key:
        # Dev fallback — persist a generated key so it survives restarts
        if _FERNET_FILE.exists():
            key = _FERNET_FILE.read_text().strip()
        else:
            key = Fernet.generate_key().decode()
            _FERNET_FILE.write_text(key)
            try:
                _FERNET_FILE.chmod(0o600)
            except Exception:
                pass
    _cached_fernet = Fernet(key.encode() if isinstance(key, str) else key)
    return _cached_fernet


def encrypt(plain: str) -> str:
    if not plain:
        return ""
    return _get_fernet().encrypt(plain.encode()).decode()


def decrypt(token: str) -> str:
    if not token:
        return ""
    try:
        return _get_fernet().decrypt(token.encode()).decode()
    except Exception:
        return ""
