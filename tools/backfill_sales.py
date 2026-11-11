"""Pull your completed eBay sales into a local comp store.

These are the best comps you can get for your own inventory: same market, same photos,
same seller, same shipping. They cost nothing and need nobody's approval.

Run after tools/ebay_consent.py:

    python tools/backfill_sales.py            # last 24 months
    python tools/backfill_sales.py 6          # last 6 months
"""

import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import requests

from services.ebay_auth import refresh_ebay_user_token

ORDERS_URL = "https://api.ebay.com/sell/fulfillment/v1/order"
STORE_PATH = Path(__file__).resolve().parents[1] / "data" / "own_sales.json"
PAGE_SIZE = 200


def fetch_orders(token, months):
    since = (datetime.utcnow() - timedelta(days=30 * months)).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    headers = {"Authorization": "Bearer " + token, "Content-Type": "application/json"}
    offset, sales, seen_orders = 0, [], 0

    while True:
        response = requests.get(
            ORDERS_URL,
            headers=headers,
            params={
                "filter": "creationdate:[{}..]".format(since),
                "limit": PAGE_SIZE,
                "offset": offset,
            },
            timeout=45,
        )

        if response.status_code == 403:
            raise SystemExit(
                "403 from eBay: the token lacks sell.fulfillment.readonly.\n"
                "Run python tools/ebay_consent.py first."
            )
        if response.status_code >= 400:
            raise SystemExit("eBay error {}: {}".format(response.status_code, response.text[:400]))

        payload = response.json()
        orders = payload.get("orders", [])
        seen_orders += len(orders)

        for order in orders:
            sold_on = order.get("creationDate", "")[:10]
            for line in order.get("lineItems", []):
                total = (line.get("total") or {}).get("value")
                if not total:
                    continue
                sales.append({
                    "title": line.get("title", ""),
                    "sku": line.get("sku", ""),
                    "price": float(total),
                    "quantity": line.get("quantity", 1),
                    "sold_on": sold_on,
                    "legacy_item_id": line.get("legacyItemId", ""),
                })

        print("  fetched {} order(s), {} line item(s) so far".format(seen_orders, len(sales)))

        if len(orders) < PAGE_SIZE:
            break
        offset += PAGE_SIZE

    return sales


def main():
    months = int(sys.argv[1]) if len(sys.argv) > 1 else 24

    print("Refreshing eBay token...")
    token = refresh_ebay_user_token()

    print("Pulling sales from the last {} month(s)...".format(months))
    sales = fetch_orders(token, months)

    if not sales:
        print("\nNo sales returned. Nothing to store.")
        return 0

    STORE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STORE_PATH.write_text(json.dumps({
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "months": months,
        "count": len(sales),
        "sales": sales,
    }, indent=2), encoding="utf-8")

    prices = sorted(sale["price"] for sale in sales)
    middle = prices[len(prices) // 2]

    print("\nStored {} sold line item(s) in {}".format(len(sales), STORE_PATH))
    print("   price range: ${:.2f} - ${:.2f}, median ${:.2f}".format(
        prices[0], prices[-1], middle))
    print("\nThe app will now price a card from your own past sales whenever one matches.")
    print("Next: python tools/calibrate_prices.py  (measures how far asking prices run high)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
