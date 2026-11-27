# Reaching Kora Terminal from any device (free)

Kora reads your MT5 terminal, so it runs on your Windows PC. Remote access
means reaching that PC securely from your phone, tablet or another computer.
Both options below are free, need no router or port changes, and keep the
dashboard bound to `127.0.0.1` on the PC.

Whichever you choose, the PC must be **on, logged in, with MT5 open** (see
"Keep it running" at the bottom).

## Option A — Tailscale (recommended: private, simplest)

Your devices join a private network only you can see. Nothing is exposed to
the public internet. Free personal plan: up to 100 devices.

1. Install Tailscale on the PC (https://tailscale.com/download) and sign in.
2. Install the Tailscale app on your phone/tablet/laptop and sign in with the
   **same account**.
3. On the PC, with the dashboard running, open a terminal and run:
   ```
   tailscale serve --bg 5000
   ```
   It prints an address like `https://laptop-4us2eq7n.tail1234.ts.net`. That
   address works from any of your signed-in devices, anywhere, over HTTPS.
   `--bg` keeps it running across restarts.
4. On your phone, open that address and use "Add to Home Screen" for an
   app-like icon.

To stop sharing: `tailscale serve --https=443 off`.

## Option B — Cloudflare Tunnel on your own domain (any browser, no app)

Gives a normal web address such as `https://kora.yourdomain.com` that opens
in any browser. **Protect it with Cloudflare Access** so only you can sign in.
Requirements: your domain's DNS on Cloudflare (free plan; your registrar
stays the same).

1. Add the domain to Cloudflare (free plan) and switch its nameservers as
   Cloudflare instructs.
2. Install `cloudflared` on the PC:
   ```
   winget install --id Cloudflare.cloudflared
   ```
3. Create the tunnel and point a subdomain at it:
   ```
   cloudflared tunnel login
   cloudflared tunnel create kora
   cloudflared tunnel route dns kora kora.yourdomain.com
   ```
4. Create `C:\Users\<you>\.cloudflared\config.yml`:
   ```yaml
   tunnel: kora
   credentials-file: C:\Users\<you>\.cloudflared\<tunnel-id>.json
   ingress:
     - hostname: kora.yourdomain.com
       service: http://localhost:5000
     - service: http_status:404
   ```
5. Run it as a Windows service so it starts with the PC:
   ```
   cloudflared service install
   ```
6. **Lock it down.** Cloudflare dashboard → Zero Trust (free up to 50 users)
   → Access → Applications → add a self-hosted app for
   `kora.yourdomain.com`. Add a policy allowing only your email, with the
   one-time PIN login method. Visitors must then verify by email before they
   ever reach the dashboard.

Quick test with no domain or account (temporary random URL, so lock down
with `DASHBOARD_PASSWORD` first):
```
cloudflared tunnel --url http://localhost:5000
```

## Always set a dashboard password

Before either option, add to `.env` and restart the dashboard:
```
DASHBOARD_PASSWORD=choose-a-long-password
```
Tailscale is private to your devices, and Cloudflare Access adds an email
login in front, but the password is a second lock that costs nothing.

## Keep it running

- **Auto-start at logon** (Git Bash, once):
  ```
  WINPATH=$(pwd -W)
  MSYS_NO_PATHCONV=1 schtasks /create /tn "KoraAgent" /tr "\"$WINPATH/run_kora.bat\"" /sc onlogon /rl highest /f
  MSYS_NO_PATHCONV=1 schtasks /create /tn "TradeAgentDashboard" /tr "\"$WINPATH/run_dashboard.bat\"" /sc onlogon /rl highest /f
  ```
- **Start MT5 at logon:** put a shortcut to the Deriv MT5 app in
  `shell:startup` (Win+R → `shell:startup`). Tick "Save login info" in MT5.
- **Stop the PC sleeping** while plugged in: Settings → System → Power →
  Screen and sleep → "When plugged in, put my device to sleep after: Never",
  or `powercfg /change standby-timeout-ac 0`.
- If the laptop is off, Kora is off. For 24/7 coverage without your laptop,
  the only route is a Windows VPS running MT5 (paid, roughly $10–20/month).
