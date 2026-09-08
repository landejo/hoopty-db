"""Evidence gaps: what is missing on each live candidate, whether a DOCUMENT
could settle it or only an INSPECTION can, and what to ask for.

    .venv/bin/python -m scout.evidence          # ranked report
    .venv/bin/python -m scout.evidence 83       # the request pack for one car
"""
from __future__ import annotations

import re
import sys
from typing import Any

from scout import db

# A history report or service records can settle these.
_DOCUMENT = re.compile(
    r"timing_belt|cooling|coolant|head_history|rod_bearing|bearings|water_pump|"
    r"service|records|maintenance|major_service|accident|damage_repair|collision|"
    r"secondary_air|air_injection|leaks|recall|ownership|title|overrev|dme", re.I)
# Only eyes, a lift, a scan tool or a specialist can settle these.
_INSPECTION = re.compile(
    r"borescope|subframe|rear_structure|spot_weld|differential_mount|rust|underbody|"
    r"corrosion|tire|photo|video|top_cycle|exhaust|mod_|reversib|kdss|suspension|"
    r"warning_light|window|clutch|smog|tune", re.I)

DOC_MAX = 25          # documentation is 25 of the 100 points
GAIN_PER_ITEM = 5     # observed on the GX470: documents moved documentation 10 -> 20


def classify(item_key: str, label: str = "") -> str:
    """document | inspection | both — what could actually resolve this item."""
    blob = f"{item_key} {label}"
    doc, insp = bool(_DOCUMENT.search(blob)), bool(_INSPECTION.search(blob))
    if doc and insp:
        return "both"
    return "document" if doc else "inspection"


def gaps(listing_id: int, path=None) -> dict[str, Any] | None:
    l = db.get_listing(listing_id, path)
    if not l:
        return None
    a = db.latest_assessment(listing_id, path)
    prof = db.get_profile(l["profile_key"], path) if l.get("profile_key") else None
    labels = {c["key"]: c.get("label", "") for c in (prof or {}).get("critical_evidence") or []}
    crit = (a or {}).get("evidence", {}).get("critical_evidence") or []
    unresolved = [c for c in crit if c.get("status") in ("missing", "claimed_only")]
    by = {"document": [], "inspection": [], "both": []}
    for c in unresolved:
        by[classify(c["key"], labels.get(c["key"], ""))].append(
            {"key": c["key"], "status": c["status"], "label": labels.get(c["key"], c["key"]), "note": c.get("evidence", "")})
    doc_score = (a or {}).get("score", {}).get("documentation", 0)
    n_doc = len(by["document"]) + len(by["both"])
    headroom = max(0, DOC_MAX - doc_score)
    est_gain = min(headroom, GAIN_PER_ITEM * n_doc) if n_doc else 0
    docs = db.list_documents(listing_id, path)
    return {
        "id": listing_id, "title": l.get("title"), "url": l.get("url"), "site": l["site"],
        "price": l.get("price"), "status": l.get("status"), "vin": l.get("vin"),
        "score": (a or {}).get("score", {}).get("total"), "documentation": doc_score,
        "confidence": (a or {}).get("confidence"), "assessed": bool(a),
        "documents": [{"kind": d["kind"], "chars": len(d["text"])} for d in docs],
        "resolvable_by_document": by["document"] + by["both"], "inspection_only": by["inspection"],
        "estimated_score_gain": est_gain,
        "potential_score": ((a or {}).get("score", {}).get("total") or 0) + est_gain,
        "blocked_on_vin": not l.get("vin"),
        "seller_questions": (a or {}).get("evidence", {}).get("seller_questions", [])[:6],
    }


def request_message(listing_id: int, path=None) -> str:
    """A short, specific records request to send the seller."""
    g = gaps(listing_id, path)
    if not g:
        return ""
    l = db.get_listing(listing_id, path)
    car = l.get("title") or f"{l.get('year')} {l.get('make')} {l.get('model')}"
    asks: list[str] = []
    if g["blocked_on_vin"]:
        asks.append("the VIN, so I can run the history report myself")
    asks.append("a Carfax or AutoCheck report if you have one")
    seen = set()
    for item in g["resolvable_by_document"]:
        label = item["label"].split("(")[0].strip().rstrip(":")
        short = label[:110]
        if short.lower() in seen:
            continue
        seen.add(short.lower())
        verb = "the dated invoice for" if item["status"] == "claimed_only" else "any records covering"
        asks.append(f"{verb} {short.lower()}")
    body = (f"Hi — I'm seriously interested in the {car} and I'd like to move quickly.\n\n"
            "Could you send whatever you have of the following? Photos of the paperwork are fine.\n\n"
            + "\n".join(f"{i+1}. {a[0].upper() + a[1:]}" for i, a in enumerate(asks[:8])))
    if g["inspection_only"]:
        items = ", ".join(x["label"].split("(")[0].strip().rstrip(":").lower() for x in g["inspection_only"][:4])
        body += ("\n\nI'd also like to arrange a pre-purchase inspection at my expense; the things I'd want looked at are "
                 f"{items}. Are you open to that?")
    body += "\n\nThanks!"
    return body


def report(limit: int | None = None, path=None) -> list[dict[str, Any]]:
    live = [r for r in db.list_listings(role="candidate", path=path)
            if r["availability"] in ("active", "pending")]
    out = [g for g in (gaps(r["id"], path) for r in live) if g]
    out.sort(key=lambda g: (-(g["potential_score"] or 0), -(g["estimated_score_gain"] or 0)))
    return out[:limit] if limit else out


def main(argv: list[str]) -> int:
    db.init_db()
    if len(argv) > 1 and argv[1].isdigit():
        lid = int(argv[1])
        g = gaps(lid)
        if not g:
            print(f"no listing {lid}")
            return 1
        print(f"#{g['id']} {g['title']} — score {g['score']} (documentation {g['documentation']}/25, confidence {g['confidence']})")
        print(f"potential with documents: {g['potential_score']} (+{g['estimated_score_gain']})")
        print(f"VIN: {g['vin'] or 'MISSING — ask the seller first'} | documents attached: {g['documents'] or 'none'}\n")
        print("A document could settle:")
        for x in g["resolvable_by_document"]:
            print(f"  - [{x['status']}] {x['label'][:110]}")
        print("\nOnly an inspection can settle:")
        for x in g["inspection_only"]:
            print(f"  - [{x['status']}] {x['label'][:110]}")
        print("\n--- draft message to the seller ---\n")
        print(request_message(lid))
        return 0
    rows = report()
    novin = [g for g in rows if g["blocked_on_vin"]]
    nodocs = [g for g in rows if not g["documents"]]
    print(f"live candidates {len(rows)} · with documents attached {len(rows) - len(nodocs)} · blocked on a missing VIN {len(novin)}\n")
    print(f"{'id':<5}{'car':<34}{'now':>4}{'pot':>5}{'gain':>6}{'doc':>7}{'cnf':>5}{'VIN':>5}  ask for")
    for g in rows:
        keys = ", ".join(x["key"] for x in g["resolvable_by_document"])[:44]
        print(f"#{g['id']:<4}{str(g['title'])[:33]:<34}{g['score'] or 0:>4}{g['potential_score']:>5}{g['estimated_score_gain']:>+6}"
              f"{str(g['documentation'])+'/25':>7}{g['confidence'] or 0:>5}{('y' if g['vin'] else 'ASK'):>5}  {keys}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
