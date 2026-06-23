# IM-results

Historical Ironman triathlon results dashboard. Scrapes data from the WTC/Competitor internal API, stores it locally in SQLite, and presents it via a Streamlit web app.

## Setup

```bash
pip install streamlit pandas numpy
```

The database is built automatically on first launch from seed files in `data/seed/`. No manual setup needed.

## Running the dashboard

```bash
streamlit run app.py
```

Opens at `http://localhost:8501`. Three tabs:

- **Race Results** — browse finisher tables, time distributions, and race-day weather for a single race/year/age group
- **Year-over-Year Comparison** — overlay finish-time distributions and segment splits across years; includes weather summary per year
- **Athlete Search** — look up any athlete's full history across all races in the DB

## Adding new race data

Use `fetch.py` to add a new race series. Find the group UUID from the Competitor results URL (`https://labs-v2.competitor.com/results/event/<GROUP_UUID>`), then:

```bash
python3 fetch.py <GROUP_UUID>
```

This script:
1. Registers all race years in the group
2. Fetches results for any race year not yet stored
3. Geocodes each race location and fetches race-day weather from Open-Meteo
4. Is fully idempotent — safe to re-run

To backfill weather for existing races, just re-run `fetch.py` with the same UUID(s).

## Current data

| Race series | Years |
|---|---|
| IRONMAN 70.3 Coeur d'Alene | 2016, 2017, 2018, 2019, 2022, 2024, 2025, 2026 |
| IRONMAN 70.3 Oregon (Salem) | 2021, 2022, 2023, 2024, 2025 |

## Docs

See [`docs/`](docs/) for detailed reference on the API structure and database schema.
