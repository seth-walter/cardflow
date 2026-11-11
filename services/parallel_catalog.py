"""Parallel checklists: which parallels a product has and how many of each were printed.

data/parallels.json holds the checklists (names and print runs from Checklist Insider,
copied from Topps sell sheets). A serial number is the most reliable thing on a card,
so it is used to check the parallel the identifier named:

- a print run only one parallel has fills in the parallel (/399 in Topps Chrome
  Basketball can only be Magenta);
- a parallel whose checklist run disagrees with the serial is flagged for a look.

Visual descriptions in the file are not used: none of the sources describe looks, and
the learned examples in parallel_refs do that job far better.
"""

import json
import re
from pathlib import Path

CATALOG_PATH = Path(__file__).resolve().parents[1] / "data" / "parallels.json"
SPORTS = ("basketball", "football", "baseball", "hockey", "soccer")
# Words on a card (sport, league, set) that say which sport a product belongs to.
SPORT_SIGNS = {
    "basketball": r"basketball|\bnba\b|wnba|g league",
    "football": r"football|\bnfl\b",
    "baseball": r"baseball|\bmlb\b",
    "hockey": r"hockey|\bnhl\b",
    "soccer": r"soccer|uefa|premier league|\bmls\b|fifa",
    "racing": r"racing|formula|\bf1\b|nascar|indycar",
    "mma": r"\bmma\b|\bufc\b|mixed martial",
    "wrestling": r"wrestling|\bwwe\b|\baew\b",
}

_catalog = None


def catalog():
    global _catalog
    if _catalog is None:
        try:
            data = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        _catalog = {key: value for key, value in data.items()
                    if not key.startswith("_") and value.get("parallels")}
    return _catalog


def _text(value) -> str:
    return " ".join(str(value or "").split())


def name_key(name: str) -> frozenset:
    """Order-free words of a parallel name. "RayWave Blue Refractor" == "Blue Ray Wave"."""
    low = _text(name).lower()
    low = low.replace("raywave", "ray wave").replace("x-fractor", "x-refractor")
    low = re.sub(r"\(.*?\)", " ", low)
    words = set(re.findall(r"[a-z0-9]+(?:-[a-z0-9]+)*", low))
    return frozenset(words - {"refractor", "refractors", "fractor", "parallel"})


def match_product(card: dict, ignore_year: bool = False):
    """(key, product) for the card's product, or (None, None)."""
    card_set = _text(card.get("set")).lower()
    if not card_set:
        return None, None
    year = "" if ignore_year else _text(card.get("year") or card.get("season"))[:4]
    sport = _text(card.get("sport")).lower()
    haystack = " ".join([card_set, sport, _text(card.get("league")).lower()])
    card_sports = {s for s, sign in SPORT_SIGNS.items() if re.search(sign, haystack)}

    # "Topps Chrome" with league "UFC" is the Topps Chrome UFC product.
    named = card_set + " " + _text(card.get("league")).lower()

    best, best_score = None, 0
    for key, product in catalog().items():
        phrase = max((len(p) for p in product.get("match", [])
                      if p.lower() in card_set or p.lower() in named), default=0)
        if not phrase:
            continue
        score = phrase
        product_year = _text(product.get("year"))[:4]
        if year and product_year:
            score += 50 if year == product_year else -100
        key_sport = _text(product.get("sport")).lower() or next(
            (s for s in SPORTS if s in key), "")
        if "formula" in key or "_f1_" in key:
            key_sport = "racing"
        if key_sport in SPORT_SIGNS:
            if re.search(SPORT_SIGNS[key_sport], haystack):
                score += 30
            elif card_sports and key_sport not in card_sports:
                continue   # a basketball card is never a baseball product
            else:
                score -= 60
        if "disney" in key:
            score += 0 if "disney" in haystack else -60
        if score > best_score:
            best, best_score = key, score
    return (best, catalog()[best]) if best else (None, None)


def _run(card: dict) -> int:
    for field in ("serial_number", "print_run"):
        match = re.search(r"\d*\s*/\s*(\d+)", _text(card.get(field)))
        if match:
            return int(match.group(1))
    return 0


