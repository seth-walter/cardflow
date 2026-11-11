"""Duplicate-scan protection. No network: card processing is stubbed.

The failure this guards against: press Start again with scans still in the inbox and
every one of them becomes a second eBay draft.

Run: python tools/test_duplicates.py
"""

import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PIL import Image

import core.scan_ledger as ledger
import core.scan_session as scan_session_module
from core.scan_session import STATUS_DRAFT, ScanSession

failures = []
processed = []


def check(label, condition, detail=""):
    print(("  PASS  " if condition else "  FAIL  ") + label + (" -> " + str(detail) if detail else ""))
    if not condition:
        failures.append(label)


def fake_process(front, back=None):
    processed.append(front.name)
    return {"sku": "sku-" + front.name, "offer_id": "offer-" + front.name,
            "title": "Card from " + front.name, "description": "d", "price": 10.0,
            "price_source": "test", "best_offer_enabled": True, "min_offer_price": 2.0,
            "category_id": "261328", "category_name": "Trading Card Singles",
            "card_name": "c", "card_data": {}, "price_data": {}, "image_urls": []}


def write_scan(folder, name, colour=(30, 90, 180)):
    path = Path(folder) / name
    Image.new("RGB", (400, 560), colour).save(path, format="JPEG", quality=70)
    return path


def wait_for(session, count, timeout=25):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if len([c for c in session.cards if c["status"] == STATUS_DRAFT]) >= count:
            return True
        time.sleep(0.25)
    return False


def main():
    scan_session_module.process_scanned_card = fake_process
    ledger.LEDGER_PATH = str(Path(tempfile.mkdtemp()) / "ledger.json")
    inbox = Path(tempfile.mkdtemp(prefix="dup_"))

    print("\n1. A first scan is processed normally")
    write_scan(inbox, "image0001.jpg", colour=(30, 90, 180))
    write_scan(inbox, "image0002.jpg", colour=(60, 60, 60))
    first = ScanSession()
    first.start(str(inbox), 150, 2, import_existing=True)
    check("card created", wait_for(first, 1), [c["status"] for c in first.cards])
    check("processor ran once", len(processed) == 1, processed)
    check("the front scan is remembered", ledger.count() == 1, ledger.count())
    check("the back is not (it would collide with future fronts)",
          ledger.lookup(inbox / "image0002.jpg") is None)
    first.stop()

    print("\n2. Pressing Start again does not list them a second time")
    # This is the real scenario: the scans are still in the inbox because the card was
    # drafted but never published.
    processed[:] = []
    second = ScanSession()
    second.start(str(inbox), 150, 2, import_existing=True)
    time.sleep(6)
    check("no new cards created", len(second.cards) == 0, len(second.cards))
    check("processor never ran again", processed == [], processed)
    check("skip was counted", second.stats()["skipped_duplicates"] == 1,
          second.stats()["skipped_duplicates"])
    check("skip explained in the log",
          any("already listed" in m for m in second.messages), list(second.messages)[:2])
    second.stop()

    print("\n3. A brand new card in the same folder still goes through")
    processed[:] = []
    write_scan(inbox, "image0003.jpg", colour=(200, 40, 40))
    write_scan(inbox, "image0004.jpg", colour=(120, 20, 20))
    third = ScanSession()
    third.start(str(inbox), 150, 2, import_existing=True)
    check("new card processed", wait_for(third, 1), [c["status"] for c in third.cards])
    check("only the new scan ran", len(processed) == 1, processed)
    third.stop()

    print("\n4. Reused filenames are not mistaken for duplicates")
    # PaperStream restarts numbering, so image0001.jpg recurs with different content.
    processed[:] = []
    fresh_inbox = Path(tempfile.mkdtemp(prefix="dup2_"))
    write_scan(fresh_inbox, "image0001.jpg", colour=(10, 200, 90))
    write_scan(fresh_inbox, "image0002.jpg", colour=(10, 120, 60))
    fourth = ScanSession()
    fourth.start(str(fresh_inbox), 150, 2, import_existing=True)
    check("same filename, new content is processed", wait_for(fourth, 1),
          [c["status"] for c in fourth.cards])
    check("no false duplicate skip", fourth.stats()["skipped_duplicates"] == 0,
          fourth.stats()["skipped_duplicates"])
    fourth.stop()

    print("\n5. History survives a restart and can be cleared deliberately")
    check("ledger persisted to disk", Path(ledger.LEDGER_PATH).exists())
    remembered = ledger.count()
    check("one entry per listed card", remembered == 3, remembered)

    entry = ledger.lookup(inbox / "image0001.jpg")
    check("records what the scan became", entry and entry["sku"] == "sku-image0001.jpg", entry)

    check("forget clears it", ledger.forget_all() == remembered)
    check("empty afterwards", ledger.count() == 0)

    processed[:] = []
    fifth = ScanSession()
    fifth.start(str(inbox), 150, 2, import_existing=True)
    check("after forgetting, scans are listed again", wait_for(fifth, 1),
          [c["status"] for c in fifth.cards])
    fifth.stop()

    print("\n6. The card list itself survives a restart")
    # Without this a reload loses drafted-but-unpublished cards, and their scans are
    # already in the ledger, so rescanning would skip them.
    saved = str(Path(tempfile.mkdtemp()) / "session_cards.json")
    processed[:] = []
    ledger.forget_all()
    live = ScanSession(persist_path=saved)
    live.start(str(inbox), 150, 2, import_existing=True)
    wait_for(live, 2)
    live.stop()
    live.update_card(live.cards[0]["id"], title="Edited title")
    restored = ScanSession(persist_path=saved)
    check("cards restored", len(restored.cards) == len(live.cards) == 2,
          (len(live.cards), len(restored.cards)))
    check("edits kept", restored.cards[0]["title"] == "Edited title", restored.cards[0]["title"])
    check("drafts still publishable", restored.cards[0]["status"] == STATUS_DRAFT
          and restored.cards[0]["offer_id"], restored.cards[0].get("offer_id"))
    restored.reset()
    check("Clear all cards empties the save", ScanSession(persist_path=saved).cards == [])

    print("\n" + ("ALL CHECKS PASSED" if not failures else "FAILED: " + ", ".join(failures)))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
