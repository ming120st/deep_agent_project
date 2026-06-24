from __future__ import annotations

import json
import os
import asyncio

from langchain_core.messages import HumanMessage

from src.core.internal_esim_client import esim_client
from src.core.llm_config import LLM_LIGHT
from src.core.task_manager import set_cancelled, set_completed, set_waiting
from src.core.session_logger import add_event, session_event, build_llm_meta, EventType
from src.workflow.graph_infra.state import GraphState
from datetime import timezone, timedelta
from src.core.cm_client import cm_client
from src.core.bc_client import bc_cmi_client, bc_irc_client


# ───────────────────────────────────상수───────────────────────────────────────
AGENT_NAME = "esim_early_termination"

KST = timezone(timedelta(hours=9))


# ── 헬퍼 ─────────────────────────────────────────────────────────────────────

def _get_params(state: GraphState) -> dict:
    return state.collected_parameters.get(AGENT_NAME, {})


def _set_param(state: GraphState, key: str, value) -> None:
    if AGENT_NAME not in state.collected_parameters:
        state.collected_parameters[AGENT_NAME] = {}
    state.collected_parameters[AGENT_NAME][key] = value


def _parse_llm_json(content):
    """Gemini는 content를 list of parts로 반환하는 경우가 있음."""
    if isinstance(content, list):
        content = "".join(
            part.get("text", "") if isinstance(part, dict) else str(part)
            for part in content
        )
    cleaned = content.strip()
    if cleaned.startswith("```"):
        lines = cleaned.split("\n")
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        cleaned = "\n".join(lines)
    return json.loads(cleaned)


def _resolve_supplier(supplier_code: str) -> str:
    """공급사 코드를 사람이 읽을 수 있는 이름으로 변환."""
    if supplier_code == "4104002":
        return "빌리언커넥트"
    if supplier_code == "4104001":
        return "차이나모바일"
    return f"지원불가({supplier_code})"


# ─────────────────────────────────핵심 노드────────────────────────────────────


async def request_orderid(state: GraphState) -> GraphState:
    """주문번호 요청 노드."""
    state.add_ai_message("eSIM 중도해지 처리를 진행하기 위해 이심 주문번호를 알려주세요.")

    await add_event(state.session_id, EventType.HITL_WAIT, "주문번호 입력 요청", task_id=state.task_id)
    await set_waiting(state, result="주문번호 입력 요청")

    return state


async def fetch_order(state: GraphState) -> GraphState:
    """주문 상세 조회 노드. order_products 전체 추출."""
    params = _get_params(state)
    order_id = params.get("order_id", "")
    formatted_order_id = esim_client.encrypt(order_id)

    try:
        async with session_event(state.session_id, EventType.API_CALL, "이심 주문 조회 API",
                                  task_id=state.task_id) as ev:
            response = await esim_client.call_esim_api_get(
                f"https://wapi.wifidosirak.com/api/v1/order/{formatted_order_id}"
            )
            ev.content = f"order_id={order_id}"
    except Exception as e:
        print(f"[{AGENT_NAME}] 주문 조회 오류 | order_id={order_id}, error={e}")
        state.add_ai_message("현재 내부 전산 오류로 주문 조회가 되지 않습니다. 잠시 후 다시 시도해주세요.")
        await set_cancelled(state, result=f"주문 조회 오류: {e}")
        state.current_agent = None
        return state

    if response.get("resultCode") is not True:
        await add_event(state.session_id, EventType.ERROR, "주문 조회 실패",
                        content="resultCode=False", task_id=state.task_id)
        state.add_ai_message("현재 내부 전산 오류로 주문 조회가 되지 않습니다. 잠시 후 다시 시도해주세요.")
        await set_cancelled(state, result="주문 조회 resultCode=False")
        state.current_agent = None
        return state

    rv = response["resultValue"]
    print(f"[{AGENT_NAME}] 주문 조회 응답 resultValue keys: {list(rv.keys())}")

    order_products = []
    for item in rv.get("ordersDetail", []):
        order_products.append({
            "product_code": item["productCode"],
            "product_name": item["productName"],
            "days":         item["useDay"],
            "price":        item["price"],
            "iccid":        item.get("iccid", ""),
            "supplier":     _resolve_supplier(item.get("supplier", "")),
        })

    _set_param(state, "order_info", {
        "name":          rv.get("name", ""),
        "email":         rv.get("email", ""),
        "mobile":        rv.get("mobile", ""),
        "status":        rv.get("status", ""),
        "join_route_id": rv.get("joinRouteId", ""),
    })
    _set_param(state, "order_products", order_products)
    print(f"[{AGENT_NAME}] 주문 조회 성공 | order_id={order_id}, 상품 수={len(order_products)}")
    return state


