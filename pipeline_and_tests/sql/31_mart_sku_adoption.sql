-- SKU-level adoption per customer-product-month: utilization, feature depth,
-- and deployment status.

CREATE OR REPLACE TABLE ${DATASET}.mart_sku_adoption AS
WITH product_usage AS (
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
joined AS (
  SELECT
    me.cust_id,
    me.product_id,
    me.month_start,
    me.active_entitled_units,
    me.active_entitlement_count,
    COALESCE(pu.consumed_units, 0) AS consumed_units,
    SAFE_DIVIDE(COALESCE(pu.consumed_units, 0), me.active_entitled_units) AS raw_utilization,
    LEAST(SAFE_DIVIDE(COALESCE(pu.consumed_units, 0), me.active_entitled_units), 1.0) AS utilization,
    COALESCE(fd.eligible_features, 0) AS eligible_features,
    COALESCE(fd.active_features, 0) AS active_features,
    COALESCE(fd.feature_depth, 0) AS feature_depth,
    SUM(COALESCE(pu.consumed_units, 0)) OVER (
      PARTITION BY me.cust_id, me.product_id ORDER BY me.month_start
    ) AS cumulative_consumed
  FROM ${DATASET}.int_monthly_entitlements me
  LEFT JOIN product_usage pu
    ON pu.cust_id = me.cust_id
   AND pu.product_id = me.product_id
   AND pu.month_start = me.month_start
  LEFT JOIN ${DATASET}.int_feature_depth fd
    ON fd.cust_id = me.cust_id
   AND fd.product_id = me.product_id
   AND fd.month_start = me.month_start
)
SELECT
  j.cust_id,
  cu.cust_name,
  cu.region,
  cu.segment AS customer_segment,
  j.product_id,
  p.product_name,
  p.product_platform,
  j.month_start,
  j.active_entitled_units,
  j.active_entitlement_count,
  j.consumed_units,
  j.raw_utilization,
  j.utilization,
  j.eligible_features,
  j.active_features,
  j.feature_depth,
  CASE
    WHEN j.cumulative_consumed = 0 THEN 'Not Deployed'
    WHEN j.consumed_units > 0 THEN 'Deployed'
    ELSE 'Dormant'
  END AS deployment_status,
  -- First adoption date for this customer-product and the day-level lag from
  -- the contract's start to that first use.
  pa.first_adoption_date,
  DATE_DIFF(pa.first_adoption_date, es.earliest_start, DAY) AS days_to_first_value,
  -- Revenue impact (assumed list pricing). These are reporting outputs only;
  -- no revenue column is ever an input to CVRS.
  p.unit_price,
  ROUND(j.active_entitled_units * p.unit_price, 2) AS committed_mrr,
  ROUND(GREATEST(j.consumed_units - j.active_entitled_units, 0) * p.unit_price, 2) AS overage_mrr
FROM joined j
JOIN ${DATASET}.stg_customers cu
  ON cu.cust_id = j.cust_id
JOIN ${DATASET}.stg_products p
  ON p.product_id = j.product_id
LEFT JOIN ${DATASET}.stg_product_adoption pa
  ON pa.cust_id = j.cust_id AND pa.product_id = j.product_id
LEFT JOIN (
  SELECT cust_id, product_id, MIN(start_date) AS earliest_start
  FROM ${DATASET}.stg_entitlements GROUP BY 1, 2
) es
  ON es.cust_id = j.cust_id AND es.product_id = j.product_id;
