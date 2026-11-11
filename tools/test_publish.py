"""Publish-flow tests with every eBay/Cloudinary call stubbed. Nothing leaves the machine.

Covers the failure where listings published successfully but the app still threw, plus
publish-all and the post-publish handling of scanned files.

Run: python tools/test_publish.py
"""

import os
import shutil
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
import services.ebay_inventory as inventory_module
import services.ebay_offers as offers_module

scan_ledger.LEDGER_PATH = str(Path(tempfile.mkdtemp()) / "test_ledger.json")

failures = []
calls = {"inventory": 0, "offer_update": 0, "published": []}


def _safe(text):
    """The Windows console is cp1252; button labels contain rotate arrows."""
    return str(text).encode("ascii", "replace").decode("ascii")


def check(label, condition, detail=""):
    print(("  PASS  " if condition else "  FAIL  ") + label
          + (" -> " + _safe(detail) if detail else ""))
    if not condition:
        failures.append(label)


# ---- stubs, installed before CardFlow.py is imported so its `from ... import` binds them

def fake_inventory(**kwargs):
    calls["inventory"] += 1
    return {}


def fake_update_offer(**kwargs):
    calls["offer_update"] += 1
    return {}


def fake_bulk_publish(offer_ids):
    calls["published"].extend(offer_ids)
    return {"responses": [{"offerId": oid, "statusCode": 200} for oid in offer_ids]}


def fake_listing_url(offer_id):
    return "https://www.ebay.com/itm/" + str(offer_id).replace("offer-", "")


def fake_process(front, back=None):
    return {
        "sku": "sku-" + front.name, "offer_id": "offer-" + front.name,
        "title": "2025 Topps Chrome Card", "description": "d", "price": 25.0,
        "price_source": "ebay_active", "best_offer_enabled": True, "min_offer_price": 20.0,
        "category_id": "261328", "category_name": "Trading Card Singles",
        "card_name": "Card", "card_data": {"year": "2025", "brand": "Topps"},
        "price_data": {}, "image_urls": ["u1", "u2"],
    }


inventory_module.create_or_replace_inventory_item = fake_inventory
offers_module.update_offer_full = fake_update_offer
offers_module.bulk_publish_offers = fake_bulk_publish
offers_module.get_listing_url = fake_listing_url
scan_session_module.process_scanned_card = fake_process

from streamlit.testing.v1 import AppTest  # noqa: E402  (must follow the stubs)

APP = str(Path(__file__).resolve().parents[1] / "CardFlow.py")


def body_text(at):
    return "\n".join(
        str(e.value) for e in
        list(at.markdown) + list(at.caption) + list(at.success) + list(at.warning)
        + list(at.info) + list(at.error)
    )


def clear_session(at):
    """The app's ScanSession is a cached resource shared by every AppTest in this
    process, so each scenario must reset it or it inherits the previous one's cards."""
    box = [c for c in at.sidebar.checkbox if "clear this session" in str(c.label).lower()]
    if box:
        box[0].check().run()
        button = [b for b in at.sidebar.button if "Clear all cards" in str(b.label)]
        if button:
            button[0].click().run()
        # No need to untick: each scenario builds a fresh AppTest with its own state.


def start_session(inbox, pairs=3, tag="s"):
    at = AppTest.from_file(APP, default_timeout=90)
    at.run()
    clear_session(at)
    at.sidebar.text_input[0].set_value(str(inbox)).run()
    at.sidebar.button[0].click().run()
    for i in range(pairs * 2):
        # Unique across the whole run, not just this folder: two scenarios that wrote
        # identical pixels would be correctly treated as the same card and skipped.
        seed = 41 * (i + 1) + 17 * sum(ord(ch) for ch in tag)
        shade = seed % 240
        Image.new("RGB", (600, 840), (shade, (shade * 3) % 240, 180)).save(
            inbox / ("%s%03d.jpg" % (tag, i)), "JPEG")
    for _ in range(20):
        time.sleep(1)
        at.run()
        if len([c for c in at.caption if "SKU" in str(c.value)]) >= pairs:
            break
    return at


