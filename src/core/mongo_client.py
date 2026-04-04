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
boa_members_collection = db["boa_members"]
recipients_collection = db["boa_report_recipients"]
boa_spaces_collection = db["boa_spaces"]
boa_agents_collection = db["boa_agents"]
boa_knowledge_collection = db["boa_knowledge"]
boa_knowledge_versions_collection = db["boa_knowledge_versions"]
boa_device_jobs_collection = db["boa_device_jobs"]
task_traces_collection = db["task_traces"]

# checkpointer용 동기 pymongo 클라이언트
sync_client = MongoClient(MONGODB_URI, tlsCAFile=certifi.where())
checkpointer = MongoDBSaver(sync_client, db_name="boa_db", ttl=60*60*24*1)  # TTL 1일 (초 단위)
session_logs_collection = db['session_logs']
# session_logs_collection.create_index('session_id', unique=True)
# session_logs_collection.create_index('user_id')
# session_logs_collection.create_index('started_at')
# session_logs_collection.create_index('turns.events.task_id')
