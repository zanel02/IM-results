"""
Ironman results storage layer.

DB layout:
  event_groups  — one row per group page UUID (the URL you curl)
  races         — one row per race-year, keyed by wtc_eventid UUID
  results       — one row per athlete result

CLI usage:
  # register races from a group page (reads HTML from stdin):
  curl -sL "https://labs-v2.competitor.com/results/event/<UUID>" | python storage.py register <group-uuid>

  # store results for a specific race (reads API JSON from stdin):
  curl -sL "https://labs-v2.competitor.com/api/results?wtc_eventid=<UUID>" | python storage.py ingest <wtc_eventid>

  # check what's in the DB:
  python storage.py status
"""

import re
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

DB_PATH = Path(__file__).parent / "data" / "ironman.db"


# ── connection & schema ──────────────────────────────────────────────────────

def get_connection() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db() -> None:
    with get_connection() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS event_groups (
                id          INTEGER PRIMARY KEY,
                group_uuid  TEXT UNIQUE NOT NULL,
                name        TEXT,
                sport       TEXT,
                fetched_at  TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS races (
                id                  INTEGER PRIMARY KEY,
                group_id            INTEGER REFERENCES event_groups(id),
                wtc_eventid         TEXT UNIQUE NOT NULL,
                event_name          TEXT,
                external_name       TEXT,
                event_date          TEXT,
                year                INTEGER,
                distance_type       TEXT,
                results_fetched_at  TEXT
            );

            CREATE TABLE IF NOT EXISTS results (
                id                   INTEGER PRIMARY KEY,
                race_id              INTEGER NOT NULL REFERENCES races(id),
                wtc_resultid         TEXT UNIQUE NOT NULL,
                bib                  TEXT,
                athlete_name         TEXT,
                first_name           TEXT,
                last_name            TEXT,
                gender               TEXT,
                city                 TEXT,
                state                TEXT,
                country_iso2         TEXT,
                country_representing TEXT,
                age_group            TEXT,
                status               TEXT,
                finish_secs          INTEGER,
                swim_secs            INTEGER,
                t1_secs              INTEGER,
                bike_secs            INTEGER,
                t2_secs              INTEGER,
                run_secs             INTEGER,
                finish_fmt           TEXT,
                swim_fmt             TEXT,
                t1_fmt               TEXT,
                bike_fmt             TEXT,
                t2_fmt               TEXT,
                run_fmt              TEXT,
                rank_overall         INTEGER,
                rank_gender          INTEGER,
                rank_division        INTEGER,
                swim_rank_overall    INTEGER,
                swim_rank_gender     INTEGER,
                swim_rank_division   INTEGER,
                bike_rank_overall    INTEGER,
                bike_rank_gender     INTEGER,
                bike_rank_division   INTEGER,
                run_rank_overall     INTEGER,
                run_rank_gender      INTEGER,
                run_rank_division    INTEGER,
                awa_points           REAL,
                swim_dist_km         REAL,
                bike_dist_km         REAL,
                run_dist_km          REAL,
                total_dist_km        REAL
            );

            CREATE TABLE IF NOT EXISTS race_weather (
                id              INTEGER PRIMARY KEY,
                race_id         INTEGER UNIQUE NOT NULL REFERENCES races(id),
                fetched_at      TEXT NOT NULL,
                venue_lat       REAL NOT NULL,
                venue_lon       REAL NOT NULL,
                timezone        TEXT,
                hourly_json     TEXT NOT NULL,
                temp_f_7am      REAL,
                temp_f_high     REAL,
                total_precip_in REAL,
                avg_wind_mph    REAL
            );

            CREATE INDEX IF NOT EXISTS idx_results_race     ON results(race_id);
            CREATE INDEX IF NOT EXISTS idx_results_athlete  ON results(athlete_name);
            CREATE INDEX IF NOT EXISTS idx_results_status   ON results(status);
            CREATE INDEX IF NOT EXISTS idx_races_year       ON races(year);
            CREATE INDEX IF NOT EXISTS idx_races_type       ON races(distance_type);
        """)
    print(f"Database ready at {DB_PATH}")


# ── public API ───────────────────────────────────────────────────────────────

def register_event_group(group_uuid: str, html: str) -> int:
    """
    Parse __NEXT_DATA__ from the event group page HTML and upsert the group +
    all its sub-races (without results). Returns the event_group row id.
    """
    next_data = _extract_next_data(html)
    pp = next_data["props"]["pageProps"]
    sport = pp.get("sport", "Triathlon")
    subevents = pp.get("subevents", [])
    name = subevents[0].get("wtc_name") if subevents else group_uuid

    with get_connection() as conn:
        cur = conn.execute(
            """
            INSERT INTO event_groups (group_uuid, name, sport, fetched_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(group_uuid) DO UPDATE SET
                name=excluded.name, sport=excluded.sport, fetched_at=excluded.fetched_at
            """,
            (group_uuid, name, sport, _now()),
        )
        group_id = cur.lastrowid or conn.execute(
            "SELECT id FROM event_groups WHERE group_uuid = ?", (group_uuid,)
        ).fetchone()["id"]

        for s in subevents:
            conn.execute(
                """
                INSERT INTO races
                    (group_id, wtc_eventid, event_name, external_name,
                     event_date, year, distance_type)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(wtc_eventid) DO UPDATE SET
                    event_name=excluded.event_name,
                    external_name=excluded.external_name,
                    event_date=excluded.event_date,
                    year=excluded.year,
                    distance_type=excluded.distance_type
                """,
                (
                    group_id,
                    s.get("wtc_eventid"),
                    s.get("wtc_name") or s.get("label"),
                    s.get("wtc_externaleventname"),
                    s.get("wtc_eventdate"),
                    _year_from_str(s.get("wtc_name")),
                    _distance_from_str(s.get("wtc_name")),
                ),
            )
        conn.commit()

    print(f"Registered group '{name}' — {len(subevents)} races")
    return group_id


def is_race_fetched(wtc_eventid: str) -> bool:
    """True if results for this race are already stored."""
    with get_connection() as conn:
        row = conn.execute(
            "SELECT results_fetched_at FROM races WHERE wtc_eventid = ?",
            (wtc_eventid,),
        ).fetchone()
    return row is not None and row["results_fetched_at"] is not None


def save_results(wtc_eventid: str, api_response: dict) -> int:
    """
    Persist results from /api/results?wtc_eventid=... JSON.
    Idempotent — skips silently if already stored. Returns row count saved.
    """
    if is_race_fetched(wtc_eventid):
        print(f"Already stored: {wtc_eventid} — skipping")
        return 0

    rows = api_response.get("resultsJson", {}).get("value", [])
    if not rows:
        print(f"No results in response for {wtc_eventid}")
        return 0

    with get_connection() as conn:
        race_row = conn.execute(
            "SELECT id FROM races WHERE wtc_eventid = ?", (wtc_eventid,)
        ).fetchone()

        if race_row is None:
            # Race wasn't registered via register_event_group — create a stub row
            first = rows[0]
            event_name = first.get("_wtc_eventid_value_formatted", wtc_eventid)
            cur = conn.execute(
                """INSERT INTO races (wtc_eventid, event_name, year, distance_type)
                   VALUES (?, ?, ?, ?)""",
                (
                    wtc_eventid,
                    event_name,
                    _year_from_str(event_name),
                    _distance_from_result(first),
                ),
            )
            race_id = cur.lastrowid
        else:
            race_id = race_row["id"]

        conn.executemany(
            """
            INSERT INTO results (
                race_id, wtc_resultid, bib, athlete_name, first_name, last_name,
                gender, city, state, country_iso2, country_representing, age_group,
                status,
                finish_secs, swim_secs, t1_secs, bike_secs, t2_secs, run_secs,
                finish_fmt, swim_fmt, t1_fmt, bike_fmt, t2_fmt, run_fmt,
                rank_overall, rank_gender, rank_division,
                swim_rank_overall, swim_rank_gender, swim_rank_division,
                bike_rank_overall, bike_rank_gender, bike_rank_division,
                run_rank_overall, run_rank_gender, run_rank_division,
                awa_points, swim_dist_km, bike_dist_km, run_dist_km, total_dist_km
            ) VALUES (
                :race_id, :wtc_resultid, :bib, :athlete_name, :first_name, :last_name,
                :gender, :city, :state, :country_iso2, :country_representing, :age_group,
                :status,
                :finish_secs, :swim_secs, :t1_secs, :bike_secs, :t2_secs, :run_secs,
                :finish_fmt, :swim_fmt, :t1_fmt, :bike_fmt, :t2_fmt, :run_fmt,
                :rank_overall, :rank_gender, :rank_division,
                :swim_rank_overall, :swim_rank_gender, :swim_rank_division,
                :bike_rank_overall, :bike_rank_gender, :bike_rank_division,
                :run_rank_overall, :run_rank_gender, :run_rank_division,
                :awa_points, :swim_dist_km, :bike_dist_km, :run_dist_km, :total_dist_km
            ) ON CONFLICT(wtc_resultid) DO NOTHING
            """,
            [_map_result(race_id, r) for r in rows],
        )
        conn.execute(
            "UPDATE races SET results_fetched_at = ? WHERE id = ?",
            (_now(), race_id),
        )
        conn.commit()

    print(f"Saved {len(rows)} results for {wtc_eventid}")
    return len(rows)


def list_races() -> list[dict]:
    """Return all races with their fetch status."""
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT r.id, r.wtc_eventid, r.event_name, r.event_date, r.year, r.distance_type,
                   eg.group_uuid,
                   r.results_fetched_at IS NOT NULL AS fetched
            FROM races r
            LEFT JOIN event_groups eg ON r.group_id = eg.id
            ORDER BY r.year DESC, r.distance_type, r.event_name
            """
        ).fetchall()
    return [dict(r) for r in rows]


# ── internal helpers ─────────────────────────────────────────────────────────

def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _extract_next_data(html: str) -> dict:
    m = re.search(
        r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>',
        html,
        re.DOTALL,
    )
    if not m:
        raise ValueError("__NEXT_DATA__ script tag not found in HTML")
    return json.loads(m.group(1))


def _year_from_str(s: str | None) -> int | None:
    if not s:
        return None
    m = re.search(r"\b(20\d{2})\b", s)
    return int(m.group(1)) if m else None


def _distance_from_str(s: str | None) -> str | None:
    if not s:
        return None
    if "70.3" in s:
        return "IRONMAN 70.3"
    if re.search(r"\bIRONMAN\b", s, re.IGNORECASE):
        return "IRONMAN"
    return None


def _distance_from_result(r: dict) -> str | None:
    brand = (r.get("wtc_AgeGroupId") or {}).get("_wtc_brandid_value_formatted")
    return brand or _distance_from_str(r.get("_wtc_eventid_value_formatted"))


def _int(val: Any) -> int | None:
    try:
        return int(val)
    except (TypeError, ValueError):
        return None


def _float(val: Any) -> float | None:
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


def _map_result(race_id: int, r: dict) -> dict:
    contact = r.get("wtc_ContactId") or {}
    age_group = r.get("wtc_AgeGroupId") or {}
    country_rep = r.get("wtc_CountryRepresentingId") or {}

    if r.get("wtc_dns"):
        status = "DNS"
    elif r.get("wtc_dnf"):
        status = "DNF"
    elif r.get("wtc_dq"):
        status = "DQ"
    else:
        status = "FIN"

    return {
        "race_id":              race_id,
        "wtc_resultid":         r.get("wtc_resultid"),
        "bib":                  r.get("bib") or _int(r.get("wtc_bibnumber")),
        "athlete_name":         r.get("athlete") or contact.get("fullname"),
        "first_name":           contact.get("firstname"),
        "last_name":            contact.get("lastname"),
        "gender":               contact.get("gendercode_formatted"),
        "city":                 contact.get("address1_city"),
        "state":                contact.get("address1_stateorprovince"),
        "country_iso2":         r.get("countryiso2"),
        "country_representing": country_rep.get("wtc_iso2") or country_rep.get("wtc_name"),
        "age_group":            age_group.get("wtc_agegroupname") or r.get("_wtc_agegroupid_value_formatted"),
        "status":               status,
        "finish_secs":          _int(r.get("wtc_finishtime")),
        "swim_secs":            _int(r.get("wtc_swimtime")),
        "t1_secs":              _int(r.get("wtc_transition1time")),
        "bike_secs":            _int(r.get("wtc_biketime")),
        "t2_secs":              _int(r.get("wtc_transition2time")),
        "run_secs":             _int(r.get("wtc_runtime")),
        "finish_fmt":           r.get("wtc_finishtimeformatted"),
        "swim_fmt":             r.get("wtc_swimtimeformatted"),
        "t1_fmt":               r.get("wtc_transition1timeformatted"),
        "bike_fmt":             r.get("wtc_biketimeformatted"),
        "t2_fmt":               r.get("wtc_transitiontime2formatted"),
        "run_fmt":              r.get("wtc_runtimeformatted"),
        "rank_overall":         _int(r.get("wtc_finishrankoverall")),
        "rank_gender":          _int(r.get("wtc_finishrankgender")),
        "rank_division":        _int(r.get("wtc_finishrankgroup")),
        "swim_rank_overall":    _int(r.get("wtc_swimrankoverall")),
        "swim_rank_gender":     _int(r.get("wtc_swimrankgender")),
        "swim_rank_division":   _int(r.get("wtc_swimrankgroup")),
        "bike_rank_overall":    _int(r.get("wtc_bikerankoverall")),
        "bike_rank_gender":     _int(r.get("wtc_bikerankgender")),  # string "1" in API — _int handles it
        "bike_rank_division":   _int(r.get("wtc_bikerankgroup")),
        "run_rank_overall":     _int(r.get("wtc_runrankoverall")),
        "run_rank_gender":      _int(r.get("wtc_runrankgender")),
        "run_rank_division":    _int(r.get("wtc_runrankgroup")),
        "awa_points":           _float(r.get("wtc_points")),
        "swim_dist_km":         _float(r.get("wtc_swimdistancecompleted")),
        "bike_dist_km":         _float(r.get("wtc_bikedistancecompleted")),
        "run_dist_km":          _float(r.get("wtc_rundistancecompleted")),
        "total_dist_km":        _float(r.get("wtc_totaldistancecompleted")),
    }


# ── CLI ──────────────────────────────────────────────────────────────────────

def _cli():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    cmd = sys.argv[1]

    if cmd == "init":
        init_db()

    elif cmd == "register":
        # reads HTML from stdin
        if len(sys.argv) < 3:
            print("Usage: ... | python storage.py register <group-uuid>")
            sys.exit(1)
        init_db()
        html = sys.stdin.read()
        register_event_group(sys.argv[2], html)

    elif cmd == "ingest":
        # reads results API JSON from stdin
        if len(sys.argv) < 3:
            print("Usage: ... | python storage.py ingest <wtc_eventid>")
            sys.exit(1)
        init_db()
        data = json.load(sys.stdin)
        save_results(sys.argv[2], data)

    elif cmd == "status":
        init_db()
        races = list_races()
        if not races:
            print("No races registered yet.")
            return
        fetched = sum(1 for r in races if r["fetched"])
        print(f"\n{'EVENT':55} {'YEAR':6} {'TYPE':14} {'FETCHED'}")
        print("-" * 90)
        for r in races:
            tick = "✓" if r["fetched"] else " "
            print(f"[{tick}] {r['event_name'] or r['wtc_eventid']:52} {r['year'] or '':6} {r['distance_type'] or '':14}")
        print(f"\n{fetched}/{len(races)} races have results stored.")

    else:
        print(f"Unknown command: {cmd}")
        print("Commands: init | register <uuid> | ingest <uuid> | status")
        sys.exit(1)


if __name__ == "__main__":
    _cli()
