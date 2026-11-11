"""Promote: listings that have sat unsold, and what to do about each one."""

import sys
from pathlib import Path
from urllib.parse import quote

import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.promote import (
    ACTION_PRICE, ACTION_PROMOTE, ACTION_RETHINK, ACTION_WAIT, stale_listings,
)
from core.reprice import suggest_for_listings
from services import ebay_marketing as marketing
from services.comp_links import build_comp_links
from services.ebay_auction import (
    DEFAULT_DURATION, DURATIONS, convert_to_auction, net_after_fees, recommend_start_price,
)
from services.ebay_listings import fetch_active_listings, is_auction

st.set_page_config(page_title="CardFlow - Promote", page_icon="megaphone", layout="wide")

SELLER_HUB_PROMOTIONS = "https://www.ebay.com/sh/mkt/ad-rate-strategy"

BADGES = {
    ACTION_PROMOTE: ("#1f6f9c", "rgba(45,156,219,0.18)", "PROMOTE"),
    ACTION_PRICE: ("#9a7d0a", "rgba(241,196,15,0.20)", "REPRICE FIRST"),
    ACTION_RETHINK: ("#c0392b", "rgba(235,87,87,0.18)", "RETHINK"),
    ACTION_WAIT: ("#777", "rgba(120,120,120,0.18)", "WAIT"),
}

st.title("Promote")
st.caption("A card that has not sold is telling you one of two things: nobody is seeing "
           "it, or the people seeing it think it costs too much. Promotion fixes the "
           "first and makes the second worse.")

state = st.session_state
state.setdefault("promote_listings", [])
state.setdefault("promote_priced", {})
state.setdefault("promote_ads", {})      # listing id -> ad rate in the CardFlow campaign
state.setdefault("promote_notes", {})    # row key -> (kind, message) from the last action
state.setdefault("promote_converted", set())

can_promote = marketing.has_marketing_scope()


def row_key(row) -> str:
    return row.get("offer_id") or row.get("listing_id") or row.get("title", "")


def note(row, kind: str, message: str) -> None:
    state["promote_notes"][row_key(row)] = (kind, message)


def load_ads() -> None:
    try:
        state["promote_ads"] = marketing.current_ads()
    except Exception as exc:
        st.warning("Could not read your promoted listings: {}".format(exc))


def promote(rows_and_rates) -> None:
    rates = {row["listing_id"]: rate for row, rate in rows_and_rates if row.get("listing_id")}
    try:
        outcome = marketing.promote_listings(rates)
    except Exception as exc:
        for row, _ in rows_and_rates:
            note(row, "error", "Not promoted: {}".format(exc))
        return
    for row, _ in rows_and_rates:
        result = outcome.get(str(row.get("listing_id")), {"ok": False, "error": "no listing id"})
        if result["ok"]:
            state["promote_ads"][str(row["listing_id"])] = result["rate"]
            note(row, "success", "Promoted at {:.1f}%.".format(result["rate"]))
        else:
            note(row, "error", "Not promoted: " + result["error"])

with st.sidebar:
    st.header("Load")
    max_listings = st.number_input("Live listings to load", 10, 1000, 100, step=10)
    workers = st.slider("Parallel workers", 1, 10, 6)

    if st.button("Load live listings", type="primary", use_container_width=True):
        bar = st.progress(0.0, text="Reading listings...")
        try:
            state["promote_listings"] = fetch_active_listings(
                max_listings=int(max_listings), workers=workers,
                on_progress=lambda found, scanned: bar.progress(
                    min(found / max(int(max_listings), 1), 1.0),
                    text="Found {} listing(s)".format(found)),
            )
            state["promote_priced"] = {}
            state["promote_notes"] = {}
            state["promote_converted"] = set()
        except Exception as exc:
            st.error("Could not load listings: {}".format(exc))
        bar.empty()
        if can_promote:
            load_ads()

    st.divider()
    st.header("Promoted Listings")
    if can_promote:
        st.caption("Ads go in a campaign named '{}', cost-per-sale: the rate is charged "
                   "only when a buyer clicks the ad and buys within 30 days."
                   .format(marketing.CAMPAIGN_NAME))
        st.caption("{} listing(s) promoted by CardFlow.".format(len(state["promote_ads"])))
    else:
        st.warning("Promoting from here needs eBay's marketing permission. Run "
                   "`python tools/ebay_consent.py` once, approve it, then restart CardFlow.")

    st.divider()
    st.header("Filter")
    min_days = st.slider("Unsold for at least (days)", 0, 180, 30, step=5)
    only_actionable = st.checkbox("Hide 'wait' listings", value=True)

