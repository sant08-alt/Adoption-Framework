"""Synthetic B2B SaaS adoption dataset generator.

Implements specs/02_data_spec.md: 12 months of history (2025-07 .. 2026-06),
six relational tables plus a month spine, with labeled anomaly cohorts
(spike_drop, shelfware, overage, expansion) written to cohort_assignments.csv
as ground truth for downstream metric validation.

Run: python data_generation/generate_data.py
Outputs CSVs to data_generation/output/.
"""

import random
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
from faker import Faker

SEED = 42
N_CUSTOMERS = 100
N_PRODUCTS = 500
N_ENTITLEMENTS_TARGET = 500
MONTHS = pd.date_range("2025-07-01", periods=12, freq="MS").date

# In-flight current month for the month-to-date (projected) view. Fixed and
# seeded so the demo is deterministic; in production this is simply "today".
CURRENT_MONTH_START = date(2026, 7, 1)
AS_OF_DATE = date(2026, 7, 15)
TRAILING_MONTHS = [date(2026, 4, 1), date(2026, 5, 1), date(2026, 6, 1)]

COHORT_SIZES = {"spike_drop": 5, "shelfware": 10, "overage": 15, "expansion": 9}

PLATFORMS = [
    "Endpoint Security",
    "Cloud Security",
    "SIEM & Analytics",
    "Identity Protection",
    "Network Security",
]
CAPABILITY_WORDS = [
    "Threat Hunting", "Ransomware Rollback", "Device Control", "Posture Scan",
    "Anomaly Detection", "Zero Trust Access", "Credential Guard", "Log Forwarding",
    "Behavioral Analytics", "Attack Surface Map", "Sandbox Detonation", "Policy Engine",
    "Auto Remediation", "Exposure Scoring", "Session Recording", "API Shield",
    "Container Scan", "Firewall Orchestration", "Phishing Triage", "Data Loss Guard",
]

OUTPUT_DIR = Path(__file__).parent / "output"

# Assumed list price per licensed unit per month, by platform. Higher-value
# platforms (SIEM, Identity) price above commodity endpoint coverage. Applied
# deterministically (no RNG draw) so adding pricing does not perturb any other
# generated value.
PLATFORM_PRICES = {
    "Endpoint Security": 1.50,
    "Cloud Security": 3.00,
    "SIEM & Analytics": 6.00,
    "Identity Protection": 4.00,
    "Network Security": 2.50,
}


def unit_price_for(platform: str, product_id: str) -> float:
    """Deterministic per-unit monthly price: platform base +/- up to 10% jitter
    derived from the product number (stable, never touches the RNG stream)."""
    base = PLATFORM_PRICES[platform]
    n = int(product_id.split("-")[1])
    jitter = 1 + ((n * 37) % 21 - 10) / 100.0  # -10%..+10%, deterministic
    return round(base * jitter, 2)


def month_end(month_start: date) -> date:
    return (pd.Timestamp(month_start) + pd.offsets.MonthEnd(0)).date()


def build_customers(fake: Faker) -> pd.DataFrame:
    rows = []
    for i in range(1, N_CUSTOMERS + 1):
        rows.append({
            "cust_id": f"CUST-{i:04d}",
            "cust_name": fake.unique.company(),
            "region": random.choices(["AMER", "EMEA", "APJ"], weights=[50, 30, 20])[0],
            "segment": random.choices(["Enterprise", "Mid-Market"], weights=[40, 60])[0],
        })
    return pd.DataFrame(rows)


def build_products() -> pd.DataFrame:
    rows = []
    used_names = set()
    for i in range(1, N_PRODUCTS + 1):
        platform = random.choice(PLATFORMS)
        while True:
            name = f"{platform.split(' ')[0]} {random.choice(CAPABILITY_WORDS)} {random.choice(['Pro', 'Advanced', 'Core', 'Elite', 'Base'])} {i}"
            if name not in used_names:
                used_names.add(name)
                break
        product_id = f"PROD-{i:04d}"
        rows.append({
            "product_id": product_id,
            "product_name": name,
            "product_platform": platform,
            "unit_price": unit_price_for(platform, product_id),
        })
    return pd.DataFrame(rows)


