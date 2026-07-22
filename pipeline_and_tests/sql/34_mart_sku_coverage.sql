-- SKU catalog coverage: how much of the product catalog has actually been sold.
-- A SKU (product) is "unsold" if no customer holds an entitlement for it.
-- Grain: product_platform (sum across rows = portfolio totals).

CREATE OR REPLACE TABLE ${DATASET}.mart_sku_coverage AS
WITH sold AS (
  SELECT DISTINCT product_id FROM ${DATASET}.stg_entitlements
)
SELECT
  p.product_platform,
  COUNT(*) AS catalog_skus,
  COUNTIF(s.product_id IS NOT NULL) AS sold_skus,
  COUNTIF(s.product_id IS NULL) AS unsold_skus,
  ROUND(SAFE_DIVIDE(COUNTIF(s.product_id IS NULL), COUNT(*)), 3) AS unsold_pct
FROM ${DATASET}.stg_products p
LEFT JOIN sold s ON s.product_id = p.product_id
GROUP BY 1
ORDER BY unsold_skus DESC;
