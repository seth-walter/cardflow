"""CardFlow: scan on the fi-7160, cards appear and identify themselves.

PaperStream Capture writes duplex JPEGs into a watched folder. Images are paired in
arrival order (1st = front, 2nd = back), queued to background workers that identify,
price, and create the eBay draft, then rendered here for editing and publishing.
"""

import re
import tempfile
import time
import traceback
from pathlib import Path

import requests
import streamlit as st

from config import settings
from core.batch_processor import ensure_card_category, reupload_rotated_images
from core.listing_builder import apply_title_to_description, build_comp_query
from services import gemini_service
from services.card_aspects import match_title_year
from utils import notices
from utils.text_utils import ebay_safe
from services.comp_links import build_comp_links
from core.scan_ledger import count as ledger_count, forget_all as ledger_forget
from core.scan_session import (
    STATUS_DRAFT,
    STATUS_ERROR,
    STATUS_PROCESSING,
    STATUS_PUBLISHED,
    ScanSession,
)
from services.ebay_auction import net_after_fees, recommend_start_price
from services.ebay_inventory import create_or_replace_inventory_item
from services.parallel_refs import learn_from_published
from services.ebay_offers import (
    bulk_publish_offers, create_draft_offer, delete_draft_offer, get_listing_url,
    suggested_min_offer, update_offer_full,
)

st.set_page_config(page_title="CardFlow", page_icon="🃏", layout="wide")

st.markdown(
    """
    <style>
    div[data-testid="stVerticalBlockBorderWrapper"] { border-radius: 12px; }
    .card-title { font-size: 1.02rem; font-weight: 700; margin-bottom: 0.1rem; }
    .card-sub { font-size: 0.8rem; color: #888; margin-bottom: 0.5rem; }
    .badge {
        display: inline-block; padding: 1px 8px; border-radius: 999px;
        font-size: 0.72rem; font-weight: 700; letter-spacing: 0.02em;
    }
    .badge-processing { background: rgba(255,176,32,0.18); color: #b9770e; }
    .badge-draft      { background: rgba(45,156,219,0.18); color: #1f6f9c; }
    .badge-published  { background: rgba(39,174,96,0.18);  color: #1e8449; }
    .badge-error      { background: rgba(235,87,87,0.18);  color: #c0392b; }
    .badge-review     { background: rgba(241,196,15,0.20); color: #9a7d0a; }
    </style>
    """,
    unsafe_allow_html=True,
)

missing = settings.missing_required()
if missing:
    st.error("CardFlow is not set up yet. Missing from .env: **{}**".format(", ".join(missing)))
    st.info("Copy `.env.example` to `.env` and fill it in - the README walks through each "
            "value. `python tools/ebay_setup.py` finds your eBay policy IDs for you.")
    st.stop()

DEFAULT_DESCRIPTION = (
    "Please review photos carefully for exact condition.\n"
    "Exact card shown.\n"
    "Shipped securely."
)

SCAN_ARCHIVE = "moved to a 'published' subfolder"
SCAN_DELETE = "deleted"
SCAN_KEEP = "left in the folder"
SCAN_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".tif", ".tiff", ".bmp"}

BADGE_LABELS = {
    STATUS_PROCESSING: ("badge-processing", "IDENTIFYING"),
    STATUS_DRAFT: ("badge-draft", "DRAFT READY"),
    STATUS_PUBLISHED: ("badge-published", "PUBLISHED"),
    STATUS_ERROR: ("badge-error", "ERROR"),
}


@st.cache_resource
def get_scan_session() -> ScanSession:
    return ScanSession(persist_path=settings.SESSION_STATE_PATH)


session = get_scan_session()


def trim_title(title, max_len: int = 80) -> str:
    # Hand edits too: a pasted ★ or ™ gets the whole listing rejected.
    return ebay_safe(title)[:max_len].strip()


FORMAT_FIXED = "Buy It Now"
FORMAT_AUCTION = "Auction"

OPENAI_BILLING_URL = "https://platform.openai.com/settings/organization/billing/overview"
GEMINI_BILLING_URL = "https://ai.studio/projects"
OUT_OF_CREDIT = r"insufficient_quota|no credits|exceeded your current quota|RESOURCE_EXHAUSTED"


