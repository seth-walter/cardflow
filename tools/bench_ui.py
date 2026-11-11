"""Measures how long a script rerun takes, which is what interaction lag is made of.

Streamlit re-executes the page on every widget interaction, so the cost of one rerun
is the cost of one click. Run before and after changes to see if they helped.

Run: python tools/bench_ui.py [cards] [page_size]
"""

import os
import statistics
import sys
import tempfile
import time
from pathlib import Path

# Never load or overwrite the real saved card list.
os.environ["SESSION_STATE_PATH"] = os.path.join(tempfile.mkdtemp(), "session_cards.json")

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PIL import Image

import core.scan_ledger as scan_ledger
import core.scan_session as scan_session_module

PAGE_SIZE_OPTIONS = [5, 10, 20, 50]


def fake_process(front, back=None):
    price = 24.99
    return {
        "sku": "sku-" + front.name, "offer_id": "offer-" + front.name,
        "title": "2025 Topps Chrome Update Series Player Name #SE-7",
        "description": "<p>2025 Topps Chrome Player Name #SE-7</p>" * 4,
        "price": price, "price_source": "ebay_active", "best_offer_enabled": True,
        "min_offer_price": 19.99, "category_id": "261328",
        "category_name": "Trading Card Singles", "card_name": "Player Name",
        "card_data": {
            "card_category": "sports", "year": "2025", "brand": "Topps",
            "set": "Topps Chrome Update Series", "player_athlete": "Player Name",
            "card_number": "SE-7", "sport": "Basketball", "league": "NBA",
            "team": "Los Angeles Clippers", "condition_estimate": "Near Mint",
            "corners_condition": "Sharp", "edges_condition": "Clean",
            "front_surface_condition": "Glossy", "back_surface_condition": "Clean",
            "whitening_condition": "None", "centering_condition": "55/45",
            "visible_flaws": "", "notes": "", "rarity": "", "variant": "",
        },
        "price_data": {
            "source": "ebay_active", "estimated_price": price, "low": 18.0, "high": 44.0,
            "confidence": "medium", "query_used": "2025 Topps Chrome Player Name SE-7",
            "raw": {"count": 12, "median": price, "low": 18.0, "high": 44.0,
                    "examples": [{"title": "comp %d" % i, "price": price} for i in range(5)]},
        },
        "image_urls": ["u1", "u2"],
    }


scan_ledger.LEDGER_PATH = str(Path(tempfile.mkdtemp()) / "bench_ledger.json")
scan_session_module.process_scanned_card = fake_process

from streamlit.testing.v1 import AppTest  # noqa: E402

APP = str(Path(__file__).resolve().parents[1] / "CardFlow.py")


def build_session(card_count):
    inbox = Path(tempfile.mkdtemp(prefix="bench_"))
    at = AppTest.from_file(APP, default_timeout=300)
    at.run()

    box = [c for c in at.sidebar.checkbox if "clear this session" in str(c.label).lower()]
    if box:
        box[0].check().run()
        clear = [b for b in at.sidebar.button if "Clear all cards" in str(b.label)]
        if clear:
            clear[0].click().run()

    at.sidebar.text_input[0].set_value(str(inbox)).run()
    at.sidebar.number_input[0].set_value(max(10, card_count)).run()
    at.sidebar.button[0].click().run()

    for i in range(card_count * 2):
        Image.new("RGB", (900, 1260), (30, 90, 180)).save(inbox / ("c%04d.jpg" % i), "JPEG")

    for _ in range(120):
        time.sleep(1)
        at.run()
        if len([c for c in at.caption if "SKU" in str(c.value)]) or True:
            ready = sum(1 for m in at.metric if m.label == "Drafts ready")
            drafts = [m.value for m in at.metric if m.label == "Drafts ready"]
            if drafts and int(drafts[0]) >= card_count:
                break
    return at, inbox


def timed_runs(at, repeats=5):
    samples = []
    for _ in range(repeats):
        start = time.time()
        at.run()
        samples.append((time.time() - start) * 1000)
    return statistics.median(samples)


def main():
    card_count = int(sys.argv[1]) if len(sys.argv) > 1 else 40
    print("Building a session with %d identified cards..." % card_count)
    at, inbox = build_session(card_count)

    drafts = [m.value for m in at.metric if m.label == "Drafts ready"]
    print("cards ready: %s\n" % (drafts[0] if drafts else "?"))

    def element_count(at):
        total = 0
        for kind in ("markdown", "caption", "text_input", "number_input", "checkbox",
                     "toggle", "text_area", "button", "json", "code", "selectbox"):
            try:
                total += len(at.get(kind))
            except Exception:
                pass
        return total

    print("%-11s %-14s %s" % ("PAGE SIZE", "RERUN (ms)", "ELEMENTS SENT"))
    for size in PAGE_SIZE_OPTIONS:
        if size > card_count:
            continue
        selects = [s for s in at.selectbox if "Per page" in str(s.label)]
        if not selects:
            print("  (per-page control not found)")
            break
        selects[0].set_value(size).run()
        print("%-11s %-14.0f %d" % (size, timed_runs(at), element_count(at)))

    print("\nSession totals: %d cards held, snapshot+stats on every rerun" % card_count)
    import shutil
    shutil.rmtree(inbox, ignore_errors=True)


if __name__ == "__main__":
    main()
