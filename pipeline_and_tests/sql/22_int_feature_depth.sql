-- Feature adoption depth per customer-SKU-month:
-- eligible = features of products with an active entitlement that month,
-- active   = feature_adoption rows flagged monthly_active.

CREATE OR REPLACE TABLE ${DATASET}.int_feature_depth AS
WITH eligible AS (
  SELECT
    me.cust_id,
    me.product_id,
    me.month_start,
    f.feature_id
  FROM ${DATASET}.int_monthly_entitlements me
  JOIN ${DATASET}.stg_features f
    ON f.product_id = me.product_id
),
active AS (
  SELECT DISTINCT
    fa.cust_id,
    f.product_id,
    fa.usage_month AS month_start,
    fa.feature_id
  FROM ${DATASET}.stg_feature_adoption fa
  JOIN ${DATASET}.stg_features f
    ON f.feature_id = fa.feature_id
  WHERE fa.monthly_active
)
SELECT
  e.cust_id,
  e.product_id,
  e.month_start,
  COUNT(DISTINCT e.feature_id) AS eligible_features,
  COUNT(DISTINCT a.feature_id) AS active_features,
  COALESCE(SAFE_DIVIDE(COUNT(DISTINCT a.feature_id), COUNT(DISTINCT e.feature_id)), 0) AS feature_depth
FROM eligible e
LEFT JOIN active a
  ON a.cust_id = e.cust_id
 AND a.product_id = e.product_id
 AND a.month_start = e.month_start
 AND a.feature_id = e.feature_id
GROUP BY 1, 2, 3;
