"""AI-call infrastructure: multi-block cached system prompts, the ai_calls
cost log, and the photo download/downscale/cache pipeline. Everything here is
mocked - no network, no paid AI calls."""
from __future__ import annotations

import io
from types import SimpleNamespace

import pytest

from scout import db
from scout.config import CONFIG, estimate_cost


# ---------- fakes ----------

class FakeUsage:
    def __init__(self, input_tokens=100, output_tokens=50, cache_creation_input_tokens=0,
                 cache_read_input_tokens=0):
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens
        self.cache_creation_input_tokens = cache_creation_input_tokens
        self.cache_read_input_tokens = cache_read_input_tokens


class FakeMessage:
    def __init__(self, text="ok", stop_reason="end_turn", model="claude-opus-5", usage=None):
        self.content = [SimpleNamespace(type="text", text=text)]
        self.stop_reason = stop_reason
        self.model = model
        self.usage = usage or FakeUsage()


class FakeStream:
    def __init__(self, msg):
        self._msg = msg

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def get_final_message(self):
        return self._msg


class FakeMessagesAPI:
    """Records every stream() call's kwargs; returns queued messages in order."""
    def __init__(self, messages):
        self._messages = list(messages)
        self.calls: list[dict] = []

    def stream(self, **kwargs):
        self.calls.append(kwargs)
        return FakeStream(self._messages.pop(0))


class FakeClient:
    def __init__(self, messages):
        self.messages = FakeMessagesAPI(messages)
        self.beta = SimpleNamespace(messages=self.messages)  # same recorder for beta path


def _patch_client(monkeypatch, messages):
    import scout.ai as ai
    client = FakeClient(messages)
    monkeypatch.setattr(ai, "require_client", lambda: client)
    return client


# ---------- system blocks / caching ----------

def test_system_blocks_str_unchanged():
    import scout.ai as ai
    blocks = ai._system_blocks("hello", True)
    assert blocks == [{"type": "text", "text": "hello", "cache_control": {"type": "ephemeral"}}]


def test_system_blocks_list_cache_control_on_first_only():
    import scout.ai as ai
    blocks = ai._system_blocks(["static part", "dynamic part"], True)
    assert len(blocks) == 2
    assert blocks[0] == {"type": "text", "text": "static part", "cache_control": {"type": "ephemeral"}}
    assert blocks[1] == {"type": "text", "text": "dynamic part"}


def test_system_blocks_list_drops_empty_strings():
    import scout.ai as ai
    blocks = ai._system_blocks(["static", "", "dynamic"], True)
    assert [b["text"] for b in blocks] == ["static", "dynamic"]


