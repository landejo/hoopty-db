"""Availability check: is a tracked listing still for sale?

The extension opens each listing page in the user's own browser (logged in, so
Facebook and dealer sites render normally) and posts what it read here. We
classify it deterministically first; only a page with no clear signal goes to
the fast model, whose answer counts only when it quotes the page verbatim.
A listing that is sold, delisted ("no longer available") or ended becomes a
market comp, its status moves to Sold / Ended, and the policy engine's
availability gate turns its verdict to Do not pursue."""
from __future__ import annotations

import re
from typing import Any

from scout import db
from scout.config import CONFIG

CHECKABLE_SITES = {"facebook", "cargurus", "carscom", "autotrader", "carsandbids", "bat"}
# Block pages automated browsers were served in the 2026-09-24 E2E runs: Cloudflare
# (Cars.com), Akamai (Autotrader), DataDome (CarGurus). They say nothing about the car.
BLOCK_PAGE_RE = re.compile(r"you have been blocked|access denied|site is currently unavailable.*incident number|^var dd=\{", re.I | re.S)
MIN_PAGE_TEXT = 120        # less than this is an empty / placeholder page, not a listing
HEAD_CHARS = 2500          # only the top of the page carries the listing's own status
KEEP_STATUSES = {"Purchased"}   # the user bought it: never overwrite

_MONEY = r"(?:USD\s*)?\$\s?([\d,]{3,})"
SOLD_PRICE_RE = re.compile(r"\bsold\s+for\s*:?\s*" + _MONEY, re.I)
BID_TO_RE = re.compile(r"\bbid\s+to\s*:?\s*" + _MONEY, re.I)
RESERVE_NOT_MET_RE = re.compile(r"\breserve\s+not\s+met\b", re.I)
SOLD_TEXT_RE = re.compile(r"\b(this (listing|vehicle|car|item) (is|has been|was) sold|this car sold)\b", re.I)
SOLD_LINE_RE = re.compile(r"^\s*sold\s*$", re.I | re.M)          # Facebook's status line
UNAVAILABLE_RE = re.compile(
    r"\b(no longer available|(isn't|is not|isn’t) available (anymore|any more|right now)"
    r"|this listing (has been|was) (removed|deleted)|listing (is )?unavailable"
    r"|vehicle (is )?no longer (listed|for sale)"
    r"|looks like (that|this) one got away"   # CarGurus' sold page (seen 2026-09-24)
    r"|(this|the) car has already found a new home)\b", re.I)   # Autotrader's (seen 2026-09-25)
PENDING_RE = re.compile(r"\b(sale pending|deposit (taken|received))\b|^\s*pending\s*$", re.I | re.M)
LIVE_AUCTION_RE = re.compile(r"\b(current bid|high bid|time left|ends in|place (a )?bid)\b", re.I)
# What a live listing shows and a sold/removed one does not (read off real pages 2026-09-24).
# Checked only after every sold/unavailable pattern has failed.
LIVE_LISTING_RE = {
    "facebook": re.compile(r"^\s*Message\s*$", re.M),     # the "Message" seller button
    "cargurus": re.compile(r"\bCheck availability\b"),
    "autotrader": re.compile(r"\bListing Price\b"),
}


def _money(s: str) -> int | None:
    try:
        v = int(s.replace(",", ""))
    except ValueError:
        return None
    return v if 0 < v < 2_000_000 else None


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "")).strip().lower()


