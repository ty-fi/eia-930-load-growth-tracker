"""
process_data.py — Aggregate EIA 930 hourly demand into growth metrics.

Reads three PUDL parquet files (subregion, BA, regional), calculates rolling
12-month peak demand and total load, computes year-over-year growth, and
writes a unified CSV with two aggregation levels:
  - most_aggregated: regions + BAs not covered by any region (one dot per area)
  - least_aggregated: subregions + BAs without subregion data (most granular)

Usage:
    python process_data.py
"""

import pandas as pd
from pathlib import Path

INPUT_SUBREGION = Path("inputs/out_eia930__hourly_subregion_demand.parquet")
INPUT_OPERATIONS = Path("inputs/out_eia930__hourly_operations.parquet")
INPUT_AGGREGATED = Path("inputs/out_eia930__hourly_aggregated_demand.parquet")
INPUT_SUBREGION_CENTROIDS = Path("inputs/ba_subregion_centroids.csv")
INPUT_BA_CENTROIDS = Path("inputs/ba_centroids.csv")
INPUT_REGION_CENTROIDS = Path("inputs/region_centroids.csv")
INPUT_BA_REGION_MAP = Path("inputs/ba_region_mapping.csv")
OUTPUT_CSV = Path("inputs/growth_metrics.csv")

MIN_HOURS_FOR_COMPLETE_MONTH = 672  # 28 days * 24 hours

# Quarter assignment: month -> quarter label
# Year anchored on Q2: "year" runs Q2 through Q1
QUARTER_MONTH_MAP = {
    1: "Q1", 2: "Q2", 3: "Q3", 4: "Q4",
    5: "Q2", 6: "Q2", 7: "Q3", 8: "Q3",
    9: "Q4", 10: "Q4", 11: "Q1", 12: "Q1",
}

# Comparison quarters: (recent, baseline)
# Each recent quarter matched against same-season baseline
COMPARISON_QUARTERS = [
    ("2023Q2", "2022Q2"), ("2023Q3", "2022Q3"), ("2023Q4", "2022Q4"), ("2024Q1", "2023Q1"),
    ("2024Q2", "2022Q2"), ("2024Q3", "2022Q3"), ("2024Q4", "2022Q4"), ("2025Q1", "2023Q1"),
    ("2025Q2", "2022Q2"), ("2025Q3", "2022Q3"), ("2025Q4", "2022Q4"), ("2026Q1", "2023Q1"),
]

# All quarters that appear in baseline or comparison
ALL_QUARTERS = sorted(set(
    [b for _, b in COMPARISON_QUARTERS] + [r for r, _ in COMPARISON_QUARTERS]
))


def load_subregion_demand() -> pd.DataFrame:
    """Load subregion-level demand (10 BAs, 90 subregions)."""
    print("Loading subregion demand...")
    df = pd.read_parquet(INPUT_SUBREGION)
    df["demand_mwh"] = df["demand_imputed_pudl_mwh"].fillna(df["demand_reported_mwh"])
    df = df.dropna(subset=["demand_mwh"])
    df["entity_id"] = df["balancing_authority_subregion_code_eia"]
    df["ba_code"] = df["balancing_authority_code_eia"]
    print(f"  {len(df):,} rows, {df['entity_id'].nunique()} subregions")
    return df[["datetime_utc", "ba_code", "entity_id", "demand_mwh"]]


def load_ba_demand() -> pd.DataFrame:
    """Load BA-level demand (70 BAs)."""
    print("Loading BA-level demand...")
    df = pd.read_parquet(INPUT_OPERATIONS)
    df["demand_mwh"] = df["demand_imputed_pudl_mwh"].fillna(df["demand_reported_mwh"])
    df = df.dropna(subset=["demand_mwh"])
    df["entity_id"] = df["balancing_authority_code_eia"]
    df["ba_code"] = df["balancing_authority_code_eia"]
    print(f"  {len(df):,} rows, {df['entity_id'].nunique()} BAs")
    return df[["datetime_utc", "ba_code", "entity_id", "demand_mwh"]]


def load_region_demand() -> pd.DataFrame:
    """Load regional demand (13 regions)."""
    print("Loading regional demand...")
    df = pd.read_parquet(INPUT_AGGREGATED)
    df = df[df["aggregation_level"] == "region"].copy()
    df["demand_mwh"] = df["demand_imputed_pudl_mwh"]
    df = df.dropna(subset=["demand_mwh"])
    df["entity_id"] = df["aggregation_group"]
    df["ba_code"] = df["aggregation_group"]
    print(f"  {len(df):,} rows, {df['entity_id'].nunique()} regions")
    return df[["datetime_utc", "ba_code", "entity_id", "demand_mwh"]]


