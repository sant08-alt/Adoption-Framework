"""Suite A: local data-quality tests against the generated CSVs.

No cloud dependencies — validates structure, integrity, and that every
injected anomaly cohort actually behaves as specified (ground truth for
the metric-detection tests in Suite B).

Run: pytest pipeline_and_tests/tests/test_raw_data_quality.py
"""

from pathlib import Path

import pandas as pd
import pytest

OUTPUT_DIR = Path(__file__).parents[2] / "data_generation" / "output"


@pytest.fixture(scope="module")
def tables():
    if not OUTPUT_DIR.exists():
        pytest.skip("No generated data. Run data_generation/generate_data.py first.")
    names = ["customers", "products", "features", "entitlements",
             "consumption", "product_adoption", "feature_adoption",
             "month_spine", "cohort_assignments"]
    loaded = {n: pd.read_csv(OUTPUT_DIR / f"{n}.csv") for n in names}
    for name, cols in {
        "entitlements": ["start_date", "end_date"],
        "consumption": ["usage_month"],
        "product_adoption": ["first_adoption_date"],
        "feature_adoption": ["usage_month", "adoption_date"],
        "month_spine": ["month_start", "month_end"],
    }.items():
        for col in cols:
            loaded[name][col] = pd.to_datetime(loaded[name][col])
    return loaded


def cohort_custs(tables, cohort):
    ca = tables["cohort_assignments"]
    return set(ca.loc[ca["cohort"] == cohort, "cust_id"])


# --- structure and volumes -------------------------------------------------

def test_row_counts(tables):
    assert len(tables["customers"]) == 100
    assert len(tables["products"]) == 500
    assert 1500 <= len(tables["features"]) <= 3000
    assert 450 <= len(tables["entitlements"]) <= 600
    assert len(tables["month_spine"]) == 12
    assert len(tables["cohort_assignments"]) == 100


def test_primary_keys_unique(tables):
    for name, pk in [("customers", "cust_id"), ("products", "product_id"),
                     ("features", "feature_id"), ("entitlements", "entitlement_id")]:
        assert tables[name][pk].is_unique, f"duplicate {pk} in {name}"
    dup = tables["consumption"].duplicated(["entitlement_id", "usage_month"]).sum()
    assert dup == 0, f"{dup} duplicate entitlement-month rows in consumption"


def test_product_adoption_after_contract_start(tables):
    # A customer can never adopt a product before the contract for it starts.
    starts = (tables["entitlements"].groupby(["cust_id", "product_id"])["start_date"]
              .min().rename("earliest_start"))
    pa = tables["product_adoption"].merge(starts, on=["cust_id", "product_id"], how="left")
    assert pa["earliest_start"].notna().all(), "adoption row without a matching entitlement"
    early = pa[pa["first_adoption_date"] < pa["earliest_start"]]
    assert early.empty, f"{len(early)} adoptions dated before contract start"


def test_shelfware_has_no_adoption(tables):
    shelf = cohort_custs(tables, "shelfware")
    offenders = set(tables["product_adoption"]["cust_id"]) & shelf
    assert not offenders, f"shelfware accounts with an adoption date: {offenders}"


def test_foreign_keys_resolve(tables):
    custs = set(tables["customers"]["cust_id"])
    prods = set(tables["products"]["product_id"])
    ents = set(tables["entitlements"]["entitlement_id"])
    feats = set(tables["features"]["feature_id"])
    assert set(tables["product_adoption"]["cust_id"]) <= custs
    assert set(tables["product_adoption"]["product_id"]) <= prods

    assert set(tables["entitlements"]["cust_id"]) <= custs
    assert set(tables["entitlements"]["product_id"]) <= prods
    assert set(tables["features"]["product_id"]) <= prods
    assert set(tables["consumption"]["entitlement_id"]) <= ents
    assert set(tables["consumption"]["cust_id"]) <= custs
    assert set(tables["feature_adoption"]["feature_id"]) <= feats
    assert set(tables["feature_adoption"]["cust_id"]) <= custs


