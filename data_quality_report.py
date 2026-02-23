# -----------------------
# Imports & Setup
# -----------------------
import os
import sys
from datetime import datetime
import pandas as pd
from pymongo import MongoClient
from google.cloud import bigquery
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = os.getenv("GOOGLE_APPLICATION_CREDENTIALS")

# BigQuery client
bq_client = bigquery.Client(project=os.getenv("BIGQUERY_PROJECT"))
dataset = os.getenv("BIGQUERY_DATASET")
project = os.getenv("BIGQUERY_PROJECT")

# MongoDB connection
mongo_client = MongoClient(os.getenv("MONGO_URI"))
collection = mongo_client[os.getenv("MONGO_DB")]["events_raw"]

# -----------------------
# Report Output Setup
# Saves a dated report file every time the script runs
# -----------------------

# Create reports folder if it doesn't exist
report_dir = "reports"
os.makedirs(report_dir, exist_ok=True)

# Dated filename — e.g. reports/dq_report_2026-02-22.txt
report_date = datetime.utcnow().strftime("%Y-%m-%d")
report_filename = os.path.join(report_dir, f"dq_report_{report_date}.txt")


class Tee:
        
    def __init__(self, file):
        self.file = file
        self.terminal = sys.stdout

    def write(self, message):
        self.terminal.write(message)
        self.file.write(message)

    def flush(self):
        self.terminal.flush()
        self.file.flush()


# Open the report file and redirect stdout to Tee
report_file = open(report_filename, "w", encoding="utf-8")
sys.stdout = Tee(report_file)


# -----------------------
# Helper — Query BigQuery
# -----------------------
def query(sql):
    """Run a SQL query against BigQuery and return a DataFrame"""
    return bq_client.query(sql).to_dataframe()


# -----------------------
# Report Header
# -----------------------
print("=" * 60)
print("  CommercePulse — Daily Data Quality Report")
print(f"  Generated: {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')} UTC")
print("=" * 60)

issues_found = 0

# -----------------------
# Check 1 — Missing Amounts in Orders
# -----------------------
print("\n[1] Orders with missing amounts")
df = query(f"""
    SELECT COUNT(*) as count
    FROM `{project}.{dataset}.fact_orders`
    WHERE amount IS NULL
""")
count = df["count"][0]
print(f"    → {count} orders have no amount recorded")
if count > 0:
    issues_found += 1
    print(f"    ⚠ WARNING: These orders will be excluded from revenue calculations")
else:
    print(f"    ✓ All orders have amounts")

# -----------------------
# Check 2 — Missing Customer Emails
# -----------------------
print("\n[2] Orders with missing customer emails")
df = query(f"""
    SELECT COUNT(*) as count
    FROM `{project}.{dataset}.fact_orders`
    WHERE customer_email IS NULL
""")
count = df["count"][0]
print(f"    → {count} orders have no customer email")
if count > 0:
    issues_found += 1
    print(f"    ⚠ WARNING: Likely from historical batch exports — customers unidentifiable")
else:
    print(f"    ✓ All orders have customer emails")

# -----------------------
# Check 3 — Payment Success Rate by Vendor
# -----------------------
print("\n[3] Payment success rate by vendor")
df = query(f"""
    SELECT
        vendor,
        COUNT(*) as total_payments,
        COUNTIF(status = 'SUCCESS') as successful,
        ROUND(COUNTIF(status = 'SUCCESS') / COUNT(*) * 100, 2) as success_rate_pct
    FROM `{project}.{dataset}.fact_payments`
    WHERE status IS NOT NULL
    GROUP BY vendor
    ORDER BY success_rate_pct ASC
""")
print(df.to_string(index=False))
low = df[df["success_rate_pct"] < 70]
if len(low) > 0:
    issues_found += 1
    print(f"    ⚠ WARNING: {len(low)} vendor(s) have payment success rate below 70%")
else:
    print(f"    ✓ All vendors have acceptable payment success rates")

# -----------------------
# Check 4 — Orders With No Matching Payment
# -----------------------
print("\n[4] Orders with no payment record")
df = query(f"""
    SELECT COUNT(*) as count
    FROM `{project}.{dataset}.fact_orders` o
    LEFT JOIN `{project}.{dataset}.fact_payments` p
        ON o.order_id = p.order_id
    WHERE p.order_id IS NULL
""")
count = df["count"][0]
print(f"    → {count} orders have no payment record")
if count > 0:
    issues_found += 1
    print(f"    ⚠ WARNING: These orders were never paid or payment data is missing")
else:
    print(f"    ✓ All orders have payment records")

# -----------------------
# Check 5 — Payments With No Matching Order
# -----------------------
print("\n[5] Payments with no matching order")
df = query(f"""
    SELECT COUNT(*) as count
    FROM `{project}.{dataset}.fact_payments` p
    LEFT JOIN `{project}.{dataset}.fact_orders` o
        ON p.order_id = o.order_id
    WHERE o.order_id IS NULL
""")
count = df["count"][0]
print(f"    → {count} payments reference an order that doesn't exist")
if count > 0:
    issues_found += 1
    print(f"    ⚠ WARNING: Possible late-arriving orders or data loss")
