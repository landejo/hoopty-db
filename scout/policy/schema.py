"""Validated shapes. `EvidenceInterpretation` is what the language model may
return (interpretation only: facts, provenance, contradictions, flags, ratings,
qualitative lists). `Assessment` is what the deterministic engine stores."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator

from scout.policy.preferences import EVIDENCE_SOURCES, FACT_STATUSES, MISSIONS, URGENCY_MODES, VERDICTS

Source = Literal["receipt", "history_report", "photo", "external_vin", "listing_text", "seller_comment", "seller_claim", "ai_inference"]
FactStatus = Literal["verified", "claimed", "inferred", "unknown"]
Tri = Literal["yes", "no", "unknown"]
CriticalStatus = Literal["satisfied", "claimed_only", "missing", "failed"]


class Fact(BaseModel):
    key: str = Field(max_length=60)
    value: str | None = Field(default=None, max_length=300)
    status: FactStatus
    source: Source
    note: str = Field(default="", max_length=400)


class Contradiction(BaseModel):
    topic: str = Field(max_length=80)
    detail: str = Field(max_length=500)
    severity: Literal["minor", "material", "identity"]


class CriticalEvidence(BaseModel):
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


class CategoryRating(BaseModel):
    rating: int = Field(ge=0, le=10)
    rationale: str = Field(max_length=600)


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


class EvidenceInterpretation(BaseModel):
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
    mission_note: str = Field(default="", max_length=600)
    rationale: str = Field(default="", max_length=2500)
    next_action: str = Field(default="", max_length=400)

    @field_validator("positives", "concerns", "unknowns", "seller_questions", "ppi_focus", "what_would_change_verdict")
    @classmethod
    def _trim(cls, v):
        return [str(x).strip()[:400] for x in v if str(x).strip()]


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
