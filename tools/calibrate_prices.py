"""Measures how far eBay asking prices run above what cards actually sell for.

For a sample of your own past sales, this asks eBay what that card is being *asked* for
today and compares it to what it *sold* for. The median ratio is the correction factor
for every ask-based price the app produces - derived from your own realised sales rather
than a guess.

Run after tools/backfill_sales.py:

    python tools/calibrate_prices.py           # samples 40 sales
    python tools/calibrate_prices.py 100       # samples 100 (slower, more solid)
"""

import json
import random
import sys
from pathlib import Path
from statistics import median

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.ebay_browse import ebay_browse_search, summarize_comps

STORE_PATH = Path(__file__).resolve().parents[1] / "data" / "own_sales.json"
MIN_COMPS = 3


def load_sales():
    if not STORE_PATH.exists():
        raise SystemExit("No sales store found. Run python tools/backfill_sales.py first.")
    data = json.loads(STORE_PATH.read_text(encoding="utf-8"))
    return data.get("sales", [])


def main():
    sample_size = int(sys.argv[1]) if len(sys.argv) > 1 else 40
    sales = [s for s in load_sales() if s.get("title") and s.get("price")]

    if not sales:
        raise SystemExit("The sales store is empty.")

    random.seed(7)  # same sample each run, so the number is comparable over time
    sample = random.sample(sales, min(sample_size, len(sales)))

    print("Comparing {} of your sales against today's asking prices...\n".format(len(sample)))
    print("%-52s %9s %9s %6s" % ("CARD", "SOLD", "ASKING", "RATIO"))

    ratios = []
    skipped = 0

    for sale in sample:
        title = sale["title"]
        try:
            summary = summarize_comps(ebay_browse_search(title, limit=12))
        except Exception:
            skipped += 1
            continue

        if summary["count"] < MIN_COMPS or not summary["median"]:
            skipped += 1
            continue

        ratio = sale["price"] / summary["median"]
        ratios.append(ratio)
        print("%-52s %9.2f %9.2f %6.2f" % (title[:52], sale["price"], summary["median"], ratio))

    if len(ratios) < 5:
        raise SystemExit(
            "\nOnly {} usable comparison(s) - not enough to calibrate. "
            "Try a larger sample.".format(len(ratios))
        )

    factor = round(median(ratios), 2)

    print("\n%d comparison(s) used, %d skipped for thin comps." % (len(ratios), skipped))
    print("Median sold/asking ratio: %.2f" % factor)

    if factor >= 0.95:
        print("\nYour asking-price medians already track your realised prices closely.")
        print("Leave ASK_TO_SOLD_FACTOR at 1.0.")
    else:
        print("\nAsking prices run about {:.0f}% above what you actually realise.".format(
            (1 / factor - 1) * 100))
        print("Set this in .env to correct every ask-based price:\n")
        print("    ASK_TO_SOLD_FACTOR={}".format(factor))

    print("\nNote: this compares past sales against today's asking prices, so a hot or "
          "cooling market shifts it. Re-run occasionally.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
