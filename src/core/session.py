import pytz
from datetime import datetime, timedelta
import asyncio
import time
import os
from typing import Dict, Optional
from dotenv import load_dotenv
from src.workflow.graph_infra.state import GraphState

load_dotenv()

# 세션 관련 셋팅
session_lock = asyncio.Lock()  # 세션 동기화를 위한 Lock
SESSION_TIMEOUT_SECONDS = 3600  # 세션 타임아웃 1시간

# 인메모리 세션 저장소
_sessions: Dict[str, GraphState] = {}
_session_timestamps: Dict[str, float] = {}  # 세션 마지막 활동 시간

# 세션관리 함수
async def get_or_create_state(user_id: str) -> GraphState:
    current_time = int(time.time())
    
    async with session_lock:
        state = _sessions.get(user_id)
        last_activity = _session_timestamps.get(user_id)
        
        if state:
            # 타임아웃 체크
            if last_activity and (current_time - last_activity > SESSION_TIMEOUT_SECONDS):
                print("세션 타임아웃 -> 새로 초기화")
                state = GraphState(user_id=user_id, last_activity_time=current_time)
                _sessions[user_id] = state
            else:
                print("기존 세션 유지")
                # 객체가 이미 메모리에 있으므로 별도 로드 필요 없음
        else:
            # 상태가 없으면 새로 생성
            print("새로운 세션 생성")
            state = GraphState(user_id=user_id, last_activity_time=current_time)
            _sessions[user_id] = state
        
        # 마지막 활동 시간 갱신
        _session_timestamps[user_id] = current_time
        state.last_activity_time = current_time

        return state

# 상태 업데이트 함수 (필요시 명시적 저장용 - 인메모리라 사실상 불필요하지만 인터페이스 유지)
async def save_state(user_id: str, state: GraphState):
    async with session_lock:
        _sessions[user_id] = state
        _session_timestamps[user_id] = int(time.time())

def today():
    korea = pytz.timezone('Asia/Seoul') 
    now = datetime.now(korea)
    return now.strftime("%Y%m%d")

def yesterday():
    korea = pytz.timezone('Asia/Seoul')
    now = datetime.now(korea)
    one_day = timedelta(days=1)
    yesterday = now - one_day
    return yesterday.strftime('%Y%m%d')

def currTime():
    korea = pytz.timezone('Asia/Seoul')
    now = datetime.now(korea)
    formatted_now = now.strftime("%Y.%m.%d %H:%M:%S")
    return formatted_now