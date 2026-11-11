"""A record of when each listing was last repriced.

Two problems without it. Inside a session, a row keeps its old price after being
updated, so it still looks like it needs changing and can be sent twice. Across
sessions, a listing whose comps drift slightly gets repriced again and again, which
churns your listings and resets their search standing for a few cents.
"""

import json
import os
import threading
from datetime import datetime
from typing import Any, Dict, Optional

HISTORY_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "data", "reprice_history.json",
)

_lock = threading.Lock()
_STAMP = "%Y-%m-%dT%H:%M:%S"


def _load() -> Dict[str, Any]:
    try:
        with open(HISTORY_PATH, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _save(data: Dict[str, Any]) -> None:
    try:
        os.makedirs(os.path.dirname(HISTORY_PATH), exist_ok=True)
        with open(HISTORY_PATH, "w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=2, sort_keys=True)
    except OSError:
        pass


def record(offer_id: str, price: float, title: str = "", previous_price=None) -> None:
    if not offer_id:
        return

    with _lock:
        data = _load()
        data[str(offer_id)] = {
            "price": round(float(price), 2),
            "previous_price": round(float(previous_price), 2) if previous_price else None,
            "title": title,
            "updated_at": datetime.utcnow().strftime(_STAMP),
        }
        _save(data)


def lookup(offer_id: str) -> Optional[Dict[str, Any]]:
    if not offer_id:
        return None
    with _lock:
        return _load().get(str(offer_id))


def days_since(offer_id: str) -> Optional[float]:
    """How long since this listing was repriced, or None if it never was."""
    entry = lookup(offer_id)
    if not entry:
        return None

    try:
        when = datetime.strptime(entry["updated_at"], _STAMP)
    except (KeyError, TypeError, ValueError):
        return None

    return round((datetime.utcnow() - when).total_seconds() / 86400.0, 2)


def count() -> int:
    with _lock:
        return len(_load())


def forget_all() -> int:
    with _lock:
        total = len(_load())
        _save({})
        return total
