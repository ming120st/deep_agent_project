from __future__ import annotations

import json
import os
import re

import httpx
from langchain_core.messages import HumanMessage
from langgraph.types import interrupt

from src.core.device_job_manager import create_device_job
from src.core.internal_esim_client import esim_client
from src.core.llm_config import LLM_LIGHT
from src.core.task_manager import set_cancelled, set_completed, set_waiting
from src.workflow.device_job.schemas import DeviceJobCallback, DeviceJobRequest
from src.workflow.graph_infra.state import GraphState

################################################################################
#  이심 재발급 업무 노드 (esim_fault_replacement)
#
#  흐름:
#    request_parameters (interrupt)
#      → fetch_order (자동)
#        → select_iccid (자동 or interrupt)
#          → validate_iccid (자동, 실패 시 select_iccid 재실행)
#            → select_product (자동, 필요 시 국가 입력 후 재실행)
#              → fetch_product_details (자동)
#                → confirm_payload (interrupt)
#                  → process_confirmation (자동)
#                    → send_esim (자동)
#                      → register_consultation (interrupt)
################################################################################

AGENT_NAME = "esim_fault_replacement"
REGIST_URL = "https://wapi.wifidosirak.com/api/v1/order/agentregist"
MAX_RETRY  = 5

DEVICE_JOB_URL    = os.getenv("DEVICE_JOB_URL", "")
DEVICE_JOB_SECRET = os.getenv("DEVICE_JOB_SECRET", "")


# ── 헬퍼 ─────────────────────────────────────────────────────────────────────

def _get_params(state: GraphState) -> dict:
    return state.collected_parameters.get(AGENT_NAME, {})


def _set_param(state: GraphState, key: str, value) -> None:
    if AGENT_NAME not in state.collected_parameters:
        state.collected_parameters[AGENT_NAME] = {}
    state.collected_parameters[AGENT_NAME][key] = value


def _parse_llm_json(content):
    # Gemini는 content를 list of parts로 반환하는 경우가 있음
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


# ── 3.1 주요 파라미터 확보 ────────────────────────────────────────────────────

async def request_parameters(state: GraphState) -> GraphState:
    """주문번호, 사용일수 요청 노드. (수량은 3.2.2에서 자동 설정)"""
    params = _get_params(state)
    missing = []
    if not params.get("order_id"):
        missing.append("• **주문번호** (E로 시작하는 내부 주문번호, 예: E26031101886)")
    if not params.get("request_days"):
        missing.append("• **사용일수** (며칠짜리 이심으로 재발급할지)")

    state.add_ai_message(
        "이심 재발급을 진행합니다.\n"
        "아래 정보를 알려주세요.\n\n" + "\n".join(missing)
    )
    await set_waiting(state, result="파라미터 입력 요청")
    return state


# ── 3.2 주문 상세 조회 ────────────────────────────────────────────────────────

async def fetch_order(state: GraphState) -> GraphState:
    """주문 상세 조회 노드 (자동 진행). order_products 전체 추출."""
    params   = _get_params(state)
    order_id = params.get("order_id", "")
    formatted_order_id = esim_client.encrypt(order_id)

    try:
        response = await esim_client.call_esim_api_get(
            f"https://wapi.wifidosirak.com/api/v1/order/{formatted_order_id}"
        )
    except Exception as e:
        print(f"[{AGENT_NAME}] 주문 조회 오류 | order_id={order_id}, error={e}")
        state.add_ai_message("현재 내부 전산 오류로 주문 조회가 되지 않습니다. 잠시 후 다시 시도해주세요.")
        await set_cancelled(state, result=f"주문 조회 오류: {e}")
        state.current_agent = None
        return state

    if response.get("resultCode") is not True:
        state.add_ai_message("현재 내부 전산 오류로 주문 조회가 되지 않습니다. 잠시 후 다시 시도해주세요.")
        await set_cancelled(state, result="주문 조회 resultCode=False")
        state.current_agent = None
        return state

    rv = response["resultValue"]

    # 공통 주문자 정보
    order_info = {
        "name":          rv.get("name", ""),
        "email":         rv.get("email", ""),
        "mobile":        rv.get("mobile", ""),
        "status":        rv.get("status", ""),
        "join_route_id": rv.get("joinRouteId", ""),
    }

    # 전체 ordersDetail 순회
    order_products = []
    for item in rv.get("ordersDetail", []):
        order_products.append({
            "product_code": item["productCode"],
            "product_name": item["productName"],
            "days":         item["useDay"],
            "price":        item["price"],
            "iccid":        item.get("iccid", ""),
        })

    _set_param(state, "order_info", order_info)
    _set_param(state, "order_products", order_products)
    print(f"[{AGENT_NAME}] 주문 조회 성공 | order_id={order_id}, 상품 수={len(order_products)}")
    return state


