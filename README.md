# Product Adoption Framework — CVRS Prototype

A product adoption measurement framework for a consumption-based security business,
built end-to-end with a **spec-driven AI development approach**: Markdown specs first,
then AI-generated implementation, validated by automated tests against labeled
ground truth.

**North Star metric:** Customer Value Realization Score (CVRS) — a 0–100 monthly
composite of consumption utilization, feature adoption depth, consistency, and
breadth, with adoption insights at the customer, SKU, and feature level.
See [specs/01_framework_spec.md](specs/01_framework_spec.md).

**Revenue impact overlay:** the dashboard also reports Committed ARR, ARR-at-risk, and
an expansion pipeline using **assumed list pricing** set per SKU (`unit_price` on the
products table — a per-platform base with per-SKU variation). Revenue is a reporting
overlay only and is **never an input to CVRS** (enforced by a test). See §Revenue impact
in [specs/01_framework_spec.md](specs/01_framework_spec.md).

## Repository map

| Path | Contents |
|------|----------|
| [specs/](specs/) | Product & technical specifications (written before the code) |
| [specs/05_data_model.md](specs/05_data_model.md) | Column-level data dictionary for every raw, intermediate, and mart table |
| [specs/06_hard_truths.md](specs/06_hard_truths.md) | Decisions, defects found and fixed, and known limitations — with the guard tests |
| [data_generation/](data_generation/) | Synthetic dataset generator + BigQuery loader |
| [pipeline_and_tests/sql/](pipeline_and_tests/sql/) | Layered dbt-style SQL (staging → intermediate → marts) |
| [pipeline_and_tests/tests/](pipeline_and_tests/tests/) | Automated data-quality + metric-correctness tests (pytest) |
| [dashboard/](dashboard/) | Streamlit executive dashboard |
| [presentation/](presentation/) | Executive deck (PPTX with speaker notes + PDF) |

## Setup

```powershell
# 1. Python environment
python -m venv .venv
.\.venv\Scripts\pip install -r requirements.txt

# 2. Google Cloud (BigQuery Sandbox is free, no credit card)
#    Create a project at console.cloud.google.com, then:
gcloud auth application-default login

# 3. Configuration
Copy-Item .env.example .env   # then set GCP_PROJECT_ID in .env
```

## Run order

```powershell
.\.venv\Scripts\python data_generation\generate_data.py        # 1. generate CSVs (seeded, reproducible)
.\.venv\Scripts\python -m pytest pipeline_and_tests\tests\test_raw_data_quality.py   # 2. validate raw data (local, no cloud)
.\.venv\Scripts\python data_generation\load_to_bigquery.py     # 3. load to BigQuery
.\.venv\Scripts\python pipeline_and_tests\run_pipeline.py --export-snapshots   # 4. build metric marts (+ dashboard fallback CSVs)
.\.venv\Scripts\python -m pytest pipeline_and_tests\tests      # 5. full test suite incl. ground-truth detection
.\.venv\Scripts\python -m streamlit run dashboard\app.py       # 6. dashboard
```

## The evaluation story

The synthetic data injects labeled anomaly cohorts (spike-and-drop, shelfware,
sustained overage, mid-year expansion). Because the answer key is known, the test
suite can require the metrics to *detect* what was planted — ≥90% of each cohort
must land in the intended health tier. A metric you cannot test is an opinion; this one
ships with its own evaluation harness.

## Notes

- The dashboard falls back to CSV snapshots (`run_pipeline.py --export-snapshots`)
  when BigQuery is unreachable, so the demo survives a broken conference-room network.
- All randomness is seeded; regeneration is byte-identical.
- Pricing is **assumed, per-SKU** (`PLATFORM_PRICES` base + deterministic jitter in the
  generator); revenue figures are illustrative, not real, and never feed the score.
- No credentials live in this repo: configuration via `.env` (gitignored), auth via
  Application Default Credentials.
