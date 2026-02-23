# -----------------------
# Imports & Setup
# -----------------------
import os
import pandas as pd
from datetime import datetime
from pymongo import MongoClient
from google.cloud import bigquery
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Set Google credentials
os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = os.getenv("GOOGLE_APPLICATION_CREDENTIALS")

# BigQuery client
bq_client = bigquery.Client(project=os.getenv("BIGQUERY_PROJECT"))
dataset = os.getenv("BIGQUERY_DATASET")

# MongoDB connection
mongo_client = MongoClient(os.getenv("MONGO_URI"))
collection = mongo_client[os.getenv("MONGO_DB")]["events_raw"]


# -----------------------
# Helper Functions
# -----------------------

def parse_timestamp(value):
    """Convert various date formats to ISO string"""
    if value is None:
        return None
    try:
        if isinstance(value, (int, float)):
            return datetime.utcfromtimestamp(value).isoformat()
        for fmt in [
            "%Y-%m-%dT%H:%M:%SZ",
            "%Y-%m-%dT%H:%M:%S",
            "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%d %H:%M",
            "%Y-%m-%d",
        ]:
            try:
                return datetime.strptime(str(value), fmt).isoformat()
            except ValueError:
                continue
    except Exception:
        pass
    return None


def get_order_id(payload):
    """Extract order ID from multiple vendor structures"""
    if "order_id" in payload:
        return payload["order_id"]
    if "orderRef" in payload:
        return payload["orderRef"]
    order = payload.get("order")
    if isinstance(order, str):
        return order
    if isinstance(order, dict):
        return order.get("id")
    return None


def get_amount(payload, *keys):
    """Try multiple field names for amount"""
    for key in keys:
        if key in payload:
            return payload[key]
    return None


def get_shipping_address(payload):
    """Extract shipping address from multiple structures"""
    shipping = payload.get("shipping") or {}
    if shipping.get("address"):
        return shipping["address"]
    line1 = shipping.get("line1", "")
    city = shipping.get("city", "")
    combined = f"{line1} {city}".strip()
    return combined if combined else None


def get_latest_shipment_status(payload):
    """Get most recent shipment status from updates or timeline"""
    history = payload.get("updates") or payload.get("timeline") or []
    if not history:
        return None, None
    latest = history[-1]
    return latest.get("status"), parse_timestamp(latest.get("time"))


def get_delivered_at(payload):
    """Find DELIVERED timestamp in shipment history"""
    history = payload.get("updates") or payload.get("timeline") or []
    for entry in history:
        if entry.get("status") == "DELIVERED":
            return parse_timestamp(entry.get("time"))
    return None


# -----------------------
# Normalizers
# -----------------------

def normalize_order(event):
    payload = event.get("payload", {})
    order_obj = payload.get("order", {})
    raw_time = None
    if isinstance(order_obj, dict):
        raw_time = order_obj.get("ts")
    if raw_time is None:
        raw_time = (
            payload.get("created_at") or
            payload.get("order_date") or
            event.get("event_time")
        )
    return {
        "order_id":         get_order_id(payload),
        "event_id":         event.get("event_id"),
        "event_type":       event.get("event_type"),
        "vendor":           event.get("vendor", payload.get("vendor_id", "unknown")),
        "customer_email":   payload.get("email"),
        "customer_id":      payload.get("cust_id"),
        "amount":           get_amount(payload, "amount", "amt", "amountPaid"),
        "currency":         payload.get("ccy") or payload.get("currencyCode", "NGN"),
        "region":           (payload.get("geo") or {}).get("region"),
        "shipping_address": get_shipping_address(payload),
        "item_count":       len(payload.get("items", [])),
        "order_time":       parse_timestamp(raw_time),
        "ingested_at":      event.get("ingested_at"),
    }


