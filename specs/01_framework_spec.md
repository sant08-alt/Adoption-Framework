# Product Adoption Framework — Product Specification

**Status:** Approved for prototype
**Owner:** Principal PM, Product Analytics
**Audience:** CPO, GMs of Product, Customer Success leadership

---

## 1. Problem Statement

Our business sells recurring-revenue SKUs with consumption-based offerings. Each SKU
bundles multiple features. Today, "adoption" is reported as raw usage volume, which
creates three failure modes:

1. **Usage ≠ value.** A customer can burn through entitlements in a quarter (a rushed
   POC, a one-off migration) and show up as "highly adopted" while being a churn risk.
2. **Shelfware is invisible.** Accounts that bought but never deployed look identical
   to accounts mid-ramp unless someone manually inspects them.
3. **Wrong incentives.** Customer-facing teams are paid on metrics they can game
   (logins, raw consumption) rather than durable value realization.

We need a measurement framework that scores adoption at the **feature**, **SKU**, and
**customer** level, tracks the journey from **deployment through post-implementation**,
and gives executives a defensible portfolio view.

## 2. North Star Metric: Customer Value Realization Score (CVRS)

CVRS is a 0–100 composite computed **per customer per month**. It blends four
components, each 0–1, weighted and scaled:

```
CVRS = 100 * (0.40 * utilization + 0.30 * feature_depth + 0.20 * consistency + 0.10 * breadth)
```

| # | Component | Weight | Formula | Failure mode it catches |
|---|-----------|--------|---------|------------------------|
| 1 | **Consumption Utilization** | 40% | `min(consumed_units_month / active_entitled_units_month, 1.0)`, averaged across the customer's active entitlements | Shelfware (utilization = 0) |
| 2 | **Feature Adoption Depth** | 30% | `features_actively_used / features_eligible` per SKU, averaged across active SKUs | Shallow deployments — paying for a platform, using one feature |
| 3 | **Consistency** | 20% | Trailing-3-month utilization stability: `max(0, 1 - relative_drop) * (1 - volatility_penalty)` where `relative_drop` compares current month to the trailing-3-month peak | Spike & Drop — front-loaded consumption masking abandonment |
| 4 | **Breadth** | 10% | `SKUs_with_any_usage / SKUs_owned` | Single-product dependence in multi-SKU accounts |

### Precise component definitions

**1. Consumption Utilization (`utilization`)**
- Denominator: the sum of `licensed_amount` across **all entitlements active in that
  calendar month** (an entitlement is active in month M if `start_date <= last_day(M)`
  and `end_date >= first_day(M)`).
- Numerator: total consumed units recorded in that month across those entitlements.
- Capped at 1.0. Overconsumption is not "extra health" — it is a commercial signal
  tracked separately (see §4, Expansion Signal).

**2. Feature Adoption Depth (`feature_depth`)**
- A feature is "eligible" if it belongs to a product the customer holds an active
  entitlement for in that month.
- A feature is "actively used" if the customer has a feature-adoption record with
  `monthly_active = true` for that month.
- Ratio computed per SKU, then averaged across the customer's active SKUs (so a small
  add-on SKU counts as much as the flagship — depth is about deployment quality, not size).

**3. Consistency (`consistency`)**
- Let `u_t` be capped utilization in month t. Over the trailing 3 months
  (t-2 .. t): `peak = max(u_{t-2}, u_{t-1}, u_t)`.
- `relative_drop = (peak - u_t) / peak` when `peak > 0`, else 0.
- `volatility_penalty = min(coefficient_of_variation(u_{t-2}..u_t), 1.0) * 0.5`.
- `consistency = max(0, 1 - relative_drop) * (1 - volatility_penalty)`.
- First 2 months of a customer's history: consistency defaults to the mean of available
  months' utilization ratio to peak (no penalty for a short history — new customers
  should not be punished for ramping).

**4. Breadth (`breadth`)**
- `SKUs_with_any_usage`: active entitlements with consumed_units > 0 in the month.
- Single-SKU customers score 1.0 if that SKU has usage, 0 otherwise (no penalty for
  owning one SKU).

## 3. Health Tiers (derived, actionable)

Health tiers drive Customer Success playbooks. Evaluated monthly. (Terminology note:
"tier" is used deliberately — "segment" is reserved for the commercial customer
segment, Enterprise vs. Mid-Market.)

