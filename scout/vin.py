"""Free VIN services. NHTSA vPIC decode (no key) + NHTSA recalls by
make/model/year. Results are cached in the vin_decodes table. Everything here
is best-effort: a network failure returns None and the assessment proceeds
with the VIN decode marked unknown."""
from __future__ import annotations

import json
import urllib.parse
import urllib.request
from typing import Any

from scout import db

VPIC_URL = "https://vpic.nhtsa.dot.gov/api/vehicles/DecodeVinValues/{vin}?format=json"
RECALLS_URL = "https://api.nhtsa.gov/recalls/recallsByVehicle?make={make}&model={model}&modelYear={year}"
TIMEOUT = 12

FIELDS = {
    "ModelYear": "year", "Make": "make", "Model": "model", "Series": "series", "Trim": "trim",
    "BodyClass": "body_class", "DisplacementL": "engine_liters", "EngineCylinders": "cylinders",
    "EngineHP": "engine_hp", "FuelTypePrimary": "fuel", "TransmissionStyle": "transmission",
    "DriveType": "drive_type", "PlantCountry": "plant_country", "PlantCity": "plant_city",
    "Doors": "doors", "ErrorCode": "error_code", "ErrorText": "error_text",
}


def _get_json(url: str) -> Any:
    req = urllib.request.Request(url, headers={"User-Agent": "hoopty-scout/0.1 (personal use)"})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:  # noqa: S310 (fixed https hosts)
        return json.loads(r.read().decode("utf-8"))


def decode_vin(vin: str, path=None, refresh: bool = False) -> dict[str, Any] | None:
    vin = (vin or "").strip().upper()
    if len(vin) != 17:
        return None
    if not refresh:
        cached = db.get_vin_decode(vin, path)
        if cached:
            return cached
    try:
        data = _get_json(VPIC_URL.format(vin=vin))
        row = (data.get("Results") or [{}])[0]
    except Exception as e:  # network / parse; never fatal
        db.log_event("vin_decode_error", None, f"{vin}: {e}", path)
        return None
    out: dict[str, Any] = {"vin": vin, "source": "nhtsa_vpic"}
    for k, name in FIELDS.items():
        v = (row.get(k) or "").strip()
        if v:
            out[name] = v
    for k in ("year", "cylinders", "doors", "engine_hp"):
        if k in out:
            try:
                out[k] = int(float(out[k]))
            except ValueError:
                out.pop(k)
    if "engine_liters" in out:
        try:
            out["engine_liters"] = round(float(out["engine_liters"]), 1)
        except ValueError:
            out.pop("engine_liters")
    # Only a real decode is worth caching (vPIC error code 0 = clean, 1 = check digit etc).
    ok = str(out.get("error_code", "")).split(",")[0].strip() in {"0", "1", "8", "14"} and out.get("make")
    if ok:
        try:
            out["recalls"] = recalls_for(out.get("make"), out.get("model"), out.get("year"))
        except Exception:
            out["recalls"] = None
        db.set_vin_decode(vin, out, path)
        return out
    return None


def recalls_for(make: str | None, model: str | None, year: int | None) -> list[dict[str, str]] | None:
    if not (make and model and year):
        return None
    url = RECALLS_URL.format(make=urllib.parse.quote(make), model=urllib.parse.quote(model), year=year)
    data = _get_json(url)
    out = []
    for r in data.get("results") or []:
        out.append({"campaign": r.get("NHTSACampaignNumber", ""), "component": r.get("Component", ""),
                    "summary": (r.get("Summary") or "")[:300], "date": r.get("ReportReceivedDate", "")})
    return out[:25]


def compare_decode(decoded: dict[str, Any] | None, listing: dict[str, Any]) -> list[dict[str, str]]:
    """Deterministic identity checks between the decoded VIN and the listing."""
    if not decoded:
        return []
    out = []
    if decoded.get("year") and listing.get("year") and int(decoded["year"]) != int(listing["year"]):
        out.append({"topic": "model year", "detail": f"VIN decodes to {decoded['year']}; listing says {listing['year']}", "severity": "material"})
    if decoded.get("make") and listing.get("make"):
        dm, lm = decoded["make"].lower(), listing["make"].lower()
        if dm not in lm and lm not in dm and not (dm.startswith("bmw") and lm.startswith("bmw")):
            out.append({"topic": "make", "detail": f"VIN decodes to {decoded['make']}; listing says {listing['make']}", "severity": "identity"})
    if decoded.get("engine_liters") and listing.get("engine_liters"):
        try:
            if abs(float(decoded["engine_liters"]) - float(listing["engine_liters"])) >= 0.35:
                out.append({"topic": "engine displacement",
                            "detail": f"VIN decodes to {decoded['engine_liters']}L; listing says {listing['engine_liters']}L",
                            "severity": "material"})
        except (TypeError, ValueError):
            pass
    return out


def decoded_facts(decoded: dict[str, Any] | None) -> list[dict[str, str]]:
    """Facts with external_vin provenance for the assessment record."""
    if not decoded:
        return []
    facts = []
    for k in ("year", "make", "model", "series", "trim", "engine_liters", "cylinders", "body_class", "transmission", "plant_country"):
        if decoded.get(k) not in (None, ""):
            facts.append({"key": f"vin_{k}", "value": str(decoded[k]), "status": "verified", "source": "external_vin",
                          "note": "NHTSA vPIC decode"})
    if decoded.get("recalls"):
        facts.append({"key": "recall_campaigns", "value": f"{len(decoded['recalls'])} NHTSA campaigns for this make/model/year",
                      "status": "verified", "source": "external_vin", "note": "completion status unknown; verify with dealer"})
    return facts
