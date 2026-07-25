# Retell Lead Router

Captures Retell.ai voice-agent call data (summary, caller number, sentiment,
recording) for inbound calls on your rank-and-rent sites, and immediately
alerts the right local business owner (tenant) by email and SMS so they can
follow up with the lead.

## How it fits your setup

```
Caller -> Twilio number -> SIP trunk -> Retell agent -> webhook -> this service -> tenant (email + SMS)
```

Each of your rank-and-rent sites has its own Twilio number forwarding to a
Retell agent. This service maps the **`to_number`** Retell reports on each
call to a **tenant** (the local biz owner who should get that lead), stores
the call, and notifies them once Retell finishes analyzing the call.

Nothing about your existing Twilio SIP trunk / Retell agent config needs to
change — you're just adding a webhook destination in Retell.

## What it does

- Exposes `POST /webhooks/retell` and verifies Retell's `x-retell-signature`
  header (HMAC-SHA256 over `raw_body + timestamp`, keyed with your Retell API
  key, 5-minute replay window) before trusting any payload.
- On `call_analyzed` events, pulls `call_summary`, `user_sentiment`,
  `call_successful`, `from_number`, `recording_url`, transcript, etc. and
  upserts a `Lead` row keyed by `call_id`.
- Looks up the `Tenant` whose `to_number` matches the call's `to_number`, and
  sends them an email + SMS with the caller's number and the call summary as
  soon as the summary is ready.
- Exposes simple admin-only JSON endpoints (`/tenants`, `/leads`) so you can
  manage tenants and pull lead history (e.g. to build a dashboard later, or
  just query it directly).

## Setup

1. **Install dependencies**

   ```bash
   python3 -m venv .venv && source .venv/bin/activate
   pip install -r requirements.txt
   ```

2. **Configure environment**

   ```bash
   cp .env.example .env
   ```

   Fill in:
   - `RETELL_API_KEY` — the API key with the "webhook" badge in your Retell
     dashboard (used only to verify signatures, not to call Retell's API).
   - `ADMIN_API_KEY` — any long random string; sent as `X-Admin-Key` to manage
     tenants/leads.
   - `DATABASE_URL` — defaults to a local SQLite file. Point it at Postgres
     for production (e.g. `postgresql+psycopg2://user:pass@host:5432/db`).
   - SMTP settings for email notifications.
   - Twilio settings for SMS notifications (reuse your existing Twilio
     account/credentials — this is separate from the SIP trunk config, just
     used to send outbound texts to tenants).

3. **Run it**

   ```bash
   uvicorn app.main:app --host 0.0.0.0 --port 8000
   ```

   Or with Docker:

   ```bash
   docker build -t retell-lead-router .
   docker run --env-file .env -p 8000:8000 retell-lead-router
   ```

   Deploy anywhere that can run a container or a Python process and give you
   a public HTTPS URL (Render, Railway, Fly.io, a small VPS behind Caddy/
   nginx, etc). Retell needs to be able to reach it over HTTPS.

4. **Point Retell at your webhook**

   In the Retell dashboard, set your agent's (or account-level) webhook URL
   to `https://<your-domain>/webhooks/retell` and enable at least the
   `call_analyzed` event (add `call_started`/`call_ended` too if you want
   earlier records, though the summary only exists once `call_analyzed`
   fires).

5. **Register your tenants**

   One entry per rank-and-rent site, keyed by the Twilio number that site
   forwards to Retell:

   ```bash
   python scripts/seed_tenant.py \
     --business-name "Joe's Plumbing" \
     --to-number +15551234567 \
     --notify-email joe@example.com \
     --notify-sms +15559876543
   ```

   Or via the API:

   ```bash
   curl -X POST https://<your-domain>/tenants \
     -H "X-Admin-Key: $ADMIN_API_KEY" \
     -H "Content-Type: application/json" \
     -d '{"business_name": "Joe'\''s Plumbing", "to_number": "+15551234567",
          "notify_email": "joe@example.com", "notify_sms_number": "+15559876543"}'
   ```

6. **Check leads**

   ```bash
   curl https://<your-domain>/leads -H "X-Admin-Key: $ADMIN_API_KEY"
   curl https://<your-domain>/leads/1 -H "X-Admin-Key: $ADMIN_API_KEY"   # includes full transcript
   ```

   A call that arrives before its `to_number` has a matching tenant is still
   stored (with `tenant_id` null) — you won't lose the lead, you'll just see
   a warning in the logs and no notification goes out until you add that
   tenant and a later event re-matches, or you backfill manually.

## Running tests

```bash
pip install -r requirements-dev.txt
pytest
```

## Notes / future enhancements

- Notifications only fire on `call_analyzed` (summary/sentiment available).
  If you want an instant "phone rang" alert too, add a `call_started` branch
  in `app/main.py`'s webhook handler.
- For a proper tenant-facing UI (lead list, mark as "contacted", etc.) put a
  small frontend on top of the existing `/leads` and `/tenants` endpoints, or
  swap the admin-key auth for per-tenant login.
- SQLite is fine for a single instance; move `DATABASE_URL` to Postgres if
  you deploy multiple instances or want concurrent writes at higher volume.
