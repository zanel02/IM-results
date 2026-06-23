"""
Weather fetching and storage for race days.

Geocodes race locations via OpenStreetMap Nominatim, then fetches hourly
conditions (6am–6pm local time) from Open-Meteo. Data is persisted in the
race_weather table and never re-fetched.

Public API:
    is_weather_fetched(race_id)                        – True if already stored
    fetch_and_save_weather(race_id, event_name, date)  – geocode + fetch + persist
    load_weather(race_id)                              – read stored row as dict
"""

import json
import re
import urllib.request
import urllib.parse
from datetime import datetime, timezone

from storage import get_connection

_NOMINATIM = "https://nominatim.openstreetmap.org/search"
_ARCHIVE   = "https://archive-api.open-meteo.com/v1/archive"
_FORECAST  = "https://api.open-meteo.com/v1/forecast"
_UA        = "ironman-results-dashboard/1.0 (personal project)"

_WMO_CODES = {
    0: "Clear", 1: "Mainly Clear", 2: "Partly Cloudy", 3: "Overcast",
    45: "Fog", 48: "Icy Fog",
    51: "Light Drizzle", 53: "Drizzle", 55: "Heavy Drizzle",
    61: "Light Rain", 63: "Rain", 65: "Heavy Rain",
    71: "Light Snow", 73: "Snow", 75: "Heavy Snow",
    80: "Showers", 81: "Heavy Showers", 82: "Violent Showers",
    95: "Thunderstorm", 96: "Thunderstorm w/ Hail", 99: "Thunderstorm w/ Hail",
}

_COMPASS = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]


def _wind_dir(degrees: float) -> str:
    return _COMPASS[round(degrees / 45) % 8]


def _geocode(event_name: str) -> tuple[float, float]:
    """Strip year + IRONMAN prefix, query Nominatim, return (lat, lon)."""
    name = re.sub(r"^\d{4}\s+", "", event_name)
    name = re.sub(r"^IRONMAN\s+(70\.3\s+)?", "", name, flags=re.IGNORECASE).strip()
    params = urllib.parse.urlencode({"q": name, "format": "json", "limit": "1"})
    req = urllib.request.Request(f"{_NOMINATIM}?{params}", headers={"User-Agent": _UA})
    with urllib.request.urlopen(req, timeout=10) as r:
        results = json.loads(r.read().decode())
    if not results:
        raise ValueError(f"Nominatim: no results for {name!r}")
    return float(results[0]["lat"]), float(results[0]["lon"])


def is_weather_fetched(race_id: int) -> bool:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT id FROM race_weather WHERE race_id = ?", (race_id,)
        ).fetchone()
    return row is not None


def fetch_and_save_weather(race_id: int, event_name: str, event_date: str) -> None:
    """Geocode + fetch hourly weather for a race day. Idempotent."""
    if is_weather_fetched(race_id):
        print(f"  Weather already stored for race {race_id} — skipping")
        return

    race_date_str = (event_date or "")[:10]
    if not race_date_str:
        print(f"  Warning: no event_date for race {race_id}, skipping weather")
        return

    race_date = datetime.strptime(race_date_str, "%Y-%m-%d").date()
    today = datetime.now(timezone.utc).date()
    days_ago = (today - race_date).days

    print(f"  Geocoding {event_name!r}...")
    lat, lon = _geocode(event_name)
    print(f"  → lat={lat:.4f}, lon={lon:.4f}")

    common = {
        "latitude": str(lat),
        "longitude": str(lon),
        "start_date": race_date_str,
        "end_date": race_date_str,
        "hourly": "temperature_2m,relative_humidity_2m,wind_speed_10m,wind_direction_10m,precipitation,weathercode",
        "timezone": "auto",
        "temperature_unit": "fahrenheit",
        "wind_speed_unit": "mph",
        "precipitation_unit": "inch",
    }
    base_url = _ARCHIVE if days_ago > 5 else _FORECAST
    url = f"{base_url}?{urllib.parse.urlencode(common)}"

    req = urllib.request.Request(url, headers={"User-Agent": _UA})
    with urllib.request.urlopen(req, timeout=15) as r:
        data = json.loads(r.read().decode())

    h = data.get("hourly", {})
    times     = h.get("time", [])
    temps     = h.get("temperature_2m", [])
    humids    = h.get("relative_humidity_2m", [])
    winds     = h.get("wind_speed_10m", [])
    wind_degs = h.get("wind_direction_10m", [])
    precips   = h.get("precipitation", [])
    codes     = h.get("weathercode", [])
    tz_name   = data.get("timezone", "")

    hourly_rows = []
    for i, t in enumerate(times):
        hour = int(t[11:13])  # "2024-06-23T06:00" → 6
        if 6 <= hour <= 18:
            hourly_rows.append({
                "hour":       t[11:16],
                "temp_f":     round(float(temps[i]), 1) if i < len(temps) and temps[i] is not None else None,
                "humidity":   int(humids[i]) if i < len(humids) and humids[i] is not None else None,
                "wind_mph":   round(float(winds[i]), 1) if i < len(winds) and winds[i] is not None else None,
                "wind_dir":   _wind_dir(float(wind_degs[i])) if i < len(wind_degs) and wind_degs[i] is not None else "—",
                "precip_in":  round(float(precips[i]), 3) if i < len(precips) and precips[i] is not None else 0.0,
                "conditions": _WMO_CODES.get(int(codes[i]), "Unknown") if i < len(codes) and codes[i] is not None else "—",
            })

    if not hourly_rows:
        print(f"  Warning: no hourly data returned for race {race_id}")
        return

    valid_temps  = [r["temp_f"] for r in hourly_rows if r["temp_f"] is not None]
    valid_winds  = [r["wind_mph"] for r in hourly_rows if r["wind_mph"] is not None]
    total_precip = sum(r["precip_in"] for r in hourly_rows if r["precip_in"] is not None)
    temp_7am     = next((r["temp_f"] for r in hourly_rows if r["hour"] == "07:00"), None)

    with get_connection() as conn:
        conn.execute(
            """INSERT OR IGNORE INTO race_weather
               (race_id, fetched_at, venue_lat, venue_lon, timezone, hourly_json,
                temp_f_7am, temp_f_high, total_precip_in, avg_wind_mph)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                race_id,
                datetime.now(timezone.utc).isoformat(),
                lat,
                lon,
                tz_name,
                json.dumps(hourly_rows),
                temp_7am,
                max(valid_temps) if valid_temps else None,
                round(total_precip, 3),
                round(sum(valid_winds) / len(valid_winds), 1) if valid_winds else None,
            ),
        )
        conn.commit()

    print(f"  Saved weather for race {race_id} ({len(hourly_rows)} hours, tz={tz_name})")


def load_weather(race_id: int) -> dict | None:
    """Return stored weather row with hourly list parsed, or None."""
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM race_weather WHERE race_id = ?", (race_id,)
        ).fetchone()
    if row is None:
        return None
    d = dict(row)
    d["hourly"] = json.loads(d["hourly_json"])
    return d
