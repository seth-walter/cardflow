"""Score generated titles against the titles you corrected by hand.

data/title_eval_set.json pairs published scans with their live eBay titles, which are
the ground truth: every edit you made there is something the identifier got wrong.

    python tools/eval_titles.py                          # VISION_MODEL from settings
    python tools/eval_titles.py --models gpt-5.4 gpt-5.5 --limit 10

Costs one vision call per card per model. Nothing is written to eBay.
"""

import argparse
import json
import re
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config import settings
from core.scanned_image import ScannedImage
from services.onepiece_lookup import enrich_one_piece_card_data
from services.openai_service import build_seo_title, generate_card_data
from services.parallel_catalog import apply_catalog
from services.serial_check import recheck_serial
from services.pokemon_lookup import enrich_pokemon_card_data
from services.sports_lookup import enrich_sports_card_data
from utils.json_utils import try_parse_json

EVAL_PATH = Path(__file__).resolve().parents[1] / "data" / "title_eval_set.json"


def title_tokens(title):
    """Comparable words. "2025-26" counts as its first year; dots and # are ignored."""
    text = re.sub(r"\b(\d{4})-\d{2}\b", r"\1", str(title).lower())
    text = text.replace("#", " ").replace(".", " ")
    return set(re.findall(r"[a-z0-9]+(?:[-/][a-z0-9]+)*|/\d+", text))


def score(generated, live):
    got, want = title_tokens(generated), title_tokens(live)
    if not got or not want:
        return 0.0, sorted(want), sorted(got)
    hit = len(got & want)
    precision, recall = hit / len(got), hit / len(want)
    f1 = 2 * precision * recall / (precision + recall) if hit else 0.0
    return f1, sorted(want - got), sorted(got - want)


def identify(row, model):
    front = ScannedImage(row["front"])
    back = ScannedImage(row["back"]) if row.get("back") and Path(row["back"]).exists() else None
    raw = generate_card_data(front.vision_data_url(), back.vision_data_url() if back else "",
                             model=model, front_tiles=front.vision_tiles(),
                             reference_exclude=row.get("sku", ""))
    card = try_parse_json(raw) or {}
    card = enrich_pokemon_card_data(card)
    card = enrich_one_piece_card_data(card, front.vision_data_url())
    card = enrich_sports_card_data(card)
    card = apply_catalog(card)
    card = recheck_serial(card, front, back, model=model)
    return build_seo_title(card, {}), card


def run(model, rows, workers):
    def one(row):
        try:
            title, card = identify(row, model)
        except Exception as error:
            title, card = "ERROR " + str(error)[:60], {}
        return row, title, card

    with ThreadPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(one, rows))

    total, exact = 0.0, 0
    print("\n=== {} ===".format(model))
    for row, title, card in results:
        f1, missing, extra = score(title, row["live"])
        total += f1
        exact += f1 == 1.0
        mark = "OK " if f1 == 1.0 else "{:.2f}".format(f1)
        print("{}  {}".format(mark, title))
        if card.get("serial_check"):
            check = card["serial_check"]
            print("      serial check ({}): first {!r} -> {!r}".format(
                ", ".join(check["reasons"]), card.get("serial_number_first_read", card.get("serial_number")),
                check["result"].get("serial_number")))
        if f1 < 1.0:
            print("      want {}".format(row["live"]))
            print("      missing {}  extra {}".format(missing, extra))
    print("--- {}: exact {}/{}  mean token F1 {:.3f}".format(
        model, exact, len(results), total / max(1, len(results))))
    return exact, total / max(1, len(results))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--models", nargs="*", default=[settings.VISION_MODEL])
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--workers", type=int, default=6)
    parser.add_argument("--only", default="", help="substring filter on the live title")
    args = parser.parse_args()

    rows = json.loads(EVAL_PATH.read_text(encoding="utf-8"))
    rows = [r for r in rows if Path(r["front"]).exists()]
    if args.only:
        rows = [r for r in rows if args.only.lower() in r["live"].lower()]
    if args.limit:
        rows = rows[:args.limit]

    summary = [(model,) + run(model, rows, args.workers) for model in args.models]
    print("\n" + "\n".join("{:<24} exact {:>2}/{}  F1 {:.3f}".format(m, e, len(rows), f)
                           for m, e, f in summary))
    from services.gemini_service import usage
    if usage["calls"]:
        print("Gemini: {calls} calls, {input_tokens} input / {output_tokens} output tokens "
              "(per card: {0:.0f} in / {1:.0f} out)".format(
                  usage["input_tokens"] / len(rows), usage["output_tokens"] / len(rows), **usage))


if __name__ == "__main__":
    sys.exit(main())
