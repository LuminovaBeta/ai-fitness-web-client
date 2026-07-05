import threading
import time
from typing import Dict, Any


_lock = threading.Lock()
_session_realtime_cache: Dict[str, Dict[str, Any]] = {}


def upsert_session_realtime(session_id: str, payload: Dict[str, Any]) -> None:
    now_ts = time.time()
    with _lock:
        entry = _session_realtime_cache.get(session_id, {})
        entry.update(payload)
        entry["updated_at"] = now_ts
        _session_realtime_cache[session_id] = entry


def get_session_realtime(session_id: str, ttl_sec: int = 20) -> Dict[str, Any]:
    if not session_id:
        return {}

    now_ts = time.time()
    with _lock:
        entry = _session_realtime_cache.get(session_id)
        if not entry:
            return {}

        updated_at = float(entry.get("updated_at", 0) or 0)
        if ttl_sec > 0 and now_ts - updated_at > ttl_sec:
            _session_realtime_cache.pop(session_id, None)
            return {}

        return dict(entry)


def pop_session_realtime(session_id: str) -> Dict[str, Any]:
    with _lock:
        return _session_realtime_cache.pop(session_id, {})
