from pymongo import MongoClient
import os
from dotenv import load_dotenv

# Load environment variables from .env
load_dotenv()

mongo_uri = os.getenv("MONGO_URI")
mongo_db = os.getenv("MONGO_DB")

# Connect to MongoDB
client = MongoClient(mongo_uri)
db = client[mongo_db]

print("MongoDB connection successful!")
print("Collections:", db.list_collection_names())
