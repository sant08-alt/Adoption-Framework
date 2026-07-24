# Hard Truths — Decisions, Defects, and Known Limitations

A single log of what actually broke while building this, why, how it was fixed, and
what now guards against regression. Kept because a defect you can't explain later is a
defect you will reintroduce — and because two of these fixes are otherwise invisible in
the code.

Status key: **Fixed** (with a guard) · **Retained** (deliberate, documented) ·
**Open** (accepted limitation)

| # | Hard truth | Area | Status |
|---|-----------|------|--------|
| 1 | CTE / column name collision silently changed the score's meaning | Pipeline | Fixed |
| 2 | Negative time-to-first-value for 82% of accounts | Pipeline | Fixed |
| 3 | The relative-drop At-Risk rule is dormant | Metric | Retained |
| 4 | Executive KPIs couldn't be made clickable | Dashboard | Fixed |
| 5 | The time-to-adopt chart was unreadable | Dashboard | Fixed |
| 6 | Trend-chart month labels were inexact | Dashboard | Fixed |
| 7 | A share rendered with a misleading trend arrow | Dashboard | Fixed |

---

## 1. CTE / column name collision — BigQuery averaged a whole row

**Symptom.** The pipeline failed on `30_mart_customer_health.sql`:
`No matching signature for aggregate function AVG; Argument types:
STRUCT<cust_id STRING, month_start DATE, entitled_units INT64, ...>`

**Root cause.** A CTE was named `cvrs` *and* it produced a column named `cvrs`. In
`AVG(cvrs)`, BigQuery resolved the bare identifier to the **CTE's range variable (the
whole row struct)** rather than the column — so the window function was trying to
average an entire row, not the score.

**Fix.** Renamed the CTE to `cvrs_calc` so `AVG(cvrs)` unambiguously means the column.

**Guard.** `test_cvrs_is_pure_function_of_its_four_components` asserts CVRS reconstructs
exactly from its four components, so a silent change in what's being aggregated fails
the suite. A comment above the CTE explains why it must not be renamed back.

**Lesson.** This passed local reasoning and surfaced *only* against BigQuery's stricter
name resolution — the argument for validating on the engine you will actually run on,
not a local stand-in.

---

## 2. Negative time-to-first-value — a data-grain mismatch

**Symptom.** `ttfv_days` was **negative for 82 of 100 customers** (as low as −30). Value
was apparently being realised before the contract started.

**Root cause.** A grain mismatch. Entitlements carry a **daily** `start_date` (e.g.
2025-07-31), but consumption is **monthly**, stamped on the 1st (2025-07-01). So
`DATE_DIFF(Jul 1, Jul 31) = −30` for any contract starting after the 1st. The day-level
precision was fictional: with monthly consumption we cannot know the day of first use.

**First fix (insufficient).** Anchoring to `DATE_TRUNC(start_date, MONTH)` removed the
negatives — but made TTFV **0 for every deployed account**, because every non-shelfware
account consumes in its first month. Non-negative, but uninformative.

**Final fix.** Generate a real daily `product_adoption.first_adoption_date`
(= contract start + a modelled deployment lag), and zero consumption before the adoption
month so the daily date and monthly consumption stay consistent. TTFV is now genuine
day-level, ranging 0–~90 days.

**Guards.** `test_time_metrics_never_negative` (no negative TTFV or time-to-adopt) and
`test_product_adoption_after_contract_start` (adoption never precedes the contract).

**Lesson.** Never compute day-precision against a coarser-grain fact; fix the data model
rather than clamping the symptom.

---

## 3. The relative-drop At-Risk rule is dormant — retained, not hidden

**Symptom.** The "CVRS fell ≥25 points vs the trailing-3-month average" rule — and the
post-expansion grace window built to modulate it — **never independently determine any
classification** on this dataset.

**Measured evidence.**

| Observation | Value |
|---|---|
| Rows with a ≥25-point drop | 15 |
| ...of those, with CVRS ≥ 40 | **0** — the absolute floor already caught every one |
| Capacity-increase events (expansion) | 9 |
| Largest post-expansion dip | 23.5 pts (2.24× capacity) — below the 25 threshold |

