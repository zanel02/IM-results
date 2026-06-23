"""
Fetch and store all results for an Ironman event group.

Usage:
    python3 fetch.py <group-uuid> [<group-uuid> ...]

Given a group UUID (from the labs-v2.competitor.com/results/event/<UUID> URL),
this script:
  1. Downloads the group page and registers all sub-events (race years)
  2. Fetches and stores results for every sub-event that hasn't been ingested yet
  3. Fetches race-day weather for every sub-event with results but no weather yet
  4. Skips anything already stored (fully idempotent)
"""

import json
import sys
import time
import urllib.request
from storage import init_db, register_event_group, save_results, list_races
from weather import fetch_and_save_weather, is_weather_fetched

BASE = "https://labs-v2.competitor.com"
HEADERS = {"User-Agent": "Mozilla/5.0"}


def fetch(url: str) -> str | bytes:
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read().decode("utf-8")


def fetch_group(group_uuid: str) -> None:
    print(f"\n── {group_uuid}")

    # 1. Register the group (download HTML, parse __NEXT_DATA__, upsert races)
    print(f"  Fetching group page...")
    html = fetch(f"{BASE}/results/event/{group_uuid}")
    register_event_group(group_uuid, html)

    # 2. Find sub-events that still need results
    all_races = list_races()
    group_races = [r for r in all_races if r.get("group_uuid") == group_uuid]
    pending = [r for r in group_races if not r["fetched"]]

    if not pending:
        print("  All sub-events already ingested.")
    else:
        # 3. Fetch results for each pending sub-event
        for race in pending:
            wtc_id = race["wtc_eventid"]
            name = race["event_name"] or wtc_id
            print(f"  Fetching results for {name}...")
            url = f"{BASE}/api/results?wtc_eventid={wtc_id}"
            data = json.loads(fetch(url))
            count = save_results(wtc_id, data)
            print(f"  Saved {count} results.")
            time.sleep(0.5)

    # 4. Fetch weather for all group races with results but no weather yet
    # Re-query so newly-fetched races show fetched=True
    all_races = list_races()
    group_races = [r for r in all_races if r.get("group_uuid") == group_uuid]
    needs_weather = [r for r in group_races if r["fetched"] and not is_weather_fetched(r["id"])]

    if not needs_weather:
        print("  Weather up to date for all sub-events.")
    else:
        for race in needs_weather:
            try:
                fetch_and_save_weather(race["id"], race["event_name"], race["event_date"])
            except Exception as exc:
                print(f"  Warning: weather fetch failed for {race['event_name']} — {exc}")
            time.sleep(1.0)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    init_db()
    for uuid in sys.argv[1:]:
        fetch_group(uuid.strip())