def build_features(products: pd.DataFrame, fake: Faker) -> pd.DataFrame:
    rows = []
    feature_counter = 1
    for product_id in products["product_id"]:
        for _ in range(random.randint(3, 6)):
            rows.append({
                "feature_id": f"FEAT-{feature_counter:05d}",
                "feature_name": f"{random.choice(CAPABILITY_WORDS)} {random.choice(['Module', 'Engine', 'Console', 'Agent', 'Insights'])}",
                "feature_description": fake.sentence(nb_words=10),
                "product_id": product_id,
            })
            feature_counter += 1
    return pd.DataFrame(rows)


def assign_cohorts(customers: pd.DataFrame) -> pd.DataFrame:
    cust_ids = customers["cust_id"].tolist()
    random.shuffle(cust_ids)
    rows = []
    idx = 0
    for cohort, size in COHORT_SIZES.items():
        for cust_id in cust_ids[idx: idx + size]:
            rows.append({"cust_id": cust_id, "cohort": cohort})
        idx += size
    for cust_id in cust_ids[idx:]:
        rows.append({"cust_id": cust_id, "cohort": "normal"})
    return pd.DataFrame(rows).sort_values("cust_id").reset_index(drop=True)


def build_entitlements(customers: pd.DataFrame, products: pd.DataFrame,
                       cohorts: pd.DataFrame) -> pd.DataFrame:
    cohort_of = dict(zip(cohorts["cust_id"], cohorts["cohort"]))
    seg_of = dict(zip(customers["cust_id"], customers["segment"]))
    product_ids = products["product_id"].tolist()
    rows = []
    ent_counter = 1

    for cust_id in customers["cust_id"]:
        n_base = random.randint(2, 8)
        chosen_products = random.sample(product_ids, n_base)
        lo, hi = (1000, 50000) if seg_of[cust_id] == "Enterprise" else (100, 8000)
        start = date(2025, 7, 1) + timedelta(days=random.randint(0, 60))
        term_months = random.choices([12, 24, 36], weights=[70, 20, 10])[0]
        for product_id in chosen_products:
            units = random.randint(lo, hi)
            rows.append({
                "entitlement_id": f"ENT-{ent_counter:05d}",
                "product_id": product_id,
                "cust_id": cust_id,
                "units_purchased": units,
                "licensed_amount": units,
                "start_date": start,
                "end_date": start + timedelta(days=int(term_months * 30.44)),
            })
            ent_counter += 1

        # Mid-year expansion: a second, larger contract on an existing product
        # with overlapping active dates.
        if cohort_of[cust_id] == "expansion":
            expand_product = random.choice(chosen_products)
            base_units = next(r["licensed_amount"] for r in rows
                              if r["cust_id"] == cust_id and r["product_id"] == expand_product)
            expansion_start = MONTHS[random.randint(4, 7)]
            rows.append({
                "entitlement_id": f"ENT-{ent_counter:05d}",
                "product_id": expand_product,
                "cust_id": cust_id,
                "units_purchased": int(base_units * random.uniform(1.5, 3.0)),
                "licensed_amount": int(base_units * random.uniform(1.5, 3.0)),
                "start_date": expansion_start,
                "end_date": expansion_start + timedelta(days=365),
            })
            ent_counter += 1
    return pd.DataFrame(rows)


def active_months(ent: pd.Series) -> list[date]:
    return [m for m in MONTHS
            if ent["start_date"] <= month_end(m) and ent["end_date"] >= m]


def month_first(d: date) -> date:
    return d.replace(day=1)


def deployment_lag_days(cohort: str) -> int:
    """Days from contract start to first product use (drives day-level TTFV).

    Most accounts deploy quickly; a realistic minority lag by weeks or months.
    Spike-drop accounts burn immediately (no lag); other heavy users adopt fast.
    """
    if cohort == "spike_drop":
        return 0
    if cohort == "normal":
        r = random.random()
        if r < 0.70:
            return random.randint(0, 20)   # fast: within the first month
        if r < 0.90:
            return random.randint(21, 55)  # 1-2 month lag
        return random.randint(56, 90)      # slow deployers
    return random.randint(0, 12)           # overage / expansion: quick


