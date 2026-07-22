-- Staging layer: typed, deduplicated views over the raw loaded tables.
-- ${DATASET} is replaced by run_pipeline.py with project.dataset.

CREATE OR REPLACE VIEW ${DATASET}.stg_customers AS
SELECT DISTINCT
  CAST(cust_id AS STRING) AS cust_id,
  TRIM(cust_name) AS cust_name,
  region,
  segment
FROM ${DATASET}.customers;

CREATE OR REPLACE VIEW ${DATASET}.stg_products AS
SELECT DISTINCT
  CAST(product_id AS STRING) AS product_id,
  TRIM(product_name) AS product_name,
  product_platform,
  CAST(unit_price AS FLOAT64) AS unit_price
FROM ${DATASET}.products;

CREATE OR REPLACE VIEW ${DATASET}.stg_features AS
SELECT DISTINCT
  CAST(feature_id AS STRING) AS feature_id,
  TRIM(feature_name) AS feature_name,
  feature_description,
  CAST(product_id AS STRING) AS product_id
FROM ${DATASET}.features;

CREATE OR REPLACE VIEW ${DATASET}.stg_entitlements AS
SELECT DISTINCT
  CAST(entitlement_id AS STRING) AS entitlement_id,
  CAST(product_id AS STRING) AS product_id,
  CAST(cust_id AS STRING) AS cust_id,
  CAST(units_purchased AS INT64) AS units_purchased,
  CAST(licensed_amount AS INT64) AS licensed_amount,
  CAST(start_date AS DATE) AS start_date,
  CAST(end_date AS DATE) AS end_date
FROM ${DATASET}.entitlements;

CREATE OR REPLACE VIEW ${DATASET}.stg_consumption AS
SELECT
  CAST(cust_id AS STRING) AS cust_id,
  CAST(entitlement_id AS STRING) AS entitlement_id,
  CAST(usage_month AS DATE) AS usage_month,
  CAST(consumed_units AS INT64) AS consumed_units
FROM ${DATASET}.consumption;

CREATE OR REPLACE VIEW ${DATASET}.stg_product_adoption AS
SELECT DISTINCT
  CAST(cust_id AS STRING) AS cust_id,
  CAST(product_id AS STRING) AS product_id,
  CAST(first_adoption_date AS DATE) AS first_adoption_date
FROM ${DATASET}.product_adoption;

CREATE OR REPLACE VIEW ${DATASET}.stg_feature_adoption AS
SELECT
  CAST(cust_id AS STRING) AS cust_id,
  CAST(feature_id AS STRING) AS feature_id,
  CAST(usage_month AS DATE) AS usage_month,
  CAST(monthly_active AS BOOL) AS monthly_active,
  CAST(adoption_date AS DATE) AS adoption_date
FROM ${DATASET}.feature_adoption;

CREATE OR REPLACE VIEW ${DATASET}.stg_month_spine AS
SELECT
  CAST(month_start AS DATE) AS month_start,
  CAST(month_end AS DATE) AS month_end
FROM ${DATASET}.month_spine;