# ── 3.2.1 재발급 대상 iccid 선택 ──────────────────────────────────────────────

async def select_iccid(state: GraphState) -> GraphState:
    """
    재발급 대상 iccid 선택 노드.
    - 상품 1개: 자동 선택 (사용자에게 묻지 않음)
    - 상품 2개 이상: 목록 표시 후 선택 요청
    """
    params         = _get_params(state)
    order_products = params.get("order_products", [])

    if len(order_products) == 1:
        # 자동 선택
        p     = order_products[0]
        iccid = p.get("iccid", "")
        _set_param(state, "selected_iccids",  [iccid] if iccid else [])
        _set_param(state, "product_code",     p["product_code"])
        _set_param(state, "product_name",     p["product_name"])
        _set_param(state, "days",             p["days"])
        _set_param(state, "price",            p["price"])
        _set_param(state, "request_qnt",      1)
        print(f"[{AGENT_NAME}] iccid 자동 선택 | iccid={iccid}")
        return state  # conditional edge → select_product

    # 2개 이상: 목록 표시
    lines = ["아래 구매 목록 중 재발급이 필요한 항목을 선택해주세요. (복수 선택 가능)\n"]
    for i, p in enumerate(order_products, start=1):
        lines.append(f"{i}. {p['product_name']} / ICCID: {p['iccid']} / {p['days']}일")

    state.add_ai_message("\n".join(lines))
    _set_param(state, "iccid_list_shown", True)
    await set_waiting(state, result="iccid 선택 요청")
    return state


# ── 3.2.2 iccid 유효성 검증 및 공통 상품정보 추출 ────────────────────────────

async def validate_iccid(state: GraphState) -> GraphState:
    """
    사용자 응답을 AI로 파싱하여 선택된 iccid 확정.
    선택된 항목들의 product_code 동일성 검증 후 공통 상품정보 추출.
    실패 시 iccid_list_shown=False 세팅 → select_iccid 재실행.
    """
    params         = _get_params(state)
    order_products = params.get("order_products", [])

    user_message = next(
        (m["content"] for m in reversed(state.messages) if m["role"] == "user"),
        "",
    )

    product_lines = "\n".join(
        f"{i + 1}. {p['product_name']} / ICCID: {p['iccid']} / {p['days']}일"
        for i, p in enumerate(order_products)
    )
    iccid_list = [p["iccid"] for p in order_products]

    prompt = (
        "아래 상품 목록에서 사용자가 선택한 항목의 ICCID를 추출해주세요.\n\n"
        f"상품 목록:\n{product_lines}\n\n"
        f"유효한 ICCID 목록: {json.dumps(iccid_list, ensure_ascii=False)}\n\n"
        f"사용자 선택: {user_message}\n\n"
        '반환 형식: {"selected_iccids": ["iccid1", "iccid2", ...]}\n'
        "- '전체' 또는 '모두' 등의 경우 전체 ICCID 포함\n"
        "- 번호로 선택한 경우 해당 번호의 ICCID 반환\n"
        "JSON만 반환."
    )

    llm_resp = await LLM_LIGHT.ainvoke([HumanMessage(content=prompt)])
    try:
        result   = _parse_llm_json(llm_resp.content)
        selected = result.get("selected_iccids", [])
    except Exception as e:
        print(f"[{AGENT_NAME}] validate_iccid 파싱 오류: {e}")
        selected = []

    if not selected:
        state.add_ai_message("선택된 항목을 파악하지 못했습니다. 번호 또는 ICCID로 다시 선택해주세요.")
        _set_param(state, "iccid_list_shown", False)
        return state

    # order_products에서 선택된 항목 필터링
    selected_products = [p for p in order_products if p["iccid"] in selected]

    if not selected_products:
        state.add_ai_message("유효하지 않은 항목입니다. 목록에서 다시 선택해주세요.")
        _set_param(state, "iccid_list_shown", False)
        return state

    # product_code 동일성 검증
    codes = {p["product_code"] for p in selected_products}
    if len(codes) > 1:
        state.add_ai_message(
            "선택하신 iccid들의 상품 종류가 서로 다릅니다.\n"
            "동일한 상품의 iccid만 함께 선택해주세요."
        )
        _set_param(state, "iccid_list_shown", False)
        return state

    # 검증 성공 → 공통 상품정보 추출
    first = selected_products[0]
    _set_param(state, "selected_iccids", [p["iccid"] for p in selected_products])
    _set_param(state, "product_code",    first["product_code"])
    _set_param(state, "product_name",    first["product_name"])
    _set_param(state, "days",            first["days"])
    _set_param(state, "price",           first["price"])
    _set_param(state, "request_qnt",     len(selected_products))
    print(f"[{AGENT_NAME}] iccid 검증 완료 | selected={[p['iccid'] for p in selected_products]}")
    return state


