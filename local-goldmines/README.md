# Local Goldmines

Research tool for local-SEO site networks: pick the affluent towns around a hub
city, find the sub-niches worth building pages for, and spot the quoted-phrase
searches with almost no competition.

Built in three steps:

1. **Areas** (built). Maps the towns around a hub city in 8 compass slices
   (N, NE … NW), ranked by affluence. You tick the 6–8 you want per site, then
   drill into any town to pick its mini suburbs.
2. **Sub-services** (next). Suggests 15–20 sub-niches for the main niche with
   search volume; you pick 6–8.
3. **Competition** (next). For every sub-service + location, gets the quoted-phrase
   result count (≤10 = green), plus allintitle, map pack and competitor strength
   for page 1.

## Step 1: how places are ranked

| | UK | US |
|---|---|---|
| Towns / suburbs | OpenStreetMap (city, town, village, suburb) | Census Gazetteer (every city/town/CDP), bundled |
| Income | ONS avg household income per neighbourhood (MSOA), FYE 2023 | ACS 2023 median household income |
| House prices | ONS median price paid per MSOA, year to Mar 2023 | ACS 2023 median home value |
| Population | OSM figure → Census 2021 ward → sum of nearby neighbourhoods (marked *) | ACS 2023 |

UK places are sampled at their centre and 4 points ~0.6 miles out, so one
unusual street doesn't skew a town. Affluence is scored 0–100 **relative to the
other places on the same map** (income 60%, house prices 40%). The ★ marks the
most affluent place in each slice. Scotland and Northern Ireland have no
income/price data in these ONS files, so those columns show "—" there.

Default radius: 10 miles around the hub, 5 miles around each town
(15 miles reach each way). Both can be changed per map.

Every "Save picks" stores what was on offer, the stats for each place, and what
you chose (`selection_logs` table). That history is the training data for the
v2 auto-picker.

## Running on Railway

This folder is its own app. In the Railway project:

1. **New → GitHub Repo →** the `retell` repo. In the new service's
   **Settings → Source**, set **Root Directory** to `local-goldmines`.
2. **New → Database → PostgreSQL**. Railway sets `DATABASE_URL`; reference it
   on this service (Variables → Add reference → `DATABASE_URL`).
3. Set these variables:
   - `APP_PASSWORD`: your login password (required; login is refused while it's empty)
   - `CONTACT_EMAIL`: your email (OpenStreetMap asks apps to identify themselves)
   - `CENSUS_API_KEY`: for US research (see below)
   - `DATAFORSEO_LOGIN`, `DATAFORSEO_PASSWORD`, `ANTHROPIC_API_KEY`: used from Step 2
4. **Settings → Networking → Generate Domain** (or add your own domain).

## Getting a US Census API key (free, 2 minutes)

1. Go to https://api.census.gov/data/key_signup.html
2. Enter your organisation name (anything, e.g. your agency name) and email,
   tick the terms, and submit.
3. Open the email from the Census Bureau and click the link to **activate**
   the key. It won't work until activated.
4. Paste the key into Railway as `CENSUS_API_KEY`.

## Local development

```bash
pip install -r requirements-dev.txt
cp .env.example .env   # set APP_PASSWORD
uvicorn app.main:app --reload
pytest
```

## Data refresh

The bundled files in `app/data/` come from:
- `uk_msoa_income.csv`: ONS "Income estimates for small areas, England and Wales", FYE 2023 (Total annual income)
- `uk_msoa_house_price.csv`: ONS HPSSA Dataset 2, median price paid by MSOA (latest quarter)
- `uk_msoa_population.csv`, `uk_ward_population.csv`: Census 2021 TS001 via Nomis
- `us_places.csv`: Census 2023 Gazetteer places file

Map tiles come from OpenStreetMap's free tile server, which is fine for one
user. A paid tile provider will be needed if this becomes a public SaaS.
