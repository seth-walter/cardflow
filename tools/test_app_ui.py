"""UI smoke test: runs app.py through Streamlit's AppTest with the card pipeline stubbed.

Verifies the page renders, Start begins watching a folder, scanned images turn into
cards in the list, and the cap warning appears. No OpenAI/eBay/Cloudinary calls.

Run: python tools/test_app_ui.py
"""

import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

# Checks below expect OpenAI's wording and billing link whatever .env selects.
os.environ["VISION_PROVIDER"] = "openai"
os.environ.pop("VISION_MODEL", None)
# Never load or overwrite the real saved card list.
os.environ["SESSION_STATE_PATH"] = os.path.join(tempfile.mkdtemp(), "session_cards.json")

sys.path.insert(0, str(Path(__file__).resolve().parent))
import offline_env  # noqa: F401,E402  (placeholder keys; must come before settings)

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PIL import Image
from streamlit.testing.v1 import AppTest

import core.scan_ledger as scan_ledger
import core.scan_session as scan_session_module

# Never touch the real record of what has been listed.
scan_ledger.LEDGER_PATH = str(Path(tempfile.mkdtemp()) / "test_ledger.json")

failures = []


def check(label, condition, detail=""):
    print(("  PASS  " if condition else "  FAIL  ") + label + (" -> " + str(detail) if detail else ""))
    if not condition:
        failures.append(label)


def fake_process(front, back=None):
    price = 12.50
    return {
        "sku": "sku-" + front.name,
        "offer_id": "offer-" + front.name,
        "title": "1998 Test Set Sample Card #12",
        "description": "Nice card.",
        "price": price,
        "price_source": "test",
        "best_offer_enabled": True,
        "min_offer_price": round(price * 0.8, 2),
        "category_id": "183454",
        "category_name": "CCG Individual Cards",
        "card_name": "Sample Card",
        "card_data": {
            "card_category": "sports", "year": "1998", "brand": "Test Set",
            "player_athlete": "Sample Player", "card_number": "12",
        },
        "price_data": {"estimated_price": price},
        "image_urls": ["u1", "u2"],
    }


def write_scan(folder, name):
    Image.new("RGB", (600, 840), (30, 90, 180)).save(Path(folder) / name, format="JPEG", quality=70)


def body_text(at):
    chunks = []
    for element in list(at.markdown) + list(at.caption) + list(at.warning) + list(at.info) + list(at.error):
        chunks.append(str(element.value))
    return "\n".join(chunks)


