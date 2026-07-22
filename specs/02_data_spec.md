# Synthetic Data Specification

**Purpose:** generate a realistic B2B SaaS/cybersecurity dataset with labeled anomaly
cohorts so downstream metrics can be validated against known ground truth.

## Global rules

- **Reproducibility:** all randomness seeded (`SEED = 42`). Re-running the generator
  produces byte-identical CSVs.
- **History window:** 12 months, **2025-07-01 through 2026-06-30** (monthly grain).
- **Output:** one CSV per table in `data_generation/output/`, plus
  `cohort_assignments.csv` (ground truth, used by tests — also loaded to BigQuery).
- **Domain flavor:** cybersecurity product names (Endpoint, Cloud Security, SIEM,
  Identity, Network platforms).

## Tables

### 1. `customers` (~100 rows)
| Column | Type | Rule |
|--------|------|------|
| cust_id | STRING PK | `CUST-0001` … |
| cust_name | STRING | Faker company names |
| region | STRING | AMER / EMEA / APJ (weighted 50/30/20) |
| segment | STRING | Enterprise / Mid-Market (weighted 40/60) |

### 2. `products` (~500 rows)
| Column | Type | Rule |
|--------|------|------|
| product_id | STRING PK | `PROD-0001` … |
| product_name | STRING | Generated from platform + capability word bank (unique) |
| product_platform | STRING | Endpoint Security / Cloud Security / SIEM & Analytics / Identity Protection / Network Security |
| unit_price | FLOAT | Assumed list price per licensed unit per month. Deterministic per-platform base (Endpoint 1.50 … SIEM 6.00) ±10% jitter from the product number — computed without drawing from the RNG so it does not perturb any other generated value |

### 3. `entitlements` (~500 rows)
| Column | Type | Rule |
|--------|------|------|
| entitlement_id | STRING PK | `ENT-00001` … |
| product_id | STRING FK | Every customer gets 2–8 entitlements across distinct products |
| cust_id | STRING FK | |
| units_purchased | INT | 100–50,000 (Enterprise skews higher) |
| licensed_amount | INT | = units_purchased (the consumable monthly capacity) |
| start_date | DATE | Base contracts start 2025-07-01 ± 0–60 days |
| end_date | DATE | start + 12 months (some 24/36-month terms) |

### 4. `features` (~2,000 rows)
| Column | Type | Rule |
|--------|------|------|
| feature_id | STRING PK | `FEAT-00001` … |
| feature_name | STRING | capability word bank |
| feature_description | STRING | Faker sentence |
| product_id | STRING FK | ~4 features per product (3–6) |

### 5. `consumption` (monthly grain; ~5,000+ rows)
| Column | Type | Rule |
|--------|------|------|
| cust_id | STRING FK | |
| entitlement_id | STRING FK | |
| usage_month | DATE | First day of month |
| consumed_units | INT | Per cohort behavior (below); rows only exist for months inside the entitlement's active window |

### 6. `product_adoption` (customer × product grain; ~450 rows)
| Column | Type | Rule |
|--------|------|------|
| cust_id | STRING FK | |
| product_id | STRING FK | |
| first_adoption_date | DATE | Daily date of first product use = `start_date + deployment_lag`, always ≥ the contract start. **Deployment lag** (normal cohort): ~70% adopt within 20 days, ~20% in 21–55 days, ~10% in 56–90 days; other consuming cohorts adopt within ~12 days; spike-drop adopts immediately. Shelfware customers get **no row** (never adopt). Consumption is zeroed before the adoption month, so the daily adoption date and monthly consumption stay consistent — this is what makes day-level TTFV meaningful |

### 7. `feature_adoption` (~15,000+ rows)
| Column | Type | Rule |
|--------|------|------|
| cust_id | STRING FK | |
| feature_id | STRING FK | Only features of products the customer is entitled to |
| usage_month | DATE | |
| monthly_active | BOOL | Adoption ramps: a customer adopts features progressively; adopted features stay active with ~90% persistence |
| adoption_date | DATE | First active date for this cust-feature pair (repeated on each row) |

### 8. `month_spine` (12 rows)
| Column | Type |
|--------|------|
| month_start | DATE |
| month_end | DATE |

### 9. `cohort_assignments` (ground truth; ~100 rows)
| Column | Type | Values |
|--------|------|--------|
| cust_id | STRING | |
| cohort | STRING | `spike_drop` / `shelfware` / `overage` / `expansion` / `normal` |

## Injected anomaly cohorts (disjoint, assigned first)

| Cohort | Count | Behavior |
|--------|-------|----------|
| **spike_drop** | 5 | Months 1–3 of each entitlement: consumption totaling ~90% of annual licensed amount (30% per month); months 4+: 0 units (rows written with 0 — the account went dark, not missing) |
| **shelfware** | 10 | **No rows at all** in `consumption`. Feature adoption also empty. |
| **overage** | 15 | Every month: 120–150% of monthly licensed amount, mild noise |
| **expansion** | 9 | Normal ramp on the base contract; a **second entitlement on the same product** at 1.5–3× the size starts in month 5–8 with a fresh 12-month term, overlapping the original. Consumption on the new entitlement ramps from its start. |
| **normal** | ~61 | S-curve ramp: ~15–30% utilization month 1 rising to a plateau of 60–85% by month 4–6, ±10% monthly noise. A minority (~15% of normals) plateau low (30–45%) — realistic mediocre adoption. |

**Feature adoption per cohort:** normal/expansion/overage accounts progressively adopt
40–90% of eligible features; spike_drop accounts adopt a few features early then go
inactive; shelfware adopts none.

## Known coverage gap (deferred)

The generated cohorts produce only two shapes of decline: catastrophic (spike-and-drop
→ CVRS 0.0) and none. There is no **gradual erosion** cohort — an account drifting from
~75 to ~45 over several months without breaching the CVRS < 40 floor.

Consequence: the relative-drop At Risk rule (≥ 25 points vs trailing average) and its
post-expansion grace window are never the deciding factor for any row, because the
absolute floor always fires first. See `01_framework_spec.md §3` for the measured
evidence.

Two candidate fixes, both deferred:
1. Raise expansion multiples from 1.5–3.0× to ~2.5–4.0× (the observed maximum dip was
   23.5 points at 2.24×, so ~2.5× is roughly the threshold for exceeding 25).
2. Add a `slow_erosion` cohort: plateau near 80% utilization, then decay ~8–10% per
   month while feature adoption quietly lapses.

## Data-quality invariants (asserted by tests)

1. PKs unique; all FKs resolve.
2. `consumption.consumed_units >= 0`; no rows outside the entitlement active window.
3. `start_date < end_date` for every entitlement.
4. Cohort proportions exactly as specified (5/10/15/9 + remainder).
5. Shelfware custs have zero consumption rows; overage custs exceed 1.2× in ≥ 10 of 12 months; expansion custs have ≥ 2 overlapping entitlements on some month.
6. 12 distinct months in the spine covering 2025-07 … 2026-06.
