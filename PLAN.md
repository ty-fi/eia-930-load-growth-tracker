# EIA 930 Load Growth Tracker — Implementation Plan

## Overview
Streamlit app with an interactive US map showing annual growth of peak demand and total load by EIA 930 subregion, using a rolling 12-month comparison window.

## Data Summary
- **Source**: `inputs/out_eia930__hourly_subregion_demand.parquet` (5.7M rows)
- **Date range**: 2018-07-01 → 2026-05-02
- **Scope**: 10 balancing authorities, 90 subregions
- **Columns**: `datetime_utc`, `balancing_authority_code_eia`, `balancing_authority_subregion_code_eia`, `demand_reported_mwh`, `demand_imputed_pudl_mwh`, `demand_imputed_pudl_mwh_imputation_code`
- **Demand column strategy**: Use `demand_imputed_pudl_mwh` where available, fall back to `demand_reported_mwh` (99.8% coverage)

## Architecture

```
eia-930-load-growth-tracker/
├── inputs/
│   ├── out_eia930__hourly_subregion_demand.parquet   (existing)
│   └── ba_subregion_centroids.csv                    (existing, needs fixes)
├── process_data.py      # Step 1: Aggregate parquet → growth metrics CSV
├── app.py               # Step 2: Streamlit app with interactive map
├── requirements.txt     # Dependencies
├── PLAN.md              # This file
└── _SESSION-CONTEXT.md  # Session tracking
```

---

## Step 1: `process_data.py` — Data Processing Script

**Input**: `out_eia930__hourly_subregion_demand.parquet`

**Logic**:
1. Load parquet, create unified demand column: `coalesce(demand_imputed_pudl_mwh, demand_reported_mwh)`
2. Determine rolling 12-month windows:
   - `latest_ts = max(datetime_utc)` → e.g., 2026-05-02
   - `recent_year`: 12 months ending at `latest_ts`
   - `prior_year`: 12 months before that
3. For each subregion, for each window, calculate:
   - **Peak demand**: `max(demand_mwh)` (MWh/h ≈ MW)
   - **Total load**: `sum(demand_mwh)` (MWh)
4. Calculate growth:
   - `peak_demand_gw = peak_demand_mwh / 1000`
   - `total_load_twh = total_load_mwh / 1e6`
   - `peak_demand_growth_abs = peak_recent - peak_prior` (GW)
   - `peak_demand_growth_pct = (peak_recent - peak_prior) / peak_prior * 100`
   - `total_load_growth_abs` and `total_load_growth_pct` (same)
5. Join with centroid coordinates from `ba_subregion_centroids.csv`
6. Output: `inputs/growth_metrics.csv`

**Edge cases**:
- 2018 and 2026 are partial years — rolling window handles this
- Subregions with missing data in either window → flag or exclude
- Consider filtering to complete months only to avoid partial-month bias

---

## Step 1b: Fix `ba_subregion_centroids.csv`

### Bug: PJM DAY row (line 20)
- `subregion_code` is "Dayton Power and Light" instead of `DAY`
- Fix to: `PJM,PJM Interconnection,DAY,Dayton Power and Light,39.76,-84.19,Dayton Ohio region`

### Bug: BHBA geocoding
- BHBA = **Basin Electric Power Cooperative** (not Baja California)
- BASI → Basin HQ, Bismarck ND area: ~46.8, -100.8
- SDE → South Dakota Electric: ~44.3, -100.3
- WYE → Wyoming Electric: ~43.0, -104.6

---

## Step 2: `app.py` — Streamlit App

### Layout
- **Header**: Title, description, date range info
- **Sidebar**: Metric selector (Peak Demand / Total Load), display toggle (absolute GW/GWh vs. % growth)
- **Main panel**:
  - Interactive map with circle markers at subregion centroids
    - **Size** → absolute growth (GW or GWh)
    - **Color** → percent growth (diverging red/green)
    - **Hover tooltip** → subregion name, BA, peak demand, total load, growth values
  - Summary stats table below map
  - Bar chart of top/bottom growth subregions

### Tech choices
- `streamlit-folium` for interactive Folium map
- Color scale: diverging red-white-green (red = decline, green = growth) centered at 0
- Size scale: sqrt mapping of absolute growth to circle radius

---

## Step 3: `requirements.txt`

```
pandas>=2.0
pyarrow>=12.0
streamlit>=1.30
folium>=0.15
streamlit-folium>=0.18
```

---

## Open Questions — RESOLVED

1. **Rolling window**: Filter to complete months only — **YES** (implemented: filters to months with >= 672 hours)
2. **Map library**: `streamlit-folium` — **SELECTED** (Leaflet-based, hover tooltips, zoom)
3. **BHBA data**: Include Basin Electric — **YES** (3 subregions, but only recent-year data available; no prior-year for growth calc)
4. **Color scheme**: Diverging red-white-green at 0% — **SELECTED**

## Implementation Status

- [x] Fix `ba_subregion_centroids.csv` (DAY bug, BHBA geocoding)
- [x] Create `process_data.py` — outputs `inputs/growth_metrics.csv` (86 subregions)
- [x] Create `requirements.txt`
- [x] Create `app.py` — Streamlit app with Folium map
- [x] Test data processing pipeline
- [ ] Live test `streamlit run app.py`

## Results

- **86 subregions** with valid growth metrics (4 excluded: 3 BHBA with no prior data, 1 other)
- **Rolling window**: 2025-04-30 → 2026-04-30 (recent) vs 2024-04-30 → 2025-04-30 (prior)
- **Peak demand growth**: Mean -0.05 GW, range -3.41 GW (SCE) to +0.84 GW (MISO 0027/Michigan)
- **Total load growth**: Mean +0.64 TWh
