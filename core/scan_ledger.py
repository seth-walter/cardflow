"""A durable record of which scans have already become listings.

The folder watcher only remembers files for the life of one watcher object, and a new
one is built every time Start is pressed. Without this, pressing Start again re-imports
anything still sitting in the inbox and creates a second draft for the same card.

Scans are keyed by content, not filename: PaperStream restarts its numbering, so
image0017.jpg today is a different card from image0017.jpg last week.
"""

import hashlib
import json
import os
import threading
from datetime import datetime
from typing import Any, Dict, Optional

LEDGER_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "data", "processed_scans.json",
)

_lock = threading.Lock()


def fingerprint(path) -> str:
    """Content hash of a scan. Empty string when the file cannot be read."""
    try:
        with open(str(path), "rb") as handle:
            return hashlib.md5(handle.read()).hexdigest()
    except OSError:
        return ""


def _load() -> Dict[str, Any]:
    try:
        with open(LEDGER_PATH, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _save(data: Dict[str, Any]) -> None:
    try:
        os.makedirs(os.path.dirname(LEDGER_PATH), exist_ok=True)
        with open(LEDGER_PATH, "w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=2, sort_keys=True)
    except OSError:
        pass


def lookup(path) -> Optional[Dict[str, Any]]:
    """What this scan became last time, or None if it is new."""
    key = fingerprint(path)
    if not key:
        return None

    with _lock:
        return _load().get(key)


def record(path, sku: str = "", title: str = "", offer_id: str = "") -> None:
    """Remember that this scan produced a listing."""
    key = fingerprint(path)
    if not key:
        return

    with _lock:
        data = _load()
        data[key] = {
            "sku": sku,
            "title": title,
            "offer_id": offer_id,
            "filename": os.path.basename(str(path)),
            "processed_at": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S"),
        }
        _save(data)


def forget_all() -> int:
    """Clear the history, so previously scanned cards can be listed again."""
    with _lock:
        count = len(_load())
        _save({})
        return count


def count() -> int:
    with _lock:
        return len(_load())
