# Database Schema

SQLite database at `data/ironman.db`. Initialized via `python3 storage.py init`.

WAL mode is enabled (`PRAGMA journal_mode=WAL`) and foreign keys are enforced (`PRAGMA foreign_keys=ON`).

---

## Tables

### `event_groups`

One row per race series (the umbrella across all years of a given event).

| Column | Type | Notes |
|---|---|---|
| `id` | INTEGER PK | Auto-increment |
| `group_uuid` | TEXT UNIQUE | The UUID from the Competitor URL: `.../results/event/<group_uuid>` |
| `name` | TEXT | Series name pulled from the first subevent's `wtc_name`, e.g. `"2026 IRONMAN 70.3 Coeur d'Alene"`. Contains the most-recent year as a prefix — strip with `r"^\d{4}\s+"` to get a clean display name. |
| `sport` | TEXT | `"Triathlon"` |
| `fetched_at` | TEXT | ISO 8601 UTC timestamp of when the group page was registered |

---

### `races`

One row per race-year. Populated by `storage.py register` (from the group page subevents list). Results are fetched separately.

| Column | Type | Notes |
|---|---|---|
| `id` | INTEGER PK | Auto-increment |
| `group_id` | INTEGER FK → `event_groups.id` | The parent series |
| `wtc_eventid` | TEXT UNIQUE | UUID for this specific race year; used as the key for the results API |
| `event_name` | TEXT | Full name, e.g. `"2025 IRONMAN 70.3 Oregon"` |
| `external_name` | TEXT | Short code, e.g. `"IRM-OREGON703-2025"` |
| `event_date` | TEXT | ISO 8601 datetime of the race |
| `year` | INTEGER | Parsed from `event_name` |
| `distance_type` | TEXT | `"IRONMAN 70.3"` or `"IRONMAN"` (full distance) |
| `results_fetched_at` | TEXT | NULL until results are ingested. Used as a fetch-once gate — `storage.py ingest` is a no-op if this is non-NULL. |

**Key query pattern:**
```sql
-- Only races with results loaded
WHERE results_fetched_at IS NOT NULL
```

---

### `results`

One row per athlete per race. Populated by `storage.py ingest`.

| Column | Type | Notes |
|---|---|---|
| `id` | INTEGER PK | Auto-increment |
| `race_id` | INTEGER FK → `races.id` | |
| `wtc_resultid` | TEXT UNIQUE | WTC's unique result ID; dedup key for idempotent inserts |
| `bib` | TEXT | Bib number |
| `athlete_name` | TEXT | Display name |
| `first_name` | TEXT | |
| `last_name` | TEXT | |
| `gender` | TEXT | `"Male"` / `"Female"` |
| `city` | TEXT | Home city |
| `state` | TEXT | State/province |
| `country_iso2` | TEXT | ISO 2-letter country |
| `country_representing` | TEXT | Country athlete competed under (may differ from `country_iso2`) |
| `age_group` | TEXT | e.g. `"M35-39"`, `"F25-29"` |
| `status` | TEXT | `"FIN"`, `"DNF"`, `"DNS"`, `"DQ"` |
| `finish_secs` | INTEGER | Total finish time in seconds (NULL for non-finishers) |
| `swim_secs` | INTEGER | Swim segment in seconds |
| `t1_secs` | INTEGER | Transition 1 in seconds |
| `bike_secs` | INTEGER | Bike segment in seconds |
| `t2_secs` | INTEGER | Transition 2 in seconds |
| `run_secs` | INTEGER | Run segment in seconds |
| `finish_fmt` | TEXT | Formatted string, e.g. `"4:32:18"` |
| `swim_fmt` | TEXT | |
| `t1_fmt` | TEXT | |
| `bike_fmt` | TEXT | |
| `t2_fmt` | TEXT | |
| `run_fmt` | TEXT | |
| `rank_overall` | INTEGER | Overall finish rank |
| `rank_gender` | INTEGER | Rank within gender |
| `rank_division` | INTEGER | Rank within age group |
| `swim_rank_overall` | INTEGER | |
| `swim_rank_gender` | INTEGER | |
| `swim_rank_division` | INTEGER | |
| `bike_rank_overall` | INTEGER | |
| `bike_rank_gender` | INTEGER | |
| `bike_rank_division` | INTEGER | |
| `run_rank_overall` | INTEGER | |
| `run_rank_gender` | INTEGER | |
| `run_rank_division` | INTEGER | |
| `awa_points` | REAL | AWA qualification points |
| `swim_dist_km` | REAL | Distance completed in km (~0.5 for 70.3, ~1.9 for full) |
| `bike_dist_km` | REAL | |
| `run_dist_km` | REAL | |
| `total_dist_km` | REAL | |

**Quirks:**
- The API returns `wtc_bikerankgender` as a string `"1"` instead of an integer. `storage.py` uses `_int()` to cast it safely.
- DNF/DNS athletes still have a result row with `status = "DNF"/"DNS"` and NULL times.
- Distances are in **km**, not miles.

---

## Indexes

```sql
CREATE INDEX idx_results_race     ON results(race_id);
CREATE INDEX idx_results_athlete  ON results(athlete_name);
CREATE INDEX idx_results_status   ON results(status);
CREATE INDEX idx_races_year       ON races(year);
CREATE INDEX idx_races_type       ON races(distance_type);
```

---

## Idempotency

- **Re-registering a group** is safe — uses `ON CONFLICT(group_uuid) DO UPDATE` and `ON CONFLICT(wtc_eventid) DO UPDATE`.
- **Re-ingesting a race** is a no-op — `is_race_fetched()` checks `results_fetched_at IS NOT NULL` and returns early before touching the DB.
- **Individual result rows** use `ON CONFLICT(wtc_resultid) DO NOTHING` as a secondary guard.

---

## Common queries

```sql
-- All races with results, newest first
SELECT r.event_name, r.year, r.distance_type, eg.name as series
FROM races r
JOIN event_groups eg ON r.group_id = eg.id
WHERE r.results_fetched_at IS NOT NULL
ORDER BY r.year DESC;

-- Finishers for a specific race by age group
SELECT athlete_name, age_group, finish_fmt, rank_overall, rank_division
FROM results
WHERE race_id = ? AND status = 'FIN' AND age_group = ?
ORDER BY rank_division;

-- Year-over-year median finish time for a series
SELECT r.year, AVG(res.finish_secs) as avg_secs, COUNT(*) as finishers
FROM results res
JOIN races r ON res.race_id = r.id
JOIN event_groups eg ON r.group_id = eg.id
WHERE eg.group_uuid = ? AND res.status = 'FIN'
GROUP BY r.year
ORDER BY r.year;
```