def test_call_text_list_system_sent_as_separate_blocks(monkeypatch):
    import scout.ai as ai
    client = _patch_client(monkeypatch, [FakeMessage(text="hi")])
    out = ai.call_text("claude-sonnet-5", ["static", "dynamic"], "user msg", 1000, "last_normalize")
    assert out == "hi"
    kwargs = client.messages.calls[0]
    assert [b["text"] for b in kwargs["system"]] == ["static", "dynamic"]
    assert kwargs["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert "cache_control" not in kwargs["system"][1]


def test_call_json_text_truncation_retry_appends_to_last_block_only(monkeypatch):
    import scout.ai as ai
    truncated = FakeMessage(text="cut off", stop_reason="max_tokens")
    finished = FakeMessage(text='{"ok": true}', stop_reason="end_turn")
    client = _patch_client(monkeypatch, [truncated, finished])
    out = ai.call_json_text("claude-sonnet-5", ["static part", "dynamic part"], "user msg", 1000, "last_assess")
    assert out == '{"ok": true}'
    assert len(client.messages.calls) == 2
    first_system = client.messages.calls[0]["system"]
    second_system = client.messages.calls[1]["system"]
    # Static (cached) block is byte-identical between calls - the retry never
    # touches it, so the cache prefix survives.
    assert first_system[0]["text"] == second_system[0]["text"] == "static part"
    assert second_system[0]["cache_control"] == {"type": "ephemeral"}
    # Terse instruction lands only on the last (dynamic) block.
    assert second_system[1]["text"].startswith("dynamic part")
    assert "OUTPUT LENGTH" in second_system[1]["text"]
    assert first_system[1]["text"] == "dynamic part"


def test_call_text_refusal_raises(monkeypatch):
    import scout.ai as ai
    _patch_client(monkeypatch, [FakeMessage(text="", stop_reason="refusal")])
    with pytest.raises(RuntimeError, match="refused"):
        ai.call_text("claude-sonnet-5", "sys", "user", 1000, "last_assess")


# ---------- cost log ----------

def test_estimate_cost_opus5_matches_worked_example():
    cost = estimate_cost("claude-opus-5", 1000, 2000, 3000, 4000)
    expected = 1000 * 5e-6 + 2000 * 25e-6 + 3000 * 5e-6 * 1.25 + 4000 * 5e-6 * 0.1
    assert cost == pytest.approx(expected)


def test_estimate_cost_unknown_model_uses_opus5_price(capsys):
    cost = estimate_cost("some-future-model", 1_000_000, 0)
    assert cost == pytest.approx(5.0)
    assert "unknown model" in capsys.readouterr().out


def test_call_text_writes_ai_calls_row_with_correct_cost(monkeypatch):
    import scout.ai as ai
    usage = FakeUsage(input_tokens=1000, output_tokens=2000, cache_creation_input_tokens=3000,
                      cache_read_input_tokens=4000)
    _patch_client(monkeypatch, [FakeMessage(text="hi", model="claude-opus-5", usage=usage)])
    ai.call_text("claude-opus-5", "sys", "user", 1000, "last_assess", listing_id=7)
    spend = db.ai_spend()
    assert spend["total"]["calls"] == 1
    expected = 1000 * 5e-6 + 2000 * 25e-6 + 3000 * 5e-6 * 1.25 + 4000 * 5e-6 * 0.1
    assert spend["total"]["cost_usd"] == pytest.approx(expected)
    assert spend["by_kind"]["last_assess"]["calls"] == 1
    assert spend["by_model"]["claude-opus-5"]["calls"] == 1


def test_call_text_logs_truncated_and_refusal_paths_before_raising(monkeypatch):
    import scout.ai as ai
    _patch_client(monkeypatch, [FakeMessage(text="", stop_reason="refusal")])
    with pytest.raises(RuntimeError):
        ai.call_text("claude-sonnet-5", "sys", "user", 1000, "last_assess")
    assert db.ai_spend()["total"]["calls"] == 1

    _patch_client(monkeypatch, [FakeMessage(text="cut", stop_reason="max_tokens")])
    with pytest.raises(ai.TruncatedOutput):
        ai.call_text("claude-sonnet-5", "sys", "user", 1000, "last_assess")
    assert db.ai_spend()["total"]["calls"] == 2


def test_logging_failure_does_not_break_the_call(monkeypatch):
    import scout.ai as ai
    _patch_client(monkeypatch, [FakeMessage(text="still works")])
    monkeypatch.setattr(db, "add_ai_call", lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("db is down")))
    out = ai.call_text("claude-sonnet-5", "sys", "user", 1000, "last_assess")
    assert out == "still works"


def test_ai_spend_aggregation_by_kind_model_day_and_cache_ratio():
    db.add_ai_call("last_assess", "claude-opus-5", input_tokens=100, output_tokens=50,
                   cache_read_tokens=200, cost_usd=1.5)
    db.add_ai_call("last_normalize", "claude-sonnet-5", input_tokens=100, output_tokens=50,
                   cache_write_tokens=100, cost_usd=0.5)
    spend = db.ai_spend()
    assert spend["total"]["calls"] == 2
    assert spend["total"]["cost_usd"] == pytest.approx(2.0)
    assert set(spend["by_kind"]) == {"last_assess", "last_normalize"}
    assert set(spend["by_model"]) == {"claude-opus-5", "claude-sonnet-5"}
    assert len(spend["by_day"]) == 1
    assert spend["by_day"][0]["calls"] == 2
    # cache_read=200, cache_write=100, input=200 -> 200 / 500
    assert spend["cache_hit_ratio"] == pytest.approx(200 / 500)


