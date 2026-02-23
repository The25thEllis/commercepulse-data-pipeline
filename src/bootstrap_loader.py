import os
import json
import hashlib
from datetime import datetime
from pymongo import MongoClient
from dotenv import load_dotenv

# Load .env
load_dotenv()
mongo_uri = os.getenv("MONGO_URI")
mongo_db = os.getenv("MONGO_DB")

client = MongoClient(mongo_uri)
db = client[mongo_db]
collection = db["events_raw"]

# Path to bootstrap files
bootstrap_path = "data/bootstrap"

# Map file names to event types
event_type_map = {
    "orders_2023.json": "historical_order",
    "payments_2023.json": "historical_payment",
    "shipments_2023.json": "historical_shipment",
    "refunds_2023.json": "historical_refund"
}

def generate_event_id(record: dict) -> str:
    """Generate deterministic hash based on record content"""
    record_str = json.dumps(record, sort_keys=True)
    return hashlib.md5(record_str.encode("utf-8")).hexdigest()

# Loop through files
for file_name, event_type in event_type_map.items():
    file_path = os.path.join(bootstrap_path, file_name)
    print(f"Processing {file_name} ...")
    
    with open(file_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    
    for record in data:
        event_id = generate_event_id(record)
        # Use a field from the record for event_time, fallback to now
        event_time = record.get("created_at", record.get("order_date", None))
        if event_time is None:
            event_time = datetime.utcnow().isoformat()
        
        vendor = record.get("vendor_id", "unknown")
        
        doc = {
            "event_id": event_id,
            "event_type": event_type,
            "event_time": event_time,
            "vendor": vendor,
            "payload": record,
            "ingested_at": datetime.utcnow().isoformat()
        }
        
        # Insert into MongoDB (upsert to avoid duplicates)
        collection.update_one(
            {"event_id": event_id},
            {"$setOnInsert": doc},
            upsert=True
        )
    print(f"{file_name} loaded successfully.")

print("All historical data loaded into MongoDB!")