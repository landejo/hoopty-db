"""E2E: a freshly started server on a copy of the real DB re-derives every stored
assessment made under an older policy, in the background, for free. Sold and
ended cars then read Do not pursue; nothing is left on an old policy version."""
import json
import sys
import time
import urllib.request

BASE = "http://127.0.0.1:8766"


def get(path):
    return json.load(urllib.request.urlopen(urllib.request.Request(BASE + path, headers={"Origin": BASE})))


policy = get("/api/health")["policy_version"]
deadline = time.time() + 180
while time.time() < deadline:
    ev = [e for e in get("/api/events") if e["kind"] in {"startup_rescore", "startup_rescore_error"}]
    if ev:
        break
    time.sleep(2)
print("startup event:", ev[0]["kind"] if ev else "none", (ev[0]["detail"][:120] if ev else ""))
listings = get("/api/export")["listings"]
assessed = [l for l in listings if l.get("assessment")]
old = [l["id"] for l in assessed if l["assessment"].get("policy_version") != policy]
# A sold listing can show the assessment of the same car's live listing elsewhere
# (shared_from, same VIN): that verdict is about the car, which is still for sale.
gone = [l for l in assessed if l["availability"] in {"sold", "ended"} and not l["assessment"].get("shared_from")]
wrong = [(l["id"], l["assessment"]["verdict"]) for l in gone if l["assessment"]["verdict"] != "Do not pursue"]
checks = {
    "startup re-derive ran": bool(ev) and ev[0]["kind"] == "startup_rescore",
    f"every assessment is on policy {policy}": not old,
    "every sold/ended car reads Do not pursue": not wrong,
}
for k, ok in checks.items():
    print("PASS" if ok else "FAIL", k, "" if ok else (old[:10] if "policy" in k else wrong[:10]))
print(f"{len(assessed)} assessed, {len(gone)} sold/ended")
json.dump({k: {"ok": v, "detail": ""} for k, v in checks.items()}, open(sys.argv[1], "w"), indent=2)
