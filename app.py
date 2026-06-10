"""
app.py — EIA 930 Load Growth Tracker

Streamlit app with an interactive map, quarterly/yearly line chart, and
sortable table showing annual growth of peak demand, total load, and
average demand vs a 2022-2023 baseline.

Usage:
    streamlit run app.py
"""

import math
import pandas as pd
import streamlit as st
import folium
from streamlit_folium import st_folium
import plotly.express as px
from pathlib import Path

METRICS_CSV = Path("inputs/growth_metrics.csv")

LEVEL_STYLES = {
    "most_aggregated": {
        "min_r": 10, "max_r": 40, "weight": 2,
        "label": "Most aggregated (EIA regions)",
    },
    "middle_aggregated": {
        "min_r": 6, "max_r": 30, "weight": 1.5,
        "label": "Balancing authorities",
    },
    "least_aggregated": {
        "min_r": 3, "max_r": 20, "weight": 1,
        "label": "Least aggregated (subregions + BAs)",
    },
}

LEVEL_ORDER = ["most_aggregated", "middle_aggregated", "least_aggregated"]


@st.cache_data
def load_data() -> pd.DataFrame:
    return pd.read_csv(METRICS_CSV)


def get_color(pct_value: float, vmin: float, vmax: float) -> str:
    """Diverging color based on % change. More intense = larger % swing."""
    abs_max = max(abs(vmin), abs(vmax))
    if abs_max == 0:
        return "#888888"

    normalized = max(-1, min(1, pct_value / abs_max))
    intensity = abs(normalized)

    if normalized >= 0:
        # White to green: more intense green = more growth
        r = int(230 - intensity * 170)
        g = int(230 - intensity * 50)
        b = int(230 - intensity * 170)
    else:
        # White to red: more intense red = more decline
        r = int(230 - intensity * 50)
        g = int(230 - intensity * 170)
        b = int(230 - intensity * 170)

    return f"#{r:02x}{g:02x}{b:02x}"


def get_radius(current_value: float, max_value: float, min_r: float = 5, max_r: float = 25) -> float:
    """Size = current demand/load, sqrt scaled."""
    if max_value == 0 or pd.isna(current_value):
        return min_r
    normalized = min(current_value / max_value, 1.0)
    return min_r + (max_r - min_r) * (normalized ** 0.5)


def build_map(
    df: pd.DataFrame,
    metric: str,
    level: str,
) -> folium.Map:
    m = folium.Map(location=[39.5, -98.35], zoom_start=4, tiles="cartodbpositron")
    style = LEVEL_STYLES[level]

    if metric == "Peak Demand":
        size_col = "peak_demand_gw_recent"
        pct_col = "peak_demand_growth_pct"
        abs_col = "peak_demand_growth_gw"
        size_unit = "GW"
    else:
        size_col = "total_load_twh_recent"
        pct_col = "total_load_growth_pct"
        abs_col = "total_load_growth_twh"
        size_unit = "TWh"

    plot_df = df.dropna(subset=[size_col, pct_col]).copy()
    if len(plot_df) == 0:
        return m

    max_value = plot_df[size_col].max()
    pct_min = plot_df[pct_col].min()
    pct_max = plot_df[pct_col].max()

    for _, row in plot_df.iterrows():
        color = get_color(row[pct_col], pct_min, pct_max)
        radius = get_radius(row[size_col], max_value, style["min_r"], style["max_r"])

        name = row["display_name_full"]
        entity = row["entity_id"]
        etype = row.get("entity_type", "")

        tooltip_html = f"""
        <div style="min-width:240px">
        <b>{name}</b> ({entity})<br>
        <i style="color:#666">{etype}</i>
        <hr style="margin:4px 0">
        <b>Peak Demand:</b> {row['peak_demand_gw_recent']:.1f} GW<br>
        <b>Peak Growth:</b> {row['peak_demand_growth_gw']:+.2f} GW ({row['peak_demand_growth_pct']:+.1f}%)<br>
        <hr style="margin:4px 0">
        <b>Total Load:</b> {row['total_load_twh_recent']:.1f} TWh<br>
        <b>Load Growth:</b> {row['total_load_growth_twh']:+.2f} TWh ({row['total_load_growth_pct']:+.1f}%)<br>
        </div>
        """

        folium.CircleMarker(
            location=[row["latitude"], row["longitude"]],
            radius=radius,
            color=color,
            fill=True,
            fill_color=color,
            fill_opacity=0.7,
            weight=style["weight"],
            tooltip=folium.Tooltip(tooltip_html),
        ).add_to(m)

    return m


