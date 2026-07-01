import base64
import json
import sqlite3
from pathlib import Path

import altair as alt
import numpy as np
import pandas as pd
import plotly.express as px
import streamlit as st

from storage import build_from_seed

DB_PATH = Path(__file__).parent / "data" / "ironman.db"

# Build DB from seed files if it doesn't exist yet (first run after cloning)
if not DB_PATH.exists():
    build_from_seed()

st.set_page_config(page_title="Ironman Results", layout="wide", page_icon="🔴")

# ── visual theme ──────────────────────────────────────────────────────────────

st.markdown("""
<style>
#MainMenu, footer { visibility: hidden; }
header[data-testid="stHeader"] { background: transparent; }

/* Metric cards */
[data-testid="stMetric"] {
    border-radius: 10px;
    padding: 0.9rem 1.1rem;
    border: 1px solid rgba(128,128,128,0.16);
    background: rgba(128,128,128,0.035);
    transition: border-color 0.15s;
}
[data-testid="stMetric"]:hover { border-color: rgba(227,24,55,0.45); }
[data-testid="stMetricLabel"] p {
    font-size: 0.72rem !important;
    font-weight: 600 !important;
    text-transform: uppercase !important;
    letter-spacing: 0.07em !important;
}
[data-testid="stMetricValue"] { font-size: 1.45rem !important; font-weight: 700 !important; }

/* Active tab indicator */
[data-baseweb="tab-highlight"] { background-color: #e31837 !important; }
[data-baseweb="tab"][aria-selected="true"] { color: #e31837 !important; }
[data-baseweb="tab"] { font-weight: 500 !important; }

/* Expander, dataframe, divider */
[data-testid="stExpander"] details { border-radius: 10px !important; }
hr { border-color: rgba(128,128,128,0.13) !important; }

/* Selectbox / text input */
[data-baseweb="select"] > div:first-child { border-radius: 8px !important; }
[data-baseweb="input"] { border-radius: 8px !important; }
</style>
""", unsafe_allow_html=True)

_IM_RED = "#e31837"
_IM_PALETTE = [
    "#e31837", "#3b82f6", "#10b981", "#f59e0b",
    "#8b5cf6", "#ec4899", "#06b6d4", "#f97316", "#84cc16", "#14b8a6",
]
_DIST_DB = {"70.3": "IRONMAN 70.3", "Full": "IRONMAN"}
_SEGMENTS = {"Swim": "swim_secs", "Bike": "bike_secs", "Run": "run_secs", "Finish": "finish_secs"}

_LOGO_B64 = base64.b64encode(
    (Path(__file__).parent / "assets" / "ironman_logo.png").read_bytes()
).decode()

def _im_theme() -> dict:
    return {
        "config": {
            "background": "transparent",
            "view": {"stroke": "transparent"},
            "axis": {
                "gridColor": "rgba(128,128,128,0.22)",
                "gridOpacity": 1,
                "gridDash": [4, 4],
                "labelColor": "#6b7280",
                "titleColor": "#4b5563",
                "tickColor": "transparent",
                "domainColor": "rgba(128,128,128,0.28)",
                "labelFontSize": 11,
                "titleFontSize": 12,
                "titleFontWeight": 600,
            },
            "legend": {
                "labelColor": "#4b5563",
                "titleColor": "#374151",
                "labelFontSize": 11,
                "titleFontSize": 11,
                "titleFontWeight": 600,
                "symbolSize": 80,
                "padding": 8,
            },
            "bar": {"color": _IM_RED, "cornerRadius": 3},
            "line": {"strokeWidth": 2.5},
            "point": {"size": 55, "filled": True},
            "range": {"category": _IM_PALETTE},
        }
    }

alt.themes.register("ironman", _im_theme)
alt.themes.enable("ironman")

# ── data loading ──────────────────────────────────────────────────────────────

@st.cache_resource
def get_conn():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


@st.cache_data
def load_races() -> pd.DataFrame:
    return pd.read_sql(
        """SELECT r.id, r.event_name, r.year, r.distance_type, r.event_date,
                  eg.group_uuid, eg.name as series_name
           FROM races r
           LEFT JOIN event_groups eg ON r.group_id = eg.id
           WHERE r.results_fetched_at IS NOT NULL
           ORDER BY r.year DESC, r.event_name""",
        get_conn(),
    )


@st.cache_data
def load_results(race_id: int) -> pd.DataFrame:
    return pd.read_sql(
        "SELECT * FROM results WHERE race_id = ?", get_conn(), params=(race_id,)
    )


@st.cache_data
def search_athletes(query: str) -> pd.DataFrame:
    """Return distinct (athlete_name, country_iso2) pairs matching a partial, case-insensitive query."""
    return pd.read_sql(
        """SELECT DISTINCT athlete_name, country_iso2
           FROM results
           WHERE lower(athlete_name) LIKE lower(?)
           ORDER BY athlete_name, country_iso2
           LIMIT 50""",
        get_conn(),
        params=(f"%{query}%",),
    )


@st.cache_data
def load_athlete_results(athlete_name: str, country_iso2: str | None = None) -> pd.DataFrame:
    """All results for a specific athlete across every race in the DB."""
    return pd.read_sql(
        """SELECT res.status, res.age_group, res.gender,
                  res.swim_fmt, res.t1_fmt, res.bike_fmt, res.t2_fmt, res.run_fmt, res.finish_fmt,
                  res.finish_secs, res.swim_secs, res.bike_secs, res.run_secs,
                  res.rank_overall, res.rank_gender, res.rank_division,
                  r.event_name, r.year, r.distance_type, r.event_date,
                  (SELECT COUNT(*) FROM results r2
                   WHERE r2.race_id = res.race_id
                     AND r2.age_group = res.age_group
                     AND r2.status = 'FIN') AS division_size
           FROM results res
           JOIN races r ON res.race_id = r.id
           WHERE res.athlete_name = ?
             AND (? IS NULL OR res.country_iso2 = ?)
           ORDER BY r.year DESC, r.event_date DESC""",
        get_conn(),
        params=(athlete_name, country_iso2, country_iso2),
    )


@st.cache_data
def load_weather_data(race_id: int) -> dict | None:
    """Return stored race_weather row with hourly list, or None if not available."""
    try:
        conn = get_conn()
        row = conn.execute(
            "SELECT * FROM race_weather WHERE race_id = ?", (race_id,)
        ).fetchone()
    except Exception:
        return None
    if row is None:
        return None
    d = dict(row)
    d["hourly"] = json.loads(d["hourly_json"])
    return d



@st.cache_data
def load_trend_raw(age_groups: tuple, dist_filter: str) -> pd.DataFrame:
    """All finisher times for the given age groups across races matching distance type."""
    if not age_groups:
        return pd.DataFrame(columns=["group_uuid", "series_name", "year", "age_group", "finish_secs"])
    placeholders = ",".join("?" * len(age_groups))
    return pd.read_sql(
        f"""SELECT eg.group_uuid, eg.name AS series_name,
                  r.year, res.age_group, res.finish_secs
           FROM results res
           JOIN races r ON res.race_id = r.id
           JOIN event_groups eg ON r.group_id = eg.id
           WHERE res.age_group IN ({placeholders})
             AND res.status = 'FIN'
             AND r.results_fetched_at IS NOT NULL
             AND r.distance_type = ?
           ORDER BY eg.name, r.year, res.finish_secs""",
        get_conn(),
        params=list(age_groups) + [dist_filter],
    )


@st.cache_data
def load_segment_percentiles(dist_filter: str) -> pd.DataFrame:
    """Per-race swim/bike/run/finish times for all finishers matching distance type."""
    return pd.read_sql(
        """SELECT r.id AS race_id, r.year, r.event_name,
                  eg.name AS series_name,
                  res.athlete_name, res.country_iso2,
                  res.swim_secs, res.bike_secs, res.run_secs, res.finish_secs,
                  res.age_group, res.gender
           FROM results res
           JOIN races r ON res.race_id = r.id
           JOIN event_groups eg ON r.group_id = eg.id
           WHERE res.status = 'FIN'
             AND r.results_fetched_at IS NOT NULL
             AND r.distance_type = ?""",
        get_conn(),
        params=(dist_filter,),
    )


@st.cache_data
def load_series_all_finishers(group_uuid: str) -> pd.DataFrame:
    """All finisher splits for every year of a race series, for athlete YoY comparison."""
    return pd.read_sql(
        """SELECT r.year, res.athlete_name, res.country_iso2,
                  res.age_group, res.gender,
                  res.swim_secs, res.bike_secs, res.run_secs, res.finish_secs
             FROM results res
             JOIN races r ON res.race_id = r.id
             JOIN event_groups eg ON r.group_id = eg.id
            WHERE eg.group_uuid = ?
              AND res.status = 'FIN'
              AND r.results_fetched_at IS NOT NULL
            ORDER BY r.year, res.athlete_name""",
        get_conn(),
        params=(group_uuid,),
    )


@st.cache_data
def load_series_results(group_uuid: str) -> pd.DataFrame:
    return pd.read_sql(
        """SELECT res.*, r.year
           FROM results res
           JOIN races r ON res.race_id = r.id
           JOIN event_groups eg ON r.group_id = eg.id
           WHERE eg.group_uuid = ? AND res.status = 'FIN'
           ORDER BY r.year, res.rank_overall""",
        get_conn(),
        params=(group_uuid,),
    )


@st.cache_data
def load_map_races() -> pd.DataFrame:
    df = pd.read_sql(
        """SELECT eg.group_uuid, eg.name AS series_name,
                  AVG(rw.venue_lat) AS lat, AVG(rw.venue_lon) AS lon,
                  COALESCE(r.distance_type, 'Other') AS distance_type,
                  COUNT(DISTINCT r.year) AS num_years,
                  MAX(r.year) AS latest_year,
                  COUNT(res.id) AS total_finishers
           FROM race_weather rw
           JOIN races r ON rw.race_id = r.id
           JOIN event_groups eg ON r.group_id = eg.id
           LEFT JOIN results res ON res.race_id = r.id AND res.status = 'FIN'
           WHERE r.results_fetched_at IS NOT NULL
           GROUP BY eg.group_uuid
           ORDER BY eg.name""",
        get_conn(),
    )
    df["clean_name"] = df["series_name"].str.replace(r"^\d{4}\s+", "", regex=True)
    df["distance_type"] = df["distance_type"].replace({"IRONMAN": "Full", "IRONMAN 70.3": "70.3"})
    return df


# ── Keenan-page loaders ──────────────────────────────────────────────────────

_KEENAN_VALID = (
    "(r.distance_type = 'IRONMAN'"
    " AND res.swim_secs > 2000 AND res.t1_secs > 0 AND res.t2_secs > 0"
    " AND res.finish_secs BETWEEN 25200 AND 61200)"
    " OR (r.distance_type = 'IRONMAN 70.3'"
    " AND res.swim_secs > 800"
    " AND res.finish_secs BETWEEN 10800 AND 32400)"
)


@st.cache_data
def load_keenan_participation() -> pd.DataFrame:
    return pd.read_sql(
        f"""SELECT r.year, r.distance_type, COUNT(*) AS finishers
            FROM results res JOIN races r ON res.race_id = r.id
            WHERE r.year BETWEEN 2016 AND 2025
              AND r.distance_type IN ('IRONMAN', 'IRONMAN 70.3')
              AND ({_KEENAN_VALID})
            GROUP BY r.year, r.distance_type ORDER BY r.year""",
        get_conn(),
    )


@st.cache_data
def load_keenan_age_groups() -> pd.DataFrame:
    return pd.read_sql(
        f"""SELECT r.year, res.age_group, COUNT(*) AS finishers
            FROM results res JOIN races r ON res.race_id = r.id
            WHERE r.year BETWEEN 2016 AND 2025
              AND r.distance_type IN ('IRONMAN', 'IRONMAN 70.3')
              AND ({_KEENAN_VALID})
              AND res.age_group IS NOT NULL AND res.age_group != ''
            GROUP BY r.year, res.age_group ORDER BY r.year""",
        get_conn(),
    )


