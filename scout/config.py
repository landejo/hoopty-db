"""Configuration. Local-first, single-user."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
DB_PATH = Path(os.environ.get("SCOUT_DB_PATH") or (DATA_DIR / "scout.db"))
DOCS_DIR = ROOT / "docs"
SITE_DATA_DIR = DOCS_DIR / "data"
SEED_PROFILES_DIR = ROOT / "scout" / "profiles"

load_dotenv(ROOT / ".env")
DATA_DIR.mkdir(parents=True, exist_ok=True)


@dataclass
class Config:
    anthropic_api_key: str | None = None
    model_deep: str = "claude-opus-5"
    model_fast: str = "claude-haiku-4-5"
    home_location: str = "Carmel, CA"
    port: int = 8765
    skip_sold: bool = False  # sold/ended listings become market comps by default

    @classmethod
    def load(cls) -> "Config":
        return cls(
            anthropic_api_key=os.environ.get("ANTHROPIC_API_KEY"),
            model_deep=os.environ.get("SCOUT_MODEL_DEEP", "claude-opus-5"),
            model_fast=os.environ.get("SCOUT_MODEL_FAST", "claude-haiku-4-5"),
            home_location=os.environ.get("SCOUT_HOME_LOCATION", "Carmel, CA"),
            port=int(os.environ.get("SCOUT_PORT", "8765")),
            skip_sold=os.environ.get("SCOUT_SKIP_SOLD", "0") == "1",
        )

    @property
    def ai_enabled(self) -> bool:
        return bool(self.anthropic_api_key)


CONFIG = Config.load()

SITES = {
    "facebook": "Facebook Marketplace",
    "cargurus": "CarGurus",
    "carscom": "Cars.com",
    "carsandbids": "Cars & Bids",
    "bat": "Bring a Trailer",
}
AUCTION_SITES = {"carsandbids", "bat"}

AVAILABILITY = ["active", "sold", "ended", "removed", "unknown"]
ROLES = ["candidate", "comp"]

STATUSES = [
    "New", "Pursue", "Verify", "Contacted", "PPI Scheduled",
    "Offer Made", "Pass", "Purchased",
]

# Fixed score-axis vocabulary. Every profile assigns weights over a subset of
# these; AI-generated profiles must pick from this list (coerce drops others).
AXES: dict[str, str] = {
    "reliability": "Reliability of this make/model/year at this mileage",
    "condition": "Condition of THIS car from the listing's evidence",
    "value": "Price vs. market for the configuration and condition",
    "engagement": "Driving engagement intrinsic to the model/variant",
    "practicality": "Cargo / passengers / daily usability",
    "capability": "Off-road, towing, or adventure capability",
    "locality": "Proximity to home base (PPI, pickup, rust exposure)",
    "ownership_cost": "Parts, service access, insurance, fuel, and likely repairs",
    "desirability": "Spec/color/options appeal and long-term want-it factor",
}

LOCALITY_HOME = "Carmel, CA"
