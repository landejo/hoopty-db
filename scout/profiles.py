"""Profile registry: seed YAML files + AI-generated rows in the DB."""
from __future__ import annotations

import re
from typing import Any

import yaml

from scout import db
from scout.config import SEED_PROFILES_DIR


def load_seed_profiles() -> list[dict[str, Any]]:
    out = []
    for path in sorted(SEED_PROFILES_DIR.glob("*.yaml")):
        with path.open() as f:
            data = yaml.safe_load(f) or {}
        if data.get("key") and data.get("weights"):
            data.setdefault("source", "seed")
            data.setdefault("verified", True)
            out.append(data)
    return out


def sync_seed_profiles(path=None) -> int:
    """Seed profiles always win over anything with the same key in the DB."""
    seeds = load_seed_profiles()
    for p in seeds:
        db.upsert_profile(p, path)
    return len(seeds)


def registry_summary(profiles: list[dict[str, Any]]) -> str:
    """Compact text block the normalizer uses to pick a profile_key."""
    lines = []
    for p in profiles:
        yrs = p.get("years") or []
        yr = f" {yrs[0]}-{yrs[1]}" if len(yrs) == 2 else ""
        models = ", ".join(p.get("models") or [])
        lines.append(f"- {p['key']}: {p.get('make','')} {models}{yr} — {p['label']}")
    return "\n".join(lines)


_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def suggest_key(make: str | None, model: str | None, generation: str | None) -> str:
    parts = [make or "", model or "", generation or ""]
    key = "_".join(_NON_ALNUM.sub("_", p.lower()).strip("_") for p in parts if p)
    return key[:60] or "unknown"


def match_profile(profiles: list[dict[str, Any]], make: str | None, model: str | None,
                  year: int | None) -> dict[str, Any] | None:
    """Deterministic match on make + model substring + year window."""
    if not model:
        return None
    m = model.lower()
    mk = (make or "").lower()
    best = None
    for p in profiles:
        if p.get("make") and mk and p["make"].lower() != mk:
            continue
        for pm in p.get("models") or []:
            pml = pm.lower().replace(" ", "")
            if pml and pml in m.replace(" ", ""):
                yrs = p.get("years") or []
                if year and len(yrs) == 2 and not (yrs[0] <= year <= yrs[1]):
                    continue
                if best is None or len(pml) > best[0]:
                    best = (len(pml), p)
    return best[1] if best else None
