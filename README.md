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
- Exposes simple admin-only JSON endpoints (`/tenants`, `/leads`) for
  scripting, plus a password-protected admin panel at `/admin` for the same
  thing through a browser.
- Optionally **auto-provisions new sites**: pick a niche template in the
  panel and it creates the Retell LLM + agent from that niche's prompt,
  attaches your already-purchased Twilio number to your shared SIP trunk,
  and imports the number into Retell pointed at the new agent — the only
  manual step left is buying the number in Twilio.

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
     dashboard (see note on this below the Twilio settings).
   - `ADMIN_API_KEY` — any long random string. Used two ways: as the
     `X-Admin-Key` header for the `/tenants`/`/leads` API, and as the login
     password for the `/admin` panel.
   - `DATABASE_URL` — defaults to a local SQLite file. Point it at Postgres
     for production (e.g. `postgresql+psycopg2://user:pass@host:5432/db`).
   - SMTP settings for email notifications.
   - Twilio settings for SMS notifications (reuse your existing Twilio
     account/credentials — this is separate from the SIP trunk config, just
     used to send outbound texts to tenants).
   - `TWILIO_TRUNK_SID` / `TWILIO_SIP_USERNAME` / `TWILIO_SIP_PASSWORD` —
     only needed if you want the auto-provisioning feature (see below). These
     come from the one-time manual trunk setup: the trunk's SID and the
     termination credential list you created on it.
   - `RETELL_API_KEY` is used both to verify webhook signatures and — if you
     use auto-provisioning — to call Retell's agent/LLM/phone-number APIs.
     Retell docs note only the key with the "webhook badge" verifies
     signatures; if agent-management calls fail with that same key, check
     whether your account needs a separate full-access key for those calls.

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

