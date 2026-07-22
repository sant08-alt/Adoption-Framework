-- Feature-level adoption per month: adoption rate across eligible customers
-- and median days from entitlement start to first adoption.

CREATE OR REPLACE TABLE ${DATASET}.mart_feature_adoption AS
WITH eligible AS (
  SELECT
    me.month_start,
    f.feature_id,
    COUNT(DISTINCT me.cust_id) AS eligible_customers
  FROM ${DATASET}.int_monthly_entitlements me
  JOIN ${DATASET}.stg_features f
    ON f.product_id = me.product_id
  GROUP BY 1, 2
),
active AS (
  SELECT
    usage_month AS month_start,
    feature_id,
    COUNT(DISTINCT cust_id) AS active_customers
  FROM ${DATASET}.stg_feature_adoption
  WHERE monthly_active
  GROUP BY 1, 2
),
first_starts AS (
  SELECT
    cust_id,
    product_id,
    MIN(start_date) AS first_start
  FROM ${DATASET}.stg_entitlements
  GROUP BY 1, 2
),
adopt AS (
  SELECT
    fa.feature_id,
    -- Anchor to the start of the entitlement's first month (see TTFV note in
    -- 30_mart_customer_health.sql) so a mid-month contract start cannot yield
    -- a negative time-to-adopt. Always non-negative.
    APPROX_QUANTILES(DATE_DIFF(fa.adoption_date, DATE_TRUNC(fs.first_start, MONTH), DAY), 100)[OFFSET(50)] AS median_days_to_adopt
  FROM (
    SELECT cust_id, feature_id, MIN(adoption_date) AS adoption_date
    FROM ${DATASET}.stg_feature_adoption
    GROUP BY 1, 2
  ) fa
  JOIN ${DATASET}.stg_features f
    ON f.feature_id = fa.feature_id
  JOIN first_starts fs
    ON fs.cust_id = fa.cust_id
   AND fs.product_id = f.product_id
  GROUP BY 1
)
SELECT
  e.month_start,
  e.feature_id,
  f.feature_name,
  f.product_id,
  p.product_name,
  p.product_platform,
  e.eligible_customers,
  COALESCE(a.active_customers, 0) AS active_customers,
  SAFE_DIVIDE(COALESCE(a.active_customers, 0), e.eligible_customers) AS adoption_rate,
  ad.median_days_to_adopt
FROM eligible e
LEFT JOIN active a
  ON a.feature_id = e.feature_id AND a.month_start = e.month_start
JOIN ${DATASET}.stg_features f
  ON f.feature_id = e.feature_id
JOIN ${DATASET}.stg_products p
  ON p.product_id = f.product_id
LEFT JOIN adopt ad
  ON ad.feature_id = e.feature_id;