def ensure_widget_defaults(card) -> None:
    """Seed edit widgets once, the first time a card renders with identified data."""
    card_id = card["id"]
    if st.session_state.get("seeded_" + card_id):
        return

    price = float(card.get("price") or 9.99)
    st.session_state["title_" + card_id] = trim_title(
        card.get("title") or card.get("card_name") or card.get("front_name")
    )
    st.session_state["price_" + card_id] = price
    st.session_state["desc_" + card_id] = card.get("description") or DEFAULT_DESCRIPTION
    st.session_state["offers_" + card_id] = bool(card.get("best_offer_enabled", True))
    st.session_state["minoffer_" + card_id] = float(
        card.get("min_offer_price") or suggested_min_offer(price)
    )
    st.session_state["format_" + card_id] = (
        FORMAT_AUCTION if card.get("listing_format") == "AUCTION" else FORMAT_FIXED)
    # Seeded for every card, so switching a Buy It Now draft to auction starts from the
    # recommended opening bid rather than from nothing.
    st.session_state["start_" + card_id] = float(
        card.get("auction_start_price")
        or recommend_start_price(card.get("price_data") or {}, price)["start_price"]
    )
    st.session_state.setdefault("select_" + card_id, False)
    st.session_state["seeded_" + card_id] = True


def offers_on_for_buy_it_now(card_id: str) -> None:
    """An auction card switched to Buy It Now comes in with offers off; turn them on."""
    if st.session_state.get("format_" + card_id) == FORMAT_FIXED:
        st.session_state["offers_" + card_id] = True


def select_for_publish(card_ids, selected: bool) -> None:
    # A callback, because the checkboxes are already drawn when the buttons are.
    for card_id in card_ids:
        st.session_state["select_" + card_id] = selected



def sync_description_to_title(card_id: str) -> None:
    """Keep the description's opening line in step with an edited title.

    Runs as the title box's on_change callback, before any widget is drawn this pass.
    Only the headline paragraph changes; condition notes and hand-written text stay.
    """
    title = st.session_state.get("title_" + card_id, "")
    description = st.session_state.get("desc_" + card_id, "")
    st.session_state["desc_" + card_id] = apply_title_to_description(description, title)



# --------------------------------------------------------------------- sidebar

with st.sidebar:
    st.header("🖨️ Scanner")

    folder_value = st.text_input(
        "Scan inbox folder",
        value=session.folder,
        help="The folder your PaperStream Capture profile saves scans into.",
    )
    max_cards_value = st.number_input(
        "Max cards per session",
        min_value=10,
        max_value=500,
        value=session.max_cards,
        step=10,
        help="Intake pauses at this many cards so the page stays responsive. "
             "Publish and clear, then keep scanning.",
    )
    workers_value = st.slider(
        "Identify workers",
        min_value=1,
        max_value=6,
        value=session.workers,
        help="Cards identified in parallel. 2-3 is safest for OpenAI/eBay rate limits.",
    )
    import_existing = st.checkbox(
        "Import images already in the folder",
        value=True,
        help="Off means only images that land after you press Start are picked up.",
    )

    start_col, stop_col = st.columns(2)

    if start_col.button("▶ Start", type="primary", use_container_width=True, disabled=session.is_scanning):
        session.start(folder_value, max_cards_value, workers_value, import_existing)
        st.rerun()

    if stop_col.button("⏹ Stop", use_container_width=True, disabled=not session.is_scanning):
        session.stop()
        st.rerun()

    if max_cards_value != session.max_cards:
        session.set_max_cards(max_cards_value)

    st.toggle("Auto-refresh while scanning", value=True, key="auto_refresh")

    if session.is_scanning:
        st.success("Watching for scans")
    else:
        st.info("Idle")

    st.divider()
    st.subheader("Scan folder")

    st.selectbox(
        "After publishing, scans are",
        [SCAN_ARCHIVE, SCAN_DELETE, SCAN_KEEP],
        key="scan_mode",
        help="Published scans are cleared out so the next stack starts clean. Archiving "
             "moves them to a 'published' subfolder; the originals are the only "
             "full-resolution copies you have left once a card is listed.",
    )

    # The folder listing is disk I/O, so it only runs when the control is opened
    # rather than on every interaction with the page.
    if st.checkbox("I want to empty the scan folder", key="confirm_clear_folder"):
        inbox_files = []
        try:
            inbox_path = Path(folder_value)
            if inbox_path.is_dir():
                inbox_files = [p for p in inbox_path.iterdir()
                               if p.is_file() and p.suffix.lower() in SCAN_EXTENSIONS]
        except OSError:
            pass

        st.caption("{} image(s) currently in the folder.".format(len(inbox_files)))

        if st.button("Empty scan folder ({} files)".format(len(inbox_files)),
                     use_container_width=True, disabled=not inbox_files):
            removed, failed = 0, 0
            for path in inbox_files:
                try:
                    path.unlink()
                    removed += 1
                except OSError:
                    failed += 1
            session.drop_backlog()
            notices.post("sidebar", "warning", "Deleted {} file(s).".format(removed)
                         + (" {} could not be deleted.".format(failed) if failed else ""))

    st.caption("{} scan(s) remembered as already listed.".format(ledger_count()))
    if st.checkbox("Let me re-list scans I already listed", key="confirm_forget_history"):
        st.caption("Only do this if you want duplicates: the app will treat every scan "
                   "as new again.")
        if st.button("Forget scan history", use_container_width=True):
            notices.post("sidebar", "warning",
                         "Forgot {} remembered scan(s).".format(ledger_forget()))

    st.divider()
    st.subheader("Misfeed tools")
    st.caption(
        "Pairing is strictly front, back, front, back in scan order. "
        "If a sheet was dropped, fix the queue here before scanning more."
    )

    tool_left, tool_right = st.columns(2)
    if tool_left.button("Queue leftover as single-sided", use_container_width=True):
        if session.flush_single_sided():
            st.rerun()
        else:
            notices.post("sidebar", "warning", "Nothing unpaired in the queue.")

    if tool_right.button("Drop queued images", use_container_width=True):
        session.drop_backlog()
        st.rerun()

    st.divider()
    with st.expander("Add images manually"):
        manual_files = st.file_uploader(
            "Upload in front, back order",
            type=["jpg", "jpeg", "png", "webp"],
            accept_multiple_files=True,
            key="manual_upload",
        )
        if manual_files and st.button("Queue uploaded images", use_container_width=True):
            staging = Path(tempfile.gettempdir()) / "ebay_card_lister_manual"
            staging.mkdir(parents=True, exist_ok=True)
            staged_paths = []
            for order, uploaded in enumerate(manual_files):
                target = staging / "{}_{:03d}_{}".format(int(time.time()), order, uploaded.name)
                target.write_bytes(uploaded.getvalue())
                staged_paths.append(target)
            session.enqueue_paths(staged_paths)
            st.rerun()

    st.divider()
    if st.checkbox("I want to clear this session"):
        if st.button("Clear all cards", type="secondary", use_container_width=True):
            for key in [k for k in st.session_state.keys() if k.startswith(
                ("title_", "price_", "desc_", "offers_", "minoffer_", "select_", "seeded_")
            )]:
                del st.session_state[key]
            session.reset()
            st.rerun()

    notices.show("sidebar")