# ── 3.3 상품 선정 ─────────────────────────────────────────────────────────────

async def select_product(state: GraphState) -> GraphState:
    """
    상품 선정 노드 (3.3 전체).
    - 동일 일수: 기존 상품코드 사용
    - 다른 일수: alt_product_code 시도
    - alt 실패: 국가명 입력 요청 → ISO 변환 → 국가 상품 조회 → AI 선정
    """
    params        = _get_params(state)
    product_code: str   = params.get("product_code", "")
    product_name: str   = params.get("product_name", "")
    days: int           = params.get("days")
    price: float        = params.get("price", 0)
    request_days: int   = params.get("request_days")

    # 3.3.1: 동일 일수 → 기존 상품코드 즉시 사용
    if request_days == days:
        _set_param(state, "raw_product_list", [product_code])
        print(f"[{AGENT_NAME}] 상품 선정: 동일 일수 → {product_code}")
        return state

    # 3.3.1~3.3.3: alt_product_code 시도
    alt_product_code = re.sub(r"\d+D$", f"{request_days}D", product_code)
    try:
        alt_response = await esim_client.call_esim_api_get(
            f"https://wapi.wifidosirak.com/api/v1/product/{alt_product_code}"
        )
        if alt_response.get("resultCode") is True:
            _set_param(state, "raw_product_list", [alt_product_code])
            print(f"[{AGENT_NAME}] 상품 선정: alt_product_code → {alt_product_code}")
            return state
    except Exception:
        pass

    # alt 실패 → 국가 기반 검색 필요
    country_name: str = params.get("country_name", "")

    if not country_name:
        _set_param(state, "needs_country", True)
        state.add_ai_message(
            "대체 상품 검색을 위해 이심을 사용할 **국가명**을 알려주세요.\n"
            "(예시: 일본, 대만, 미국)"
        )
        await set_waiting(state, result="국가명 입력 요청")
        return state

    # 3.3.5: ISO 3166-1 Alpha-3 변환
    conversion_prompt = (
        f"국가명을 ISO 3166-1 Alpha-3 기준 3자리 국가코드로 변환하세요.\n"
        f"가능하면: {{\"code\": \"JPN\"}}\n불가능하면: {{\"code\": null}}\n"
        f"국가명: {country_name}\nJSON만 반환."
    )
    llm_resp = await LLM_LIGHT.ainvoke([HumanMessage(content=conversion_prompt)])
    try:
        country_data = _parse_llm_json(llm_resp.content)
        country      = country_data.get("code")
    except Exception:
        country = None

    if not country:
        state.add_ai_message(
            f"죄송하지만 '{country_name}'의 국가코드를 변환할 수 없습니다. "
            "다시 정확한 국가명을 알려주세요. (예시: 일본, 대만, 미국)"
        )
        state.collected_parameters[AGENT_NAME].pop("country_name", None)
        await set_waiting(state, result="국가코드 변환 실패, 재입력 요청")
        return state

    # 3.3.5: 국가 상품 목록 조회
    try:
        country_response = await esim_client.call_esim_api_post(
            api_url="https://wapi.wifidosirak.com/api/v1/product/detail/country",
            json={"countryId": country},
        )
    except Exception as e:
        state.add_ai_message("현재 내부 전산 오류로 나중에 다시 시도해주세요.")
        await set_cancelled(state, result=f"국가 상품 조회 오류: {e}")
        state.current_agent = None
        return state

    if country_response.get("resultCode") is not True:
        state.add_ai_message("현재 내부 전산 오류로 나중에 다시 시도해주세요.")
        await set_cancelled(state, result="국가 상품 조회 resultCode=False")
        state.current_agent = None
        return state

    # 3.3.7: 필터링
    products    = country_response["resultValue"].get("products", [])
    daily_price = price / days if days else 0

    alt_product_list = []
    for p in products:
        if p.get("useDay") != request_days:
            continue
        if "개통희망일" in (p.get("description") or ""):
            continue
        if "실명인증" in (p.get("name") or ""):
            continue
        p_use_day = p.get("useDay") or 1
        if p.get("price", 0) / p_use_day <= daily_price:
            continue
        alt_product_list.append(p)

    if not alt_product_list:
        state.add_ai_message("죄송하지만 조건에 맞는 대체상품이 없습니다.")
        await set_cancelled(state, result="조건에 맞는 대체상품 없음")
        state.current_agent = None
        return state

    # 3.3.8: AI 선정 (최대 5개)
    product_json = json.dumps(
        [
            {
                "code":   p.get("code", p.get("productCode", "")),
                "name":   p.get("name", ""),
                "price":  p.get("price", 0),
                "useDay": p.get("useDay", 0),
            }
            for p in alt_product_list
        ],
        ensure_ascii=False,
    )
    ai_select_prompt = (
        f"아래 상품 리스트에서 재발급 대체 상품을 선정해주세요.\n\n"
        f"기존 구매 상품명: {product_name}\n\n"
        "선정 기준:\n"
        "1. 기존 구매 상품보다 데이터 제공량이 적지 않은 상품만 선정 (무제한은 무제한만 매칭)\n"
        "2. 가격이 낮은 것 우선\n"
        "3. 위 조건이 동일하면 데이터 용량이 큰 것 우선\n"
        "4. 최대 5개까지만 선정\n\n"
        f"상품 리스트:\n{product_json}\n\n"
        '반환 형식 (JSON 배열, 상품코드만): ["코드1", "코드2", ...]\n'
        "JSON만 반환."
    )
    llm_resp = await LLM_LIGHT.ainvoke([HumanMessage(content=ai_select_prompt)])
    try:
        selected_codes = _parse_llm_json(llm_resp.content)
        if not isinstance(selected_codes, list):
            raise ValueError("list 아님")
    except Exception:
        selected_codes = [
            p.get("code", p.get("productCode", "")) for p in alt_product_list[:5]
        ]

    _set_param(state, "raw_product_list", selected_codes)
    print(f"[{AGENT_NAME}] 상품 선정: 국가 기반 → {selected_codes}")
    return state


