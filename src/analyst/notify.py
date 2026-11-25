"""Push Kora's findings to you: Telegram and/or any JSON webhook
(Discord/Slack-compatible). Configure in .env:

    TELEGRAM_BOT_TOKEN=...   TELEGRAM_CHAT_ID=...
    KORA_WEBHOOK_URL=...     (optional, e.g. a Discord channel webhook)
    DASHBOARD_PUBLIC_URL=... (optional, adds a link to the full breakdown)

Every alert is de-duplicated (same setup on the same instrument at a
similar entry won't re-alert for 6 hours).
"""
from __future__ import annotations

import json
import os
import sys
import urllib.request

from src.store.db import alert_already_sent, mark_alert_sent


def channels() -> list[str]:
    out = []
    if os.environ.get("TELEGRAM_BOT_TOKEN") and os.environ.get("TELEGRAM_CHAT_ID"):
        out.append("telegram")
    if os.environ.get("KORA_WEBHOOK_URL"):
        out.append("webhook")
    return out


def _post(url: str, payload: dict) -> None:
    req = urllib.request.Request(url, data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json", "User-Agent": "kora-terminal/1.0"})
    urllib.request.urlopen(req, timeout=15).read()


def send(text: str) -> bool:
    ok = False
    if "telegram" in channels():
        try:
            _post(f"https://api.telegram.org/bot{os.environ['TELEGRAM_BOT_TOKEN']}/sendMessage",
                  {"chat_id": os.environ["TELEGRAM_CHAT_ID"], "text": text, "disable_web_page_preview": True})
            ok = True
        except Exception as e:
            print(f"telegram send failed: {e}", file=sys.stderr)
    if "webhook" in channels():
        try:
            _post(os.environ["KORA_WEBHOOK_URL"], {"text": text, "content": text[:1990]})
            ok = True
        except Exception as e:
            print(f"webhook send failed: {e}", file=sys.stderr)
    return ok


def notify_once(conn, key: str, text: str, within_hours: float = 6) -> bool:
    if not channels() or alert_already_sent(conn, key, within_hours):
        return False
    if send(text):
        mark_alert_sent(conn, key)
        return True
    return False


def setup_message(report: dict, cand: dict) -> str:
    d = report["digits"]
    link = os.environ.get("DASHBOARD_PUBLIC_URL")
    lines = [
        f"KORA · {cand['grade']}-grade setup · {report['code']} {cand['direction'].upper()}",
        f"{cand['setup_name']} on {cand['timeframe']} — confidence {cand['confidence']}%",
        f"Entry {cand['entry']:,.{d}f} ({cand['entry_type']})  SL {cand['stop']:,.{d}f}",
        f"TP1 {cand['tp1']:,.{d}f} ({cand['rr1']}R)  TP2 {cand['tp2']:,.{d}f} ({cand['rr2']}R)",
        f"Bias {report['bias']} · regime {report['regime']['primary']} · {report['alignment']} TFs aligned",
        "Why: " + "; ".join(cand["confluence"][:3]),
    ]
    if cand["against"]:
        lines.append("Against: " + "; ".join(cand["against"][:2]))
    lines.append(cand["calibration"]["note"])
    lines.append("Invalidation: " + cand["invalidation"])
    if link:
        lines.append(f"{link.rstrip('/')}/kora/{report['code']}")
    lines.append("Analysis only — your decision, your execution.")
    return "\n".join(lines)
