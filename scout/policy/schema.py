"""Validated shapes. `EvidenceInterpretation` is what the language model may
return (interpretation only: facts, provenance, contradictions, flags, ratings,
qualitative lists). `Assessment` is what the deterministic engine stores."""
from __future__ import annotations

from types import UnionType
from typing import Literal, Union, get_args, get_origin

from pydantic import BaseModel, Field, field_validator, model_validator


class Trimmed(BaseModel):
    """Long strings are trimmed to each field's max_length rather than failing
    validation: a 700-character rationale must never cost a paid assessment."""

    @model_validator(mode="before")
    @classmethod
    def _trim_strings(cls, data):
        if not isinstance(data, dict):
            return data
        out = dict(data)
        for name, field in cls.model_fields.items():
            v = out.get(name)
            ann = field.annotation
            origin = get_origin(ann)
            str_field = ann is str or (origin in (Union, UnionType) and str in get_args(ann))
            if isinstance(v, str) and str_field:
                cap = next((m.max_length for m in field.metadata if getattr(m, "max_length", None)), None)
                if cap and len(v) > cap:
                    out[name] = v[:cap]
            elif isinstance(v, list) and field.annotation is not None:
                lim = next((m.max_length for m in field.metadata if getattr(m, "max_length", None)), None)
                if lim and len(v) > lim:
                    out[name] = v[:lim]
        return out

from scout.policy.preferences import EVIDENCE_SOURCES, FACT_STATUSES, MISSIONS, URGENCY_MODES, VERDICTS

Source = Literal["receipt", "history_report", "photo", "external_vin", "listing_text", "seller_comment", "seller_claim", "ai_inference"]
FactStatus = Literal["verified", "claimed", "inferred", "unknown"]
Tri = Literal["yes", "no", "unknown"]
CriticalStatus = Literal["satisfied", "claimed_only", "missing", "failed"]


class Fact(Trimmed):
    key: str = Field(max_length=60)
    value: str | None = Field(default=None, max_length=300)
    status: FactStatus
    source: Source
    note: str = Field(default="", max_length=400)

    @field_validator("value", mode="before")
    @classmethod
    def _stringify(cls, v):
        if v is None or isinstance(v, str):
            return v
        if isinstance(v, (int, float, bool)):
            return str(v)
        return str(v)[:300]


class Contradiction(Trimmed):
    topic: str = Field(max_length=80)
    detail: str = Field(max_length=500)
    severity: Literal["minor", "material", "identity"]


class CriticalEvidence(Trimmed):
    key: str = Field(max_length=60)
    status: CriticalStatus
    evidence: str = Field(default="", max_length=500)
    source: Source = "ai_inference"


class Flags(BaseModel):
    """Gate inputs. 'unknown' is the honest default; code never treats unknown as good."""
    seller_refuses_vin_or_ppi: Tri = "unknown"
    identity_or_odometer_fraud: Tri = "unknown"
    active_overheating_or_coolant_loss: Tri = "unknown"
    serious_brake_or_oil_pressure_issue: Tri = "unknown"
    unsafe_structure_or_heavy_rust: Tri = "unknown"
    emissions_or_registration_infeasible: Tri = "unknown"
    salvage_or_rebuilt_title: Tri = "unknown"
    salvage_fully_documented_with_specialist_ppi: Tri = "unknown"
    accident_without_repair_docs: Tri = "unknown"
    permanent_warning_lights: Tri = "unknown"
    modified_powertrain_undocumented: Tri = "unknown"
    remote_auction_no_ppi: Tri = "unknown"
    major_service_claimed_undocumented: Tri = "unknown"
    age_related_service_documented: Tri = "unknown"
    transformation_documented_since_last_sale: Tri = "unknown"
    reserve_auction: Tri = "unknown"


class CategoryRating(Trimmed):
    rating: int = Field(ge=0, le=10)
    rationale: str = Field(max_length=1500)


class Ratings(BaseModel):
    documentation: CategoryRating
    condition: CategoryRating
    price_value: CategoryRating
    mission_fit: CategoryRating
    logistics: CategoryRating
    emotional_spec_fit: CategoryRating


class MoneyRange(BaseModel):
    low: int = Field(ge=0, le=100000)
    high: int = Field(ge=0, le=100000)

    @field_validator("high")
    @classmethod
    def _ordered(cls, v, info):
        low = info.data.get("low", 0)
        return max(v, low)


class EvidenceInterpretation(Trimmed):
    facts: list[Fact] = Field(default_factory=list, max_length=60)
    contradictions: list[Contradiction] = Field(default_factory=list, max_length=15)
    critical_evidence: list[CriticalEvidence] = Field(default_factory=list, max_length=20)
    flags: Flags = Field(default_factory=Flags)
    ratings: Ratings
    evidence_quality: int = Field(ge=0, le=10, description="How much of the key evidence is verifiable")
    immediate_service_estimate: MoneyRange
    expected_hammer: MoneyRange | None = None
    positives: list[str] = Field(default_factory=list, max_length=8)
    concerns: list[str] = Field(default_factory=list, max_length=8)
    unknowns: list[str] = Field(default_factory=list, max_length=12)
    seller_questions: list[str] = Field(default_factory=list, max_length=12)
    ppi_focus: list[str] = Field(default_factory=list, max_length=12)
    what_would_change_verdict: list[str] = Field(default_factory=list, max_length=6)
    mission_note: str = Field(default="", max_length=1500)
    rationale: str = Field(default="", max_length=4000)
    next_action: str = Field(default="", max_length=800)

    @field_validator("positives", "concerns", "unknowns", "seller_questions", "ppi_focus", "what_would_change_verdict", mode="before")
    @classmethod
    def _trim(cls, v):
        if isinstance(v, str):
            v = [v]
        return [str(x).strip()[:600] for x in (v or []) if str(x).strip()]