def monthly_units(cohort: str, ent: pd.Series, months: list[date],
                  adoption_month: date) -> dict[date, int]:
    """Consumption per active month for one entitlement, per cohort behavior.

    No consumption before the adoption month; the ramp is indexed from adoption.
    """
    licensed = ent["licensed_amount"]
    units: dict[date, int] = {m: 0 for m in months}
    if cohort == "shelfware":
        return {}
    active = [m for m in months if m >= adoption_month]
    if not active:
        return units
    if cohort == "spike_drop":
        # ~90% of annual entitlement burned across the first 3 active months, then dark.
        for m in active[:3]:
            units[m] = int(licensed * 12 * 0.90 / 3 * random.uniform(0.92, 1.08))
        return units
    if cohort == "overage":
        for m in active:
            units[m] = int(licensed * random.uniform(1.20, 1.50))
        return units
    # normal + expansion: S-curve ramp to a plateau with noise, from adoption.
    plateau = random.uniform(0.30, 0.45) if random.random() < 0.15 else random.uniform(0.60, 0.85)
    ramp_months = random.randint(3, 6)
    for i, m in enumerate(active):
        target = plateau * min(1.0, (i + 1) / ramp_months)
        noisy = max(0.0, target * random.uniform(0.90, 1.10))
        units[m] = int(licensed * noisy)
    return units


def build_consumption_and_adoption(entitlements: pd.DataFrame, cohorts: pd.DataFrame):
    """Generate monthly consumption plus a customer-product first-adoption date.

    Each entitlement gets a deployment lag; adoption_date = start + lag (always
    >= start). Consumption is zeroed before the adoption month, so the daily
    adoption date and the monthly consumption stay consistent and TTFV can be
    measured in real days.
    """
    cohort_of = dict(zip(cohorts["cust_id"], cohorts["cohort"]))
    cons_rows = []
    first_adoption: dict[tuple[str, str], date] = {}
    for _, ent in entitlements.iterrows():
        cohort = cohort_of[ent["cust_id"]]
        if cohort == "shelfware":
            continue
        months = active_months(ent)
        if not months:
            continue
        adoption_date = ent["start_date"] + timedelta(days=deployment_lag_days(cohort))
        adoption_m = month_first(adoption_date)
        if adoption_m > months[-1]:  # lag past the window; clamp to last active month
            adoption_m = months[-1]
            adoption_date = max(ent["start_date"], adoption_m)
        key = (ent["cust_id"], ent["product_id"])
        if key not in first_adoption or adoption_date < first_adoption[key]:
            first_adoption[key] = adoption_date
        for m, consumed in monthly_units(cohort, ent, months, adoption_m).items():
            cons_rows.append({
                "cust_id": ent["cust_id"],
                "entitlement_id": ent["entitlement_id"],
                "usage_month": m,
                "consumed_units": consumed,
            })
    consumption = pd.DataFrame(cons_rows)
    product_adoption = pd.DataFrame(
        [{"cust_id": c, "product_id": p, "first_adoption_date": d}
         for (c, p), d in first_adoption.items()]
    ).sort_values(["cust_id", "product_id"]).reset_index(drop=True)
    return consumption, product_adoption


def build_daily_consumption(entitlements: pd.DataFrame, consumption: pd.DataFrame,
                            cohorts: pd.DataFrame) -> pd.DataFrame:
    """Daily consumption for the in-flight current month (up to AS_OF_DATE).

    Each entitlement's daily pace is anchored to its own trailing-3-month daily
    rate, then scaled by a per-customer "mover" factor so momentum (this month's
    pace vs the account's own baseline) is a real signal: ~10% of active
    customers are decelerating this month, ~5% accelerating, the rest steady.
    This is what powers the month-to-date / projected CVRS view.
    """
    cohort_of = dict(zip(cohorts["cust_id"], cohorts["cohort"]))
    trailing = consumption[consumption["usage_month"].isin(TRAILING_MONTHS)]
    trail_by_ent = trailing.groupby("entitlement_id")["consumed_units"].sum().to_dict()

    active = entitlements[
        (entitlements["start_date"] <= month_end(CURRENT_MONTH_START))
        & (entitlements["end_date"] >= CURRENT_MONTH_START)
    ]
    active = active[active["cust_id"].map(cohort_of) != "shelfware"]

    # Assign each active customer a current-month momentum class.
    mover_factor: dict[str, float] = {}
    for cust_id in sorted(active["cust_id"].unique()):
        r = random.random()
        if r < 0.10:
            mover_factor[cust_id] = random.uniform(0.20, 0.50)   # decelerating
        elif r < 0.15:
            mover_factor[cust_id] = random.uniform(1.30, 1.60)   # accelerating
        else:
            mover_factor[cust_id] = random.uniform(0.90, 1.10)   # steady

    as_of_day = AS_OF_DATE.day
    rows = []
    for _, ent in active.iterrows():
        daily_target = (trail_by_ent.get(ent["entitlement_id"], 0) / 91.0) * mover_factor[ent["cust_id"]]
        if daily_target <= 0:
            continue
        for day in range(1, as_of_day + 1):
            d = date(CURRENT_MONTH_START.year, CURRENT_MONTH_START.month, day)
            if d < ent["start_date"]:
                continue
            rows.append({
                "cust_id": ent["cust_id"],
                "entitlement_id": ent["entitlement_id"],
                "usage_date": d,
                "consumed_units": int(max(0, daily_target * random.uniform(0.80, 1.20))),
            })
    return pd.DataFrame(rows)