async def select_iccid(state: GraphState) -> GraphState:
    """
    중도해지 대상 iccid 선택 노드.
    - 상품 1개: 자동 선택 (iccid 있을 때만)
    - 상품 2개 이상: 목록 표시 후 선택 요청

    4104001 차이나모바일
    4104002 빌리언커넥트
    """
    params         = _get_params(state)
    order_products = params.get("order_products", [])

    if not order_products:
        state.add_ai_message("주문에 포함된 상품 정보를 찾을 수 없습니다. 주문번호를 다시 확인해주세요.")
        await set_cancelled(state, result="order_products 비어 있음")
        state.current_agent = None
        return state

    if len(order_products) == 1:
        p     = order_products[0]
        iccid = p.get("iccid", "")

        if not iccid:
            state.add_ai_message("해당 상품의 ICCID 정보가 없습니다. 주문번호를 다시 확인해주세요.")
            await set_cancelled(state, result="ICCID 없음 (단일 상품)")
            state.current_agent = None
            return state

        _set_param(state, "selected_sims", [{
            "iccid":        iccid,
            "product_code": p["product_code"],
            "product_name": p["product_name"],
            "supplier":     p.get("supplier", ""),
        }])
        print(f"[{AGENT_NAME}] iccid 자동 선택 | iccid={iccid}")
        return state

    # 2개 이상: 목록 표시
    lines = ["아래 구매 목록 중 중도 해지가 필요한 항목을 선택해주세요. (복수 선택 가능)\n"]
    for i, p in enumerate(order_products, start=1):
        iccid_display = p['iccid'] if p['iccid'] else "ICCID 없음"
        lines.append(f"{i}. {p['product_name']} / ICCID: {iccid_display} / 공급사: {p.get('supplier', '')}")

    state.add_ai_message("\n".join(lines))
    _set_param(state, "iccid_list_shown", True)
    await add_event(state.session_id, EventType.HITL_WAIT, "iccid 선택 요청", task_id=state.task_id)
    await set_waiting(state, result="iccid 선택 요청")
    return state