# Quarter ordering for chart x-axis
QUARTER_ORDER = [
    "2022Q2", "2022Q3", "2022Q4",
    "2023Q1", "2023Q2", "2023Q3", "2023Q4",
    "2024Q1", "2024Q2", "2024Q3", "2024Q4",
    "2025Q1", "2025Q2", "2025Q3", "2025Q4",
    "2026Q1",
]

# Comparison quarters (skip baseline quarters for growth view)
COMPARISON_PAIRS = [
    ("2023Q2", "2022Q2"), ("2023Q3", "2022Q3"), ("2023Q4", "2022Q4"), ("2024Q1", "2023Q1"),
    ("2024Q2", "2022Q2"), ("2024Q3", "2022Q3"), ("2024Q4", "2022Q4"), ("2025Q1", "2023Q1"),
    ("2025Q2", "2022Q2"), ("2025Q3", "2022Q3"), ("2025Q4", "2022Q4"), ("2026Q1", "2023Q1"),
]

# Yearly aggregation: Q2-Q1 anchored years
YEARLY_PERIODS = {
    "2022-23": ["2022Q2", "2022Q3", "2022Q4", "2023Q1"],
    "2023-24": ["2023Q2", "2023Q3", "2023Q4", "2024Q1"],
    "2024-25": ["2024Q2", "2024Q3", "2024Q4", "2025Q1"],
    "2025-26": ["2025Q2", "2025Q3", "2025Q4", "2026Q1"],
}
YEAR_ORDER = ["2022-23", "2023-24", "2024-25", "2025-26"]


def _get_recent_growth_col(granularity: str, chart_metric: str) -> str | None:
    """Return the column name for the most recent growth vs. baseline metric."""
    if chart_metric == "Average Demand (GW)":
        return None  # no growth column for this metric
    if granularity == "Quarterly":
        suffix = "gw" if chart_metric == "Growth vs Baseline (GW)" else "pct"
        return f"load_growth_2026Q1_vs_2023Q1_{suffix}"
    else:
        return "avg_demand_2025-26_gw"  # used for computing yearly growth


def compute_yearly_averages(df: pd.DataFrame) -> pd.DataFrame:
    """Compute Q2-Q1 anchored yearly averages from quarterly data."""
    result = df[["entity_id", "display_name_full", "entity_type"]].copy()
    for year_label, quarters in YEARLY_PERIODS.items():
        cols = [f"avg_demand_{q}_gw" for q in quarters]
        existing = [c for c in cols if c in df.columns]
        if existing:
            result[f"avg_demand_{year_label}_gw"] = df[existing].mean(axis=1)
        else:
            result[f"avg_demand_{year_label}_gw"] = None
    return result


