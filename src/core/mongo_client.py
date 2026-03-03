from motor.motor_asyncio import AsyncIOMotorClient  # 기존 컬렉션용 (async)
from pymongo import MongoClient                      # checkpointer용 (sync)
from langgraph.checkpoint.mongodb import MongoDBSaver
import certifi
import os
from dotenv import load_dotenv

load_dotenv()
MONGODB_URI = os.getenv("MONGODB_URI")

# 기존 Motor 클라이언트 (task, sop, memory 등 비동기 작업용)
async_client = AsyncIOMotorClient(MONGODB_URI, tlsCAFile=certifi.where())
db = async_client["boa_db"]
boa_tasks_collection = db["boa_tasks"]
boa_admins_collection = db["boa_admins"]
boa_sops_collection = db["boa_sops"]
boa_memories_collection = db["boa_memories"]

# checkpointer용 동기 pymongo 클라이언트
sync_client = MongoClient(MONGODB_URI, tlsCAFile=certifi.where())
checkpointer = MongoDBSaver(sync_client, db_name="boa_db", ttl=604800)  # TTL 7일 (초 단위)