"""Anthropic client (lazy) + shared call helper."""
from __future__ import annotations

from typing import TYPE_CHECKING

from scout.config import CONFIG, DATA_DIR, estimate_cost

if TYPE_CHECKING:
    from anthropic import Anthropic

_client: "Anthropic | None" = None

# Server-side refusal fallback (beta): re-routes a refused claude-opus-5 request
# to another model within the same call. Not enabled for claude-opus-5-5 - its
# support isn't confirmed, and the fallback set is model-specific.
_FALLBACK_BETA = "server-side-fallback-2026-07-01"


def _supports_fallback(model: str) -> bool:
    return model.startswith("claude-opus-5") and not model.startswith("claude-opus-5-5")


def get_client() -> "Anthropic | None":
    global _client
    if not CONFIG.ai_enabled:
        return None
    if _client is None:
        from anthropic import Anthropic  # lazy: keep server startup fast
        _client = Anthropic(api_key=CONFIG.anthropic_api_key)
    return _client


def require_client() -> "Anthropic":
    client = get_client()
    if client is None:
        raise RuntimeError("ANTHROPIC_API_KEY is not set (see .env.example).")
    return client


def _system_blocks(system: "str | list[str]", cache_system: bool) -> list[dict]:
    """One text block per non-empty string. cache_control (if any) goes on the
    FIRST block only - the static, identical-across-listings part - so a
    [static, dynamic] system keeps its cache prefix stable."""
    parts = [system] if isinstance(system, str) else [s for s in system if s]
    blocks = [{"type": "text", "text": p} for p in parts]
    if cache_system and blocks:
        blocks[0]["cache_control"] = {"type": "ephemeral"}
    return blocks


def _log_call(kind: str, model: str, usage, stop_reason: str | None, listing_id: int | None) -> None:
    """Write one ai_calls row. Never let logging break the actual call."""
    try:
        from scout import db
        input_tokens = getattr(usage, "input_tokens", 0) or 0
        output_tokens = getattr(usage, "output_tokens", 0) or 0
        cache_write_tokens = getattr(usage, "cache_creation_input_tokens", 0) or 0
        cache_read_tokens = getattr(usage, "cache_read_input_tokens", 0) or 0
        cost_usd = estimate_cost(model, input_tokens, output_tokens, cache_write_tokens, cache_read_tokens)
        db.add_ai_call(kind, model, input_tokens, output_tokens, cache_write_tokens, cache_read_tokens,
                       cost_usd, stop_reason, listing_id)
    except Exception:
        pass


def call_text(model: str, system: "str | list[str]", user, max_tokens: int, log_name: str,
              effort: str | None = None, cache_system: bool = True, listing_id: int | None = None) -> str:
    """One Messages call, returns concatenated text. Streams so long outputs
    never hit the HTTP timeout. Raw response saved to data/<log_name>.log.

    `system` may be a plain string, or a list of strings rendered as separate
    blocks (e.g. [static_prompt, dynamic_listing_context]) - the cache
    breakpoint always lands on the first block."""
    client = require_client()
    system_blocks = _system_blocks(system, cache_system)
    kwargs: dict = dict(
        model=model,
        max_tokens=max_tokens,
        system=system_blocks,
        messages=[{"role": "user", "content": user}],
    )
    if effort and not model.startswith("claude-haiku"):
        kwargs["output_config"] = {"effort": effort}

    if _supports_fallback(model):
        with client.beta.messages.stream(betas=[_FALLBACK_BETA], fallbacks="default", **kwargs) as stream:
            msg = stream.get_final_message()
    else:
        with client.messages.stream(**kwargs) as stream:
            msg = stream.get_final_message()

    served_model = getattr(msg, "model", None) or model
    _log_call(log_name, served_model, msg.usage, msg.stop_reason, listing_id)

    if msg.stop_reason == "refusal":
        raise RuntimeError("Model refused the request.")
    text = "".join(b.text for b in msg.content if getattr(b, "type", "") == "text")
    try:
        (DATA_DIR / f"{log_name}.log").write_text(text[:500_000])
        (DATA_DIR / f"{log_name}.meta").write_text(f"stop_reason={msg.stop_reason} output_tokens={getattr(msg.usage, 'output_tokens', '?')}\n")
    except OSError:
        pass
    if msg.stop_reason == "max_tokens":
        raise TruncatedOutput(f"model output hit the {max_tokens}-token ceiling (thinking counts toward it)")
    return text


class TruncatedOutput(RuntimeError):
    pass


def call_json_text(model: str, system: "str | list[str]", user, max_tokens: int, log_name: str,
                   effort: str | None = None, listing_id: int | None = None) -> str:
    """call_text with one retry when the output is truncated: a bigger ceiling
    and an instruction to be terser. Deep assessments need this headroom.

    The terse instruction is appended to the LAST system block only, never the
    static (cached) one, so the cache prefix survives the retry."""
    try:
        return call_text(model, system, user, max_tokens, log_name, effort=effort, listing_id=listing_id)
    except TruncatedOutput:
        terse_suffix = ("\n\nOUTPUT LENGTH: your previous answer was cut off. Keep every string under 300 "
                        "characters, at most 6 items per list, and no prose outside the JSON.")
        if isinstance(system, str):
            terse = system + terse_suffix
        else:
            parts = [s for s in system if s]
            terse = list(parts)
            if terse:
                terse[-1] = terse[-1] + terse_suffix
            else:
                terse = [terse_suffix]
        return call_text(model, terse, user, int(max_tokens * 1.5), log_name, effort=effort, listing_id=listing_id)
