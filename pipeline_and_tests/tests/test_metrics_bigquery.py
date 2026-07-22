"""Suite B: metric-correctness tests against the BigQuery marts.

Validates the framework's own success criteria (specs/01_framework_spec.md §6):
injected anomaly cohorts must be detected by the metrics. Skips with
instructions when BigQuery is not configured or reachable.

Run (after the pipeline): pytest pipeline_and_tests/tests/test_metrics_bigquery.py
"""

import os
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[2]


@pytest.fixture(scope="module")
def bq():
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
    project = os.getenv("GCP_PROJECT_ID")
    dataset = os.getenv("BQ_DATASET", "adoption_analytics")
    if not project or project == "your-gcp-project-id":
        pytest.skip("BigQuery not configured: copy .env.example to .env and set GCP_PROJECT_ID")
    try:
        from google.cloud import bigquery
        client = bigquery.Client(project=project)
        client.query("SELECT 1").result()
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"BigQuery unreachable ({exc}). Run: gcloud auth application-default login")
    return client, f"{project}.{dataset}"


def query_df(bq, sql):
    client, dataset = bq
    return client.query(sql.replace("${DATASET}", f"`{dataset}`")).to_dataframe()


# --- bounds ----------------------------------------------------------------

def test_cvrs_bounded(bq):
    df = query_df(bq, """
        SELECT COUNT(*) AS bad FROM ${DATASET}.mart_customer_health
        WHERE cvrs < 0 OR cvrs > 100
    """)
    assert df["bad"][0] == 0


def test_components_bounded(bq):
    df = query_df(bq, """
        SELECT COUNT(*) AS bad FROM ${DATASET}.mart_customer_health
        WHERE utilization < 0 OR utilization > 1
           OR feature_depth < 0 OR feature_depth > 1
           OR consistency < 0 OR consistency > 1
           OR breadth < 0 OR breadth > 1
    """)
    assert df["bad"][0] == 0


def test_no_customer_month_dropped(bq):
    # Every customer with an active entitlement in a month must appear in the
    # health mart that month (shelfware can never vanish from reporting).
    df = query_df(bq, """
        WITH expected AS (
          SELECT DISTINCT cust_id, month_start FROM ${DATASET}.int_monthly_entitlements
        )
        SELECT COUNT(*) AS missing
        FROM expected e
        LEFT JOIN ${DATASET}.mart_customer_health h
          ON h.cust_id = e.cust_id AND h.month_start = e.month_start
        WHERE h.cust_id IS NULL
    """)
    assert df["missing"][0] == 0


# --- ground-truth cohort detection -----------------------------------------

def test_shelfware_detected(bq):
    df = query_df(bq, """
        SELECT
          COUNTIF(health_tier = 'Shelfware') / COUNT(*) AS hit_rate
        FROM ${DATASET}.mart_customer_health
        WHERE ground_truth_cohort = 'shelfware'
    """)
    assert df["hit_rate"][0] >= 0.90, f"shelfware detection {df['hit_rate'][0]:.0%} < 90%"


def test_spike_drop_score_collapses(bq):
    # By each spike-drop account's 5th month, CVRS must sit >= 25 points
    # below its months-1-3 peak.
    df = query_df(bq, """
        WITH ranked AS (
          SELECT cust_id, cvrs,
                 ROW_NUMBER() OVER (PARTITION BY cust_id ORDER BY month_start) AS rn
          FROM ${DATASET}.mart_customer_health
          WHERE ground_truth_cohort = 'spike_drop'
        )
        SELECT cust_id,
               MAX(IF(rn <= 3, cvrs, NULL)) AS peak_early,
               MAX(IF(rn = 5, cvrs, NULL)) AS month5
        FROM ranked GROUP BY cust_id
    """)
    bad = df[df["peak_early"] - df["month5"] < 25]
    assert bad.empty, f"spike-drop accounts whose score did not collapse: {bad.to_dict('records')}"