@st.cache_data
def load_keenan_race_counts() -> pd.DataFrame:
    return pd.read_sql(
        """SELECT year, distance_type, COUNT(DISTINCT id) AS races
           FROM races
           WHERE year BETWEEN 2016 AND 2025
             AND distance_type IN ('IRONMAN', 'IRONMAN 70.3')
             AND results_fetched_at IS NOT NULL
           GROUP BY year, distance_type ORDER BY year""",
        get_conn(),
    )


# ── helpers ───────────────────────────────────────────────────────────────────

def fmt_time(secs) -> str:
    if secs is None or (isinstance(secs, float) and np.isnan(secs)):
        return "—"
    secs = int(secs)
    h, rem = divmod(secs, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def fmt_time_sortable(secs) -> str:
    """Always H:MM:SS — string sort == numeric sort across the table."""
    if secs is None or (isinstance(secs, float) and np.isnan(secs)):
        return "—"
    secs = int(secs)
    h, rem = divmod(secs, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}"


# Unicode arrows showing direction wind is blowing toward (standard meteorological sense)
_DIR_ARROW: dict[str, str] = {
    "N": "↓", "NE": "↙", "E": "←", "SE": "↖",
    "S": "↑", "SW": "↗", "W": "→", "NW": "↘",
}


def time_histogram(series: pd.Series, bin_minutes: int) -> pd.DataFrame:
    mins = (series / 60).replace([np.inf, -np.inf], np.nan).dropna()
    if mins.empty:
        return pd.DataFrame()
    lo = np.floor(mins.min() / bin_minutes) * bin_minutes
    hi = np.ceil(mins.max() / bin_minutes) * bin_minutes + bin_minutes
    bins = np.arange(lo, hi, bin_minutes)
    counts, edges = np.histogram(mins, bins=bins)

    def edge_label(e: float) -> str:
        h, m = divmod(int(e), 60)
        return f"{h}:{m:02d}"

    labels = [f"{edge_label(edges[i])} – {edge_label(edges[i+1])}" for i in range(len(counts))]
    return pd.DataFrame({"Bucket": labels, "Athletes": counts.astype(int)})


def pct_dot_chart(series: pd.Series, height: int = 180) -> alt.Chart | None:
    """Horizontal dot chart showing P10/P25/P50/P75/P90 for a time series (seconds)."""
    clean = series.dropna()
    if clean.empty:
        return None
    pcts = [10, 25, 50, 75, 90]
    rows = []
    for p in pcts:
        secs = float(np.percentile(clean, p))
        rows.append({"Percentile": f"P{p}", "secs": secs, "label": fmt_time(int(secs))})
    df = pd.DataFrame(rows)
    tick = alt.Chart(df).mark_tick(thickness=2, size=20, color="#94a3b8").encode(
        x=alt.X("secs:Q", title="seconds", axis=alt.Axis(labelExpr=(
            "floor(datum.value/60) + ':' + (datum.value % 60 < 10 ? '0' : '') + (datum.value % 60)"
        ))),
        y=alt.Y("Percentile:N", sort=["P10", "P25", "P50", "P75", "P90"], title=None),
    )
    dots = alt.Chart(df).mark_circle(size=80).encode(
        x=alt.X("secs:Q"),
        y=alt.Y("Percentile:N", sort=["P10", "P25", "P50", "P75", "P90"]),
        color=alt.Color("Percentile:N", legend=None, scale=alt.Scale(
            domain=["P10", "P25", "P50", "P75", "P90"],
            range=["#6ee7b7", "#34d399", "#e31837", "#f87171", "#fca5a5"],
        )),
        tooltip=[
            alt.Tooltip("Percentile:N"),
            alt.Tooltip("label:N", title="Time"),
        ],
    )
    return (tick + dots).properties(height=height)


def hist_chart(df: pd.DataFrame, height: int) -> alt.Chart:
    step = max(14, min(30, 560 // max(len(df), 1)))
    return (
        alt.Chart(df)
        .mark_bar()
        .encode(
            x=alt.X("Bucket:N", sort=None, title=None, axis=alt.Axis(labelAngle=-45)),
            y=alt.Y("Athletes:Q", title="Athletes", stack=None),
            tooltip=[alt.Tooltip("Bucket:N", title="Time"), alt.Tooltip("Athletes:Q")],
        )
        .properties(height=height, width=alt.Step(step))
    )


_AG_RANGES_ORDER = ["18-24", "25-29", "30-34", "35-39", "40-44",
                    "45-49", "50-54", "55-59", "60-64", "65-69"]


def _render_ag_participation(counts: pd.DataFrame, ay1: int | None, ay2: int | None) -> None:
    """Render grouped bar + optional delta bar + table for one gender section.

    counts must have columns: ag_label (Categorical), year (int), Year (str), Finishers (int).
    ay1/ay2 are the endpoint years for the delta charts; pass None to skip deltas.
    """
    bar = (
        alt.Chart(counts)
        .mark_bar()
        .encode(
            x=alt.X("ag_label:N", sort=None, title="Age Group",
                    axis=alt.Axis(labelAngle=-40)),
            xOffset=alt.XOffset("Year:N"),
            y=alt.Y("Finishers:Q", title="Finishers", stack=None),
            color=alt.Color("Year:N", title="Year"),
            tooltip=["ag_label:N", "Year:N", "Finishers:Q"],
        )
        .properties(height=280)
    )
    st.altair_chart(bar, width="stretch")

    if ay1 is None or ay2 is None or ay1 == ay2:
        return

    cnt1 = counts[counts["year"] == ay1].set_index("ag_label")["Finishers"]
    cnt2 = counts[counts["year"] == ay2].set_index("ag_label")["Finishers"]
    all_ag = cnt2.index.union(cnt1.index)
    delta = pd.DataFrame({
        "Age Group": all_ag,
        str(ay1): cnt1.reindex(all_ag).fillna(0).astype(int),
        str(ay2): cnt2.reindex(all_ag).fillna(0).astype(int),
    })
    delta["Change"] = delta[str(ay2)] - delta[str(ay1)]
    delta["Change %"] = (
        delta["Change"] / delta[str(ay1)].replace(0, np.nan) * 100
    ).round(1)
    delta = delta.dropna(subset=["Change %"])
    delta["Age Group"] = pd.Categorical(
        delta["Age Group"], categories=_AG_RANGES_ORDER, ordered=True
    )
    delta = delta.sort_values("Age Group").reset_index(drop=True)

    st.markdown(f"**{ay1} → {ay2} change**")
    delta_chart = (
        alt.Chart(delta)
        .mark_bar()
        .encode(
            x=alt.X("Age Group:N", sort=None, axis=alt.Axis(labelAngle=-40)),
            y=alt.Y("Change %:Q", title="% Change", stack=None),
            color=alt.condition(
                alt.datum["Change %"] >= 0,
                alt.value("#10b981"),
                alt.value("#e31837"),
            ),
            tooltip=[
                "Age Group:N",
                alt.Tooltip(f"{ay1}:Q", title=str(ay1)),
                alt.Tooltip(f"{ay2}:Q", title=str(ay2)),
                alt.Tooltip("Change:Q", title="Δ Finishers"),
                alt.Tooltip("Change %:Q", title="% Change", format=".1f"),
            ],
        )
        .properties(height=240)
    )
    st.altair_chart(delta_chart, width="stretch")
    st.dataframe(delta, width="stretch", hide_index=True)


# ── load data ─────────────────────────────────────────────────────────────────

races = load_races()
if races.empty:
    st.warning("No race results in the database yet.")
    st.stop()

series_df = (
    races.dropna(subset=["group_uuid"])
    .drop_duplicates("group_uuid")[["group_uuid", "series_name"]]
    .reset_index(drop=True)
)
series_df["clean_name"] = series_df["series_name"].str.replace(r"^\d{4}\s+", "", regex=True)

# ── tabs ──────────────────────────────────────────────────────────────────────

st.markdown(f"""
<div style="display:flex;align-items:center;gap:1.6rem;padding:1.1rem 1.6rem;
            background:linear-gradient(135deg,#0d0d0d 0%,#1e1e1e 100%);
            border-radius:14px;margin-bottom:0.5rem;border-left:5px solid #e31837;
            box-shadow:0 4px 20px rgba(0,0,0,0.15);">
  <img src="data:image/png;base64,{_LOGO_B64}"
       style="height:44px;width:auto;object-fit:contain;" />
  <div style="color:#9ca3af;font-size:0.72rem;font-weight:700;letter-spacing:0.22em;
              text-transform:uppercase;border-left:2px solid #e31837;padding-left:1rem;">
    RESULTS DASHBOARD
  </div>
</div>
""", unsafe_allow_html=True)
tab1, tab2, tab3, tab4, tab5, tab6, tab7, tab8 = st.tabs([
    "Race Results", "Year-over-Year Comparison", "Athlete Search",
    "Analytics", "Race Comparison", "Athlete YoY", "Race Map", "For Keenan",
])


# ══════════════════════════════════════════════════════════════════════════════
# TAB 1 — single race results
# ══════════════════════════════════════════════════════════════════════════════

with tab1:
    st.subheader("Select a Race")
    ctrl1, ctrl2, ctrl3 = st.columns(3)

    with ctrl1:
        race_series_idx = st.selectbox(
            "Race",
            range(len(series_df)),
            format_func=lambda i: series_df["clean_name"].iloc[i],
            key="t1_race",
        )
    sel_race_series = series_df.iloc[race_series_idx]
    race_year_options = (
        races[races["group_uuid"] == sel_race_series["group_uuid"]]
        .sort_values("year", ascending=False)
    )

    with ctrl2:
        selected_year = st.selectbox("Year", race_year_options["year"].tolist(), key="t1_year")

    race = race_year_options[race_year_options["year"] == selected_year].iloc[0]
    df_all = load_results(int(race["id"]))
    finishers_single = df_all[df_all["status"] == "FIN"]
    age_groups = ["All"] + sorted(finishers_single["age_group"].dropna().unique().tolist())

    with ctrl3:
        selected_ag = st.selectbox("Age Group", age_groups, key="t1_ag")

    st.divider()

    # ── race header ───────────────────────────────────────────────────────────

    st.header(race["event_name"])
    event_date = race["event_date"][:10] if race["event_date"] else "—"
    st.caption(f"{race['distance_type']}  ·  {event_date}")

    n_fin = len(finishers_single)
    n_dnf = int((df_all["status"] == "DNF").sum())
    n_dns = int((df_all["status"] == "DNS").sum())
    n_dq  = int((df_all["status"] == "DQ").sum())

    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Finishers", f"{n_fin:,}")
    c2.metric("DNF", n_dnf)
    c3.metric("DNS", n_dns)
    c4.metric("DQ", n_dq)
    c5.metric("Median Finish", fmt_time(finishers_single["finish_secs"].median()))

    # ── weather ───────────────────────────────────────────────────────────────

    weather = load_weather_data(int(race["id"]))
    _has_precip = weather is not None and (weather.get("total_precip_in") or 0) > 0
    with st.expander("Race Day Weather", expanded=False):
        if weather is None:
            st.info("Weather not available — run `python3 fetch.py <group-uuid>` to load it.")
        else:
            tz_label = weather.get("timezone", "")
            wcols = st.columns(4)
            wcols[0].metric("Temp at 7am", f"{weather['temp_f_7am']:.0f}°F" if weather["temp_f_7am"] else "—")
            wcols[1].metric("High (6am–6pm)", f"{weather['temp_f_high']:.0f}°F" if weather["temp_f_high"] else "—")
            wcols[2].metric("Total Precip", f"{weather['total_precip_in']:.2f} in" if weather["total_precip_in"] is not None else "—")
            wcols[3].metric("Avg Wind", f"{weather['avg_wind_mph']:.0f} mph" if weather["avg_wind_mph"] else "—")

            if tz_label:
                st.caption(f"All times in {tz_label}")

            hourly = weather["hourly"]
            hourly_df = pd.DataFrame(hourly)
            hourly_df["wind_arrow"] = hourly_df["wind_dir"].map(_DIR_ARROW).fillna("·")
            hourly_df["precip_in"] = hourly_df["precip_in"].fillna(0.0)
            hourly_df["hour_num"] = hourly_df["hour"].str[:2].astype(int)

            x_enc = alt.X(
                "hour_num:Q", title="Hour",
                axis=alt.Axis(format="d", tickMinStep=1, labelExpr="datum.value + ':00'"),
            )

            temp_df = hourly_df.dropna(subset=["temp_f"])
            temp_chart = alt.Chart(temp_df).mark_line(
                color="#f97316", strokeWidth=2.5,
                point=alt.OverlayMarkDef(color="#f97316", size=40),
            ).encode(
                x=x_enc,
                y=alt.Y(
                    "temp_f:Q", title="Temperature (°F)",
                    scale=alt.Scale(zero=False),
                    axis=alt.Axis(titleColor="#f97316"),
                ),
                tooltip=[
                    alt.Tooltip("hour:N", title="Hour"),
                    alt.Tooltip("temp_f:Q", title="Temp (°F)", format=".1f"),
                    alt.Tooltip("humidity:Q", title="Humidity (%)"),
                    alt.Tooltip("conditions:N", title="Conditions"),
                ],
            ).properties(height=180)
            st.altair_chart(temp_chart, width="stretch")

            if _has_precip:
                precip_chart = alt.Chart(hourly_df).mark_bar(
                    color="#60a5fa", opacity=0.85,
                ).encode(
                    x=x_enc,
                    y=alt.Y(
                        "precip_in:Q", title="Precip (in)",
                        axis=alt.Axis(titleColor="#60a5fa"),
                        stack=None,
                    ),
                    tooltip=[
                        alt.Tooltip("hour:N", title="Hour"),
                        alt.Tooltip("precip_in:Q", title="Precip (in)", format=".3f"),
                    ],
                ).properties(height=120)
                st.altair_chart(precip_chart, width="stretch")

            wind_df = hourly_df.dropna(subset=["wind_mph"])
            wind_base = alt.Chart(wind_df).encode(
                x=alt.X(
                    "hour_num:Q", title="Hour",
                    axis=alt.Axis(format="d", tickMinStep=1, labelExpr="datum.value + ':00'"),
                )
            )
            wind_bars = wind_base.mark_bar(color="#94a3b8", opacity=0.85).encode(
                y=alt.Y("wind_mph:Q", title="Wind (mph)", stack=None),
                tooltip=[
                    alt.Tooltip("hour:N", title="Hour"),
                    alt.Tooltip("wind_mph:Q", title="Speed (mph)", format=".1f"),
                    alt.Tooltip("wind_dir:N", title="From direction"),
                ],
            )
            wind_arrows = wind_base.mark_text(dy=-12, fontSize=15, color="#cbd5e1").encode(
                y=alt.Y("wind_mph:Q"),
                text=alt.Text("wind_arrow:N"),
            )
            st.altair_chart(
                alt.layer(wind_bars, wind_arrows).properties(height=140),
                width="stretch",
            )

    st.divider()

    if selected_ag == "All":
        df = finishers_single.sort_values("rank_overall")
        rank_col, rank_label = "rank_overall", "Overall"
    else:
        df = finishers_single[finishers_single["age_group"] == selected_ag].sort_values("rank_division")
        rank_col, rank_label = "rank_division", selected_ag

    if df.empty:
        st.info("No finisher results for this selection.")
    else:
        display = pd.DataFrame({
            rank_label:  df[rank_col].astype("Int64"),
            "Bib":       df["bib"],
            "Athlete":   df["athlete_name"],
            "Age Group": df["age_group"],
            "City":      df["city"].fillna("") + df["state"].fillna("").apply(lambda s: f", {s}" if s else ""),
            "Country":   df["country_iso2"],
            "Swim":      df["swim_fmt"],
            "T1":        df["t1_fmt"],
            "Bike":      df["bike_fmt"],
            "T2":        df["t2_fmt"],
            "Run":       df["run_fmt"],
            "Finish":    df["finish_fmt"],
            "AWA Pts":   df["awa_points"].where(pd.notna(df["awa_points"])).astype("Int64"),
        })
        if selected_ag != "All":
            display.insert(1, "Overall", df["rank_overall"].astype("Int64"))

        st.subheader(f"Results — {selected_ag}")
        st.dataframe(display, width="stretch", hide_index=True, height=600)

        st.divider()
        st.subheader("Finish Time Distribution")
        hist = time_histogram(df["finish_secs"], bin_minutes=15)
        if not hist.empty:
            st.altair_chart(hist_chart(hist, 260), width="content")

        st.subheader("Segment Distributions")
        col_swim, col_bike, col_run = st.columns(3)
        with col_swim:
            st.markdown("**Swim**")
            h = time_histogram(df["swim_secs"], bin_minutes=5)
            if not h.empty:
                st.altair_chart(hist_chart(h, 240), width="content")
        with col_bike:
            st.markdown("**Bike**")
            h = time_histogram(df["bike_secs"], bin_minutes=10)
            if not h.empty:
                st.altair_chart(hist_chart(h, 240), width="content")
        with col_run:
            st.markdown("**Run**")
            h = time_histogram(df["run_secs"], bin_minutes=10)
            if not h.empty:
                st.altair_chart(hist_chart(h, 240), width="content")

        col_t1, col_t2, _ = st.columns(3)
        with col_t1:
            st.markdown("**T1**")
            h = time_histogram(df["t1_secs"], bin_minutes=1)
            if not h.empty:
                st.altair_chart(hist_chart(h, 200), width="content")
            pc = pct_dot_chart(df["t1_secs"])
            if pc:
                st.altair_chart(pc, use_container_width=True)
        with col_t2:
            st.markdown("**T2**")
            h = time_histogram(df["t2_secs"], bin_minutes=1)
            if not h.empty:
                st.altair_chart(hist_chart(h, 200), width="content")
            pc = pct_dot_chart(df["t2_secs"])
            if pc:
                st.altair_chart(pc, use_container_width=True)


# ══════════════════════════════════════════════════════════════════════════════
# TAB 2 — year-over-year comparison
# ══════════════════════════════════════════════════════════════════════════════

PERCENTILE_OPTIONS = {
    "10th": 10,
    "25th": 25,
    "50th (Median)": 50,
    "75th": 75,
    "90th": 90,
}

with tab2:
    st.subheader("Select a Series")
    ctrl_a, ctrl_b, ctrl_c, ctrl_d = st.columns(4)

    with ctrl_a:
        compare_series_idx = st.selectbox(
            "Race Series",
            range(len(series_df)),
            format_func=lambda i: series_df["clean_name"].iloc[i],
            key="t2_series",
        )
    sel_series = series_df.iloc[compare_series_idx]
    series_races = races[races["group_uuid"] == sel_series["group_uuid"]].sort_values("year")
    all_years = series_races["year"].tolist()

    with ctrl_b:
        selected_years = st.multiselect("Years", all_years, default=all_years, key="t2_years")

    with ctrl_c:
        series_ag_options = ["All"] + sorted(
            pd.read_sql(
                """SELECT DISTINCT res.age_group FROM results res
                   JOIN races r ON res.race_id = r.id
                   JOIN event_groups eg ON r.group_id = eg.id
                   WHERE eg.group_uuid = ? AND res.status = 'FIN' AND res.age_group IS NOT NULL""",
                get_conn(),
                params=(str(sel_series["group_uuid"]),),
            )["age_group"].tolist()
        )
        ag_compare = st.selectbox("Age Group", series_ag_options, key="t2_ag")

    with ctrl_d:
        pct_label = st.selectbox("Percentile", list(PERCENTILE_OPTIONS.keys()), index=2, key="t2_pct")
    pct = PERCENTILE_OPTIONS[pct_label] / 100

    st.divider()

    if not selected_years:
        st.info("Select at least one year above.")
    else:
        df_series = load_series_results(sel_series["group_uuid"])
        if ag_compare != "All":
            df_series = df_series[df_series["age_group"] == ag_compare]
        df_series = df_series[df_series["year"].isin(selected_years)]

        if df_series.empty:
            st.info("No data for the selected filters.")
        else:
            # ── summary stats table ───────────────────────────────────────────────────

            st.header(sel_series["clean_name"])
            st.subheader("Summary by Year")

            ag_filter_sql = "AND res.age_group = ?" if ag_compare != "All" else ""
            ag_filter_params = [ag_compare] if ag_compare != "All" else []
            all_results_by_year = pd.read_sql(
                """SELECT r.year, res.status
                   FROM results res
                   JOIN races r ON res.race_id = r.id
                   JOIN event_groups eg ON r.group_id = eg.id
                   WHERE eg.group_uuid = ? AND r.year IN ({}) {}""".format(
                    ",".join("?" * len(selected_years)), ag_filter_sql
                ),
                get_conn(),
                params=[sel_series["group_uuid"]] + selected_years + ag_filter_params,
            )

            rows = []
            for year in sorted(selected_years):
                yr_all = all_results_by_year[all_results_by_year["year"] == year]
                yr_fin = df_series[df_series["year"] == year]
                n_fin = len(yr_fin)
                n_dnf = int((yr_all["status"] == "DNF").sum())
                dnf_pct = f"{100 * n_dnf / len(yr_all):.1f}%" if len(yr_all) else "—"

                race_row = series_races[series_races["year"] == year]
                w = load_weather_data(int(race_row["id"].iloc[0])) if not race_row.empty else None

                rows.append({
                    "Year":                        year,
                    "Finishers":                   n_fin,
                    "DNF":                         n_dnf,
                    "DNF %":                       dnf_pct,
                    f"{pct_label} Finish":         fmt_time(yr_fin["finish_secs"].quantile(pct)),
                    f"{pct_label} Swim":           fmt_time(yr_fin["swim_secs"].quantile(pct)),
                    f"{pct_label} Bike":           fmt_time(yr_fin["bike_secs"].quantile(pct)),
                    f"{pct_label} Run":            fmt_time(yr_fin["run_secs"].quantile(pct)),
                    "Temp 7am (°F)":               f"{w['temp_f_7am']:.0f}°" if w and w.get("temp_f_7am") else "—",
                    "Precip (in)":                 f"{w['total_precip_in']:.2f}" if w and w.get("total_precip_in") is not None else "—",
                })

            st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True)

            # ── finish time overlay ───────────────────────────────────────────────────

            st.divider()
            st.subheader("Finish Time Distribution by Year")

            finish_mins = df_series[["year", "finish_secs"]].copy()
            finish_mins["mins"] = finish_mins["finish_secs"] / 60
            finish_mins = finish_mins[np.isfinite(finish_mins["mins"].fillna(np.nan))]

            if not finish_mins.empty:
                bin_size = 15
                lo = np.floor(finish_mins["mins"].min() / bin_size) * bin_size
                hi = np.ceil(finish_mins["mins"].max() / bin_size) * bin_size + bin_size
                shared_bins = np.arange(lo, hi, bin_size)

                def edge_label(e: float) -> str:
                    h, m = divmod(int(e), 60)
                    return f"{h}:{m:02d}"

                bin_labels = [f"{edge_label(shared_bins[i])} – {edge_label(shared_bins[i+1])}" for i in range(len(shared_bins) - 1)]

                overlay_rows = []
                for year in sorted(selected_years):
                    yr_mins = finish_mins[finish_mins["year"] == year]["mins"]
                    counts, _ = np.histogram(yr_mins, bins=shared_bins)
                    for label, cnt in zip(bin_labels, counts):
                        overlay_rows.append({"Bucket": label, "Year": str(year), "Athletes": int(cnt)})
                overlay_long = pd.DataFrame(overlay_rows)

                if not overlay_long.empty:
                    n_buckets = overlay_long["Bucket"].nunique()
                    ov_step = max(14, min(28, 560 // max(n_buckets, 1)))
                    overlay_chart = (
                        alt.Chart(overlay_long)
                        .mark_line(point=True)
                        .encode(
                            x=alt.X("Bucket:N", sort=None, title="Finish Time", axis=alt.Axis(labelAngle=-45)),
                            y=alt.Y("Athletes:Q", title="Athletes"),
                            color=alt.Color("Year:N", title="Year"),
                            tooltip=[alt.Tooltip("Bucket:N", title="Time"), "Year:N", "Athletes:Q"],
                        )
                        .properties(height=320, width=alt.Step(ov_step))
                    )
                    st.altair_chart(overlay_chart, width="content")

            # ── median splits ─────────────────────────────────────────────────────────

            st.divider()
            st.subheader(f"{pct_label} Percentile Segment Times by Year")

            segments = [("Swim", "swim_secs"), ("Bike", "bike_secs"), ("Run", "run_secs")]
            col_charts = st.columns(len(segments))

            for col, (label, col_secs) in zip(col_charts, segments):
                with col:
                    st.markdown(f"**{label}**")
                    pct_vals = (
                        df_series.groupby("year")[col_secs]
                        .quantile(pct)
                        .loc[lambda s: s.index.isin(selected_years)]
                        .sort_index()
                    )
                    pct_df = pd.DataFrame({"Year": pct_vals.index.astype(int), "Minutes": (pct_vals / 60).values})
                    pct_df = pct_df.replace([np.inf, -np.inf], np.nan).dropna()
                    if not pct_df.empty:
                        y_min = pct_df["Minutes"].min()
                        y_max = pct_df["Minutes"].max()
                        y_pad = max((y_max - y_min) * 0.18, 1.0)
                        seg_chart = (
                            alt.Chart(pct_df)
                            .mark_bar()
                            .encode(
                                x=alt.X("Year:Q", title="Year", axis=alt.Axis(format="d", tickMinStep=1)),
                                y=alt.Y("Minutes:Q", title="Minutes",
                                        scale=alt.Scale(domain=[y_min - y_pad, y_max + y_pad]),
                                        stack=None),
                                tooltip=[alt.Tooltip("Year:Q", format="d"), alt.Tooltip("Minutes:Q", format=".1f")],
                            )
                            .properties(height=260)
                        )
                        st.altair_chart(seg_chart, width="stretch")

            # ── participation by age group ─────────────────────────────────────────

            _AG_ORDER = [
                "M18-24", "M25-29", "M30-34", "M35-39", "M40-44",
                "M45-49", "M50-54", "M55-59", "M60-64", "M65-69",
                "F18-24", "F25-29", "F30-34", "F35-39", "F40-44",
                "F45-49", "F50-54", "F55-59", "F60-64", "F65-69",
            ]

            st.divider()
            st.subheader("Participation by Age Group")

            ag_gender_filter = st.radio(
                "Gender", ["All", "M", "F"], horizontal=True, key="t2_ag_part_gender"
            )

            # use cached full load (no ag_compare filter) restricted to selected years
            df_ag_full = load_series_results(sel_series["group_uuid"])
            df_ag_full = df_ag_full[
                df_ag_full["year"].isin(selected_years) &
                df_ag_full["age_group"].isin(_AG_ORDER)
            ]
            if ag_gender_filter != "All":
                df_ag_full = df_ag_full[df_ag_full["age_group"].str.startswith(ag_gender_filter)]

            ag_counts = (
                df_ag_full.groupby(["year", "age_group"])
                .size()
                .reset_index(name="Finishers")
            )
            ag_counts["age_group"] = pd.Categorical(
                ag_counts["age_group"], categories=_AG_ORDER, ordered=True
            )
            ag_counts["Year"] = ag_counts["year"].astype(str)
            ag_counts = ag_counts.sort_values(["age_group", "year"])

            if not ag_counts.empty:
                ag_bar = (
                    alt.Chart(ag_counts)
                    .mark_bar()
                    .encode(
                        x=alt.X("age_group:N", sort=None, title="Age Group",
                                axis=alt.Axis(labelAngle=-40)),
                        xOffset=alt.XOffset("Year:N"),
                        y=alt.Y("Finishers:Q", title="Finishers", stack=None),
                        color=alt.Color("Year:N", title="Year"),
                        tooltip=["age_group:N", "Year:N", "Finishers:Q"],
                    )
                    .properties(height=300)
                )
                st.altair_chart(ag_bar, width="stretch")

                sorted_sel = sorted(selected_years)
                if len(sorted_sel) >= 2:
                    y1, y2 = sorted_sel[0], sorted_sel[-1]
                    st.markdown(f"**{y1} → {y2} change**")

                    cnt1 = ag_counts[ag_counts["year"] == y1].set_index("age_group")["Finishers"]
                    cnt2 = ag_counts[ag_counts["year"] == y2].set_index("age_group")["Finishers"]
                    all_ags = cnt2.index.union(cnt1.index)
                    delta_df = pd.DataFrame({
                        "Age Group":  all_ags,
                        str(y1):      cnt1.reindex(all_ags).fillna(0).astype(int),
                        str(y2):      cnt2.reindex(all_ags).fillna(0).astype(int),
                    })
                    delta_df["Change"] = delta_df[str(y2)] - delta_df[str(y1)]
                    delta_df["Change %"] = (
                        delta_df["Change"] / delta_df[str(y1)].replace(0, np.nan) * 100
                    ).round(1)
                    delta_df = delta_df.dropna(subset=["Change %"]).sort_values(
                        "Change %", ascending=False
                    ).reset_index(drop=True)

                    delta_chart = (
                        alt.Chart(delta_df)
                        .mark_bar()
                        .encode(
                            x=alt.X("Age Group:N",
                                    sort=alt.SortField("Change %", order="descending"),
                                    axis=alt.Axis(labelAngle=-40)),
                            y=alt.Y("Change %:Q", title="% Change", stack=None),
                            color=alt.condition(
                                alt.datum["Change %"] >= 0,
                                alt.value("#10b981"),
                                alt.value("#e31837"),
                            ),
                            tooltip=["Age Group:N",
                                     alt.Tooltip(f"{y1}:Q", title=str(y1)),
                                     alt.Tooltip(f"{y2}:Q", title=str(y2)),
                                     alt.Tooltip("Change:Q", title="Δ Finishers"),
                                     alt.Tooltip("Change %:Q", title="% Change", format=".1f")],
                        )
                        .properties(height=260)
                    )
                    st.altair_chart(delta_chart, width="stretch")

                    st.dataframe(delta_df, width="stretch", hide_index=True)


# ══════════════════════════════════════════════════════════════════════════════
# TAB 3 — athlete search
# ══════════════════════════════════════════════════════════════════════════════

with tab3:
    st.subheader("Athlete Search")
    query = st.text_input("Search by name", placeholder="e.g. Smith, Jane")

    if not query:
        st.caption("Enter a name to search across all races in the database.")
    else:
        matches = search_athletes(query)

        if matches.empty:
            st.warning(f'No athletes found matching "{query}".')
        else:
            # Build (display_label, name, country) options; label includes country when present
            options = []
            for _, row in matches.iterrows():
                name = row["athlete_name"]
                country = row["country_iso2"] if pd.notna(row["country_iso2"]) else None
                label = f"{name} ({country})" if country else name
                options.append((label, name, country))

            if len(options) == 1:
                sel_label, sel_name, sel_country = options[0]
            else:
                sel_label = st.selectbox(
                    f"{len(options)} athletes found — select one",
                    [o[0] for o in options],
                )
                sel_label, sel_name, sel_country = next(o for o in options if o[0] == sel_label)

            st.divider()

            df_athlete = load_athlete_results(sel_name, sel_country)

            if df_athlete.empty:
                st.info("No results found for this athlete.")
            else:
                st.header(sel_label)

                finishes = df_athlete[df_athlete["status"] == "FIN"]
                c1, c2, c3 = st.columns(3)
                c1.metric("Races in DB", len(df_athlete))
                c2.metric("Finishes", len(finishes))
                c3.metric("Best Finish", fmt_time(finishes["finish_secs"].min()) if not finishes.empty else "—")

                st.divider()

                display = pd.DataFrame({
                    "Year":       df_athlete["year"],
                    "Race":       df_athlete["event_name"],
                    "Distance":   df_athlete["distance_type"],
                    "Age Group":  df_athlete["age_group"],
                    "Status":     df_athlete["status"],
                    "Swim":       df_athlete["swim_fmt"],
                    "T1":         df_athlete["t1_fmt"],
                    "Bike":       df_athlete["bike_fmt"],
                    "T2":         df_athlete["t2_fmt"],
                    "Run":        df_athlete["run_fmt"],
                    "Finish":     df_athlete["finish_fmt"],
                    "Overall":    df_athlete["rank_overall"].astype("Int64"),
                    "Gender":     df_athlete["rank_gender"].astype("Int64"),
                    "Division":   df_athlete.apply(
                        lambda row: f"{int(row['rank_division'])} / {int(row['division_size'])}"
                        if pd.notna(row["rank_division"]) and pd.notna(row["division_size"])
                        else "—",
                        axis=1,
                    ),
                })
                st.dataframe(display, width="stretch", hide_index=True)

                if len(finishes) > 1:
                    st.divider()
                    st.subheader("Finish Time Over Time")
                    chart_df = (
                        finishes[["year", "finish_secs", "event_name"]]
                        .sort_values("year")
                        .assign(finish_mins=lambda d: d["finish_secs"] / 60)
                    )
                    athlete_chart = (
                        alt.Chart(chart_df)
                        .mark_line(point=True)
                        .encode(
                            x=alt.X("year:Q", title="Year", axis=alt.Axis(format="d", tickMinStep=1)),
                            y=alt.Y("finish_mins:Q", title="Finish (min)", scale=alt.Scale(zero=False)),
                            tooltip=[
                                alt.Tooltip("year:Q", title="Year", format="d"),
                                alt.Tooltip("event_name:N", title="Race"),
                                alt.Tooltip("finish_mins:Q", title="Finish (min)", format=".1f"),
                            ],
                        )
                        .properties(height=260)
                    )
                    st.altair_chart(athlete_chart, width="stretch")

# ══════════════════════════════════════════════════════════════════════════════
# TAB 4 — Analytics
# ══════════════════════════════════════════════════════════════════════════════


with tab4:
    st.subheader("Age Group Trends")

    _AGE_RANGES = ["18-24", "25-29", "30-34", "35-39", "40-44",
                   "45-49", "50-54", "55-59", "60-64", "65-69"]
    _GENDER_MAP = {"All": ["M", "F"], "M": ["M"], "F": ["F"]}

    c5a, c5b, c5c, c5d = st.columns([1, 3, 2, 3])
    with c5a:
        ana_gender = st.radio("Gender", ["All", "M", "F"], horizontal=True, key="ana_gender")
    with c5b:
        ana_ages = st.multiselect(
            "Age Ranges", _AGE_RANGES, placeholder="All ranges", key="ana_ages"
        )
    with c5c:
        ana_dist = st.selectbox("Distance", ["70.3", "Full"], key="ana_dist")
    with c5d:
        ana_both = st.checkbox(
            "Only races with editions in both endpoint years",
            value=False,
            key="ana_both",
        )

    _ranges = ana_ages if ana_ages else _AGE_RANGES
    _age_group_list = tuple(
        f"{g}{r}" for g in _GENDER_MAP[ana_gender] for r in _ranges
    )
    raw_ana = load_trend_raw(_age_group_list, _DIST_DB[ana_dist]).copy()
    raw_ana["clean_name"] = raw_ana["series_name"].str.replace(r"^\d{4}\s+", "", regex=True)

    # Show slider first so yr1/yr2 drive everything below
    avail_years = [int(y) for y in sorted(raw_ana["year"].dropna().unique())] if not raw_ana.empty else []
    if len(avail_years) > 1:
        yr_range = st.slider(
            "Year range",
            min_value=avail_years[0],
            max_value=avail_years[-1],
            value=(avail_years[0], avail_years[-1]),
            key="ana_yr_range",
        )
        yr1, yr2 = yr_range
    else:
        yr1 = yr2 = avail_years[0] if avail_years else 2026

    # Races that have editions in BOTH endpoint years
    if not raw_ana.empty:
        both_uuids = (
            set(raw_ana[raw_ana["year"] == yr1]["group_uuid"]) &
            set(raw_ana[raw_ana["year"] == yr2]["group_uuid"])
        )
    else:
        both_uuids = set()

    if ana_both:
        raw_ana = raw_ana[raw_ana["group_uuid"].isin(both_uuids)]

    raw_ana = raw_ana[(raw_ana["year"] >= yr1) & (raw_ana["year"] <= yr2)]

    if raw_ana.empty:
        st.info("No data matches the current filters.")
    else:
        # ── endpoint-year summary metrics ──────────────────────────────────────
        s_y1 = raw_ana[raw_ana["year"] == yr1]["finish_secs"]
        s_y2 = raw_ana[raw_ana["year"] == yr2]["finish_secs"]
        if yr1 != yr2 and len(s_y1) > 0 and len(s_y2) > 0:
            r_y1 = raw_ana[raw_ana["year"] == yr1]["clean_name"].nunique()
            r_y2 = raw_ana[raw_ana["year"] == yr2]["clean_name"].nunique()
            sc1, sc2, sc3, sc4, sc5 = st.columns(5)
            sc1.metric("Races", int(r_y2), delta=int(r_y2 - r_y1))
            _p_delta = len(s_y2) - len(s_y1)
            _p_pct = _p_delta / len(s_y1) * 100 if len(s_y1) else 0
            sc2.metric(
                "Participants", f"{len(s_y2):,}",
                delta=f"{_p_delta:+,} ({_p_pct:+.1f}%)",
            )
            sc3.metric(
                "Avg Finish", fmt_time(int(s_y2.mean())),
                delta=f"{(s_y2.mean() - s_y1.mean()) / 60:+.1f} min",
                delta_color="inverse",
            )
            sc4.metric(
                "Median Finish", fmt_time(int(s_y2.median())),
                delta=f"{(s_y2.median() - s_y1.median()) / 60:+.1f} min",
                delta_color="inverse",
            )
            sc5.metric(
                "Winner", fmt_time(int(s_y2.min())),
                delta=f"{(s_y2.min() - s_y1.min()) / 60:+.1f} min",
                delta_color="inverse",
            )
            both_label = f" · {len(both_uuids)} races with editions in both years" if ana_both else ""
            st.caption(f"{yr2} values · delta vs {yr1}{both_label}")
            st.divider()

        # ── compute per-race-per-year metrics ──────────────────────────────────
        top5_avg = (
            raw_ana.sort_values("finish_secs")
            .groupby(["clean_name", "year"])
            .head(5)
            .groupby(["clean_name", "year"])["finish_secs"]
            .mean()
            .rename("top5_avg_secs")
            .reset_index()
        )

        g_ana = raw_ana.groupby(["clean_name", "year"])["finish_secs"]
        metrics_df = pd.DataFrame({
            "participants": g_ana.count(),
            "winner_secs": g_ana.min(),
            "median_secs": g_ana.median(),
        }).reset_index()
        metrics_df = metrics_df.merge(top5_avg, on=["clean_name", "year"], how="left")

        for _c in ["winner", "top5_avg", "median"]:
            metrics_df[f"{_c}_mins"] = metrics_df[f"{_c}_secs"] / 60
            metrics_df[f"{_c}_fmt"] = metrics_df[f"{_c}_secs"].apply(fmt_time)

        # ── race selector ──────────────────────────────────────────────────────
        all_ana_races = sorted(metrics_df["clean_name"].unique())
        y25 = metrics_df[metrics_df["year"] == 2025]
        default_ana = (
            y25.nlargest(10, "participants")["clean_name"].tolist()
            if not y25.empty else all_ana_races[:10]
        )
        selected_ana = st.multiselect(
            "Races to display", all_ana_races, default=default_ana, key="ana_races"
        )

        if not selected_ana:
            st.info("Select at least one race above.")
        else:
            plot_df = metrics_df[metrics_df["clean_name"].isin(selected_ana)]

            # Collapse selected races into one aggregate line per year:
            # participants = sum; times = mean of per-race values
            agg_df = plot_df.groupby("year", as_index=False).agg(
                participants=("participants", "sum"),
                winner_secs=("winner_secs", "mean"),
                top5_avg_secs=("top5_avg_secs", "mean"),
                median_secs=("median_secs", "mean"),
            )
            for _c in ["winner", "top5_avg", "median"]:
                agg_df[f"{_c}_mins"] = agg_df[f"{_c}_secs"] / 60
                agg_df[f"{_c}_fmt"] = agg_df[f"{_c}_secs"].apply(
                    lambda s: fmt_time(int(s)) if pd.notna(s) else "—"
                )

            def _trend_chart(df, y_col, y_title, tt_col=None):
                tt = [
                    alt.Tooltip("year:Q", title="Year", format="d"),
                    alt.Tooltip(f"{y_col}:Q", title=y_title, format=".1f"),
                ]
                if tt_col:
                    tt.append(alt.Tooltip(f"{tt_col}:N", title="Time"))
                return (
                    alt.Chart(df)
                    .mark_line(point=True)
                    .encode(
                        x=alt.X("year:Q", title="Year",
                                axis=alt.Axis(format="d", tickMinStep=1)),
                        y=alt.Y(f"{y_col}:Q", title=y_title,
                                scale=alt.Scale(zero=False)),
                        tooltip=tt,
                    )
                    .properties(height=270)
                    .interactive()
                )

            n = len(selected_ana)
            race_lbl = f"{n} race" if n == 1 else f"{n} races"
            if n > 1:
                st.caption(
                    f"Participants = total across {race_lbl} · "
                    "winning / top-5 / median = average of per-race values"
                )

            col5a, col5b = st.columns(2)
            with col5a:
                st.markdown("##### Participants")
                st.altair_chart(
                    _trend_chart(agg_df, "participants", "Finishers"),
                    width="stretch",
                )
            with col5b:
                st.markdown("##### Winning Time")
                st.altair_chart(
                    _trend_chart(agg_df, "winner_mins", "Minutes", tt_col="winner_fmt"),
                    width="stretch",
                )

            col5c, col5d = st.columns(2)
            with col5c:
                st.markdown("##### Top-5 Average")
                st.altair_chart(
                    _trend_chart(agg_df, "top5_avg_mins", "Minutes", tt_col="top5_avg_fmt"),
                    width="stretch",
                )
            with col5d:
                st.markdown("##### Median Finish")
                st.altair_chart(
                    _trend_chart(agg_df, "median_mins", "Minutes", tt_col="median_fmt"),
                    width="stretch",
                )

            # ── age group participation breakdown ──────────────────────────────

            _AG_ORDER_ANA = (
                [f"M{r}" for r in _AG_RANGES_ORDER] +
                [f"F{r}" for r in _AG_RANGES_ORDER]
            )

            st.divider()
            st.subheader("Participation by Age Group")
            st.caption(
                f"Aggregated across {len(selected_ana)} selected race{'s' if len(selected_ana) != 1 else ''}"
            )

            raw_plot = raw_ana[raw_ana["clean_name"].isin(selected_ana)].copy()
            raw_plot = raw_plot[raw_plot["age_group"].isin(_AG_ORDER_ANA)]
            raw_plot["ag_range"] = raw_plot["age_group"].str[1:]  # strip M/F

            sorted_ana_years = (
                sorted(raw_plot["year"].dropna().unique().astype(int).tolist())
                if not raw_plot.empty else []
            )
            ay1 = sorted_ana_years[0] if len(sorted_ana_years) >= 2 else None
            ay2 = sorted_ana_years[-1] if len(sorted_ana_years) >= 2 else None

            def _prep_counts(df_in):
                counts = (
                    df_in.groupby(["year", "ag_range"])
                    .size()
                    .reset_index(name="Finishers")
                    .rename(columns={"ag_range": "ag_label"})
                )
                counts["ag_label"] = pd.Categorical(
                    counts["ag_label"], categories=_AG_RANGES_ORDER, ordered=True
                )
                counts["Year"] = counts["year"].astype(str)
                return counts.sort_values(["ag_label", "year"])

            if not raw_plot.empty:
                st.markdown("#### Overall")
                _render_ag_participation(_prep_counts(raw_plot), ay1, ay2)

                st.markdown("#### Male")
                m_raw = raw_plot[raw_plot["age_group"].str.startswith("M")]
                if not m_raw.empty:
                    _render_ag_participation(_prep_counts(m_raw), ay1, ay2)

                st.markdown("#### Female")
                f_raw = raw_plot[raw_plot["age_group"].str.startswith("F")]
                if not f_raw.empty:
                    _render_ag_participation(_prep_counts(f_raw), ay1, ay2)


# ══════════════════════════════════════════════════════════════════════════════
# TAB 5 — race map
# ══════════════════════════════════════════════════════════════════════════════

with tab5:
    st.subheader("Race Comparison")


    _DEFAULT_PCTS = [10, 25, 50, 75, 90]
    _SWIM_DIST_M = {"Full": 3862.0, "70.3": 1900.0}
    _SWIM_DIST_Y = {"Full": 4224.0, "70.3": 2078.0}

    _SWIM_VENUES: dict = json.loads(
        (Path(__file__).parent / "data" / "swim_venues.json").read_text()
    )["races"]
    _SWIM_TYPE_LABEL = {"ocean": "Ocean/Sea", "lake": "Lake", "river": "River", "reservoir": "Reservoir"}

    rc_c1, rc_c2, rc_c3 = st.columns([1, 2, 2])
    with rc_c1:
        rc_dist = st.selectbox("Distance", ["Full", "70.3"], key="rc_dist")
    with rc_c2:
        rc_segment = st.selectbox("Segment", list(_SEGMENTS.keys()), key="rc_segment")
    with rc_c3:
        rc_pcts = st.multiselect(
            "Percentiles", [5, 10, 25, 50, 75, 90, 95],
            default=_DEFAULT_PCTS,
            key="rc_pcts",
        )

    rc_dist_db = _DIST_DB.get(rc_dist, "IRONMAN")
    raw_rc = load_segment_percentiles(rc_dist_db)
    if raw_rc.empty:
        st.info("No data available for the selected distance.")
    else:
        raw_rc["clean_name"] = raw_rc["series_name"].str.replace(r"^\d{4}\s+", "", regex=True)

        # Join swim venue metadata
        raw_rc["swim_type"] = raw_rc["clean_name"].map(
            lambda n: _SWIM_VENUES.get(n, {}).get("type", "unknown")
        )
        raw_rc["swim_venue"] = raw_rc["clean_name"].map(
            lambda n: _SWIM_VENUES.get(n, {}).get("venue_name", "")
        )

        avail_rc_years = sorted(raw_rc["year"].dropna().unique().astype(int).tolist())
        rc_c4, rc_c5, rc_c6 = st.columns([2, 2, 2])
        with rc_c4:
            if len(avail_rc_years) > 1:
                rc_yr_range = st.slider(
                    "Year range",
                    min_value=avail_rc_years[0], max_value=avail_rc_years[-1],
                    value=(avail_rc_years[0], avail_rc_years[-1]),
                    key="rc_yr_range",
                )
                rc_yr1, rc_yr2 = rc_yr_range
            else:
                rc_yr1 = rc_yr2 = avail_rc_years[0] if avail_rc_years else 2025
        with rc_c5:
            rc_gender = st.radio("Gender", ["All", "Male", "Female"], horizontal=True, key="rc_gender")
        with rc_c6:
            avail_swim_types = sorted(raw_rc["swim_type"].unique().tolist())
            swim_type_opts = ["All"] + [_SWIM_TYPE_LABEL.get(t, t.title()) for t in avail_swim_types if t != "unknown"]
            rc_swim_filter = st.selectbox("Swim type", swim_type_opts, key="rc_swim_type")

        filtered_rc = raw_rc[(raw_rc["year"] >= rc_yr1) & (raw_rc["year"] <= rc_yr2)].copy()
        if rc_gender != "All":
            filtered_rc = filtered_rc[filtered_rc["gender"] == rc_gender]
        if rc_swim_filter != "All":
            inv_label = {v: k for k, v in _SWIM_TYPE_LABEL.items()}
            filtered_rc = filtered_rc[filtered_rc["swim_type"] == inv_label.get(rc_swim_filter, rc_swim_filter)]

        seg_col = _SEGMENTS[rc_segment]
        filtered_rc = filtered_rc.dropna(subset=[seg_col])
        if filtered_rc.empty:
            st.info("No segment data for the current filters.")
        else:
            rc_pool = st.checkbox(
                "Pool years — combine all selected years into one row per race",
                value=False,
                key="rc_pool_years",
            )

            # Build label: per-edition when not pooling, per-series when pooling
            if rc_pool:
                filtered_rc["race_label"] = filtered_rc["clean_name"]
            else:
                filtered_rc["race_label"] = filtered_rc["clean_name"] + " (" + filtered_rc["year"].astype(str) + ")"

            all_race_labels = sorted(filtered_rc["race_label"].unique().tolist())
            rc_selected = st.multiselect(
                f"Races ({len(all_race_labels)} available)",
                all_race_labels,
                default=all_race_labels,
                key="rc_races",
            )

            if not rc_selected:
                st.info("Select at least one race above.")
            else:
                plot_rc = filtered_rc[filtered_rc["race_label"].isin(rc_selected)].copy()

                if not rc_pcts:
                    st.info("Select at least one percentile.")
                else:
                    pct_rows = []
                    for label, grp in plot_rc.groupby("race_label"):
                        vals = grp[seg_col].dropna()
                        if vals.empty:
                            continue
                        years_in_grp = sorted(grp["year"].dropna().unique().astype(int).tolist())
                        if rc_pool and len(years_in_grp) > 1:
                            year_label = f"{years_in_grp[0]}–{years_in_grp[-1]}"
                        else:
                            year_label = str(years_in_grp[0])
                        venue = grp["swim_venue"].iloc[0]
                        stype = _SWIM_TYPE_LABEL.get(grp["swim_type"].iloc[0], "")
                        for p in sorted(rc_pcts):
                            pct_rows.append({
                                "race_label": label,
                                "years": year_label,
                                "Percentile": f"P{p}",
                                "secs": float(np.percentile(vals, p)),
                                "mins": float(np.percentile(vals, p)) / 60,
                                "time_fmt": fmt_time(int(np.percentile(vals, p))),
                                "n": len(vals),
                                "swim_venue": venue,
                                "swim_type": stype,
                            })

                    pct_df = pd.DataFrame(pct_rows)

                    # Sort by median (or middle selected percentile), fastest on top
                    mid_p = 50 if 50 in rc_pcts else rc_pcts[len(rc_pcts) // 2]
                    median_order = (
                        pct_df[pct_df["Percentile"] == f"P{mid_p}"]
                        .sort_values("secs")["race_label"].tolist()
                    )
                    if not median_order:
                        median_order = sorted(pct_df["race_label"].unique().tolist())

                    # Gray span rule (min pct → max pct) to show spread per race
                    span_df = (
                        pct_df.groupby("race_label")
                        .agg(min_mins=("mins", "min"), max_mins=("mins", "max"))
                        .reset_index()
                    )
                    _time_axis = alt.Axis(
                        labelExpr=(
                            "floor(datum.value/60) + ':'"
                            " + (datum.value % 60 < 10 ? '0' : '')"
                            " + (datum.value % 60)"
                        ),
                        title=f"{rc_segment} Time",
                    )
                    _y_enc = alt.Y(
                        "race_label:N",
                        sort=median_order,
                        title=None,
                        axis=alt.Axis(labelLimit=320, labelFontSize=11),
                    )

                    rule_layer = (
                        alt.Chart(span_df)
                        .mark_rule(strokeWidth=2, color="#cbd5e1")
                        .encode(
                            y=_y_enc,
                            x=alt.X("min_mins:Q", axis=_time_axis),
                            x2="max_mins:Q",
                        )
                    )
                    # Distinct, ordered colors: blue (fast) → red (slow)
                    _PCT_COLORS = {
                        "P5":  "#1d4ed8", "P10": "#3b82f6", "P25": "#10b981",
                        "P50": "#f59e0b", "P75": "#f97316", "P90": "#ef4444", "P95": "#991b1b",
                    }
                    pct_domain = [f"P{p}" for p in sorted(rc_pcts)]
                    pct_range  = [_PCT_COLORS.get(k, "#94a3b8") for k in pct_domain]

                    dot_layer = (
                        alt.Chart(pct_df)
                        .mark_point(filled=True, size=90, opacity=0.92)
                        .encode(
                            y=_y_enc,
                            x=alt.X("mins:Q", axis=_time_axis),
                            color=alt.Color(
                                "Percentile:N",
                                title="Percentile",
                                scale=alt.Scale(domain=pct_domain, range=pct_range),
                                sort=pct_domain,
                            ),
                            tooltip=[
                                alt.Tooltip("race_label:N", title="Race"),
                                alt.Tooltip("years:N", title="Years"),
                                alt.Tooltip("swim_venue:N", title="Swim Venue"),
                                alt.Tooltip("swim_type:N", title="Swim Type"),
                                alt.Tooltip("Percentile:N"),
                                alt.Tooltip("time_fmt:N", title="Time"),
                                alt.Tooltip("n:Q", title="Finishers"),
                            ],
                        )
                    )
                    chart_h = max(320, len(median_order) * 24)
                    st.altair_chart(
                        (rule_layer + dot_layer).properties(height=chart_h),
                        width="stretch",
                    )

                    # Summary table: one row per race, one column per percentile
                    pivot = pct_df.pivot(index="race_label", columns="Percentile", values="time_fmt")
                    meta_map = pct_df.groupby("race_label")[["n", "swim_venue", "swim_type", "years"]].first()
                    pivot.insert(0, "Years", meta_map["years"])
                    pivot.insert(1, "Swim Type", meta_map["swim_type"])
                    pivot.insert(2, "Swim Venue", meta_map["swim_venue"])
                    pivot.insert(3, "Finishers", meta_map["n"])
                    pivot.index.name = "Race"
                    pivot.columns.name = None
                    st.dataframe(pivot.loc[median_order], width="stretch")

                    # ── Cross-reference: athletes who did ALL selected races in the same year ──
                    st.divider()
                    st.markdown("#### Athletes Who Completed All Selected Races")

                    # One row per (athlete, year, race)
                    xref = (
                        plot_rc.dropna(subset=[seg_col])
                        .groupby(["athlete_name", "country_iso2", "year", "race_label", "age_group"], as_index=False)
                        [seg_col].min()
                    )
                    xref["time_fmt"] = xref[seg_col].apply(
                        lambda s: fmt_time_sortable(int(s)) if pd.notna(s) else "—"
                    )

                    # Qualify: (athlete, year) rows where they completed every selected race label
                    n_selected_races = plot_rc["race_label"].nunique()
                    per_ay = (
                        xref.groupby(["athlete_name", "country_iso2", "year"])["race_label"]
                        .nunique()
                        .reset_index(name="race_count")
                    )
                    qualifying_ay = per_ay[per_ay["race_count"] == n_selected_races][
                        ["athlete_name", "country_iso2", "year"]
                    ]

                    if qualifying_ay.empty:
                        st.info("No athletes completed all selected races in the same year.")
                    else:
                        xref_q = xref.merge(qualifying_ay, on=["athlete_name", "country_iso2", "year"])

                        # ── Aggregate summary ─────────────────────────────────
                        agg = (
                            xref_q.groupby("race_label")[seg_col]
                            .mean()
                            .reset_index(name="avg_secs")
                        )
                        agg["Avg Time"] = agg["avg_secs"].apply(lambda s: fmt_time(int(s)))
                        fastest = agg["avg_secs"].min()
                        agg["Δ vs Fastest"] = agg["avg_secs"].apply(
                            lambda s: ("+" + fmt_time(int(s - fastest))) if s > fastest else "—"
                        )
                        agg["% vs Fastest"] = agg["avg_secs"].apply(
                            lambda s: (f"+{(s - fastest) / fastest * 100:.1f}%") if s > fastest else "—"
                        )
                        agg["Athletes"] = xref_q.groupby("race_label")["athlete_name"].nunique().values
                        if rc_segment == "Swim":
                            dm = _SWIM_DIST_M[rc_dist]
                            dy = _SWIM_DIST_Y[rc_dist]
                            agg["Pace/100y (100m)"] = agg["avg_secs"].apply(
                                lambda s: f"{fmt_time(int(s / dy * 100))} ({fmt_time(int(s / dm * 100))})"
                            )
                        agg = agg.rename(columns={"race_label": "Race"}).drop(columns="avg_secs")
                        agg["_order"] = agg["Race"].map(
                            {r: i for i, r in enumerate(median_order)}
                        ).fillna(999)
                        agg = agg.sort_values("_order").drop(columns="_order").reset_index(drop=True)
                        st.dataframe(agg, width="stretch", hide_index=True)

                        # ── Per-athlete table ─────────────────────────────────
                        xref_pivot = xref_q.pivot_table(
                            index=["athlete_name", "country_iso2", "year"],
                            columns="race_label",
                            values="time_fmt",
                            aggfunc="first",
                        )
                        xref_pivot.columns.name = None
                        xref_pivot = xref_pivot.reset_index()
                        xref_pivot.rename(columns={
                            "athlete_name": "Athlete",
                            "country_iso2": "Country",
                            "year": "Year",
                        }, inplace=True)
                        xref_pivot["Year"] = xref_pivot["Year"].astype(int)

                        ag_map = xref_q.groupby(["athlete_name", "year"])["age_group"].first()
                        xref_pivot["AG"] = xref_pivot.apply(
                            lambda r: ag_map.get((r["Athlete"], r["Year"]), ""), axis=1
                        )

                        if rc_segment == "Swim":
                            dm = _SWIM_DIST_M[rc_dist]
                            dy = _SWIM_DIST_Y[rc_dist]
                            xref_q["pace_fmt"] = xref_q[seg_col].apply(
                                lambda s: f"{fmt_time(int(s / dy * 100))} ({fmt_time(int(s / dm * 100))})"
                            )
                            ppace = xref_q.pivot_table(
                                index=["athlete_name", "country_iso2", "year"],
                                columns="race_label", values="pace_fmt", aggfunc="first",
                            )
                            ppace.columns.name = None
                            ppace.columns = [f"{c} Pace" for c in ppace.columns]
                            ppace = ppace.reset_index().rename(columns={
                                "athlete_name": "Athlete", "country_iso2": "Country", "year": "Year",
                            })
                            xref_pivot = xref_pivot.merge(ppace, on=["Athlete", "Country", "Year"], how="left")

                        rc_col_names = [c for c in xref_pivot.columns if c not in ("Athlete", "Country", "Year", "AG")]
                        ordered_cols = [c for c in median_order if c in rc_col_names]
                        remaining   = [c for c in rc_col_names if c not in ordered_cols]
                        # Interleave: time, pace for each race in order
                        interleaved = []
                        for rc in ordered_cols:
                            interleaved.append(rc)
                            if f"{rc} Pace" in rc_col_names:
                                interleaved.append(f"{rc} Pace")
                        interleaved += [c for c in remaining if c not in interleaved]
                        xref_pivot = xref_pivot[["Athlete", "Country", "Year", "AG"] + interleaved]
                        xref_pivot = xref_pivot.sort_values(["Athlete", "Year"]).reset_index(drop=True)

                        n_athletes = xref_pivot["Athlete"].nunique()
                        n_ay = len(qualifying_ay)
                        st.caption(f"{n_athletes:,} athletes · {n_ay} athlete-year combinations · {rc_segment} times")
                        st.dataframe(xref_pivot, width="stretch", hide_index=True)


with tab6:
    st.subheader("Athlete Year-Over-Year")

    c1, c2, c3, c4 = st.columns([1, 1, 1, 2])
    with c1:
        yoy_dist = st.selectbox("Distance", ["Full", "70.3"], key="yoy_dist")
    with c4:
        yoy_seg = st.selectbox("Segment", list(_SEGMENTS.keys()), key="yoy_seg")

    yoy_all = load_segment_percentiles(_DIST_DB[yoy_dist]).copy()
    yoy_all["clean_name"] = yoy_all["event_name"].str.replace(r"^\d{4}\s+", "", regex=True)
    avail_yoy_years = sorted(yoy_all["year"].dropna().unique().astype(int))

    if len(avail_yoy_years) < 2:
        st.info("Need at least 2 years of data for this distance.")
    else:
        with c2:
            year_a = int(st.selectbox("Year A", avail_yoy_years,
                                      index=len(avail_yoy_years) - 2, key="yoy_ya"))
        with c3:
            year_b = int(st.selectbox("Year B", avail_yoy_years,
                                      index=len(avail_yoy_years) - 1, key="yoy_yb"))

        if year_a == year_b:
            st.warning("Year A and Year B must be different.")
        else:
            yoy_seg_col = _SEGMENTS[yoy_seg]

            # Best result per athlete per race per year — merge on race so only
            # same-race cross-year pairs are compared (no Florida→Jacksonville matches)
            raw_a = yoy_all[yoy_all["year"] == year_a].dropna(subset=[yoy_seg_col])
            idx_a = raw_a.groupby(["athlete_name", "country_iso2", "clean_name"])[yoy_seg_col].idxmin()
            df_a = (raw_a.loc[idx_a]
                    [["athlete_name", "country_iso2", "gender", "age_group",
                      yoy_seg_col, "clean_name"]]
                    .rename(columns={yoy_seg_col: "secs_a", "age_group": f"ag_{year_a}"}))

            raw_b = yoy_all[yoy_all["year"] == year_b].dropna(subset=[yoy_seg_col])
            idx_b = raw_b.groupby(["athlete_name", "country_iso2", "clean_name"])[yoy_seg_col].idxmin()
            df_b = (raw_b.loc[idx_b]
                    [["athlete_name", "country_iso2", "age_group", yoy_seg_col, "clean_name"]]
                    .rename(columns={yoy_seg_col: "secs_b", "age_group": f"ag_{year_b}"}))

            merged = df_a.merge(df_b, on=["athlete_name", "country_iso2", "clean_name"])
            merged["delta_secs"] = merged["secs_b"] - merged["secs_a"]

            if merged.empty:
                st.info("No athletes found in both selected years.")
            else:
                # ── age group filter ───────────────────────────────────
                all_ags = sorted(merged[f"ag_{year_b}"].dropna().unique())
                sel_ags = st.multiselect(
                    "Age Groups", all_ags, placeholder="All age groups", key="yoy_ags"
                )
                if sel_ags:
                    merged = merged[merged[f"ag_{year_b}"].isin(sel_ags)]

                # ── direction / improvement filter ─────────────────────
                fc1, fc2 = st.columns([2, 3])
                with fc1:
                    yoy_dir = st.radio("Show", ["All", "Improved", "Declined"],
                                       horizontal=True, key="yoy_dir")
                with fc2:
                    if yoy_dir in ("Improved", "Declined"):
                        min_delta_min = st.slider(
                            "By at least (minutes)", 0, 120, 0, key="yoy_min_delta"
                        )
                    else:
                        min_delta_min = 0

                if yoy_dir == "Improved":
                    filtered_yoy = merged[merged["delta_secs"] <= -min_delta_min * 60]
                elif yoy_dir == "Declined":
                    filtered_yoy = merged[merged["delta_secs"] >= min_delta_min * 60]
                else:
                    filtered_yoy = merged

                if filtered_yoy.empty:
                    st.info("No athletes match the current filters.")
                else:
                    # ── summary metrics ────────────────────────────────
                    n_total = len(merged)
                    n_imp = (merged["delta_secs"] < 0).sum()
                    n_dec = (merged["delta_secs"] > 0).sum()
                    avg_d = merged["delta_secs"].mean()
                    sign = "+" if avg_d >= 0 else ""

                    m1, m2, m3, m4 = st.columns(4)
                    m1.metric("Athletes in both years", f"{n_total:,}")
                    m2.metric("Improved", f"{n_imp:,} ({n_imp / n_total * 100:.0f}%)")
                    m3.metric("Declined", f"{n_dec:,} ({n_dec / n_total * 100:.0f}%)")
                    m4.metric("Avg Δ", f"{sign}{fmt_time(int(abs(avg_d)))}")

                    # ── scatter chart ──────────────────────────────────
                    pdata = filtered_yoy.copy()
                    pdata["ya_mins"] = pdata["secs_a"] / 60
                    pdata["yb_mins"] = pdata["secs_b"] / 60
                    pdata["direction"] = pdata["delta_secs"].apply(
                        lambda d: "Improved" if d < -60 else ("Declined" if d > 60 else "Similar")
                    )
                    pdata["ya_fmt"] = pdata["secs_a"].apply(fmt_time_sortable)
                    pdata["yb_fmt"] = pdata["secs_b"].apply(fmt_time_sortable)
                    pdata["delta_min"] = (pdata["delta_secs"] / 60).round(1)

                    _mn = min(pdata["ya_mins"].min(), pdata["yb_mins"].min()) * 0.99
                    _mx = max(pdata["ya_mins"].max(), pdata["yb_mins"].max()) * 1.01
                    ref_df = pd.DataFrame({"x": [_mn, _mx], "y": [_mn, _mx]})

                    _hm_axis = alt.Axis(
                        labelExpr=(
                            "floor(datum.value/60) + ':'"
                            " + (datum.value % 60 < 10 ? '0' : '')"
                            " + (datum.value % 60)"
                        )
                    )
                    ref_line = alt.Chart(ref_df).mark_line(
                        strokeDash=[5, 5], color="#94a3b8", strokeWidth=1.5
                    ).encode(
                        x=alt.X("x:Q", axis=_hm_axis, title=f"{year_a} {yoy_seg}"),
                        y=alt.Y("y:Q", axis=_hm_axis, title=f"{year_b} {yoy_seg}"),
                    )
                    dots = alt.Chart(pdata).mark_circle(size=65, opacity=0.75).encode(
                        x=alt.X("ya_mins:Q", axis=_hm_axis, title=f"{year_a} {yoy_seg}"),
                        y=alt.Y("yb_mins:Q", axis=_hm_axis, title=f"{year_b} {yoy_seg}"),
                        color=alt.Color("direction:N", sort=["Improved", "Similar", "Declined"],
                                        scale=alt.Scale(
                                            domain=["Improved", "Similar", "Declined"],
                                            range=["#10b981", "#94a3b8", "#ef4444"],
                                        )),
                        tooltip=[
                            alt.Tooltip("athlete_name:N", title="Athlete"),
                            alt.Tooltip("country_iso2:N", title="Country"),
                            alt.Tooltip("clean_name:N", title="Race"),
                            alt.Tooltip(f"ag_{year_b}:N", title="AG"),
                            alt.Tooltip("ya_fmt:N", title=str(year_a)),
                            alt.Tooltip("yb_fmt:N", title=str(year_b)),
                            alt.Tooltip("delta_min:Q", title="Δ (min)", format="+.1f"),
                        ],
                    )
                    st.altair_chart(
                        (ref_line + dots).properties(height=460),
                        use_container_width=True,
                    )

                    # ── table ──────────────────────────────────────────
                    tbl = filtered_yoy[
                        ["athlete_name", "country_iso2", "gender",
                         f"ag_{year_a}", f"ag_{year_b}",
                         "clean_name", "secs_a", "secs_b", "delta_secs"]
                    ].copy()
                    tbl[str(year_a)] = tbl["secs_a"].apply(fmt_time_sortable)
                    tbl[str(year_b)] = tbl["secs_b"].apply(fmt_time_sortable)
                    tbl["Δ (min)"] = (tbl["delta_secs"] / 60).round(1)
                    tbl["% Δ"] = (tbl["delta_secs"] / tbl["secs_a"] * 100).round(1)
                    tbl = (tbl
                           .drop(columns=["secs_a", "secs_b", "delta_secs"])
                           .rename(columns={
                               "athlete_name": "Athlete",
                               "country_iso2": "Country",
                               "gender": "Gender",
                               f"ag_{year_a}": f"AG {year_a}",
                               f"ag_{year_b}": f"AG {year_b}",
                               "clean_name": "Race",
                           })
                           .sort_values("Δ (min)")
                           .reset_index(drop=True))
                    tbl = tbl[["Athlete", "Country", "Gender",
                               f"AG {year_a}", f"AG {year_b}",
                               "Race", str(year_a), str(year_b), "Δ (min)", "% Δ"]]
                    st.caption(f"{len(tbl):,} athletes shown · sorted by Δ (most improved first)")
                    st.dataframe(tbl, use_container_width=True, hide_index=True)


with tab7:
    st.subheader("Race Map")

    map_df = load_map_races()

    _DIST_COLORS = {"Full": "#e31837", "70.3": "#3b82f6", "Other": "#94a3b8"}

    fig_map = px.scatter_geo(
        map_df,
        lat="lat",
        lon="lon",
        color="distance_type",
        color_discrete_map=_DIST_COLORS,
        hover_name="clean_name",
        hover_data={
            "lat": False,
            "lon": False,
            "distance_type": True,
            "num_years": True,
            "latest_year": True,
        },
        size="num_years",
        size_max=14,
        custom_data=["group_uuid", "clean_name", "distance_type", "num_years", "latest_year"],
        labels={"distance_type": "Distance", "num_years": "Years of data", "latest_year": "Latest year"},
        projection="natural earth",
    )
    fig_map.update_layout(
        legend_title_text="Distance",
        clickmode="event+select",  # single click registers as a selection event
        geo=dict(
            showland=True,
            landcolor="#f1f5f9",
            showocean=True,
            oceancolor="#e0f2fe",
            showlakes=True,
            lakecolor="#e0f2fe",
            showcountries=True,
            countrycolor="#cbd5e1",
            showcoastlines=True,
            coastlinecolor="#94a3b8",
            showframe=False,
        ),
        margin=dict(l=0, r=0, t=0, b=0),
        height=500,
        paper_bgcolor="rgba(0,0,0,0)",
    )

    map_event = st.plotly_chart(fig_map, on_select="rerun", key="race_map")

    selected_pts = []
    try:
        if map_event and map_event.selection:
            selected_pts = map_event.selection.points or []
    except Exception:
        pass

    if not selected_pts:
        st.caption("Click any race marker to view results for that venue.")
    else:
        pt = selected_pts[0]
        # Streamlit wraps customdata as a list; fall back to point_index lookup if missing
        cd = pt.get("customdata") or pt.get("custom_data") or []
        if len(cd) < 5:
            st.warning("Could not read race data from selection — try clicking again.")
        else:
            sel_uuid, sel_name, sel_dist, sel_nyears, sel_latest = cd[0], cd[1], cd[2], int(cd[3]), int(cd[4])

            st.divider()
            badge_color = _DIST_COLORS.get(sel_dist, "#94a3b8")
            st.markdown(
                f"### {sel_name} "
                f"<span style='background:{badge_color};color:white;padding:2px 10px;"
                f"border-radius:20px;font-size:0.8rem;font-weight:700;vertical-align:middle;'>"
                f"{sel_dist}</span>",
                unsafe_allow_html=True,
            )

            df_map_series = load_series_results(sel_uuid)
            avail_map_years = sorted(df_map_series["year"].dropna().unique().astype(int).tolist(), reverse=True)

            if not avail_map_years:
                st.info("No results available for this venue.")
            else:
                map_year = st.selectbox(
                    "Year", avail_map_years,
                    index=0,
                    key="map_year_sel",
                )
                df_map_year = df_map_series[df_map_series["year"] == map_year]
                finishers_map = df_map_year if not df_map_year.empty else pd.DataFrame()

                mc1, mc2, mc3, mc4 = st.columns(4)
                mc1.metric("Years of data", sel_nyears)
                mc2.metric("Finishers", f"{len(finishers_map):,}" if not finishers_map.empty else "—")
                if not finishers_map.empty:
                    finish_s = finishers_map["finish_secs"].dropna().replace([np.inf, -np.inf], np.nan).dropna()
                    mc3.metric("Median finish", fmt_time(int(finish_s.median())) if len(finish_s) else "—")
                    mc4.metric("Course record", fmt_time(int(finish_s.min())) if len(finish_s) else "—")

                    hist_map = time_histogram(finishers_map["finish_secs"], 20)
                    if not hist_map.empty:
                        st.altair_chart(hist_chart(hist_map, 300), width="content")


# ══════════════════════════════════════════════════════════════════════════════
# TAB 8 — For Keenan
# ══════════════════════════════════════════════════════════════════════════════

with tab8:
    import re as _re

    st.markdown("## For Keenan")


    # ── load data ─────────────────────────────────────────────────────────────
    # .copy() prevents mutating the @st.cache_data-returned object across reruns
    part_raw = load_keenan_participation().copy()
    ag_raw = load_keenan_age_groups().copy()
    races_raw = load_keenan_race_counts().copy()

    # add distance labels up-front so filtered slices always have the column
    _dist_map = {"IRONMAN": "Full (140.6)", "IRONMAN 70.3": "70.3"}
    part_raw["distance"] = part_raw["distance_type"].map(_dist_map)
    races_raw["distance"] = races_raw["distance_type"].map(_dist_map)

    # ── Toggle (applied before all derived data) ──────────────────────────────
    excl_2020 = st.toggle("Exclude 2020", value=False, key="keenan_excl_2020",
                          help="2020 was a COVID year — most races were cancelled")

    part_plot = part_raw[part_raw["year"] != 2020].copy() if excl_2020 else part_raw
    ag_plot = ag_raw[ag_raw["year"] != 2020].copy() if excl_2020 else ag_raw
    races_plot = races_raw[races_raw["year"] != 2020].copy() if excl_2020 else races_raw

    total_by_year = part_plot.groupby("year")["finishers"].sum().reset_index().rename(columns={"finishers": "total"})

    # age bracket classifier — returns None for pros, para, unknown, or <18
    def _bracket(ag: str) -> str | None:
        m = _re.search(r"\d+", ag)
        if not m:
            return None
        age = int(m.group())
        if age < 18:
            return None
        if age < 30:
            return "18–29"
        if age < 45:
            return "30–44"
        if age < 60:
            return "45–59"
        return "60+"

    ag_plot["bracket"] = ag_plot["age_group"].apply(_bracket)
    bracket_df = (
        ag_plot.dropna(subset=["bracket"])
        .groupby(["year", "bracket"])["finishers"]
        .sum()
        .reset_index()
    )

    # young people % denominator = age-group finishers only (excludes pros, para, nulls)
    ag_total_by_year = (
        bracket_df.groupby("year")["finishers"].sum()
        .reset_index().rename(columns={"finishers": "ag_total"})
    )
    young_df = bracket_df[bracket_df["bracket"] == "18–29"].merge(ag_total_by_year, on="year")
    young_df["pct"] = young_df["finishers"] / young_df["ag_total"] * 100

    # ── headline metrics ───────────────────────────────────────────────────────
    yr25 = total_by_year[total_by_year["year"] == 2025]["total"].values
    yr16 = total_by_year[total_by_year["year"] == 2016]["total"].values
    total_25 = int(yr25[0]) if len(yr25) else 0
    total_16 = int(yr16[0]) if len(yr16) else 0
    growth_pct = (total_25 - total_16) / total_16 * 100 if total_16 else 0

    young_25 = young_df[young_df["year"] == 2025]
    young_16 = young_df[young_df["year"] == 2016]
    young_pct_25 = float(young_25["pct"].values[0]) if not young_25.empty else 0
    young_pct_16 = float(young_16["pct"].values[0]) if not young_16.empty else 0

    km1, km2 = st.columns(2)
    km1.metric("2025 Participants", f"{total_25:,}")
    km2.metric("Growth 2016→2025", f"+{growth_pct:.0f}%", delta=f"+{total_25 - total_16:,} athletes")

    st.divider()

    # ── Sections 1 & 2: side by side ─────────────────────────────────────────
    pc_col, rc_col = st.columns(2)

    _dist_scale = alt.Scale(domain=["Full (140.6)", "70.3"], range=["#e31837", "#3b82f6"])
    _dist_legend = alt.Legend(title="Distance")

    with pc_col:
        st.subheader("Total Participation")
        part_chart = (
            alt.Chart(part_plot)
            .mark_bar()
            .encode(
                x=alt.X("year:O", axis=alt.Axis(labelAngle=0, title="Year")),
                y=alt.Y("finishers:Q", stack="zero", axis=alt.Axis(title="Participants", format=",d")),
                color=alt.Color("distance:N", scale=_dist_scale, legend=_dist_legend),
                tooltip=[
                    alt.Tooltip("year:O", title="Year"),
                    alt.Tooltip("distance:N", title="Distance"),
                    alt.Tooltip("finishers:Q", title="Participants", format=",d"),
                ],
            )
            .properties(height=280)
        )
        st.altair_chart(part_chart.configure_view(strokeWidth=0), use_container_width=True)

    with rc_col:
        st.subheader("Number of Races")
        races_chart = (
            alt.Chart(races_plot)
            .mark_bar()
            .encode(
                x=alt.X("year:O", axis=alt.Axis(labelAngle=0, title="Year")),
                y=alt.Y("races:Q", stack="zero", axis=alt.Axis(title="Races")),
                color=alt.Color("distance:N", scale=_dist_scale, legend=None),
                tooltip=[
                    alt.Tooltip("year:O", title="Year"),
                    alt.Tooltip("distance:N", title="Distance"),
                    alt.Tooltip("races:Q", title="Races"),
                ],
            )
            .properties(height=280)
        )
        st.altair_chart(races_chart.configure_view(strokeWidth=0), use_container_width=True)

    st.divider()

    # ── Section 4: The Youth Wave ─────────────────────────────────────────────
    st.subheader("The Youth Wave (Ages 18–29)")

    yw_col1, yw_col2 = st.columns(2)

    with yw_col1:
        st.markdown("**Absolute count**")
        young_bar = (
            alt.Chart(young_df)
            .mark_bar(color="#22c55e")
            .encode(
                x=alt.X("year:O", axis=alt.Axis(labelAngle=0, title="Year")),
                y=alt.Y("finishers:Q", axis=alt.Axis(title="Participants", format=",d")),
                tooltip=[
                    alt.Tooltip("year:O", title="Year"),
                    alt.Tooltip("finishers:Q", title="18–29 participants", format=",d"),
                ],
            )
            .properties(height=250)
        )
        young_label = (
            alt.Chart(young_df)
            .mark_text(dy=-8, color="#22c55e", fontSize=11, fontWeight="bold")
            .encode(
                x=alt.X("year:O"),
                y=alt.Y("finishers:Q"),
                text=alt.Text("finishers:Q", format=",d"),
            )
        )
        st.altair_chart(alt.layer(young_bar, young_label).configure_view(strokeWidth=0), use_container_width=True)

    with yw_col2:
        st.markdown("**Share of all participants**")
        young_line = (
            alt.Chart(young_df)
            .mark_line(point=alt.OverlayMarkDef(size=80), color="#22c55e", strokeWidth=2.5)
            .encode(
                x=alt.X("year:O", axis=alt.Axis(labelAngle=0, title="Year")),
                y=alt.Y("pct:Q", axis=alt.Axis(title="% of all participants", format=".1f")),
                tooltip=[
                    alt.Tooltip("year:O", title="Year"),
                    alt.Tooltip("pct:Q", title="% of participants", format=".1f"),
                ],
            )
            .properties(height=250)
        )
        st.altair_chart(young_line.configure_view(strokeWidth=0), use_container_width=True)

    young_16_abs = young_df[young_df["year"] == 2016]["finishers"].values
    young_25_abs = young_df[young_df["year"] == 2025]["finishers"].values
    if len(young_16_abs) and len(young_25_abs):
        young_growth = (young_25_abs[0] - young_16_abs[0]) / young_16_abs[0] * 100
        st.info(
            f"Athletes aged 18–29 grew **{young_growth:.0f}%** from 2016 to 2025 "
            f"({int(young_16_abs[0]):,} → {int(young_25_abs[0]):,}), "
            f"outpacing overall growth of {growth_pct:.0f}%."
        )

    st.divider()

    # ── Revenue estimate ──────────────────────────────────────────────────────
    st.subheader("Estimated Entry Fee Revenue")

    _fee_full = 1000
    _fee_703 = 500

    rev_data = []
    for _, row in part_plot.iterrows():
        fee = _fee_full if row["distance_type"] == "IRONMAN" else _fee_703
        rev_data.append({"year": int(row["year"]), "distance": row["distance"], "revenue": int(row["finishers"]) * fee})
    rev_df = pd.DataFrame(rev_data)
    rev_by_year = rev_df.groupby("year")["revenue"].sum().reset_index()

    rev_chart = (
        alt.Chart(rev_df)
        .mark_bar()
        .encode(
            x=alt.X("year:O", axis=alt.Axis(labelAngle=0, title="Year")),
            y=alt.Y("revenue:Q", stack="zero", axis=alt.Axis(title="Est. Entry Fee Revenue (USD)", format="$,.0f")),
            color=alt.Color("distance:N", scale=alt.Scale(domain=["Full (140.6)", "70.3"], range=["#e31837", "#3b82f6"]), legend=alt.Legend(title="Distance")),
            tooltip=[
                alt.Tooltip("year:O", title="Year"),
                alt.Tooltip("distance:N", title="Distance"),
                alt.Tooltip("revenue:Q", title="Revenue", format="$,.0f"),
            ],
        )
        .properties(height=300)
    )
    st.altair_chart(rev_chart.configure_view(strokeWidth=0), use_container_width=True)

    rev_16 = int(rev_by_year[rev_by_year["year"] == 2016]["revenue"].values[0])
    rev_25 = int(rev_by_year[rev_by_year["year"] == 2025]["revenue"].values[0])
    st.caption(
        f"Estimated at $1,000/entry for full IRONMAN and $500/entry for 70.3. "
        f"2016: **${rev_16/1e6:.1f}M** → 2025: **${rev_25/1e6:.1f}M** "
        f"(+{(rev_25 - rev_16) / rev_16 * 100:.0f}%). Entry fees are the primary revenue driver for the IRONMAN Group."
    )