def build_yearly_chart(
    df: pd.DataFrame,
    metric_type: str,
    selected_entities: list[str],
) -> px.line:
    """Build a line chart of yearly average demand or growth vs baseline."""
    yearly_df = compute_yearly_averages(df)
    chart_df = yearly_df[yearly_df["entity_id"].isin(selected_entities)].copy()

    if metric_type == "Average Demand (GW)":
        avg_cols = [f"avg_demand_{y}_gw" for y in YEAR_ORDER]
        id_cols = ["entity_id", "display_name_full"]
        melted = chart_df[id_cols + avg_cols].melt(
            id_vars=id_cols, var_name="year_col", value_name="avg_demand_gw"
        )
        melted["year"] = melted["year_col"].str.replace("avg_demand_", "").str.replace("_gw", "")
        melted["year"] = pd.Categorical(melted["year"], categories=YEAR_ORDER, ordered=True)
        melted = melted.dropna(subset=["avg_demand_gw"])

        fig = px.line(
            melted.sort_values("year"),
            x="year",
            y="avg_demand_gw",
            color="display_name_full",
            labels={"avg_demand_gw": "Average Demand (GW)", "year": "Year", "display_name_full": ""},
            markers=True,
        )
    elif metric_type == "Growth vs Baseline (GW)":
        baseline_col = "avg_demand_2022-23_gw"
        id_cols = ["entity_id", "display_name_full"]
        growth_cols = [f"avg_demand_{y}_gw" for y in YEAR_ORDER]
        melted = chart_df[id_cols + growth_cols].melt(
            id_vars=id_cols, var_name="year_col", value_name="avg_gw"
        )
        melted["year"] = melted["year_col"].str.replace("avg_demand_", "").str.replace("_gw", "")
        melted["year"] = pd.Categorical(melted["year"], categories=YEAR_ORDER, ordered=True)
        # Compute growth vs baseline
        baseline_map = chart_df.set_index("entity_id")[baseline_col].to_dict()
        melted["baseline"] = melted["entity_id"].map(baseline_map)
        melted["growth_gw"] = melted["avg_gw"] - melted["baseline"]
        melted = melted.dropna(subset=["growth_gw"])

        fig = px.line(
            melted.sort_values("year"),
            x="year",
            y="growth_gw",
            color="display_name_full",
            labels={"growth_gw": "Growth vs Baseline (GW)", "year": "Year", "display_name_full": ""},
            markers=True,
        )
    else:
        baseline_col = "avg_demand_2022-23_gw"
        id_cols = ["entity_id", "display_name_full"]
        growth_cols = [f"avg_demand_{y}_gw" for y in YEAR_ORDER]
        melted = chart_df[id_cols + growth_cols].melt(
            id_vars=id_cols, var_name="year_col", value_name="avg_gw"
        )
        melted["year"] = melted["year_col"].str.replace("avg_demand_", "").str.replace("_gw", "")
        melted["year"] = pd.Categorical(melted["year"], categories=YEAR_ORDER, ordered=True)
        baseline_map = chart_df.set_index("entity_id")[baseline_col].to_dict()
        melted["baseline"] = melted["entity_id"].map(baseline_map)
        melted["growth_pct"] = (melted["avg_gw"] - melted["baseline"]) / melted["baseline"] * 100
        melted = melted.dropna(subset=["growth_pct"])

        fig = px.line(
            melted.sort_values("year"),
            x="year",
            y="growth_pct",
            color="display_name_full",
            labels={"growth_pct": "Growth vs Baseline (%)", "year": "Year", "display_name_full": ""},
            markers=True,
        )

    fig.update_layout(
        legend_title_text="",
        hovermode="x unified",
        height=550,
    )
    return fig