def filter_complete_months(df: pd.DataFrame, group_cols: list[str]) -> pd.DataFrame:
    """Keep only rows belonging to complete calendar months."""
    df = df.copy()
    df["year"] = df["datetime_utc"].dt.year
    df["month"] = df["datetime_utc"].dt.month

    hours_per_month = (
        df.groupby(group_cols + ["year", "month"])["datetime_utc"]
        .nunique()
        .reset_index(name="n_hours")
    )

    complete = hours_per_month[hours_per_month["n_hours"] >= MIN_HOURS_FOR_COMPLETE_MONTH][
        group_cols + ["year", "month"]
    ]

    before = len(df)
    df = df.merge(complete, on=group_cols + ["year", "month"], how="inner")
    print(f"  Filtered to complete months: {before:,} -> {len(df):,} rows")
    df = df.drop(columns=["year", "month"])
    return df


def assign_quarters(df: pd.DataFrame) -> pd.DataFrame:
    """Add year_label and quarter columns based on month."""
    df = df.copy()
    df["quarter"] = df["datetime_utc"].dt.month.map(QUARTER_MONTH_MAP)
    # Year label: Q2-Q1 anchored. Months 1-4 belong to the year of the preceding Q2.
    df["year_label"] = df["datetime_utc"].dt.year
    # Q1 (Jan-Mar): year_label = current year (belongs to year starting prior Q2)
    # Q2 (Apr-Jun): year_label = current year
    # Q3 (Jul-Sep): year_label = current year
    # Q4 (Oct-Dec): year_label = current year
    # The "anchor year" for a quarter is the year of its Q2.
    # So 2022Q2=2022, 2022Q3=2022, 2022Q4=2022, 2023Q1=2023 (starts 2022's Q2-year)
    # This is naturally the month's year for Q2-Q4, and the month's year for Q1 too.
    df["quarter_label"] = df["year_label"].astype(str) + df["quarter"]
    return df


def compute_quarterly_stats(df: pd.DataFrame) -> pd.DataFrame:
    """Compute average hourly demand (GW) per entity per quarter."""
    df = assign_quarters(df)
    stats = (
        df.groupby(["entity_id", "quarter_label"])["demand_mwh"]
        .mean()
        .reset_index()
    )
    stats["avg_demand_gw"] = stats["demand_mwh"] / 1000
    stats = stats.drop(columns=["demand_mwh"])
    return stats


def compute_quarterly_growth(
    quarterly_stats: pd.DataFrame,
    growth_df: pd.DataFrame,
) -> pd.DataFrame:
    """
    Merge quarterly averages into growth_df and compute YoY change
    for each comparison quarter vs its baseline quarter.
    """
    # Pivot to wide: one column per quarter
    pivoted = quarterly_stats.pivot(
        index="entity_id", columns="quarter_label", values="avg_demand_gw"
    ).reset_index()
    pivoted.columns.name = None

    # Rename quarter columns to avg_demand_{quarter}_gw
    rename_map = {q: f"avg_demand_{q}_gw" for q in pivoted.columns if q != "entity_id"}
    pivoted = pivoted.rename(columns=rename_map)

    # Merge into growth_df
    result = growth_df.merge(pivoted, on="entity_id", how="left")

    # Compute growth for each comparison quarter
    for recent_q, baseline_q in COMPARISON_QUARTERS:
        recent_col = f"avg_demand_{recent_q}_gw"
        baseline_col = f"avg_demand_{baseline_q}_gw"
        abs_col = f"load_growth_{recent_q}_vs_{baseline_q}_gw"
        pct_col = f"load_growth_{recent_q}_vs_{baseline_q}_pct"

        if recent_col in result.columns and baseline_col in result.columns:
            result[abs_col] = result[recent_col] - result[baseline_col]
            result[pct_col] = (
                (result[recent_col] - result[baseline_col])
                / result[baseline_col]
                * 100
            )
        else:
            result[abs_col] = None
            result[pct_col] = None

    return result


def compute_windows(df: pd.DataFrame) -> tuple[pd.Timestamp, pd.Timestamp, pd.Timestamp]:
    """Determine the rolling 12-month comparison windows."""
    latest = df["datetime_utc"].max()
    recent_end = latest.normalize()
    recent_start = recent_end - pd.DateOffset(years=1)
    prior_start = recent_start - pd.DateOffset(years=1)

    print(f"  Latest observation: {latest}")
    print(f"  Recent year window: {recent_start.date()} -> {recent_end.date()}")
    print(f"  Prior year window:  {prior_start.date()} -> {recent_start.date()}")

    return recent_start, recent_end, prior_start


