"""
Short-lived in-memory cache of rendered PDFs so the webchat frontend can
download them via HTTP. Keyed by a URL-safe token; TTL 30 minutes.

Simple and process-local — if we scale to multiple uvicorn workers we'll move
this to Redis or disk. Not worth solving until that happens.
"""
from __future__ import annotations

import secrets
import threading
import time
from dataclasses import dataclass

_TTL_SECONDS = 30 * 60  # 30 minutes


@dataclass
class _Entry:
    payload: bytes
    filename: str
    content_type: str
    created_at: float


_cache: dict[str, _Entry] = {}
_lock = threading.Lock()


def _evict_expired(now: float) -> None:
    for k in list(_cache.keys()):
        if now - _cache[k].created_at > _TTL_SECONDS:
            del _cache[k]


def put(payload: bytes, filename: str, content_type: str = "application/pdf") -> str:
    with _lock:
        _evict_expired(time.time())
        token = secrets.token_urlsafe(12)
        _cache[token] = _Entry(
            payload=payload,
            filename=filename,
            content_type=content_type,
            created_at=time.time(),
        )
        return token


def get(token: str) -> _Entry | None:
    with _lock:
        _evict_expired(time.time())
        return _cache.get(token)
