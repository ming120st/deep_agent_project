"""
메인 대화 그래프 (Main Conversation Graph)

Google Chat 메시지 수신부터 응답 전송까지의 전체 대화 흐름을
하나의 LangGraph StateGraph로 관리합니다.

────────────────────────────────────────────────────────────────
  플로우 다이어그램

  START
    │
    ▼ (항상 의도 분류 먼저)
  [classify_agent_action]  ←── LLM 의도 분류
    │
    ▼ [route_by_action] — actionResult에 따라 라우팅
  ┌──────────┬───────────────┼──────────────┬──────────────┐
  │new_agent │ continue_task │     rag      │context_response│
       ▼           ▼                ▼              ▼       │
     execute_   update_        execute_rag  execute_context_ │
     <agent>  parameters                       response    │
  │          │               │              │              │
  │                ▼         │              │              │              
  │           [route_after_  │              │              │
  │           update_params] │              │              │
  │          │               │              │              │
  └──────────┴───────────────┴──────────────┴──────────────┘
                              │
                        [send_response]
                              │
                            END
────────────────────────────────────────────────────────────────
"""
from typing import Literal

from langgraph.graph import StateGraph, START, END

from src.workflow.graph_infra.state import GraphState
from src.workflow.main_graph.nodes.action_classifier import classify_agent_action
from src.workflow.main_graph.nodes.response_sender import send_response
from src.agent.registry import AGENT_REGISTRY
from src.core.task_manager import create_task, TaskStatus
from src.core.mongo_client import checkpointer
from src.core.mongo_client import get_collection

from src.core.session_logger import add_event, session_event, update_turn_meta, EventType, build_llm_meta

import logging
logger = logging.getLogger(__name__)

# 워크플로우(서브에이전트) 추가 시, 해당 워크플로우의 그래프를 import하고, AGENT_REGISTRY에 에이전트 정보 등록해야함

def _extract_text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(b.get("text", "") if isinstance(b, dict) else str(b) for b in content)
    return str(content)

async def execute_rag(state: GraphState):
    """
    RAG 노드: TopicGraphRetriever로 지식 탐색 → 하이브리드 정책 적용 → LLM 답변 생성
    """
    session_id = state.session_id
    await update_turn_meta(session_id, intent="rag")

    from src.core.topic_graph_retriever import TopicGraphRetriever
    from src.core.gap_recorder import record_gap
    from src.core.llm_config import LLM_LIGHT

    user_query = state.get_last_user_message() or ""

    # ── 1. TopicGraphRetriever 검색 ───────────────────────────────
    retriever = TopicGraphRetriever()
    try:
        result = await retriever.retrieve(query=user_query, session_id=session_id)

        await add_event(session_id, EventType.RAG_SEARCH, "지식베이스 탐색",
                        content=f"{result.total_hops}홉, {len(result.visited_topic_ids)}개 주제",
                        meta={
                            "docs": result.visited_docs,
                            "has_context": result.has_context,
                            "gap_flag": result.gap_flag,
                        })
    except Exception as e:
        logger.error("[RAG] retriever 실패: %s", e)
        result = None

    # ── 2. gap 기록 ───────────────────────────────────────────────
    if result and result.gap_flag:
        try:
            await record_gap(
                query=user_query,
                session_id=session_id,
                user_member_id=state.member_id if hasattr(state, "member_id") else None,
                searched_topics=[{"topic_id": t, "read": True} for t in (result.visited_topic_ids or [])],
                ai_verdict="no_match" if not result.has_context else "insufficient_coverage",
            )
        except Exception as e:
            logger.error("[RAG] gap 기록 실패: %s", e)

    # ── 3. 시스템 프롬프트 결정 ──────────────
    if result and result.has_context:
        system_prompt = """당신은 와이드모바일의 내부 업무 지원 AI(BOA)입니다.
아래 지식베이스 내용을 참고하여 질문에 답변하세요.
문서에 없는 내용은 일반 지식을 활용해 보완할 수 있습니다.

{context}"""
        prompt = system_prompt.format(context=result.context_xml)
    else:
        prompt = """당신은 와이드모바일의 내부 업무 지원 AI(BOA)입니다.
지식베이스에서 관련 내용을 찾지 못했습니다.
일반적인 답변을 제공하거나, 정확한 정보를 위해 담당자에게 확인을 권유하세요."""

    # ── 4. LLM 답변 생성 ──────────────────────────────────────────
    from langchain_core.messages import SystemMessage, HumanMessage, AIMessage

    lc_messages = [SystemMessage(content=prompt)]
    for m in state.messages[:-1]:
        if m["role"] == "user":
            lc_messages.append(HumanMessage(content=m["content"]))
        elif m["role"] == "assistant":
            lc_messages.append(AIMessage(content=m["content"]))
    lc_messages.append(HumanMessage(content=user_query))

    try:
        async with session_event(session_id, EventType.LLM_RESPONSE, "RAG 답변 생성") as ev:
            response = await LLM_LIGHT.ainvoke(lc_messages)
            answer = _extract_text(response.content)
            ev.content = answer[:200]
            ev.llm = build_llm_meta("gemini-2.5-flash", getattr(response, "usage_metadata", {}))
    except Exception as e:
        logger.error("[RAG] LLM 호출 실패: %s", e)
        answer = "답변 생성 중 오류가 발생했습니다. 잠시 후 다시 시도해 주세요."

    # ── 5. state에 답변 추가 (send_response 노드가 Google Chat 전송) ─
    state.add_ai_message(answer)
    return state

