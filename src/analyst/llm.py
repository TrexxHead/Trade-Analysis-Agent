"""Claude integration: the "Ask Kora" chat and optional per-instrument
narratives. Needs ANTHROPIC_API_KEY (or an `ant auth login` profile).
Everything degrades gracefully when no credential is configured.
"""
from __future__ import annotations

import json
import os
import sys

MODEL = os.environ.get("KORA_MODEL", "claude-opus-5-5")

PERSONA = (
    "You are Kora, the user's market-analysis agent inside the Kora Terminal (Deriv MT5: synthetic indices, "
    "gold, silver, FX). You do NOT place or manage trades - you assess markets, explain setups and risks, "
    "and the user makes every decision and executes it themselves. Personality: precise, candid, calm - a "
    "professional quant colleague, never salesy, never hyping. Ground every answer in the CONTEXT JSON and "
    "cite its actual numbers (levels, confidence, hit-rates). Be explicit about uncertainty, small samples "
    "and what would invalidate an idea. Never promise outcomes. If asked something the context can't "
    "answer, say what data you'd need."
)


def available() -> bool:
    if os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"):
        return True
    try:
        import anthropic  # noqa: F401  - a stored `ant auth login` profile also works
        return bool(os.environ.get("ANTHROPIC_PROFILE")) or os.path.exists(
            os.path.expanduser("~/.config/anthropic"))
    except ImportError:
        return False


def _create(**kwargs):
    import anthropic
    client = anthropic.Anthropic()
    try:
        # Server-side fallback: if the primary model declines, the API re-runs on a fallback model.
        resp = client.beta.messages.create(betas=["server-side-fallback-2026-07-01"], fallbacks="default", **kwargs)
    except (TypeError, anthropic.BadRequestError):
        resp = client.messages.create(**kwargs)
    if resp.stop_reason == "refusal":
        return None
    return "".join(b.text for b in resp.content if b.type == "text").strip()


def chat(context: dict, history: list[dict]) -> str:
    msgs = [{"role": "user" if m["role"] == "user" else "assistant", "content": m["text"]} for m in history[-8:]]
    while msgs and msgs[0]["role"] != "user":
        msgs.pop(0)
    if not msgs:
        return "Ask me something about the markets, your setups, or your trading."
    system = (PERSONA + " Reply in plain conversational text: no markdown headings; short paragraphs; use a "
              "compact list only when listing levels. Under 180 words unless the user asks for a full breakdown "
              "or report.\n\nCONTEXT: " + json.dumps(context, default=str))
    try:
        text = _create(model=MODEL, max_tokens=1500, system=system, messages=msgs,
                       output_config={"effort": "medium"})
        return text or "I can't help with that one - try rephrasing it around the markets or your trades."
    except Exception as e:
        print(f"chat failed: {e}", file=sys.stderr)
        return "I couldn't reach my reasoning service just now - check ANTHROPIC_API_KEY in .env and try again."


def narrative(report: dict) -> str | None:
    slim = {k: report[k] for k in ("code", "name", "price", "bias", "confidence", "alignment", "regime", "reads",
                                   "key_levels", "fundamentals", "candidates", "verdict", "prev_day")}
    prompt = ("Write 'Kora's take' on this instrument for a discretionary trader: 120-200 words, plain prose, "
              "no headings. Cover: what the higher timeframes say, where price sits relative to key levels, the "
              "best plan if any (exact entry/stop/targets and why), what would invalidate it, and any event "
              "risk. If there is no setup, say what you'd wait for.\n\nANALYSIS: " + json.dumps(slim, default=str))
    try:
        return _create(model=MODEL, max_tokens=1200, system=PERSONA,
                       messages=[{"role": "user", "content": prompt}], output_config={"effort": "low"})
    except Exception as e:
        print(f"narrative failed for {report.get('code')}: {e}", file=sys.stderr)
        return None