async def validate_iccid(state: GraphState) -> GraphState:
    """
    사용자 응답을 AI로 파싱하여 선택된 iccid 확정.
    성공 시 selected_sims 설정, 실패 시 iccid_list_shown=False 세팅 → select_iccid 재실행.
    """
    params         = _get_params(state)
    order_products = params.get("order_products", [])

    user_message = next(
        (m["content"] for m in reversed(state.messages) if m["role"] == "user"),
        "",
    )

    # iccid가 있는 상품만 후보로
    valid_products = [p for p in order_products if p.get("iccid")]
    if not valid_products:
        state.add_ai_message("선택 가능한 ICCID가 없습니다. 관리자에게 문의해주세요.")
        await set_cancelled(state, result="유효한 ICCID 없음")
        state.current_agent = None
        return state

    product_lines = "\n".join(
        f"{i + 1}. {p['product_name']} / ICCID: {p['iccid']} / 공급사: {p.get('supplier', '')}"
        for i, p in enumerate(valid_products)
    )
    iccid_list = [p["iccid"] for p in valid_products]

    prompt = (
        "아래 상품 목록에서 사용자가 선택한 항목의 ICCID를 추출해주세요.\n\n"
        f"상품 목록:\n{product_lines}\n\n"
        f"유효한 ICCID 목록: {json.dumps(iccid_list, ensure_ascii=False)}\n\n"
        f"사용자 선택: {user_message}\n\n"
        '반환 형식: {"selected_iccids": ["iccid1", "iccid2", ...]}\n'
        "- '전체' 또는 '모두' 등의 경우 전체 ICCID 포함\n"
        "- 번호 또는 순서로 선택한 경우 해당 ICCID 반환\n"
        "JSON만 반환."
    )

    async with session_event(state.session_id, EventType.LLM_GENERIC, "iccid 선택 파싱",
                              task_id=state.task_id) as ev:
        llm_resp = await LLM_LIGHT.ainvoke([HumanMessage(content=prompt)])
        ev.content = str(llm_resp.content)[:200]
        ev.llm = build_llm_meta(LLM_LIGHT.model, llm_resp.usage_metadata)

    try:
        result   = _parse_llm_json(llm_resp.content)
        selected = result.get("selected_iccids", [])
    except Exception as e:
        print(f"[{AGENT_NAME}] validate_iccid 파싱 오류: {e}")
        selected = []

    if not selected:
        state.add_ai_message("선택된 항목을 파악하지 못했습니다. 번호 또는 ICCID로 다시 선택해주세요.")
        _set_param(state, "iccid_list_shown", False)  # select_iccid 재실행 트리거
        return state

    selected_products = [p for p in valid_products if p["iccid"] in selected]

    if not selected_products:
        state.add_ai_message("유효하지 않은 항목입니다. 목록에서 다시 선택해주세요.")
        _set_param(state, "iccid_list_shown", False)  # select_iccid 재실행 트리거
        return state

    # 검증 성공 → selected_sims 저장
    _set_param(state, "selected_sims", [
        {
            "iccid":        p["iccid"],
            "product_code": p["product_code"],
            "product_name": p["product_name"],
            "supplier":     p.get("supplier", ""),
        }
        for p in selected_products
    ])
    print(f"[{AGENT_NAME}] iccid 검증 완료 | selected={[p['iccid'] for p in selected_products]}")
    return state