def handle_unclear(state: GraphState):
    # actionResult가 예상 범위를 벗어났을 때 사용자에게 재입력을 요청하는 응답을 추가
    state.add_ai_message("죄송합니다. 말씀하신 내용을 정확히 이해하지 못했습니다. 다시 한 번 말씀해 주시겠어요?")
    return state

async def handle_ambiguous(state: GraphState):
    """
    사용자 의도가 애매모호할 때, 라우터가 남긴 reason을 바탕으로
    사용자에게 명확한 의도 확인을 요청하는 응답을 LLM을 통해 생성합니다.
    """
    session_id = state.session_id
    action_data = getattr(state, "action", {})
    reason = action_data.get("reason", "사용자 요청의 의도가 불분명합니다.")
    
    from src.core.llm_config import LLM_LIGHT
    from langchain_core.messages import SystemMessage, HumanMessage, AIMessage

    system_prompt = f"""당신은 와이드모바일의 친절한 업무 지원 AI 시락이입니다.
현재 사용자의 요청이 명확하지 않거나 모호한 경계의 작업이 있어 진행할 수 없는 상황입니다.
의도 분류 AI가 판단한 '의도가 모호한 이유'는 다음과 같습니다:
[{reason}]

이를 바탕으로 사용자에게 어떤 점을 더 구체적으로 말해달라고 요청할지 친절하고 자연스러운 응답(1~2문장)을 작성하세요.
기계적인 말투를 피하고, 사용자가 필요한 것이 이것인지 저것인지 등 구체적으로 되묻는 형식을 취하면 좋습니다.
해결책을 지레짐작해서 답변하지 말고, 명확한 의도를 파악하는 데에만 집중하세요."""

    lc_messages = [SystemMessage(content=system_prompt)]
    # 최근 3~4개의 대화 내역만 컨텍스트로 전달
    for m in state.messages[-4:]:
        if m["role"] == "user":
            lc_messages.append(HumanMessage(content=m["content"]))
        elif m["role"] == "assistant":
            lc_messages.append(AIMessage(content=m["content"]))

    try:
        async with session_event(session_id, EventType.LLM_RESPONSE, "모호성 확인 응답 생성") as ev:
            response = await LLM_LIGHT.ainvoke(lc_messages)
            answer = _extract_text(response.content)
            ev.content = answer[:200]
            ev.llm = build_llm_meta("gemini-2.5-flash", getattr(response, "usage_metadata", {}))
    except Exception as e:
        logger.error(f"[handle_ambiguous] LLM 호출 실패: {e}")
        answer = "말씀하신 의도가 명확하지 않아 어떤 작업을 도와드려야 할지 잘 모르겠습니다. 다시 한 번 구체적으로 말씀해 주시겠어요?"

    state.add_ai_message(answer)
    return {"messages": state.messages}