def normalize_payment(event):
    payload = event.get("payload", {})
    return {
        "event_id":       event.get("event_id"),
        "order_id":       get_order_id(payload),
        "vendor":         event.get("vendor", "unknown"),
        "transaction_id": payload.get("transaction_id") or payload.get("txn"),
        "status":         payload.get("payment_status") or payload.get("state") or payload.get("status"),
        "amount":         get_amount(payload, "amountPaid", "amt", "amount"),
        "currency":       payload.get("currencyCode") or payload.get("ccy", "NGN"),
        "channel":        payload.get("channel") or payload.get("paymentMethod"),
        "paid_at":        parse_timestamp(
                            payload.get("paid_at") or
                            payload.get("timestamp") or
                            payload.get("ts")
                          ),
        "ingested_at":    event.get("ingested_at"),
    }


def normalize_refund(event):
    payload = event.get("payload", {})
    raw_items = payload.get("refunded_items") or payload.get("items_refunded") or []
    item_count = len(raw_items) if isinstance(raw_items, list) else 0
    return {
        "event_id":             event.get("event_id"),
        "order_id":             get_order_id(payload),
        "vendor":               event.get("vendor", "unknown"),
        "amount":               get_amount(payload, "refundAmount", "amt", "amount"),
        "currency":             payload.get("currencyCode") or payload.get("ccy", "NGN"),
        "reason":               payload.get("refund_reason") or payload.get("reason"),
        "items_refunded_count": item_count,
        "refunded_at":          parse_timestamp(
                                    payload.get("refunded_at") or payload.get("ts")
                                ),
        "ingested_at":          event.get("ingested_at"),
    }


def normalize_shipment(event):
    payload = event.get("payload", {})
    latest_status, latest_time = get_latest_shipment_status(payload)
    return {
        "event_id":           event.get("event_id"),
        "order_id":           get_order_id(payload),
        "vendor":             event.get("vendor", "unknown"),
        "carrier":            payload.get("carrier"),
        "tracking_number":    payload.get("tracking"),
        "latest_status":      latest_status,
        "latest_status_time": latest_time,
        "delivered_at":       get_delivered_at(payload),
        "ingested_at":        event.get("ingested_at"),
    }


# -----------------------
# Extract & Normalize from MongoDB
# -----------------------

def extract_and_normalize():
    """Pull all events from MongoDB and return clean DataFrames"""
    print("Extracting events from MongoDB...")
    all_events = list(collection.find({}, {"_id": 0}))
    df = pd.DataFrame(all_events)

    orders_raw    = df[df["event_type"].isin([
        "historical_order", "order_created", "order_updated"
    ])]
    payments_raw  = df[df["event_type"].isin([
        "historical_payment", "payment_succeeded", "payment_attempt", "payment_confirmed"
    ])]
    refunds_raw   = df[df["event_type"].isin([
        "historical_refund", "refund_issued", "refund_processed"
    ])]
    shipments_raw = df[df["event_type"].isin([
        "historical_shipment", "shipment_updated", "shipment_update", "shipment_created"
    ])]

    fact_orders    = pd.DataFrame([normalize_order(e)    for e in orders_raw.to_dict("records")])
    fact_payments  = pd.DataFrame([normalize_payment(e)  for e in payments_raw.to_dict("records")])
    fact_refunds   = pd.DataFrame([normalize_refund(e)   for e in refunds_raw.to_dict("records")])
    fact_shipments = pd.DataFrame([normalize_shipment(e) for e in shipments_raw.to_dict("records")])

    # Drop rows with no order_id
    fact_orders    = fact_orders.dropna(subset=["order_id"])
    fact_payments  = fact_payments.dropna(subset=["order_id"])
    fact_refunds   = fact_refunds.dropna(subset=["order_id"])
    fact_shipments = fact_shipments.dropna(subset=["order_id"])

    # Deduplicate orders — keep latest version of each order
    fact_orders = fact_orders.sort_values("order_time")
    fact_orders = fact_orders.drop_duplicates(subset=["order_id"], keep="last")

    # Add order_date column for aggregations
    fact_orders["order_date"] = pd.to_datetime(
        fact_orders["order_time"], errors="coerce"
    ).dt.date.astype(str)

    print(f"Orders: {len(fact_orders)} | Payments: {len(fact_payments)} | "
          f"Refunds: {len(fact_refunds)} | Shipments: {len(fact_shipments)}")

    return fact_orders, fact_payments, fact_refunds, fact_shipments