def test_ai_spend_endpoint():
    from fastapi.testclient import TestClient
    from scout.server import app
    db.add_ai_call("last_assess", "claude-opus-5", input_tokens=10, output_tokens=5, cost_usd=0.1)
    with TestClient(app) as c:
        resp = c.get("/api/ai-spend", headers={"Origin": f"http://127.0.0.1:{CONFIG.port}"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"]["calls"] == 1


# ---------- photos ----------

def _png_bytes(w, h):
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (w, h), color=(200, 50, 50)).save(buf, format="PNG")
    return buf.getvalue()


def _noisy_png_bytes(w, h):
    """Solid color compresses to well under the 2000-byte skip threshold at
    small sizes - use noise so a small test image still clears it."""
    import random

    from PIL import Image
    rng = random.Random(0)
    img = Image.new("RGB", (w, h))
    img.putdata([(rng.randrange(256), rng.randrange(256), rng.randrange(256)) for _ in range(w * h)])
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


class _FakeHTTPResponse:
    def __init__(self, data, content_type):
        self._data = data
        self._ctype = content_type

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    @property
    def headers(self):
        return SimpleNamespace(get=lambda k, default=None: self._ctype if k == "Content-Type" else default)

    def read(self, n=-1):
        return self._data


def test_photo_blocks_downscales_and_caches(monkeypatch, tmp_path):
    import scout.ai.photos as photos
    monkeypatch.setattr(photos, "_CACHE_DIR", tmp_path / "photo_cache")

    png = _png_bytes(4000, 3000)
    calls = {"n": 0}

    def fake_urlopen(req, timeout=8):
        calls["n"] += 1
        return _FakeHTTPResponse(png, "image/png")

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    blocks = photos.photo_blocks(["https://example.com/photo1.jpg"], max_edge=1568)
    assert len(blocks) == 1
    assert calls["n"] == 1
    block = blocks[0]
    assert block["source"]["media_type"] == "image/jpeg"

    import base64
    from PIL import Image
    raw = base64.b64decode(block["source"]["data"])
    img = Image.open(io.BytesIO(raw))
    assert img.format == "JPEG"
    assert max(img.size) == 1568

    # Cache file was written.
    cached = list((tmp_path / "photo_cache").glob("*.jpg"))
    assert len(cached) == 1

    # Second call reuses the cache - urlopen must not be hit again.
    def fail_urlopen(*a, **kw):
        raise AssertionError("should not re-download a cached photo")

    monkeypatch.setattr("urllib.request.urlopen", fail_urlopen)
    blocks2 = photos.photo_blocks(["https://example.com/photo1.jpg"], max_edge=1568)
    assert len(blocks2) == 1
    assert blocks2[0]["source"]["data"] == blocks[0]["source"]["data"]


def test_photo_blocks_never_upscales_small_image(monkeypatch, tmp_path):
    import scout.ai.photos as photos
    monkeypatch.setattr(photos, "_CACHE_DIR", tmp_path / "photo_cache")
    png = _noisy_png_bytes(400, 300)
    monkeypatch.setattr("urllib.request.urlopen", lambda req, timeout=8: _FakeHTTPResponse(png, "image/png"))

    import base64
    from PIL import Image
    blocks = photos.photo_blocks(["https://example.com/small.jpg"], max_edge=1568)
    raw = base64.b64decode(blocks[0]["source"]["data"])
    img = Image.open(io.BytesIO(raw))
    assert img.size == (400, 300)


def test_photo_blocks_skips_failures(monkeypatch, tmp_path):
    import scout.ai.photos as photos
    monkeypatch.setattr(photos, "_CACHE_DIR", tmp_path / "photo_cache")

    def boom(req, timeout=8):
        raise OSError("network down")

    monkeypatch.setattr("urllib.request.urlopen", boom)
    assert photos.photo_blocks(["https://example.com/bad.jpg"]) == []


def test_photo_blocks_skips_non_image_content_type(monkeypatch, tmp_path):
    import scout.ai.photos as photos
    monkeypatch.setattr(photos, "_CACHE_DIR", tmp_path / "photo_cache")
    monkeypatch.setattr("urllib.request.urlopen",
                        lambda req, timeout=8: _FakeHTTPResponse(b"<html></html>", "text/html"))
    assert photos.photo_blocks(["https://example.com/notreally.jpg"]) == []


def test_refusal_fallback_covers_opus_5_5():
    """Opus 5.5 runs broader safety classifiers than Opus 5; its requests carry the
    server-side fallback too (accepted by the API, probed 2026-09-25)."""
    from scout.ai import _supports_fallback
    assert _supports_fallback("claude-opus-5-5") and _supports_fallback("claude-opus-5")
    assert not _supports_fallback("claude-sonnet-5") and not _supports_fallback("claude-haiku-4-5")


def test_effort_is_set_per_tier(monkeypatch):
    from scout.config import Config
    for k in ("SCOUT_MODEL_DEEP", "SCOUT_MODEL_MID", "SCOUT_MODEL_TOP", "SCOUT_EFFORT_DEEP", "SCOUT_EFFORT_MID"):
        monkeypatch.delenv(k, raising=False)   # isolate from the developer's .env
    monkeypatch.setenv("SCOUT_EFFORT_TOP", "medium")   # also the default since the 2026-09-25 eval
    c = Config.load()
    assert c.effort_top == "medium" and c.effort_deep == "medium" and c.effort_mid == "low"


def test_a_tier_pinned_to_an_older_model_keeps_high_effort(monkeypatch):
    from scout.config import Config
    monkeypatch.setenv("SCOUT_MODEL_DEEP", "claude-opus-5")
    monkeypatch.delenv("SCOUT_EFFORT_DEEP", raising=False)
    assert Config.load().effort_deep == "high"
