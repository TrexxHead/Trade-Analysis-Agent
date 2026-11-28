# Alerts and the daily brief: free setup

Kora sends two kinds of message to every channel you configure:
- **Alerts:** A/B-grade setups, high-impact news 15 minutes out, positions with no stop.
- **The brief:** your account, the whole board with Kora's plans, today's
  red-folder news and Kora's track record. Sent at the times set in
  **Settings → Daily brief** (default 07:00 and 13:00, your PC's local time).
  **Send a brief now** sends one immediately.

All four channels below are free. Use one or several. After editing `.env`,
restart Kora and the dashboard.

## Telegram (recommended)
1. In Telegram, open **@BotFather**, send `/newbot`, pick a name and a
   username ending in `bot`. Copy the **token** it gives you.
2. Open your new bot and send it any message (e.g. "hi").
3. In a browser, open
   `https://api.telegram.org/bot<YOUR_TOKEN>/getUpdates`. Find `"chat":{"id":`
   followed by a number. That number is your **chat id**.
4. Add to `.env`:
   ```
   TELEGRAM_BOT_TOKEN=123456:ABC...
   TELEGRAM_CHAT_ID=123456789
   ```

## ntfy (free push-notification app, no account)
1. Install **ntfy** from the App Store or Google Play.
2. Make up a long, hard-to-guess topic name, e.g. `kora-joseph-7f3k9q2x`.
   Anyone who knows the name can read it, so don't use something obvious.
3. In the app, tap **+** and subscribe to that topic.
4. Add to `.env`:
   ```
   NTFY_TOPIC=kora-joseph-7f3k9q2x
   ```

## Email (Gmail)
1. Turn on 2-Step Verification on your Google account, then create an
   **App password** at https://myaccount.google.com/apppasswords.
2. Add to `.env`:
   ```
   SMTP_HOST=smtp.gmail.com
   SMTP_PORT=587
   SMTP_USER=you@gmail.com
   SMTP_PASSWORD=the-16-character-app-password
   BRIEF_EMAIL_TO=you@gmail.com
   ```
   Outlook/Hotmail: `SMTP_HOST=smtp-mail.outlook.com`, port 587, with an app
   password if your account uses 2-step sign-in.

## Discord (or Slack)
Server settings → Integrations → Webhooks → New webhook → copy the URL:
```
KORA_WEBHOOK_URL=https://discord.com/api/webhooks/...
```

## Check it works
- Settings → Notifications → **Send test alert**
- Settings → Daily brief → **Send a brief now** (or `python scripts/send_brief.py`)

Briefs only go out while Kora (`scripts/run_kora.py`) is running, so have it
start at logon (see `docs/remote_access.md`).