def test_no_negative_consumption(tables):
    assert (tables["consumption"]["consumed_units"] >= 0).all()


def test_entitlement_dates_sane(tables):
    ents = tables["entitlements"]
    assert (ents["start_date"] < ents["end_date"]).all()


def test_consumption_within_entitlement_window(tables):
    merged = tables["consumption"].merge(
        tables["entitlements"][["entitlement_id", "start_date", "end_date"]],
        on="entitlement_id",
    )
    month_ends = merged["usage_month"] + pd.offsets.MonthEnd(0)
    in_window = (merged["start_date"] <= month_ends) & (merged["end_date"] >= merged["usage_month"])
    assert in_window.all(), f"{(~in_window).sum()} consumption rows outside entitlement window"


def test_month_spine_coverage(tables):
    spine = tables["month_spine"]
    assert spine["month_start"].min() == pd.Timestamp("2025-07-01")
    assert spine["month_start"].max() == pd.Timestamp("2026-06-01")
    assert spine["month_start"].dt.to_period("M").nunique() == 12


# --- cohort ground truth ---------------------------------------------------

def test_cohort_proportions(tables):
    counts = tables["cohort_assignments"]["cohort"].value_counts()
    assert counts["spike_drop"] == 5
    assert counts["shelfware"] == 10
    assert counts["overage"] == 15
    assert counts["expansion"] == 9


def test_shelfware_has_no_consumption(tables):
    shelf = cohort_custs(tables, "shelfware")
    offenders = set(tables["consumption"]["cust_id"]) & shelf
    assert not offenders, f"shelfware accounts with consumption: {offenders}"
    adopted = set(tables["feature_adoption"]["cust_id"]) & shelf
    assert not adopted, f"shelfware accounts with feature adoption: {adopted}"


def test_overage_exceeds_entitlement_most_months(tables):
    over = cohort_custs(tables, "overage")
    merged = tables["consumption"].merge(
        tables["entitlements"][["entitlement_id", "licensed_amount"]], on="entitlement_id")
    monthly = (merged[merged["cust_id"].isin(over)]
               .groupby(["cust_id", "usage_month"])[["consumed_units", "licensed_amount"]]
               .sum())
    ratio = monthly["consumed_units"] / monthly["licensed_amount"]
    months_over = ratio.ge(1.2).groupby("cust_id").sum()
    assert (months_over >= 10).all(), (
        f"overage custs below 10 months of 120%+: {months_over[months_over < 10].to_dict()}")


def test_expansion_has_overlapping_entitlements(tables):
    exp = cohort_custs(tables, "expansion")
    ents = tables["entitlements"]
    for cust in exp:
        ce = ents[ents["cust_id"] == cust].sort_values("start_date")
        starts = ce["start_date"].unique()
        assert len(starts) >= 2, f"{cust} has no second contract"
        # Later contract starts before an earlier one ends -> overlap.
        overlap = any(
            (row.start_date > ce["start_date"].min()) and (row.start_date < ce["end_date"].max())
            for row in ce.itertuples()
        )
        assert overlap, f"{cust} contracts do not overlap"


def test_spike_drop_burns_early_then_goes_dark(tables):
    spike = cohort_custs(tables, "spike_drop")
    cons = tables["consumption"]
    for cust in spike:
        cc = cons[cons["cust_id"] == cust].sort_values("usage_month")
        months = sorted(cc["usage_month"].unique())
        assert len(months) >= 6, f"{cust} has too few months to evaluate"
        first3 = cc[cc["usage_month"].isin(months[:3])]["consumed_units"].sum()
        rest = cc[cc["usage_month"].isin(months[3:])]["consumed_units"].sum()
        assert first3 > 0 and rest == 0, (
            f"{cust}: expected all consumption in first 3 months (first3={first3}, rest={rest})")
