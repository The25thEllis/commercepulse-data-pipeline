\# CommercePulse Data Pipeline



A complete data engineering pipeline for CommercePulse Ltd — an e-commerce aggregation platform operating across multiple African markets.



---



\## Architecture Overview



The pipeline uses a dual-store architecture:



\- \*\*MongoDB\*\* — Raw event store. Stores all historical and live events exactly as received. No business logic applied. Acts as the system of record.

\- \*\*BigQuery\*\* — Analytics warehouse. Stores clean, normalized, query-optimized tables for BI and reporting.

\- \*\*Pandas\*\* — Transformation layer. Normalizes schemas, applies business rules, and handles schema drift across vendors.



---



\## Project Structure

```

commercepulse\_data\_pack/

├── data/

│   ├── bootstrap/          # Historical JSON exports (2023)

│   └── live\_events/        # Daily live event files (.jsonl)

├── reports/                # Daily data quality reports

├── sql/                    # Analytics queries

│   └── analytics\_queries.sql

├── src/

│   ├── bootstrap\_loader.py     # Loads historical data into MongoDB

│   ├── live\_event\_loader.py    # Loads live events into MongoDB

│   ├── transform.py            # Normalizes and transforms data

│   ├── bq\_loader.py            # Loads clean data into BigQuery

│   └── data\_quality\_report.py  # Daily data quality checks

├── .env                    # Environment variables (not committed)

├── .gitignore

└── README.md

```



---



\## Setup Instructions



\### Requirements

\- Python 3.9+

\- MongoDB running locally

\- Google Cloud account with BigQuery enabled

\- Git



\### Installation



1\. Clone the repository:

```

git clone https://github.com/The25thEllis/commercepulse.git

cd commercepulse

```



2\. Create and activate virtual environment:

```

python -m venv venv

venv\\Scripts\\activate

```



3\. Install dependencies:

```

pip install pymongo pandas python-dotenv google-cloud-bigquery pandas-gbq

```



4\. Create a `.env` file in the project root:

```

MONGO\_URI=mongodb://localhost:27017

MONGO\_DB=commercepulse

GOOGLE\_APPLICATION\_CREDENTIALS=credentials.json

BIGQUERY\_PROJECT=your-project-id

BIGQUERY\_DATASET=commercepulse

```



5\. Add your BigQuery service account key as `credentials.json` in the project root.



---



\## Running the Pipeline



Run these scripts in order:



\### Step 1 — Load Historical Data

```

python src/bootstrap\_loader.py

```



\### Step 2 — Generate and Load Live Events

```

python src/live\_event\_generator.py --out data/live\_events --events 2000

python src/live\_event\_loader.py

```



\### Step 3 — Transform and Load into BigQuery

```

python src/bq\_loader.py

```



\### Step 4 — Run Daily Data Quality Report

```

python src/data\_quality\_report.py

```



Reports are saved to `reports/dq\_report\_YYYY-MM-DD.txt`



---



\## BigQuery Tables



| Table | Type | Description |

|-------|------|-------------|

| dim\_customer | Dimension | Unique customers by email |

| dim\_date | Dimension | Date breakdown with year/month/day |

| fact\_orders | Fact | One row per order (deduplicated) |

| fact\_payments | Fact | All payment attempts (append-only) |

| fact\_refunds | Fact | All refund events (append-only) |

| fact\_shipments | Fact | Shipment tracking per order |

| fact\_order\_daily | Aggregate | Daily gross/net revenue summary |



---



\## Data Quality Checks



The daily report (`src/data\_quality\_report.py`) checks for:



1\. Orders with missing amounts

2\. Orders with missing customer emails

3\. Payment success rate by vendor

4\. Orders with no payment record

5\. Payments with no matching order

6\. Late arriving refunds (>7 days after order)

7\. Duplicate event IDs in MongoDB

8\. Days with zero revenue

9\. Average order to payment time

10\. Overall refund rate



---



\## Trade-Off Decisions



\### MongoDB vs BigQuery Responsibilities

MongoDB stores raw events exactly as received — no transformations applied. This preserves the original data for auditing and reprocessing. BigQuery stores only clean, normalized data optimized for analytics queries. Keeping these separate means neither system is compromised by the other's requirements.



\### Append-Only vs Upsert

Payments and refunds are append-only in BigQuery — every event is kept. Orders use upsert (deduplication) because an order can be updated after creation and we only want the latest state for revenue calculations.



\### Historical Batch vs Live Events

Historical records were wrapped as synthetic events with deterministic IDs so they could coexist with live events in the same MongoDB collection. This means one pipeline handles both data types consistently.



\### Pandas vs SQL Transformations

Schema normalization and vendor inconsistency handling is done in Pandas because it is more flexible for handling missing fields, nested structures, and mixed types. Aggregations and joins are done in BigQuery SQL because it is faster and more scalable for large datasets.



\### Correctness vs Performance

The pipeline prioritizes correctness over performance. Every run re-normalizes all events from MongoDB rather than processing only new ones. For this project size this is acceptable. At scale, incremental extraction using `ingested\_at` timestamps would be implemented.



\### Simplicity vs Maintainability

Helper functions like `get\_order\_id()`, `get\_amount()`, and `parse\_timestamp()` are designed to handle multiple vendor formats in one place. Adding a new vendor only requires updating these functions — not rewriting the entire pipeline.



---



\## Known Limitations



\- Product-level SKU revenue reporting requires flattening nested item arrays from MongoDB — not yet implemented in BigQuery

\- Historical batch data is missing vendor IDs, customer emails, and amounts in some records — this is a source data quality issue not a pipeline issue

\- Negative average payment times indicate some payment timestamps predate order timestamps in the source data



---



\## Author



Built as part of the CommercePulse Data Engineering case study.



