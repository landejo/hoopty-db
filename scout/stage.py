"""Lifecycle stage of a listing: Listing -> Questions sent -> Docs in -> PPI
done. Deterministic from the listing's status and attached documents; feeds
the policy engine's open-question / observed classification (policy 1.4.0)."""
from __future__ import annotations

from datetime import datetime
from typing import Any

_PPI_STATUSES = {"Offer Made", "Purchased"}
_QUESTIONS_STATUSES = {"Contacted", "PPI Scheduled"}


def stage_for(listing: dict[str, Any], documents: list[dict[str, Any]]) -> str:
    status = listing.get("status")
    if any(d.get("kind") == "inspection" for d in documents) or status in _PPI_STATUSES:
        return "ppi"
    if any(d.get("kind") != "inspection" for d in documents):
        return "docs"
    if status in _QUESTIONS_STATUSES:
        return "questions"
    return "listing"


def documents_read_by(assessment: dict[str, Any], documents: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The documents a stored assessment actually read: those attached at or before it
    ran. A re-derivation (free, no AI) stages on these, so a report attached later
    can't move the car to "docs" and turn the old assessment's "missing" items into
    observed gaps before anything has read it. That made a clean Carfax flip #637
    from Maybe to Reject (2026-10-23)."""
    at = assessment.get("assessed_at")
    if not at:
        return documents
    ran = datetime.fromisoformat(at)
    return [d for d in documents if not d.get("created_at") or datetime.fromisoformat(d["created_at"]) <= ran]
