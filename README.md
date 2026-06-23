# IM-results

Historical Ironman triathlon results dashboard. Scrapes data from the WTC/Competitor internal API, stores it locally in SQLite, and presents it via a Streamlit web app.

## Setup

```bash
pip install streamlit pandas numpy
python3 storage.py init
```

## Running the dashboard

```bash
streamlit run app.py
```

Opens at `http://localhost:8501`. Two tabs:

- **Race Results** — browse finisher tables and time distributions for a single race/year/age group
- **Year-over-Year Comparison** — overlay finish-time distributions and median segment splits across years for a race series

## Adding new race data

Data is loaded manually in two steps: register the event group (gets the list of races), then ingest results for each individual race.

### 1. Register an event group

Find the group UUID from the Competitor results URL:
`https://labs-v2.competitor.com/results/event/<GROUP_UUID>`

```bash
curl -sL "https://labs-v2.competitor.com/results/event/<GROUP_UUID>" \
  | python3 storage.py register <GROUP_UUID>
```

This upserts the `event_groups` row and stubs out a `races` row for every sub-event (year) in the group.

### 2. Ingest results for each race

```bash
python3 storage.py status          # lists all races and which have results
```

For each race that hasn't been fetched (no ✓):

```bash
curl -sL "https://labs-v2.competitor.com/api/results?wtc_eventid=<WTC_EVENT_UUID>" \
  | python3 storage.py ingest <WTC_EVENT_UUID>
```

Ingestion is idempotent — re-running a fetch that's already stored is a no-op.

## Current data

| Race series | Years |
|---|---|
| IRONMAN 70.3 Coeur d'Alene | 2016, 2017, 2018, 2019, 2022, 2024, 2025, 2026 |
| IRONMAN 70.3 Oregon (Salem) | 2021, 2022, 2023, 2024, 2025 |

## Docs

See [`docs/`](docs/) for detailed reference on the API structure and database schema.
