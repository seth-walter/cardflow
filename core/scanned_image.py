"""Adapter that makes a scanned file on disk behave like a Streamlit UploadedFile.

The fi-7160 writes full-resolution scans (often 2-4 MB each). Those bytes are read
from disk on demand and never cached in memory, so a 150 card session stays small.
Vision calls and previews use downscaled copies instead of the originals.

Orientation: aspect ratio cannot decide this. A card is physically 2.5x3.5, but plenty
of cards carry landscape artwork, so a wide scan may already read correctly while a tall
one may be sideways. The identifier looks at the artwork and reports the clockwise
rotation each image needs; that value is passed to the methods below as `rotation`.
"""

import base64
import io
import mimetypes
import re
from pathlib import Path

from PIL import Image, ImageOps

VISION_MAX_EDGE = 1400
VISION_QUALITY = 82
# Close-ups for the identifier. Serial numbers are tiny foil digits, often sideways over
# busy art; at whole-card scale the model misreads them (112/399 came back as 17/99).
# Overlapping quadrants enlarged 2x read them correctly.
TILE_SHARE = 0.55
TILE_SCALE = 2
TILE_MAX_EDGE = 1600
TILE_QUALITY = 88
THUMBNAIL_MAX_EDGE = 320
THUMBNAIL_QUALITY = 75
UPLOAD_QUALITY = 92

EXIF_ORIENTATION_TAG = 274
EXIF_SWAPPED_ORIENTATIONS = {5, 6, 7, 8}


