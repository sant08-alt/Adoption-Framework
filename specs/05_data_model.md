# Data Model Reference

Column-level dictionary for every object in the `adoption_analytics` dataset.
Grain, types, and derivation are stated per column. Row counts are from the
current seeded dataset (SEED = 42, 12 months, 100 customers).

For *why* each metric exists see `01_framework_spec.md`; for the raw generation
rules see `02_data_spec.md`; for layer/orchestration logic see `03_pipeline_spec.md`.

## Object inventory

| Layer | Object | Type | Grain | Rows |
|---|---|---|---|---|
| Raw | `customers` | table | customer | 100 |
| Raw | `products` | table | product | 500 |
| Raw | `features` | table | feature | 2,276 |
| Raw | `entitlements` | table | contract line | 507 |
| Raw | `consumption` | table | entitlement × month | 5,254 |
| Raw | `product_adoption` | table | customer × product | ~451 |
| Raw | `feature_adoption` | table | customer × feature × month | 11,611 |
| Raw | `month_spine` | table | month | 12 |
| Raw | `cohort_assignments` | table | customer | 100 |
| Staging | `stg_*` (7 objects) | **view** | mirrors raw | — |
| Intermediate | `int_monthly_entitlements` | table | customer × product × month | 5,741 |
| Intermediate | `int_monthly_utilization` | table | customer × month | 1,151 |
| Intermediate | `int_feature_depth` | table | customer × product × month | 5,741 |
| Mart | `mart_customer_health` | table | customer × month | 1,151 |
| Mart | `mart_sku_adoption` | table | customer × product × month | 5,741 |
| Mart | `mart_feature_adoption` | table | feature × month | 16,923 |

Staging is deliberately **views** (zero storage, always fresh); intermediates and
marts are **tables** (materialized once per run, so the dashboard reads are cheap).

---

## Intermediate layer

### `int_monthly_entitlements` — customer × product × month

| Column | Type | Definition |
|---|---|---|
| `cust_id` | STRING | FK → customers |
| `product_id` | STRING | FK → products |
| `month_start` | DATE | First day of month |
| `active_entitled_units` | INT64 | **SUM** of `licensed_amount` across all contracts active in this month. Overlapping (expansion) contracts sum here — the mid-year-expansion fix |
| `active_entitlement_count` | INT64 | How many contracts were simultaneously active (>1 ⇒ overlap) |
| `earliest_start_date` | DATE | Earliest contract start for this customer-product, used for TTFV |

A row exists for every month a contract was live, **regardless of usage** — this is
what prevents shelfware accounts from disappearing from reporting.

### `int_monthly_utilization` — customer × month

| Column | Type | Definition |
|---|---|---|
| `cust_id` | STRING | FK → customers |
| `month_start` | DATE | First day of month |
| `entitled_units` | INT64 | Total active entitled capacity across all SKUs |
| `consumed_units` | INT64 | Total consumption; **0, not NULL**, when no consumption exists |
| `raw_utilization` | FLOAT64 | `consumed / entitled`, **uncapped** — can exceed 1.0 (overage) |
| `utilization` | FLOAT64 | `LEAST(raw_utilization, 1.0)` — the capped value used in CVRS |

### `int_feature_depth` — customer × product × month

| Column | Type | Definition |
|---|---|---|
| `cust_id`, `product_id`, `month_start` | — | Grain |
| `eligible_features` | INT64 | Features belonging to a product the customer holds an active entitlement for |
| `active_features` | INT64 | Distinct features with `monthly_active = TRUE` this month |
| `feature_depth` | FLOAT64 | `active / eligible`, 0 when none active (never NULL) |

---

## Mart layer

### `mart_customer_health` — customer × month (the CVRS table)

The primary table. 21 columns, grouped by purpose:

**Identity & dimensions**

| Column | Type | Definition |
|---|---|---|
| `cust_id` | STRING | Grain key |
| `cust_name` | STRING | Display name |
| `region` | STRING | AMER / EMEA / APJ |
| `customer_segment` | STRING | **Commercial** segment: Enterprise / Mid-Market (distinct from `health_tier`) |
| `ground_truth_cohort` | STRING | Injected label: `normal` / `spike_drop` / `shelfware` / `overage` / `expansion`. **Evaluation only — never an input to the score** |
| `month_start` | DATE | Grain key |

**Volume inputs**

| Column | Type | Definition |
|---|---|---|
| `entitled_units` | INT64 | Active entitled capacity this month (expansion-aware denominator) |
| `consumed_units` | INT64 | Consumption this month; 0 for shelfware |
| `cumulative_consumed` | INT64 | Running total to date; used to separate "never deployed" from "went quiet" |

**CVRS components** (each 0–1)

| Column | Type | Definition | Weight |
|---|---|---|---|
| `utilization` | FLOAT64 | Capped consumed ÷ entitled | 40% |
| `feature_depth` | FLOAT64 | Mean feature depth across the customer's active SKUs | 30% |
| `consistency` | FLOAT64 | Trailing-3-month peak retention × volatility penalty; 0 when no usage history | 20% |
| `breadth` | FLOAT64 | SKUs with usage ÷ SKUs owned | 10% |
| `cvrs` | FLOAT64 | `100 × (0.4u + 0.3d + 0.2c + 0.1b)`, rounded to 1dp. Range 0–100 | — |

**Classification & diagnostics**

