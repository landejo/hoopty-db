"""Temporary state and thresholds (guide §2, §10, §19). Editable at runtime via
the settings table; defaults here. Keys are stable so old assessments can be
read against the policy version that produced them."""
from __future__ import annotations

import json
from typing import Any

from scout import db

DEFAULT_STATE: dict[str, Any] = {
    "urgency_mode": "accelerated_bridge",
    "budget": {
        "ideal_low": 10000,
        "ideal_high": 13000,
        "max_price": 15000,          # "generally under $15,000"
        "acceptable_all_in": 16500,  # what the max hammer is solved backward from
        "defeats_purpose_all_in": 21000,  # above this a bridge car fails the hard cost gate
    },
    "current_vehicles": [
        {"name": "2018 Lexus RX 350", "role": "household utility, working well"},
        {"name": "2011 BMW 335i (E90, N55, auto)", "role": "finite life; recurring coolant leak; front suspension due"},
    ],
    "active_exclusions": ["Lexus SC430", "Mazda Miata", "Honda CR-Z", "Lexus IS350", "Lexus GS350"],
    "deprioritized": ["turbo BMW like the 335i", "Land Rover LR4", "second Lexus SUV unless the capability itself is wanted"],
    "home_location": "Carmel, CA",
    "fees": {
        # Verify against each platform's current terms before relying on the numbers.
        "bat": {"pct": 0.05, "min": 250, "max": 7500},
        "carsandbids": {"pct": 0.05, "min": 250, "max": 7500},
        "facebook": {"pct": 0.0, "min": 0, "max": 0},
        "cargurus": {"pct": 0.0, "min": 0, "max": 0},
        "carscom": {"pct": 0.0, "min": 0, "max": 0},
        "autotrader": {"pct": 0.0, "min": 0, "max": 0},
    },
    "transport_by_locality_band": {"5": 0, "4": 400, "3": 900, "2": 1400, "1": 1900, "unknown": 1200},
    "tax_rate": 0.0925,
    "registration_fee": 350,
    "overdue_allowance": {"old_or_high_mileage": 1500, "middle_aged": 800, "recent": 300,
                          "old_years": 15, "high_mileage": 100000, "middle_years": 8,
                          "documented_discount": 0.5},
    "default_risk_reserve": 1500,
    "reserve_per_unresolved_conditional": 1000,
    "reserve_max_unresolved_counted": 2,   # stacking cap so four open items do not read as a $4k penalty
    "early_bid_hours_before_close": 24,
    # Listing age (days since the site says it was listed, else since first seen).
    # Not applied to live auctions. Penalty steps are mission-fit points.
    "listing_age": {"fresh_days": 45, "steps": [[90, 2], [120, 4], [240, 6], [99999, 8]], "stale_after_days": 120},
}

SETTINGS_KEY = "policy_state"


def _deep_merge(base: dict, override: dict) -> dict:
    out = dict(base)
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def load_state(path=None) -> dict[str, Any]:
    stored = db.get_setting(SETTINGS_KEY, path)
    return _deep_merge(DEFAULT_STATE, stored or {})


def save_state(update: dict[str, Any], path=None) -> dict[str, Any]:
    """Merge `update` into the stored overrides and return the effective state."""
    from scout.policy.preferences import URGENCY_MODES
    if "urgency_mode" in update and update["urgency_mode"] not in URGENCY_MODES:
        raise ValueError(f"urgency_mode must be one of {URGENCY_MODES}")
    stored = db.get_setting(SETTINGS_KEY, path) or {}
    merged = _deep_merge(stored, update)
    db.set_setting(SETTINGS_KEY, merged, path)
    return load_state(path)


def reset_state(path=None) -> dict[str, Any]:
    db.set_setting(SETTINGS_KEY, {}, path)
    return load_state(path)