**Why it is retained.** It guards **gradual erosion** — an account drifting from ~75 to
~45 without ever breaching the CVRS < 40 floor. That pattern is real in production books;
the synthetic data simply doesn't model it yet.

**Closing the gap** (deferred, see `02_data_spec.md`): raise expansion multiples to
~2.5–4.0× so the dip exceeds 25 points while the account stays above the floor, and/or
add a `slow_erosion` cohort.

**Guards.** `test_expansion_grace_window_is_exactly_two_months`,
`test_expansion_dip_does_not_trigger_false_at_risk`,
`test_absolute_floor_survives_grace_period` — the floor is never suspended.

**Lesson.** A rule that cannot be shown to fire isn't validated; it's decoration until
the data exercises it. Say so rather than let a green suite imply otherwise.

---

## 4. Executive KPIs couldn't be made clickable

**Symptom.** The requirement was for tier percentages to jump to the underlying customer
list. Streamlit's `st.metric` cannot be a hyperlink.

**Fix.** Rendered the KPIs as theme-aware HTML cards wrapped in anchors, targeting
Streamlit's supported `st.subheader(..., anchor=...)` ids.

**Follow-on defects, both fixed.**
- The non-clickable "Avg CVRS" card sat taller than its neighbours because it was a bare
  `<div>` while the others were `<a>`-wrapped — Streamlit gives those different box
  spacing. Unified so every card uses the same `<a>` wrapper (`href` omitted when not a
  link).
- A long platform name ("Cloud Security") wrapped to two lines and again broke row
  alignment. Reduced the value font and raised the card `min-height` so a two-line value
  still fits a consistent card height.

**Lesson.** Mixed markup in a row of "identical" cards is a silent alignment bug.

---

## 5. The time-to-adopt chart was unreadable

**Symptom.** A per-platform box plot (quartiles, whiskers, outliers) required explanation
before an executive could read it — and exposed negative-day artifacts from defect #2.

**Fix.** Replaced it with a single labelled horizontal bar per platform, sorted
slowest-first, with the value printed on each bar and hover disabled. The takeaway is
legible without a tooltip.

**Lesson.** Statistical richness is not the same as communication. Match the chart to the
decision, not to the data's complexity.

---

## 6. Trend-chart month labels were inexact

**Symptom.** The health-tier trend chart showed imprecise, time-stamped ticks
(e.g. `00:00:00Jun 1, 2026`) because Plotly auto-placed datetime ticks.

**Fix.** Plot against explicit `%b %Y` month labels with an enforced chronological
category order, so every tick names its month exactly.

---

## 7. A share rendered with a misleading trend arrow

**Symptom.** "21% of book" appeared next to ARR-at-risk with an **↑ arrow**, implying an
increase that didn't exist.

**Root cause.** The share was passed into `st.metric`'s `delta` argument, which always
draws a sign-based arrow — that slot is for period-over-period change, not a ratio.

**Fix.** Moved the share to a card sub-line (no arrow), and split the figure into
**Shelfware ARR** and **At Risk ARR** so each maps to its own CS motion.

---

## Open limitations carried forward

These are accepted, not defects — but they bound what the prototype can claim.

1. **Synthetic ground truth validates logic, not predictive power.** The ≥90% cohort
   detection proves the metric finds what was planted. The production milestone is a
   back-test against historical renewals: does CVRS at month 9 predict the month-12
   renewal?
2. **Composite weights (40/30/20/10) are judgment.** Therefore versioned in the spec,
   test-covered, and owned by a Product + CS + Finance governance forum — never tuned
   quietly.
3. **No slow-erosion cohort** in the data (see #3).
4. **Pricing is assumed** (per-SKU list price in the generator) and
   `units_purchased = licensed_amount`. All revenue figures are illustrative; revenue is
   a reporting overlay and never a CVRS input.
5. **Feature adoption is binary** (`monthly_active`), not intensity-weighted — a v1
   telemetry scope limit.
6. **The month-to-date view holds depth/breadth at last month's baseline**; only
   utilization is live. It is provisional and never used for tier assignment or
   compensation.
