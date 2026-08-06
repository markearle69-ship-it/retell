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
- Also includes a **self-hosted, cookie-free visitor analytics platform**
  (`/admin/analytics`) for the same sites — page views, unique visitors, and
  the search query / referrer / UTM campaign that brought them, without
  using Google Analytics or Search Console on any of your sites. See
  [Visitor analytics](#visitor-analytics-self-hosted-no-google) below.

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

## Visitor analytics (self-hosted, no Google)

A separate, self-hosted page-view analytics platform for the same portfolio of
microsites — deliberately **not** Google Analytics / Search Console, so there's
no shared script, cookie, or account tying your sites together. Each site
loads a small snippet from this service and posts a beacon back to it; nothing
talks to Google.

1. **Add a site**: `/admin/analytics` → "Add a site" (name + domain). This
   generates a `site_key` and gives you a snippet:

   ```html
   <script defer data-site="SITE_KEY" src="https://<your-domain>/t.js"></script>
   ```

   Paste it once, near the end of `<body>`, on that microsite. The same
   `/t.js` file is served for every site — it reads its own key off the
   `data-site` attribute — so there's nothing per-site to host yourself.

2. **What you get per site** (`/admin/analytics/sites/{id}`): view counts and
   unique-visitor counts for 24h/7d/30d plus a daily table, top pages, top
   non-search referrers, and — the "what query got them here" part — search
   engine + search query, and UTM source/campaign/term for anything you've
   tagged.

3. **The honest limit on search queries**: Google has stripped the search
   term from its outbound referrer for organic results since 2013 (this is
   the origin of "(not provided)" in GA too) — no self-hosted tool can get
   around that, it's stripped before it ever leaves Google. Bing, DuckDuckGo,
   Yahoo, Yandex, and Baidu still pass the term through, and those show up
   directly under "Search queries". If these sites run paid traffic, set
   `utm_term={keyword}` (Google Ads ValueTrack — or Bing Ads' equivalent) on
   the destination URL and you get exact keyword-level attribution
   reliably, including from Google Ads.

4. **Privacy / what's stored**: no cookies, no localStorage, no third-party
   requests. No raw IP address or full user-agent string is ever written to
   the database — `/collect` only persists a one-way HMAC of
   `(ip, user-agent, site key, calendar day)`, keyed by `ANALYTICS_SALT`, so
   the same visitor hashes differently every day and the hash can't be
   reversed back to an IP. Only coarse device type (mobile/tablet/desktop)
   and browser family are stored, not the raw UA string. This reduces (but
   doesn't eliminate) compliance work compared to GA — you're still running
   analytics on real visitors, so check what a cookieless-analytics
   exemption looks like under GDPR/ePrivacy (and CCPA if relevant) in the
   jurisdictions your visitors are in; this isn't legal advice.
   Common crawlers/bots are flagged (`is_bot`) and excluded from the visitor
   counts, though a determined bot can still spoof a normal user-agent.

5. **Regenerating a key** (`Regenerate key` on a site's detail page)
   invalidates the old snippet immediately — update the `<script>` tag on the
   site afterward or it'll silently stop recording.

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
