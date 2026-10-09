# Going live checklist

What has to be true before Siloam staff and principals use the app with real prices.
Items marked **Blocker** must be done first.

## 1. Sign-in
- **Siloam staff** sign in with email + password (`dashboard/auth.py`, `PasswordSignIn`).
  Passwords are scrypt-hashed in `app.db`; nobody, admins included, can read them.
  - **First admin:** set `NEGO_ADMIN_EMAIL`, and either `NEGO_ADMIN_PASSWORD` (first start only),
    or leave it empty and open the one-time link the server prints in its log on first start.
  - **Everyone else:** Admin → Users → Invite. The app shows a one-time link (valid 7 days); send
    it to the person and they choose their own password. **Reset link** on the same row makes a
    new one if someone forgets theirs.
  - Eight failed sign-ins from one address within 15 minutes are blocked for a while.
  - `NEGO_AUTH=dev` switches back to email-only sign-in. Use it only for local demos and tests.
  - Microsoft Entra ID (SSO) can be added later as another provider with the same `authenticate()`.
- **Principals (vendors)** don't have accounts. Each negotiation step sends them a private link
  (email/WhatsApp). Links are hashed, expire, can be revoked, and too many bad links from one
  address get blocked.

## 2. Hosting
- **HTTPS:** run behind a reverse proxy (IIS, nginx, Azure App Gateway). The app sends HSTS
  over HTTPS, and cookies become `Secure` automatically.
- **Network:** allow outbound HTTPS to Power Automate (`*.logic.azure.com` /
  `*.environment.api.powerplatform.com`). If Claude drafting is on, also allow `api.anthropic.com`.
- **One process:** run a single app process (one uvicorn worker). The reminder scheduler and
  message sender run inside it, and de-duplication keys stop repeats after restarts.
- **Backups:** back up the workspace folder (`NEGO_HOME`) daily. It holds:
  - `app.db` (users, audit);
  - `nego.db` (negotiations, prices, findings, messages);
  - `nego_docs/` (encrypted company documents).

  Keep `NEGO_DOC_KEY` in a separate place: without it the documents can't be read.

## 3. Environment variables (secrets go in the server's secret store, never in git)

| Variable | Needed for | Notes |
|---|---|---|
| `NEGO_HOME` | everything | Workspace folder (databases, documents). |
| `NEGO_SECRET` | sessions | A long random string. Without it, a key file in the workspace is used. |
| `NEGO_ADMIN_EMAIL` | first start | The first admin account. Invite everyone else from Admin → Users. |
| `NEGO_ADMIN_PASSWORD` | optional | First admin's password on first start. Without it, use the link printed in the log. |
| `NEGO_DOC_KEY` | documents | Fernet key: `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"` |
| `NEGO_PUBLIC_URL` | principal links | e.g. `https://nego.siloamhospitals.com` |
| `NEGO_NOTIFY_WEBHOOK_URL` | automatic sending | The Power Automate trigger URL (see `POWER_AUTOMATE.md`). |
| `NEGO_NOTIFY_SECRET` | optional | HMAC signature header for the webhook. |
| `NEGO_NOTIFY_ADMINS` | notices to Siloam | Comma-separated emails for "principal sent" notices and the MOU alert. |
| `NEGO_HELP_WHATSAPP` | vendor help | Number for the portal's **Bantuan** button (wa.me link), e.g. `+6281234567890`. |
| `NEGO_HELP_EMAIL` | vendor help | Address for the same button. |
| `ANTHROPIC_API_KEY` | optional | Claude drafting. Also turn on "Draft messages with Claude" in Engine settings. |

## 4. Data
- **Price data:** load the real price history in Admin → Price data. This replaces the sample, and
  the yellow "SAMPLE DATA" banner disappears.
- **Principals:** import the principal list in Negotiations → Import principals. It needs
  contact email and WhatsApp numbers for automatic sending.
- **Settings:** check the values in Admin → Engine settings:
  - PPN 11%;
  - the counter-offer cap;
  - the escalation limit;
  - the reminder days;
  - the rebate breakage rate, which stays empty until Finance measures it.
- **Templates:** swap in Siloam's own Excel Confirmation and BAK templates when they're
  available. The package uses generic layouts until then.

## 5. Messages
- **Power Automate:** build the flow and submit both WhatsApp templates to Meta, as in
  `POWER_AUTOMATE.md`.
- **Test it:** send a test (`POST /api/admin/nego/notify/test`), then open one negotiation to a
  principal step with your own email and phone as the contact.

## 6. People
- **Siloam staff:** invite viewers and admins (Admin → Users). Admins can change data and
  settings, and every change is logged (Admin → Activity).
- **Pilot:** start with one or two principals before sending links to all 200+.
