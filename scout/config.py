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
    model_deep: str = "claude-opus-5"     # full assessment, provenance, new profiles
    model_mid: str = "claude-sonnet-5"    # quick assessment (same prompt, triage tier)
    model_fast: str = "claude-sonnet-5"   # sync-time read; Haiku 4.5 is the cheaper option
    home_location: str = "Carmel, CA"
    port: int = 8765
    skip_sold: bool = False  # sold/ended listings become market comps by default

    @classmethod
    def load(cls) -> "Config":
        return cls(
            anthropic_api_key=os.environ.get("ANTHROPIC_API_KEY"),
            model_deep=os.environ.get("SCOUT_MODEL_DEEP", "claude-opus-5"),
            model_mid=os.environ.get("SCOUT_MODEL_MID", "claude-sonnet-5"),
            model_fast=os.environ.get("SCOUT_MODEL_FAST", "claude-sonnet-5"),
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
    "autotrader": "Autotrader",
    "carsandbids": "Cars & Bids",
    "bat": "Bring a Trailer",
    "builtforbackroads": "Built for Backroads",
}
AUCTION_SITES = {"carsandbids", "bat"}

# $ per million tokens (input, output). Unknown models fall back to the
# Opus 5 price in estimate_cost() and print a warning.
PRICES: dict[str, tuple[float, float]] = {
    "claude-opus-5": (5.0, 25.0),
    "claude-opus-5-5": (4.0, 20.0),
    "claude-sonnet-5": (2.0, 10.0),
    "claude-haiku-4-5": (1.0, 5.0),
}


def estimate_cost(model: str, input_tokens: int = 0, output_tokens: int = 0,
                  cache_write_tokens: int = 0, cache_read_tokens: int = 0) -> float:
    """Cost in USD. Cache writes are billed at 1.25x the input price (5-minute
    TTL), cache reads at 0.1x. An unrecognized model uses the Opus 5 price."""
    prices = PRICES.get(model)
    if prices is None:
        print(f"warning: estimate_cost: unknown model {model!r}, using Opus 5 pricing")
        prices = PRICES["claude-opus-5"]
    in_price, out_price = prices
    cost = (
        input_tokens * in_price
        + output_tokens * out_price
        + cache_write_tokens * in_price * 1.25
        + cache_read_tokens * in_price * 0.1
    )
    return cost / 1_000_000


AVAILABILITY = ["active", "pending", "sold", "ended", "removed", "withdrawn", "unknown"]
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
