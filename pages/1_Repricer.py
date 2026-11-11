"""Repricer: pull your live listings, see a fresh suggested price, update in bulk.

Nothing is sent to eBay until a listing is ticked and the update button is pressed.
"""

import sys
import time
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.batch_processor import NON_SPORT_CATEGORY, SPORTS_CATEGORY, TCG_CATEGORY
from core.reprice import movers, suggest_for_listings
from core.reprice_history import (
    count as reprice_count, forget_all as reprice_forget, record as record_reprice,
)
from services.comp_links import build_comp_links
from config import settings
from services.ebay_listings import fetch_active_listings, update_offer_price
from services.ebay_auction import (
    DEFAULT_DURATION, DURATIONS, convert_to_auction, recommend_start_price,
)
from services.ebay_offers import suggested_min_offer
from services.ebay_trading import revise_listing_price
from utils import notices

st.set_page_config(page_title="CardFlow - Repricer", page_icon="dollar",
                   layout="wide")

st.markdown(
    """
    <style>
    .rp-badge {display:inline-block;padding:1px 8px;border-radius:999px;
               font-size:0.72rem;font-weight:700;}
    .rp-up   {background:rgba(39,174,96,0.18); color:#1e8449;}
    .rp-down {background:rgba(235,87,87,0.18); color:#c0392b;}
    .rp-sold {background:rgba(45,156,219,0.18); color:#1f6f9c;}
    .rp-ask  {background:rgba(241,196,15,0.20);color:#9a7d0a;}
    </style>
    """,
    unsafe_allow_html=True,
)

st.title("Repricer")
st.caption("Pull your live listings, compare each price against fresh comps, and update "
           "the ones that have drifted.")

def row_key(row):
    """Stable id for a listing. Legacy listings have no Inventory offer id."""
    return row.get("offer_id") or "item" + str(row.get("listing_id", ""))


state = st.session_state
state.setdefault("listings", [])
state.setdefault("priced", [])


# ----------------------------------------------------------------- load + price

with st.sidebar:
    st.header("Load")

    max_listings = st.number_input(
        "Live listings to load", min_value=10, max_value=1000, value=100, step=10,
        help="Counts live listings, not inventory items. Your account holds plenty of "
             "items that were never published or have already sold, so the app keeps "
             "reading inventory until it has this many live ones.",
    )
    workers = st.slider("Parallel workers", 1, 10, 6)

    if st.button("1. Load live listings", type="primary", use_container_width=True):
        target = int(max_listings)
        bar = st.progress(0.0, text="Reading inventory...")

        def show(found, scanned):
            bar.progress(
                min(found / max(target, 1), 1.0),
                text="Found {}/{} live listing(s) - {} inventory item(s) checked".format(
                    found, target, scanned),
            )

        try:
            state["listings"] = fetch_active_listings(
                max_listings=target, workers=workers, on_progress=show,
            )
            state["priced"] = []
            found = len(state["listings"])
            notices.clear("load")
            if found < target:
                notices.post("load", "info", "Found {} live listing(s) - that is everything "
                             "currently published on your account.".format(found))
        except Exception as exc:
            notices.clear("load")
            notices.post("load", "error", "Could not load listings: {}".format(exc))
        bar.empty()

    if state["listings"]:
        if st.button("2. Price {} listing(s)".format(len(state["listings"])),
                     use_container_width=True):
            bar = st.progress(0.0, text="Pricing...")
            started = time.time()
            state["priced"] = suggest_for_listings(
                state["listings"], workers=workers,
                on_progress=lambda done, total: bar.progress(
                    done / max(total, 1), text="Priced {}/{}".format(done, total)),
            )
            bar.empty()
            notices.clear("load")
            notices.post("load", "success", "Priced {} listing(s) in {:.0f}s".format(
                len(state["priced"]), time.time() - started))

    notices.show("load")

    recorded = reprice_count()
    if recorded:
        st.caption("{} listing(s) have a reprice on record.".format(recorded))
    else:
        st.caption("No reprices on record yet. The cooldown only knows about changes "
                   "made here, so anything you repriced elsewhere will still appear - "
                   "use 'Leave it' on those rows.")
    if st.checkbox("Clear reprice history", key="confirm_clear_reprice"):
        if st.button("Forget when listings were repriced", use_container_width=True):
            notices.clear("history")
            notices.post("history", "warning", "Cleared {} record(s).".format(reprice_forget()))
    notices.show("history")

    cards_only = st.checkbox(
        "Card listings only", value=True,
        help="Your account also holds non-card listings. Pricing those from trading-card "
             "sold comps would produce nonsense, so they are hidden unless you ask.",
    )

    st.divider()
    st.header("Best offer")
    floor_mode = st.radio(
        "When repricing, set the offer floor to",
        ["{:.0f}% of the new price".format(settings.MIN_OFFER_RATIO * 100),
         "keep each listing's current ratio"],
        help="Older listings were created with an 80% floor. The first option moves them "
             "onto your current policy as you reprice them.",
    )
    keep_ratio = floor_mode.startswith("keep")

    st.divider()
    st.header("Filter")
    min_change = st.slider("Only show changes of at least (%)", 0, 100, 15, step=5)
    cooldown = st.slider(
        "Skip listings repriced in the last (days)", 0, 90, 14,
        help="Comps drift a little every week. Without a cooldown the same cards "
             "resurface every run and get churned for pennies.",
    )
    sold_only = st.checkbox("Only sold-comp suggestions", value=False,
                            help="Hides suggestions derived from asking prices.")
    hide_flagged = st.checkbox("Hide NEEDS PRICE REVIEW", value=False)