else:
    print(f"    ✓ All payments are linked to valid orders")

# -----------------------
# Check 6 — Late Arriving Refunds
# Refund arriving more than 7 days after order
# -----------------------
print("\n[6] Late arriving refunds (>7 days after order)")
df = query(f"""
    SELECT COUNT(*) as count
    FROM `{project}.{dataset}.fact_refunds` r
    JOIN `{project}.{dataset}.fact_orders` o
        ON r.order_id = o.order_id
    WHERE r.refunded_at IS NOT NULL
      AND o.order_time IS NOT NULL
      AND TIMESTAMP_DIFF(
            TIMESTAMP(r.refunded_at),
            TIMESTAMP(o.order_time),
            DAY
          ) > 7
""")
count = df["count"][0]
print(f"    → {count} refunds arrived more than 7 days after the order")
if count > 0:
    issues_found += 1
    print(f"    ⚠ WARNING: Late refunds may cause revenue to be overstated on original date")
else:
    print(f"    ✓ No late arriving refunds detected")

# -----------------------
# Check 7 — Duplicate Event IDs in MongoDB
# -----------------------
print("\n[7] Duplicate event IDs in MongoDB raw store")
all_events = list(collection.find({}, {"_id": 0, "event_id": 1}))
df_events = pd.DataFrame(all_events)
total = len(df_events)
unique = df_events["event_id"].nunique()
dupes = total - unique
print(f"    → Total events: {total} | Unique: {unique} | Duplicates: {dupes}")
if dupes > 0:
    issues_found += 1
    print(f"    ⚠ WARNING: {dupes} duplicate event IDs found — deduplication is working correctly")
else:
    print(f"    ✓ No duplicate event IDs in raw store")

# -----------------------
# Check 8 — Days With Zero Revenue
# -----------------------
print("\n[8] Days with zero gross revenue")
df = query(f"""
    SELECT order_date, total_orders, gross_revenue
    FROM `{project}.{dataset}.fact_order_daily`
    WHERE gross_revenue = 0 OR gross_revenue IS NULL
    ORDER BY order_date
""")
print(f"    → {len(df)} days have zero or null revenue")
if len(df) > 0:
    issues_found += 1
    print(df.to_string(index=False))
    print(f"    ⚠ WARNING: These days may have missing amount data")
else:
    print(f"    ✓ All days have revenue recorded")

# -----------------------
# Check 9 — Average Order to Payment Time
# -----------------------
print("\n[9] Average time from order creation to payment")
df = query(f"""
    SELECT
        ROUND(AVG(
            TIMESTAMP_DIFF(
                TIMESTAMP(p.paid_at),
                TIMESTAMP(o.order_time),
                HOUR
            )
        ), 2) as avg_hours_to_payment
    FROM `{project}.{dataset}.fact_orders` o
    JOIN `{project}.{dataset}.fact_payments` p
        ON o.order_id = p.order_id
    WHERE p.status = 'SUCCESS'
      AND p.paid_at IS NOT NULL
      AND o.order_time IS NOT NULL
""")
avg_hours = df["avg_hours_to_payment"][0]
print(f"    → Average time from order to successful payment: {avg_hours} hours")
if avg_hours and avg_hours > 48:
    issues_found += 1
    print(f"    ⚠ WARNING: Average payment time exceeds 48 hours")
else:
    print(f"    ✓ Payment time is within acceptable range")

# -----------------------
# Check 10 — Refund Rate Overall
# -----------------------
print("\n[10] Overall refund rate")
df = query(f"""
    SELECT
        COUNT(DISTINCT o.order_id) as total_orders,
        COUNT(DISTINCT r.order_id) as refunded_orders,
        ROUND(COUNT(DISTINCT r.order_id) / COUNT(DISTINCT o.order_id) * 100, 2) as refund_rate_pct
    FROM `{project}.{dataset}.fact_orders` o
    LEFT JOIN `{project}.{dataset}.fact_refunds` r
        ON o.order_id = r.order_id
""")
print(df.to_string(index=False))
rate = df["refund_rate_pct"][0]
if rate and rate > 20:
    issues_found += 1
    print(f"    ⚠ WARNING: Refund rate of {rate}% exceeds acceptable threshold of 20%")
else:
    print(f"    ✓ Refund rate is within acceptable range")

# -----------------------
# Summary
# -----------------------
print("\n" + "=" * 60)
print(f"  Report Complete — {issues_found} issue(s) flagged")
print(f"  Report saved to: {report_filename}")
print("=" * 60)

# -----------------------
# Close Report File
# Restore stdout back to normal terminal output
# -----------------------
sys.stdout = sys.stdout.terminal
report_file.close()
print(f"\nReport saved to: {report_filename}")
