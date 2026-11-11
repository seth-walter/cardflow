"""Which listings are sitting unsold, and what to do about it.

A card that has not sold is telling you one of two things: nobody is seeing it, or
everybody who sees it thinks it is too expensive. Promotion fixes the first and makes
the second worse, so the suggestion depends on both age and what the comps say.
"""

from datetime import datetime
from typing import Any, Dict, List, Optional

# Ad rates are a ladder against how long a card has sat. eBay shows its own suggested
# rate per category in Seller Hub; prefer that number when it disagrees with this one.
FRESH_DAYS = 30
SLOW_DAYS = 60
STALE_DAYS = 90

ACTION_WAIT = "wait"
ACTION_PROMOTE = "promote"
ACTION_PRICE = "reprice first"
ACTION_RETHINK = "rethink"


def days_listed(row: Dict[str, Any], now: Optional[datetime] = None) -> Optional[int]:
    """Days since the listing first went up."""
    stamp = (row.get("start_time") or "").strip()
    if not stamp:
        return None

    for fmt in ("%Y-%m-%dT%H:%M:%S.%fZ", "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S"):
        try:
            started = datetime.strptime(stamp, fmt)
            break
        except ValueError:
            started = None

    if started is None:
        return None

    return max(0, int(((now or datetime.utcnow()) - started).total_seconds() // 86400))


def suggest_promotion(row: Dict[str, Any], price_data: Optional[Dict[str, Any]] = None,
                      now: Optional[datetime] = None) -> Dict[str, Any]:
    """What to do with an unsold listing, and why."""
    age = days_listed(row, now)
    watchers = int(row.get("watch_count") or 0)

    result = {
        "days_listed": age,
        "watchers": watchers,
        "action": ACTION_WAIT,
        "ad_rate": 0.0,
        "reason": "",
        "overpriced": False,
    }

    if age is None:
        result["reason"] = "no listing date available"
        return result

    # A price well above the comps is not a visibility problem. Paying to show more
    # people an overpriced card just costs money.
    if price_data and price_data.get("basis") == "sold" and not price_data.get("needs_review"):
        try:
            market = float(price_data.get("estimated_price") or 0)
            price = float(row.get("price") or 0)
        except (TypeError, ValueError):
            market = price = 0.0

        if market and price and price > market * 1.25:
            result.update({
                "action": ACTION_PRICE,
                "overpriced": True,
                "reason": "listed at ${:.2f} against a ${:.2f} sold median - lower the "
                          "price before paying to promote it".format(price, market),
            })
            return result

    if age < FRESH_DAYS:
        result["reason"] = "only {} days old - give it time".format(age)
        return result

    if age < SLOW_DAYS:
        if watchers:
            result.update({"action": ACTION_PROMOTE, "ad_rate": 2.0,
                           "reason": "{} days old with {} watcher(s) - interest exists, "
                                     "a small ad rate may close it".format(age, watchers)})
        else:
            result.update({"action": ACTION_PROMOTE, "ad_rate": 3.0,
                           "reason": "{} days old and nobody is watching - it needs to "
                                     "be seen".format(age)})
        return result

    if age < STALE_DAYS:
        result.update({"action": ACTION_PROMOTE, "ad_rate": 5.0,
                       "reason": "{} days unsold with {} watcher(s)".format(age, watchers)})
        return result

    result.update({
        "action": ACTION_RETHINK,
        "ad_rate": 7.0,
        "reason": "{} days unsold - promotion alone rarely rescues a card this stale; "
                  "consider an auction or a real price cut too".format(age),
    })
    return result


def stale_listings(rows: List[Dict[str, Any]], min_days: int = FRESH_DAYS,
                   priced: Optional[Dict[str, Dict[str, Any]]] = None,
                   now: Optional[datetime] = None) -> List[Dict[str, Any]]:
    """Listings old enough to act on, oldest first, each with its suggestion."""
    out = []

    for row in rows:
        age = days_listed(row, now)
        if age is None or age < min_days:
            continue

        key = row.get("offer_id") or row.get("listing_id") or ""
        suggestion = suggest_promotion(row, (priced or {}).get(key), now)
        out.append(dict(row, **suggestion))

    return sorted(out, key=lambda r: r["days_listed"], reverse=True)