def build_line_chart(
    df: pd.DataFrame,
    metric_type: str,
    selected_entities: list[str],
) -> px.line:
    """
    Build a line chart of quarterly demand or growth vs baseline.

    metric_type: "Average Demand (GW)" or "Growth vs Baseline (%)"
    """
    chart_df = df[df["entity_id"].isin(selected_entities)].copy()

    if metric_type == "Average Demand (GW)":
        # Melt the quarterly average columns
        avg_cols = [f"avg_demand_{q}_gw" for q in QUARTER_ORDER]
        id_cols = ["entity_id", "display_name_full"]
        melted = chart_df[id_cols + avg_cols].melt(
            id_vars=id_cols, var_name="quarter_col", value_name="avg_demand_gw"
        )
        melted["quarter"] = melted["quarter_col"].str.replace("avg_demand_", "").str.replace("_gw", "")
        melted["quarter"] = pd.Categorical(melted["quarter"], categories=QUARTER_ORDER, ordered=True)
        melted = melted.dropna(subset=["avg_demand_gw"])

        fig = px.line(
            melted.sort_values("quarter"),
            x="quarter",
            y="avg_demand_gw",
            color="display_name_full",
            labels={"avg_demand_gw": "Average Demand (GW)", "quarter": "Quarter", "display_name_full": ""},
            markers=True,
        )
    elif metric_type == "Growth vs Baseline (GW)":
        growth_cols = [f"load_growth_{rq}_vs_{bq}_gw" for rq, bq in COMPARISON_PAIRS]
        growth_quarters = [rq for rq, _ in COMPARISON_PAIRS]

        id_cols = ["entity_id", "display_name_full"]
        melted = chart_df[id_cols + growth_cols].melt(
            id_vars=id_cols, var_name="quarter_col", value_name="growth_gw"
        )
        melted["quarter"] = melted["quarter_col"].str.extract(r"load_growth_(\d{4}Q\d)_vs")[0]
        melted["quarter"] = pd.Categorical(melted["quarter"], categories=growth_quarters, ordered=True)
        melted = melted.dropna(subset=["growth_gw"])

        fig = px.line(
            melted.sort_values("quarter"),
            x="quarter",
            y="growth_gw",
            color="display_name_full",
            labels={"growth_gw": "Growth vs Baseline (GW)", "quarter": "Quarter", "display_name_full": ""},
            markers=True,
        )
    else:
        # Growth vs baseline (%)
        growth_cols = [f"load_growth_{rq}_vs_{bq}_pct" for rq, bq in COMPARISON_PAIRS]
        growth_quarters = [rq for rq, _ in COMPARISON_PAIRS]

        id_cols = ["entity_id", "display_name_full"]
        melted = chart_df[id_cols + growth_cols].melt(
            id_vars=id_cols, var_name="quarter_col", value_name="growth_pct"
        )
        # Extract the comparison quarter from column name
        melted["quarter"] = melted["quarter_col"].str.extract(r"load_growth_(\d{4}Q\d)_vs")[0]
        melted["quarter"] = pd.Categorical(melted["quarter"], categories=growth_quarters, ordered=True)
        melted = melted.dropna(subset=["growth_pct"])

        fig = px.line(
            melted.sort_values("quarter"),
            x="quarter",
            y="growth_pct",
            color="display_name_full",
            labels={"growth_pct": "Growth vs Baseline (%)", "quarter": "Quarter", "display_name_full": ""},
            markers=True,
        )

    fig.update_layout(
        legend_title_text="",
        hovermode="x unified",
        height=550,
    )
    return fig


def main():
    st.set_page_config(page_title="EIA 930 Load Growth Tracker", layout="wide")

    st.title("EIA 930 Load Growth Tracker")
    st.caption(
        "Annual peak demand and total load growth (map), plus quarterly average demand "
        "and year-over-year growth vs 2022-2023 baseline (chart)."
    )

    df = load_data()

    st.sidebar.header("Controls")

    view = st.sidebar.radio("View", ["Map", "Growth Chart + Table"], index=1)

    level = st.sidebar.radio(
        "Aggregation Level",
        LEVEL_ORDER,
        format_func=lambda x: LEVEL_STYLES[x]["label"],
        index=2,
    )

    level_df = df[df["agg_level"] == level].copy()
    level_df["display_name_full"] = level_df.apply(_get_display_name, axis=1)

    if view == "Map":
        render_map_view(level_df, level)
    else:
        render_chart_view(level_df, level)


def render_map_view(level_df: pd.DataFrame, level: str):
    """Map view: bubble map with peak demand or total load growth."""
    metric = st.sidebar.radio(
        "Metric",
        ["Peak Demand", "Total Load"],
    )

    if metric == "Peak Demand":
        size_col = "peak_demand_gw_recent"
        pct_col = "peak_demand_growth_pct"
        abs_col = "peak_demand_growth_gw"
        size_unit = "GW"
    else:
        size_col = "total_load_twh_recent"
        pct_col = "total_load_growth_pct"
        abs_col = "total_load_growth_twh"
        size_unit = "TWh"

    valid = level_df.dropna(subset=[size_col, pct_col])

    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Entities", len(valid))
    col2.metric(f"Avg Growth (%)", f"{valid[pct_col].mean():+.1f}%")
    col3.metric(f"Total Current {size_unit}", f"{valid[size_col].sum():,.0f}")
    growing = (valid[pct_col] > 0).sum()
    col4.metric("Growing / Declining", f"{growing} / {len(valid) - growing}")

    st.subheader("Map")
    m = build_map(level_df, metric, level)
    st_folium(m, width=None, height=600, returned_objects=[])

    st.subheader("Top & Bottom Growth")
    col_a, col_b = st.columns(2)

    display_df = valid[["entity_id", "display_name_full", "entity_type", size_col, abs_col, pct_col]].copy()
    display_df.columns = ["ID", "Name", "Type", f"Current ({size_unit})", f"Growth ({size_unit})", "Growth (%)"]

    with col_a:
        st.markdown("**Top 10 Growing**")
        st.dataframe(
            display_df.nlargest(10, "Growth (%)").reset_index(drop=True),
            use_container_width=True,
        )

    with col_b:
        st.markdown("**Top 10 Declining**")
        st.dataframe(
            display_df.nsmallest(10, "Growth (%)").reset_index(drop=True),
            use_container_width=True,
        )

    with st.expander("Full Data Table"):
        st.dataframe(
            display_df.sort_values("Growth (%)", ascending=False).reset_index(drop=True),
            use_container_width=True,
        )


