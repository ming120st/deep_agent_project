# ─────────────────────────────────────────────────────────────────
# 에이전트 레지스트리
# 각 에이전트의 이름, 설명, 사용자로부터 수집하는 파라미터 정보를 담고 있습니다.
# 새로운 에이전트를 추가할 때 이 레지스트리에 등록해야 합니다.
# 다양한 곳에서 에이전트 정보를 참조할 때 이 레지스트리만 사용합니다.
# ─────────────────────────────────────────────────────────────────

from typing import Dict, Any
from src.agent.wifi_usage_inquiry.graph import wifi_usage_inquiry_graph
from src.agent.registry import AGENT_REGISTRY

# ─────────────────────────────────────────────────────────────────
# 헬퍼 함수 및 공통 설정
# 필요한 공통 함수나 설정이 있다면 이곳에 추가합니다.
# ─────────────────────────────────────────────────────────────────

# 에이전트 이름 리스트
AGENT_NAMES = list(AGENT_REGISTRY.keys())

# 에이전트 이름+설명 딕셔너리
AGENT_DESCRIPTIONS = {name: info["description"] for name, info in AGENT_REGISTRY.items()}

def get_current_agent_spec(agent_name: str) -> Dict[str, Any]:
    """현재 에이전트의 상세 스펙을 반환하는 헬퍼 함수입니다. (graph 객체 제외)"""
    spec = AGENT_REGISTRY.get(agent_name, {})
    return {k: v for k, v in spec.items() if k != "graph"}