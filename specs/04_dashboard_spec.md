# Dashboard Specification

**Stack:** Streamlit + Plotly, reading the three mart tables from BigQuery.
**Persona:** GM of Product / CPO first; CSM drill-down second.
**Run:** `streamlit run dashboard/app.py`

## Data access

- Primary: BigQuery via `google-cloud-bigquery`, cached with `st.cache_data(ttl=600)`.
- Fallback: if BigQuery is unreachable or unconfigured, load mart snapshot CSVs from
  `dashboard/snapshots/` (written by `run_pipeline.py --export-snapshots`) and show a
  visible "snapshot mode" banner. Keeps the live demo resilient.

## Views (sidebar navigation)

### 1. Portfolio (default — the executive view)
- **Filters:** date range (Month / Quarter / Year granularity + a **multi-select** of
  periods, defaulting to all periods of the current year) plus region, customer segment,
  and product platform. The platform filter scopes to customers who own a SKU on that
  platform. All point-in-time figures are "as of" the latest month across the selected
  periods; the trend chart spans every selected period with exact month-name labels.
- **Snapshot row:** entitled customers, overall adoption rate (share of customers using
  ≥1 entitled product — hidden if it is ever 100%), healthiest platform (highest average
  of utilization + feature depth).
- **Adoption health row:** avg CVRS plus % Healthy / % Shelfware / % At Risk /
  % Expansion — all as percentages. Each percentage is a link that scrolls to the
  corresponding customer table below.
- **Revenue impact row** (assumed pricing, never a CVRS input): Committed ARR,
  ARR-at-risk, expansion pipeline, avg ARR/customer.
- CVRS distribution trend (stacked health-tier counts over the selected period) +
  health-tier donut for the as-of month.
- **Customer detail tables** (anchored): Healthy, Shelfware, At Risk, and Expansion
  signals. The expansion table is sorted by months-in-overage then peak raw utilization,
  descending (the "hand this to sales" list).

### 1b. Live · Month-to-Date (the in-flight view)
- As-of banner (date + % of month elapsed + "provisional, not used for tiers/comp").
- KPI row: projected avg CVRS, # decelerating (momentum < −30%), # accelerating,
  # projected < 40.
- **Decelerating call-list:** accounts sorted by momentum — those pacing furthest below
  their own baseline, with projected CVRS, last final CVRS, and the delta. The "act
  today" list.
- Movers scatter: projected CVRS vs last month's final CVRS, colored by momentum
  (below the diagonal = trending down).

### 2. Customer Drill-Down (the CSM view)
- Customer selector →
  - CVRS line over 12 months with health-tier band coloring.
  - Component breakdown (utilization / depth / consistency / breadth) per month —
    the "why is my score X" transparency required by the human-in-the-loop design.
  - Entitlement timeline: consumed vs entitled per month; overlapping entitlements
    render as a stepped entitled line (expansion visible at a glance).
  - SKU table: per-product utilization, feature depth, deployment status, plus the
    first adoption date and day-level time-to-first-value for that product.

### 3. Product & Feature (the product-GM view)
- SKU adoption ranking: products by avg utilization and feature depth.
- Feature adoption heatmap: adoption rate by feature (top/bottom 20) over months.
- Time-to-adopt distribution per platform — which capabilities are hard to turn on.

## Non-goals
Auth, write-back annotations, scheduled refresh — out of scope for prototype (noted
in the deck as roadmap).