# ---------------------------------------------------------------------- status

# Read the version first: any change that lands while rendering leaves version ahead of
# what we recorded, so the live_sync fragment redraws instead of sitting on stale cards.
st.session_state["_rendered_version"] = session.version
cards = session.snapshot()
stats = session.stats()

st.title("🃏 CardFlow")
st.caption("Scan a stack on the fi-7160 — cards land here and identify themselves as they feed.")

metric_cols = st.columns(6)
metric_cols[0].metric("Cards", "{} / {}".format(stats["total"], stats["max_cards"]))
metric_cols[1].metric("Identifying", stats["processing"])
metric_cols[2].metric("Drafts ready", stats["drafts"])
metric_cols[3].metric("Published", stats["published"])
metric_cols[4].metric("Errors", stats["errors"])
metric_cols[5].metric(
    "Images queued", stats["backlog"],
    delta="{} dup skipped".format(stats["skipped_duplicates"]) if stats["skipped_duplicates"] else None,
    delta_color="off",
)

st.progress(min(1.0, stats["total"] / float(stats["max_cards"])))

if stats["paused_for_cap"]:
    st.warning(
        "Card cap of {} reached. Intake is paused with {} image(s) waiting — publish and clear "
        "the session (or raise the cap) to continue.".format(stats["max_cards"], stats["backlog"])
    )
elif stats["capacity_left"] <= 10 and stats["total"]:
    st.info("{} card slot(s) left before intake pauses.".format(stats["capacity_left"]))

if stats["skipped_duplicates"]:
    st.info(
        "Skipped {} scan(s) already listed from a previous session - they were not "
        "listed twice. See the activity log for which ones.".format(
            stats["skipped_duplicates"])
    )

if stats["watcher_error"]:
    st.error("Watcher problem: {}".format(stats["watcher_error"]))

if gemini_service.on_free_tier:
    st.warning("**Paid Gemini credit is used up - identifying on the free-tier key.** Slower "
               "under its rate limits, and Google may train on these scans. Add credit and "
               "restart CardFlow to go back to the paid key.")
    st.link_button("Add credit", GEMINI_BILLING_URL)

# An empty balance fails every card (OpenAI 429, Gemini 402). Without this the stack just
# fills with identical red errors and the cause is buried in each one.
out_of_credit = [c for c in cards if c.get("status") == STATUS_ERROR
                 and re.search(OUT_OF_CREDIT, c.get("error") or "", re.IGNORECASE)]
