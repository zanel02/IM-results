import json
import sqlite3
from pathlib import Path

import numpy as np
import pandas as pd
import streamlit as st

DB_PATH = Path(__file__).parent / "data" / "ironman.db"

st.set_page_config(page_title="Ironman Results", layout="wide")

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
    """Return distinct athlete names matching a partial, case-insensitive query."""
    return pd.read_sql(
        """SELECT DISTINCT athlete_name
           FROM results
           WHERE lower(athlete_name) LIKE lower(?)
           ORDER BY athlete_name
           LIMIT 50""",
        get_conn(),
        params=(f"%{query}%",),
    )


@st.cache_data
def load_athlete_results(athlete_name: str) -> pd.DataFrame:
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
           ORDER BY r.year DESC, r.event_date DESC""",
        get_conn(),
        params=(athlete_name,),
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


# ── helpers ───────────────────────────────────────────────────────────────────

def fmt_time(secs) -> str:
    if secs is None or (isinstance(secs, float) and np.isnan(secs)):
        return "—"
    secs = int(secs)
    h, rem = divmod(secs, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def time_histogram(series: pd.Series, bin_minutes: int) -> pd.DataFrame:
    mins = (series / 60).dropna()
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
    return pd.DataFrame({"Athletes": counts}, index=labels)


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

st.title("Ironman Results")
tab1, tab2, tab3 = st.tabs(["Race Results", "Year-over-Year Comparison", "Athlete Search"])


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

            hourly_df = pd.DataFrame(weather["hourly"]).rename(columns={
                "hour": "Hour",
                "temp_f": "Temp (°F)",
                "humidity": "Humidity (%)",
                "wind_mph": "Wind (mph)",
                "wind_dir": "Dir",
                "precip_in": "Precip (in)",
                "conditions": "Conditions",
            })
            if tz_label:
                st.caption(f"All times in {tz_label}")
            st.dataframe(hourly_df, use_container_width=True, hide_index=True)

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
            "AWA Pts":   df["awa_points"].apply(lambda x: f"{int(x):,}" if pd.notna(x) else ""),
        })
        if selected_ag != "All":
            display.insert(1, "Overall", df["rank_overall"].astype("Int64"))

        st.subheader(f"Results — {selected_ag}")
        st.dataframe(display, use_container_width=True, hide_index=True, height=600)

        st.divider()
        st.subheader("Finish Time Distribution")
        hist = time_histogram(df["finish_secs"], bin_minutes=15)
        if not hist.empty:
            st.bar_chart(hist, height=260, use_container_width=True)

        st.subheader("Segment Distributions")
        col_swim, col_bike, col_run = st.columns(3)
        with col_swim:
            st.markdown("**Swim**")
            h = time_histogram(df["swim_secs"], bin_minutes=5)
            if not h.empty:
                st.bar_chart(h, height=240, use_container_width=True)
        with col_bike:
            st.markdown("**Bike**")
            h = time_histogram(df["bike_secs"], bin_minutes=10)
            if not h.empty:
                st.bar_chart(h, height=240, use_container_width=True)
        with col_run:
            st.markdown("**Run**")
            h = time_histogram(df["run_secs"], bin_minutes=10)
            if not h.empty:
                st.bar_chart(h, height=240, use_container_width=True)


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
        st.stop()

    df_series = load_series_results(sel_series["group_uuid"])
    if ag_compare != "All":
        df_series = df_series[df_series["age_group"] == ag_compare]
    df_series = df_series[df_series["year"].isin(selected_years)]

    if df_series.empty:
        st.info("No data for the selected filters.")
        st.stop()

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

    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

    # ── finish time overlay ───────────────────────────────────────────────────

    st.divider()
    st.subheader("Finish Time Distribution by Year")

    finish_mins = df_series[["year", "finish_secs"]].copy()
    finish_mins["mins"] = finish_mins["finish_secs"] / 60
    finish_mins = finish_mins.dropna(subset=["mins"])

    bin_size = 15
    lo = np.floor(finish_mins["mins"].min() / bin_size) * bin_size
    hi = np.ceil(finish_mins["mins"].max() / bin_size) * bin_size + bin_size
    shared_bins = np.arange(lo, hi, bin_size)

    def edge_label(e: float) -> str:
        h, m = divmod(int(e), 60)
        return f"{h}:{m:02d}"

    bin_labels = [f"{edge_label(shared_bins[i])} – {edge_label(shared_bins[i+1])}" for i in range(len(shared_bins) - 1)]

    overlay = pd.DataFrame(index=bin_labels)
    for year in sorted(selected_years):
        yr_mins = finish_mins[finish_mins["year"] == year]["mins"]
        counts, _ = np.histogram(yr_mins, bins=shared_bins)
        overlay[str(year)] = counts

    st.line_chart(overlay, height=320, use_container_width=True)

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
            pct_mins = (pct_vals / 60).rename(f"{pct_label} (min)")
            st.bar_chart(pct_mins, height=260, use_container_width=True)


# ══════════════════════════════════════════════════════════════════════════════
# TAB 3 — athlete search
# ══════════════════════════════════════════════════════════════════════════════

with tab3:
    st.subheader("Athlete Search")
    query = st.text_input("Search by name", placeholder="e.g. Smith, Jane")

    if not query:
        st.caption("Enter a name to search across all races in the database.")
        st.stop()

    matches = search_athletes(query)

    if matches.empty:
        st.warning(f'No athletes found matching "{query}".')
        st.stop()

    athlete_names = matches["athlete_name"].tolist()
    if len(athlete_names) == 1:
        selected_athlete = athlete_names[0]
    else:
        selected_athlete = st.selectbox(
            f"{len(athlete_names)} athletes found — select one",
            athlete_names,
        )

    st.divider()

    df_athlete = load_athlete_results(selected_athlete)

    if df_athlete.empty:
        st.info("No results found for this athlete.")
        st.stop()

    st.header(selected_athlete)

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
    st.dataframe(display, use_container_width=True, hide_index=True)

    if len(finishes) > 1:
        st.divider()
        st.subheader("Finish Time Over Time")
        chart_df = (
            finishes[["year", "finish_secs", "event_name"]]
            .sort_values("year")
            .assign(finish_mins=lambda d: d["finish_secs"] / 60)
            .set_index("year")[["finish_mins"]]
            .rename(columns={"finish_mins": "Finish (min)"})
        )
        st.line_chart(chart_df, height=260, use_container_width=True)
