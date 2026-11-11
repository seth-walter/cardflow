"""Watches the scanner inbox folder and reports images once they finish writing.

PaperStream Capture (fi-7160) creates a file, holds it open, then streams bytes into
it. Picking a file up too early yields a truncated JPEG, so a file is only reported
once its size has held steady across two polls, it can be opened for reading, and -
for JPEGs - it ends with the end-of-image marker.
"""

import threading
import time
from pathlib import Path
from typing import Callable, List, Optional, Set

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".tif", ".tiff", ".bmp"}
JPEG_EXTENSIONS = {".jpg", ".jpeg"}
JPEG_END_MARKER = b"\xff\xd9"


class FolderWatcher:
    def __init__(
        self,
        folder,
        on_new_files: Callable[[List[Path]], None],
        poll_seconds: float = 1.0,
        extensions: Optional[Set[str]] = None,
    ):
        self.folder = Path(folder)
        self.poll_seconds = poll_seconds
        self.extensions = extensions or IMAGE_EXTENSIONS
        self.last_error = ""

        self._on_new_files = on_new_files
        self._seen = set()
        self._sizes = {}
        self._thread = None
        self._stop_event = threading.Event()

    # ------------------------------------------------------------------ status

    @property
    def is_running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def list_images(self) -> List[Path]:
        if not self.folder.is_dir():
            return []

        found = []
        for path in self.folder.iterdir():
            if not path.is_file():
                continue
            if path.suffix.lower() not in self.extensions:
                continue
            if path.name.startswith((".", "~")):
                continue
            found.append(path)
        return found

    def unseen_count(self) -> int:
        return len([p for p in self.list_images() if str(p) not in self._seen])

    def mark_existing_as_seen(self) -> int:
        existing = self.list_images()
        for path in existing:
            self._seen.add(str(path))
        return len(existing)

    # ----------------------------------------------------------------- control

    def start(self) -> None:
        if self.is_running:
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, name="scan-watcher", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()

    # ------------------------------------------------------------------ polling

    def _run(self) -> None:
        while not self._stop_event.is_set():
            try:
                ready = self.poll_once()
                if ready:
                    self._on_new_files(ready)
                self.last_error = ""
            except Exception as exc:  # keep the watcher alive through transient IO errors
                self.last_error = str(exc)
            self._stop_event.wait(self.poll_seconds)

    def poll_once(self) -> List[Path]:
        ready = []

        for path in self.list_images():
            key = str(path)
            if key in self._seen:
                continue

            try:
                size = path.stat().st_size
            except OSError:
                continue

            previous_size = self._sizes.get(key)
            self._sizes[key] = size

            # Needs at least one poll at an unchanged, non-zero size.
            if size == 0 or previous_size != size:
                continue

            if not self._is_complete(path):
                continue

            self._seen.add(key)
            self._sizes.pop(key, None)
            ready.append(path)

        ready.sort(key=self._sort_key)
        return ready

    @staticmethod
    def _sort_key(path: Path):
        try:
            return (path.stat().st_mtime, path.name)
        except OSError:
            return (time.time(), path.name)

    @staticmethod
    def _is_complete(path: Path) -> bool:
        """Readable, unlocked, and (for JPEG) terminated with its end marker."""
        try:
            with open(path, "rb") as handle:
                handle.seek(0, 2)
                size = handle.tell()
                if size < 4:
                    return False
                if path.suffix.lower() in JPEG_EXTENSIONS:
                    handle.seek(-2, 2)
                    return handle.read(2) == JPEG_END_MARKER
                handle.seek(0)
                handle.read(1)
                return True
        except OSError:
            # Still locked by the scanning software.
            return False