if out_of_credit:
    gemini = settings.VISION_MODEL.startswith("gemini")
    st.error("**{} is out of credit.** {} card(s) could not be identified. Add credit, "
             "then retry them.".format("Gemini" if gemini else "OpenAI", len(out_of_credit)))
    credit_cols = st.columns([1, 1, 3])
    credit_cols[0].link_button("Add credit", GEMINI_BILLING_URL if gemini else OPENAI_BILLING_URL,
                               type="primary")
    if credit_cols[1].button("Retry {} card(s)".format(len(out_of_credit)), key="retry_credit"):
        for card in out_of_credit:
            session.retry(card["id"])
        st.rerun()

if not session.is_scanning and not cards:
    st.info(
        "Point the sidebar at your PaperStream output folder and press **Start**, then feed "
        "the stack. Scan duplex so each card produces a front then a back image."
    )


# ------------------------------------------------------------------ live sync

refresh_seconds = 2 if (session.is_scanning and st.session_state.get("auto_refresh", True)) else None


@st.fragment(run_every=refresh_seconds)
def live_sync():
    """Cheap poll: only forces a full redraw when the session actually changed."""
    if session.version != st.session_state.get("_rendered_version"):
        st.rerun(scope="app")

    if not session.is_scanning:
        st.caption("Auto-refresh off (scanner stopped).")
        return

    # No folder listing here: this ticks every 2s, and the queued-images metric above
    # already reports intake without touching the disk.
    st.caption("Live at {} · {} job(s) running".format(
        time.strftime("%H:%M:%S"), session.stats()["active_jobs"]))


live_sync()


# ------------------------------------------------------------------ card list

@st.fragment
def render_card_previews(card_id: str) -> None:
    """Previews plus rotate controls, isolated so a rotate repaints one card only.

    Reads the card fresh rather than from the page snapshot, because the snapshot is
    already stale by the time a rotate is handled.
    """
    card = session.get_card(card_id) or {}

    if not card.get("thumb_path"):
        st.caption("No preview")
        return

    for side, thumb_key in (("front", "thumb_path"), ("back", "back_thumb_path")):
        if not card.get(thumb_key):
            continue

        # The image is painted into a placeholder after the clicks are handled, so the
        # new angle shows immediately without asking Streamlit for another rerun.
        image_slot = st.empty()
        left, right = st.columns(2)

        turn = 0
        if left.button("⟲", key="rotl_{}_{}".format(side, card_id),
                       help="Rotate {} left".format(side), use_container_width=True):
            turn = -90
        if right.button("⟳", key="rotr_{}_{}".format(side, card_id),
                        help="Rotate {} right".format(side), use_container_width=True):
            turn = 90

        if turn:
            session.rotate_card_image(card_id, side, turn)
            card = session.get_card(card_id) or card

        thumb = card.get(thumb_key)
        if thumb and Path(thumb).exists():
            image_slot.image(thumb, width=130)

    turned = []
    if card.get("front_rotation"):
        turned.append("front {}°".format(card["front_rotation"]))
    if card.get("back_rotation"):
        turned.append("back {}°".format(card["back_rotation"]))
    if turned:
        st.caption("rotated " + ", ".join(turned) + " · applied on publish")