# ── 3.4 상품 상세 조회 ────────────────────────────────────────────────────────

async def fetch_product_details(state: GraphState) -> GraphState:
    """상품 상세 조회 노드. raw_product_list 순회하며 product_list 완성."""
    params           = _get_params(state)
    raw_product_list = params.get("raw_product_list", [])
    product_list     = []

    for code in raw_product_list:
        try:
            response = await esim_client.call_esim_api_get(
                f"https://wapi.wifidosirak.com/api/v1/product/{code}"
            )
        except Exception as e:
            print(f"[{AGENT_NAME}] 상품 상세 조회 오류 | code={code}, error={e}")
            state.add_ai_message("현재 내부 전산 오류로 나중에 다시 시도해주세요.")
            await set_cancelled(state, result=f"상품 상세 조회 오류: {e}")
            state.current_agent = None
            return state

        if response.get("resultCode") is not True:
            state.add_ai_message("현재 내부 전산 오류로 나중에 다시 시도해주세요.")
            await set_cancelled(state, result="상품 상세 조회 resultCode=False")
            state.current_agent = None
            return state

        rv = response["resultValue"]
        product_list.append(
            {
                "product_code":  rv.get("code", code),
                "product_name":  rv.get("name", ""),
                "product_price": rv.get("price", 0),
            }
        )

    _set_param(state, "product_list", product_list)
    print(f"[{AGENT_NAME}] 상품 상세 조회 완료 | {len(product_list)}개")
    return state