def handle_error(state: GraphState):
    state.add_ai_message("시스템 처리 중 오류가 발생했습니다. 잠시 후 다시 시도해 주세요.")
    return state

# ── 파라미터 업데이트 노드 ─────────────────────────────────────────────────────────────
async def update_parameters(state: GraphState):
    session_id = state.session_id
    action_data = getattr(state, "action", {})
    updates = {}

    collected_parameters = dict(state.collected_parameters)  # 기존 파라미터 복사
    
    agent_name = action_data.get("agent_name")
    current_agent = state.current_agent
    if agent_name:
        current_agent = agent_name
        updates["current_agent"] = current_agent

    domain_id = action_data.get("domain_id") or state.domain_id
    if not domain_id and current_agent:
        agent_doc = await get_collection("agent_registry").find_one({"agent_id": current_agent})
        if agent_doc:
            domain_id = agent_doc.get("domain_id")

    if domain_id:
        updates["domain_id"] = domain_id
        updates["team_input"] = {
            "domain_id": domain_id,
            "agent_id": current_agent,
            "task": current_agent,
            "user_message": state.get_last_user_message() or "",
            "boa_intent": action_data.get("action"),
            "boa_extracted": action_data.get("parameters") or {},
        }
    
    if current_agent and isinstance(action_data, dict):        
        if current_agent not in collected_parameters:
            collected_parameters[current_agent] = {}
        else:
            collected_parameters[current_agent] = dict(collected_parameters[current_agent])
            
        if action_data.get("parameters"):
            collected_parameters[current_agent].update(action_data["parameters"])

        if action_data.get("action") == "new_agent":
            collected_parameters[current_agent].setdefault("intent_confirmed", True)

        updates["collected_parameters"] = collected_parameters

    if action_data.get("action") == "new_agent":
        agent_query = {"agent_id": current_agent}
        if domain_id:
            agent_query["domain_id"] = domain_id
        agent_doc = await get_collection("agent_registry").find_one(agent_query)
        if agent_doc and not agent_doc.get("enabled", True):
            msg = "죄송합니다. 현재 해당 에이전트는 사용이 중지되어 있습니다. AI팀에 문의해 주세요."
            state.add_ai_message(msg)  # state.messages 뮤테이션
            return {
                "messages": state.messages,  # 뮤테이션된 messages 전체를 반환에 포함
                "current_agent": None,       # 라우팅 차단
            }

    # ── new_task True이면 task 생성 ──────────────────────────────
    if action_data.get("new_task") is True:
        updates["error_message"] = None # 새로운 업무 시작이므로 기존 오류 메시지 초기화
        updates["task_status"] = None
        updates["response"] = {}

        collected_parameters[current_agent] = {}
        if action_data.get("parameters"):
            collected_parameters[current_agent].update(action_data["parameters"])
        updates["collected_parameters"] = collected_parameters

        agent_info = AGENT_REGISTRY.get(current_agent, {})
        task_id = await create_task(
            user_id=state.chat_context.get("user_id", ""),
            user_name=state.chat_context.get("user_name", ""),
            agent_name=current_agent,
            agent_display_name=agent_info.get("display_name", current_agent),
            parameters={},
        )
        updates["task_id"] = task_id
        
        await update_turn_meta(session_id, intent="new_agent", task_id=task_id)
        await add_event(session_id, EventType.AGENT_ROUTE, "에이전트 라우팅",
                        content=current_agent, task_id=task_id)
        print(f"[Task] 새 task 생성: {task_id} ({current_agent})")
    else:
        # continue_task 시
        await update_turn_meta(session_id, intent="continue_task")
        await add_event(session_id, EventType.AGENT_ROUTE, "에이전트 라우팅",
                        content=current_agent, task_id=state.task_id)

    return updates

# ── 라우팅 함수 ───────────────────────────────────────────────────────────────

