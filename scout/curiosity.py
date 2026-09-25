"""Curiosities: cars Jason follows out of interest that are not serious
candidates (a $131k 911 Widebody). Tracked like any listing (synced,
availability-checked, a comp once sold) but off the candidates board and out
of every paid bulk assessment.

A car becomes a curiosity automatically when its price is over
state["curiosity_over_price"] (default $40,000), and returns to the
candidates if the price falls back under it. The price is the higher of the
listed price and the expected auction hammer from its assessment, so a live
auction's early bid does not hide a $60k car. A role set by hand wins."""
from __future__ import annotations

from typing import Any

from scout import db

DEFAULT_OVER_PRICE = 40_000
LIVE = ("active", "pending")


def reference_price(row: dict[str, Any], assessment: dict[str, Any] | None = None) -> int | None:
    """What the car will realistically cost: listed price, or the expected
    hammer when an assessment has one and it is higher."""
    prices = [row.get("price") or 0]
    c = (assessment or {}).get("costs") or {}
    if c.get("price_basis") in {"expected_hammer", "current_bid", "asking"}:
        prices.append(c.get("price") or 0)
    top = max(prices)
    return top or None


def desired_role(row: dict[str, Any], assessment: dict[str, Any] | None, state: dict[str, Any]) -> str | None:
    """"curiosity" / "candidate" when the price rule wants a change, else None."""
    if row.get("role") not in ("candidate", "curiosity") or row.get("availability") not in LIVE or row.get("role_user_set"):
        return None
    over = state.get("curiosity_over_price", DEFAULT_OVER_PRICE)
    price = reference_price(row, assessment)
    want = "curiosity" if (over and price and price > over) else "candidate"
    return want if want != row["role"] else None


def sync(listing_id: int, state: dict[str, Any] | None = None) -> str | None:
    """Apply the price rule to one listing. Returns the new role when it changed."""
    from scout.policy.state import load_state
    row = db.get_listing(listing_id)
    if not row:
        return None
    state = state or load_state()
    a = db.latest_assessment(listing_id)
    want = desired_role(row, a, state)
    if not want:
        return None
    db.update_listing(listing_id, {"role": want})
    over = state.get("curiosity_over_price", DEFAULT_OVER_PRICE)
    db.log_event("role_changed", listing_id,
                 f"{row['role']} -> {want}: ${reference_price(row, a) or 0:,} is "
                 f"{'over' if want == 'curiosity' else 'at or under'} the ${over:,} curiosity line")
    return want


def sync_all(state: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    from scout.policy.state import load_state
    state = state or load_state()
    changed = []
    for r in db.list_listings():
        if r["role"] in ("candidate", "curiosity"):
            new = sync(r["id"], state)
            if new:
                changed.append({"id": r["id"], "title": r.get("title"), "role": new})
    return changed