def compute_growth(
    df: pd.DataFrame,
    recent_start: pd.Timestamp,
    recent_end: pd.Timestamp,
    prior_start: pd.Timestamp,
    group_col: str,
) -> pd.DataFrame:
    """Compute peak demand and total load growth for each entity."""
    key = [group_col]

    def window_stats(start, end, label):
        mask = (df["datetime_utc"] >= start) & (df["datetime_utc"] < end)
        subset = df.loc[mask]
        stats = subset.groupby(key)["demand_mwh"].agg(["max", "sum"]).reset_index()
        stats.columns = key + [f"peak_demand_mwh_{label}", f"total_load_mwh_{label}"]
        print(f"  {label} window: {len(subset):,} rows, {stats.shape[0]} entities")
        return stats

    recent = window_stats(recent_start, recent_end, "recent")
    prior = window_stats(prior_start, recent_start, "prior")

    merged = prior.merge(recent, on=key, how="outer")

    merged["peak_demand_gw_recent"] = merged["peak_demand_mwh_recent"] / 1000
    merged["peak_demand_gw_prior"] = merged["peak_demand_mwh_prior"] / 1000
    merged["total_load_twh_recent"] = merged["total_load_mwh_recent"] / 1e6
    merged["total_load_twh_prior"] = merged["total_load_mwh_prior"] / 1e6

    merged["peak_demand_growth_gw"] = merged["peak_demand_gw_recent"] - merged["peak_demand_gw_prior"]
    merged["peak_demand_growth_pct"] = (
        (merged["peak_demand_mwh_recent"] - merged["peak_demand_mwh_prior"])
        / merged["peak_demand_mwh_prior"]
        * 100
    )
    merged["total_load_growth_twh"] = merged["total_load_twh_recent"] - merged["total_load_twh_prior"]
    merged["total_load_growth_pct"] = (
        (merged["total_load_mwh_recent"] - merged["total_load_mwh_prior"])
        / merged["total_load_mwh_prior"]
        * 100
    )

    return merged


GROWTH_COLS = [
    "peak_demand_gw_recent",
    "peak_demand_gw_prior",
    "peak_demand_growth_gw",
    "peak_demand_growth_pct",
    "total_load_twh_recent",
    "total_load_twh_prior",
    "total_load_growth_twh",
    "total_load_growth_pct",
]

# Quarterly columns: avg demand per quarter + YoY growth vs baseline
QUARTERLY_GROWTH_COLS = []
for recent_q, baseline_q in COMPARISON_QUARTERS:
    QUARTERLY_GROWTH_COLS.append(f"avg_demand_{recent_q}_gw")
    QUARTERLY_GROWTH_COLS.append(f"load_growth_{recent_q}_vs_{baseline_q}_gw")
    QUARTERLY_GROWTH_COLS.append(f"load_growth_{recent_q}_vs_{baseline_q}_pct")
for q in ALL_QUARTERS:
    col = f"avg_demand_{q}_gw"
    if col not in QUARTERLY_GROWTH_COLS:
        QUARTERLY_GROWTH_COLS.append(col)

ALL_GROWTH_COLS = GROWTH_COLS + QUARTERLY_GROWTH_COLS


def build_most_aggregated(
    region_growth: pd.DataFrame,
    ba_growth: pd.DataFrame,
    ba_region_map: dict[str, str],
    region_centroids: pd.DataFrame,
    ba_centroids: pd.DataFrame,
) -> pd.DataFrame:
    """
    Most aggregated view: one dot per area, coarsest available.
    - Show regions (13 dots) for areas covered by regions
    - Show BAs for areas NOT covered by any region
    """
    # Find BAs not mapped to any region
    mapped_bas = set(ba_region_map.keys())
    all_bas = set(ba_growth["entity_id"].tolist())
    unmapped_bas = all_bas - mapped_bas

    rows = []

    # Add regions
    for _, r in region_growth.iterrows():
        rc = r["entity_id"]
        centroid = region_centroids[region_centroids["region_code"] == rc]
        rows.append({
            "agg_level": "most_aggregated",
            "entity_id": rc,
            "display_name": centroid["region_name"].values[0] if len(centroid) > 0 else rc,
            "entity_type": "region",
            "latitude": centroid["latitude"].values[0] if len(centroid) > 0 else None,
            "longitude": centroid["longitude"].values[0] if len(centroid) > 0 else None,
            **{col: r[col] for col in ALL_GROWTH_COLS},
        })

    # Add unmapped BAs
    for ba_id in sorted(unmapped_bas):
        ba_row = ba_growth[ba_growth["entity_id"] == ba_id]
        if len(ba_row) == 0:
            continue
        r = ba_row.iloc[0]
        centroid = ba_centroids[ba_centroids["ba_code"] == ba_id]
        rows.append({
            "agg_level": "most_aggregated",
            "entity_id": ba_id,
            "display_name": centroid["ba_name"].values[0] if len(centroid) > 0 else ba_id,
            "entity_type": "ba",
            "latitude": centroid["latitude"].values[0] if len(centroid) > 0 else None,
            "longitude": centroid["longitude"].values[0] if len(centroid) > 0 else None,
            **{col: r[col] for col in ALL_GROWTH_COLS},
        })

    return pd.DataFrame(rows)


