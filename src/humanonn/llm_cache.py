from __future__ import annotations

import hashlib
import json
import time
from threading import Lock
from typing import Any

_lock = Lock()
_CACHE: dict[str, tuple[float, int, Any]] = {}


def _now() -> float:
    return time.time()


def compute_cache_key(
    provider: str,
    model: str,
    system_prompt: str,
    user_payload: dict[str, Any],
    image_paths: list[str] | None,
    temperature: float,
) -> str:
    # Normalize payload and prompt deterministically
    normalized_payload = json.dumps(user_payload or {}, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    image_part = "" if not image_paths else ",".join(image_paths)
    key_source = f"{provider}|{model}|{temperature}|{system_prompt}|{normalized_payload}|{image_part}"
    return hashlib.sha256(key_source.encode("utf-8")).hexdigest()


def get_cache(key: str) -> Any | None:
    now = _now()
    with _lock:
        entry = _CACHE.get(key)
        if not entry:
            return None
        ts, ttl_seconds, value = entry
        if ts + ttl_seconds < now:
            # expired
            del _CACHE[key]
            return None
        return value


def set_cache(key: str, value: Any, ttl_days: int = 7) -> None:
    ttl_seconds = int(ttl_days) * 24 * 3600
    with _lock:
        _CACHE[key] = (_now(), ttl_seconds, value)


def stats() -> dict[str, int]:
    with _lock:
        return {"entries": len(_CACHE)}