def build_feature_adoption(entitlements: pd.DataFrame, features: pd.DataFrame,
                           cohorts: pd.DataFrame) -> pd.DataFrame:
    cohort_of = dict(zip(cohorts["cust_id"], cohorts["cohort"]))
    features_by_product = features.groupby("product_id")["feature_id"].apply(list).to_dict()
    rows = []
    for (cust_id, product_id), group in entitlements.groupby(["cust_id", "product_id"]):
        cohort = cohort_of[cust_id]
        if cohort == "shelfware":
            continue
        eligible = features_by_product.get(product_id, [])
        months = sorted({m for _, ent in group.iterrows() for m in active_months(ent)})
        if not months or not eligible:
            continue

        if cohort == "spike_drop":
            target_share, active_window = random.uniform(0.2, 0.4), 3
        else:
            target_share, active_window = random.uniform(0.4, 0.9), None

        n_adopt = max(1, int(len(eligible) * target_share))
        adopted = random.sample(eligible, n_adopt)
        for feature_id in adopted:
            # Features come online progressively across the first ~6 months.
            first_idx = min(int(abs(np.random.normal(0, 2))), len(months) - 1)
            adoption_date = months[first_idx] + timedelta(days=random.randint(0, 27))
            for i, m in enumerate(months):
                if i < first_idx:
                    continue
                if active_window is not None and i >= active_window:
                    active = False
                else:
                    active = random.random() < 0.90
                rows.append({
                    "cust_id": cust_id,
                    "feature_id": feature_id,
                    "usage_month": m,
                    "monthly_active": active,
                    "adoption_date": adoption_date,
                })
    return pd.DataFrame(rows)


def main() -> None:
    random.seed(SEED)
    np.random.seed(SEED)
    fake = Faker()
    Faker.seed(SEED)

    OUTPUT_DIR.mkdir(exist_ok=True)

    customers = build_customers(fake)
    products = build_products()
    features = build_features(products, fake)
    cohorts = assign_cohorts(customers)
    entitlements = build_entitlements(customers, products, cohorts)
    consumption, product_adoption = build_consumption_and_adoption(entitlements, cohorts)
    consumption_daily = build_daily_consumption(entitlements, consumption, cohorts)
    feature_adoption = build_feature_adoption(entitlements, features, cohorts)
    month_spine = pd.DataFrame({
        "month_start": list(MONTHS),
        "month_end": [month_end(m) for m in MONTHS],
    })

    tables = {
        "customers": customers,
        "products": products,
        "features": features,
        "entitlements": entitlements,
        "consumption": consumption,
        "consumption_daily": consumption_daily,
        "product_adoption": product_adoption,
        "feature_adoption": feature_adoption,
        "month_spine": month_spine,
        "cohort_assignments": cohorts,
    }
    for name, df in tables.items():
        path = OUTPUT_DIR / f"{name}.csv"
        df.to_csv(path, index=False)
        print(f"{name}: {len(df):,} rows -> {path}")

    print("\nCohort counts:")
    print(cohorts["cohort"].value_counts().to_string())


if __name__ == "__main__":
    main()