| Health Tier | Rule (in priority order) | Playbook |
|---------|--------------------------|----------|
| **Shelfware** | Zero consumption in the current month AND zero cumulative consumption to date | Deployment rescue: services engagement, onboarding restart |
| **At Risk** | CVRS < 40 (absolute floor, never suspended), or CVRS dropped ≥ 25 points vs trailing-3-month average **outside the post-expansion grace window** | Exec sponsor outreach, health review |
| **Expansion Signal** | Sustained overage: raw (uncapped) utilization ≥ 120% for 2+ consecutive months | Commercial motion: true-up / upsell conversation |
| **Healthy** | CVRS ≥ 70 | Nurture, reference/advocacy candidates |
| **Watch** | Everything else (CVRS 40–69) | Standard cadence, feature-adoption campaigns |

A customer can be **Expansion Signal** and **Healthy** simultaneously; Expansion Signal
is surfaced as a flag alongside the tier rather than replacing it — except when it
co-occurs with At Risk (overage + falling depth = urgent true-up risk).

### Post-expansion grace window

When a customer buys additional capacity, the entitled denominator steps up in that
month while consumption has not yet ramped — utilization mechanically dips. That dip is
**expected absorption behaviour, not deterioration**, so it must not read as churn risk.

- **Window:** the capacity-increase month plus the following month (**2 months total**).
- **What is suspended:** only the *relative* At Risk rule (≥ 25-point drop vs trailing
  average).
- **What is never suspended:** the *absolute* floor. A customer whose CVRS falls below
  40 is At Risk regardless of any recent expansion — buying more licences cannot mask
  genuine distress.
- **Transparency:** `in_expansion_grace` and `months_since_capacity_increase` are
  exposed on every customer-month row, so a CSM can always see whether a suppression
  applied and why.

#### Known limitation: both rules are dormant on the current dataset

Measured against the generated data, neither the relative-drop rule nor its grace
window changes any classification:

| Observation | Value |
|---|---|
| Capacity-increase events in the dataset | 9 (all expansion cohort) |
| Largest post-expansion drop | 23.5 pts (CUST-0003, 2.24× capacity) — below the 25 threshold |
| Rows anywhere with a ≥ 25-point drop | 15 |
| ...of those, with CVRS ≥ 40 | **0** |

Every ≥ 25-point drop in the dataset belongs to a spike-and-drop account that has
already fallen to CVRS 0.0, so the **absolute floor classifies it first**. The relative
rule therefore never independently determines an outcome, and the grace window — which
suppresses only that rule — is provably a no-op here.

Both are retained deliberately: they guard against *gradual erosion* (a customer
declining from ~75 to ~45 without ever breaching the floor), a pattern the current
synthetic data does not model. The honest position is that this logic is **specified and
unit-covered but not yet exercised by realistic data**.

Closing the gap would require either (a) raising expansion multiples to ~2.5–4.0× so the
post-expansion dip exceeds 25 points while the account stays above the floor, or
(b) adding a slow-erosion cohort. Both are deferred; see `02_data_spec.md`.

## 4. Deployment & Value Realization Metrics

Beyond the score, the framework tracks journey metrics from deployment onward:

- **Time-to-First-Value (TTFV):** days from the entitlement `start_date` to the
  customer's first use of that product, taken from the real daily
  `product_adoption.first_adoption_date` (always ≥ the contract start). At customer
  level, TTFV is the fastest product to reach first value. Null TTFV = never deployed;
  sustained null = escalation. The generator models a realistic deployment-lag spread
  (most accounts adopt within ~3 weeks; a minority lag 1–3 months), so TTFV varies from
  0 to ~90 days rather than being uniform.
- **Time-to-Feature-Adoption:** days from entitlement start to each feature's first
  active month; reported as median per feature (product teams use this to find
  features that are hard to turn on).
- **Overage % (raw utilization):** uncapped consumption / entitlement, the input to
  Expansion Signal and true-up forecasting.
- **Entitlement Coverage:** % of owned SKUs deployed (TTFV achieved) — the
  post-implementation completeness measure.

### Revenue impact (reporting overlay, not part of the score)

