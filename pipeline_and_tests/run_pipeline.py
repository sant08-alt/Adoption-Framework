"""Execute the layered SQL pipeline against BigQuery in filename order.

Usage:
  python pipeline_and_tests/run_pipeline.py
  python pipeline_and_tests/run_pipeline.py --export-snapshots   # also dump marts
                                                                 # to dashboard/snapshots/

Requires .env (GCP_PROJECT_ID, BQ_DATASET) and Application Default Credentials.
"""

import argparse
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).parent.parent
SQL_DIR = Path(__file__).parent / "sql"
SNAPSHOT_DIR = ROOT / "dashboard" / "snapshots"
MARTS = ["mart_customer_health", "mart_sku_adoption", "mart_feature_adoption"]


def get_client_and_dataset():
    load_dotenv(ROOT / ".env")
    project = os.getenv("GCP_PROJECT_ID")
    dataset = os.getenv("BQ_DATASET", "adoption_analytics")
    if not project or project == "your-gcp-project-id":
        sys.exit(
            "GCP_PROJECT_ID is not configured.\n"
            "1. Copy .env.example to .env and set your project id.\n"
            "2. Run: gcloud auth application-default login"
        )
    try:
        from google.cloud import bigquery
        client = bigquery.Client(project=project)
        client.query("SELECT 1").result()
    except Exception as exc:  # noqa: BLE001
        sys.exit(
            f"Could not reach BigQuery: {exc}\n"
            "Run: gcloud auth application-default login"
        )
    return client, f"{project}.{dataset}"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--export-snapshots", action="store_true",
                        help="After the run, export mart tables as CSVs for the dashboard fallback")
    args = parser.parse_args()

    client, dataset = get_client_and_dataset()

    sql_files = sorted(SQL_DIR.glob("*.sql"))
    if not sql_files:
        sys.exit(f"No SQL files found in {SQL_DIR}")

    for path in sql_files:
        sql = path.read_text(encoding="utf-8").replace("${DATASET}", f"`{dataset}`")
        print(f"Running {path.name} ...", end=" ", flush=True)
        client.query(sql).result()
        print("done")

    print("\nRow counts:")
    for mart in MARTS:
        count = next(iter(client.query(
            f"SELECT COUNT(*) AS n FROM `{dataset}.{mart}`").result())).n
        print(f"  {mart}: {count:,}")

    if args.export_snapshots:
        SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
        for mart in MARTS:
            df = client.query(f"SELECT * FROM `{dataset}.{mart}`").to_dataframe()
            out = SNAPSHOT_DIR / f"{mart}.csv"
            df.to_csv(out, index=False)
            print(f"Snapshot: {out} ({len(df):,} rows)")

    print("\nPipeline complete. Next: pytest pipeline_and_tests/tests/")


if __name__ == "__main__":
    main()
