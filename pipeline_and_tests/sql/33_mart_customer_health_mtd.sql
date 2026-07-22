-- Month-to-date (in-flight) health mart: a projected, pace-adjusted CVRS plus a
-- momentum signal, so leaders don't have to wait for month end. Provisional by
-- design - never used for tier assignment or compensation (the monthly
-- mart_customer_health remains the system of record).
--
-- Pacing: raw MTD utilization would read artificially low mid-month (only part
-- of the month has elapsed), so utilization is normalized by elapsed_fraction -
-- comparing actual pace to expected pace. Depth, breadth, and consistency ride
-- the last completed month's baselines (they move slowly); only utilization is
-- live. Momentum compares this month's daily pace to the account's own
-- trailing-3-month daily pace.

CREATE OR REPLACE TABLE ${DATASET}.mart_customer_health_mtd AS
WITH params AS (
  SELECT
    DATE '2026-07-15' AS as_of_date,
    DATE '2026-07-01' AS month_start,
    EXTRACT(DAY FROM LAST_DAY(DATE '2026-07-01')) AS days_in_month
),
mtd AS (
  SELECT cust_id, SUM(consumed_units) AS consumed_mtd
  FROM ${DATASET}.stg_consumption_daily
  GROUP BY 1
),
entitled AS (  -- entitled capacity active in the current (as-of) month
  SELECT e.cust_id, SUM(e.licensed_amount) AS entitled_units
  FROM ${DATASET}.stg_entitlements e, params p
  WHERE e.start_date <= LAST_DAY(p.month_start) AND e.end_date >= p.month_start
  GROUP BY 1
),
trailing AS (  -- last 3 completed months -> average daily pace (91 days)
  SELECT cust_id, SUM(consumed_units) / 91.0 AS trailing_daily_pace
  FROM ${DATASET}.stg_consumption
  WHERE usage_month IN (DATE '2026-04-01', DATE '2026-05-01', DATE '2026-06-01')
  GROUP BY 1
),
baseline AS (  -- slow-moving components + last final score, from the last completed month
  SELECT cust_id, feature_depth, breadth, consistency,
         cvrs AS last_final_cvrs, health_tier AS last_tier
  FROM ${DATASET}.mart_customer_health
  WHERE month_start = DATE '2026-06-01'
),
calc AS (
  SELECT
    en.cust_id,
    p.as_of_date,
    EXTRACT(DAY FROM p.as_of_date) AS day_of_month,
    ROUND(EXTRACT(DAY FROM p.as_of_date) / p.days_in_month, 3) AS elapsed_fraction,
    en.entitled_units,
    COALESCE(m.consumed_mtd, 0) AS consumed_mtd,
    COALESCE(t.trailing_daily_pace, 0) AS trailing_daily_pace,
    b.feature_depth, b.breadth, b.consistency, b.last_final_cvrs, b.last_tier
  FROM entitled en
  CROSS JOIN params p
  JOIN baseline b ON b.cust_id = en.cust_id
  LEFT JOIN mtd m ON m.cust_id = en.cust_id
  LEFT JOIN trailing t ON t.cust_id = en.cust_id
)
SELECT
  c.cust_id,
  cu.cust_name,
  cu.region,
  cu.segment AS customer_segment,
  c.as_of_date,
  c.day_of_month,
  c.elapsed_fraction,
  ROUND(c.elapsed_fraction * 100, 0) AS pct_month_elapsed,
  c.entitled_units,
  c.consumed_mtd,
  -- Pace-adjusted MTD utilization (capped) - the only live CVRS component.
  LEAST(SAFE_DIVIDE(c.consumed_mtd, c.entitled_units * c.elapsed_fraction), 1.0) AS mtd_utilization,
  -- Uncapped run-rate: where full-month utilization lands if pace holds.
  ROUND(SAFE_DIVIDE(c.consumed_mtd / c.elapsed_fraction, c.entitled_units), 3) AS projected_util_run_rate,
  -- Momentum: current daily pace vs the account's own trailing daily pace.
  ROUND(SAFE_DIVIDE(c.consumed_mtd / c.day_of_month - c.trailing_daily_pace,
                    NULLIF(c.trailing_daily_pace, 0)), 3) AS momentum,
  c.feature_depth,
  c.breadth,
  c.consistency,
  c.last_final_cvrs,
  c.last_tier,
  ROUND(100 * (
      0.40 * LEAST(SAFE_DIVIDE(c.consumed_mtd, c.entitled_units * c.elapsed_fraction), 1.0)
    + 0.30 * c.feature_depth
    + 0.20 * c.consistency
    + 0.10 * c.breadth), 1) AS projected_cvrs,
  (c.elapsed_fraction < 0.20) AS low_confidence
FROM calc c
JOIN ${DATASET}.stg_customers cu ON cu.cust_id = c.cust_id;
