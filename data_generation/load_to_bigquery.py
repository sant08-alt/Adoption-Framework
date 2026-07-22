"""Load generated CSVs into BigQuery.

Creates the dataset if needed, then loads every CSV in data_generation/output/
as a table (WRITE_TRUNCATE, schema autodetected with explicit date parsing).

Prereqs: .env with GCP_PROJECT_ID / BQ_DATASET, and Application Default
Credentials (`gcloud auth application-default login`).

Run: python data_generation/load_to_bigquery.py
"""

import os
import sys
from pathlib import Path

from dotenv import load_dotenv

OUTPUT_DIR = Path(__file__).parent / "output"

DATE_COLUMNS = {
    "entitlements": ["start_date", "end_date"],
    "consumption": ["usage_month"],
    "product_adoption": ["first_adoption_date"],
    "feature_adoption": ["usage_month", "adoption_date"],
    "month_spine": ["month_start", "month_end"],
}


def get_client():
    load_dotenv(Path(__file__).parent.parent / ".env")
    project = os.getenv("GCP_PROJECT_ID")
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
    except Exception as exc:  # noqa: BLE001 - surface any auth/setup problem with guidance
        sys.exit(
            f"Could not reach BigQuery: {exc}\n"
            "Run: gcloud auth application-default login"
        )
    return client


def main() -> None:
    import pandas as pd
    from google.cloud import bigquery

    client = get_client()
    dataset_id = f"{client.project}.{os.getenv('BQ_DATASET', 'adoption_analytics')}"
    dataset = bigquery.Dataset(dataset_id)
    dataset.location = os.getenv("BQ_LOCATION", "US")
    client.create_dataset(dataset, exists_ok=True)
    print(f"Dataset ready: {dataset_id}")

    csvs = sorted(OUTPUT_DIR.glob("*.csv"))
    if not csvs:
        sys.exit("No CSVs found. Run data_generation/generate_data.py first.")

    for path in csvs:
        table_name = path.stem
        df = pd.read_csv(path)
        for col in DATE_COLUMNS.get(table_name, []):
            df[col] = pd.to_datetime(df[col]).dt.date
        job = client.load_table_from_dataframe(
            df,
            f"{dataset_id}.{table_name}",
            job_config=bigquery.LoadJobConfig(write_disposition="WRITE_TRUNCATE"),
        )
        job.result()
        print(f"Loaded {table_name}: {len(df):,} rows")

    print("\nAll tables loaded. Next: python pipeline_and_tests/run_pipeline.py")


if __name__ == "__main__":
    main()