def build_middle_aggregated(
    ba_growth: pd.DataFrame,
    ba_centroids: pd.DataFrame,
) -> pd.DataFrame:
    """
    Middle aggregation: all BAs (70 dots), one per balancing authority.
    """
    rows = []
    for _, r in ba_growth.iterrows():
        ba_id = r["entity_id"]
        centroid = ba_centroids[ba_centroids["ba_code"] == ba_id]
        rows.append({
            "agg_level": "middle_aggregated",
            "entity_id": ba_id,
            "display_name": centroid["ba_name"].values[0] if len(centroid) > 0 else ba_id,
            "entity_type": "ba",
            "ba_code": ba_id,
            "latitude": centroid["latitude"].values[0] if len(centroid) > 0 else None,
            "longitude": centroid["longitude"].values[0] if len(centroid) > 0 else None,
            **{col: r[col] for col in ALL_GROWTH_COLS},
        })
    return pd.DataFrame(rows)


def build_least_aggregated(
    subregion_growth: pd.DataFrame,
    ba_growth: pd.DataFrame,
    ba_region_map: dict[str, str],
    subregion_centroids: pd.DataFrame,
    ba_centroids: pd.DataFrame,
) -> pd.DataFrame:
    """
    Least aggregated view: most granular available per area.
    - Show subregions (86 dots) for BAs that have subregion data
    - Show BAs for BAs that don't have subregion data
    """
    # BAs that have subregion data
    bas_with_subregions = set(subregion_growth["entity_id"].str[0:4].unique())
    # Actually, we need to find which BAs have subregions from the original data
    # The subregion entity_ids are the subregion codes (PGAE, SCE, etc.)
    # We need to find which BA codes have subregion data

    # Get the BA codes that have subregion data from the centroids file
    sub_centroids = pd.read_csv(INPUT_SUBREGION_CENTROIDS)
    bas_with_sub_data = set(sub_centroids["ba_code"].unique())

    rows = []

    # Add subregions (for BAs that have subregion data)
    for _, r in subregion_growth.iterrows():
        entity = r["entity_id"]
        centroid = subregion_centroids[subregion_centroids["subregion_code"] == entity]
        ba_code = centroid["ba_code"].values[0] if len(centroid) > 0 else ""
        rows.append({
            "agg_level": "least_aggregated",
            "entity_id": entity,
            "display_name": centroid["subregion_name"].values[0] if len(centroid) > 0 else entity,
            "entity_type": "subregion",
            "ba_code": ba_code,
            "latitude": centroid["latitude"].values[0] if len(centroid) > 0 else None,
            "longitude": centroid["longitude"].values[0] if len(centroid) > 0 else None,
            **{col: r[col] for col in ALL_GROWTH_COLS},
        })

    # Add BAs that don't have subregion data
    for _, r in ba_growth.iterrows():
        ba_id = r["entity_id"]
        if ba_id in bas_with_sub_data:
            continue  # already represented by subregions
        centroid = ba_centroids[ba_centroids["ba_code"] == ba_id]
        rows.append({
            "agg_level": "least_aggregated",
            "entity_id": ba_id,
            "display_name": centroid["ba_name"].values[0] if len(centroid) > 0 else ba_id,
            "entity_type": "ba",
            "ba_code": ba_id,
            "latitude": centroid["latitude"].values[0] if len(centroid) > 0 else None,
            "longitude": centroid["longitude"].values[0] if len(centroid) > 0 else None,
            **{col: r[col] for col in ALL_GROWTH_COLS},
        })

    return pd.DataFrame(rows)