if not state["promote_listings"]:
    st.info("Press **Load live listings** to begin. Nothing is changed on eBay from "
            "this page - it only tells you where to look.")
    st.stop()

rows = stale_listings(state["promote_listings"], min_days=min_days,
                      priced=state["promote_priced"])

if only_actionable:
    rows = [row for row in rows if row["action"] != ACTION_WAIT]

counts = {action: len([r for r in rows if r["action"] == action]) for _, action in
          [(0, ACTION_PROMOTE), (1, ACTION_PRICE), (2, ACTION_RETHINK)]}

summary = st.columns(4)
summary[0].metric("Listings loaded", len(state["promote_listings"]))
summary[1].metric("Worth promoting", counts[ACTION_PROMOTE])
summary[2].metric("Reprice first", counts[ACTION_PRICE])
summary[3].metric("Long unsold", counts[ACTION_RETHINK])

st.caption(
    "Ad rates below are a ladder against how long a card has sat. eBay shows its own "
    "suggested rate per category when you set a campaign up - prefer that number."
)

suggested = [row for row in rows if row["action"] == ACTION_PROMOTE and row.get("listing_id")
             and str(row["listing_id"]) not in state["promote_ads"]
             and row_key(row) not in state["promote_converted"]]
if can_promote and suggested:
    bulk_cols = st.columns([3, 2])
    confirm_bulk = bulk_cols[0].checkbox(
        "Promote all {} 'worth promoting' listing(s) at their suggested rates "
        "(about ${:,.2f} in fees if every one sells through an ad)".format(
            len(suggested), sum((r["price"] or 0) * r["ad_rate"] / 100 for r in suggested)))
    if bulk_cols[1].button("Promote {} listing(s)".format(len(suggested)), type="primary",
                           disabled=not confirm_bulk, use_container_width=True):
        with st.spinner("Promoting..."):
            promote([(row, row["ad_rate"]) for row in suggested])
        st.rerun()

if state["promote_listings"] and not state["promote_priced"]:
    st.info("Suggestions improve if the comps are known: a card priced well above its "
            "sold median needs a price cut, not an ad.")
    if st.button("Check prices for these {} listing(s)".format(len(rows))):
        bar = st.progress(0.0, text="Pricing...")
        priced = suggest_for_listings(
            rows, workers=workers,
            on_progress=lambda done, total: bar.progress(done / max(total, 1)))
        state["promote_priced"] = {
            (row.get("offer_id") or row.get("listing_id") or ""): row.get("price_data") or {}
            for row in priced
        }
        bar.empty()
        st.rerun()

st.divider()

