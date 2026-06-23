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
tab1, tab2 = st.tabs(["Race Results", "Year-over-Year Comparison"])


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

with tab2:
    st.subheader("Select a Series")
    ctrl_a, ctrl_b, ctrl_c = st.columns(3)

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
        rows.append({
            "Year": year,
            "Finishers": n_fin,
            "DNF": n_dnf,
            "DNF %": dnf_pct,
            "Median Finish": fmt_time(yr_fin["finish_secs"].median()),
            "Median Swim":   fmt_time(yr_fin["swim_secs"].median()),
            "Median Bike":   fmt_time(yr_fin["bike_secs"].median()),
            "Median Run":    fmt_time(yr_fin["run_secs"].median()),
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
    st.subheader("Median Segment Times by Year")

    segments = [("Swim", "swim_secs"), ("Bike", "bike_secs"), ("Run", "run_secs")]
    col_charts = st.columns(len(segments))

    for col, (label, col_secs) in zip(col_charts, segments):
        with col:
            st.markdown(f"**{label}**")
            medians = (
                df_series.groupby("year")[col_secs]
                .median()
                .loc[lambda s: s.index.isin(selected_years)]
                .sort_index()
            )
            median_mins = (medians / 60).rename("Median (min)")
            st.bar_chart(median_mins, height=260, use_container_width=True)
