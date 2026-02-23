# -----------------------
# Imports & Setup
# -----------------------
import os
import pandas as pd
from datetime import datetime
from pymongo import MongoClient
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Connect to MongoDB
client = MongoClient(os.getenv("MONGO_URI"))
db = client[os.getenv("MONGO_DB")]
collection = db["events_raw"]

# Load all events from MongoDB into a DataFrame
all_events = list(collection.find({}, {"_id": 0}))
df = pd.DataFrame(all_events)

print(f"Total raw events loaded: {len(df)}")
print("Event types found:", df["event_type"].unique())

# -----------------------
# Separate Event Types
# Covers both historical and all live event type name variations
# -----------------------
orders_df = df[df["event_type"].isin([
    "historical_order",
    "order_created",
    "order_updated"
])].copy()

payments_df = df[df["event_type"].isin([
    "historical_payment",
    "payment_succeeded",
    "payment_attempt",
    "payment_confirmed"
])].copy()

refunds_df = df[df["event_type"].isin([
    "historical_refund",
    "refund_issued",
    "refund_processed"
])].copy()

shipments_df = df[df["event_type"].isin([
    "historical_shipment",
    "shipment_updated",
    "shipment_update",
    "shipment_created"
])].copy()

print(f"\nRaw event counts before normalization:")
print(f"  Orders:    {len(orders_df)}")
print(f"  Payments:  {len(payments_df)}")
print(f"  Refunds:   {len(refunds_df)}")
print(f"  Shipments: {len(shipments_df)}")


# -----------------------
# Helper Functions
# Handle schema drift across vendors
# -----------------------

def parse_timestamp(value):
    """
    Convert various date/time formats into a consistent ISO string.
    Handles: Unix timestamps (int/float), ISO strings, and partial datetime strings.
    Returns None if value cannot be parsed.
    """
    if value is None:
        return None
    try:
        # Unix timestamp
        if isinstance(value, (int, float)):
            return datetime.utcfromtimestamp(value).isoformat()
        # String formats — try each one until one works
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
    """
    Extract order ID handling multiple vendor structures:
    - payload["order_id"]       → flat string
    - payload["orderRef"]       → shipment style
    - payload["order"]          → could be a plain string
    - payload["order"]["id"]    → nested dict
    """
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
    """
    Try multiple field names for amount and return first match found.
    Example: get_amount(payload, "amountPaid", "amt", "amount")
    """
    for key in keys:
        if key in payload:
            return payload[key]
    return None


def get_shipping_address(payload):
    """
    Extract shipping address handling multiple structures:
    - payload["shipping"]["address"]  → simple string
    - payload["shipping"]["line1"]    → structured address object
    """
    shipping = payload.get("shipping") or {}
    # Try simple address string first
    if shipping.get("address"):
        return shipping["address"]
    # If not found, try to construct from line1 + city
    line1 = shipping.get("line1", "")
    city = shipping.get("city", "")
    combined = f"{line1} {city}".strip()
    return combined if combined else None


def get_latest_shipment_status(payload):
    """
    Get the most recent shipment status from either
    'updates' (Vendor A) or 'timeline' (Vendor B) arrays.
    Returns (latest_status, latest_time) tuple.
    """
    history = payload.get("updates") or payload.get("timeline") or []
    if not history:
        return None, None

    latest = history[-1]
    return latest.get("status"), parse_timestamp(latest.get("time"))


def get_delivered_at(payload):
    """
    Scan shipment history for a DELIVERED entry
    and return its timestamp if found.
    """
    history = payload.get("updates") or payload.get("timeline") or []
    for entry in history:
        if entry.get("status") == "DELIVERED":
            return parse_timestamp(entry.get("time"))
    return None


# -----------------------
# Normalize Orders
# -----------------------
def normalize_order(event):
    """
    Flatten and normalize an order event into a consistent structure.
    Handles nested order IDs, Unix timestamps, and missing fields.
    """
    payload = event.get("payload", {})
    order_obj = payload.get("order", {})

    # Timestamp can be nested inside payload.order.ts or at top level
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