def main():
    print("=" * 60)
    print("EIA 930 Load Growth Data Processing")
    print("=" * 60)

    # Load all three sources
    sub_df = load_subregion_demand()
    ba_df = load_ba_demand()
    reg_df = load_region_demand()

    # Filter to complete months
    print("\nFiltering to complete months...")
    sub_df = filter_complete_months(sub_df, ["ba_code", "entity_id"])
    ba_df = filter_complete_months(ba_df, ["ba_code", "entity_id"])
    reg_df = filter_complete_months(reg_df, ["ba_code", "entity_id"])

    # Compute windows
    print("\nDetermining rolling 12-month windows...")
    recent_start, recent_end, prior_start = compute_windows(ba_df)

    # Compute growth for each raw level
    print("\n--- Subregion growth ---")
    sub_growth = compute_growth(sub_df, recent_start, recent_end, prior_start, "entity_id")

    print("\n--- BA growth ---")
    ba_growth = compute_growth(ba_df, recent_start, recent_end, prior_start, "entity_id")

    print("\n--- Region growth ---")
    reg_growth = compute_growth(reg_df, recent_start, recent_end, prior_start, "entity_id")

    # Compute quarterly averages and YoY growth vs baseline
    print("\n--- Quarterly subregion stats ---")
    sub_qstats = compute_quarterly_stats(sub_df)
    print(f"  {len(sub_qstats)} entity-quarter combos, {sub_qstats['entity_id'].nunique()} entities")

    print("--- Quarterly BA stats ---")
    ba_qstats = compute_quarterly_stats(ba_df)
    print(f"  {len(ba_qstats)} entity-quarter combos, {ba_qstats['entity_id'].nunique()} entities")

    print("--- Quarterly region stats ---")
    reg_qstats = compute_quarterly_stats(reg_df)
    print(f"  {len(reg_qstats)} entity-quarter combos, {reg_qstats['entity_id'].nunique()} entities")

    print("\nComputing quarterly YoY growth...")
    sub_growth = compute_quarterly_growth(sub_qstats, sub_growth)
    ba_growth = compute_quarterly_growth(ba_qstats, ba_growth)
    reg_growth = compute_quarterly_growth(reg_qstats, reg_growth)

    # Load reference data
    ba_region_map_df = pd.read_csv(INPUT_BA_REGION_MAP)
    ba_region_map = dict(zip(ba_region_map_df["ba_code"], ba_region_map_df["region_code"]))

    sub_centroids = pd.read_csv(INPUT_SUBREGION_CENTROIDS)
    ba_centroids = pd.read_csv(INPUT_BA_CENTROIDS)
    region_centroids = pd.read_csv(INPUT_REGION_CENTROIDS)

    # Build aggregated levels
    print("\nBuilding most_aggregated level...")
    most = build_most_aggregated(reg_growth, ba_growth, ba_region_map, region_centroids, ba_centroids)
    print(f"  {len(most)} dots ({len(most[most['entity_type']=='region'])} regions + {len(most[most['entity_type']=='ba'])} unmapped BAs)")

    print("\nBuilding middle_aggregated level...")
    middle = build_middle_aggregated(ba_growth, ba_centroids)
    print(f"  {len(middle)} dots (all BAs)")

    print("\nBuilding least_aggregated level...")
    least = build_least_aggregated(sub_growth, ba_growth, ba_region_map, sub_centroids, ba_centroids)
    print(f"  {len(least)} dots ({len(least[least['entity_type']=='subregion'])} subregions + {len(least[least['entity_type']=='ba'])} BAs)")

    # Combine and save
    result = pd.concat([most, middle, least], ignore_index=True)
    result.to_csv(OUTPUT_CSV, index=False)
    print(f"\nSaved {len(result)} rows to {OUTPUT_CSV}")

    # Summary
    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)
    for level in ["most_aggregated", "middle_aggregated", "least_aggregated"]:
        subset = result[result["agg_level"] == level]
        valid = subset.dropna(subset=["peak_demand_growth_gw"])
        print(f"\n{level} ({len(valid)} with growth data):")
        if len(valid) > 0:
            print(f"  Peak Demand Growth: {valid['peak_demand_growth_gw'].mean():+.2f} GW mean")
            print(f"  Total Load Growth:  {valid['total_load_growth_twh'].mean():+.2f} TWh mean")

    print(f"\nWindow: {recent_start.date()} -> {recent_end.date()} vs {prior_start.date()} -> {recent_start.date()}")


if __name__ == "__main__":
    main()