# --------------------------------------------------------------------- summary

if not state["listings"]:
    st.info("Press **1. Load live listings** to begin. Nothing on eBay changes until you "
            "tick a listing and press update.")
    st.stop()

if not state["priced"]:
    st.info("{} live listing(s) loaded. Press **2. Price listings** to fetch fresh comps."
            .format(len(state["listings"])))
    st.stop()

CARD_CATEGORIES = {SPORTS_CATEGORY[0], NON_SPORT_CATEGORY[0], TCG_CATEGORY[0]}

priced_rows = state["priced"]
if cards_only:
    priced_rows = [row for row in priced_rows
                   if row.get("category_id") in CARD_CATEGORIES]

rows = movers(priced_rows, min_change_pct=min_change, sold_only=sold_only,
              cooldown_days=cooldown)
if hide_flagged:
    rows = [row for row in rows if not row.get("needs_review")]

errors = [row for row in state["priced"] if row.get("error")]
raises = [row for row in rows if row["delta"] > 0]
drops = [row for row in rows if row["delta"] < 0]

summary = st.columns(5)
summary[0].metric("Live listings", len(state["priced"]))
summary[1].metric("Worth changing", len(rows))
summary[2].metric("Raise", len(raises))
summary[3].metric("Lower", len(drops))
summary[4].metric("Net change", "${:+,.2f}".format(sum(r["delta"] for r in rows)))

held = [row for row in movers(priced_rows, min_change_pct=min_change,
                              sold_only=sold_only)
        if row not in rows]
non_card = [row for row in state["priced"]
            if row.get("category_id") not in CARD_CATEGORIES]
notes = []
if errors:
    notes.append("{} could not be priced".format(len(errors)))
if held:
    notes.append("{} repriced within the last {} days".format(len(held), cooldown))
if cards_only and non_card:
    notes.append("{} non-card listing(s)".format(len(non_card)))
if notes:
    st.caption("Hidden: " + "; ".join(notes) + ".")


# ----------------------------------------------------------------------- table

st.divider()

