"""Labelled example scans of parallels, shown to the identifier beside every card.

Described in words, a Ray Wave, an X-Refractor and a plain Refractor all sound alike,
and the model kept calling them "Refractor". Side by side with a real example of each,
the difference is obvious: sweeping arcs, a checkerboard, a flat sheen.

The library learns from you. When a card publishes, the parallel named in its final
title (the one you corrected) is matched against PARALLEL_VOCAB and the scan is kept as
an example of that parallel. New products teach themselves the first time you list one.

Files live in data/parallel_refs/: one small JPEG per example plus index.json.
"""

import base64
import io
import json
import re
import threading
import time
from pathlib import Path

from PIL import Image, ImageOps

REF_DIR = Path(__file__).resolve().parents[1] / "data" / "parallel_refs"
INDEX_PATH = REF_DIR / "index.json"

# Whole-card thumbnails: big enough to show a border pattern, small enough that low
# detail (one flat-rate tile per image) covers it.
REF_MAX_EDGE = 512
REF_QUALITY = 85
PER_LABEL = 2
MAX_REFERENCES = 30

# Parallel names as they appear in titles. Longest match wins, so "Blue Wave" beats
# "Blue" and "Ray Wave Refractor" beats "Refractor". Labels already in the library are
# matched too, so anything you have listed once is recognised from then on.
PARALLEL_VOCAB = [
    "Refractor", "X-Refractor", "Ray Wave Refractor", "RayWave Refractor", "Ray Wave",
    "Negative Refractor", "Prism Refractor", "Speckle Refractor", "Mojo Refractor",
    "Sonar Refractor", "Lava Refractor", "Pulsar Refractor", "Mini Diamond Refractor",
    "Geometric Refractor", "Shimmer Refractor", "Lazer Refractor", "Checker Flag Refractor",
    "Logofractor", "Superfractor", "Wave Refractor",
    "Magenta", "Magenta Refractor", "Pink", "Pink Refractor", "Purple", "Purple Refractor",
    "Aqua", "Aqua Refractor", "Teal", "Teal Refractor", "Blue", "Blue Refractor",
    "Blue Wave", "Blue Wave Refractor", "Green", "Green Refractor", "Green Wave",
    "Green Wave Refractor", "Yellow", "Yellow Refractor", "Gold", "Gold Refractor",
    "Gold Wave", "Gold Wave Refractor", "Orange", "Orange Refractor", "Orange Wave",
    "Orange Wave Refractor", "Black", "Black Refractor", "Black Wave", "Red",
    "Red Refractor", "Red Wave",
    "Vintage Stock", "Golden Mirror", "Rainbow Foil", "Foilboard", "Independence Day",
    "Clear", "Black and White", "Mother's Day Pink", "Father's Day Blue", "Platinum",
    "Sapphire", "Holo", "Silver", "Silver Prizm", "Lava",
]

COLOUR_WORDS = {"magenta", "pink", "purple", "aqua", "teal", "blue", "green", "yellow",
                "gold", "orange", "black", "red", "silver", "platinum", "sapphire", "clear"}

_lock = threading.Lock()
_cache = {}


def _load_index():
    try:
        return json.loads(INDEX_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []


def _save_index(rows):
    REF_DIR.mkdir(parents=True, exist_ok=True)
    INDEX_PATH.write_text(json.dumps(rows, indent=1, ensure_ascii=False), encoding="utf-8")


def labels():
    return sorted({row["label"] for row in _load_index()})


def parallel_in_title(title: str, extra=()):
    """The parallel named in a title, or "". Longest known name wins."""
    text = " " + re.sub(r"\s+", " ", str(title or "")) + " "
    best = ""
    for name in list(PARALLEL_VOCAB) + labels() + [e for e in extra if e]:
        pattern = r"(?<![\w-])" + re.escape(name) + r"(?![\w-])"
        if " " not in name and name.lower() in COLOUR_WORDS:
            # A bare colour is a parallel only beside its print run ("Black 3/10");
            # otherwise it is "Red Wings", "Golden State" or a surname.
            pattern += r"\s+\d*/\d+"
        if len(name) > len(best) and re.search(pattern, text, re.IGNORECASE):
            best = name
    return best


def _thumbnail_bytes(path, rotation=0) -> bytes:
    with Image.open(path) as img:
        img = ImageOps.exif_transpose(img).convert("RGB")
    if rotation % 360:
        img = img.rotate(-rotation, expand=True)
    img.thumbnail((REF_MAX_EDGE, REF_MAX_EDGE), Image.LANCZOS)
    buffer = io.BytesIO()
    img.save(buffer, format="JPEG", quality=REF_QUALITY, optimize=True)
    return buffer.getvalue()


def add_reference(label: str, image_path, rotation: int = 0, source: str = "") -> bool:
    """Keep this scan as an example of `label`. The newest PER_LABEL examples are kept."""
    label = " ".join(str(label or "").split())
    if not label or not image_path or not Path(image_path).exists():
        return False

    data = _thumbnail_bytes(image_path, rotation)
    with _lock:
        rows = _load_index()
        if source and any(r["label"] == label and r.get("source") == source for r in rows):
            return False

        REF_DIR.mkdir(parents=True, exist_ok=True)
        slug = re.sub(r"[^a-z0-9]+", "-", label.lower()).strip("-")
        filename = "{}_{}.jpg".format(slug, int(time.time() * 1000))
        (REF_DIR / filename).write_bytes(data)
        rows.append({"label": label, "file": filename, "source": source,
                     "added": time.strftime("%Y-%m-%d")})

        same = [r for r in rows if r["label"] == label]
        for old in same[:-PER_LABEL]:
            rows.remove(old)
            try:
                (REF_DIR / old["file"]).unlink()
            except OSError:
                pass

        _save_index(rows)
        _cache.clear()
    return True


def learn_from_published(title: str, card_data: dict, front_path, rotation=0, source=""):
    """Record the parallel in a published title. Returns the label learned, or ""."""
    label = parallel_in_title(title, extra=[(card_data or {}).get("parallel", "")])
    if label and add_reference(label, front_path, rotation, source):
        return label
    return ""


def reference_images(exclude_source: str = ""):
    """[(label, data_url)] for the identifier, newest first, capped at MAX_REFERENCES.

    exclude_source leaves out examples taken from one card, so the eval cannot score a
    card against a reference of itself.
    """
    rows = _load_index()
    signature = (exclude_source, len(rows), rows[-1]["file"] if rows else "")
    with _lock:
        if signature in _cache:
            return _cache[signature]

    picked = []
    for row in reversed(rows):
        if exclude_source and row.get("source") == exclude_source:
            continue
        path = REF_DIR / row["file"]
        if not path.exists():
            continue
        encoded = base64.b64encode(path.read_bytes()).decode("utf-8")
        picked.append((row["label"], "data:image/jpeg;base64," + encoded))
        if len(picked) >= MAX_REFERENCES:
            break

    picked.sort(key=lambda pair: pair[0])
    with _lock:
        _cache[signature] = picked
    return picked
