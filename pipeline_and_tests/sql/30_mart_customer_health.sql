-- Customer-month health mart: CVRS components, score, health tier, flags.
-- Formulas per specs/01_framework_spec.md §2-§3.

CREATE OR REPLACE TABLE ${DATASET}.mart_customer_health AS
WITH depth AS (
  SELECT
    cust_id,
    month_start,
    AVG(feature_depth) AS feature_depth
  FROM ${DATASET}.int_feature_depth
  GROUP BY 1, 2
),
product_usage AS (
  SELECT
    c.cust_id,
    e.product_id,
    c.usage_month AS month_start,
    SUM(c.consumed_units) AS consumed_units
  FROM ${DATASET}.stg_consumption c
  JOIN ${DATASET}.stg_entitlements e
    ON e.entitlement_id = c.entitlement_id
  GROUP BY 1, 2, 3
),
breadth AS (
  SELECT
    me.cust_id,
    me.month_start,
    COUNT(DISTINCT me.product_id) AS skus_owned,
    COUNT(DISTINCT IF(pu.consumed_units > 0, me.product_id, NULL)) AS skus_used
  FROM ${DATASET}.int_monthly_entitlements me
  LEFT JOIN product_usage pu
    ON pu.cust_id = me.cust_id
   AND pu.product_id = me.product_id
   AND pu.month_start = me.month_start
  GROUP BY 1, 2
),
ttfv AS (
  -- Day-level time-to-first-value from the real customer-product adoption date
  -- (product_adoption.first_adoption_date, always >= the contract start). Per
  -- customer, TTFV is the fastest product to reach first value.
  SELECT
    pa.cust_id,
    MIN(DATE_DIFF(pa.first_adoption_date, es.earliest_start, DAY)) AS ttfv_days
  FROM ${DATASET}.stg_product_adoption pa
  JOIN (
    SELECT cust_id, product_id, MIN(start_date) AS earliest_start
    FROM ${DATASET}.stg_entitlements
    GROUP BY 1, 2
  ) es
    ON es.cust_id = pa.cust_id AND es.product_id = pa.product_id
  GROUP BY 1
),
revenue AS (
  -- Customer-month revenue rolled up across the customer's active SKUs.
  -- Committed = entitled capacity x list price; overage = billable
  -- over-consumption x list price. Reporting only; never feeds CVRS.
  SELECT
    me.cust_id,
    me.month_start,
    ROUND(SUM(me.active_entitled_units * p.unit_price), 2) AS committed_mrr,
    ROUND(SUM(GREATEST(COALESCE(pu.consumed_units, 0) - me.active_entitled_units, 0)
              * p.unit_price), 2) AS overage_mrr
  FROM ${DATASET}.int_monthly_entitlements me
  JOIN ${DATASET}.stg_products p
    ON p.product_id = me.product_id
  LEFT JOIN product_usage pu
    ON pu.cust_id = me.cust_id
   AND pu.product_id = me.product_id
   AND pu.month_start = me.month_start
  GROUP BY 1, 2
),
base AS (
  SELECT
    u.cust_id,
    u.month_start,
    u.entitled_units,
    u.consumed_units,
    u.raw_utilization,
    u.utilization,
    COALESCE(d.feature_depth, 0) AS feature_depth,
    COALESCE(SAFE_DIVIDE(b.skus_used, b.skus_owned), 0) AS breadth,
    MAX(u.utilization) OVER w3 AS peak_util_3m,
    AVG(u.utilization) OVER w3 AS avg_util_3m,
    STDDEV_POP(u.utilization) OVER w3 AS std_util_3m,
    SUM(u.consumed_units) OVER (
      PARTITION BY u.cust_id ORDER BY u.month_start
    ) AS cumulative_consumed,
    LAG(u.raw_utilization) OVER (
      PARTITION BY u.cust_id ORDER BY u.month_start
    ) AS prev_raw_utilization,
    LAG(u.entitled_units) OVER (
      PARTITION BY u.cust_id ORDER BY u.month_start
    ) AS prev_entitled_units
  FROM ${DATASET}.int_monthly_utilization u
  LEFT JOIN depth d
    ON d.cust_id = u.cust_id AND d.month_start = u.month_start
  LEFT JOIN breadth b
    ON b.cust_id = u.cust_id AND b.month_start = u.month_start
  WINDOW w3 AS (
    PARTITION BY u.cust_id ORDER BY u.month_start
    ROWS BETWEEN 2 PRECEDING AND CURRENT ROW
  )
),
scored AS (
  SELECT
    *,
    -- Consistency: penalize drops from the trailing-3-month peak plus volatility.
    -- All-zero history (shelfware) scores 0, never NULL.
    CASE
      WHEN peak_util_3m = 0 THEN 0
      ELSE GREATEST(0, 1 - SAFE_DIVIDE(peak_util_3m - utilization, peak_util_3m))
           * (1 - LEAST(COALESCE(SAFE_DIVIDE(std_util_3m, NULLIF(avg_util_3m, 0)), 0), 1) * 0.5)
    END AS consistency
  FROM base
),
-- NOTE: this CTE must NOT be named `cvrs`. It emits a column also called `cvrs`,
-- and BigQuery would then resolve AVG(cvrs) below to the CTE's row-struct instead
-- of the column (see specs/06_hard_truths.md #1).
cvrs_calc AS (
  SELECT
    *,
    ROUND(100 * (0.40 * utilization
               + 0.30 * feature_depth
               + 0.20 * consistency
               + 0.10 * breadth), 1) AS cvrs
  FROM scored
),
flagged AS (
  SELECT
    *,
    (raw_utilization >= 1.2 AND COALESCE(prev_raw_utilization, 0) >= 1.2) AS expansion_flag,
    AVG(cvrs) OVER (
      PARTITION BY cust_id ORDER BY month_start
      ROWS BETWEEN 3 PRECEDING AND 1 PRECEDING
    ) AS trailing_avg_cvrs,
    -- Most recent month in which entitled capacity stepped up (a new or expanded
    -- contract landing). Drives the post-expansion grace period below.
    MAX(IF(entitled_units > COALESCE(prev_entitled_units, entitled_units),
           month_start, NULL)) OVER (
      PARTITION BY cust_id ORDER BY month_start
      ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
    ) AS last_capacity_increase_month
  FROM cvrs_calc
),
graced AS (
  SELECT
    *,
    DATE_DIFF(month_start, last_capacity_increase_month, MONTH)
      AS months_since_capacity_increase,
    -- Grace window = the increase month plus the one after it (2 months total).
    -- A utilization dip immediately after buying more capacity is expected
    -- behaviour, not deterioration, so the relative-drop rule is suspended.
    -- The absolute floor (CVRS < 40) is NEVER suspended.
    COALESCE(
      DATE_DIFF(month_start, last_capacity_increase_month, MONTH) <= 1, FALSE
    ) AS in_expansion_grace
  FROM flagged
)
SELECT
  f.cust_id,
  cu.cust_name,
  cu.region,
  cu.segment AS customer_segment,
  ca.cohort AS ground_truth_cohort,
  f.month_start,
  f.entitled_units,
  f.consumed_units,
  f.raw_utilization,
  f.utilization,
  f.feature_depth,
  f.consistency,
  f.breadth,
  f.cvrs,
  f.expansion_flag,
  f.cumulative_consumed,
  t.ttfv_days,
  f.trailing_avg_cvrs,
  f.months_since_capacity_increase,
  f.in_expansion_grace,
  CASE
    WHEN f.consumed_units = 0 AND f.cumulative_consumed = 0 THEN 'Shelfware'
    -- Absolute distress always registers, grace period or not.
    WHEN f.cvrs < 40 THEN 'At Risk'
    -- Relative drop is suspended while new capacity is being absorbed.
    WHEN f.trailing_avg_cvrs IS NOT NULL
     AND f.trailing_avg_cvrs - f.cvrs >= 25
     AND NOT f.in_expansion_grace THEN 'At Risk'
    WHEN f.cvrs >= 70 THEN 'Healthy'
    ELSE 'Watch'
  END AS health_tier,
  -- Revenue impact (assumed list pricing). Separate from the score by design;
  -- no revenue column is ever an input to CVRS.
  COALESCE(rev.committed_mrr, 0) AS committed_mrr,
  ROUND(COALESCE(rev.committed_mrr, 0) * 12, 2) AS committed_arr,
  COALESCE(rev.overage_mrr, 0) AS overage_mrr,
  ROUND(COALESCE(rev.overage_mrr, 0) * 12, 2) AS overage_arr_run_rate
FROM graced f
JOIN ${DATASET}.stg_customers cu
  ON cu.cust_id = f.cust_id
LEFT JOIN ${DATASET}.cohort_assignments ca
  ON ca.cust_id = f.cust_id
LEFT JOIN revenue rev
  ON rev.cust_id = f.cust_id AND rev.month_start = f.month_start
LEFT JOIN ttfv t
  ON t.cust_id = f.cust_id;
