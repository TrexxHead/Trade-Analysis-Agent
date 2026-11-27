"""Instrument catalog, your watchlist, and user settings - shared by the Kora
service and the hub.

config/instruments.yaml is the catalog. Your watchlist (which instruments
Kora analyses and the hub shows by default) and any custom instruments you
add live in the database, edited from the hub."""
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
    "brief": {"enabled": True, "times": ["07:00", "13:00"], "weekdays_only": False},  # local time on the PC
    "llm_narratives": False,      # write a "Kora's take" paragraph per breakdown via Claude (costs API tokens)
    "watchlist": None,            # list of codes; None = config default_watchlist
    "custom_instruments": [],     # user-added instruments (same fields as the catalog)
    "setups_off": {},             # {code: [setup ids Kora should not look for on that instrument]}
    "user_initial": "D",
    "pnl_mode": "usd",
}


def load_watchlist() -> dict:
    """The raw catalog file (name kept for backwards compatibility)."""
    return yaml.safe_load(INSTRUMENTS_PATH.read_text())


def _conn_settings() -> dict | None:
    try:
        from src.store.db import kora_connection
        return get_settings(kora_connection())
    except Exception:
        return None


def _symbol_specs() -> dict:
    try:
        from src.store.db import kora_connection, kv_get
        return kv_get(kora_connection(), "symbol_specs", {}) or {}
    except Exception:
        return {}


def catalog(settings: dict | None = None) -> list[dict]:
    specs = _symbol_specs()
    items = []
    for m in load_watchlist()["instruments"]:
        s = specs.get(m["code"]) or {}
        # the broker's own precision (read from MT5) wins over the config default
        items.append({**m, "digits": s["digits"]} if s.get("digits") is not None else dict(m))
    settings = settings if settings is not None else _conn_settings()
    known = {m["code"] for m in items}
    for c in (settings or {}).get("custom_instruments", []) or []:
        if c.get("code") and c["code"] not in known:
            s = specs.get(c["code"]) or {}
            items.append({"kind": "Custom", "digits": 2, "currencies": [], **c, "custom": True,
                          **({"digits": s["digits"]} if s.get("digits") is not None else {})})
    return items


def watchlist_codes(settings: dict | None = None) -> list[str]:
    settings = settings if settings is not None else _conn_settings()
    wl = (settings or {}).get("watchlist")
    if wl is None:
        wl = load_watchlist().get("default_watchlist") or [m["code"] for m in load_watchlist()["instruments"]]
    return list(wl)


def instruments(include_disabled: bool = True, settings: dict | None = None) -> list[dict]:
    """include_disabled=True -> the whole catalog (+ custom); False -> watchlist only,
    in watchlist order."""
    settings = settings if settings is not None else _conn_settings()
    items = catalog(settings)
    if include_disabled:
        return items
    by = {m["code"]: m for m in items}
    return [by[c] for c in watchlist_codes(settings) if c in by]


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
    if "disabled" in saved and saved.get("watchlist") is None:  # migrate the old on/off list
        s["watchlist"] = [m["code"] for m in load_watchlist()["instruments"] if m["code"] not in saved["disabled"]]
    return s