| Column | Type | Definition |
|---|---|---|
| `raw_utilization` | FLOAT64 | Uncapped ratio; drives overage detection |
| `expansion_flag` | BOOL | TRUE when `raw_utilization >= 1.2` for 2+ consecutive months |
| `ttfv_days` | INT64 | Time-to-first-value: days from the contract `start_date` to `product_adoption.first_adoption_date`, minimised across the customer's products (always ≥ 0). **NULL = never deployed.** Ranges 0–~90 days |
| `trailing_avg_cvrs` | FLOAT64 | Mean CVRS over the 3 months *preceding* this one (excludes current). NULL for a customer's first month |
| `months_since_capacity_increase` | INT64 | Months since entitled capacity last stepped up. NULL if never |
| `in_expansion_grace` | BOOL | TRUE when `months_since_capacity_increase <= 1` (2-month window) |
| `health_tier` | STRING | `Healthy` / `Watch` / `At Risk` / `Shelfware` — see rules below |

**Revenue impact** (assumed list pricing — reporting only, **never an input to CVRS**)

| Column | Type | Definition |
|---|---|---|
| `committed_mrr` | FLOAT64 | Σ over active SKUs of `active_entitled_units × unit_price` — monthly committed recurring revenue |
| `committed_arr` | FLOAT64 | `committed_mrr × 12` — annual run-rate |
| `overage_mrr` | FLOAT64 | Σ over SKUs of `max(consumed − entitled, 0) × unit_price` — billable over-consumption this month |
| `overage_arr_run_rate` | FLOAT64 | `overage_mrr × 12` — annualized overage pipeline (used for Expansion Signal sizing) |

**`health_tier` evaluation order** (first match wins):
1. `Shelfware` — `consumed_units = 0 AND cumulative_consumed = 0`
2. `At Risk` — `cvrs < 40` (absolute floor, **never suspended**)
3. `At Risk` — `trailing_avg_cvrs - cvrs >= 25 AND NOT in_expansion_grace`
4. `Healthy` — `cvrs >= 70`
5. `Watch` — everything else

Current distribution (all 1,151 rows): Healthy 504 · Watch 474 · Shelfware 115 · At Risk 58.

### `mart_sku_adoption` — customer × product × month

Same customer dimensions, plus:

| Column | Type | Definition |
|---|---|---|
| `product_id`, `product_name`, `product_platform` | STRING | SKU dimensions |
| `active_entitled_units` | INT64 | Entitled capacity for **this SKU** this month |
| `active_entitlement_count` | INT64 | Contracts simultaneously active on this SKU |
| `consumed_units` | INT64 | Consumption attributed to this SKU |
| `raw_utilization` / `utilization` | FLOAT64 | Uncapped / capped, same semantics as customer level |
| `eligible_features` / `active_features` | INT64 | Feature counts for this SKU |
| `feature_depth` | FLOAT64 | `active ÷ eligible` for this SKU |
| `deployment_status` | STRING | `Not Deployed` (cumulative = 0) · `Deployed` (consumed this month) · `Dormant` (consumed historically, zero this month) |
| `first_adoption_date` | DATE | Daily date the customer first used this product (from `product_adoption`). NULL if never adopted |
| `days_to_first_value` | INT64 | Days from the contract start to `first_adoption_date` for this SKU (always ≥ 0) |
| `unit_price` | FLOAT64 | Assumed list price per licensed unit per month (from `products`) |
| `committed_mrr` | FLOAT64 | `active_entitled_units × unit_price` for this SKU |
| `overage_mrr` | FLOAT64 | `max(consumed − entitled, 0) × unit_price` — billable over-consumption |

Current distribution: Deployed 5,038 · Not Deployed 541 · Dormant 162.

### `mart_feature_adoption` — feature × month

| Column | Type | Definition |
|---|---|---|
| `month_start` | DATE | Grain key |
| `feature_id`, `feature_name` | STRING | Feature identity |
| `product_id`, `product_name`, `product_platform` | STRING | Parent SKU |
| `eligible_customers` | INT64 | Customers entitled to the parent product this month |
| `active_customers` | INT64 | Customers with `monthly_active = TRUE` on this feature |
| `adoption_rate` | FLOAT64 | `active ÷ eligible`, 0–1 |
| `median_days_to_adopt` | INT64 | Median days from entitlement start to first adoption, across all customers who ever adopted. NULL if never adopted |

Note: `median_days_to_adopt` is **constant per feature** (not time-varying) — it
repeats across months. Filter to a single month or `DISTINCT feature_id` when
analysing it, as the dashboard does.

---

## Known modelling notes

- **`units_purchased` = `licensed_amount`** in the generated data. In production these
  differ (commercial quantity vs. metered monthly capacity); all pipeline logic reads
  only `licensed_amount`, so it is already correct for the general case.
- **`ground_truth_cohort` is carried into the mart deliberately** so the evaluation
  suite can query it. It is never read by any scoring logic. In production this column
  would be absent.
- **Feature adoption is binary** (`monthly_active`), not intensity-weighted — a v1
  telemetry scope limit, not a modelling preference.
- **Pricing is assumed, not real.** `unit_price` is a deterministic per-platform list
  price (`PLATFORM_PRICES` in the generator), applied so revenue figures are
  illustrative. Revenue columns are a **reporting overlay** — `test_cvrs_is_pure_
  function_of_its_four_components` asserts CVRS reconstructs exactly from its four
  inputs, proving revenue never enters the score. Revenue models the *committed* +
  *overage* consumption structure; it does not model discounts, ramp deals, or
  multi-year escalators.