@st.fragment
def render_card_details(card_id: str) -> None:
    """One card's heading and edit controls, isolated so typing in it reruns only it.

    Without this every keystroke committed to a title re-executed the whole page,
    which is what interaction lag was made of.
    """
    card = session.get_card(card_id) or {}
    status = card.get("status", STATUS_PROCESSING)
    badge_class, badge_text = BADGE_LABELS.get(status, BADGE_LABELS[STATUS_PROCESSING])

    heading = card.get("title") or card.get("card_name") or card.get("front_name", "")
    pages = card.get("front_name", "")
    pages += " + " + card["back_name"] if card.get("back_name") else " (single-sided)"

    review_badge = ""
    if card.get("needs_review") and status == STATUS_DRAFT:
        review_badge = '<span class="badge badge-review">NEEDS PRICE REVIEW</span> &nbsp; '
    if status == STATUS_DRAFT and st.session_state.get("format_" + card_id, (
            FORMAT_AUCTION if card.get("listing_format") == "AUCTION" else FORMAT_FIXED)) == FORMAT_AUCTION:
        review_badge += '<span class="badge badge-review">AUCTION</span> &nbsp; '

    st.markdown(
        '<div class="card-title">#{} &nbsp; {}</div>'
        '<div class="card-sub"><span class="badge {}">{}</span> &nbsp; {}{}</div>'.format(
            card.get("index", 0) + 1, heading, badge_class, badge_text, review_badge, pages
        ),
        unsafe_allow_html=True,
    )

    if status == STATUS_PROCESSING:
        st.caption("Identifying, pricing, and creating the eBay draft…")
        return

    if status == STATUS_ERROR:
        st.error(card.get("error") or "Processing failed.")
        if st.button("Retry this card", key="retry_" + card_id):
            session.retry(card_id)
            st.rerun()
        if card.get("traceback"):
            with st.expander("Error details"):
                st.code(card["traceback"])
        return

    ensure_widget_defaults(card)

    st.caption(
        "SKU {} · Offer {} · {} ({}) · price source: {}".format(
            card.get("sku", "—"), card.get("offer_id", "—"),
            card.get("category_name", "—"), card.get("category_id", "—"),
            card.get("price_source", "—"),
        )
    )

    title_value = st.text_input(
        "Title", key="title_" + card_id,
        on_change=sync_description_to_title, args=(card_id,),
        help="Editing this also rewrites the first line of the description.",
    )
    length = len(title_value)
    if length > 80:
        st.error("Title too long ({}/80). It will be trimmed on publish.".format(length))
    elif length > 70:
        st.warning("Title length: {}/80".format(length))
    else:
        st.caption("Title length: {}/80".format(length))

    listing_format = st.radio(
        "Format", [FORMAT_FIXED, FORMAT_AUCTION], key="format_" + card_id,
        horizontal=True, on_change=offers_on_for_buy_it_now, args=(card_id,),
        help="Cards under ${:.0f} default to auction (AUCTION_UNDER in .env).".format(
            settings.AUCTION_UNDER),
    )

    price_cols = st.columns(3)
    if listing_format == FORMAT_AUCTION:
        price_cols[0].number_input("Market value", min_value=0.99, step=0.50,
                                   key="price_" + card_id,
                                   help="What the card sells for. Picks the shipping policy.")
        start_bid = price_cols[1].number_input("Starting bid", min_value=0.99, step=0.50,
                                               key="start_" + card_id)
        price_cols[2].metric("You net at the opening bid", "${:,.2f}".format(
            net_after_fees(float(start_bid))))
        reason = card.get("auction_reason") or recommend_start_price(
            card.get("price_data") or {}, float(st.session_state.get("price_" + card_id) or 0)
        )["reason"]
        st.caption("Opening bid: " + reason + ". One bidder means this is the sale price.")
    else:
        price_cols[0].number_input("Price", min_value=0.99, step=0.50, key="price_" + card_id)
        price_cols[1].checkbox("Allow offers", key="offers_" + card_id)
        price_cols[2].number_input("Min offer", min_value=0.01, step=0.50, key="minoffer_" + card_id)

    price_data = card.get("price_data") or {}
    basis = price_data.get("basis", card.get("price_basis", "ask"))
    source_label = {
        "own_sales": "your own past sales",
        "tcgplayer_market": "TCGplayer market (sold)",
        "pricecharting": "PriceCharting (sold)",
        "sportscardspro": "SportsCardsPro (sold)",
        "ebay_active": "eBay asking prices",
        "fallback": "no comps found",
    }.get(price_data.get("source", card.get("price_source", "")), card.get("price_source", ""))

    if basis == "sold":
        st.caption("Priced from {} - {}".format(source_label, price_data.get("detail", "")))
    else:
        st.caption("Estimate from {} - {}. Not a completed-sale comp.".format(
            source_label, price_data.get("detail", "")))

    offers = price_data.get("offers") or {}
    if offers.get("offer_sales"):
        bin_median = offers.get("bin_median")
        offer_median = offers.get("offer_median")
        line = "{:.0f}% of these sales were accepted offers".format(
            offers.get("offer_share", 0) * 100)
        if bin_median and offer_median:
            line += " - offers landed at ${:,.2f} against ${:,.2f} asked".format(
                offer_median, bin_median)
        st.caption(line)

    for warning in card.get("title_warnings") or []:
        st.warning("Check the title: " + warning)

    if card.get("needs_review") or price_data.get("needs_review"):
        reasons = price_data.get("review_reasons") or card.get("review_reasons") or []
        st.warning("Check this price: " + ("; ".join(reasons) if reasons else "thin comp data"))

    st.text_area("Description", key="desc_" + card_id, height=110)

    card_data = card.get("card_data") or {}
    comp_links = build_comp_links(card_data)
    if comp_links:
        st.caption("Check the price yourself — comps for: {}".format(build_comp_query(card_data)))
        link_cols = st.columns(len(comp_links))
        for col, (label, url) in zip(link_cols, comp_links):
            col.link_button(label, url, use_container_width=True)

    # The raw dumps are large and were being serialised for every card on every rerun,
    # so they are only built when asked for.
    if st.toggle("Show detected data", key="details_" + card_id):
        detail_cols = st.columns(2)
        if card.get("price_data"):
            with detail_cols[0]:
                st.caption("Pricing")
                st.json(card["price_data"], expanded=False)
        if card_data:
            with detail_cols[1]:
                st.caption("Detected card data")
                st.json(card_data, expanded=False)