# -----------------------
# Build Dimension Tables
# -----------------------

def build_dimensions(fact_orders):
    """Create dimension tables from normalized order data"""

    dim_customer = fact_orders[["customer_id", "customer_email", "vendor"]]\
        .drop_duplicates(subset=["customer_email"])\
        .dropna(subset=["customer_email"])\
        .reset_index(drop=True)

    dim_date = pd.DataFrame({
        "date": fact_orders["order_date"].drop_duplicates().dropna()
    })
    dim_date["year"]  = pd.to_datetime(dim_date["date"]).dt.year
    dim_date["month"] = pd.to_datetime(dim_date["date"]).dt.month
    dim_date["day"]   = pd.to_datetime(dim_date["date"]).dt.day
    dim_date = dim_date.reset_index(drop=True)

    return dim_customer, dim_date


# -----------------------
# Build Daily Aggregate
# -----------------------

def build_daily_aggregate(fact_orders, fact_refunds):
    """
    Build daily aggregate showing gross revenue,
    total refunds, and net revenue per day.
    """
    daily_orders = fact_orders.groupby("order_date").agg(
        total_orders=("order_id", "count"),
        gross_revenue=("amount", "sum")
    ).reset_index()

    fact_refunds["refund_date"] = pd.to_datetime(
        fact_refunds["refunded_at"], errors="coerce"
    ).dt.date.astype(str)

    daily_refunds = fact_refunds.groupby("refund_date").agg(
        total_refunds=("amount", "sum")
    ).reset_index().rename(columns={"refund_date": "order_date"})

    daily_agg = daily_orders.merge(daily_refunds, on="order_date", how="left")
    daily_agg["total_refunds"] = daily_agg["total_refunds"].fillna(0)
    daily_agg["net_revenue"]   = daily_agg["gross_revenue"] - daily_agg["total_refunds"]

    return daily_agg


# -----------------------
# Load DataFrame to BigQuery
# -----------------------

def load_to_bigquery(df, table_name):
    """
    Load a DataFrame into BigQuery.
    - Converts any dict/list columns to strings (fixes mixed type errors)
    - Uses WRITE_TRUNCATE so each run replaces the table (idempotent)
    """
    # Fix mixed type columns — BigQuery can't handle dicts or lists in a column
    for col in df.columns:
        if df[col].dtype == "object":
            df[col] = df[col].apply(
                lambda x: str(x) if isinstance(x, (dict, list)) else x
            )

    table_id = f"{os.getenv('BIGQUERY_PROJECT')}.{dataset}.{table_name}"

    job_config = bigquery.LoadJobConfig(
        write_disposition=bigquery.WriteDisposition.WRITE_TRUNCATE,
        autodetect=True,
    )

    print(f"Loading {len(df)} rows into {table_name}...")
    job = bq_client.load_table_from_dataframe(df, table_id, job_config=job_config)
    job.result()
    print(f"  ✓ {table_name} loaded successfully")


# -----------------------
# Main Pipeline
# -----------------------

if __name__ == "__main__":
    print("=" * 50)
    print("CommercePulse BigQuery Loader")
    print("=" * 50)

    # Step 1 — Extract and normalize from MongoDB
    fact_orders, fact_payments, fact_refunds, fact_shipments = extract_and_normalize()

    # Step 2 — Build dimension tables
    dim_customer, dim_date = build_dimensions(fact_orders)

    # Step 3 — Build daily aggregate
    fact_order_daily = build_daily_aggregate(fact_orders, fact_refunds)

    # Step 4 — Load everything into BigQuery
    print("\nLoading tables into BigQuery...")

    load_to_bigquery(dim_customer,     "dim_customer")
    load_to_bigquery(dim_date,         "dim_date")
    load_to_bigquery(fact_orders,      "fact_orders")
    load_to_bigquery(fact_payments,    "fact_payments")
    load_to_bigquery(fact_refunds,     "fact_refunds")
    load_to_bigquery(fact_shipments,   "fact_shipments")
    load_to_bigquery(fact_order_daily, "fact_order_daily")

    print("\n" + "=" * 50)
    print("All tables loaded into BigQuery successfully!")
    print("=" * 50)