# ── 3.5 사용자 컨펌 ───────────────────────────────────────────────────────────

async def confirm_payload(state: GraphState) -> GraphState:
    """
    payload 구성 및 컨펌 요청 노드 (3.5.1 + 3.5.2).
    최초 실행: product_list[0]으로 payload 구성.
    이후 실행: 기존 payload 그대로 표시.
    """
    params       = _get_params(state)
    order_info   = params.get("order_info", {})
    product_list = params.get("product_list", [])

    # 3.5.1: 최초 payload 구성
    if "current_payload" not in params:
        first    = product_list[0]
        order_id = params.get("order_id", "")
        payload  = {
            "OutOrderId":   f"{order_id}재발송",
            "Name":         order_info.get("name", ""),
            "ProductName":  first["product_name"],
            "ProductCode":  first["product_code"],
            "JoinRoute":    str(order_info.get("join_route_id", "")),
            "Qty":          params.get("request_qnt", 1),
            "Email":        order_info.get("email", ""),
            "Mobile":       order_info.get("mobile", ""),
            "PaymentPrice": first["product_price"],
            "UserID":       state.chat_context.get("user_id", ""),
        }
        _set_param(state, "current_payload",       payload)
        _set_param(state, "current_product_index", 0)

    p = _get_params(state).get("current_payload", {})

    lines = [
        "아래 내용으로 이심 재발급을 진행할까요?\n",
        f"• 주문번호: {p.get('OutOrderId', '')}",
        f"• 수신자: {p.get('Name', '')}",
        f"• 상품명: {p.get('ProductName', '')}",
        f"• 상품코드: {p.get('ProductCode', '')}",
        f"• 수량: {p.get('Qty', '')}",
        f"• 이메일: {p.get('Email', '')}",
        f"• 전화번호: {p.get('Mobile', '')}",
        f"• 건당 결제금액: {p.get('PaymentPrice', '')}원",
        "\n**확인** 또는 수정·상품변경 요청을 말씀해주세요.",
    ]
    state.add_ai_message("\n".join(lines))
    _set_param(state, "payload_shown",     True)
    _set_param(state, "payload_confirmed", False)
    await set_waiting(state, result="payload 컨펌 요청")
    return state


