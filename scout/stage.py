"""Lifecycle stage of a listing: Listing -> Questions sent -> Docs in -> PPI
done. Deterministic from the listing's status and attached documents; feeds
the policy engine's open-question / observed classification (policy 1.4.0)."""
from __future__ import annotations

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
