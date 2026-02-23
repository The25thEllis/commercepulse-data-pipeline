import os
from dotenv import load_dotenv
from google.cloud import bigquery

# Load environment variables
load_dotenv()

# Set Google credentials
os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = os.getenv("GOOGLE_APPLICATION_CREDENTIALS")

# Create BigQuery client
client = bigquery.Client(project=os.getenv("BIGQUERY_PROJECT"))

# Test connection by listing datasets
print("Connecting to BigQuery...")
datasets = list(client.list_datasets())

if datasets:
    print("Connection successful! Datasets found:")
    for dataset in datasets:
        print(f"  - {dataset.dataset_id}")
else:
    print("Connected but no datasets found yet.")