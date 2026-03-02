from pymongo import MongoClient
import datetime
import certifi
import os
from dotenv import load_dotenv

# 환경 변수 로드
load_dotenv()
MONGODB_URI = os.getenv("MONGODB_URI")

# MongoDB 연결
client = MongoClient(MONGODB_URI, tlsCAFile=certifi.where())
db = client["boa_db"]  # 사용할 데이터베이스 이름
boa_tasks_collection = db["boa_tasks"]  # 로그를 저장할 컬렉션
boa_admins_collection = db["boa_admins"] # 관리자 정보 저장 컬렉션
boa_sops_collection = db["boa_sops"] # SOP 정보 저장 컬렉션
boa_memories_collection = db["boa_memories"] # 에피소드 기억 저장 컬렉션