page_size = st.selectbox("Rows per page", [10, 25, 50], index=0)
total_pages = max(1, (len(rows) + page_size - 1) // page_size)
page = st.number_input("Page", 1, total_pages, 1, step=1)
start = (page - 1) * page_size
visible = rows[start:start + page_size]

st.caption("Showing {}-{} of {} listing(s), longest unsold first."
           .format(start + 1, start + len(visible), len(rows)))

for row in visible:
    colour, background, label = BADGES.get(row["action"], BADGES[ACTION_WAIT])

    with st.container(border=True):
        image_col, detail_col, action_col = st.columns([1, 4, 2])

        with image_col:
            if row.get("image"):
                st.image(row["image"], width=90)

        with detail_col:
            st.markdown(
                "**{}**<br>"
                '<span style="background:{};color:{};padding:1px 8px;border-radius:999px;'
                'font-size:0.72rem;font-weight:700">{}</span> &nbsp; '
                "<span style='color:#888;font-size:0.8rem'>{} days listed &middot; "
                "{} watcher(s)</span>".format(
                    row["title"][:88], background, colour, label,
                    row["days_listed"], row["watchers"]),
                unsafe_allow_html=True,
            )
            st.caption(row["reason"])

            links = build_comp_links(row.get("card_data") or {})
            if links:
                link_cols = st.columns(len(links))
                for col, (link_label, url) in zip(link_cols, links):
                    col.link_button(link_label, url, use_container_width=True)

        key = row_key(row)
        converted = key in state["promote_converted"]
        listing_id = str(row.get("listing_id") or "")
        promoted_rate = state["promote_ads"].get(listing_id)

        with action_col:
            st.metric("Price", "${:,.2f}".format(row["price"] or 0))
            if row["ad_rate"]:
                st.metric("Suggested ad rate", "{:.0f}%".format(row["ad_rate"]),
                          "${:,.2f} per sale".format((row["price"] or 0) * row["ad_rate"] / 100))

            if row.get("view_url"):
                st.link_button("View listing", row["view_url"], use_container_width=True)

            if converted:
                pass
            elif not can_promote:
                st.link_button("Promote in Seller Hub", SELLER_HUB_PROMOTIONS,
                               use_container_width=True)
            elif not listing_id:
                st.caption("No live listing id, so it cannot be promoted.")
            else:
                if promoted_rate is not None:
                    st.success("Promoted at {:.1f}%".format(promoted_rate))
                rate = st.number_input(
                    "Ad rate %", min_value=marketing.MIN_AD_RATE, max_value=marketing.MAX_AD_RATE,
                    step=0.5, key="rate_" + key,
                    value=float(promoted_rate or row["ad_rate"] or 5.0))
                st.caption("${:,.2f} if it sells through an ad".format(
                    (row["price"] or 0) * rate / 100))
                if st.button("Update rate" if promoted_rate is not None else "Promote",
                             key="promote_" + key, use_container_width=True,
                             type="secondary" if promoted_rate is not None else "primary"):
                    with st.spinner("Promoting..."):
                        promote([(row, rate)])
                    st.rerun()
                if promoted_rate is not None and st.button(
                        "Stop promoting", key="unpromote_" + key, use_container_width=True):
                    try:
                        result = marketing.stop_promoting([listing_id]).get(listing_id, {})
                        if result.get("ok"):
                            state["promote_ads"].pop(listing_id, None)
                            note(row, "success", "No longer promoted.")
                        else:
                            note(row, "error", "Could not stop: " + result.get("error", ""))
                    except Exception as exc:
                        note(row, "error", "Could not stop: {}".format(exc))
                    st.rerun()

        kind, message = state["promote_notes"].get(key, ("", ""))
        if message:
            getattr(st, kind if kind in ("success", "error", "warning") else "info")(message)

        # Converting only works for listings CardFlow made: a Seller Hub listing has no
        # Inventory API offer to withdraw and rebuild from.
        if not converted and row.get("offer_id") and not is_auction(row):
            with st.expander("Convert to auction"):
                advice = recommend_start_price(
                    state["promote_priced"].get(key) or {}, row["price"] or 0)
                st.caption("Suggested opening bid ${:,.2f}: {}".format(
                    advice["start_price"], advice["reason"]))
                auction_cols = st.columns(3)
                start_price = auction_cols[0].number_input(
                    "Starting bid", min_value=0.99, step=0.50,
                    value=float(advice["start_price"]), key="start_" + key)
                duration = auction_cols[1].selectbox(
                    "Duration", DURATIONS, index=DURATIONS.index(DEFAULT_DURATION),
                    format_func=lambda d: d.replace("DAYS_", "") + " days", key="dur_" + key)
                auction_cols[2].metric("You net at the opening bid",
                                       "${:,.2f}".format(net_after_fees(float(start_price))))
                st.warning(
                    "This ends the ${:,.2f} Buy It Now listing and starts a {}-day auction at "
                    "${:,.2f}. Its {} watcher(s){} do not carry over, and one bidder means "
                    "the opening bid is the sale price.".format(
                        row["price"] or 0, duration.replace("DAYS_", ""), start_price,
                        row["watchers"], " and its promotion" if promoted_rate is not None else ""))
                if st.checkbox("I understand this ends the live listing", key="confirm_" + key):
                    if st.button("Convert to auction", key="convert_" + key, type="primary"):
                        try:
                            with st.spinner("Ending the listing and starting the auction..."):
                                result = convert_to_auction(row["offer_id"], start_price,
                                                            duration=duration)
                            state["promote_converted"].add(key)
                            state["promote_ads"].pop(listing_id, None)
                            note(row, "success", "Now an auction: listing {} opening at ${:,.2f}, "
                                 "{} days. The old offer is kept in case you want it back."
                                 .format(result["listing_id"], result["start_price"],
                                         result["duration"].replace("DAYS_", "")))
                        except Exception as exc:
                            note(row, "error", "{} If the Buy It Now listing was ended but the "
                                 "auction did not go up, the card is not listed: republish it "
                                 "from Seller Hub.".format(exc))
                        st.rerun()

st.divider()
st.caption(
    "Promoting here uses eBay's general Promoted Listings (cost per sale): you pay the ad "
    "rate only when a buyer clicks the ad and buys within 30 days. Ads CardFlow places live "
    "in the '{}' campaign, which you can also see and change in Seller Hub."
    .format(marketing.CAMPAIGN_NAME)
)