def _get_display_name(row):
    """Subregions get BA prefix (e.g. 'PJM - SCE'), others use plain name."""
    if row["entity_type"] == "subregion":
        ba = row.get("ba_code", "")
        if pd.notna(ba) and ba:
            return f"{ba} - {row['display_name']}"
    return row["display_name"]



def render_chart_view(level_df: pd.DataFrame, level: str):
    """Combined chart + table view: quarterly or yearly, with sortable table."""
    granularity = st.sidebar.radio("Granularity", ["Quarterly", "Yearly"], index=0)

    chart_metric = st.sidebar.radio(
        "Chart Metric",
        ["Average Demand (GW)", "Growth vs Baseline (GW)", "Growth vs Baseline (%)"],
        index=1,
    )

    # Get entities with valid data
    if granularity == "Quarterly":
        if chart_metric == "Average Demand (GW)":
            valid = level_df.dropna(subset=["avg_demand_2022Q2_gw"])
        elif chart_metric == "Growth vs Baseline (GW)":
            valid = level_df.dropna(subset=["load_growth_2023Q2_vs_2022Q2_gw"])
        else:
            valid = level_df.dropna(subset=["load_growth_2023Q2_vs_2022Q2_pct"])
    else:
        valid = level_df.dropna(subset=["avg_demand_2022Q2_gw"])

    all_names = valid["display_name_full"].tolist()
    name_to_id = dict(zip(valid["display_name_full"], valid["entity_id"]))
    valid_ids = set(valid["entity_id"].tolist())

    # Compute default top-3 by highest magnitude on most recent growth metric
    growth_col = _get_recent_growth_col(granularity, chart_metric)
    if growth_col and growth_col in valid.columns:
        ranked = valid.dropna(subset=[growth_col]).copy()
        ranked["_abs"] = ranked[growth_col].abs()
        default_ids = set(ranked.nlargest(3, "_abs")["entity_id"].tolist())
    else:
        default_ids = set(valid.head(3)["entity_id"].tolist())

    # Initialize on first run
    if "selected_ids" not in st.session_state:
        st.session_state.selected_ids = default_ids

    # Apply pending toggle from table checkbox (must happen before widget creation)
    id_to_name = {v: k for k, v in name_to_id.items()}
    pending = st.session_state.pop("_pending_toggle_ids", None)
    if pending is not None:
        st.session_state.selected_ids = pending
        st.session_state["geo_select"] = [id_to_name[eid] for eid in pending if eid in id_to_name]
    elif "geo_select" not in st.session_state:
        st.session_state["geo_select"] = [id_to_name[eid] for eid in st.session_state.selected_ids if eid in id_to_name]

    # Persist geographies: silently drop entities not valid at this level/granularity/metric
    selected_ids = st.session_state.selected_ids & valid_ids

    # Sidebar multiselect (Streamlit auto-drops invalid options)
    selected_names = st.sidebar.multiselect(
        "Geographies",
        options=all_names,
        key="geo_select",
    )

    # Sync multiselect → session state
    new_ids = set(name_to_id[n] for n in selected_names if n in name_to_id)
    if new_ids != selected_ids:
        st.session_state.selected_ids = new_ids
        selected_ids = new_ids

    # Summary stats
    sel_df = valid[valid["entity_id"].isin(selected_ids)]
    if granularity == "Quarterly":
        _render_quarterly_summary(sel_df, chart_metric)
        fig = build_line_chart(level_df, chart_metric, list(selected_ids))
    else:
        _render_yearly_summary(sel_df, chart_metric)
        fig = build_yearly_chart(level_df, chart_metric, list(selected_ids))

    # Dynamic chart title (immediately above the graph)
    unit = "GW" if chart_metric == "Growth vs Baseline (GW)" else "%" if chart_metric == "Growth vs Baseline (%)" else "GW"
    gran_label = "Quarter" if granularity == "Quarterly" else "Year"
    st.markdown(
        f"<h3 style='text-align: center;'>Load Growth ({unit}) Since April 2022–March 2023, by {gran_label}</h3>",
        unsafe_allow_html=True,
    )

    st.plotly_chart(fig, use_container_width=True)

    # Full sortable table
    _render_full_table(level_df, granularity, chart_metric)


