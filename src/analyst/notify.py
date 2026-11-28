"""Push Kora's findings to you - all free channels, configure any in .env:

    TELEGRAM_BOT_TOKEN=...   TELEGRAM_CHAT_ID=...      Telegram bot
    NTFY_TOPIC=...           (NTFY_SERVER=https://ntfy.sh)  ntfy push app, no account
    SMTP_HOST / SMTP_PORT / SMTP_USER / SMTP_PASSWORD / BRIEF_EMAIL_TO   email
    KORA_WEBHOOK_URL=...     Discord / Slack incoming webhook
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
    if os.environ.get("NTFY_TOPIC"):
        out.append("ntfy")
    if os.environ.get("SMTP_HOST") and os.environ.get("SMTP_USER") and os.environ.get("BRIEF_EMAIL_TO"):
        out.append("email")
    return out


def _post(url: str, payload: dict) -> None:
    req = urllib.request.Request(url, data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json", "User-Agent": "kora-terminal/1.0"})
    urllib.request.urlopen(req, timeout=15).read()


def _ntfy(text: str, title: str) -> None:
    server = (os.environ.get("NTFY_SERVER") or "https://ntfy.sh").rstrip("/")
    req = urllib.request.Request(f"{server}/{os.environ['NTFY_TOPIC']}", data=text.encode(),
                                 headers={"Title": title.replace("·", "-").encode("ascii", "ignore").decode(), "Tags": "chart_with_upwards_trend",
                                          "User-Agent": "kora-terminal/1.0"})
    urllib.request.urlopen(req, timeout=15).read()


def _email(text: str, title: str) -> None:
    import smtplib
    from email.message import EmailMessage
    msg = EmailMessage()
    msg["Subject"], msg["From"], msg["To"] = title, os.environ["SMTP_USER"], os.environ["BRIEF_EMAIL_TO"]
    msg.set_content(text)
    port = int(os.environ.get("SMTP_PORT") or 587)
    with smtplib.SMTP(os.environ["SMTP_HOST"], port, timeout=20) as s:
        s.starttls()
        s.login(os.environ["SMTP_USER"], os.environ.get("SMTP_PASSWORD", ""))
        s.send_message(msg)


def send(text: str, title: str = "Kora") -> bool:
    ok = False
    if "telegram" in channels():
        try:
            for i in range(0, len(text), 3900):  # Telegram caps a message at 4096 characters
                _post(f"https://api.telegram.org/bot{os.environ['TELEGRAM_BOT_TOKEN']}/sendMessage",
                      {"chat_id": os.environ["TELEGRAM_CHAT_ID"], "text": text[i:i + 3900], "disable_web_page_preview": True})
            ok = True
        except Exception as e:
            print(f"telegram send failed: {e}", file=sys.stderr)
    if "ntfy" in channels():
        try:
            _ntfy(text, title)
            ok = True
        except Exception as e:
            print(f"ntfy send failed: {e}", file=sys.stderr)
    if "email" in channels():
        try:
            _email(text, title)
            ok = True
        except Exception as e:
            print(f"email send failed: {e}", file=sys.stderr)
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
