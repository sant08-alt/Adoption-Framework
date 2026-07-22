-- Active entitled capacity per customer-product-month.
-- Date-range join against the month spine: overlapping (mid-year expansion)
-- contracts SUM into the denominator for the months they overlap, which is
-- the structural fix for stacked entitlements.

CREATE OR REPLACE TABLE ${DATASET}.int_monthly_entitlements AS
SELECT
  e.cust_id,
  e.product_id,
  s.month_start,
  SUM(e.licensed_amount) AS active_entitled_units,
  COUNT(DISTINCT e.entitlement_id) AS active_entitlement_count,
  MIN(e.start_date) AS earliest_start_date
FROM ${DATASET}.stg_entitlements e
JOIN ${DATASET}.stg_month_spine s
  ON e.start_date <= s.month_end
 AND e.end_date >= s.month_start
GROUP BY 1, 2, 3;