class ScannedImage:
    """Wraps a scanned image path with the small surface the pipeline expects."""

    def __init__(self, path):
        self.path = Path(path)
        self.name = self.path.name
        guessed, _ = mimetypes.guess_type(self.name)
        self.type = guessed or "image/jpeg"

    def getvalue(self) -> bytes:
        """Full-resolution bytes exactly as scanned."""
        return self.path.read_bytes()

    # -------------------------------------------------------------- orientation

    def source_size(self):
        """(width, height) after EXIF orientation, without decoding the pixels."""
        with Image.open(self.path) as img:
            width, height = img.size
            try:
                orientation = img.getexif().get(EXIF_ORIENTATION_TAG, 1)
            except Exception:
                orientation = 1
        if orientation in EXIF_SWAPPED_ORIENTATIONS:
            width, height = height, width
        return width, height

    @property
    def is_sideways(self) -> bool:
        """True when the scan is landscape, i.e. the card fed through on its side."""
        width, height = self.source_size()
        return width > height

    def _load(self) -> Image.Image:
        with Image.open(self.path) as img:
            return ImageOps.exif_transpose(img).convert("RGB")

    def _oriented(self, rotation: int = 0) -> Image.Image:
        """The image turned `rotation` degrees clockwise."""
        img = self._load()

        if rotation % 360:
            img = img.rotate(-rotation, expand=True)

        return img

    def _encode_jpeg(self, img: Image.Image, max_edge, quality: int) -> bytes:
        if max_edge:
            img.thumbnail((max_edge, max_edge), Image.LANCZOS)
        buffer = io.BytesIO()
        img.save(buffer, format="JPEG", quality=quality, optimize=True)
        return buffer.getvalue()

    # ------------------------------------------------------------------ outputs

    def vision_data_url(self, rotation: int = 0) -> str:
        """Downscaled data URL for the OpenAI vision call, as scanned by default."""
        encoded = base64.b64encode(
            self._encode_jpeg(self._oriented(rotation), VISION_MAX_EDGE, VISION_QUALITY)
        )
        return "data:image/jpeg;base64," + encoded.decode("utf-8")

    def vision_tiles(self, rotation: int = 0):
        """Four overlapping enlarged quadrants (TL, TR, BL, BR) as data URLs."""
        return self.grid_tiles(rotation, grid=2, share=TILE_SHARE, scale=TILE_SCALE)

    def grid_tiles(self, rotation: int = 0, grid: int = 2, share: float = TILE_SHARE,
                   scale: int = TILE_SCALE):
        """grid x grid overlapping enlarged crops, row by row, as data URLs."""
        img = self._oriented(rotation)
        width, height = img.size
        span_w, span_h = int(width * share), int(height * share)
        urls = []

        for row in range(grid):
            for col in range(grid):
                left = (width - span_w) * col // max(1, grid - 1)
                top = (height - span_h) * row // max(1, grid - 1)
                tile = img.crop((left, top, left + span_w, top + span_h))
                tile = tile.resize((tile.width * scale, tile.height * scale), Image.LANCZOS)
                encoded = base64.b64encode(self._encode_jpeg(tile, TILE_MAX_EDGE, TILE_QUALITY))
                urls.append("data:image/jpeg;base64," + encoded.decode("utf-8"))

        return urls

    def tile_rotations(self, index: int, grid: int = 3, share: float = 0.42, scale: int = 3):
        """One grid tile (row by row, 0-based), contrast-boosted, in all four rotations.

        Serial stamps often run sideways; one of the four reads upright.
        """
        img = self._oriented()
        width, height = img.size
        span_w, span_h = int(width * share), int(height * share)
        row, col = divmod(index, grid)
        left = (width - span_w) * col // max(1, grid - 1)
        top = (height - span_h) * row // max(1, grid - 1)
        tile = img.crop((left, top, left + span_w, top + span_h))
        tile = ImageOps.autocontrast(tile, cutoff=1)
        tile = tile.resize((tile.width * scale, tile.height * scale), Image.LANCZOS)

        urls = []
        for degrees in (0, 90, 180, 270):
            turned = tile.rotate(-degrees, expand=True) if degrees else tile
            encoded = base64.b64encode(self._encode_jpeg(turned.copy(), TILE_MAX_EDGE, TILE_QUALITY))
            urls.append("data:image/jpeg;base64," + encoded.decode("utf-8"))
        return urls

    def write_thumbnail(self, thumbnail_dir, rotation: int = 0) -> str:
        """Write a small preview JPEG and return its path.

        Rotated previews are turned from the existing thumbnail rather than the original
        scan. Decoding a 320px JPEG instead of a full 1500px scan is what keeps the
        rotate buttons feeling instant.
        """
        thumbnail_dir = Path(thumbnail_dir)
        thumbnail_dir.mkdir(parents=True, exist_ok=True)

        stat = self.path.stat()
        safe_stem = re.sub(r"[^A-Za-z0-9_.-]", "_", self.path.stem)[:60]
        stamp = int(stat.st_mtime)
        rotation = rotation % 360

        base = thumbnail_dir / "{}_{}_0.jpg".format(safe_stem, stamp)
        if not base.exists():
            base.write_bytes(
                self._encode_jpeg(self._oriented(), THUMBNAIL_MAX_EDGE, THUMBNAIL_QUALITY)
            )

        if not rotation:
            return str(base)

        target = thumbnail_dir / "{}_{}_{}.jpg".format(safe_stem, stamp, rotation)
        if not target.exists():
            with Image.open(base) as preview:
                turned = preview.convert("RGB").rotate(-rotation, expand=True)
            buffer = io.BytesIO()
            turned.save(buffer, format="JPEG", quality=THUMBNAIL_QUALITY, optimize=True)
            target.write_bytes(buffer.getvalue())

        return str(target)

    def upload_payload(self, rotation: int = 0):
        """(filename, bytes, mime) for the listing photo, turned upright.

        Scans that need no rotation are uploaded byte-for-byte, so the common case never
        pays a re-encode.
        """
        if not rotation % 360:
            return self.name, self.getvalue(), self.type

        data = self._encode_jpeg(self._oriented(rotation), None, UPLOAD_QUALITY)
        return self.path.stem + ".jpg", data, "image/jpeg"
