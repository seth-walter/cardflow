"""Result messages that stay on screen until dismissed.

Anything drawn with st.success/st.warning lives for one run only. After a publish the
session changes, the live_sync poll redraws the page within two seconds, and the
summary, Discord rows and warnings vanish before they can be read. Messages posted here
are kept in session state and redrawn on every run until the seller dismisses them or
the same action runs again.
"""

import streamlit as st

MESSAGE_KINDS = {"success", "info", "warning", "error", "caption"}


def _store():
    return st.session_state.setdefault("_notices", {})


def clear(area: str) -> None:
    _store().pop(area, None)


def post(area: str, kind: str, payload, label: str = "") -> None:
    """Keep a message for `area`. kind: success/info/warning/error/caption, "rows"
    (copyable text lines, `label` heads them) or "table" (a list of dicts)."""
    _store().setdefault(area, []).append((kind, payload, label))


def show(area: str) -> None:
    items = _store().get(area)
    if not items:
        return
    for index, (kind, payload, label) in enumerate(items):
        if kind in MESSAGE_KINDS:
            getattr(st, kind)(payload)
        elif kind == "rows":
            st.text_area(label or "Copy/paste these rows:", value="\n".join(payload),
                         height=220, key="notice_{}_{}".format(area, index))
        elif kind == "table":
            st.table(payload)
    if st.button("Dismiss", key="dismiss_notices_" + area):
        clear(area)
        st.rerun()