def test_overage_flagged_not_inflated(bq):
    df = query_df(bq, """
        SELECT cust_id,
               COUNTIF(expansion_flag) AS flagged_months,
               MAX(utilization) AS max_capped_util
        FROM ${DATASET}.mart_customer_health
        WHERE ground_truth_cohort = 'overage'
        GROUP BY cust_id
    """)
    unflagged = df[df["flagged_months"] == 0]
    assert unflagged.empty, f"overage accounts never flagged: {unflagged['cust_id'].tolist()}"
    assert (df["max_capped_util"] <= 1.0).all(), "capped utilization exceeded 1.0"


def test_sku_coverage_totals_reconcile(bq):
    df = query_df(bq, """
        SELECT SUM(catalog_skus) AS catalog, SUM(sold_skus) AS sold,
               SUM(unsold_skus) AS unsold,
               COUNTIF(sold_skus + unsold_skus != catalog_skus) AS mismatch
        FROM ${DATASET}.mart_sku_coverage
    """)
    assert df["catalog"][0] == 500, "catalog is not the full 500-SKU product table"
    assert df["sold"][0] + df["unsold"][0] == df["catalog"][0], "sold + unsold != catalog"
    assert df["mismatch"][0] == 0, "per-platform sold + unsold != catalog"
    assert df["unsold"][0] > 0, "no unsold SKUs surfaced"


def test_mtd_projected_cvrs_bounded(bq):
    df = query_df(bq, """
        SELECT COUNTIF(projected_cvrs < 0 OR projected_cvrs > 100) AS bad_cvrs,
               COUNTIF(mtd_utilization < 0 OR mtd_utilization > 1) AS bad_util
        FROM ${DATASET}.mart_customer_health_mtd
    """)
    assert df["bad_cvrs"][0] == 0, "projected_cvrs out of [0,100]"
    assert df["bad_util"][0] == 0, "mtd_utilization out of [0,1]"


def test_mtd_surfaces_decelerating_accounts(bq):
    # The whole point of the MTD view: accounts pacing well below their own
    # baseline must be detectable now, not at month end.
    df = query_df(bq, """
        SELECT COUNTIF(momentum <= -0.30) AS decelerating,
               COUNTIF(momentum IS NOT NULL) AS with_history
        FROM ${DATASET}.mart_customer_health_mtd
    """)
    assert df["decelerating"][0] >= 3, "no decelerating accounts surfaced by momentum"
    assert df["with_history"][0] > 0


def test_mtd_pacing_is_not_artificially_low(bq):
    # A steady account (|momentum| small) must project close to its last final
    # score - proving the elapsed-fraction pacing works and mid-month isn't
    # uniformly depressed.
    df = query_df(bq, """
        SELECT COUNTIF(ABS(projected_cvrs - last_final_cvrs) > 20) AS drifted,
               COUNT(*) AS steady
        FROM ${DATASET}.mart_customer_health_mtd
        WHERE ABS(momentum) < 0.10 AND last_final_cvrs IS NOT NULL
    """)
    assert df["steady"][0] > 0, "no steady accounts to check"
    assert df["drifted"][0] == 0, "steady accounts drifted far from their last final score"


def test_time_metrics_never_negative(bq):
    # Value cannot be realized before a contract starts. Monthly consumption
    # grain vs daily start_date must not produce negative TTFV / time-to-adopt.
    health = query_df(bq, """
        SELECT COUNTIF(ttfv_days < 0) AS bad
        FROM ${DATASET}.mart_customer_health WHERE ttfv_days IS NOT NULL
    """)
    assert health["bad"][0] == 0, "negative TTFV found"
    feat = query_df(bq, """
        SELECT COUNTIF(median_days_to_adopt < 0) AS bad
        FROM ${DATASET}.mart_feature_adoption WHERE median_days_to_adopt IS NOT NULL
    """)
    assert feat["bad"][0] == 0, "negative median_days_to_adopt found"


def test_revenue_never_negative_and_arr_consistent(bq):
    df = query_df(bq, """
        SELECT
          COUNTIF(committed_mrr < 0 OR overage_mrr < 0) AS negative_revenue,
          COUNTIF(ABS(committed_arr - committed_mrr * 12) > 0.01) AS arr_mismatch
        FROM ${DATASET}.mart_customer_health
    """)
    assert df["negative_revenue"][0] == 0, "negative revenue found"
    assert df["arr_mismatch"][0] == 0, "committed_arr != committed_mrr * 12"