# ---------- provenance (same-car investigation) ----------

PriceType = Literal["verified_sale", "winning_bid", "high_bid_reserve_not_met", "advertised_sold", "asking", "estimated"]
EventStatus = Literal["Listed", "Sold", "Bid to / reserve not met", "Withdrawn", "Relisted", "Price reduced",
                      "Seller decided to keep", "Dealer acquisition", "Auction or wholesale movement", "Unknown"]
Identity = Literal["confirmed", "strongly_likely", "possible", "not_established"]
StatementKind = Literal["withdrawn", "keep", "sold", "reason_for_selling", "recent_purchase", "problem", "ppi",
                        "track_use", "failed_sale", "earlier_price", "condition_opinion", "contradiction", "other"]


class ProvenanceEvent(Trimmed):
    date: str | None = None
    venue: str = Field(default="", max_length=80)
    url: str = Field(default="", max_length=1000)
    mileage: int | None = Field(default=None, ge=0, le=999999)
    price: int | None = Field(default=None, ge=0, le=2000000)
    price_type: PriceType = "estimated"
    status: EventStatus = "Unknown"
    evidence: str = Field(default="", max_length=600)
    identity_confidence: Identity = "not_established"
    identity_basis: str = Field(default="", max_length=400)
    seller: str | None = Field(default=None, max_length=120)

    @field_validator("date")
    @classmethod
    def _iso(cls, v):
        if v in (None, ""):
            return None
        import re as _re
        return v[:10] if _re.match(r"^\d{4}-\d{2}-\d{2}", str(v)) else None


class SellerStatement(Trimmed):
    date: str | None = None
    url: str = Field(default="", max_length=1000)
    venue: str = Field(default="", max_length=80)
    kind: StatementKind = "other"
    quote: str = Field(max_length=600)
    factual: bool = True

    @field_validator("date")
    @classmethod
    def _iso(cls, v):
        if v in (None, ""):
            return None
        import re as _re
        return v[:10] if _re.match(r"^\d{4}-\d{2}-\d{2}", str(v)) else None


class ProvenanceInterpretation(Trimmed):
    events: list[ProvenanceEvent] = Field(default_factory=list, max_length=40)
    seller_statements: list[SellerStatement] = Field(default_factory=list, max_length=25)
    work_before_prior_sale: list[str] = Field(default_factory=list, max_length=15)
    work_after_prior_sale: list[str] = Field(default_factory=list, max_length=15)
    cosmetic_or_preference: list[str] = Field(default_factory=list, max_length=10)
    repairs_correcting_faults: list[str] = Field(default_factory=list, max_length=10)
    identity_notes: str = Field(default="", max_length=800)
    summary: str = Field(default="", max_length=2000)


class Gate(BaseModel):
    kind: Literal["hard", "conditional", "configuration", "strategy"]
    key: str
    reason: str


class CostBreakdown(BaseModel):
    price_basis: str                     # asking | current_bid | expected_hammer | sold
    price: int
    buyer_fee: int
    transport: int
    immediate_service_low: int
    immediate_service_high: int
    overdue_allowance: int
    risk_reserve: int
    tax_and_registration: int
    all_in_low: int
    all_in_high: int
    max_price: int                       # max hammer / walk-away, solved backward
    offer_low: int
    offer_high: int
    notes: list[str] = Field(default_factory=list)


class Score(BaseModel):
    documentation: int
    condition: int
    price_value: int
    mission_fit: int
    logistics: int
    emotional_spec_fit: int
    total: int
    caps_applied: list[str] = Field(default_factory=list)


class Assessment(BaseModel):
    policy_version: str
    mission: Literal["enthusiast_bridge", "pragmatic_bridge", "future_keeper", "utility_capability"]
    urgency_mode: Literal["accelerated_bridge", "emergency", "casual_search"]
    gates: list[Gate]
    score: Score
    confidence: int = Field(ge=0, le=100)
    verdict: Literal["Pursue", "Pursue conditionally", "Maybe / verify", "Reject", "Do not pursue"]
    verdict_reason: str
    costs: CostBreakdown
    evidence: EvidenceInterpretation
    vin_history: dict = Field(default_factory=dict)
    assessed_at: str
    model: str


assert set(MISSIONS) == set(Assessment.model_fields["mission"].annotation.__args__)
assert set(URGENCY_MODES) == set(Assessment.model_fields["urgency_mode"].annotation.__args__)
assert set(VERDICTS) == set(Assessment.model_fields["verdict"].annotation.__args__)
assert set(EVIDENCE_SOURCES) == set(Source.__args__) and set(FACT_STATUSES) == set(FactStatus.__args__)
