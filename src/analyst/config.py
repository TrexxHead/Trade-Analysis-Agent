"""Watchlist + user settings shared by the Kora service and the hub."""
from __future__ import annotations

import copy
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
INSTRUMENTS_PATH = ROOT / "config" / "instruments.yaml"

DEFAULT_SETTINGS = {
    "interval_minutes": 15,       # how often Kora re-analyses the watchlist
    "alert_threshold": 65,        # min confidence (%) for a setup to trigger a notification
    "notify": {"setups": True, "watch": False, "news": True, "risk": True},
    "llm_narratives": False,      # write a "Kora's take" paragraph per breakdown via Claude (costs API tokens)
    "disabled": [],               # instrument codes excluded from analysis
    "user_initial": "D",
    "pnl_mode": "usd",
}


def load_watchlist() -> dict:
    return yaml.safe_load(INSTRUMENTS_PATH.read_text())


def instruments(include_disabled: bool = True, settings: dict | None = None) -> list[dict]:
    items = load_watchlist()["instruments"]
    if include_disabled or not settings:
        return items
    off = set(settings.get("disabled", []))
    return [m for m in items if m["code"] not in off]


def by_code() -> dict[str, dict]:
    return {m["code"]: m for m in instruments()}


def get_settings(conn) -> dict:
    from src.store.db import kv_get
    s = copy.deepcopy(DEFAULT_SETTINGS)
    saved = kv_get(conn, "settings", {}) or {}
    for k, v in saved.items():
        if isinstance(v, dict) and isinstance(s.get(k), dict):
            s[k].update(v)
        else:
            s[k] = v
    return s