def classify_deterministic(site: str, detail: dict[str, Any]) -> dict[str, Any] | None:
    """A clear status from the page head, or None when the page gives no strong
    signal (the common case for a live listing). Result keys: availability
    (sold | ended | unavailable | pending | active), evidence, price."""
    head = ((detail.get("bid_label") or "") + " " + (detail.get("bid_text") or "") + "\n" +
            (detail.get("text") or "")[:HEAD_CHARS])
    m = SOLD_PRICE_RE.search(head)
    if m:
        return {"availability": "sold", "evidence": m.group(0), "price": _money(m.group(1))}
    m = BID_TO_RE.search(head)
    if m and site in {"bat", "carsandbids"}:
        return {"availability": "ended", "evidence": m.group(0), "price": _money(m.group(1)),
                "reserve_not_met": True}
    if RESERVE_NOT_MET_RE.search(head) and site in {"bat", "carsandbids"}:
        return {"availability": "ended", "evidence": "Reserve not met", "price": None, "reserve_not_met": True}
    m = SOLD_TEXT_RE.search(head)
    if m:
        return {"availability": "sold", "evidence": m.group(0), "price": None}
    if site == "facebook":
        m = SOLD_LINE_RE.search(head[:1500])
        if m:
            return {"availability": "sold", "evidence": "status line reads \"Sold\"", "price": None}
    if site == "facebook" and "unavailable_product=1" in (detail.get("page_url") or ""):
        # Facebook redirects a gone listing to /marketplace/<city>/?unavailable_product=1 (seen 2026-09-24)
        return {"availability": "unavailable", "evidence": "Facebook redirected to unavailable_product=1", "price": None}
    m = UNAVAILABLE_RE.search(head)
    if m:
        return {"availability": "unavailable", "evidence": m.group(0), "price": None}
    m = PENDING_RE.search(head[:1500])
    if m:
        return {"availability": "pending", "evidence": m.group(0).strip(), "price": None}
    if site in {"bat", "carsandbids"} and LIVE_AUCTION_RE.search(head):
        return {"availability": "active", "evidence": LIVE_AUCTION_RE.search(head).group(0), "price": None}
    live = LIVE_LISTING_RE.get(site)
    if live and live.search(head) and detail.get("is_detail_page", True):
        return {"availability": "active", "evidence": f"live listing ({live.search(head).group(0).strip()})", "price": None}
    return None


_SYSTEM = """You check whether ONE used-car listing page shows the car is still for sale.
Return only JSON: {"availability": "active|pending|sold|ended|unavailable|unclear",
"evidence": "<an exact quote from the page text, at most 200 characters>", "sold_price": <integer or null>}
Rules:
- sold: the page says THIS car sold. unavailable: the page says this listing is gone,
  removed or no longer available without saying it sold. ended: an auction that
  closed without a sale (bid to / reserve not met). pending: sale pending / deposit taken.
- active: the page is the listing itself and shows it for sale (price, contact/message
  seller, live bidding), with no sold/removed notice.
- Boilerplate is not a status: "similar cars sold", "recently sold", "sold by <dealer>",
  "mark as sold", site navigation, recommendations.
- If the page is a login, search-results, home or error page that does not state
  what happened to this listing, answer unclear.
- evidence MUST be copied verbatim from the page text. If you cannot quote it, answer unclear."""


def classify_with_model(listing: dict[str, Any], detail: dict[str, Any]) -> dict[str, Any] | None:
    """Fast-model read for pages with no deterministic signal. The answer is
    discarded unless its evidence quote actually appears in the page."""
    if not CONFIG.ai_enabled:
        return None
    text = (detail.get("text") or "")[:6000]
    if len(text.strip()) < 40:
        return None
    from scout import coerce
    from scout.ai import call_text
    user = (f"Listing we are tracking: {listing.get('title') or ''} ({listing.get('url')})\n"
            f"Page URL after loading: {detail.get('page_url') or '?'}\n"
            f"Page title: {detail.get('title') or ''}\n"
            f"Adapter says this is {'still' if detail.get('is_detail_page', True) else 'NOT'} a listing page.\n\n"
            f"PAGE TEXT (top):\n{text}")
    try:
        out = call_text(CONFIG.model_fast, _SYSTEM, user, max_tokens=400, log_name="last_availability",
                        listing_id=listing.get("id"))
        data = coerce.parse_json(out)
    except Exception as e:
        db.log_event("availability_error", listing.get("id"), f"model: {e}")
        return None
    if not isinstance(data, dict):
        return None
    avail = data.get("availability")
    ev = str(data.get("evidence") or "")
    if avail not in {"active", "pending", "sold", "ended", "unavailable"}:
        return {"availability": "unclear", "evidence": ev[:200], "price": None, "method": "model"}
    if not ev or _norm(ev) not in _norm(detail.get("text") or ""):
        return {"availability": "unclear", "evidence": f"model said {avail} but its quote is not on the page",
                "price": None, "method": "model"}
    price = data.get("sold_price")
    return {"availability": avail, "evidence": ev[:200], "method": "model",
            "price": price if isinstance(price, int) and 0 < price < 2_000_000 else None}


