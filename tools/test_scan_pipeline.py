"""Offline smoke test for the live scan pipeline (no OpenAI/eBay/Cloudinary calls).

Simulates a scanner dropping duplex JPEGs into the inbox folder and checks pairing,
cap enforcement, single-sided flush, and error handling.

Run: python tools/test_scan_pipeline.py
"""

import shutil
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PIL import Image

import core.scan_ledger as scan_ledger
import core.scan_session as scan_session_module
from core.scan_session import STATUS_DRAFT, STATUS_ERROR, ScanSession

scan_ledger.LEDGER_PATH = str(Path(tempfile.mkdtemp()) / "test_ledger.json")

failures = []


def check(label, condition, detail=""):
    print(("  PASS  " if condition else "  FAIL  ") + label + (" -> " + str(detail) if detail else ""))
    if not condition:
        failures.append(label)


def fake_process(front, back=None):
    if "boom" in front.name:
        raise ValueError("simulated identification failure")
    return {
        "sku": "sku-" + front.name,
        "offer_id": "offer-" + front.name,
        "title": "Card from " + front.name,
        "description": "desc",
        "price": 9.99,
        "price_source": "test",
        "best_offer_enabled": True,
        "min_offer_price": 7.99,
        "category_id": "183454",
        "category_name": "CCG Individual Cards",
        "card_name": front.name,
        "card_data": {"back_seen": bool(back)},
        "price_data": {},
        "image_urls": ["u1"],
    }


_scan_counter = [0]


def write_scan(folder, name, color=None):
    """Write a JPEG the way a scanner would. Every scan gets distinct pixels, because
    identical content is now correctly recognised as the same card."""
    path = Path(folder) / name
    _scan_counter[0] += 1
    shade = (37 * _scan_counter[0]) % 240
    Image.new("RGB", (600, 840), color or (shade, 40 + shade // 3, 200 - shade // 2)).save(
        path, format="JPEG", quality=70)
    return path


def wait_until(predicate, timeout=20.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.25)
    return predicate()


def main():
    scan_session_module.process_scanned_card = fake_process

    inbox = Path(tempfile.mkdtemp(prefix="scan_inbox_"))
    session = ScanSession()

    print("\n1. Pre-existing files are imported when asked")
    write_scan(inbox, "pre_001.jpg")
    write_scan(inbox, "pre_002.jpg")
    session.start(str(inbox), max_cards=3, workers=2, import_existing=True)
    check("pre-existing pair became one card", wait_until(lambda: len(session.cards) == 1))
    check("card reached draft", wait_until(lambda: session.cards[0]["status"] == STATUS_DRAFT),
          session.cards[0]["status"])
    check("back image was passed through", session.cards[0]["card_data"]["back_seen"] is True)
    check("thumbnail written", bool(session.cards[0]["thumb_path"]))

    print("\n2. Live arrivals pair in scan order")
    for i in range(1, 5):
        write_scan(inbox, "live_{:03d}.jpg".format(i))
        time.sleep(0.2)
    check("two more cards created", wait_until(lambda: len(session.cards) == 3), len(session.cards))
    check("front/back assigned in arrival order",
          session.cards[1]["front_name"] == "live_001.jpg"
          and session.cards[1]["back_name"] == "live_002.jpg",
          (session.cards[1]["front_name"], session.cards[1]["back_name"]))

    print("\n3. Cap pauses intake and holds images in the queue")
    write_scan(inbox, "over_001.jpg")
    write_scan(inbox, "over_002.jpg")
    check("cap held at 3 cards", wait_until(lambda: session.stats()["paused_for_cap"]),
          session.stats())
    check("no card created past the cap", len(session.cards) == 3, len(session.cards))
    check("images kept in backlog", session.stats()["backlog"] == 2, session.stats()["backlog"])

    print("\n4. Raising the cap drains the backlog")
    session.set_max_cards(10)
    check("queued pair became a card", wait_until(lambda: len(session.cards) == 4), len(session.cards))
    check("cap flag cleared", session.stats()["paused_for_cap"] is False)

    print("\n5. Leftover odd scan can be flushed single-sided")
    write_scan(inbox, "odd_001.jpg")
    check("odd image sits in backlog", wait_until(lambda: session.stats()["backlog"] == 1),
          session.stats()["backlog"])
    check("flush accepted", session.flush_single_sided())
    check("single-sided card created", wait_until(lambda: len(session.cards) == 5))
    check("no back recorded", session.cards[4]["back_name"] == "")
    check("processed without a back", wait_until(
        lambda: session.cards[4].get("card_data", {}).get("back_seen") is False))

    print("\n6. Failures are captured per card and retryable")
    write_scan(inbox, "boom_001.jpg")
    write_scan(inbox, "boom_002.jpg")
    check("error card recorded", wait_until(
        lambda: len(session.cards) == 6 and session.cards[5]["status"] == STATUS_ERROR),
        session.cards[5]["status"] if len(session.cards) > 5 else "no card")
    check("error message kept", "simulated" in session.cards[5]["error"])
    check("other cards unaffected", session.cards[3]["status"] == STATUS_DRAFT)

    print("\n7. Stop halts intake")
    session.stop()
    time.sleep(0.5)
    write_scan(inbox, "after_stop_001.jpg")
    write_scan(inbox, "after_stop_002.jpg")
    time.sleep(3.0)
    check("no cards added after stop", len(session.cards) == 6, len(session.cards))

    print("\n8. Reset clears the session")
    session.reset()
    check("cards cleared", session.cards == [])
    check("stats reset", session.stats()["total"] == 0)

    shutil.rmtree(inbox, ignore_errors=True)

    print("\n" + ("ALL CHECKS PASSED" if not failures else "FAILED: " + ", ".join(failures)))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
