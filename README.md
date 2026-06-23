# IM-results

Historical Ironman triathlon results dashboard. Scrapes data from the WTC/Competitor internal API, stores it locally in SQLite, and presents it via a Streamlit web app.

## Setup

```bash
pip install streamlit pandas numpy altair
```

The database is built automatically on first launch from seed files in `data/seed/`. No manual setup needed.

## Running the dashboard

```bash
streamlit run app.py
```

Opens at `http://localhost:8501`. Three tabs:

- **Race Results** — browse finisher tables, time distributions, and race-day weather (hourly temp, precip, and wind) for a single race/year/age group
- **Year-over-Year Comparison** — overlay finish-time distributions and segment splits across years; includes weather summary per year
- **Athlete Search** — look up any athlete's full history across all races in the DB

## Adding new race data

### Step 1 — find the group UUID

Each race series has a group UUID on the Competitor results platform. You can find UUIDs two ways:

**Option A — scrape ironman.com** (discovers all ~190 current races):
```bash
python3 ironman_scraper.py --sitemap --output ironman_races.json
```

**Option B — fetch a specific page** you already know:
```bash
python3 ironman_scraper.py --fetch-page https://www.ironman.com/races/im703-chattanooga/results
```

Both old (`/im703-coeur-dalene-results`) and new (`/races/im703-coeur-dalene/results`) URL formats work.

### Step 2 — fetch results and weather

```bash
python3 fetch.py <GROUP_UUID>
```

This script:
1. Registers all race years in the group
2. Fetches results for any race year not yet stored
3. Geocodes each race location and fetches race-day weather from Open-Meteo
4. Is fully idempotent — safe to re-run

To backfill weather for existing races, re-run `fetch.py` with the same UUID.

## Current data

10 race series, 88 races, ~170k finisher results.

| Race series | Years |
|---|---|
| IRONMAN 70.3 Boise | 2008–2015, 2025 |
| IRONMAN 70.3 Calgary | 2009–2019, 2021–2025 |
| IRONMAN 70.3 Coeur d'Alene | 2016–2019, 2022, 2024–2026 |
| IRONMAN 70.3 La Quinta | 2018–2019, 2021–2025 |
| IRONMAN 70.3 Oceanside | 2005–2026 |
| IRONMAN 70.3 Oregon (Salem) | 2021–2025 |
| IRONMAN 70.3 Rockford | 2025–2026 |
| IRONMAN 70.3 Santa Cruz | 2015–2019, 2021–2025 |
| IRONMAN 70.3 Washington Tri-Cities | 2021–2025 |
| IRONMAN California | 2022–2025 |

## Docs

See [`docs/`](docs/) for detailed reference on the API structure and database schema.