def classify(listing: dict[str, Any], detail: dict[str, Any] | None) -> dict[str, Any]:
    detail = detail or {}
    if detail.get("error"):
        return {"availability": "unclear", "evidence": f"page did not load: {str(detail['error'])[:160]}", "method": "none"}
    from scout.ingest import is_blocked
    if is_blocked(detail) or BLOCK_PAGE_RE.search((detail.get("text") or "")[:600]):
        return {"availability": "unclear", "evidence": "bot wall; re-check later", "method": "none"}
    det = classify_deterministic(listing["site"], detail)
    if det:
        return {**det, "method": "page"}
    if len((detail.get("text") or "").strip()) < MIN_PAGE_TEXT:
        # Seen 2026-09-24: CarGurus serves automated browsers an empty page. No text is not "no sold notice".
        return {"availability": "unclear", "evidence": "page had no readable text", "method": "none"}
    got = classify_with_model(listing, detail)
    if got:
        return got
    if not detail.get("is_detail_page", True):
        return {"availability": "unclear", "evidence": "page redirected away from the listing", "method": "none"}
    if listing["site"] in LIVE_LISTING_RE:
        # We know what a live page on this site shows; seeing neither that nor a sold notice means we did not see the listing.
        return {"availability": "unclear", "evidence": "no sold notice and no live-listing marker on the page", "method": "none"}
    # No notice either way. Treated as live, but it is not positive evidence
    # (method "default"), so it never pulls a comp back onto the board.
    return {"availability": "active", "evidence": "listing page loaded with no sold/removed notice", "method": "default"}


def targets(limit: int | None = None) -> list[dict[str, Any]]:
    """Listings worth checking, never-checked / least-recently-checked first:
    live candidates, candidates that vanished from a saved list (to learn
    whether they sold), and comps still recorded as live."""
    rows = []
    for r in db.list_listings():
        if r["site"] not in CHECKABLE_SITES or r["role"] == "ignored":
            continue
        live = r["availability"] in ("active", "pending")
        vanished = r["role"] == "candidate" and r["availability"] == "removed"
        if live or vanished:
            rows.append(r)
    rows.sort(key=lambda r: ((r.get("raw") or {}).get("availability_check") or {}).get("checked_at") or "")
    out = [{"id": r["id"], "url": r["url"], "site": r["site"], "title": r.get("title")} for r in rows]
    return out[:limit] if limit else out


OFF_MARKET_STATUSES = {"Sold": "sold", "Ended": "ended"}
# Your own "Do not pursue" whose reason says the car is gone (not "too expensive").
OFF_MARKET_REASON_RE = re.compile(r"no longer (available|for sale|listed)|\bunavailable\b|\bsold\b|\bgone\b|delisted|"
                                  r"\bremoved\b|auction ended|reserve not met", re.I)


def user_says_off_market(row: dict[str, Any]) -> str | None:
    """"sold" / "ended" when you have said the car is off the market, else None."""
    if row.get("status") in OFF_MARKET_STATUSES:
        return OFF_MARKET_STATUSES[row["status"]]
    reason = row.get("verdict_override_reason") or ""
    if row.get("verdict_override") == "Do not pursue" and OFF_MARKET_REASON_RE.search(reason):
        return "ended" if re.search(r"auction ended|reserve not met", reason, re.I) and not re.search(r"\bsold\b", reason, re.I) else "sold"
    return None


