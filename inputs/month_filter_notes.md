# Month Filtering Notes

## What the filter does

`filter_complete_months` in `process_data.py` keeps only months where an entity has ≥ 672 hours (28 days × 24 hours) of data. This prevents partial or incomplete months from biasing the rolling 12-month comparison windows. The threshold is intentionally conservative — a full month has 672–744 hours, so anything below 672 is clearly incomplete.

## Impact

| Source | Entity-months total | Removed | Rows removed |
|--------|-------------------|---------|-------------|
| Subregion (90 entities) | 7,898 | 91 | 3,380 (0.1%) |
| BA (70 entities) | 8,335 | 71 | 3,400 (0.1%) |
| Region (13 entities) | 1,703 | 13 | 703 (0.1%) |

## What gets filtered

### 2026-05 (partial current month)

This accounts for the vast majority of filtered months: 89 of 91 subregion months, 59 of 71 BA months, and all 13 region months. Only ~30–56 hours of data exist as of processing date (2026-05-02).

### Remaining historical gaps

These are the non-trivial gaps that surfaced:

| Date | Entity type | Count | Hours present | Notes |
|------|------------|-------|---------------|-------|
| 2018-05 | BA | 1 | 56 | BA data starts mid-2018; partial onboard |
| 2018-07 | BA | 1 | 16 | Same onboarding issue |
| 2018-12 | BA | 1 | 6 | Same onboarding issue |
| 2020-01 | BA | 1 | 174 | ~73% of month — likely an outage or reporting gap |
| 2020-02 | BA | 1 | 17 | Single BA, near-total gap |
| 2021-04 | Subregion | 2 | 320–400 | ~43–54% of month — missing ~half |
| 2021-09 | BA | 1 | 6 | Near-total gap |
| 2022-09 | BA | 1 | 6 | Near-total gap |
| 2023-11 | BA | 1 | 8 | Near-total gap |
| 2025-06 | BA | 1 | 8 | Near-total gap |
| 2025-10 | BA | 1 | 7 | Near-total gap |
| 2026-04 | BA | 2 | 7–8 | Near-total gap (partial latest month) |

All of these are single-entity gaps (one BA or subregion), except 2021-04 which affects 2 subregions. No systemic data loss is visible — these are isolated onboarding artifacts or reporting outages.