def route_by_action(state: GraphState):
    action_data = getattr(state, "action", {})
    if not isinstance(action_data, dict):
        return "handle_unclear"

    action_type = action_data.get("action")

    if action_type == "rag":
        return "execute_rag"
    elif action_type == "context_response":
        return "execute_context_response"
    elif action_type == "help_request":
        return "execute_help_request"
    elif action_type in ("new_agent", "continue_task"):
        return "extract_parameters"
    elif action_type == "ambiguous":
        return "handle_ambiguous"

    return "handle_unclear"

def route_after_update_parameters(state: GraphState):
    domain_id = state.domain_id
    if domain_id:
        return f"{domain_id}_domain"
    return "handle_error"

# ── 그래프 빌더 ───────────────────────────────────────────────────────────────

def build_main_graph():
    import importlib
    from pathlib import Path
    
    # 순환참조 방지 위해 그래프 빌더 내부에서 임포트
    from src.workflow.main_graph.nodes.parameter_extractor import extract_parameters
    from src.workflow.main_graph.nodes.context_response import execute_context_response
    from src.workflow.main_graph.nodes.help_request import execute_help_request

    # 그래프 인스턴스 생성
    workflow = StateGraph(GraphState)

    # ── 고정 노드 등록 ──────────────────────────────────────────────
    workflow.add_node("classify_agent_action", classify_agent_action)
    workflow.add_node("extract_parameters", extract_parameters)
    workflow.add_node("execute_rag", execute_rag)
    workflow.add_node("execute_context_response", execute_context_response)
    workflow.add_node("update_parameters", update_parameters)
    workflow.add_node("handle_unclear", handle_unclear)
    workflow.add_node("handle_ambiguous", handle_ambiguous)
    workflow.add_node("handle_error", handle_error)
    workflow.add_node("send_response", send_response)
    workflow.add_node("execute_help_request", execute_help_request)

    # ── 엣지 ──────────────────────────────────────────────────────
    workflow.add_edge(START, "classify_agent_action")

    workflow.add_conditional_edges(
        "classify_agent_action",
        route_by_action,
        {
            "extract_parameters": "extract_parameters",  # ← new_agent, continue_task 둘 다
            "execute_rag": "execute_rag",
            "execute_context_response": "execute_context_response",
            "execute_help_request":    "execute_help_request",
            "handle_ambiguous": "handle_ambiguous",
            "handle_unclear": "handle_unclear",
        },
    )

    # extract_parameters → update_parameters (항상)
    workflow.add_edge("extract_parameters", "update_parameters")

    # ── 도메인별 서브그래프 동적 로드 & 등록 ────────────────────────
    agent_dir = Path(__file__).parent.parent.parent / "agent"
    domain_routing = {"handle_error": "handle_error"}

    for path in agent_dir.iterdir():
        if path.is_dir() and (path / "graph.py").is_file():
            domain_name = path.name
            node_name = f"{domain_name}_domain"
            try:
                module = importlib.import_module(f"src.agent.{domain_name}.graph")
                domain_graph = getattr(module, f"{domain_name}_domain_graph")

                workflow.add_node(node_name, domain_graph)
                workflow.add_edge(node_name, "send_response")
                domain_routing[node_name] = node_name
                logger.info(f"[Graph Builder] 동적 도메인 노드 등록 완료: {node_name}")
            except Exception as e:
                logger.error(f"[Graph Builder] 도메인 동적 로드 실패 ({domain_name}): {e}")

    workflow.add_conditional_edges("update_parameters", route_after_update_parameters, domain_routing)

    for node_name in ["execute_rag", "execute_context_response", "handle_unclear", "handle_ambiguous", "handle_error"]:
        workflow.add_edge(node_name, "send_response")

    workflow.add_edge("execute_help_request", END) # 도움말 노드는 send_response를 거치지 않고 바로 END로 연결 (카드 전송 후 대화 종료)
    workflow.add_edge("send_response", END)

    return workflow.compile(checkpointer=checkpointer)

# ── 싱글톤 그래프 인스턴스 ────────────────────────────────────────────────────
main_graph = build_main_graph()