5. **Register your tenants and check leads — via the admin panel**

   Go to `https://<your-domain>/admin`, log in with `ADMIN_API_KEY` as the
   password. One entry per rank-and-rent site: Agent/site name, the Twilio
   number that site forwards to Retell, and the tenant's forwarding SMS
   number + email. The panel also shows the exact webhook URL to paste into
   Retell, a table of all sites with their lead counts, and a feed of recent
   calls (click through for the full transcript). Editing a site re-submits
   the same form pre-filled; deleting a site unassigns (doesn't delete) its
   past leads.

   The same thing is available as a script/API if you'd rather automate it:

   ```bash
   python scripts/seed_tenant.py \
     --business-name "Joe's Plumbing" \
     --to-number +15551234567 \
     --notify-email joe@example.com \
     --notify-sms +15559876543
   ```

   ```bash
   curl -X POST https://<your-domain>/tenants \
     -H "X-Admin-Key: $ADMIN_API_KEY" \
     -H "Content-Type: application/json" \
     -d '{"business_name": "Joe'\''s Plumbing", "to_number": "+15551234567",
          "notify_email": "joe@example.com", "notify_sms_number": "+15559876543"}'

   curl https://<your-domain>/leads -H "X-Admin-Key: $ADMIN_API_KEY"
   curl https://<your-domain>/leads/1 -H "X-Admin-Key: $ADMIN_API_KEY"   # includes full transcript
   ```

   A call that arrives before its `to_number` has a matching tenant is still
   stored (with `tenant_id` null, visible in the panel as "Unassigned") — you
   won't lose the lead, you'll just see a warning in the logs and no
   notification goes out until you add that tenant and a later event
   re-matches, or you backfill manually.

6. **(Optional) Auto-provisioning new sites**

   This replaces most of the manual Twilio + Retell setup per site with one
   form submit. It assumes **one shared SIP trunk** for every site — Twilio
   trunks support many phone numbers, so you only do the trunk setup once:

   - In Twilio: create the Elastic SIP Trunk, enable Call Transfer, add a
     termination credential (username/password), add
     `sip:sip.retellai.com` as the origination URI. This is steps 2–5 of
     your original manual process — do it exactly once, not per site.
   - Put that trunk's SID in `TWILIO_TRUNK_SID`, and the credential
     username/password in `TWILIO_SIP_USERNAME` / `TWILIO_SIP_PASSWORD`.

   Then in `/admin/niches`, add a niche template per trade (e.g. "Auto AC
   Repair", "Oil Tank Removal") — a name, a Retell `general_prompt` with
   `{{business_name}}`, `{{location}}`, and `{{zip_codes}}` placeholders
   anywhere the per-site details should be substituted in, a Retell voice
   ID, and a model.

   From then on, adding a new site is: buy the Twilio number yourself (the
   one step that spends money, kept manual on purpose), then on `/admin`
   fill in the site form with a niche selected. Submitting it:

   1. Renders that niche's prompt with the site's business name/location/zip
      codes and creates a Retell LLM + Agent from it.
   2. Looks up the Twilio number you bought and attaches it to your shared
      trunk.
   3. Imports the number into Retell via SIP, pointed at the new agent
      (this is the equivalent of your manual step 8).

   Each step only runs if it hasn't already succeeded for that site, so if
   something fails partway (shown as an error banner with the exact cause —
   e.g. "number not found, buy it first"), fixing the issue and resubmitting
   the same form only retries what's left. A niche is just a starting point:
   after provisioning, tweak the agent/prompt further in Retell's dashboard
   as needed — the panel doesn't try to keep them in sync afterward.

## (Optional) Auto-provisioning socials

This does for each site's socials what section 6 above does for its phone
agent: one form submit generates a batch of on-brand captions, renders a
branded creative for each (if configured), and schedules them - instead of
setting each site up by hand in a scheduler's UI 60+ times.

**What it doesn't automate:** linking each site's actual Facebook/Instagram
accounts. That's an OAuth step and platforms require a human to click
through it - there's no API workaround. It's a one-time click per new site,
not per post, in Ayrshare's own dashboard (Business Plan → Profiles → open
the site's profile → connect accounts). Everything before and after that one
click is automated.

**Google Business Profile is deliberately not a default platform.** GBP
requires a verified physical/service-area listing tied to a real,
contactable business, and mass-created listings on rank-and-rent sites get
suspended quickly - if you don't already hold a verified listing for a site,
don't fight this; skip GBP for it. Add `gmb` to a niche's platforms only for
sites where you genuinely hold one.

1. **Set up the three integrations** (all in `.env`, see `.env.example`):
   - [Ayrshare](https://ayrshare.com) Business Plan → `AYRSHARE_API_KEY`.
     Every site gets its own "profile" under this one account.
   - [Anthropic](https://console.anthropic.com) → `ANTHROPIC_API_KEY`. Writes
     the captions, varying wording/hook/local detail per post so 60+ sites in
     the same niche don't read as duplicated content.
   - [Bannerbear](https://bannerbear.com) → `BANNERBEAR_API_KEY` +
     `BANNERBEAR_TEMPLATE_UID`, optional. Renders a branded image per post
     from a template with text layers named `caption` and `business_name`,
     and an optional image layer named `logo`. Leave blank to post text-only.

2. In `/admin/niches`, add **content pillars** (one topic/angle per line,
   e.g. "a quick maintenance tip", "a common warning sign", "why choose a
   local licensed pro") and a caption **voice/style** to each niche - these
   drive what Claude writes for every site in that niche. Optionally override
   platforms or the Bannerbear template per niche.

3. On a site's row in `/admin`, once it has a niche selected, click into its
   **Social** page and hit **Provision social**. First run creates its
   Ayrshare profile (shows you the profile key + the one-time reminder to
   link its social accounts in Ayrshare's dashboard), generates a batch of
   captions + creatives, and schedules them. Re-running it (manually, or via
   the "Provision social for all sites" button on the dashboard) only tops up
   the queue back to the configured batch size - already-scheduled posts and
   an already-created profile are left alone.

4. If a specific post fails (e.g. a creative render error), it's marked
   **Failed** with the reason shown on the site's Social page - re-running
   "Provision social" retries only that post, not the whole batch.

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
  you deploy multiple instances or want concurrent writes at higher volume
  (Railway wipes local SQLite files on redeploy — use a Volume or Postgres).
- If a Retell agent creation succeeds but the following step fails, the LLM
  resource it created stays behind unused on Retell's side (harmless, but
  you may want to delete it manually from Retell's dashboard).
- Changing which niche a site uses after it's already provisioned doesn't
  automatically recreate its agent — check "Force re-provision" on the edit
  form if you want a fresh agent/LLM built from the newly selected niche.