def candidates_for_run(product: dict, run: int):
    """Parallel names printed to this run. A numbered base ("Base" /99 in Motif) counts."""
    names = []
    for p in product["parallels"]:
        if run in _runs(p) and p["name"] not in names:
            names.append(p["name"])
    return names


def _runs(parallel: dict) -> set:
    """Every run this parallel was printed to. Finest-style tiered parallels carry the
    other tiers' runs in alt_runs."""
    return {parallel.get("run")} | set(parallel.get("alt_runs") or [])


def find_parallels(product: dict, name: str):
    """Every checklist entry with this name. Finest lists one per rarity tier, each with
    its own run (Gold /50, /25, /20)."""
    key = name_key(name)
    if not key:
        return []
    return [p for p in product["parallels"] if name_key(p["name"]) == key]


def is_insert_or_auto(card: dict) -> bool:
    autographed = _text(card.get("autographed")).lower() in {"yes", "true"}
    return autographed or bool(_text(card.get("insert_set")))


def apply_catalog(card: dict) -> dict:
    """Fill a parallel the serial proves, and note checklist disagreements.

    Disagreements go into card["catalog_warnings"]; nothing the identifier read is
    overwritten except an empty or plain "Refractor" parallel on a numbered card, which
    cannot be right (Topps Chrome's plain Refractor is unnumbered).
    """
    card = dict(card)
    key, product = match_product(card)
    if not product:
        # A season product is often read by its second year ("2026" for 2025-26 Topps
        # Chrome). Only that reading is corrected; any other year is left as read.
        key, product = match_product(card, ignore_year=True)
        read = _text(card.get("year"))[:4]
        if not product or "-" not in product["year"] or not read.isdigit() \
                or int(read) != int(product["year"][:4]) + 1:
            return card
        card["year_read"] = card.get("year", "")
        card["year"] = product["year"]
    if is_insert_or_auto(card):
        # The checklists hold base-card ladders. Inserts and autographs have their own
        # runs (Rookie Autographs /275 is not a base parallel), so checking them against
        # the base ladder only manufactures false alarms.
        return card
    run = _run(card)
    warnings = []
    parallel = _text(card.get("parallel"))
    label = "{} {}".format(product.get("year", ""), key.replace("_", " ")).strip()

    if run:
        options = candidates_for_run(product, run)
        listed = find_parallels(product, parallel) if parallel else []
        runs = set().union(*[_runs(p) for p in listed]) if listed else set()
        generic = name_key(parallel) <= {"base"} or not parallel
        if generic and options == ["Base"]:
            pass   # numbered base card: the run is right and there is no parallel
        elif parallel.lower() in {o.lower() for o in options}:
            pass   # e.g. Bowman Chrome's numbered plain "Refractor" /499
        elif generic and len(options) == 1:
            card["parallel"] = options[0]
            card["parallel_source"] = "print run /{} is only {}".format(run, options[0])
        elif not options:
            warnings.append("no parallel in the {} checklist is /{} - check the serial".format(
                product.get("match", [key])[0], run))
        elif generic:
            warnings.append("/{} could be: {}".format(run, ", ".join(options)))
        elif not listed:
            warnings.append("{} is not in the checklist; /{} is {}".format(
                parallel, run, ", ".join(options)))
        elif run in runs:
            pass
        elif runs == {None}:
            warnings.append("{} is unnumbered in the checklist but a serial /{} was read; "
                            "/{} is {}".format(listed[0]["name"], run, run, ", ".join(options)))
        else:
            warnings.append("{} is /{} in the checklist but the serial reads /{}".format(
                listed[0]["name"], "/".join(str(r) for r in sorted(runs - {None})), run))
    elif parallel:
        listed = find_parallels(product, parallel)
        if listed and all(p.get("run") for p in listed):
            warnings.append("{} should be numbered /{} - no serial was read".format(
                listed[0]["name"], "/".join(str(p["run"]) for p in listed)))

    if warnings:
        card["catalog_warnings"] = warnings
        card["catalog_product"] = label
    return card
