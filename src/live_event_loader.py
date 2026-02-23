import os
import json
from pymongo import MongoClient
from dotenv import load_dotenv

load_dotenv()
client = MongoClient(os.getenv("MONGO_URI"))
collection = client[os.getenv("MONGO_DB")]["events_raw"]

live_events_path = "data/live_events"
inserted = 0
skipped = 0

for day_folder in sorted(os.listdir(live_events_path)):
    day_path = os.path.join(live_events_path, day_folder)
    if not os.path.isdir(day_path):
        continue

    events_file = os.path.join(day_path, "events.jsonl")
    if not os.path.exists(events_file):
        continue

    print(f"Processing {events_file} ...")

    with open(events_file, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError as e:
                print(f"Skipping bad line: {e}")
                continue

            event_id = event.get("event_id")
            if not event_id:
                continue

            result = collection.update_one(
                {"event_id": event_id},
                {"$setOnInsert": event},
                upsert=True
            )

            if result.upserted_id:
                inserted += 1
            else:
                skipped += 1

print(f"\nDone! Inserted: {inserted} | Duplicates skipped: {skipped}")