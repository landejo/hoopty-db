"""Anthropic client (lazy) + shared call helper."""
from __future__ import annotations

from typing import TYPE_CHECKING

from scout.config import CONFIG, DATA_DIR

if TYPE_CHECKING:
    from anthropic import Anthropic

_client: "Anthropic | None" = None


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


def call_text(model: str, system: str, user: str, max_tokens: int, log_name: str,
              effort: str | None = None, cache_system: bool = True) -> str:
    """One Messages call, returns concatenated text. Streams so long outputs
    never hit the HTTP timeout. Raw response saved to data/<log_name>.log."""
    client = require_client()
    system_blocks = [{"type": "text", "text": system}]
    if cache_system:
        system_blocks[0]["cache_control"] = {"type": "ephemeral"}
    kwargs: dict = dict(
        model=model,
        max_tokens=max_tokens,
        system=system_blocks,
        messages=[{"role": "user", "content": user}],
    )
    if effort:
        kwargs["output_config"] = {"effort": effort}
    with client.messages.stream(**kwargs) as stream:
        msg = stream.get_final_message()
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


def call_json_text(model: str, system: str, user: str, max_tokens: int, log_name: str,
                   effort: str | None = None) -> str:
    """call_text with one retry when the output is truncated: a bigger ceiling
    and an instruction to be terser. Deep assessments need this headroom."""
    try:
        return call_text(model, system, user, max_tokens, log_name, effort=effort)
    except TruncatedOutput:
        terse = system + ("\n\nOUTPUT LENGTH: your previous answer was cut off. Keep every string under 300 "
                          "characters, at most 6 items per list, and no prose outside the JSON.")
        return call_text(model, terse, user, int(max_tokens * 1.5), log_name, effort=effort)