async def process_confirmation(state: GraphState) -> GraphState:
    """
    AI 노드 - 사용자 응답 의도 파악 및 payload 수정 (3.5.3).
    """
    params                = _get_params(state)
    current_payload: dict = params.get("current_payload", {})
    product_list: list    = params.get("product_list", [])
    current_product_index = params.get("current_product_index", 0)

    user_message = next(
        (m["content"] for m in reversed(state.messages) if m["role"] == "user"),
        "",
    )

    product_summary = json.dumps(
        [
            {
                "idx":           i,
                "product_code":  p["product_code"],
                "product_name":  p["product_name"],
                "product_price": p["product_price"],
            }
            for i, p in enumerate(product_list)
        ],
        ensure_ascii=False,
        indent=2,
    )

    prompt = (
        "사용자 응답을 분석하여 아래 JSON 형식으로만 응답하세요.\n\n"
        f"현재 payload:\n{json.dumps(current_payload, ensure_ascii=False, indent=2)}\n\n"
        f"상품 목록 (인덱스):\n{product_summary}\n\n"
        f"현재 선택 인덱스: {current_product_index}\n"
        f"사용자 메시지: {user_message}\n\n"
        "응답 형식:\n"
        "{\n"
        '  "action": "confirm" | "modify" | "change_product",\n'
        '  "updated_payload": { },\n'
        f'  "next_product_index": {current_product_index},\n'
        '  "message_to_user": "..."\n'
        "}\n\n"
        "규칙:\n"
        "- confirm: 사용자가 내용에 동의함\n"
        "- modify: 특정 필드 수정 요청 (수정 가능 필드: Email, Mobile, Qty)\n"
        "- change_product: 상품 교체 요청 (next_product_index로 product_list에서 선택)\n"
        "- 수정 불가 필드(OutOrderId, UserID, JoinRoute, ProductCode, ProductName, PaymentPrice) "
        "변경 요청 시 action=modify, updated_payload 변경 없이, message_to_user에 '해당 항목은 수정이 불가합니다' 안내\n"
        "- updated_payload는 전체 payload를 반환 (수정된 버전 또는 기존 그대로)\n"
        "JSON만 반환."
    )

    llm_resp = await LLM_LIGHT.ainvoke([HumanMessage(content=prompt)])
    try:
        result = _parse_llm_json(llm_resp.content)
    except Exception as e:
        print(f"[{AGENT_NAME}] process_confirmation 파싱 오류: {e}")
        result = {
            "action":             "confirm",
            "updated_payload":    current_payload,
            "next_product_index": current_product_index,
            "message_to_user":    "",
        }

    action          = result.get("action", "confirm")
    updated_payload = result.get("updated_payload", current_payload)
    next_index      = result.get("next_product_index", current_product_index)
    msg             = result.get("message_to_user", "")

    if action == "confirm":
        _set_param(state, "payload_confirmed", True)
        _set_param(state, "current_payload",   updated_payload)
        if msg:
            state.add_ai_message(msg)

    elif action == "modify":
        _set_param(state, "current_payload", updated_payload)
        _set_param(state, "payload_shown",   False)
        if msg:
            state.add_ai_message(msg)

    elif action == "change_product":
        if next_index >= len(product_list):
            state.add_ai_message("더 이상 추천 상품이 없습니다.")
        else:
            selected = product_list[next_index]
            updated_payload = dict(updated_payload)
            updated_payload["ProductCode"]  = selected["product_code"]
            updated_payload["ProductName"]  = selected["product_name"]
            updated_payload["PaymentPrice"] = selected["product_price"]
            _set_param(state, "current_payload",       updated_payload)
            _set_param(state, "current_product_index", next_index)
            if msg:
                state.add_ai_message(msg)
        _set_param(state, "payload_shown", False)

    return state


# ── 3.6 이심 발송 ─────────────────────────────────────────────────────────────

async def send_esim(state: GraphState) -> GraphState:
    """
    이심 발송 API 호출 노드.
    request_qnt만큼 반복 호출 (건당 1개). 건당 최대 MAX_RETRY회 재시도.
    어느 건이든 MAX_RETRY 소진 시 전체 중단.
    """
    params      = _get_params(state)
    payload     = params.get("current_payload", {})
    request_qnt = params.get("request_qnt", 1)
    order_id    = params.get("order_id", "")

    for i in range(request_qnt):
        payload_i = {**payload, "OutOrderId": f"{order_id}재발송_{i + 1}", "Qty": 1}
        success   = False

        for attempt in range(MAX_RETRY):
            try:
                response = await esim_client.call_esim_api_post(
                    api_url=REGIST_URL,
                    json=payload_i,
                )
                if response.get("resultCode") is True:
                    success = True
                    break
            except Exception as e:
                print(f"[{AGENT_NAME}] 발송 오류 | index={i + 1}, attempt={attempt + 1}, error={e}")

        if not success:
            print(f"[{AGENT_NAME}] 발송 최종 실패 | index={i + 1}, MAX_RETRY={MAX_RETRY}")
            state.add_ai_message(
                f"이심 발송 시스템에 문제가 발생했습니다.\n"
                f"({i + 1}번째 발송에서 {MAX_RETRY}회 시도 후 실패)\n"
                "직접 재발급 처리를 진행해주세요."
            )
            await set_cancelled(state, result=f"이심 발송 실패: {i + 1}번째 {MAX_RETRY}회 소진")
            state.current_agent = None
            return state

    # 전체 성공
    state.add_ai_message(f"총 {request_qnt}건의 이심 재발급 발송이 완료되었습니다.")
    _set_param(state, "send_success", True)
    print(f"[{AGENT_NAME}] 이심 발송 전체 성공 | {request_qnt}건")
    return state