controls = st.columns([1, 1, 3])
page_size = controls[0].selectbox("Rows per page", [10, 25, 50], index=0)
total_pages = max(1, (len(rows) + page_size - 1) // page_size)
page = controls[1].number_input("Page", 1, total_pages, 1, step=1)
start = (page - 1) * page_size
visible = rows[start:start + page_size]

controls[2].caption(
    "Showing {}-{} of {} listing(s) that moved at least {}%. Sorted by biggest move."
    .format(start + 1, start + len(visible), len(rows), min_change)
)

# Bulk selection runs BEFORE the checkboxes below are created: Streamlit refuses to let
# you write a widget's key after that widget has been drawn.
trustworthy = [r for r in rows if r.get("basis") == "sold" and not r.get("needs_review")]

bulk = st.columns([2, 2, 3])
if bulk[0].button("Select all sold-comp movers ({})".format(len(trustworthy)),
                  disabled=not trustworthy, use_container_width=True):
    for row in trustworthy:
        st.session_state["pick_" + row_key(row)] = True

if bulk[1].button("Clear selection", use_container_width=True):
    for row in rows:
        st.session_state["pick_" + row_key(row)] = False

if not trustworthy:
    bulk[2].caption(
        "No sold-comp suggestions available yet, so bulk selection is disabled. Every "
        "suggestion here is derived from asking prices, which is why each one needs "
        "your eye. Connect a sold-comp source to change that."
    )
else:
    bulk[2].caption(
        "Bulk selection only picks suggestions backed by completed sales and not "
        "flagged for review."
    )

for row in visible:
    offer_id = row["offer_id"]
    key = row_key(row)
    direction = "rp-up" if row["delta"] > 0 else "rp-down"
    basis_class = "rp-sold" if row.get("basis") == "sold" else "rp-ask"
    basis_text = "sold comps" if row.get("basis") == "sold" else "asking prices"

    with st.container(border=True):
        image_col, detail_col, action_col = st.columns([1, 4, 2])

        with image_col:
            if row.get("image"):
                st.image(row["image"], width=90)

        with detail_col:
            st.markdown(
                "**{}**<br>"
                '<span class="rp-badge {}">{:+.0f}%</span> &nbsp; '
                '<span class="rp-badge {}">{}</span> &nbsp; '
                "<span style='color:#888;font-size:0.78rem'>{}</span>".format(
                    row["title"][:88], direction, row["delta_pct"],
                    basis_class, basis_text, row.get("detail", "")[:70],
                ),
                unsafe_allow_html=True,
            )

            offers = (row.get("price_data") or {}).get("offers") or {}
            if offers.get("offer_sales"):
                st.caption("{} of {} comps sold via accepted offers{}".format(
                    offers["offer_sales"],
                    offers["offer_sales"] + offers.get("bin_sales", 0),
                    " (median ${:,.2f})".format(offers["offer_median"])
                    if offers.get("offer_median") else ""))

            age = row.get("repriced_days_ago")
            if age is not None:
                st.caption("last repriced {:.0f} day(s) ago".format(age))

            if row.get("needs_review"):
                st.warning("Check this one: " + "; ".join(row.get("review_reasons", [])))

            links = build_comp_links(row.get("card_data") or {})
            if links:
                link_cols = st.columns(len(links))
                for col, (label, url) in zip(link_cols, links):
                    col.link_button(label, url, use_container_width=True)

        with action_col:
            st.metric("Current", "${:,.2f}".format(row["price"]),
                      "{:+,.2f}".format(row["delta"]))
            if not keep_ratio and offer_id:
                st.caption("offer floor -> ${:,.2f}".format(
                    suggested_min_offer(float(st.session_state.get(
                        "new_" + key, row["suggested"])))))
            st.number_input("New price", min_value=0.99, step=0.50,
                            value=float(row["suggested"]), key="new_" + key)
            st.checkbox("Update this listing", key="pick_" + key)

            # For cards handled elsewhere - repriced in Seller Hub, or simply judged
            # fine as they are. The cooldown only knows about reprices made here.
            if st.button("Leave it - skip for {} days".format(cooldown or 14),
                         key="skip_" + key, use_container_width=True):
                record_reprice(key, row["price"], title=row.get("title", ""),
                               previous_price=row.get("price"))
                st.rerun()


# --------------------------------------------------------------------- auctions

st.divider()
st.subheader("Convert to auction")

st.caption(
    "eBay cannot change a live listing's format, so a conversion always ends the "
    "fixed-price listing and creates a new auction from the same card. Only listings "
    "CardFlow created can be converted - anything listed elsewhere has no offer here "
    "to rebuild from."
)

convertible = [row for row in rows if row.get("offer_id")]

if not convertible:
    st.caption("No convertible listings in the current view.")
else:
    labels = {
        "{} - ${:,.2f}".format(row["title"][:60], row["price"]): row
        for row in convertible
    }
    chosen_label = st.selectbox("Listing", ["-"] + sorted(labels))
    chosen = labels.get(chosen_label)

    if chosen:
        advice = recommend_start_price(chosen.get("price_data") or {}, chosen["price"])

        info_cols = st.columns(3)
        info_cols[0].metric("Current price", "${:,.2f}".format(chosen["price"]))
        info_cols[1].metric("Market", "${:,.2f}".format(advice["market_price"]))
        info_cols[2].metric("Suggested start", "${:,.2f}".format(advice["start_price"]),
                            "{:.0f}% of market".format(advice["ratio"] * 100))

        st.caption("Why: " + advice["reason"])
        if not advice["confident"]:
            st.warning("This suggestion is not based on solid sold comps. An auction "
                       "ends at whatever the room pays, so a low opener on a thin "
                       "market is a real risk.")

        auction_cols = st.columns(3)
        start_price = auction_cols[0].number_input(
            "Starting bid", min_value=0.99, step=0.50,
            value=float(advice["start_price"]), key="auction_start")
        duration = auction_cols[1].selectbox(
            "Duration", DURATIONS, index=DURATIONS.index(DEFAULT_DURATION))
        mode = auction_cols[2].radio(
            "Original listing",
            ["End it, keep the offer", "Delete it entirely"],
            help="Keeping the offer means the fixed-price listing can be republished if "
                 "the auction does not go up. Deleting removes it for good.",
        )

        delete_original = mode.startswith("Delete")

        st.warning(
            "This ends listing {} at ${:,.2f} and starts a {} auction at ${:,.2f}."
            .format(chosen.get("listing_id") or chosen["offer_id"], chosen["price"],
                    duration.replace("DAYS_", "") + "-day", start_price)
        )

        if st.checkbox("I understand this ends the live listing", key="confirm_auction"):
            if st.button("Convert to auction", type="primary"):
                notices.clear("auction")
                try:
                    with st.spinner("Ending the fixed-price listing and creating the auction..."):
                        result = convert_to_auction(
                            chosen["offer_id"], start_price,
                            duration=duration, delete_original=delete_original,
                        )
                    notices.post("auction", "success",
                                 "Auction live: listing {} starting at ${:,.2f} for {}."
                                 .format(result["listing_id"], result["start_price"],
                                         result["duration"].replace("DAYS_", "") + " days"))
                    for step in result["steps"]:
                        notices.post("auction", "caption", "- " + step)
                    notices.post("auction", "caption",
                                 "Re-run steps 1 and 2 to refresh the table.")
                except Exception as exc:
                    notices.post("auction", "error", str(exc))
                    notices.post("auction", "caption",
                                 "If the fixed-price listing was ended but the auction did "
                                 "not go up, the card is not listed. Re-publish it from the "
                                 "CardFlow page or Seller Hub.")

# Outside the listing picker: a converted listing can drop out of it.
notices.show("auction")


# ----------------------------------------------------------------------- apply

selected = [row for row in rows if st.session_state.get("pick_" + row_key(row))]

st.divider()
st.subheader("Apply price updates")

if not selected:
    st.caption("Tick the listings you want to reprice. Nothing is sent to eBay until you do.")
else:
    st.warning("{} listing(s) selected. This changes the price on live eBay listings."
               .format(len(selected)))

    if st.button("Update {} live listing(s) on eBay".format(len(selected)), type="primary"):
        notices.clear("apply")
        updated, failed = [], []
        bar = st.progress(0.0, text="Updating...")

        for index, row in enumerate(selected, start=1):
            offer_id = row["offer_id"]
            key = row_key(row)
            new_price = float(st.session_state.get("new_" + key, row["suggested"]))
            # Captured before the in-place correction below, which rewrites row["price"].
            old_price = float(row["price"])

            try:
                if offer_id:
                    update_offer_price(
                        offer_id, new_price,
                        min_offer_price=suggested_min_offer(new_price),
                        keep_existing_ratio=keep_ratio,
                    )
                else:
                    # Listed outside this app: no Inventory offer exists, so the price
                    # is changed through the legacy Trading API instead.
                    revise_listing_price(row["listing_id"], new_price)
            except Exception as exc:
                # eBay 500s intermittently on offer reads. The same listing can usually
                # still be revised through Trading, so try that before giving up.
                if offer_id and row.get("listing_id"):
                    try:
                        revise_listing_price(row["listing_id"], new_price)
                        notices.post("apply", "caption",
                                     "{}: Inventory API failed ({}), updated through the "
                                     "legacy API instead.".format(row["title"][:40], exc))
                    except Exception as fallback_exc:
                        failed.append((row["title"], "{} | fallback: {}".format(exc, fallback_exc)))
                        bar.progress(index / len(selected))
                        continue
                else:
                    failed.append((row["title"], str(exc)))
                    bar.progress(index / len(selected))
                    continue

            try:
                record_reprice(key, new_price, title=row.get("title", ""),
                               previous_price=old_price)

                # Bring the row up to date so it drops out of the movers list instead of
                # sitting there with a stale delta, inviting a second identical update.
                for source_row in state["priced"]:
                    if row_key(source_row) == key:
                        source_row["price"] = new_price
                        source_row["delta"] = round(
                            float(source_row.get("suggested") or new_price) - new_price, 2)
                        source_row["delta_pct"] = round(
                            (float(source_row.get("suggested") or new_price) / new_price - 1) * 100, 1
                        ) if new_price else 0.0
                        source_row["repriced_days_ago"] = 0.0

                # The tick box is deliberately not cleared here: Streamlit raises if a
                # widget's key is written after the widget has been drawn, and the row
                # drops out of the list on the next pass anyway.
                updated.append((row["title"], old_price, new_price))
            except Exception as exc:
                failed.append((row["title"], str(exc)))

            bar.progress(index / len(selected),
                         text="Updated {}/{}".format(index, len(selected)))

        bar.empty()

        if updated:
            notices.post("apply", "success", "Updated {} listing(s).".format(len(updated)))
            notices.post("apply", "table", [
                {"Listing": title[:60],
                 "Was": "${:,.2f}".format(old),
                 "Now": "${:,.2f}".format(new)}
                for title, old, new in updated
            ])

        for title, message in failed:
            notices.post("apply", "error", "{}: {}".format(title[:60], message))

        if updated:
            notices.post("apply", "caption",
                         "These rows are now up to date and have dropped out of the list. "
                         "They will be skipped for the next {} days.".format(cooldown))

# Outside the selection check: once updated, nothing is selected and that branch is gone.
notices.show("apply")