def mark_off_market_by_user(listing_id: int) -> bool:
    """You marked the car Sold / Ended, or gave it your own "Do not pursue" because it
    is gone: same outcome as the availability check finding it (a comp, off the
    candidates board, gated to Do not pursue). The role counts as yours, so a later
    sync or a live-looking page never puts it back."""
    row = db.get_listing(listing_id)
    want = user_says_off_market(row or {})
    if not want or (row["availability"] in ("sold", "ended") and row["role"] in ("comp", "ignored")):
        return False
    updates: dict[str, Any] = {"availability": want, "role_user_set": 1}
    if row["role"] != "ignored":
        updates["role"] = "comp"
    db.update_listing(listing_id, updates)
    db.add_snapshot(listing_id, row.get("sold_price") or row.get("price"), row.get("price_kind"), want, None)
    db.log_event("availability_changed", listing_id,
                 f"{row['availability']}/{row['role']} -> {want}/{updates.get('role', row['role'])}: "
                 + (f"status set to {row['status']} by hand" if row.get("status") in OFF_MARKET_STATUSES
                    else f"your verdict: {row.get('verdict_override_reason')}"))
    return True


def apply(listing_id: int, detail: dict[str, Any] | None) -> dict[str, Any]:
    """Classify one checked page and write the outcome. Returns a summary with
    `changed` true when availability/role/status moved."""
    row = db.get_listing(listing_id)
    if not row:
        return {"id": listing_id, "changed": False, "result": "missing"}
    res = classify(row, detail)
    avail = res["availability"]
    raw = dict(row.get("raw") or {})
    raw["availability_check"] = {"checked_at": db.now(), "result": avail, "evidence": res.get("evidence"),
                                 "method": res.get("method"),
                                 # what the page actually said, for auditing a decision later (never published)
                                 "page_url": (detail or {}).get("page_url"),
                                 "page_head": re.sub(r"\s+", " ", ((detail or {}).get("text") or ""))[:300]}
    updates: dict[str, Any] = {"raw": raw}
    if avail != "unclear":
        updates["last_seen"] = db.now()   # we saw the page; never bump it for a bot wall or failed load
    changed = False
    if avail in {"sold", "unavailable", "ended"}:
        new_avail = "ended" if avail == "ended" else "sold"
        if row["availability"] != new_avail or row["role"] not in ("comp", "ignored"):
            changed = True
        updates["availability"] = new_avail
        if row["role"] != "ignored":
            updates["role"] = "comp"
        price = res.get("price")
        if new_avail == "sold" and price:
            updates["sold_price"] = price
            updates["price_kind"] = "sold"
        elif new_avail == "ended" and price:
            updates["price"] = price
            updates["price_kind"] = "reserve_not_met"
        raw["availability_check"]["sale_evidence"] = ("explicit" if avail == "sold" else
                                                      "delisted" if avail == "unavailable" else "auction ended")
        status = "Ended" if new_avail == "ended" else "Sold"
        if (row.get("status") or "New") not in KEEP_STATUSES and row.get("status") != status:
            updates["status"] = status
            changed = True
    elif avail in {"active", "pending"} and row["role"] == "comp" and row["availability"] in ("active", "pending") \
            and not row.get("role_user_set") and res.get("method") != "default":
        # A comp whose page is live was a misread (e.g. "sold" elsewhere on the page): back to candidate.
        updates["role"] = "candidate"
        updates["availability"] = avail
        changed = True
    elif avail == "pending" and row["availability"] == "active":
        updates["availability"] = "pending"
        changed = True
    elif avail == "active" and row["availability"] == "pending":
        updates["availability"] = "active"
        changed = True
    db.update_listing(listing_id, updates)
    if changed:
        from scout.curiosity import sync as sync_curiosity
        sync_curiosity(listing_id)   # a rescued comp may be over the curiosity line
        after = db.get_listing(listing_id)
        db.add_snapshot(listing_id, after.get("sold_price") or after.get("price"), after.get("price_kind"),
                        after.get("availability"), None)
        db.log_event("availability_changed", listing_id,
                     f"{row['availability']}/{row['role']}/{row.get('status')} -> "
                     f"{after['availability']}/{after['role']}/{after.get('status')}: {res.get('evidence')}")
    return {"id": listing_id, "title": row.get("title"), "result": avail, "changed": changed,
            "evidence": res.get("evidence"), "method": res.get("method"), "profile_key": row.get("profile_key")}


def summarize(results: list[dict[str, Any]]) -> str:
    changed = [r for r in results if r.get("changed")]
    unclear = sum(1 for r in results if r.get("result") == "unclear")
    s = f"{len(results)} checked, {len(changed)} changed"
    if unclear:
        s += f", {unclear} unclear"
    return s

