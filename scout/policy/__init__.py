"""Assessment policy: the guide, encoded.

`Jason_Car_Assessment_Guide.md` in this directory is the human-readable source.
`preferences.py` holds durable preferences, `state.py` the editable temporary
state, `gates.py` / `scoring.py` / `costs.py` the deterministic rules, and
`schema.py` the validated shape of what the language model may return.
Every stored assessment records POLICY_VERSION."""
POLICY_VERSION = "1.2.1"   # 1.1 = guide 1.1 as written; 1.2 = weight/band deviations in POLICY_CHANGES.md
GUIDE_FILENAME = "Jason_Car_Assessment_Guide.md"
