"""Validate One Piece cards against optcgapi.com.

Vision reads the card art well enough but not the catalogue: it returns the set code
("PRB02") where a buyer searches the set name ("Premium Booster - The Best - Vol. 2"),
splits the card code across two fields, and picks marketing text off the card as the
brand. This lookup replaces guesses with the real catalogue entry.

One code is several cards. OP16-063 is a $0.13 base Kuzan, a $3 Alternate Art and a
$1,073 Manga; SP reprints of a code are listed under the later set they came in. So the
version is chosen by comparing the scan with the catalogue's own images, and the price
comes from that version - never from the base card unless the base card is what it is.

Set lists and card versions are cached on disk; each is fetched at most once.
"""

import base64
import io
import json
import os
import re
import threading
from typing import Any, Dict, List, Optional

import requests

API_BASE = "https://optcgapi.com/api"
DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
CACHE_PATH = os.path.join(DATA_DIR, "onepiece_cache.json")
IMAGE_DIR = os.path.join(DATA_DIR, "onepiece_images")

# The catalogue's version labels, as sellers and buyers write them. Other bracketed text
# in catalogue names ("(119)", "(Galdino)", "(EB04-007)") only tells reprints apart.
VERSION_TERMS = {
    "alternate art": "Alt Art", "super alternate art": "Super Alt Art",
    "red super alternate art": "Red Super Alt Art", "manga": "Manga", "sp": "SP",
    "gold": "Gold", "silver": "Silver", "tr": "TR", "full art": "Full Art",
    "pirate foil": "Pirate Foil", "wanted poster": "Wanted Poster", "parallel": "Parallel",
}
# How far to look when a digit of the set number was misread (EB01 for EB04).
MAX_SET_NUMBER = 20

_lock = threading.Lock()

# Card codes look like OP16-095, ST29-008, EB03-054, PRB02-006. The API's set ids carry
# a hyphen: OP-16, ST-29, EB-03, PRB-02.
CODE = re.compile(r"\b(OP|ST|EB|PRB)\s*-?\s*(\d{1,2})\s*-\s*(\d{1,3})\b", re.IGNORECASE)
PREFIX_ONLY = re.compile(r"\b(OP|ST|EB|PRB)\s*-?\s*(\d{1,2})\b", re.IGNORECASE)


