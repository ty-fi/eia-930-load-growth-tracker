# EIA 930 Load Growth Tracker

## What this is

A Streamlit dashboard that visualizes year-over-year growth in US electricity demand using EIA 930 hourly data. The goal is to detect the onset of large-load (data center) demand growth by smoothing out weather-driven peak demand spikes and comparing quarterly/yearly averages against a 2022-2023 baseline.

## Architecture

```
eia-930-load-growth-tracker/
├── inputs/
│   ├── out_eia930__hourly_subregion_demand.parquet  # 10 BAs, 90 subregions (5.7M rows)
│   ├── out_eia930__hourly_operations.parquet        # 70 BAs, BA-level (6.0M rows)
│   ├── out_eia930__hourly_aggregated_demand.parquet # 13 regions + interconnects + CONUS
│   ├── ba_subregion_centroids.csv                    # subregion coordinates (90)
│   ├── ba_centroids.csv                              # BA coordinates (70)
│   ├── region_centroids.csv                          # region coordinates (13)
│   ├── ba_region_mapping.csv                         # maps 70 BAs to 13 EIA regions
│   └── growth_metrics.csv                            # OUTPUT of process_data.py (55 cols)
├── process_data.py  # Step 1: Aggregate parquet → growth metrics CSV
├── app.py           # Step 2: Streamlit app
├── requirements.txt
├── PLAN.md          # Original implementation plan (mostly historical)
└── CLAUDE.md        # This file
```

## How to run

```bash
# Regenerate metrics (run when data updates)
python process_data.py

# Launch the app
streamlit run app.py
```

Requires: pandas, pyarrow, streamlit, folium, streamlit-folium, plotly (see `requirements.txt`).

## Data flow

### Step 1: `process_data.py`
1. Loads 3 PUDL parquet files (subregion, BA, regional).
2. For each, creates unified demand: `coalesce(demand_imputed_pudl_mwh, demand_reported_mwh)`.
3. Filters to complete months only (≥ 672 hours).
4. Computes rolling 12-month windows for peak demand (GW) and total load (TWh).
5. Computes **quarterly average demand (GW)** per entity for 16 quarters (2022Q2 → 2026Q1).
6. Computes **12 quarter-over-baseline comparisons** (absolute GW and % change) against matched same-season baseline quarters from 2022-2023.
7. Computes **yearly averages** (Q2-Q1 anchored) from quarterly data.
8. Joins with centroid coordinates.
9. Outputs `inputs/growth_metrics.csv` with 55 columns.

### Quarter-baseline mapping
Baseline year: 2022Q2, 2022Q3, 2022Q4, 2023Q1

Comparison quarters (each matched against same-season baseline):
| Recent | Baseline | | Recent | Baseline | | Recent | Baseline |
|--------|----------|---|--------|----------|---|--------|----------|
| 2023Q2 | 2022Q2 | | 2024Q2 | 2022Q2 | | 2025Q2 | 2022Q2 |
| 2023Q3 | 2022Q3 | | 2024Q3 | 2022Q3 | | 2025Q3 | 2022Q3 |
| 2023Q4 | 2022Q4 | | 2024Q4 | 2022Q4 | | 2025Q4 | 2022Q4 |
| 2024Q1 | 2023Q1 | | 2025Q1 | 2023Q1 | | 2026Q1 | 2023Q1 |

This gives 12 growth comparisons per entity, spanning 3 years of comparison periods (2023Q2-2024Q1, 2024Q2-2025Q1, 2025Q2-2026Q1).

### Yearly periods (Q2-Q1 anchored)
- 2022-23: 2022Q2 + 2022Q3 + 2022Q4 + 2023Q1
- 2023-24: 2023Q2 + 2023Q3 + 2023Q4 + 2024Q1
- 2024-25: 2024Q2 + 2024Q3 + 2024Q4 + 2025Q1
- 2025-26: 2025Q2 + 2025Q3 + 2025Q4 + 2026Q1

### Step 2: `app.py`
Streamlit app with two views:
- **Map view**: Folium bubble map with peak demand / total load growth.
- **Growth Chart + Table view**: Interactive line chart + sortable table.