def _render_quarterly_summary(sel_df: pd.DataFrame, chart_metric: str):
    """Summary metrics for quarterly chart view."""
    if chart_metric == "Average Demand (GW)":
        st.subheader("Quarterly Average Demand")
        latest_q = "avg_demand_2026Q1_gw"
        if latest_q in sel_df.columns:
            col1, col2 = st.columns(2)
            col1.metric("Entities", len(sel_df))
            col2.metric(
                "Avg Demand (2026Q1)",
                f"{sel_df[latest_q].mean():.1f} GW",
            )
    elif chart_metric == "Growth vs Baseline (GW)":
        st.subheader("Growth vs Baseline (Absolute)")
        latest_growth = "load_growth_2026Q1_vs_2023Q1_gw"
        if latest_growth in sel_df.columns:
            valid_growth = sel_df.dropna(subset=[latest_growth])
            col1, col2, col3 = st.columns(3)
            col1.metric("Entities", len(valid_growth))
            col2.metric(
                "Avg Growth (2026Q1 vs 2023Q1)",
                f"{valid_growth[latest_growth].mean():+.2f} GW",
            )
            growing = (valid_growth[latest_growth] > 0).sum()
            col3.metric("Growing / Declining", f"{growing} / {len(valid_growth) - growing}")
    else:
        st.subheader("Growth vs Baseline (%)")
        latest_growth = "load_growth_2026Q1_vs_2023Q1_pct"
        if latest_growth in sel_df.columns:
            valid_growth = sel_df.dropna(subset=[latest_growth])
            col1, col2, col3 = st.columns(3)
            col1.metric("Entities", len(valid_growth))
            col2.metric(
                "Avg Growth (2026Q1 vs 2023Q1)",
                f"{valid_growth[latest_growth].mean():+.1f}%",
            )
            growing = (valid_growth[latest_growth] > 0).sum()
            col3.metric("Growing / Declining", f"{growing} / {len(valid_growth) - growing}")


def _render_yearly_summary(sel_df: pd.DataFrame, chart_metric: str):
    """Summary metrics for yearly chart view."""
    yearly_df = compute_yearly_averages(sel_df)

    if chart_metric == "Average Demand (GW)":
        st.subheader("Yearly Average Demand")
        latest_y = "avg_demand_2025-26_gw"
        if latest_y in yearly_df.columns:
            col1, col2 = st.columns(2)
            col1.metric("Entities", len(yearly_df))
            col2.metric(
                "Avg Demand (2025-26)",
                f"{yearly_df[latest_y].mean():.1f} GW",
            )
    elif chart_metric == "Growth vs Baseline (GW)":
        st.subheader("Growth vs Baseline (Absolute)")
        baseline = yearly_df["avg_demand_2022-23_gw"]
        latest = yearly_df["avg_demand_2025-26_gw"]
        growth = latest - baseline
        valid_growth = growth.dropna()
        col1, col2, col3 = st.columns(3)
        col1.metric("Entities", len(valid_growth))
        col2.metric(
            "Avg Growth (2025-26 vs 2022-23)",
            f"{valid_growth.mean():+.2f} GW",
        )
        growing = (valid_growth > 0).sum()
        col3.metric("Growing / Declining", f"{growing} / {len(valid_growth) - growing}")
    else:
        st.subheader("Growth vs Baseline (%)")
        baseline = yearly_df["avg_demand_2022-23_gw"]
        latest = yearly_df["avg_demand_2025-26_gw"]
        growth_pct = (latest - baseline) / baseline * 100
        valid_growth = growth_pct.dropna()
        col1, col2, col3 = st.columns(3)
        col1.metric("Entities", len(valid_growth))
        col2.metric(
            "Avg Growth (2025-26 vs 2022-23)",
            f"{valid_growth.mean():+.1f}%",
        )
        growing = (valid_growth > 0).sum()
        col3.metric("Growing / Declining", f"{growing} / {len(valid_growth) - growing}")


