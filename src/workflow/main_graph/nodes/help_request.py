"""
도움말 카드 노드 (Help Request Node)

help_request 의도 분류 시 호출.
AGENT_REGISTRY / SCHEDULE_REGISTRY / TRIGGER_REGISTRY에서
visible=True인 항목을 읽어 Google Chat Card v2로 전송합니다.

[카드 구성]
  - 전체 기능 문의 → 전체 목록 카드 (에이전트별 버튼 포함)
  - 특정 에이전트 문의 → 해당 에이전트 usage_guide 상세 카드

[특징]
  - send_response를 거치지 않고 Google Chat API로 직접 카드 전송
  - 그래프에서 END로 직접 연결됨
"""
from src.workflow.graph_infra.state import GraphState
from src.core.google_chat import send_card
from src.agent.registry import AGENT_REGISTRY, SCHEDULE_REGISTRY, TRIGGER_REGISTRY
from src.core.session_logger import add_event, update_turn_meta, EventType

# ── 에이전트 타입별 아이콘 매핑 ───────────────────────────────────────────────
_AGENT_ICONS = {
    "wifi_usage_inquiry":    "DESCRIPTION",
    "esim_order_cancellation": "CONFIRMATION_NUMBER_ICON",
    "bigquery_agent":        "STAR",
    "esim_resend":           "EMAIL",
    "esim_fault_replacement": "INVITE",
    "wifi_line_change":      "PHONE",
}
_DEFAULT_CHAT_ICON     = "PERSON"
_DEFAULT_SCHEDULE_ICON = "CLOCK"
_DEFAULT_TRIGGER_ICON  = "CLOCK"


# ── 카드 빌더 ─────────────────────────────────────────────────────────────────

def _build_agent_row(agent_key: str, info: dict) -> dict:
    """단일 에이전트 행 (decoratedText 위젯) 생성."""
    return {
        "decoratedText": {
            "startIcon": {
                "knownIcon": _AGENT_ICONS.get(agent_key, _DEFAULT_CHAT_ICON)
            },
            "topLabel": info.get("display_name", agent_key),
            "text": info.get("description_short", "")[:60],  # 카드 너비 고려해 60자 제한
        }
    }


def _build_help_list_card() -> dict:
    """
    전체 기능 목록 카드.
    visible=True 항목만 포함.
    에이전트 섹션 하단에 사용방법 칩 버튼 목록 추가.
    """
    # ── 업무 에이전트 섹션 ──────────────────────────────────────────────────
    chat_widgets = []
    chip_buttons = []

    for key, info in AGENT_REGISTRY.items():
        if not info.get("visible", False):
            continue
        chat_widgets.append(_build_agent_row(key, info))
        chip_buttons.append({
            "text": f"{info.get('display_name', key)} 사용방법",
            "onClick": {
                "action": {
                    "function": "showAgentHelp",
                    "parameters": [{"key": "agent_key", "value": key}],
                }
            },
        })

    # ── 자동 실행 섹션 (SCHEDULE + TRIGGER 합산) ────────────────────────────
    auto_widgets = []

    for key, info in {**SCHEDULE_REGISTRY, **TRIGGER_REGISTRY}.items():
        if not info.get("visible", False):
            continue
        icon = (
            _DEFAULT_SCHEDULE_ICON
            if info.get("type") == "schedule"
            else _DEFAULT_TRIGGER_ICON
        )
        auto_widgets.append({
            "decoratedText": {
                "startIcon": {"knownIcon": icon},
                "topLabel": info.get("display_name", key),
                "text": info.get("description_short") or info.get("description", "")[:60],  # 카드 너비 고려해 60자 제한
            }
        })

    # ── 섹션 조립 ──────────────────────────────────────────────────────────
    sections = []

    if chat_widgets:
        sections.append({
            "header": "💬 업무 에이전트",
            "collapsible": False,
            "widgets": chat_widgets,
        })

    if auto_widgets:
        sections.append({
            "header": "⚙️ 자동 실행",
            "collapsible": True,
            "uncollapsibleWidgetsCount": 0,
            "widgets": auto_widgets,
        })

    # 사용방법 버튼 목록
    if chip_buttons:
        sections.append({
            "header": "📖 사용방법 보기",
            "collapsible": False,
            "widgets": [{"buttonList": {"buttons": chip_buttons}}],
        })

    return {
        "cardsV2": [{
            "cardId": "boa-help-card",
            "card": {
                "header": {
                    "title": "BOA 기능 안내",
                    "subtitle": "사용 가능한 에이전트 목록",
                    "imageUrl": (
                        "https://fonts.gstatic.com/s/i/googlematerialicons"
                        "/smart_toy/v1/googlematerialicons-smart_toy-24px.svg"
                    ),
                    "imageType": "CIRCLE",
                },
                "sections": sections,
            },
        }]
    }


def _build_agent_detail_card(agent_key: str, info: dict) -> dict:
    """특정 에이전트 상세 사용방법 카드."""
    display_name = info.get("display_name", agent_key)
    usage_guide  = info.get("usage_guide", "사용 가이드가 준비 중입니다.")

    return {
        "cardsV2": [{
            "cardId": "boa-agent-detail-card",
            "card": {
                "header": {
                    "title": display_name,
                    "subtitle": "사용 방법 안내",
                    "imageUrl": (
                        "https://fonts.gstatic.com/s/i/googlematerialicons"
                        "/help_outline/v1/googlematerialicons-help_outline-24px.svg"
                    ),
                    "imageType": "CIRCLE",
                },
                "sections": [
                    {
                        "collapsible": False,
                        "widgets": [
                            {
                                "textParagraph": {
                                    "text": usage_guide
                                }
                            }
                        ],
                    }
                ],
            },
        }]
    }


# ── 전체 레지스트리에서 agent_key로 조회 ───────────────────────────────────────
def _find_in_all_registries(agent_key: str) -> dict | None:
    for registry in (AGENT_REGISTRY, SCHEDULE_REGISTRY, TRIGGER_REGISTRY):
        if agent_key in registry:
            return registry[agent_key]
    return None


# ── 메인 노드 ─────────────────────────────────────────────────────────────────

async def execute_help_request(state: GraphState):
    """
    [노드] 도움말 카드 전송

    - action.agent_name이 있으면 해당 에이전트 상세 카드
    - 없으면 전체 기능 목록 카드

    send_response를 거치지 않고 직접 전송 후 END로 연결됨.
    """
    session_id = state.session_id
    await update_turn_meta(session_id, intent="help_request")

    space_name  = state.chat_context.get("space_name", "")
    action_data = getattr(state, "action", {}) or {}
    agent_key   = action_data.get("agent_name")  # 특정 에이전트 문의 시 세팅됨

    if agent_key:
        info = _find_in_all_registries(agent_key)
        if info:
            card_body = _build_agent_detail_card(agent_key, info)
            log_content = f"에이전트 상세: {agent_key}"
        else:
            # 레지스트리에 없는 agent_key → 전체 목록으로 폴백
            card_body   = _build_help_list_card()
            log_content = "전체 목록 (unknown agent_key 폴백)"
    else:
        card_body   = _build_help_list_card()
        log_content = "전체 목록"

    await add_event(session_id, EventType.CHAT_SEND, "도움말 카드 전송",
                    content=log_content)

    if space_name:
        await send_card(space_name, card_body)
    else:
        print("[HelpRequest] space_name 없음 — 카드 전송 불가")

    # send_response를 거치지 않으므로 state 반환만
    return {}