Adoption is a **leading indicator**; revenue is the **lagging outcome** it predicts.
The framework quantifies that outcome as a reporting overlay so executives see dollars,
not just units — while keeping revenue strictly **out of CVRS**:

- **Committed ARR** = Σ (entitled capacity × list price) × 12. Layered onto health tiers
  it yields **ARR-at-risk** (Σ committed ARR of At Risk + Shelfware) — the number a CFO
  actually asks for.
- **Expansion pipeline** = annualized run-rate of current billable overage on Expansion
  Signal accounts — the revenue-qualified upsell list, since the customer is already
  consuming what they would be billed for.

**Why revenue is not a CVRS component:** (1) ARR-weighting would let a large account's
shallow adoption look healthy and bury a small account's deep adoption — adoption
quality must stay orthogonal to deal size; (2) folding the lagging outcome into the
leading indicator destroys the ability to say "adoption predicted revenue." The rule is
enforced by a test (`test_cvrs_is_pure_function_of_its_four_components`) asserting CVRS
reconstructs exactly from its four inputs.

Pricing here is **assumed list pricing** (per-platform, in the generator). Production
would join real CPQ/pricing data; the pipeline structure is unchanged.

## 5. Edge-Case Handling (by design, not exception)

These are known realities of enterprise B2B data. The metric definitions above handle
each one structurally; automated tests (see `03_pipeline_spec.md`) assert it.

| Edge case | How the framework handles it |
|-----------|------------------------------|
| **Spike & Drop** (~5% of accounts consume 90% of entitlements in months 1–3, then nothing) | The Consistency component compares current utilization to the trailing-3-month peak. A cliff to zero drives `relative_drop → 1`, consistency → 0, and utilization → 0, so CVRS collapses within one month of the drop. Cumulative-consumption metrics would score these accounts as heroes; CVRS scores them as churn risks. |
| **Shelfware** (~10% of accounts with no consumption rows) | The pipeline builds the month spine from **entitlements**, not from consumption, so accounts with zero consumption rows still appear every month with utilization 0 and a hard tier override to Shelfware. Absence of data is itself a signal and can never silently drop an account from reporting. |
| **Consistent Overages** (~15% of accounts at 120%+ every month) | Utilization is capped at 1.0 inside CVRS so overconsumption cannot inflate health. Raw utilization is preserved as a separate column and drives the Expansion Signal flag — the commercial team sees the upsell, the health score stays honest. |
| **Mid-Year Expansions** (overlapping active entitlements) | The entitled denominator is computed per month as the **sum of all entitlements active in that month** via a date-range join. When a second, larger contract lands mid-year, the denominator steps up in that month; utilization dips (expected — new capacity takes time to consume) and the Consistency lookback prevents the dip from reading as churn. No double counting, no >100% artifacts from stacked contracts. |

## 6. Success Criteria for the Framework Itself

The framework is evaluated the way a model would be — against labeled ground truth:

1. **Detection precision/recall:** the synthetic dataset injects known cohorts
   (spike-drop, shelfware, overage, expansion). The pipeline must classify ≥ 90% of
   each injected cohort into the intended health tier within one month of the behavior
   manifesting. Automated tests enforce this.
2. **Business KPI linkage:** health tiers map to owned motions with measurable outcomes —
   Shelfware → deployment rescue (KPI: TTFV attainment), Expansion Signal → true-up
   pipeline (KPI: expansion ARR), At Risk → save motions (KPI: gross retention).
3. **Incentive safety:** no component of CVRS can be improved by burning consumption
   faster (cap + consistency), by shipping logins without feature use (depth), or by
   ignoring part of the portfolio (breadth).

## 7. Human in the Loop

- The score **informs**, humans **decide**: health-tier changes generate review queues for
  CSMs, not automated customer-facing actions.
- CSMs can annotate a customer-month with an override reason (e.g., "planned migration
  pause") that is stored alongside — never instead of — the computed score.
- Dashboard drill-downs always expose the component breakdown so a CSM can contest the
  *inputs* ("feature eligibility is wrong for this SKU") rather than argue with a
  black-box number.

## 8. Out of Scope (v1)

- Streaming/near-real-time scoring (monthly grain only).
- Predictive churn modeling (CVRS is descriptive; it becomes a feature for ML later).
- Automated compensation triggers (explicitly deferred until 2 quarters of stability).