def _load_cache() -> Dict[str, Any]:
    try:
        with open(CACHE_PATH, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_cache(data: Dict[str, Any]) -> None:
    try:
        os.makedirs(os.path.dirname(CACHE_PATH), exist_ok=True)
        with open(CACHE_PATH, "w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=2, sort_keys=True)
    except OSError:
        pass


def clean(value: Any) -> str:
    return str(value or "").strip()


def parse_card_code(card_data: Dict[str, Any]) -> Optional[Dict[str, str]]:
    """Find the card's catalogue code, wherever the model happened to put it.

    The number and the set are often split ("set": "OP09", "card_number": "068"), so
    both fields are searched together before falling back to combining them.
    """
    haystack = " ".join(clean(card_data.get(key)) for key in
                        ["card_number", "set", "notes", "name", "card_name", "variant"])

    match = CODE.search(haystack)
    if match:
        prefix, set_number, card_number = match.groups()
        return _code_parts(prefix, set_number, card_number)

    # Set and number in separate fields: "OP09" + "068".
    prefix_match = PREFIX_ONLY.search(" ".join(
        clean(card_data.get(key)) for key in ["set", "notes", "variant"]))
    number_match = re.search(r"\b(\d{1,3})\b", clean(card_data.get("card_number")))

    if prefix_match and number_match:
        prefix, set_number = prefix_match.groups()
        return _code_parts(prefix, set_number, number_match.group(1))

    return None


def _code_parts(prefix: str, set_number: str, card_number: str) -> Dict[str, str]:
    prefix = prefix.upper()
    set_number = set_number.zfill(2)
    card_number = card_number.zfill(3)
    return {
        "set_id": "{}-{}".format(prefix, set_number),      # API form: OP-16
        "card_set_id": "{}{}-{}".format(prefix, set_number, card_number),  # OP16-095
        "prefix": prefix,
    }


def fetch_set(set_id: str) -> List[Dict[str, Any]]:
    """Every card in a set, cached after the first call."""
    with _lock:
        cache = _load_cache()
        if set_id in cache:
            return cache[set_id] or []

    endpoint = "{}/{}/{}/".format(API_BASE, "decks" if set_id.startswith("ST") else "sets", set_id)

    try:
        response = requests.get(endpoint, timeout=20)
        cards = response.json() if response.status_code == 200 else []
    except Exception:
        cards = []

    if not isinstance(cards, list):
        cards = []

    with _lock:
        cache = _load_cache()
        cache[set_id] = cards
        _save_cache(cache)

    return cards


def find_card(code: Dict[str, str]) -> Optional[Dict[str, Any]]:
    for card in fetch_set(code["set_id"]):
        if clean(card.get("card_set_id")).upper() == code["card_set_id"]:
            return card
    return None


def strip_card_number_suffix(name: str) -> str:
    """Strip catalogue bookkeeping from a card name.

    The catalogue disambiguates reprints two ways: "Monkey.D.Luffy (095)" and
    "Roronoa Zoro - PRB02-006". Both would otherwise repeat the card code in the title.
    Descriptive parentheses such as "(Full Art)" are kept - those are worth searching.
    """
    text = clean(name)
    text = re.sub(r"\s*[-–]\s*(OP|ST|EB|PRB)\d{1,2}-\d{1,3}\s*$", "", text,
                  flags=re.IGNORECASE)
    text = re.sub(r"\s*\(\d{1,3}\)\s*$", "", text)
    return text.strip()


def is_probably_one_piece(card_data: Dict[str, Any]) -> bool:
    combined = " ".join(clean(card_data.get(key)).lower() for key in
                        ["card_category", "game", "brand", "manufacturer", "set", "notes"])
    # Union Arena is Bandai too, with its own codes; it must never be looked up here.
    if "union arena" in combined or "union_arena" in combined:
        return False
    return ("one piece" in combined or "onepiece" in combined or "one_piece" in combined
            or "bandai" in combined)


def card_versions(card_set_id: str) -> List[Dict[str, Any]]:
    """Every printing of one card code, across every set (SP reprints included)."""
    key = "card:" + card_set_id
    with _lock:
        cache = _load_cache()
        if key in cache:
            return cache[key] or []

    try:
        response = requests.get("{}/sets/card/{}/".format(API_BASE, card_set_id), timeout=20)
        versions = response.json() if response.status_code == 200 else None
    except Exception:
        versions = None

    if versions is None:
        # Endpoint unreachable: fall back to the code's own set, and don't cache that.
        code = parse_card_code({"card_number": card_set_id})
        return [c for c in fetch_set(code["set_id"])
                if clean(c.get("card_set_id")).upper() == card_set_id] if code else []

    versions = versions if isinstance(versions, list) else [versions]
    versions = [v for v in versions if isinstance(v, dict) and v.get("card_name")]
    with _lock:
        cache = _load_cache()
        cache[key] = versions
        _save_cache(cache)
    return versions


def base_name(catalogue_name: str) -> str:
    """"Monkey.D.Luffy - OP09-119 (SP)" -> "Monkey.D.Luffy"."""
    text = re.sub(r"\s*\([^)]*\)", "", clean(catalogue_name))
    return strip_card_number_suffix(text)


def version_label(catalogue_name: str) -> str:
    """The version as a buyer searches it: "Kuzan (Manga)" -> "Manga", base -> ""."""
    found = [VERSION_TERMS[part.strip().lower()]
             for part in re.findall(r"\(([^)]*)\)", clean(catalogue_name))
             if part.strip().lower() in VERSION_TERMS]
    return " ".join(found)


def _name_key(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", clean(name).lower())


def names_match(read: str, catalogue: str) -> bool:
    a, b = _name_key(read), _name_key(base_name(catalogue))
    return bool(a and b) and (a in b or b in a)


def _read_name(card_data: Dict[str, Any]) -> str:
    return clean(card_data.get("name") or card_data.get("card_name") or card_data.get("character"))


def matching_versions(code: Dict[str, str], read_name: str):
    """(code, versions) for the card actually printed, correcting a misread set number.

    A catalogue hit on the wrong card is worse than a miss: it replaces a correct name
    with someone else's. So the catalogue's name has to agree with the name on the card.
    """
    versions = card_versions(code["card_set_id"])
    if not read_name or any(names_match(read_name, v["card_name"]) for v in versions):
        return code, versions

    number = code["card_set_id"].split("-")[-1]
    for set_number in range(1, MAX_SET_NUMBER + 1):
        candidate = _code_parts(code["prefix"], str(set_number), number)
        if candidate["card_set_id"] == code["card_set_id"]:
            continue
        found = [v for v in card_versions(candidate["card_set_id"])
                 if names_match(read_name, v["card_name"])]
        if found:
            return candidate, card_versions(candidate["card_set_id"])
    return code, []


def _reference_image(version: Dict[str, Any]) -> str:
    """The catalogue's picture of one version as a data URL, kept on disk after once."""
    from PIL import Image

    image_id = re.sub(r"[^A-Za-z0-9_-]", "", clean(version.get("card_image_id"))) or "card"
    path = os.path.join(IMAGE_DIR, image_id + ".jpg")
    if not os.path.exists(path):
        response = requests.get(version["card_image"], timeout=20)
        response.raise_for_status()
        image = Image.open(io.BytesIO(response.content)).convert("RGB")
        image.thumbnail((400, 560))
        os.makedirs(IMAGE_DIR, exist_ok=True)
        image.save(path, "JPEG", quality=85)
    with open(path, "rb") as handle:
        return "data:image/jpeg;base64," + base64.b64encode(handle.read()).decode()


PICK_PROMPT = """The first image is a scanned One Piece card (it may be rotated or in a
sleeve). The numbered images after it are the catalogue's pictures of every printing of
this card code. Which printing is the scanned card?

Printings share the code and the card text; they differ in the artwork and frame: a
different illustration (Alternate Art), a black-and-white manga-panel background
(Manga), a small "SP" box printed just before the card code with a special frame (SP),
gold or silver foil versions, and so on. Compare the illustration, pose, background and
frame - not colour, glare or scan quality. The catalogue images may carry a "SAMPLE"
watermark; ignore it.

Return JSON only: {"option": <number>, "confident": true or false}. "confident" is false
when two options look alike and you cannot tell which."""


def pick_version(front_data_url: str, versions: List[Dict[str, Any]]) -> Dict[str, Any]:
    """{"index": i, "confident": bool} from comparing the scan with each version's image."""
    from services import openai_service as vision
    from utils.json_utils import try_parse_json

    content = [vision._text(PICK_PROMPT), vision._image(front_data_url)]
    for number, version in enumerate(versions, start=1):
        content.append(vision._text("Option {}: {}".format(
            number, version_label(version["card_name"]) or "Base")))
        # Low detail is enough to tell artworks apart: 267 tokens an image, not 1,093.
        content.append({"type": "input_image", "image_url": _reference_image(version),
                        "detail": "low"})

    reply = try_parse_json(vision.complete(content)) or {}
    try:
        index = int(reply.get("option")) - 1
    except (TypeError, ValueError):
        return {"index": None, "confident": False}
    if not 0 <= index < len(versions):
        return {"index": None, "confident": False}
    return {"index": index, "confident": bool(reply.get("confident"))}


def hinted_term(card_data: Dict[str, Any]) -> str:
    """What the identifier said the version is, in catalogue terms; "" for nothing special.

    It describes these loosely ("Special Card", "Special Rare" for an SP; "Parallel" for
    a Manga), so this is a hint to check against, never the answer on its own.
    """
    text = " ".join(clean(card_data.get(key)).lower() for key in ("parallel", "variant", "notes"))
    if re.search(r"\bsp\b|special (card|rare)", text) or clean(card_data.get("rarity")).upper() == "SP":
        return "SP"
    if "manga" in text:
        return "Manga"
    if re.search(r"alt(ernate)?[ -]?art|parallel|full art", text):
        return "Alt Art"
    return ""


def _fits(term: str, catalogue_name: str) -> bool:
    label = version_label(catalogue_name)
    return label == "" if not term else (term == "Alt Art" and "Alt Art" in label) or \
        label.split(" ")[0] == term


def _agrees(term: str, catalogue_name: str) -> bool:
    """Whether a printing is consistent with what the identifier read off the card.

    An SP box and a manga background are specific, visible facts; "Alt Art" is loose
    (the identifier says it of any non-regular art, Manga included); silence says nothing.
    """
    label = version_label(catalogue_name)
    if term == "SP":
        return label.startswith("SP")
    if term == "Manga":
        return "Manga" in label
    if term == "Alt Art":
        return label != ""
    return True


def _hinted_version(card_data: Dict[str, Any], versions: List[Dict[str, Any]]) -> Optional[int]:
    """The version the identifier's own words point to, when only one fits them."""
    term = hinted_term(card_data)
    fits = [i for i, v in enumerate(versions) if _fits(term, v["card_name"])]
    return fits[0] if len(fits) == 1 else None


def enrich_one_piece_card_data(card_data: Dict[str, Any],
                               front_data_url: str = "") -> Dict[str, Any]:
    """Replace guessed One Piece fields with the catalogue's own values.

    Returns the input untouched when the card cannot be identified, so a lookup miss
    never makes a listing worse. `front_data_url` lets the version be chosen by eye.
    """
    if not is_probably_one_piece(card_data):
        return card_data

    code = parse_card_code(card_data)
    if not code:
        return card_data

    read_name = _read_name(card_data)
    code, versions = matching_versions(code, read_name)

    if not versions and read_name:
        # Nothing in the catalogue carries the name printed on the card: keep the
        # identifier's reading rather than another card's name, and say so.
        enriched = dict(card_data)
        enriched.update(card_category="one_piece", game="One Piece Card Game",
                        manufacturer="Bandai", brand="Bandai",
                        card_number=code["card_set_id"])
        enriched["identity_review"] = ["{} is not {} in the One Piece catalogue - check the "
                                       "card code".format(read_name, code["card_set_id"])]
        return enriched

    if len(versions) > 1:
        choice = {"index": None, "confident": False}
        if front_data_url:
            try:
                choice = pick_version(front_data_url, versions)
            except Exception:
                pass
        if choice["index"] is None:
            hinted = _hinted_version(card_data, versions)
            choice = {"index": hinted, "confident": False} if hinted is not None else choice
        card = versions[choice["index"]] if choice["index"] is not None else None
        # The images and the identifier's reading disagree (it saw an SP box, the
        # comparison picked the base): too costly to guess, so it gets checked.
        confident = choice["confident"] and (
            card is None or _agrees(hinted_term(card_data), card["card_name"]))
    elif versions and not _agrees(hinted_term(card_data), versions[0]["card_name"]):
        # One printing listed, but the card was read as an SP/Manga/Alt Art: the
        # catalogue is missing that printing, and the base card's price is not this card's.
        card, confident = None, False
    else:
        card = versions[0] if versions else None
        confident = True

    enriched = dict(card_data)
    enriched["card_number"] = code["card_set_id"]
    enriched["card_category"] = "one_piece"
    enriched["game"] = "One Piece Card Game"
    enriched["manufacturer"] = "Bandai"
    enriched["brand"] = "Bandai"          # replaces marketing text read off the card

    if versions:
        # The card itself is known even when its version is not.
        name = base_name(versions[0]["card_name"])
        enriched["name"] = enriched["card_name"] = enriched["character"] = (
            name or clean(enriched.get("name")))
        enriched["rarity"] = clean(versions[0].get("rarity")) or clean(enriched.get("rarity"))
        enriched["card_type"] = (clean(versions[0].get("card_type"))
                                 or clean(enriched.get("card_type")))

    if not card:
        term = hinted_term(card_data)
        if term:
            enriched["parallel"] = term     # the hobby word, not "Special Card"
        if len(versions) == 1:
            enriched["identity_review"] = [
                "read as {} {}, but the catalogue only lists the base card - price it from "
                "sold listings".format(code["card_set_id"], term)]
        elif versions:
            prices = sorted(float(v.get("market_price") or 0) for v in versions)
            enriched["identity_review"] = [
                "could not tell which printing of {} this is ({}) - prices run ${:,.2f} to "
                "${:,.2f}".format(code["card_set_id"], ", ".join(
                    version_label(v["card_name"]) or "Base" for v in versions),
                    prices[0], prices[-1])]
        return enriched

    label = version_label(card.get("card_name"))
    # The catalogue's word for the version replaces the identifier's free text
    # ("Special Card", "Parallel" for a Manga). A base card carries none.
    enriched["parallel"] = label
    for key in ("variant", "parallel_variety", "validated_variant"):
        enriched.pop(key, None)
    enriched["set"] = clean(card.get("set_name")) or clean(enriched.get("set"))
    enriched["onepiece_lookup_verified"] = True
    enriched["onepiece_set_id"] = clean(card.get("set_id")) or code["set_id"]
    enriched["onepiece_version"] = clean(card.get("card_image_id"))

    if not confident:
        enriched["identity_review"] = [
            "printing of {} is a best guess ({}) - check it against the card".format(
                code["card_set_id"], label or "Base")]

    market = card.get("market_price")
    # A stamped promo is a different card with its own market, so it is priced from sold
    # comps instead. An unsure version is too: the catalogue's prices for the versions of
    # one code differ by a thousand times.
    if market and confident and not clean(card_data.get("promo_stamp")):
        try:
            enriched["onepiece_market_price"] = float(market)
        except (TypeError, ValueError):
            pass

    return enriched