## App controls (Growth Chart + Table view)

| Control | Options | Default |
|---------|---------|---------|
| View | Map / Growth Chart + Table | Growth Chart + Table |
| Aggregation Level | Most aggregated (regions) / Middle (BAs) / Least aggregated (subregions + BAs) | Least aggregated |
| Granularity | Quarterly / Yearly | Quarterly |
| Chart Metric | Average Demand (GW) / Growth vs Baseline (GW) / Growth vs Baseline (%) | Growth vs Baseline (GW) |
| Geographies (multiselect) | All entities at current level | Top 3 by magnitude on startup |

## Aggregation levels

- **Most aggregated**: 13 EIA regions
- **Middle**: 54 BAs (all with complete data)
- **Least aggregated**: 86 subregions + 45 BAs without subregion data = 131 entities

## Display names

Subregions get a BA prefix: `PJM - SCE`, `MISO - Michigan`, etc. BAs and regions keep their plain name. This avoids confusion when subregions from different BAs share names.

## Key design decisions

1. **Quarterly average demand, not peak demand** — peak is too weather-sensitive for detecting data center demand onset. Quarterly averages smooth out weather while still catching large facility commissioning.
2. **Fixed baseline (2022-2023)** — pre-AI-boom "normal" year. All growth comparisons are cumulative from this baseline.
3. **Imputed demand (PUDL) preferred** — reported demand as fallback.
4. **Complete months only (≥ 672 hours)** — partial current month (2026-05) is filtered out.
5. **No .round() on data values** — only truncation to 2 decimal places for display via `_fmt()`. All calculations use full float precision.
6. **Display truncation, not rounding** — `math.trunc(val * 100) / 100`, then strip trailing zeroes.

## File-level notes

### `process_data.py`
- Module-level constants: `QUARTER_MONTH_MAP`, `COMPARISON_QUARTERS`, `ALL_QUARTERS`
- `QUARTERLY_GROWTH_COLS` and `ALL_GROWTH_COLS` are built at module level
- Aggregation functions (`build_most_aggregated`, `build_middle_aggregated`, `build_least_aggregated`) spread `ALL_GROWTH_COLS` into output rows

### `app.py`
- Helper: `_get_display_name(row)` — adds BA prefix for subregions
- Helper: `_get_recent_growth_col(granularity, chart_metric, level_df, yearly_df)` — returns the sort column for top-3 defaults
- Helper: `_fmt(val)` — truncate to 2 decimals, strip trailing zeroes
- `render_chart_view()` — main chart view; computes top-3 defaults, renders chart, delegates to `_render_full_table`
- `build_line_chart()` / `build_yearly_chart()` — Plotly line charts
- `_render_full_table()` — sortable data_editor table (read-only, no checkboxes)

## Session state keys (in app.py)

- `selected_ids` — set of entity IDs currently plotted
- `geo_select` — multiselect widget state
- `table_editor` — data_editor widget state (preserves sort across reruns)
- `_pending_toggle_ids` — pending flag for deferred widget state writes
- `_prev_level` — previous aggregation level
- `_table_scheme_sig` — (level, granularity, chart_metric) tuple for detecting scheme changes
- `display_name_full` — computed column with BA prefix for subregions

## What NOT to do

- Don't `.round()` values for display — use `_fmt()` (truncation only). Calculations must use full precision.
- Don't modify widget state (`st.session_state["geo_select"]`, etc.) after the widget has been instantiated. Use the pending flag pattern (`_pending_toggle_ids`) and apply the state change before the widget renders on the next run.
- Don't use `st.data_editor` for interactive checkboxes — the widget state sync with `st.dataframe` and the multiselect is fragile. The table is read-only; selection is controlled by the multiselect.

## Open items / known limitations

- `st.data_editor` sort state is internal to the glide-data-grid component and not accessible via session state. The default sort (by most recent column, descending) is always applied; user column-header sorts are lost on rerun.
- Some BAs dropped (54 of 70) due to incomplete months.
- SCE and TVA show significant peak demand declines worth investigating.
