-- Customer-month utilization. Built FROM entitlements (left side), so
-- shelfware accounts appear every entitled month with consumed_units = 0
-- rather than silently dropping out of reporting.

CREATE OR REPLACE TABLE ${DATASET}.int_monthly_utilization AS
WITH entitled AS (
  SELECT
    cust_id,
    month_start,
    SUM(active_entitled_units) AS entitled_units
  FROM ${DATASET}.int_monthly_entitlements
  GROUP BY 1, 2
),
consumed AS (
  SELECT
    cust_id,
    usage_month AS month_start,
    SUM(consumed_units) AS consumed_units
  FROM ${DATASET}.stg_consumption
  GROUP BY 1, 2
)
SELECT
  entitled.cust_id,
  entitled.month_start,
  entitled.entitled_units,
  COALESCE(consumed.consumed_units, 0) AS consumed_units,
  SAFE_DIVIDE(COALESCE(consumed.consumed_units, 0), entitled.entitled_units) AS raw_utilization,
  LEAST(SAFE_DIVIDE(COALESCE(consumed.consumed_units, 0), entitled.entitled_units), 1.0) AS utilization
FROM entitled
LEFT JOIN consumed
  ON entitled.cust_id = consumed.cust_id
 AND entitled.month_start = consumed.month_start;