# ── 3.7 상담 등록 ─────────────────────────────────────────────────────────────

async def register_consultation(state: GraphState) -> GraphState:
    """
    상담 등록 노드.
    device_agent에 register_consultation 작업 요청 후 interrupt로 대기.
    콜백 수신 시 재개되어 결과 처리.
    """
    params         = _get_params(state)
    thread_id      = state.chat_context.get("user_id", "")
    order_info     = params.get("order_info", {})
    payload        = params.get("current_payload", {})
    selected_iccids = params.get("selected_iccids", [])

    # ── LangGraph 재실행 방지 ────────────────────────────────────────────────
    job_id = params.get("_job_id")

    if not job_id:
        req = DeviceJobRequest(
            thread_id=thread_id,
            task="register_consultation",
            parameters={
                "order_id":     params.get("order_id", ""),
                "name":         order_info.get("name", ""),
                "product_name": payload.get("ProductName", ""),
                "iccid_list":   selected_iccids,
                "memo":         f"이심 재발급 완료. 발송 상품: {payload.get('ProductName', '')}",
            },
            context={"user_name": state.chat_context.get("user_name")},
        )
        job_id = req.job_id
        _set_param(state, "_job_id", job_id)  # ★ interrupt() 전에 반드시 저장

        await create_device_job(job_id, thread_id)

        try:
            async with httpx.AsyncClient() as client:
                resp = await client.post(
                    f"{DEVICE_JOB_URL}/device-job",
                    json=req.model_dump(),
                    headers={"X-Device-Job-Key": DEVICE_JOB_SECRET},
                    timeout=10.0,
                )
                resp.raise_for_status()
        except Exception as e:
            state.add_ai_message(
                f"⚠️ PC 에이전트 연결에 실패했습니다. 잠시 후 다시 시도해주세요.\n({e})"
            )
            await set_cancelled(state, result=f"DeviceJob 전송 실패: {e}")
            state.current_agent = None
            return state

        state.add_ai_message("상담 등록 작업을 PC에이전트에 전달했습니다. 잠시만 기다려주세요.")
        await set_waiting(state, result=f"DeviceJob 대기 중: {job_id}")

    # interrupt — /device-callback 수신 시 자동 재개
    callback_payload: dict = interrupt({"job_id": job_id, "status": "waiting"})
    callback = DeviceJobCallback(**callback_payload)

    if callback.status == "success":
        state.add_ai_message("이심 재발급 및 상담 등록이 완료되었습니다.")
        await set_completed(state, result="이심 재발급 및 상담 등록 완료")
    else:
        error_msg = callback.error_message or "알 수 없는 오류"
        state.add_ai_message(
            "이심 발송은 완료되었으나 상담 등록 중 오류가 발생했습니다.\n"
            f"수동으로 상담 등록을 진행해주세요. (사유: {error_msg})"
        )
        await set_completed(state, result=f"이심 발송 완료, 상담 등록 실패: {error_msg}")

    _set_param(state, "job_done", True)
    state.current_agent = None
    return state


# ── 취소 ─────────────────────────────────────────────────────────────────────

async def cancel_inquiry(state: GraphState) -> GraphState:
    """업무 취소 노드."""
    state.add_ai_message("이심 재발급 업무를 취소했습니다. 다른 문의가 있으시면 말씀해주세요.")
    await set_cancelled(state, result="담당자 취소 - 업무 취소")
    state.current_agent = None
    return state