def test_overage_revenue_only_when_sku_over_entitlement(bq):
    # A SKU can only bill overage if it actually consumed beyond its entitlement.
    df = query_df(bq, """
        SELECT COUNT(*) AS phantom
        FROM ${DATASET}.mart_sku_adoption
        WHERE overage_mrr > 0 AND raw_utilization <= 1.0
    """)
    assert df["phantom"][0] == 0, "overage revenue billed without over-consumption"


def test_cvrs_is_pure_function_of_its_four_components(bq):
    # Guardrail for the core design rule: revenue (or anything else) must not
    # enter the score. CVRS must reconstruct exactly from the four components,
    # proving the formula depends on nothing else.
    df = query_df(bq, """
        SELECT COUNT(*) AS mismatches
        FROM ${DATASET}.mart_customer_health
        WHERE ABS(cvrs - ROUND(100 * (0.40 * utilization
                                    + 0.30 * feature_depth
                                    + 0.20 * consistency
                                    + 0.10 * breadth), 1)) > 0.05
    """)
    assert df["mismatches"][0] == 0, "CVRS is not a pure function of its four components"


def test_expansion_grace_window_is_exactly_two_months(bq):
    # Grace covers the capacity-increase month plus the one after it, never more.
    df = query_df(bq, """
        SELECT
          COUNTIF(in_expansion_grace AND months_since_capacity_increase > 1) AS too_long,
          COUNTIF(in_expansion_grace AND months_since_capacity_increase IS NULL) AS ungrounded,
          COUNTIF(NOT in_expansion_grace
                  AND months_since_capacity_increase BETWEEN 0 AND 1) AS missed
        FROM ${DATASET}.mart_customer_health
    """)
    assert df["too_long"][0] == 0, "grace extended beyond 2 months"
    assert df["ungrounded"][0] == 0, "grace applied without a capacity increase"
    assert df["missed"][0] == 0, "grace not applied inside the 2-month window"


def test_expansion_dip_does_not_trigger_false_at_risk(bq):
    # A utilization dip caused by new capacity landing must not read as churn
    # while the customer is still healthy in absolute terms (CVRS >= 40).
    df = query_df(bq, """
        SELECT cust_id, month_start, cvrs, trailing_avg_cvrs
        FROM ${DATASET}.mart_customer_health
        WHERE ground_truth_cohort = 'expansion'
          AND in_expansion_grace
          AND health_tier = 'At Risk'
          AND cvrs >= 40
    """)
    assert df.empty, f"false At Risk during expansion grace: {df.to_dict('records')}"


def test_absolute_floor_survives_grace_period(bq):
    # The grace period must never rescue genuinely distressed accounts.
    df = query_df(bq, """
        SELECT COUNT(*) AS bad
        FROM ${DATASET}.mart_customer_health
        WHERE cvrs < 40
          AND health_tier NOT IN ('At Risk', 'Shelfware')
    """)
    assert df["bad"][0] == 0, "CVRS < 40 escaped At Risk classification"


def test_expansion_raises_denominator_without_artifacts(bq):
    # The entitled denominator must strictly increase in some month (the
    # second contract landing), and stacked contracts alone must not push
    # capped utilization past 1.0.
    df = query_df(bq, """
        WITH deltas AS (
          SELECT cust_id,
                 entitled_units - LAG(entitled_units) OVER (
                   PARTITION BY cust_id ORDER BY month_start) AS delta,
                 utilization
          FROM ${DATASET}.mart_customer_health
          WHERE ground_truth_cohort = 'expansion'
        )
        SELECT cust_id,
               COUNTIF(delta > 0) AS increase_months,
               MAX(utilization) AS max_util
        FROM deltas GROUP BY cust_id
    """)
    no_step = df[df["increase_months"] == 0]
    assert no_step.empty, f"expansion accounts without a denominator step-up: {no_step['cust_id'].tolist()}"
    assert (df["max_util"] <= 1.0).all()
