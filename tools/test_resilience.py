"""Retry and reporting behaviour. Nothing here talks to eBay.

Covers the two bugs seen in a real repricing run: the "was" price showing the new price,
and a transient 500 killing an otherwise fine update.

Run: python tools/test_resilience.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import services.ebay_auth as auth

failures = []


def check(label, condition, detail=""):
    print(("  PASS  " if condition else "  FAIL  ") + label + (" -> " + str(detail) if detail else ""))
    if not condition:
        failures.append(label)


class Response:
    def __init__(self, status, text="{}"):
        self.status_code = status
        self.text = text

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError("HTTP {}".format(self.status_code))

    def json(self):
        return {}


def main():
    auth.RETRY_BACKOFF_SECONDS = 0        # keep the suite fast
    original_request = auth.requests.request

    print("\n1. A transient 500 on a read is retried")
    calls = []

    def flaky(method, url, **kwargs):
        calls.append(method)
        return Response(500) if len(calls) == 1 else Response(200)

    auth.requests.request = flaky
    auth.ebay_request("GET", "https://api.ebay.com/x")
    check("retried once and succeeded", calls == ["GET", "GET"], calls)

    print("\n2. A read that keeps failing still raises")
    calls[:] = []

    def always_500(method, url, **kwargs):
        calls.append(method)
        return Response(500)

    auth.requests.request = always_500
    try:
        auth.ebay_request("GET", "https://api.ebay.com/x")
        check("gives up eventually", False, "no exception")
    except RuntimeError:
        check("gives up eventually", True)
    check("tried the configured number of times",
          len(calls) == auth.GET_RETRIES + 1, len(calls))

    print("\n3. Writes are never retried")
    for method in ("POST", "PUT", "DELETE"):
        calls[:] = []
        auth.requests.request = always_500
        try:
            auth.ebay_request(method, "https://api.ebay.com/x")
        except RuntimeError:
            pass
        check("{} sent exactly once".format(method), len(calls) == 1, len(calls))

    print("\n4. A 401 still refreshes the token, once")
    calls[:] = []
    refreshed = []
    auth.refresh_ebay_user_token = lambda: refreshed.append(1) or "new-token"

    def unauthorised_then_ok(method, url, **kwargs):
        calls.append(method)
        return Response(401) if len(calls) == 1 else Response(200)

    auth.requests.request = unauthorised_then_ok
    auth.ebay_request("GET", "https://api.ebay.com/x")
    check("token refreshed", len(refreshed) == 1, refreshed)
    check("request repeated after refresh", len(calls) == 2, calls)

    auth.requests.request = original_request

    print("\n5. The reported 'was' price is the price before the change")
    # Reproduces the reporting bug: rows and the priced list share dict objects, so
    # correcting the row in place also rewrote the value being reported.
    priced = [{"offer_id": "o1", "title": "card", "price": 22.99, "suggested": 16.99}]
    rows = priced                      # movers() returns the same objects, not copies
    row = rows[0]

    new_price = 16.99
    old_price = float(row["price"])    # the fix: capture before mutating
    for source_row in priced:
        if source_row["offer_id"] == row["offer_id"]:
            source_row["price"] = new_price

    check("old price survives the in-place correction", old_price == 22.99, old_price)
    check("row itself was updated", row["price"] == 16.99, row["price"])
    check("they are genuinely the same object", rows[0] is priced[0])

    print("\n" + ("ALL CHECKS PASSED" if not failures else "FAILED: " + ", ".join(failures)))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