if cards:
    st.subheader("Scanned cards")

    control_cols = st.columns([1, 1, 2])
    newest_first = control_cols[0].toggle("Newest first", value=True)
    # Smaller pages keep full redraws cheap: every card on screen costs ~10ms of
    # rerun, which is paid again on each page load and auto-refresh tick.
    page_size = control_cols[1].selectbox("Per page", [5, 10, 25, 50], index=2)

    ordered = list(reversed(cards)) if newest_first else cards
    total_pages = max(1, (len(ordered) + page_size - 1) // page_size)
    page = control_cols[2].number_input(
        "Page", min_value=1, max_value=total_pages, value=1, step=1
    )
    start = (page - 1) * page_size
    visible = ordered[start:start + page_size]

    st.caption(
        "Showing {}-{} of {} cards. Edits are kept per card across pages.".format(
            start + 1, start + len(visible), len(ordered)
        )
    )

    for card in visible:
        card_id = card["id"]
        status = card.get("status", STATUS_PROCESSING)

        with st.container(border=True):
            image_col, detail_col = st.columns([1, 5])

            with image_col:
                render_card_previews(card_id)

            with detail_col:
                render_card_details(card_id)

                # Selection stays outside the fragment: it changes what the Publish
                # buttons below will do, so it has to reach the whole page.
                if status == STATUS_DRAFT and card.get("offer_id"):
                    st.checkbox("Publish this listing", key="select_" + card_id)
                elif status == STATUS_PUBLISHED:
                    st.success("Published.")


def collect_publish_item(card):
    """Snapshot a card plus its current edits into a publishable payload."""
    card_id = card["id"]
    price = float(st.session_state.get("price_" + card_id) or card.get("price") or 9.99)
    title = trim_title(st.session_state.get("title_" + card_id) or card.get("title"))

    return {
        "card_id": card_id,
        "offer_id": card["offer_id"],
        "sku": card["sku"],
        "title": title,
        "price": price,
        "best_offer_enabled": bool(st.session_state.get("offers_" + card_id, True)),
        "min_offer_price": float(
            st.session_state.get("minoffer_" + card_id) or suggested_min_offer(price)
        ),
        "description": st.session_state.get("desc_" + card_id) or card.get("description")
        or DEFAULT_DESCRIPTION,
        "listing_format": "AUCTION" if st.session_state.get("format_" + card_id) == FORMAT_AUCTION
        else "FIXED_PRICE",
        "offer_format": card.get("offer_format") or "FIXED_PRICE",
        "auction_start_price": float(st.session_state.get("start_" + card_id) or 0.99),
        "category_id": card["category_id"],
        "category_name": card.get("category_name", ""),
        "sport": card.get("sport", ""),
        "league": card.get("league", ""),
        "team": card.get("team", ""),
        "condition_estimate": card.get("condition_estimate", ""),
        "is_graded": card.get("is_graded", False),
        "image_urls": card.get("image_urls", []),
        # A year corrected in the title must reach Year Manufactured/Season too.
        "card_data": match_title_year(card.get("card_data", {}), title),
        "front_path": card.get("front_path", ""),
        "back_path": card.get("back_path", ""),
        "front_rotation": card.get("front_rotation", 0),
        "back_rotation": card.get("back_rotation", 0),
    }


def learn_parallels(items):
    """Keep each published card as an example of the parallel its final title names.

    Runs before the scans are moved, while the paths still point at them. A failure
    here must never block a publish, so it is reported as nothing learned.
    """
    learned = []
    for item in items:
        try:
            label = learn_from_published(item["title"], item["card_data"], item["front_path"],
                                         item["front_rotation"], source=item["sku"])
        except Exception:
            label = ""
        if label:
            learned.append(label)
    return learned


def retire_scans(items, mode: str):
    """Clear published scans out of the watch folder so the next stack starts clean.

    Archiving is the default because the originals are the only full-resolution copy
    left once a card is listed.
    """
    if mode == SCAN_KEEP:
        return 0, ""

    moved = 0
    problems = []

    for item in items:
        for path_value in (item.get("front_path"), item.get("back_path")):
            if not path_value:
                continue

            source = Path(path_value)
            if not source.exists():
                continue

            try:
                if mode == SCAN_DELETE:
                    source.unlink()
                else:
                    archive = source.parent / "published"
                    archive.mkdir(parents=True, exist_ok=True)
                    target = archive / source.name
                    if target.exists():
                        target = archive / "{}_{}{}".format(
                            source.stem, int(time.time() * 1000), source.suffix
                        )
                    source.replace(target)
                moved += 1
            except OSError as exc:
                problems.append("{}: {}".format(source.name, exc))

    return moved, "; ".join(problems[:3])


def publish_items(items, scan_mode: str):
    """Update, publish, and report on a batch of cards. Returns True if anything shipped."""
    if not items:
        return False

    st.session_state.pop("publish_failures", None)
    notices.clear("publish")
    current = None   # the card being sent when something raises, for the error report
    try:
        rotated_items = [i for i in items if i["front_rotation"] or i["back_rotation"]]
        if rotated_items:
            with st.spinner("Re-uploading {} rotated photo set(s)…".format(len(rotated_items))):
                for item in rotated_items:
                    current = item
                    item["image_urls"] = reupload_rotated_images(
                        item["front_path"], item["back_path"],
                        item["front_rotation"], item["back_rotation"],
                    )
                    session.update_card(item["card_id"], image_urls=item["image_urls"])

        with st.spinner("Updating {} listing(s)…".format(len(items))):
            for item in items:
                current = item
                # Re-check the category here: eBay's suggester has put non-sport cards in
                # sports (which demands a Sport aspect) and in categories like "Washer &
                # Dryer Parts", both of which fail at publish time.
                fixed_id, fixed_name = ensure_card_category(
                    item["card_data"], item["category_id"], item["category_name"]
                )
                if fixed_id != item["category_id"]:
                    notices.post("publish", "caption", "{}: category corrected to {} ({})".format(
                        item["title"][:40], fixed_name, fixed_id))
                    item["category_id"], item["category_name"] = fixed_id, fixed_name
                    session.update_card(item["card_id"],
                                        category_id=fixed_id, category_name=fixed_name)

                create_or_replace_inventory_item(
                    sku=item["sku"],
                    title=item["title"],
                    description=item["description"],
                    category_id=item["category_id"],
                    sport=item["sport"],
                    league=item["league"],
                    team=item["team"],
                    condition_estimate=item["condition_estimate"],
                    is_graded=item["is_graded"],
                    image_urls=item["image_urls"],
                    price_value=item["price"],
                    card_data=item["card_data"],
                    category_name=item["category_name"],
                )
                offer_terms = dict(
                    sku=item["sku"],
                    category_id=item["category_id"],
                    price_value=item["price"],
                    best_offer_enabled=item["best_offer_enabled"],
                    min_offer_price=item["min_offer_price"],
                    listing_format=item["listing_format"],
                    auction_start_price=item["auction_start_price"],
                )
                if item["listing_format"] != item["offer_format"]:
                    # eBay will not change an offer's format in place, so the draft is
                    # replaced. The inventory item (photos, specifics) is untouched.
                    delete_draft_offer(item["offer_id"])
                    created = create_draft_offer(**offer_terms)
                    if not created.get("offerId"):
                        raise RuntimeError("Could not recreate the draft for {} as {}: {}".format(
                            item["title"][:40], item["listing_format"], created))
                    item["offer_id"] = created["offerId"]
                    item["offer_format"] = item["listing_format"]
                else:
                    update_offer_full(offer_id=item["offer_id"], **offer_terms)
                session.update_card(
                    item["card_id"],
                    offer_id=item["offer_id"],
                    offer_format=item["offer_format"],
                    listing_format=item["listing_format"],
                    auction_start_price=item["auction_start_price"],
                    title=item["title"],
                    price=item["price"],
                    description=item["description"],
                    best_offer_enabled=item["best_offer_enabled"],
                    min_offer_price=item["min_offer_price"],
                )

        current = None
        offer_ids = [item["offer_id"] for item in items]
        successful_offer_ids = []
        failed_listings = []

        with st.spinner("Publishing {} offer(s)…".format(len(offer_ids))):
            for batch_start in range(0, len(offer_ids), 25):
                batch = offer_ids[batch_start:batch_start + 25]
                batch_result = bulk_publish_offers(batch)

                for response in batch_result.get("responses", []):
                    offer_id = response.get("offerId")
                    status_code = response.get("statusCode")
                    match = next((i for i in items if i["offer_id"] == offer_id), None)

                    if status_code in (200, 201, 204):
                        successful_offer_ids.append(offer_id)
                    else:
                        failed_listings.append({
                            "title": match["title"] if match else "Unknown listing",
                            "offer_id": offer_id,
                            "status_code": status_code,
                            "errors": response.get("errors", []),
                        })

        published_items = [i for i in items if i["offer_id"] in successful_offer_ids]
        for item in published_items:
            # Only the card record is touched here. Writing st.session_state["select_…"]
            # would raise, because that checkbox was already drawn during this run.
            session.update_card(item["card_id"], status=STATUS_PUBLISHED)

        if failed_listings:
            # Kept in session state: while scanning, the live_sync poll redraws the page
            # within two seconds and an error written here would vanish with it.
            st.session_state["publish_failures"] = [
                {"title": f["title"], "error": "Offer {} | Status {}: {}".format(
                    f["offer_id"], f["status_code"], ebay_errors_text(f["errors"]))}
                for f in failed_listings]
            notices.post("publish", "warning", "Published {} listing(s); {} failed.".format(
                len(successful_offer_ids), len(failed_listings)))
        else:
            notices.post("publish", "success",
                         "Published all {} listing(s).".format(len(successful_offer_ids)))

        if published_items:
            learned = learn_parallels(published_items)
            if learned:
                notices.post("publish", "caption",
                             "Saved as parallel examples for future scans: {}".format(
                                 ", ".join(sorted(set(learned)))))
            moved, problems = retire_scans(published_items, scan_mode)
            if moved:
                notices.post("publish", "caption", "{} scan file(s) {} the watch folder.".format(
                    moved, "deleted from" if scan_mode == SCAN_DELETE else "moved out of"))
            if problems:
                notices.post("publish", "warning",
                             "Some scans could not be cleared: {}".format(problems))

        discord_rows = []
        for item in published_items:
            try:
                listing_url = get_listing_url(item["offer_id"])
                discord_rows.append(
                    "$ebay view {} 200".format(listing_url) if listing_url
                    else "$ebay view URL_NOT_FOUND_FOR_OFFER_{} 200".format(item["offer_id"])
                )
            except Exception as exc:
                discord_rows.append("ERROR fetching URL for offer {}: {}".format(
                    item["offer_id"], exc))

        if discord_rows:
            notices.post("publish", "rows", discord_rows,
                         label="Discord eBay view rows - copy/paste:")

        return True

    except Exception as exc:
        st.session_state["publish_failures"] = [{
            "title": current["title"] if current else "Publish",
            "error": "{} (publishing stopped here; cards after it were not sent)".format(exc),
            "detail": traceback.format_exc()}]

    return False


def ebay_errors_text(errors) -> str:
    """eBay's per-offer error list as one readable line."""
    parts = []
    for error in errors or []:
        params = ", ".join("{}={}".format(p.get("name"), p.get("value"))
                           for p in error.get("parameters") or [] if p.get("value"))
        parts.append("{}: {}{}".format(error.get("errorId", "?"),
                                       error.get("longMessage") or error.get("message", ""),
                                       " ({})".format(params) if params else ""))
    return "; ".join(parts) or "no details from eBay"


def show_publish_failures() -> None:
    failures = st.session_state.get("publish_failures")
    if not failures:
        return
    for failure in failures:
        st.error("**{}** - {}".format(failure["title"], failure["error"]))
        if failure.get("detail"):
            with st.expander("Error details"):
                st.code(failure["detail"])
    if st.button("Dismiss publish errors", key="dismiss_publish_failures"):
        del st.session_state["publish_failures"]
        st.rerun()


ready_to_publish = [
    card for card in cards
    if card.get("status") == STATUS_DRAFT and card.get("offer_id")
]
selected_cards = [
    card for card in ready_to_publish
    if st.session_state.get("select_" + card["id"], False)
]

if ready_to_publish:
    st.divider()
    st.subheader("Publish")

    scan_mode = st.session_state.get("scan_mode", SCAN_ARCHIVE)
    ready_ids = [c["id"] for c in ready_to_publish]
    select_cols = st.columns([2, 2, 3])
    select_cols[0].button("Select all for publish ({})".format(len(ready_ids)),
                          on_click=select_for_publish, args=(ready_ids, True),
                          use_container_width=True, disabled=not ready_ids)
    select_cols[1].button("Clear selection", on_click=select_for_publish,
                          args=(ready_ids, False), use_container_width=True,
                          disabled=not selected_cards)
    publish_cols = st.columns([2, 2, 3])

    if publish_cols[0].button(
        "Publish selected ({})".format(len(selected_cards)),
        type="primary", disabled=not selected_cards, use_container_width=True,
    ):
        publish_items([collect_publish_item(c) for c in selected_cards], scan_mode)

    # Flagged prices are held back from the bulk path: the point of the flag is that
    # nobody has looked at that number yet.
    confident = [c for c in ready_to_publish if not c.get("needs_review")]
    flagged = [c for c in ready_to_publish if c.get("needs_review")]

    if publish_cols[1].button(
        "Publish ALL confident ({})".format(len(confident)),
        use_container_width=True, disabled=not confident,
    ):
        publish_items([collect_publish_item(c) for c in confident], scan_mode)

    if flagged:
        publish_cols[2].caption(
            "Publish ALL skips {} card(s) marked NEEDS PRICE REVIEW. Tick those "
            "individually once you have checked the price, then use Publish selected."
            .format(len(flagged))
        )
    else:
        publish_cols[2].caption(
            "Publish ALL ignores the checkboxes and publishes every draft in this "
            "session, using each card's current edits."
        )

notices.show("publish")
show_publish_failures()


# ---------------------------------------------------------------- activity log

if session.messages:
    with st.expander("Activity log"):
        st.code("\n".join(list(session.messages)[:40]))
