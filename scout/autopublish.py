"""Auto-publish after a sitting.

A *sitting* is a run of workbench activity with no gap longer than
IDLE_MINUTES. Activity is either a heartbeat the viewer sends while you are
actually using it (input in the last couple of minutes, tab visible) or any
data-changing API call (edits, assessments, syncs, availability checks, ...).
Only data-changing calls make a sitting worth publishing.

Publish fires when a sitting that changed data ends (IDLE_MINUTES with no
activity), or as a checkpoint every CHECKPOINT_MINUTES during a long sitting.
git_publish itself skips the push when the site content has not changed. The
pending flag is persisted, so a sitting cut short by a server restart still
publishes once the restarted server has been idle for IDLE_MINUTES."""
from __future__ import annotations

import os
import threading
from datetime import datetime, timedelta, timezone
from typing import Any

from scout import db

_lock = threading.Lock()
_state: dict[str, Any] = {"sitting_started": None, "last_activity": None, "changes": 0,
                          "last_publish": None, "last_result": None, "last_attempt": None, "publishing": False}


def enabled() -> bool:
    return os.environ.get("SCOUT_AUTOPUBLISH", "1") != "0"


def idle_minutes() -> float:
    return float(os.environ.get("SCOUT_AUTOPUBLISH_IDLE_MIN", "15"))


def checkpoint_minutes() -> float:
    return float(os.environ.get("SCOUT_AUTOPUBLISH_CHECKPOINT_MIN", "45"))


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(d: datetime | None) -> str | None:
    return d.replace(microsecond=0).isoformat() if d else None


def restore() -> None:
    """Server start: pick up changes a previous run never published."""
    saved = db.get_setting("autopublish") or {}
    with _lock:
        _state["changes"] = int(saved.get("changes") or 0)
        _state["last_publish"] = saved.get("last_publish")
        if _state["changes"]:
            _state["sitting_started"] = _state["last_activity"] = _now()


def _persist() -> None:
    db.set_setting("autopublish", {"changes": _state["changes"], "last_publish": _state["last_publish"]})


def note_activity(changed: bool, now: datetime | None = None) -> None:
    now = now or _now()
    with _lock:
        last = _state["last_activity"]
        if last is None or now - last > timedelta(minutes=idle_minutes()):
            _state["sitting_started"] = now      # a new sitting begins
        _state["last_activity"] = now
        if changed:
            _state["changes"] += 1
            if _state["changes"] == 1:
                _persist()


def due(now: datetime | None = None) -> str | None:
    """Why a publish should run now, or None."""
    now = now or _now()
    with _lock:
        if not _state["changes"] or _state["publishing"] or _state["last_activity"] is None:
            return None
        att = _state["last_attempt"]
        if att and now - att < timedelta(minutes=idle_minutes()) and (_state["last_result"] or {}).get("ok") is False:
            return None       # back off after a failure
        if now - _state["last_activity"] >= timedelta(minutes=idle_minutes()):
            return "sitting ended"
        # The later of the two: a publish from an earlier sitting must not make this one "long".
        since = max((t for t in (_state["last_attempt"], _state["sitting_started"]) if t), default=None)
        if since and now - since >= timedelta(minutes=checkpoint_minutes()):
            return "checkpoint during a long sitting"
    return None


def set_publishing(on: bool) -> int:
    """Mark a publish in progress. Returns the change count it will cover, so
    edits made while it runs stay pending (see mark_published)."""
    with _lock:
        _state["publishing"] = on
        return _state["changes"]


def mark_published(result: dict[str, Any], reason: str, manual: bool = False, covered: int | None = None) -> None:
    with _lock:
        _state["last_attempt"] = _now()
        _state["last_result"] = {"ok": bool(result.get("ok")), "changed": bool(result.get("changed")),
                                 "reason": "manual" if manual else reason, "at": _iso(_now()),
                                 "detail": "" if result.get("ok") else str(result.get("detail") or "")[-300:]}
        if result.get("ok"):
            _state["changes"] = max(0, _state["changes"] - covered) if covered is not None else 0
            _state["last_publish"] = _iso(_now())
        _persist()


def status(now: datetime | None = None) -> dict[str, Any]:
    now = now or _now()
    with _lock:
        eta = None
        if _state["changes"] and _state["last_activity"]:
            eta = _iso(_state["last_activity"] + timedelta(minutes=idle_minutes()))
        return {"enabled": enabled(), "idle_minutes": idle_minutes(), "checkpoint_minutes": checkpoint_minutes(),
                "pending_changes": _state["changes"], "sitting_started": _iso(_state["sitting_started"]),
                "last_activity": _iso(_state["last_activity"]), "publish_after": eta,
                "last_publish": _state["last_publish"], "last_result": _state["last_result"],
                "publishing": _state["publishing"]}


def reset_for_tests() -> None:
    with _lock:
        _state.update({"sitting_started": None, "last_activity": None, "changes": 0, "last_publish": None,
                       "last_result": None, "last_attempt": None, "publishing": False})
