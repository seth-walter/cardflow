"""Identification overhaul and the under-$50 auction policy. No network.

Run: python tools/test_titles_and_auctions.py
"""

import json
import os
import sys
import tempfile
from pathlib import Path

# The stubs below fake OpenAI's client; pin the provider so a .env set to Gemini cannot
# send these calls to the live API (load_dotenv never overrides a set variable).
os.environ["VISION_PROVIDER"] = "openai"
for name in ("VISION_MODEL", "SERIAL_READ_MODEL"):
    os.environ.pop(name, None)

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PIL import Image

from config import settings
import services.openai_service as vision
import services.sports_lookup as sports
import services.onepiece_lookup as onepiece
import services.ebay_listings as listings
from services.ebay_auction import choose_listing_format, price_point
from services.ebay_offers import build_offer_payload
from services.card_aspects import build_card_aspects
from core.listing_builder import is_tcg_card
from core.scanned_image import ScannedImage

failures = []


def check(label, condition, detail=""):
    print(("  PASS  " if condition else "  FAIL  ") + label + (" -> " + str(detail) if detail else ""))
    if not condition:
        failures.append(label)


def title(card):
    return vision.build_seo_title(card, {})


def main():
    print("\n1. Sports titles name the exact version")
    reaves = {"card_category": "sports", "year": "2025-26", "brand": "Topps",
              "set": "Topps Chrome Basketball", "player_athlete": "Austin Reaves",
              "card_number": "54", "parallel": "Magenta Refractor", "serial_number": "112/399"}
    got = title(reaves)
    check("numbered colour titled the seller's way", "Magenta /399" in got, got)
    check("sport word dropped from the set", "Basketball" not in got, got)
    check("no 'Topps Topps'", "Topps Topps" not in got, got)

    seymour = {"card_category": "sports", "year": "2026", "set": "Topps Chrome",
               "player_athlete": "Ian Seymour", "card_number": "RA-IS",
               "insert_set": "Rookie Autographs", "serial_number": "12/275",
               "autographed": "Yes", "rookie": True}
    got = title(seymour)
    check("autograph insert becomes 'Auto'", "Auto" in got and "Autographs" not in got, got)
    check("'Auto' appears once", got.count("Auto") == 1, got)
    check("rookie flag adds RC", got.endswith("RC"), got)

    luka = {"card_category": "sports", "year": "2026", "set": "Topps Chrome",
            "player_athlete": "Luka DonÄiÄ‡", "card_number": "AC-20", "insert_set": "Activators",
            "print_run": "/199"}
    check("insert name in the title", "Activators /199" in title(luka), title(luka))

    blue_wave = dict(reaves, parallel="Blue Wave Refractor", serial_number="31/150")
    check("Blue Wave /150", "Blue Wave /150" in title(blue_wave), title(blue_wave))
    ray = dict(reaves, parallel="Ray Wave Refractor", serial_number="")
    check("unnumbered pattern keeps 'Refractor'", "Ray Wave Refractor" in title(ray), title(ray))
    one_of_one = dict(reaves, serial_number="1/1", parallel="Superfractor")
    check("one-of-one keeps 1/1", "1/1" in title(one_of_one), title(one_of_one))

    print("\n2. One Piece promos follow the seller's format")
    jinbe = {"card_category": "one_piece", "game": "One Piece Card Game", "name": "Jinbe",
             "card_number": "ST29-005", "rarity": "C", "set": "Egghead",
             "promo_stamp": "ROUND ONE PIECE", "parallel": "Not For Sale"}
    check("Round One Promo title", title(jinbe) == "One Piece Round One Promo Jinbe", title(jinbe))
    franky = dict(jinbe, name="Franky (Parallel)", promo_stamp="ROUND ONE PIECE stamped promo")
    check("parenthetical and stamp noise dropped",
          title(franky) == "One Piece Round One Promo Franky", title(franky))
    regular = dict(jinbe, promo_stamp="", parallel="")
    check("non-promo keeps code and rarity", "#ST29-005 C" in title(regular), title(regular))

    print("\n3. Union Arena is its own game")
    gojo = {"card_category": "union_arena", "game": "Union Arena", "manufacturer": "Bandai",
            "name": "Satoru Gojo", "franchise": "Jujutsu Kaisen",
            "card_number": "UE03BT/JJK-1-009", "rarity": "SRâ˜…"}
    check("Union Arena title", title(gojo).startswith("Union Arena Jujutsu Kaisen Satoru Gojo UE03BT/JJK-1-009"),
          title(gojo))
    check("never routed through the One Piece lookup",
          onepiece.is_probably_one_piece(gojo) is False)
    check("listed in the CCG category", is_tcg_card(gojo))
    aspects = build_card_aspects(gojo)
    check("Game aspect is Union Arena", aspects.get("Game") == ["Union Arena"], aspects.get("Game"))

    print("\n4. F1 and Disney")
    norris = {"card_category": "sports", "year": "2025", "set": "Topps Chrome Formula 1",
              "player_athlete": "Lando Norris", "card_number": "4", "parallel": "Gold Refractor",
              "serial_number": "08/50", "sport": "Racing", "league": "Formula 1"}
    check("F1 title", title(norris) == "2025 Topps Chrome Formula 1 Lando Norris #4 Gold /50",
          title(norris))
    stitch = {"card_category": "other", "franchise": "Disney", "year": "2026",
              "set": "Topps Disney Neon", "character": "Stitch", "card_number": "FA-3",
              "autographed": "No"}
    check("facsimile signature is not titled Auto", "Auto" not in title(stitch), title(stitch))

    print("\n5. Serial sanity")
    check("standard run passes", vision.title_warnings({"serial_number": "112/399"}) == [])
    check("odd run is flagged", len(vision.title_warnings({"serial_number": "03/18"})) == 1)
    check("number above run is flagged",
          any("impossible" in w for w in vision.title_warnings({"serial_number": "400/399"})))
    aspects = build_card_aspects(dict(reaves, rookie=True))
    check("print run aspect", aspects.get("Print Run") == ["399"], aspects.get("Print Run"))
    check("serial numbered feature", "Serial Numbered" in aspects.get("Features", []),
          aspects.get("Features"))

    print("\n6. Active listings never overwrite what the card shows")
    sports._candidate_items = lambda card, limit=20: [
        {"title": "2025 Topps Chrome Kon Knueppel #154 Refractor RC"}] * 6
    kon = {"card_category": "sports", "year": "2025-26", "set": "Topps Chrome",
           "player_athlete": "Kon Knueppel", "card_number": "154",
           "parallel": "Ray Wave Refractor", "rookie": True}
    enriched = sports.enrich_sports_card_data(kon)
    check("parallel survives", enriched["parallel"] == "Ray Wave Refractor", enriched.get("parallel"))
    check("year survives", enriched["year"] == "2025-26", enriched.get("year"))
    blank = dict(kon, year="", set="")
    filled = sports.enrich_sports_card_data(blank)
    check("missing year filled from listings", filled["year"] == "2025", filled.get("year"))
    check("missing set filled from listings", filled["set"] == "Topps Chrome", filled.get("set"))
    check("parallel still untouched", filled["parallel"] == "Ray Wave Refractor")

    print("\n7. Promo cards do not take the base card's catalogue price")
    onepiece.card_versions = lambda code: [{"card_name": "Jinbe", "set_name": "Egghead",
                                            "rarity": "C", "market_price": "0.25"}]
    stamped = onepiece.enrich_one_piece_card_data(dict(jinbe))
    check("no catalogue price on a promo", "onepiece_market_price" not in stamped)
    plain = onepiece.enrich_one_piece_card_data(dict(regular))
    check("regular print keeps it", plain.get("onepiece_market_price") == 0.25)

    print("\n8. Under $50 goes to auction, with an opening bid")
    settings.AUCTION_UNDER, settings.AUCTION_MIN_START = 50.0, 0.99
    liquid = {"estimated_price": 12.0, "basis": "sold", "count": 10}
    plan = choose_listing_format(liquid, 12.0)
    check("auction chosen", plan["format"] == "AUCTION", plan)
    check("liquid card opens at a quarter, on a .99", plan["start_price"] == 2.99, plan)
    check("net after fees reported", 0 < plan["net_at_start"] < plan["start_price"], plan)
    check("$50 and up stays Buy It Now",
          choose_listing_format(liquid, 50.0)["format"] == "FIXED_PRICE")
    settings.AUCTION_MIN_START = 4.0
    check("minimum opening bid is honoured",
          choose_listing_format(liquid, 12.0)["start_price"] == 4.0)
    settings.AUCTION_MIN_START = 0.99
    settings.AUCTION_UNDER = 0
    check("AUCTION_UNDER=0 turns it off",
          choose_listing_format(liquid, 12.0)["format"] == "FIXED_PRICE")
    settings.AUCTION_UNDER = 50.0
    check("price points", [price_point(v) for v in (0.4, 3.0, 3.75, 12.37)] == [0.99, 2.99, 3.99, 11.99])

    print("\n9. Offer payloads per format")
    settings.EBAY_MERCHANT_LOCATION_KEY = settings.EBAY_MERCHANT_LOCATION_KEY or "home_us"
    settings.EBAY_FULFILLMENT_POLICY_ID_UNDER_20 = "UNDER"
    settings.EBAY_FULFILLMENT_POLICY_ID_OVER_20 = "OVER"
    payload = build_offer_payload("sku1", "261328", 35.0, listing_format="AUCTION",
                                  auction_start_price=9.99)
    check("format AUCTION", payload["format"] == "AUCTION")
    check("opening bid set", payload["pricingSummary"]["auctionStartPrice"]["value"] == "9.99")
    check("no availableQuantity on an auction (eBay 25762)", "availableQuantity" not in payload)
    check("no Best Offer on an auction", "bestOfferTerms" not in payload["listingPolicies"])
    check("shipping follows market value, not the opening bid",
          payload["listingPolicies"]["fulfillmentPolicyId"] == "OVER")
    check("duration set", payload["listingDuration"] == settings.AUCTION_DURATION)
    fixed = build_offer_payload("sku1", "261328", 35.0)
    check("fixed price unchanged", fixed["format"] == "FIXED_PRICE"
          and fixed["listingPolicies"]["bestOfferTerms"]["bestOfferEnabled"] is True)
    check("fixed price keeps availableQuantity", fixed.get("availableQuantity") == 1)

    print("\n10. Repricer and Promote never see auctions")
    listings.fetch_all_active_listings = lambda: [
        {"sku": "", "listing_id": "1", "listing_type": "FixedPriceItem", "category_id": "1"},
        {"sku": "", "listing_id": "2", "listing_type": "Chinese", "category_id": "1"},
    ]
    rows = listings.fetch_active_listings(workers=1)
    check("auction filtered out", [r["listing_id"] for r in rows] == ["1"], rows)
    rows = listings.fetch_active_listings(workers=1, include_auctions=True)
    check("still available on request", len(rows) == 2)

    print("\n11. The identifier gets close-ups at full detail")
    scan = Path(tempfile.mkdtemp()) / "card.jpg"
    Image.new("RGB", (1100, 1500), "white").save(scan)
    tiles = ScannedImage(str(scan)).vision_tiles()
    check("four tiles", len(tiles) == 4)
    check("tiles are enlarged", all(t.startswith("data:image/jpeg;base64,") for t in tiles))

    sent = {}

    class Response:
        output_text = "{}"

    class Responses:
        def create(self, **request):
            sent.update(request)
            return Response()

    class Client:
        responses = Responses()

    vision._client = Client()
    vision.generate_card_data("data:front", "data:back", front_tiles=tiles, model="gpt-5.4-mini",
                            references=[])
    content = sent["input"][0]["content"]
    images = [part for part in content if part["type"] == "input_image"]
    check("front + 4 tiles + back", len(images) == 6, len(images))
    check("back image last", images[-1]["image_url"] == "data:back")
    check("high detail everywhere", all(i["detail"] == "high" for i in images))
    check("reasoning set for gpt-5 models", "reasoning" in sent)

    print("\n12. Parallel examples learned from published titles")
    import services.parallel_refs as refs
    check("Ray Wave found", refs.parallel_in_title(
        "2025 Topps Chrome Kon Knueppel #154 Ray Wave Refractor RC") == "Ray Wave Refractor")
    check("longest match wins", refs.parallel_in_title(
        "2025 Topps Chrome Tre Johnson III #156 Blue Wave /150") == "Blue Wave")
    check("bare colour beside a serial", refs.parallel_in_title(
        "2025 Bowman Liam McNeeley Black 3/10") == "Black")
    check("team colours are not parallels", refs.parallel_in_title(
        "2025 Topps Chrome Dylan Larkin Red Wings #12") == "")
    check("no parallel on a base card", refs.parallel_in_title(
        "2025 Topps Chrome Stephen Curry #MT-2") == "")

    refs.REF_DIR = Path(tempfile.mkdtemp()) / "refs"
    refs.INDEX_PATH = refs.REF_DIR / "index.json"
    refs._cache.clear()
    for n in range(3):
        refs.learn_from_published("2025 Topps Chrome A #1 X-Refractor", {}, str(scan), 0,
                                  source="sku%d" % n)
    refs.learn_from_published("2025 Topps Chrome B #2 Ray Wave Refractor", {}, str(scan), 0,
                              source="ray1")
    check("same card is not stored twice",
          refs.learn_from_published("2025 Topps Chrome B #2 Ray Wave Refractor", {}, str(scan),
                                    0, source="ray1") == "")
    stored = refs._load_index()
    check("only the newest examples per parallel kept",
          [r["source"] for r in stored if r["label"] == "X-Refractor"] == ["sku1", "sku2"])
    check("files pruned with the index", len(list(refs.REF_DIR.glob("*.jpg"))) == 3)
    loaded = refs.reference_images()
    check("examples load", sorted(l for l, _ in loaded) == ["Ray Wave Refractor", "X-Refractor",
                                                            "X-Refractor"], loaded and [l for l, _ in loaded])
    check("eval can exclude a card's own example",
          [l for l, _ in refs.reference_images(exclude_source="ray1")] == ["X-Refractor"] * 2)

    sent.clear()
    vision.generate_card_data("data:front", "", model="gpt-5.4-mini")
    content = sent["input"][0]["content"]
    examples = [p for p in content if p["type"] == "input_image" and p["detail"] == "low"]
    check("examples sent after the card, at low detail", len(examples) == 3, len(examples))
    labels_sent = [p["text"] for p in content if p["type"] == "input_text" and p["text"].startswith("Example")]
    check("each example labelled", labels_sent[0] == "Example - Ray Wave Refractor:", labels_sent)
    check("finish described before the parallel",
          vision.SCHEMA.index("finish_observed") < vision.SCHEMA.index('"parallel"'))

    print("\n13. Serials checked against the product checklist")
    from services.parallel_catalog import apply_catalog
    chrome = {"card_category": "sports", "set": "Topps Chrome", "year": "2025-26",
              "sport": "Basketball", "player_athlete": "Austin Reaves", "card_number": "54"}
    filled = apply_catalog(dict(chrome, serial_number="112/399", parallel="Refractor"))
    check("/399 can only be Magenta", filled["parallel"] == "Magenta Refractor", filled.get("parallel"))
    check("filled parallel titled the seller's way", "Magenta /399" in title(filled), title(filled))
    shared = apply_catalog(dict(chrome, serial_number="31/150", parallel=""))
    check("shared run is not guessed", shared["parallel"] == "")
    check("shared run lists the options", "Blue Wave" in " ".join(shared["catalog_warnings"]))
    wrong = apply_catalog(dict(chrome, serial_number="31/150", parallel="Gold Refractor"))
    check("name that contradicts the serial is flagged",
          any("/50" in w for w in vision.title_warnings(wrong)), vision.title_warnings(wrong))
    check("Ray Wave colours are recognised",
          "catalog_warnings" not in apply_catalog(dict(chrome, serial_number="31/150",
                                                       parallel="Blue Ray Wave Refractor")))
    flagship = apply_catalog({"set": "Topps Flagship", "year": "2026", "sport": "Football",
                              "serial_number": "61/99", "parallel": "Orange"})
    check("misread paper parallel points at Vintage Stock",
          "Vintage Stock" in " ".join(flagship.get("catalog_warnings", [])), flagship.get("catalog_warnings"))
    fixed = apply_catalog(dict(chrome, year="2026", parallel="X-Refractor"))
    check("season read by its second year is corrected", fixed["year"] == "2025-26", fixed.get("year"))
    older = apply_catalog(dict(chrome, year="2021", parallel="X-Refractor"))
    check("other years left as read", older["year"] == "2021", older.get("year"))
    made_up = dict(chrome, card_number="154", insert_set="Horizons", parallel="Ray Wave Refractor")
    check("invented insert on a base number dropped", "Horizons" not in title(made_up), title(made_up))
    real = dict(chrome, card_number="MT-2", insert_set="Moment in Time")
    check("real insert kept", "Moment in Time" in title(real), title(real))
    print("\n14. Doubtful serials get a second, zoomed-in read")
    import services.serial_check as serial_check
    check("magenta with no serial is rechecked",
          serial_check.reasons_to_check(dict(chrome, parallel="Magenta Refractor")) != [])
    check("odd run is rechecked",
          serial_check.reasons_to_check(dict(chrome, serial_number="03/18")) != [])
    check("any parallel with no serial gets a look",
          serial_check.reasons_to_check(dict(chrome, parallel="X-Refractor")) != [])
    settings.SERIAL_CHECK = "all"
    check("'all' looks at a plain base card too",
          serial_check.reasons_to_check(dict(chrome, parallel="")) == ["no serial read"])
    settings.SERIAL_CHECK = "doubtful"
    check("'doubtful' skips a base card",
          serial_check.reasons_to_check(dict(chrome, parallel="")) == [])
    check("clean numbered card skips it",
          serial_check.reasons_to_check(dict(chrome, parallel="Magenta Refractor",
                                             serial_number="112/399")) == [])
    check("TCG cards never rechecked",
          serial_check.reasons_to_check({"card_category": "pokemon", "serial_number": "03/18"}) == [])

    replies = []

    class SerialResponses:
        def create(self, **request):
            sent.clear()
            sent.update(request)
            response = Response()
            response.output_text = replies.pop(0)
            return response

    class SerialClient:
        responses = SerialResponses()

    vision._client = SerialClient()
    front = ScannedImage(str(scan))
    replies.append('{"serial_number": "112/399", "where": "front right edge", "confidence": "high"}')
    fixed = serial_check.recheck_serial(dict(chrome, parallel="Refractor", serial_number="12/99"), front)
    check("second read replaces a doubtful serial", fixed["serial_number"] == "112/399", fixed)
    check("corrected run names the parallel", fixed["parallel"] == "Magenta Refractor", fixed.get("parallel"))
    check("first read kept for reference", fixed["serial_number_first_read"] == "12/99")
    zoom = [p for p in sent["input"][0]["content"] if p["type"] == "input_image"]
    check("front + 9 zoomed tiles", len(zoom) == 10, len(zoom))
    prompt = sent["input"][0]["content"][0]["text"]
    check("checklist runs given to the reader", "/399" in prompt and "/150" in prompt)

    replies.append('{"serial_number": "", "where": "", "confidence": "low"}')
    kept = serial_check.recheck_serial(dict(chrome, serial_number="03/18"), front)
    check("'none found' never erases a serial", kept["serial_number"] == "03/18", kept)
    check("no call for a base card in 'doubtful' mode",
          "serial_check" not in serial_check.recheck_serial(dict(chrome, parallel=""), front))
    settings.SERIAL_CHECK = "all"

    # Located in a tile, then re-read upright by the read model.
    replies.extend(['{"serial_number": "11/399", "tile": 2, "confidence": "medium"}',
                    '{"serial_number": "112/399", "confidence": "high"}'])
    reread = serial_check.recheck_serial(dict(chrome, parallel="Refractor"), front)
    check("located stamp is re-read", reread["serial_number"] == "112/399", reread.get("serial_number"))
    check("re-read uses the read model", sent["model"] == settings.SERIAL_READ_MODEL, sent.get("model"))
    turns = [p for p in sent["input"][0]["content"] if p["type"] == "input_image"]
    check("four turns of the tile", len(turns) == 4, len(turns))

    finest = {"set": "Topps Finest", "year": "2025-26", "sport": "Basketball"}
    tiers = apply_catalog(dict(finest, parallel="Gold Refractor", serial_number="10/25"))
    check("any tier's run accepted", "catalog_warnings" not in tiers, tiers.get("catalog_warnings"))

    print("\n15. Every checklist product is reachable from its own name")
    from services.parallel_catalog import catalog, match_product
    misrouted = []
    for key, product in catalog().items():
        sport = product.get("sport") or next(
            (s for s in ("basketball", "football", "baseball", "hockey", "soccer") if s in key), "")
        if "f1" in key or "formula" in key:
            sport = "racing"
        got, _ = match_product({"set": product["match"][0], "year": product["year"], "sport": sport})
        if got != key:
            misrouted.append((key, got))
    check("{} products, none misrouted".format(len(catalog())), not misrouted, misrouted[:5])
    check("UFC set read without its league word still found",
          match_product({"set": "Topps Chrome", "year": "2025", "sport": "MMA",
                         "league": "UFC"})[0] == "topps_chrome_ufc_mma_2025")
    check("unknown product left alone",
          apply_catalog({"set": "Panini Prizm", "serial_number": "5/99"}) == {"set": "Panini Prizm",
                                                                         "serial_number": "5/99"})

    print("\n16. Gemini as the vision model")
    from services import gemini_service
    from services import serial_check as sc

    class GeminiReply:
        def __init__(self, status, body):
            self.status_code, self._body = status, body
            self.text = json.dumps(body)

        def json(self):
            return self._body

    posted = []
    gemini_replies = [
        GeminiReply(429, {"error": {"status": "RESOURCE_EXHAUSTED",
                                    "details": [{"retryDelay": "0s"}]}}),
        GeminiReply(200, {"candidates": [{"content": {"parts": [
            {"text": "thinking...", "thought": True}, {"text": '{"player": "X"}'}]}}],
            "usageMetadata": {"promptTokenCount": 1000, "candidatesTokenCount": 50,
                              "thoughtsTokenCount": 20}}),
    ]
    original_post = gemini_service.requests.post
    gemini_service.requests.post = lambda url, **kw: (posted.append((url, kw)),
                                                      gemini_replies.pop(0))[1]
    try:
        reply = vision.complete([vision._text("hi"),
                                 vision._image("data:image/jpeg;base64,QUJD")],
                                "gemini-3.8-flash")
    finally:
        gemini_service.requests.post = original_post
    check("Gemini reply text, thoughts dropped", reply == '{"player": "X"}', reply)
    check("rate limit retried", len(posted) == 2, len(posted))
    sent = posted[-1][1]["json"]
    check("image sent inline", sent["contents"][0]["parts"][1] ==
          {"inline_data": {"mime_type": "image/jpeg", "data": "QUJD"}})
    check("low-detail reference sent at low resolution",
          gemini_service._part({"type": "input_image", "image_url": "data:image/jpeg;base64,QUJD",
                                "detail": "low"})["mediaResolution"]["level"]
          == "MEDIA_RESOLUTION_LOW")
    check("JSON reply requested",
          sent["generationConfig"]["responseMimeType"] == "application/json")
    check("tokens counted", gemini_service.usage["input_tokens"] == 1000
          and gemini_service.usage["output_tokens"] == 70, gemini_service.usage)
    check("key sent as a header, not in the URL",
          "key=" not in posted[-1][0] and "x-goog-api-key" in posted[-1][1]["headers"])
    saved_read = settings.SERIAL_READ_MODEL
    settings.SERIAL_READ_MODEL = "gpt-5.5"
    check("serial re-read stays on Gemini when the card's model is Gemini",
          sc._read_model("gemini-3.8-flash") == "gemini-3.8-flash")
    check("serial re-read uses gpt-5.5 for an OpenAI card", sc._read_model("gpt-5.4-mini") == "gpt-5.5")
    settings.SERIAL_READ_MODEL = saved_read

    print("\n17. Free-tier Gemini key takes over when paid credit runs out")
    ok = {"candidates": [{"content": {"parts": [{"text": "{}"}]}}]}
    depleted = GeminiReply(402, {"error": {"status": "RESOURCE_EXHAUSTED",
                                           "message": "Prepayment credits depleted"}})
    saved = settings.GEMINI_API_KEY, settings.GEMINI_FREE_API_KEY, settings.GEMINI_FREE_MODEL
    settings.GEMINI_API_KEY, settings.GEMINI_FREE_MODEL = "paid-key", "gemini-free"
    posted.clear()
    gemini_service.requests.post = lambda url, **kw: (posted.append((url, kw)),
                                                      gemini_replies.pop(0))[1]
    try:
        settings.GEMINI_FREE_API_KEY = ""
        gemini_replies[:] = [depleted]
        try:
            gemini_service.generate([vision._text("hi")], "gemini-3.8-flash")
            error = ""
        except RuntimeError as exc:
            error = str(exc)
        check("no free key: empty balance still reported", "402" in error and
              not gemini_service.on_free_tier, error)

        settings.GEMINI_FREE_API_KEY = "free-key"
        posted.clear()
        gemini_replies[:] = [depleted, GeminiReply(200, ok), GeminiReply(200, ok)]
        gemini_service.generate([vision._text("hi")], "gemini-3.8-flash")
        gemini_service.generate([vision._text("hi")], "gemini-3.8-flash")
        keys = [kw["headers"]["x-goog-api-key"] for _, kw in posted]
        check("402 retried on the free key, which then stays in use",
              keys == ["paid-key", "free-key", "free-key"], keys)
        check("free tier uses GEMINI_FREE_MODEL", "gemini-free:" in posted[-1][0], posted[-1][0])
        check("free calls counted", gemini_service.usage["free_calls"] == 2, gemini_service.usage)
    finally:
        gemini_service.requests.post = original_post
        gemini_service.on_free_tier = False
        settings.GEMINI_API_KEY, settings.GEMINI_FREE_API_KEY, settings.GEMINI_FREE_MODEL = saved

    print("\n" + ("ALL CHECKS PASSED" if not failures else "FAILED: " + ", ".join(failures)))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
