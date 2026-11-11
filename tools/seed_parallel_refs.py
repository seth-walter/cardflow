"""Seed the parallel example library from cards you have already published.

Reads data/title_eval_set.json (scan paths paired with your corrected live titles) and
keeps each scan whose title names a parallel. After this, publishing teaches it more.

    python tools/seed_parallel_refs.py
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.parallel_refs import labels, learn_from_published

EVAL_PATH = Path(__file__).resolve().parents[1] / "data" / "title_eval_set.json"


def main():
    rows = json.loads(EVAL_PATH.read_text(encoding="utf-8"))
    for row in rows:
        if not Path(row["front"]).exists():
            print("missing scan  " + row["front"])
            continue
        label = learn_from_published(row["live"], {}, row["front"], 0, source=row.get("sku", ""))
        if label:
            print("{:<22} {}".format(label, row["live"]))
    print("\nlibrary: " + ", ".join(labels()))


if __name__ == "__main__":
    main()
