"""Rotation tests: the rotate buttons turn the photo the way the preview shows.

Uses a marker block in one corner to prove the pixels actually moved the right way,
not just that the dimensions changed. No API calls.

Rotation is manual by design: vision models judged card orientation too unreliably
(1/6 to 4/6 correct on real scans) to be trusted with listing photos.

Run: python tools/test_orientation.py
"""

import base64
import io
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PIL import Image

from core.scanned_image import ScannedImage

failures = []
WHITE = (250, 250, 250)
MARK = (220, 20, 20)


def check(label, condition, detail=""):
    print(("  PASS  " if condition else "  FAIL  ") + label + (" -> " + str(detail) if detail else ""))
    if not condition:
        failures.append(label)


def make_card(path, width, height, mark_corner="topleft"):
    img = Image.new("RGB", (width, height), WHITE)
    block_w, block_h = width // 5, height // 5
    box = {
        "topleft": (0, 0, block_w, block_h),
        "topright": (width - block_w, 0, width, block_h),
    }[mark_corner]
    img.paste(Image.new("RGB", (box[2] - box[0], box[3] - box[1]), MARK), box[:2])
    img.save(path, format="JPEG", quality=90)
    return path


def corner_colors(img):
    """(topleft, topright, bottomleft, bottomright), sampled inside each corner."""
    w, h = img.size
    dx, dy = w // 10, h // 10
    return (
        img.getpixel((dx, dy)),
        img.getpixel((w - dx, dy)),
        img.getpixel((dx, h - dy)),
        img.getpixel((w - dx, h - dy)),
    )


def is_mark(pixel):
    return pixel[0] > 150 and pixel[1] < 110 and pixel[2] < 110


def which_corner(img):
    names = ["topleft", "topright", "bottomleft", "bottomright"]
    marks = [n for n, c in zip(names, corner_colors(img)) if is_mark(c)]
    return marks[0] if len(marks) == 1 else marks


def decode_data_url(url):
    return Image.open(io.BytesIO(base64.b64decode(url.split(",", 1)[1])))


def main():
    workdir = Path(tempfile.mkdtemp(prefix="orient_"))

    print("\n1. No rotation requested: bytes pass through untouched")
    portrait = make_card(workdir / "portrait.jpg", 600, 840, "topleft")
    img = ScannedImage(portrait)
    name, data, mime = img.upload_payload()
    check("original bytes reused, no re-encode", data == portrait.read_bytes())
    check("original filename kept", name == "portrait.jpg", name)
    check("vision sees the scan as-is", which_corner(decode_data_url(img.vision_data_url())) == "topleft")

    print("\n2. Landscape scan is left alone unless rotation is asked for")
    landscape = make_card(workdir / "landscape.jpg", 840, 600, "topleft")
    wide = ScannedImage(landscape)
    name2, data2, _ = wide.upload_payload()
    check("wide scan not auto-rotated (landscape designs stay put)",
          data2 == landscape.read_bytes())
    vision2 = decode_data_url(wide.vision_data_url())
    check("vision keeps the original shape", vision2.width > vision2.height, vision2.size)

    print("\n3. 90 degrees clockwise moves top-left to top-right")
    _, data3, mime3 = wide.upload_payload(90)
    rotated = Image.open(io.BytesIO(data3))
    check("now portrait", rotated.height > rotated.width, rotated.size)
    check("mark at top-right", which_corner(rotated) == "topright", which_corner(rotated))
    check("full resolution kept", max(rotated.size) == 840, rotated.size)
    check("re-encoded as jpeg", mime3 == "image/jpeg")

    print("\n4. 270 degrees clockwise moves top-left to bottom-left")
    _, data4, _ = wide.upload_payload(270)
    check("mark at bottom-left", which_corner(Image.open(io.BytesIO(data4))) == "bottomleft",
          which_corner(Image.open(io.BytesIO(data4))))

    print("\n5. 180 degrees flips corner to corner")
    _, data5, _ = ScannedImage(portrait).upload_payload(180)
    flipped = Image.open(io.BytesIO(data5))
    check("mark at bottom-right", which_corner(flipped) == "bottomright", which_corner(flipped))
    check("still portrait", flipped.height > flipped.width, flipped.size)

    print("\n6. Thumbnails follow the same rotation, cached per angle")
    thumbs = workdir / "thumbs"
    straight = Image.open(ScannedImage(landscape).write_thumbnail(thumbs))
    turned = Image.open(ScannedImage(landscape).write_thumbnail(thumbs, rotation=90))
    check("unrotated preview stays landscape", straight.width > straight.height, straight.size)
    check("rotated preview is portrait", turned.height > turned.width, turned.size)
    check("both cached separately", len(list(thumbs.glob("*.jpg"))) == 2,
          [p.name for p in thumbs.glob("*.jpg")])

    print("\n7. Rotate buttons accumulate and wrap, per side")
    import core.scan_session as session_module
    session_module.process_scanned_card = lambda f, b=None: {}
    session = session_module.ScanSession()
    session.thumbnail_dir = workdir / "session_thumbs"
    card = {
        "id": "card0001", "index": 0, "status": "draft",
        "front_path": str(landscape), "back_path": str(portrait),
        "front_rotation": 0, "back_rotation": 0,
        "thumb_path": "", "back_thumb_path": "",
    }
    session.cards.append(card)
    session._cards_by_id["card0001"] = card

    check("one right turn", session.rotate_card_image("card0001", "front", 90) == 90)
    check("two right turns", session.rotate_card_image("card0001", "front", 90) == 180)
    check("left turn from 180", session.rotate_card_image("card0001", "front", -90) == 90)
    check("wraps past 0", session.rotate_card_image("card0001", "front", -90) == 0)
    check("left turn wraps to 270", session.rotate_card_image("card0001", "front", -90) == 270)
    check("back rotates independently", session.rotate_card_image("card0001", "back", 90) == 90)
    check("front unchanged by back turn", card["front_rotation"] == 270, card["front_rotation"])

    preview = Image.open(card["thumb_path"])
    check("front preview matches its rotation",
          which_corner(preview) == "bottomleft", which_corner(preview))
    check("back preview written separately",
          card["back_thumb_path"] and card["back_thumb_path"] != card["thumb_path"])
    check("missing side is a no-op", session.rotate_card_image("card0001", "nope", 90) == 0)
    check("unknown card is a no-op", session.rotate_card_image("ghost", "front", 90) == 0)

    shutil.rmtree(workdir, ignore_errors=True)
    print("\n" + ("ALL CHECKS PASSED" if not failures else "FAILED: " + ", ".join(failures)))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