def _fmt(val):
    """Truncate to 2 decimal places, strip trailing zeroes."""
    if pd.isna(val):
        return ""
    truncated = math.trunc(val * 100) / 100
    return f"{truncated:.2f}".rstrip("0").rstrip(".")


def _render_full_table(
    level_df: pd.DataFrame,
    granularity: str,
    chart_metric: str,
):
    """Full sortable table showing all geographies and their metrics."""
    st.subheader("All Geographies")
    st.caption("Click column headers to sort.")

    base_cols = ["entity_id", "display_name_full", "entity_type"]
    col_rename = {"entity_id": "ID", "display_name_full": "Name", "entity_type": "Type"}

    # Parent column: subregion → parent BA, BA → itself, region → N/A
    def get_parent(row):
        if row["entity_type"] == "region":
            return "N/A"
        elif row["entity_type"] == "ba":
            return row["entity_id"]
        else:
            ba = row.get("ba_code", "")
            return ba if pd.notna(ba) and ba else "N/A"

    yearly_df = None

    if granularity == "Quarterly":
        table_df = level_df[base_cols].copy()
        table_df["parent"] = level_df.apply(get_parent, axis=1)
        if chart_metric == "Average Demand (GW)":
            data_cols = [f"avg_demand_{q}_gw" for q in QUARTER_ORDER]
            data_rename = {c: c.replace("avg_demand_", "").replace("_gw", "") for c in data_cols}
            for c in data_cols:
                table_df[c] = level_df[c]
        elif chart_metric == "Growth vs Baseline (GW)":
            data_cols = [f"load_growth_{rq}_vs_{bq}_gw" for rq, bq in COMPARISON_PAIRS]
            data_rename = {c: c.replace("load_growth_", "").replace("_gw", "").replace("_vs_", " vs ") for c in data_cols}
            for c in data_cols:
                table_df[c] = level_df[c]
        else:
            data_cols = [f"load_growth_{rq}_vs_{bq}_pct" for rq, bq in COMPARISON_PAIRS]
            data_rename = {c: c.replace("load_growth_", "").replace("_pct", "%").replace("_vs_", " vs ") for c in data_cols}
            for c in data_cols:
                table_df[c] = level_df[c]
    else:
        yearly_df = compute_yearly_averages(level_df)
        table_df = yearly_df[base_cols].copy()
        table_df["parent"] = level_df.apply(get_parent, axis=1)
        if chart_metric == "Average Demand (GW)":
            data_cols = [f"avg_demand_{y}_gw" for y in YEAR_ORDER]
            data_rename = {c: c.replace("avg_demand_", "").replace("_gw", "") for c in data_cols}
            for c in data_cols:
                table_df[c] = yearly_df[c]
        else:
            baseline = yearly_df["avg_demand_2022-23_gw"]
            data_cols = []
            data_rename = {}
            for y in YEAR_ORDER:
                if y == "2022-23":
                    continue
                avg_col = f"avg_demand_{y}_gw"
                out_col = f"growth_{y}_vs_2022-23"
                data_cols.append(out_col)
                data_rename[out_col] = f"{y} vs 2022-23"
                if chart_metric == "Growth vs Baseline (GW)":
                    table_df[out_col] = yearly_df[avg_col] - baseline
                else:
                    table_df[out_col] = (yearly_df[avg_col] - baseline) / baseline * 100

    # Column order: ID, Name, Type, Parent, then data columns
    display_cols = base_cols + ["parent"] + data_cols
    display_rename = {**col_rename, "parent": "Parent", **data_rename}
    display_df = table_df[display_cols].rename(columns=display_rename).reset_index(drop=True)

    # Sort by most recent column descending
    if data_cols:
        last_col = display_rename.get(data_cols[-1], data_cols[-1])
        display_df = display_df.sort_values(last_col, ascending=False, na_position="last").reset_index(drop=True)

    st.dataframe(display_df, use_container_width=True, height=600, hide_index=True)


if __name__ == "__main__":
    main()
