# src/infrastructure/db/mongo.py

import os
from pymongo import MongoClient

def get_client() -> MongoClient:
    uri = os.environ.get("MONGO_URI", "mongodb://localhost:27017")
    return MongoClient(uri, serverSelectionTimeoutMS=5000)

def get_db():
    client = get_client()
    db_name = os.environ.get("MONGO_DB", "trends")
    return client[db_name]