def main():
    scan_session_module.process_scanned_card = fake_process
    inbox = Path(tempfile.mkdtemp(prefix="ui_inbox_"))

    at = AppTest.from_file(str(Path(__file__).resolve().parents[1] / "CardFlow.py"), default_timeout=60)
    at.run()

    print("\n1. Initial render")
    check("no uncaught exception", not at.exception, [e.value for e in at.exception])
    check("title rendered", any("CardFlow" in str(t.value) for t in at.title))
    check("idle prompt shown", "Start" in body_text(at))
    check("6 status metrics", len(at.metric) == 6, len(at.metric))

    print("\n2. Start watching a folder")
    at.sidebar.text_input[0].set_value(str(inbox)).run()
    at.sidebar.number_input[0].set_value(10).run()  # cap of 10 cards (widget minimum)
    at.sidebar.button[0].click().run()
    check("no exception after start", not at.exception, [e.value for e in at.exception])
    check("watching state shown", any("Watching for scans" in str(s.value) for s in at.success))

    print("\n3. Scanned pages become cards")
    for i in range(1, 25):  # 12 pairs against a cap of 10
        write_scan(inbox, "s{:03d}.jpg".format(i))
    time.sleep(8)
    at.run()
    check("no exception with cards", not at.exception, [e.value for e in at.exception])
    check("card titles rendered", "1998 Test Set Sample Card #12" in body_text(at))
    check("cards capped at 10", any(m.value == "10 / 10" for m in at.metric),
          [m.value for m in at.metric])
    check("one page of edit widgets", len(at.text_input) >= 5, len(at.text_input))
    check("title prefilled", at.text_input[1].value == "1998 Test Set Sample Card #12",
          at.text_input[1].value)
    check("price prefilled", at.number_input[-1].value is not None)

    print("\n4. Cap pauses intake")
    check("cap warning shown", any("cap of 10" in str(w.value) for w in at.warning),
          [w.value for w in at.warning])
    check("leftover images held", any(m.label == "Images queued" and m.value == "4" for m in at.metric),
          [(m.label, m.value) for m in at.metric])

    print("\n5. Selecting a card reveals the publish action")
    publish_checkboxes = [c for c in at.checkbox if "Publish this listing" in str(c.label)]
    check("25 per page by default: all 10 cards shown", len(publish_checkboxes) == 10,
          len(publish_checkboxes))
    select_all = [b for b in at.button if str(b.label).startswith("Select all for publish")]
    check("select-all offered", len(select_all) == 1, [str(b.label) for b in at.button][:8])
    if select_all:
        select_all[0].click().run()
        check("select-all ticks every draft",
              any("Publish selected (10)" in str(b.label) for b in at.button),
              [str(b.label) for b in at.button if "Publish" in str(b.label)])
        [b for b in at.button if str(b.label) == "Clear selection"][0].click().run()
        check("clear selection unticks them",
              any("Publish selected (0)" in str(b.label) for b in at.button),
              [str(b.label) for b in at.button if "Publish" in str(b.label)])
    publish_checkboxes = [c for c in at.checkbox if "Publish this listing" in str(c.label)]
    publish_checkboxes[0].check().run()
    check("no exception after select", not at.exception, [e.value for e in at.exception])
    check("publish button appears",
          any("Publish selected (1)" in str(b.label) for b in at.button),
          [str(b.label) for b in at.button if "Publish" in str(b.label)])
    check("publish-all offered too",
          any("Publish ALL" in str(b.label) for b in at.button),
          [str(b.label) for b in at.button if "Publish" in str(b.label)])

    print("\n6. Rotate buttons turn a card's photo")
    rotate_right = [b for b in at.button if b.proto.help == "Rotate front right"]
    check("a rotate control per visible card", len(rotate_right) == 10, len(rotate_right))
    rotate_right[0].click().run()
    check("no exception after rotate", not at.exception, [e.value for e in at.exception])
    check("rotation recorded and flagged for publish",
          "rotated front 90° · applied on publish" in body_text(at),
          [c for c in body_text(at).split("\n") if "rotated" in c][:2])

    print("\n7. Comp links are offered for each identified card")
    check("comp query shown to the user", "Check the price yourself" in body_text(at),
          [c for c in body_text(at).split("\n") if "Check the price" in c][:1])
    try:
        link_buttons = at.get("link_button")
    except Exception:
        link_buttons = []
    labels = [getattr(b, "label", getattr(getattr(b, "proto", None), "label", "")) for b in link_buttons]
    check("eBay sold link rendered", "eBay sold" in labels, labels[:4])
    check("price guide link rendered", "SportsCardsPro" in labels, labels[:4])
    urls = [getattr(getattr(b, "proto", None), "url", "") for b in link_buttons]
    check("sold link really filters to sold",
          any("LH_Sold=1" in u and "LH_Complete=1" in u for u in urls),
          [u[:70] for u in urls[:2]])

    print("\n8. Clearing the session empties the list")
    clear_toggle = [c for c in at.sidebar.checkbox if "clear this session" in str(c.label)][0]
    clear_toggle.check().run()
    clear_button = [b for b in at.sidebar.button if "Clear all cards" in str(b.label)][0]
    clear_button.click().run()
    check("no exception after clear", not at.exception, [e.value for e in at.exception])
    check("card list gone", "1998 Test Set Sample Card #12" not in body_text(at))
    check("counter reset", any(m.value == "0 / 10" for m in at.metric), [m.value for m in at.metric])

    print("\n9. An empty OpenAI balance is called out, with a way to retry")

    def no_credit(front, back=None):
        raise RuntimeError("Error code: 429 - {'error': {'message': 'You have no credits "
                           "remaining.', 'type': 'insufficient_quota'}}")

    scan_session_module.process_scanned_card = no_credit
    at.sidebar.button[0].click().run()   # clearing stopped the watcher
    for i in range(30, 34):   # new pixels, or the duplicate check skips them as listed
        Image.new("RGB", (600, 840), (200, 20 * i % 255, 40)).save(
            inbox / "s{:03d}.jpg".format(i), format="JPEG", quality=70)
    time.sleep(6)
    at.run()
    check("no exception with failed cards", not at.exception, [e.value for e in at.exception])
    check("out-of-credit banner shown", any("out of credit" in str(e.value) for e in at.error),
          [str(e.value)[:60] for e in at.error][:3])
    try:
        urls = [getattr(getattr(b, "proto", None), "url", "") for b in at.get("link_button")]
    except Exception:
        urls = []
    check("billing link offered", any("platform.openai.com" in u for u in urls), urls[:3])

    scan_session_module.process_scanned_card = fake_process
    retry = [b for b in at.button if str(b.label).startswith("Retry 2 card")]
    check("retry-all button", len(retry) == 1, [str(b.label) for b in at.button if "Retry" in str(b.label)])
    if retry:
        retry[0].click().run()
        time.sleep(4)
        at.run()
        check("banner gone once cards identify",
              not any("out of credit" in str(e.value) for e in at.error))

    shutil.rmtree(inbox, ignore_errors=True)
    print("\n" + ("ALL CHECKS PASSED" if not failures else "FAILED: " + ", ".join(failures)))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