# -----------------------
# Normalize Payments
# -----------------------
def normalize_payment(event):
    """
    Flatten and normalize a payment event.
    Handles: order_id vs order, amountPaid vs amt,
    payment_status vs state, paid_at vs timestamp vs ts.
    """
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


# -----------------------
# Normalize Refunds
# -----------------------
def normalize_refund(event):
    """
    Flatten and normalize a refund event.
    Handles: refundAmount vs amt, refund_reason vs reason,
    refunded_items vs items_refunded (which can also be null).
    """
    payload = event.get("payload", {})

    # Items can be under either field name and can be null
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


# -----------------------
# Normalize Shipments
# -----------------------
def normalize_shipment(event):
    """
    Flatten and normalize a shipment event.
    Handles: orderRef vs order.id, updates vs timeline arrays,
    and mixed time formats within the same record.
    """
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
# Apply Normalization
# -----------------------
print("\nNormalizing data...")

fact_orders    = pd.DataFrame([normalize_order(e)    for e in orders_df.to_dict("records")])
fact_payments  = pd.DataFrame([normalize_payment(e)  for e in payments_df.to_dict("records")])
fact_refunds   = pd.DataFrame([normalize_refund(e)   for e in refunds_df.to_dict("records")])
fact_shipments = pd.DataFrame([normalize_shipment(e) for e in shipments_df.to_dict("records")])

# Drop rows where order_id couldn't be extracted — can't link them to anything
fact_orders    = fact_orders.dropna(subset=["order_id"])
fact_payments  = fact_payments.dropna(subset=["order_id"])
fact_refunds   = fact_refunds.dropna(subset=["order_id"])
fact_shipments = fact_shipments.dropna(subset=["order_id"])

# -----------------------
# Deduplicate Orders
# An order may appear as historical_order, order_created, AND order_updated
# We keep the latest version to get current state and avoid double counting revenue
# -----------------------
fact_orders = fact_orders.sort_values("order_time")
fact_orders = fact_orders.drop_duplicates(
    subset=["order_id"],
    keep="last" # keep the most recent record for each order_id
)

print(f"\nClean records after normalization and deduplication:")
print(f"  Orders:    {len(fact_orders)}")
print(f"  Payments:  {len(fact_payments)}")
print(f"  Refunds:   {len(fact_refunds)}")
print(f"  Shipments: {len(fact_shipments)}")

# Quick sanity check on revenue numbers before we load into BigQuery
print(f"\nUnique orders:  {fact_orders['order_id'].nunique()}")
print(f"Total revenue:  {fact_orders['amount'].sum():,.2f} NGN")

# -----------------------
# Dimension Tables
# -----------------------

# Customers — unique by email
dim_customer = fact_orders[["customer_id", "customer_email"]].drop_duplicates(
    subset=["customer_email"]
).dropna(subset=["customer_email"])

# Products — placeholder (items are nested, we'll expand in BigQuery step)
dim_product = pd.DataFrame({"product_sku": pd.Series(dtype="str")})

# Date dimension — every unique date in orders with year/month/day breakdown
fact_orders["order_date"] = pd.to_datetime(fact_orders["order_time"], errors="coerce").dt.date

dim_date = pd.DataFrame({
    "date": fact_orders["order_date"].drop_duplicates().dropna()
})
dim_date["year"]  = pd.to_datetime(dim_date["date"]).dt.year
dim_date["month"] = pd.to_datetime(dim_date["date"]).dt.month
dim_date["day"]   = pd.to_datetime(dim_date["date"]).dt.day

# -----------------------
# Daily Aggregate Table
# Groups orders by date to show revenue and order volume per day
# -----------------------
daily_agg = fact_orders.groupby("order_date").agg(
    total_orders=("order_id", "count"),
    total_revenue=("amount", "sum")
).reset_index()

# -----------------------
# Preview Outputs
# -----------------------
print("\n--- Daily Aggregates Sample ---")
print(daily_agg.head(10).to_string())

print("\n--- Orders Sample ---")
print(fact_orders.head(3).to_string())

print("\n--- Payments Sample ---")
print(fact_payments.head(3).to_string())

print("\n--- Refunds Sample ---")
print(fact_refunds.head(3).to_string())

print("\n--- Shipments Sample ---")
print(fact_shipments.head(3).to_string())