async def request_termination(state: GraphState) -> GraphState:
    """
    중도해지 요청 노드.
    ※ 빌리언커넥트 중 상품코드에 "_BC_" 포함 시 BC채널 상품, 이외에는 데이터풀 상품이다.
    """
    params        = _get_params(state)
    selected_sims = params.get("selected_sims", [])

    if not selected_sims:
        state.add_ai_message("중도해지할 SIM 정보를 찾지 못했습니다. 다시 시도해주세요.")
        await set_cancelled(state, result="selected_sims 비어 있음")
        state.current_agent = None
        return state

    results: list[tuple[str, str, bool, str]] = []  # (iccid, supplier_label, success, message)

    for sim in selected_sims:
        iccid        = sim.get("iccid", "")
        supplier     = sim.get("supplier", "")
        product_code = sim.get("product_code", "")

        try:
            if supplier == "차이나모바일":
                async with session_event(state.session_id, EventType.API_CALL, "차이나모바일 중도해지 API",
                                          task_id=state.task_id) as ev:
                    response = await asyncio.to_thread(cm_client.terminate_by_iccid, iccid)
                    ev.content = f"iccid={iccid}, response={response}"
                if response.get("code") == "0":
                    print(f"[{AGENT_NAME}] 차이나모바일 중도해지 성공 | iccid={iccid}")
                    results.append((iccid, supplier, True, "중도해지 처리 완료"))
                else:
                    print(f"[{AGENT_NAME}] 차이나모바일 중도해지 실패 | iccid={iccid}, response={response}")
                    results.append((iccid, supplier, False, "중도해지 처리 실패"))

            elif supplier == "빌리언커넥트":
                if "_BC_" in product_code:  # BC 채널상품
                    supplier_label = "빌리언커넥트(BC채널)"
                    async with session_event(state.session_id, EventType.API_CALL, "빌리언커넥트(BC채널) 중도해지 API",
                                              task_id=state.task_id) as ev:
                        response = await asyncio.to_thread(bc_irc_client.terminate_by_iccid, iccid)
                        ev.content = f"iccid={iccid}, response={response}"
                    if response.get("tradeCode") == "1000":
                        print(f"[{AGENT_NAME}] 빌리언커넥트(BC채널) 중도해지 성공 | iccid={iccid}")
                        results.append((iccid, supplier_label, True, "중도해지 처리 완료"))
                    else:
                        print(f"[{AGENT_NAME}] 빌리언커넥트(BC채널) 중도해지 실패 | iccid={iccid}, response={response}")
                        results.append((iccid, supplier_label, False, "중도해지 처리 실패"))
                else:  # 데이터풀 상품
                    supplier_label = "빌리언커넥트(데이터풀)"
                    async with session_event(state.session_id, EventType.API_CALL, "빌리언커넥트(데이터풀) 중도해지 API",
                                              task_id=state.task_id) as ev:
                        response = await asyncio.to_thread(bc_cmi_client.terminate_by_iccid, iccid)
                        ev.content = f"iccid={iccid}, response={response}"
                    if response.get("tradeCode") == "1000":
                        print(f"[{AGENT_NAME}] 빌리언커넥트(데이터풀) 중도해지 성공 | iccid={iccid}")
                        results.append((iccid, supplier_label, True, "중도해지 처리 완료"))
                    else:
                        print(f"[{AGENT_NAME}] 빌리언커넥트(데이터풀) 중도해지 실패 | iccid={iccid}, response={response}")
                        results.append((iccid, supplier_label, False, "중도해지 처리 실패"))

            else:
                error_msg = f"중도해지를 지원하지 않는 공급사입니다. ({supplier})"
                print(f"[{AGENT_NAME}] 중도해지 불가 공급사 | iccid={iccid}, supplier={supplier}")
                results.append((iccid, supplier or "알수없음", False, error_msg))

        except Exception as e:
            print(f"[{AGENT_NAME}] 중도해지 오류 | iccid={iccid}, supplier={supplier}, error={e}")
            if supplier == "빌리언커넥트":
                supplier_label = "빌리언커넥트(BC채널)" if "_BC_" in product_code else "빌리언커넥트(데이터풀)"
            else:
                supplier_label = supplier or "알수없음"
            results.append((iccid, supplier_label, False, "처리 중 오류 발생"))

    total   = len(results)
    success = sum(1 for _, _, ok, _ in results if ok)

    lines = [f"eSIM 중도해지 처리 결과 ({success}/{total} 성공)\n"]
    for iccid, supplier_label, ok, msg in results:
        icon = "✅" if ok else "❌"
        lines.append(f"{icon} {supplier_label} / ICCID: {iccid}\n   {msg}")

    state.add_ai_message("\n".join(lines))

    # termination_completed 플래그 세팅 → 재진입 시 "end"로 분기
    _set_param(state, "termination_completed", True)

    if success == total:
        await add_event(state.session_id, EventType.COMPLETE, f"eSIM 중도해지 완료 ({total}건)", task_id=state.task_id)
        await set_completed(state, result=f"eSIM 중도해지 완료 ({total}건)")
    else:
        await add_event(state.session_id, EventType.CANCEL, f"eSIM 중도해지 부분 실패 ({success}/{total})", task_id=state.task_id)
        await set_cancelled(state, result=f"eSIM 중도해지 부분 실패 ({success}/{total})")

    state.current_agent = None
    return state


async def cancel_inquiry(state: GraphState) -> GraphState:
    """업무 취소 노드."""
    state.add_ai_message("eSIM 중도해지 업무를 취소했습니다. 다른 문의가 있으시면 말씀해주세요.")
    await add_event(state.session_id, EventType.CANCEL, "업무 취소", task_id=state.task_id)
    await set_cancelled(state, result="담당자 취소 - 업무 취소")
    state.current_agent = None
    return state
