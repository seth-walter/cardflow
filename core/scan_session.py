"""Live scanning session: folder watcher -> duplex pairing -> background identification.

Owned by a single cached Streamlit resource. Worker threads never touch
st.session_state; the UI reads immutable snapshots and watches `version` to know
when something changed.
"""

import json
import os
import tempfile
import threading
import time
import traceback
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Dict, List, Optional

from config import settings
from core.batch_processor import process_scanned_card
from core.scan_ledger import lookup as ledger_lookup, record as ledger_record
from core.scan_watcher import FolderWatcher
from core.scanned_image import ScannedImage

STATUS_PROCESSING = "processing"
STATUS_DRAFT = "draft"
STATUS_PUBLISHED = "published"
STATUS_ERROR = "error"


class ScanSession:
    """Holds every card scanned in the current session plus the intake machinery."""

    def __init__(self, persist_path: str = ""):
        self._lock = threading.RLock()
        # Where the card list is saved after every change, so a restart, crash or code
        # reload does not throw away cards that were already identified (their scans are
        # in the ledger by then, so a rescan would skip them). Blank keeps it in memory.
        self._persist_path = persist_path
        self._executor: Optional[ThreadPoolExecutor] = None
        self._watcher: Optional[FolderWatcher] = None

        self.cards: List[Dict[str, Any]] = []
        self._cards_by_id: Dict[str, Dict[str, Any]] = {}
        self.backlog: deque = deque()

        self.folder = settings.SCAN_INBOX_DIR
        self.max_cards = settings.MAX_CARDS_PER_SESSION
        self.workers = settings.SCAN_WORKERS

        self.thumbnail_dir = Path(tempfile.gettempdir()) / "ebay_card_lister_thumbs"
        self.messages: deque = deque(maxlen=60)
        self.active_jobs = 0
        self.paused_for_cap = False
        self.skipped_duplicates = 0
        self.version = 0
        self._restore()

    # ------------------------------------------------------------------ helpers

    def _bump(self) -> None:
        self.version += 1
        self._save()

    def _save(self) -> None:
        if not self._persist_path:
            return
        with self._lock:
            temp = self._persist_path + ".tmp"
            try:
                os.makedirs(os.path.dirname(self._persist_path) or ".", exist_ok=True)
                with open(temp, "w", encoding="utf-8") as handle:
                    json.dump({"cards": self.cards}, handle, default=str)
                os.replace(temp, self._persist_path)
            except (OSError, TypeError, ValueError):
                pass  # losing the save file must never stop scanning

    def _restore(self) -> None:
        if not self._persist_path:
            return
        try:
            with open(self._persist_path, "r", encoding="utf-8") as handle:
                cards = json.load(handle).get("cards") or []
        except (OSError, ValueError, AttributeError):
            return
        for card in cards:
            if card.get("status") == STATUS_PROCESSING:
                # Its worker died with the old process; Retry identifies it again.
                card["status"] = STATUS_ERROR
                card["error"] = "Interrupted by a restart before it was identified."
        self.cards = cards
        self._cards_by_id = {card["id"]: card for card in cards}
        if cards:
            self._log("Restored {} card(s) from the last session.".format(len(cards)))

    def _log(self, message: str) -> None:
        self.messages.appendleft("{}  {}".format(time.strftime("%H:%M:%S"), message))

    def _ensure_executor(self) -> ThreadPoolExecutor:
        if self._executor is None:
            self._executor = ThreadPoolExecutor(
                max_workers=self.workers, thread_name_prefix="card-identify"
            )
        return self._executor

    # ----------------------------------------------------------------- control

    @property
    def is_scanning(self) -> bool:
        return bool(self._watcher and self._watcher.is_running)

    def pending_folder_count(self) -> int:
        if not self._watcher:
            return 0
        try:
            return self._watcher.unseen_count()
        except Exception:
            return 0

    def start(self, folder: str, max_cards: int, workers: int, import_existing: bool) -> None:
        with self._lock:
            folder_path = Path(folder).expanduser()
            folder_path.mkdir(parents=True, exist_ok=True)

            self.folder = str(folder_path)
            self.max_cards = int(max_cards)

            if workers != self.workers:
                old_executor = self._executor
                self.workers = int(workers)
                self._executor = None
                if old_executor is not None:
                    # Let in-flight cards finish on the old pool.
                    old_executor.shutdown(wait=False)

            if self._watcher is not None:
                self._watcher.stop()

            watcher = FolderWatcher(
                folder_path, self._on_new_files, poll_seconds=settings.SCAN_POLL_SECONDS
            )

            skipped = 0
            if not import_existing:
                skipped = watcher.mark_existing_as_seen()

            watcher.start()
            self._watcher = watcher
            self._ensure_executor()

            self._log("Watching {}".format(folder_path))
            if skipped:
                self._log("Ignoring {} file(s) already in the folder".format(skipped))
            self._bump()

    def stop(self) -> None:
        with self._lock:
            if self._watcher is not None:
                self._watcher.stop()
                self._log("Stopped watching. In-flight cards will still finish.")
            self._bump()

    def reset(self) -> None:
        with self._lock:
            self.stop()
            self.cards = []
            self._cards_by_id = {}
            self.backlog.clear()
            self.paused_for_cap = False
            self.skipped_duplicates = 0
            self._watcher = None
            self._log("Session cleared.")
            self._bump()

    def set_max_cards(self, max_cards: int) -> None:
        with self._lock:
            self.max_cards = int(max_cards)
            self._drain_locked()
            self._bump()

    # ------------------------------------------------------------------- intake

    def _on_new_files(self, paths) -> None:
        with self._lock:
            for path in paths:
                self.backlog.append(Path(path))
            self._drain_locked()
            self._bump()

    def enqueue_paths(self, paths) -> None:
        """Intake path for manually added images (no scanner involved)."""
        self._on_new_files(paths)

    def _drain_locked(self) -> None:
        while len(self.backlog) >= 2:
            if len(self.cards) >= self.max_cards:
                break
            front = self.backlog.popleft()
            back = self.backlog.popleft()

            # A scan that already became a listing must not become a second one. The
            # watcher only remembers files while it lives, and Start builds a new one.
            previous = ledger_lookup(front)
            if previous:
                self.skipped_duplicates += 1
                self._log("Skipped {} - already listed as {}".format(
                    front.name, previous.get("title") or previous.get("sku") or "a card"))
                continue

            self._submit_locked(front, back)

        self.paused_for_cap = len(self.cards) >= self.max_cards and len(self.backlog) > 0
        if self.paused_for_cap:
            self._log(
                "Card cap of {} reached. {} image(s) held in the queue.".format(
                    self.max_cards, len(self.backlog)
                )
            )

    def flush_single_sided(self) -> bool:
        """Turn a leftover unpaired scan into a front-only card (dropped back sheet)."""
        with self._lock:
            if not self.backlog or len(self.cards) >= self.max_cards:
                return False
            front = self.backlog.popleft()
            self._submit_locked(front, None)
            self._log("Queued {} as a single-sided card.".format(front.name))
            self._bump()
            return True

    def drop_backlog(self) -> int:
        """Discard queued-but-unpaired images, e.g. after a misfeed."""
        with self._lock:
            dropped = len(self.backlog)
            self.backlog.clear()
            self.paused_for_cap = False
            if dropped:
                self._log("Dropped {} queued image(s).".format(dropped))
                self._bump()
            return dropped

    def _submit_locked(self, front_path: Path, back_path: Optional[Path]) -> None:
        index = len(self.cards)
        # Restored cards keep their ids, so count on from the highest rather than the length.
        number = max([int(c["id"][4:]) for c in self.cards if c.get("id", "")[4:].isdigit()] or [0])
        card = {
            "id": "card{:04d}".format(number + 1),
            "index": index,
            "status": STATUS_PROCESSING,
            "error": "",
            "traceback": "",
            "front_path": str(front_path),
            "back_path": str(back_path) if back_path else "",
            "front_name": front_path.name,
            "back_name": back_path.name if back_path else "",
            "thumb_path": "",
            "back_thumb_path": "",
            "front_rotation": 0,
            "back_rotation": 0,
            "scanned_at": time.time(),
            "title": "",
            "card_name": "",
            "sku": "",
            "offer_id": "",
        }

        self.cards.append(card)
        self._cards_by_id[card["id"]] = card
        self.active_jobs += 1
        self._save()

        self._ensure_executor().submit(
            self._process, card["id"], str(front_path), str(back_path) if back_path else ""
        )

    def retry(self, card_id: str) -> bool:
        with self._lock:
            card = self._cards_by_id.get(card_id)
            if not card or not card.get("front_path"):
                return False
            card["status"] = STATUS_PROCESSING
            card["error"] = ""
            card["traceback"] = ""
            self.active_jobs += 1
            self._ensure_executor().submit(
                self._process, card_id, card["front_path"], card.get("back_path", "")
            )
            self._bump()
            return True

    # ---------------------------------------------------------------- processing

    def _process(self, card_id: str, front_path: str, back_path: str) -> None:
        try:
            front = ScannedImage(front_path)
            back = ScannedImage(back_path) if back_path else None

            try:
                previews = {"thumb_path": front.write_thumbnail(self.thumbnail_dir)}
                if back is not None:
                    previews["back_thumb_path"] = back.write_thumbnail(self.thumbnail_dir)
                self.update_card(card_id, **previews)
            except Exception:
                pass  # a missing preview must not stop identification

            result = process_scanned_card(front, back)
            result["status"] = STATUS_DRAFT
            self.update_card(card_id, **result)

            # Only the front is remembered. It identifies the card, and recording backs
            # too would let a blank-ish back collide with some future card's front and
            # wrongly skip it.
            ledger_record(front_path, sku=result.get("sku", ""),
                          title=result.get("title", ""),
                          offer_id=result.get("offer_id", "") or "")
            self._log("Identified {}".format(result.get("title") or front.name))

        except Exception as exc:
            self.update_card(
                card_id,
                status=STATUS_ERROR,
                error=str(exc),
                traceback=traceback.format_exc(),
            )
            self._log("Failed {}: {}".format(Path(front_path).name, exc))

        finally:
            with self._lock:
                self.active_jobs = max(0, self.active_jobs - 1)
                self._bump()

    def rotate_card_image(self, card_id: str, side: str, degrees: int) -> int:
        """Turn one side of a card by `degrees` clockwise and re-cut its preview.

        Local only: the corrected photo is uploaded when the listing is published.
        Returns the side's new total rotation.
        """
        if side not in ("front", "back"):
            return 0

        with self._lock:
            card = self._cards_by_id.get(card_id)
            if card is None:
                return 0

            rotation_key = "front_rotation" if side == "front" else "back_rotation"
            path_key = "front_path" if side == "front" else "back_path"
            thumb_key = "thumb_path" if side == "front" else "back_thumb_path"

            source = card.get(path_key)
            if not source:
                return 0

            rotation = (int(card.get(rotation_key, 0)) + degrees) % 360
            card[rotation_key] = rotation

            try:
                card[thumb_key] = ScannedImage(source).write_thumbnail(
                    self.thumbnail_dir, rotation=rotation
                )
            except Exception:
                pass

            # Deliberately no _bump(): rotating is a local view change, and bumping the
            # version makes the live-sync fragment redraw the entire page.
            self._save()
            return rotation

    def update_card(self, card_id: str, **fields) -> None:
        with self._lock:
            card = self._cards_by_id.get(card_id)
            if card is None:
                return
            card.update(fields)
            self._bump()

    # ------------------------------------------------------------------ reading

    def get_card(self, card_id: str) -> Optional[Dict[str, Any]]:
        """A copy of one card's current state, for redrawing it on its own."""
        with self._lock:
            card = self._cards_by_id.get(card_id)
            return dict(card) if card else None

    def snapshot(self) -> List[Dict[str, Any]]:
        with self._lock:
            return [dict(card) for card in self.cards]

    def stats(self) -> Dict[str, Any]:
        with self._lock:
            counts = {
                STATUS_PROCESSING: 0,
                STATUS_DRAFT: 0,
                STATUS_PUBLISHED: 0,
                STATUS_ERROR: 0,
            }
            for card in self.cards:
                status = card.get("status", STATUS_PROCESSING)
                counts[status] = counts.get(status, 0) + 1

            return {
                "total": len(self.cards),
                "processing": counts[STATUS_PROCESSING],
                "drafts": counts[STATUS_DRAFT],
                "published": counts[STATUS_PUBLISHED],
                "errors": counts[STATUS_ERROR],
                "backlog": len(self.backlog),
                "skipped_duplicates": self.skipped_duplicates,
                "active_jobs": self.active_jobs,
                "max_cards": self.max_cards,
                "paused_for_cap": self.paused_for_cap,
                "capacity_left": max(0, self.max_cards - len(self.cards)),
                "watcher_error": self._watcher.last_error if self._watcher else "",
            }