def main():
    inbox = Path(tempfile.mkdtemp(prefix="pub_inbox_"))

    print("\n1. Publishing a selected listing does not throw")
    at = start_session(inbox, pairs=3)
    check("three cards ready", len([c for c in at.caption if "SKU" in str(c.value)]) == 3,
          len([c for c in at.caption if "SKU" in str(c.value)]))

    boxes = [c for c in at.checkbox if "Publish this listing" in str(c.label)]
    check("publish checkboxes present", len(boxes) == 3, len(boxes))
    boxes[0].check().run()

    publish = [b for b in at.button if "publish selected" in str(b.label).lower()]
    check("publish button present", len(publish) == 1, [str(b.label) for b in at.button])
    publish[0].click().run()

    check("NO exception after publishing", not at.exception,
          [str(e.value) for e in at.exception])
    check("no error message shown to the user", not at.error,
          [_safe(e.value)[:120] for e in at.error])
    check("eBay actually got the publish call", len(calls["published"]) == 1, calls["published"])
    check("inventory item was updated first", calls["inventory"] == 1, calls["inventory"])
    check("success message shown",
          any("Published all 1" in str(s.value) for s in at.success),
          [str(s.value)[:60] for s in at.success])

    url_rows = "\n".join(str(t.value) for t in at.text_area)
    check("listing URL row produced", "ebay.com/itm/" in url_rows, url_rows[:80])

    at.run()  # the card list renders above the publish block, so status lands next pass
    check("card now shows as published", "Published." in body_text(at))
    # The live_sync redraw used to wipe these within two seconds of publishing.
    check("summary survives the redraw",
          any("Published all 1" in str(s.value) for s in at.success),
          [str(s.value)[:60] for s in at.success])
    check("Discord rows survive the redraw",
          "ebay.com/itm/" in "\n".join(str(t.value) for t in at.text_area))
    dismiss = [b for b in at.button if str(b.label) == "Dismiss"]
    check("results can be dismissed", len(dismiss) == 1, [str(b.label) for b in at.button])
    if dismiss:
        dismiss[0].click().run()
        check("dismissed results are gone",
              not any("Published all" in str(s.value) for s in at.success))
    check("published card no longer offers a publish checkbox",
          len([c for c in at.checkbox if "Publish this listing" in str(c.label)]) == 2,
          len([c for c in at.checkbox if "Publish this listing" in str(c.label)]))

    print("\n2. Publish all drafts")
    calls["published"] = []
    inbox2 = Path(tempfile.mkdtemp(prefix="pub_inbox2_"))
    at2 = start_session(inbox2, pairs=3, tag="b")
    publish_all = [b for b in at2.button if "publish all" in str(b.label).lower()]
    check("publish-all button present", len(publish_all) == 1,
          [str(b.label) for b in at2.button])
    publish_all[0].click().run()
    check("no exception on publish all", not at2.exception, [str(e.value) for e in at2.exception])
    check("no error message on publish all", not at2.error,
          [_safe(e.value)[:120] for e in at2.error])
    check("all three published without ticking boxes",
          sorted(calls["published"]) == ["offer-b000.jpg", "offer-b002.jpg", "offer-b004.jpg"],
          calls["published"])

    print("\n3. Published scans leave the watch folder")
    remaining = sorted(p.name for p in inbox2.glob("*.jpg"))
    check("scans removed from inbox", remaining == [], remaining)
    archived = sorted(p.name for p in (inbox2 / "published").glob("*.jpg"))
    check("scans archived, not destroyed", len(archived) == 6, archived)

    print("\n4. Clear-folder button empties the inbox")
    inbox3 = Path(tempfile.mkdtemp(prefix="pub_inbox3_"))
    for i in range(4):
        Image.new("RGB", (600, 840), (10, 10, 10)).save(inbox3 / ("leftover%d.jpg" % i), "JPEG")
    at3 = AppTest.from_file(APP, default_timeout=90)
    at3.run()
    at3.sidebar.text_input[0].set_value(str(inbox3)).run()
    confirm = [c for c in at3.sidebar.checkbox if "empty the scan folder" in str(c.label).lower()]
    check("clear-folder confirmation present", len(confirm) == 1,
          [str(c.label) for c in at3.sidebar.checkbox])
    confirm[0].check().run()
    clear = [b for b in at3.sidebar.button if "empty scan folder" in str(b.label).lower()]
    check("clear-folder button present", len(clear) == 1,
          [str(b.label) for b in at3.sidebar.button])
    clear[0].click().run()
    check("no exception clearing", not at3.exception, [str(e.value) for e in at3.exception])
    check("inbox emptied", sorted(p.name for p in inbox3.glob("*.jpg")) == [],
          sorted(p.name for p in inbox3.glob("*.jpg")))

    for folder in (inbox, inbox2, inbox3):
        shutil.rmtree(folder, ignore_errors=True)

    print("\n" + ("ALL CHECKS PASSED" if not failures else "FAILED: " + ", ".join(failures)))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
