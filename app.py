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
             AND r.distance_type LIKE ?
           ORDER BY eg.name, r.year, res.finish_secs""",
        get_conn(),
        params=list(age_groups) + [f"%{dist_filter}%"],
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


# ── helpers ───────────────────────────────────────────────────────────────────

def fmt_time(secs) -> str:
    if secs is None or (isinstance(secs, float) and np.isnan(secs)):
        return "—"
    secs = int(secs)
    h, rem = divmod(secs, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


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

st.markdown("""
<div style="display:flex;align-items:center;gap:1.2rem;padding:1.2rem 1.6rem;
            background:linear-gradient(135deg,#0d0d0d 0%,#1e1e1e 100%);
            border-radius:14px;margin-bottom:0.5rem;border-left:5px solid #e31837;
            box-shadow:0 4px 20px rgba(0,0,0,0.15);">
  <svg width="54" height="54" viewBox="0 0 54 54" xmlns="http://www.w3.org/2000/svg">
    <circle cx="27" cy="27" r="27" fill="#e31837"/>
    <text x="27" y="37" text-anchor="middle"
          font-family="Georgia,'Times New Roman',serif"
          font-size="28" font-weight="900" fill="white" letter-spacing="-1">M</text>
    <circle cx="40" cy="13" r="5.5" fill="white"/>
  </svg>
  <div>
    <div style="color:white;font-size:1.65rem;font-weight:800;letter-spacing:0.1em;
                line-height:1.1;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;">
      IRONMAN RESULTS
    </div>
    <div style="color:#e31837;font-size:0.72rem;font-weight:700;letter-spacing:0.22em;
                text-transform:uppercase;margin-top:5px;">
      ANYTHING IS POSSIBLE
    </div>
  </div>
</div>
""", unsafe_allow_html=True)
tab1, tab2, tab3, tab4, tab5 = st.tabs(["Race Results", "Year-over-Year Comparison", "Athlete Search", "Analytics", "Race Map"])


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
                params=(sel_series["group_uuid"],),
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
            value=True,
            key="ana_both",
        )

    _ranges = ana_ages if ana_ages else _AGE_RANGES
    _age_group_list = tuple(
        f"{g}{r}" for g in _GENDER_MAP[ana_gender] for r in _ranges
    )
    raw_ana = load_trend_raw(_age_group_list, ana_dist).copy()
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

            def _trend_chart(df, y_col, y_title, tt_col=None):
                tt = [
                    alt.Tooltip("clean_name:N", title="Race"),
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
                        color=alt.Color("clean_name:N", title="Race"),
                        tooltip=tt,
                    )
                    .properties(height=270)
                    .interactive()
                )

            col5a, col5b = st.columns(2)
            with col5a:
                st.markdown("##### Participants")
                st.altair_chart(
                    _trend_chart(plot_df, "participants", "Finishers"),
                    width="stretch",
                )
            with col5b:
                st.markdown("##### Winning Time")
                st.altair_chart(
                    _trend_chart(plot_df, "winner_mins", "Minutes", tt_col="winner_fmt"),
                    width="stretch",
                )

            col5c, col5d = st.columns(2)
            with col5c:
                st.markdown("##### Top-5 Average")
                st.altair_chart(
                    _trend_chart(plot_df, "top5_avg_mins", "Minutes", tt_col="top5_avg_fmt"),
                    width="stretch",
                )
            with col5d:
                st.markdown("##### Median Finish")
                st.altair_chart(
                    _trend_chart(plot_df, "median_mins", "Minutes", tt_col="median_fmt"),
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
