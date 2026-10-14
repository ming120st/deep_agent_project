import os

from dotenv import load_dotenv
from pymongo import AsyncMongoClient


load_dotenv()


MONGODB_URI = os.getenv("MONGODB_URI")
MONGODB_DB_NAME = os.getenv(
    "MONGODB_DB_NAME",
    "deep_agent",
)


if not MONGODB_URI:
    raise RuntimeError("MONGODB_URI is not configured")


mongo_client = AsyncMongoClient(MONGODB_URI)

mongo_db = mongo_client[MONGODB_DB_NAME]