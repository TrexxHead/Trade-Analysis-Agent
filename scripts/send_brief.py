#!/usr/bin/env python
"""Build Kora's brief from the latest analysis and send it now to every
configured channel (Telegram / ntfy / email / webhook). Prints it too.

    python scripts/send_brief.py            # print + send
    python scripts/send_brief.py --dry-run  # print only
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotenv import load_dotenv

load_dotenv()

from src.analyst import briefing, notify  # noqa: E402
from src.store.db import kora_connection  # noqa: E402

if __name__ == "__main__":
    text = briefing.build(kora_connection())
    print(text)
    if "--dry-run" in sys.argv:
        sys.exit(0)
    if not notify.channels():
        sys.exit("\nNo channel configured - see docs/alerts_setup.md")
    print("\nSent." if notify.send(text, title=text.splitlines()[0]) else "\nSend failed (see errors above).")
