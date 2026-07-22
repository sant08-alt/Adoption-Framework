# Pipeline & Data-Quality Specification

**Storage/compute:** Google BigQuery (Sandbox). Dataset: `adoption_analytics`.
**Orchestration:** `pipeline_and_tests/run_pipeline.py` executes the SQL files in
`pipeline_and_tests/sql/` in filename order (dbt-style layers without the dbt
dependency — deliberate Build-vs-Buy call for a prototype).

## Layers

```
raw (loaded CSVs)
  └─ 10_stg_*        typed, deduped views
      └─ 2x_int_*    business-grain intermediates
          └─ 3x_mart_*   consumption-ready marts (dashboard reads these)
```

### Staging (`10_…`)
One view per raw table: explicit casts, trimmed strings, deduplication on PK.

### Intermediates

**`20_int_monthly_entitlements`** — grain: cust × product × month
- Cross join `month_spine` × `stg_entitlements` filtered to
  `start_date <= month_end AND end_date >= month_start`.
- `active_entitled_units = SUM(licensed_amount)` per cust-product-month.
- **This is the mid-year-expansion fix:** overlapping contracts sum into the
  denominator for the months they overlap.

**`21_int_monthly_utilization`** — grain: cust × month
- Left join consumption onto active entitlements (left = entitlements, so shelfware
  months exist with `consumed_units = 0`).
- `raw_utilization = consumed / entitled` (uncapped), `utilization = LEAST(raw, 1.0)`.

**`22_int_feature_depth`** — grain: cust × product × month
- Eligible features: features of products with an active entitlement that month.
- Active features: `feature_adoption.monthly_active` rows for that month.
- `feature_depth = active / eligible` per SKU; also rolls to cust × month (avg across SKUs).

### Marts

**`30_mart_customer_health`** — grain: cust × month (the CVRS table)
- Components: utilization (capped), feature_depth, consistency (3-month window
  functions: peak, relative drop, stddev/mean volatility penalty), breadth.
- `cvrs = 100 * (0.4*u + 0.3*d + 0.2*c + 0.1*b)` per `01_framework_spec.md §2`.
- Post-expansion grace: `last_capacity_increase_month` (running max of months where the
  entitled denominator stepped up) drives `in_expansion_grace`
  (`months_since_capacity_increase <= 1`, i.e. a 2-month window). The grace suspends only
  the relative-drop At Risk rule, never the absolute CVRS < 40 floor.
- `health_tier` via CASE in the priority order of §3; `expansion_flag` from 2+ consecutive
  months of `raw_utilization >= 1.2`.
- TTFV per customer (min over entitlements) carried as a column.
- Revenue overlay (reporting only, never a CVRS input): `committed_mrr` / `committed_arr`
  (Σ entitled × per-SKU `unit_price`) and `overage_mrr` / `overage_arr_run_rate`
  (Σ billable over-consumption × price), rolled up across the customer's SKUs.

**`31_mart_sku_adoption`** — grain: cust × product × month
- utilization, raw_utilization, feature_depth, deployment_status:
  - `Not Deployed` — zero cumulative consumption on this SKU to date
  - `Deployed` — consumed in the current month
  - `Dormant` — consumed historically, but zero this month (went quiet)
- Revenue overlay per SKU: `unit_price`, `committed_mrr` (entitled × price),
  `overage_mrr` (billable over-consumption × price).

Full column-level definitions for every table: see `05_data_model.md`.

**`32_mart_feature_adoption`** — grain: feature × month
- `adoption_rate = customers_active_on_feature / customers_eligible`,
  median days-to-adopt.

## Runner behavior (`run_pipeline.py`)

- Reads `.env` (`GCP_PROJECT_ID`, `BQ_DATASET`); fails fast with a friendly message if
  unset or if Application Default Credentials are missing.
- Executes each file via `CREATE OR REPLACE VIEW/TABLE`; prints per-file row counts.
- Idempotent — safe to re-run.

## Automated data-quality tests (`pipeline_and_tests/tests/`)

Framework: pytest. Two suites:

### Suite A — local, pre-load (`test_raw_data_quality.py`)
Runs against the generated CSVs, no cloud needed (CI-friendly):
- Row-count expectations (§ volumes in `02_data_spec.md`).
- PK uniqueness, FK integrity across all 6 tables.
- No negative consumption; consumption only within entitlement windows.
- Entitlement date sanity (`start < end`).
- Cohort ground-truth invariants (shelfware has no consumption rows; overage ≥ 1.2×
  in ≥ 10 months; expansion has overlapping entitlements; spike_drop hits ~90% by
  month 3 then zeroes).

### Suite B — BigQuery, post-pipeline (`test_metrics_bigquery.py`)
Skips (with instructions) when ADC/project are unavailable; otherwise asserts:
- CVRS bounded [0, 100]; components bounded [0, 1].
- **Ground-truth detection** (the framework's own success criteria):
  - ≥ 90% of shelfware cohort in the `Shelfware` health tier every month.
  - Spike-drop cohort: CVRS in month 5 at least 25 points below its months-1–3 peak.
  - Overage cohort: `expansion_flag = true` by month 3; CVRS still ≤ 100 (cap works).
  - Expansion cohort: entitled denominator strictly increases in the expansion month;
    utilization never exceeds 100% due to stacked contracts alone.
  - Grace window is exactly 2 months, never applied without a capacity increase, and
    never skipped inside the window.
  - No expansion account is flagged At Risk purely from the post-expansion dip while
    still above the absolute floor.
  - The absolute floor survives grace: no row with CVRS < 40 escapes At Risk/Shelfware.
- Every cust-month with an active entitlement appears in the mart (shelfware never
  drops out of reporting).
