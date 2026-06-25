# IM-results

Historical Ironman triathlon results dashboard. Scrapes data from the WTC/Competitor internal API, stores it locally in SQLite, and presents it via a Streamlit web app.

## Setup

```bash
pip install streamlit pandas numpy altair plotly
```

The database is built automatically on first launch from seed files in `data/seed/`. No manual setup needed.

## Running the dashboard

```bash
streamlit run app.py
```

Opens at `http://localhost:8501`. Five tabs:

- **Race Results** — browse finisher tables, time distributions, and race-day weather (hourly temp, precip, and wind compass) for a single race year and age group
- **Year-over-Year Comparison** — for a selected race series: overlay finish-time distributions across years, percentile segment splits (swim/bike/run), participation by age group with year-over-year delta charts, and weather summary per year
- **Athlete Search** — look up any athlete by name and see their full history across all races in the DB
- **Analytics** — aggregate trends across any set of races; filter by gender, age range, distance, and year range; shows participant counts, finish time trends, and age group participation breakdowns (overall, male, female) with % change charts
- **Race Map** — world map of all race venues; click a marker to see results inline

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
python3 fetch.py <GROUP_UUID> [<GROUP_UUID> ...]
```

This script:
1. Registers all race years in the group
2. Fetches results for any race year not yet stored
3. Geocodes each race location and fetches race-day weather from Open-Meteo
4. Is fully idempotent — safe to re-run

To skip weather fetching:
```bash
python3 fetch.py --no-weather <GROUP_UUID>
```

## Current data

191 race series · 1,522 races · ~2.05M finisher results

Covers the full global IRONMAN / IRONMAN 70.3 / 5150 calendar, with individual series going back as far as 2002. The database includes races across North America, Europe, Asia-Pacific, Latin America, the Middle East, and Africa.

## Docs

See [`docs/`](docs/) for detailed reference:

- [`docs/database.md`](docs/database.md) — SQLite schema, indexes, and common query patterns
- [`docs/api.md`](docs/api.md) — WTC/Competitor API structure (reverse-engineered)
