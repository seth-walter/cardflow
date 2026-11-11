"""A second, focused look for the serial number on cards where the first read is doubtful.

The identifier reads a whole card at once, and a serial is a few millimetres of foil,
often sideways over busy art. Across eval runs the same card came back /399, /299 or
with no serial at all. This pass does one job: find the stamp, at higher zoom, knowing
which print runs the product actually has.

It runs only when the first read looks wrong or incomplete, so most cards cost nothing
extra.
"""

import re

from config import settings
from services import openai_service as vision
from services.parallel_catalog import apply_catalog, is_insert_or_auto, match_product
from utils.json_utils import try_parse_json

GRID = 3
SHARE = 0.42
SCALE = 3

PROMPT = """Find the serial number stamped on this trading card. Nothing else matters.

A serial number is a small stamped number in the form NN/NNN (e.g. "03/10", "112/399",
"1/1"), in gold, silver, black or coloured foil, or printed ink. It can be anywhere: along
an edge or corner of the front, running sideways, over the artwork or the border, or on
the back (often near the card number or the copyright line). Read it digit by digit.
Foil digits catch the light unevenly; check each digit against its neighbours.

{runs}
The images are: the front, then {tiles} enlarged overlapping close-ups of the front (row by
row, top-left first), then the back if present.

Return ONLY JSON: {{"serial_number": "NN/NNN or empty", "tile": close-up number 1-{tiles}
where the stamp is clearest (0 if only on the back or not found), "where": "front
top-left / back bottom / ...", "confidence": "high | medium | low"}}. Empty serial_number
when the card has no serial. Never guess one from the card's colour."""

READ_PROMPT = """These four images are the same close-up of a trading card, turned 0, 90, 180
and 270 degrees, contrast-boosted. It contains a stamped serial number (NN/NNN), which
reads upright in one of them. A first look read it as "{first}" - read it again yourself,
digit by digit, in the upright image; do not just copy that reading.
{runs}
Return ONLY JSON: {{"serial_number": "NN/NNN", "confidence": "high | medium | low"}}."""


def _run(card: dict) -> str:
    return vision.print_run(card)


def reasons_to_check(card: dict) -> list:
    """Why the first read is doubtful. Empty means it looks fine."""
    category = str(card.get("card_category") or "").lower()
    if category in {"pokemon", "one_piece", "union_arena"}:
        return []

    reasons = []
    run = _run(card)
    if vision.title_warnings(card) or vision.title_warnings(apply_catalog(card)):
        reasons.append("warnings")

    # Any parallel with no serial gets a look: a missed serial is the costliest error,
    # and a pink /399 read as plain "Refractor" looks perfectly consistent otherwise.
    # When there really is no serial this is one call that finds nothing.
    if not run and str(card.get("parallel") or "").strip():
        reasons.append("parallel with no serial")
    # A card read as plain base can still carry a serial: a Blue Wave /150 came back
    # with neither its colour nor its stamp. "all" looks at every sports card.
    if not run and not reasons and settings.SERIAL_CHECK == "all":
        reasons.append("no serial read")
    return reasons


def _valid_runs(card: dict) -> str:
    # Inserts and autos have their own ladders; steering them toward base runs turned a
    # correct /275 into /250.
    if is_insert_or_auto(card):
        return ""
    _, product = match_product(card)
    if not product:
        return ""
    runs = sorted({p["run"] for p in product["parallels"] if p.get("run")}, reverse=True)
    if not runs:
        return ""
    return ("This card is from {} {}. Its numbered parallels are /{}. The number after the "
            "slash is usually one of these, but report exactly what is printed even if it is "
            "not.").format(
        product.get("year", ""), (product.get("match") or [""])[0],
        ", /".join(str(r) for r in runs))


def read_serial(front, back=None, card=None, model: str = ""):
    """{"serial_number", "where", "confidence"} from a zoomed-in look at the card."""
    card = card or {}
    tiles = front.grid_tiles(grid=GRID, share=SHARE, scale=SCALE)
    images = [vision._image(front.vision_data_url())] + [vision._image(t) for t in tiles]
    if back is not None:
        images.append(vision._image(back.vision_data_url()))

    runs = _valid_runs(card)
    found = _ask(PROMPT.format(runs=runs, tiles=len(tiles)), images, model)

    # Locating the stamp is reliable; reading sideways foil digits is not. Read the
    # tile it is in again, upright in one of four turns.
    try:
        tile = int(found.get("tile") or 0)
    except (TypeError, ValueError):
        tile = 0
    if 1 <= tile <= len(tiles) and hasattr(front, "tile_rotations"):
        turns = front.tile_rotations(tile - 1, grid=GRID, share=SHARE, scale=SCALE + 1)
        reread = _ask(READ_PROMPT.format(first=found.get("serial_number") or "", runs=runs),
                      [vision._image(t) for t in turns], _read_model(model))
        if re.fullmatch(r"\d+/\d+", re.sub(r"\s+", "", str(reread.get("serial_number") or ""))):
            found = dict(found, first_read=found.get("serial_number"),
                         serial_number=reread["serial_number"],
                         confidence=reread.get("confidence", found.get("confidence")))
    return found


def _read_model(model: str) -> str:
    """SERIAL_READ_MODEL, unless it belongs to the other provider than the card's model."""
    read = settings.SERIAL_READ_MODEL or model
    model = model or settings.VISION_MODEL
    if read.startswith("gemini") != model.startswith("gemini"):
        return model
    return read


def _ask(text: str, images: list, model: str = "") -> dict:
    return try_parse_json(vision.complete([vision._text(text)] + images, model)) or {}


def recheck_serial(card: dict, front, back=None, model: str = "") -> dict:
    """Return the card with its serial re-read when the first read is doubtful.

    The second read replaces the first only when it found a serial; a "none found"
    second opinion does not erase a serial the first pass saw. The checklist pass runs
    again so a corrected run can name or flag the parallel.
    """
    reasons = reasons_to_check(card)
    if not reasons or not hasattr(front, "grid_tiles"):
        return card

    result = read_serial(front, back, card, model=model)
    serial = re.sub(r"\s+", "", str(result.get("serial_number") or ""))
    card = dict(card, serial_check={"reasons": reasons, "result": result})
    if not re.fullmatch(r"\d+/\d+", serial):
        return card

    card["serial_number_first_read"] = card.get("serial_number", "")
    card["serial_number"] = serial
    card["print_run"] = ""
    card.pop("catalog_warnings", None)
    if card.get("parallel_source"):
        # A parallel the first serial implied is no longer proven.
        card["parallel"] = ""
        card.pop("parallel_source", None)
    return apply_catalog(card)
