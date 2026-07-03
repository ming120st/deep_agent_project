from __future__ import annotations

import json
import os
import re

import httpx
from langchain_core.messages import HumanMessage
from langgraph.types import interrupt

from src.core.device_job_manager import create_device_job, get_job_id_by_task_id
from src.core.internal_esim_client import esim_client
from src.core.llm_config import LLM_LIGHT
from src.core.task_manager import set_cancelled, set_completed, set_waiting
from src.core.session_logger import add_event, session_event, build_llm_meta, EventType
from src.workflow.device_job.schemas import DeviceJobCallback, DeviceJobRequest
from src.workflow.graph_infra.state import GraphState
from datetime import datetime, timezone, timedelta
from src.core.bq_logger import insert_row
from google.oauth2 import service_account
from google.cloud import bigquery as bq

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

BQ_TABLE = "esim_fault_replacement_log"
AGENT_NAME = "esim_fault_replacement"
REGIST_URL = "https://wapi.wifidosirak.com/api/v1/order/agentregist"
MAX_RETRY  = 5

DEVICE_JOB_URL    = os.getenv("DEVICE_JOB_URL", "")
DEVICE_JOB_SECRET = os.getenv("DEVICE_JOB_SECRET", "")
JOB_CALLBACK_TIMEOUT = 60  # DeviceJob 콜백 최대 대기 시간 (초, 기본 1분)

# 상품리스트 조회를 위한 BigQuery 설정
_BQ_SALES_PROJECT = os.getenv("BQ_SALES_PROJECT_ID", "daily-report-widemobile")
_BQ_SALES_DATASET = os.getenv("BQ_SALES_DATASET", "data_set")
_BQ_KEY_RAW       = os.getenv("GOOGLE_SERVICE_ACCOUNT_KEY_PATH_GBQ", "")

KST = timezone(timedelta(hours=9))

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

def _get_bq_sales_client() -> bq.Client:
    """판매 데이터용 BigQuery 클라이언트 생성."""
    if _BQ_KEY_RAW:
        import json as _json
        key_info = _json.loads(_BQ_KEY_RAW)
        creds = service_account.Credentials.from_service_account_info(
            key_info,
            scopes=["https://www.googleapis.com/auth/bigquery"],
        )
        return bq.Client(project=_BQ_SALES_PROJECT, credentials=creds)
    return bq.Client(project=_BQ_SALES_PROJECT)  # ADC fallback


async def _resolve_internal_product_code(product_code_public: str) -> str | None:
    """
    외부 상품코드(숫자) → 내부 상품코드(Product_Code) 변환.
    esim_product_list 테이블에서 Product_Code_public 기준으로 조회.
    못 찾으면 None 반환.
    """
    if not product_code_public:
        return None

    query = f"""
        SELECT Product_Code
        FROM `{_BQ_SALES_PROJECT}.{_BQ_SALES_DATASET}.esim_product_list`
        WHERE Product_Code_public = @pub_code
        LIMIT 1
    """

    job_config = bq.QueryJobConfig(
        query_parameters=[
            bq.ScalarQueryParameter("pub_code", "STRING", str(product_code_public))
        ]
    )
    try:
        import asyncio
        loop = asyncio.get_event_loop()
        client = _get_bq_sales_client()

        def _run_query():
            rows = list(client.query(query, job_config=job_config).result())
            return rows[0]["Product_Code"] if rows else None

        result = await loop.run_in_executor(None, _run_query)
        print(f"[{AGENT_NAME}] BQ 코드 변환 | pub={product_code_public!r} → internal={result!r}")
        return result
    except Exception as e:
        print(f"[{AGENT_NAME}] BQ 코드 변환 오류: {e}")
        return None

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
    await add_event(state.session_id, EventType.HITL_WAIT, "파라미터 입력 요청", task_id=state.task_id)
    await set_waiting(state, result="파라미터 입력 요청")
    return state


# ── 3.2 주문 상세 조회 ────────────────────────────────────────────────────────

async def fetch_order(state: GraphState) -> GraphState:
    """주문 상세 조회 노드 (자동 진행). order_products 전체 추출."""
    params   = _get_params(state)
    order_id = params.get("order_id", "")
    formatted_order_id = esim_client.encrypt(order_id)

    try:
        async with session_event(state.session_id, EventType.API_CALL, "주문 조회 API",
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
    print(f"[{AGENT_NAME}] 주문 조회 응답 resultValue: {json.dumps(rv, ensure_ascii=False)[:500]}...")
    print(f"[{AGENT_NAME}] fetch_order resultValue keys: {list(rv.keys())}")
    print(f"[{AGENT_NAME}] fetch_order name-related fields: name={rv.get('name')!r}, userName={rv.get('userName')!r}, buyerName={rv.get('buyerName')!r}, receiverName={rv.get('receiverName')!r}")

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
    await add_event(state.session_id, EventType.HITL_WAIT, "iccid 선택 요청", task_id=state.task_id)
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
    params        = _get_params(state)
    product_code_public: str = params.get("product_code", "")  # 외부(숫자) 코드
    product_name: str        = params.get("product_name", "")
    days: int                = params.get("days")
    price: float             = params.get("price", 0)
    request_days: int        = params.get("request_days")

    print(f"[{AGENT_NAME}][3.3] ══════════ select_product 진입 ══════════")
    print(f"[{AGENT_NAME}][3.3] product_code_public(외부)={product_code_public!r}")

    # ── 외부 코드 → 내부 코드 변환 ──────────────────────────────────────────
    # 이미 변환된 내부 코드가 저장돼 있으면 재사용 (루프 재진입 시 중복 조회 방지)
    product_code: str = params.get("_internal_product_code", "")
    if not product_code:
        resolved = await _resolve_internal_product_code(product_code_public)
        if resolved:
            product_code = resolved
            _set_param(state, "_internal_product_code", product_code)
        else:
            # BQ 조회 실패 시 외부 코드를 그대로 fallback으로 사용
            print(f"[{AGENT_NAME}][3.3] 내부 코드 변환 실패 → 외부 코드 fallback: {product_code_public!r}")
            product_code = product_code_public

    print(f"[{AGENT_NAME}][3.3] product_code(내부)={product_code!r}")
    print(f"[{AGENT_NAME}][3.3] product_name={product_name!r}")
    print(f"[{AGENT_NAME}][3.3] 구매일수(days)={days!r}  요청일수(request_days)={request_days!r}")
    print(f"[{AGENT_NAME}][3.3] price={price!r}")

    # 3.3.1: 동일 일수 → 기존 상품코드 즉시 사용
    if request_days == days:
        print(f"[{AGENT_NAME}][3.3.1] 동일 일수 → 기존 코드 사용: {product_code}")
        _set_param(state, "raw_product_list",    [product_code])
        _set_param(state, "product_request_days", request_days)  # ★ 선정 시점 request_days 저장
        return state

    print(f"[{AGENT_NAME}][3.3] 요청일수({request_days}) ≠ 구매일수({days}) → alt 코드 시도")

    # 3.3.2~3.3.3: alt_product_code 시도
    # 우선 동일상품의 request_days 일수 버전이 있는지 시도해보고, 없으면 국가 기반 검색으로 fallback
    alt_product_code = re.sub(r"\d+D$", f"{request_days}D", product_code)
    print(f"[{AGENT_NAME}][3.3.2] re.sub 결과: {product_code!r} → {alt_product_code!r}")

    if alt_product_code != product_code:
        print(f"[{AGENT_NAME}][3.3.2] alt 코드가 다름(일수만 바꿔서 상품코드가 만들어짐) → API 조회 시도: {alt_product_code!r}")
        try:
            async with session_event(state.session_id, EventType.API_CALL, "대체 상품 조회 API",
                                      task_id=state.task_id) as ev:
                alt_response = await esim_client.call_esim_api_get(
                    f"https://wapi.wifidosirak.com/api/v1/product/{alt_product_code}"
                )
                ev.content = f"code={alt_product_code}, resultCode={alt_response.get('resultCode')}"
            print(f"[{AGENT_NAME}][3.3.2] alt API 응답 resultCode={alt_response.get('resultCode')!r}")
            if alt_response.get("resultCode") is True:
                print(f"[{AGENT_NAME}][3.3.2] alt 코드 조회 성공 → {alt_product_code}")
                _set_param(state, "raw_product_list",    [alt_product_code])
                _set_param(state, "product_request_days", request_days)
                return state
            else:
                print(f"[{AGENT_NAME}][3.3.2] alt 코드 조회 실패 → 국가 기반 검색으로 fallback")
        except Exception as e:
            print(f"[{AGENT_NAME}][3.3.2] alt API 예외 발생: {e} → 국가 기반 검색으로 fallback")
    else:
        print(f"[{AGENT_NAME}][3.3.2] alt 코드가 원본과 동일 (D패턴 없는 코드) → 바로 국가 기반 검색")

    # alt 실패 → 국가 기반 검색 필요
    country_name: str = params.get("country_name", "")
    print(f"[{AGENT_NAME}][3.3.3] country_name={country_name!r}")

    if not country_name:
        print(f"[{AGENT_NAME}][3.3.3] country_name 없음 → 국가명 입력 요청")
        _set_param(state, "needs_country", True)
        state.add_ai_message(
            "대체 상품 검색을 위해 이심을 사용할 **국가명**을 알려주세요.\n"
            "(예시: 일본, 대만, 미국)"
        )
        await add_event(state.session_id, EventType.HITL_WAIT, "국가명 입력 요청", task_id=state.task_id)
        await set_waiting(state, result="국가명 입력 요청")
        return state

    # 3.3.5: ISO 3166-1 Alpha-3 변환
    print(f"[{AGENT_NAME}][3.3.5] ISO 변환 시작: {country_name!r}")
    conversion_prompt = (
        f"국가명을 ISO 3166-1 Alpha-3 기준 3자리 국가코드로 변환하세요.\n"
        f"가능하면: {{\"code\": \"JPN\"}}\n불가능하면: {{\"code\": null}}\n"
        f"국가명: {country_name}\nJSON만 반환."
    )
    async with session_event(state.session_id, EventType.LLM_GENERIC, "국가명 ISO 변환",
                              task_id=state.task_id) as ev:
        llm_resp = await LLM_LIGHT.ainvoke([HumanMessage(content=conversion_prompt)])
        ev.content = str(llm_resp.content)[:200]
        ev.llm = build_llm_meta(LLM_LIGHT.model, llm_resp.usage_metadata)
    print(f"[{AGENT_NAME}][3.3.5] ISO 변환 LLM 원본 응답: {llm_resp.content!r}")
    try:
        country_data = _parse_llm_json(llm_resp.content)
        country      = country_data.get("code")
        print(f"[{AGENT_NAME}][3.3.5] ISO 변환 결과: {country!r}")
    except Exception as e:
        print(f"[{AGENT_NAME}][3.3.5] ISO 변환 파싱 오류: {e}")
        country = None

    if not country:
        print(f"[{AGENT_NAME}][3.3.5] ISO 코드 없음 → 국가명 재입력 요청")
        state.add_ai_message(
            f"죄송하지만 '{country_name}'의 국가코드를 변환할 수 없습니다. "
            "다시 정확한 국가명을 알려주세요. (예시: 일본, 대만, 미국)"
        )
        state.collected_parameters[AGENT_NAME].pop("country_name", None)
        await add_event(state.session_id, EventType.HITL_WAIT, "국가코드 변환 실패, 재입력 요청", task_id=state.task_id)
        await set_waiting(state, result="국가코드 변환 실패, 재입력 요청")
        return state

    # 3.3.5: 국가 상품 목록 조회
    print(f"[{AGENT_NAME}][3.3.5] 국가 상품 조회: countryId={country!r}")
    try:
        async with session_event(state.session_id, EventType.API_CALL, "국가 상품 조회 API",
                                  task_id=state.task_id) as ev:
            country_response = await esim_client.call_esim_api_post(
                api_url="https://wapi.wifidosirak.com/api/v1/product/detail/country",
                json={"countryId": country},
            )
            ev.content = f"countryId={country}, resultCode={country_response.get('resultCode')}"
        print(f"[{AGENT_NAME}][3.3.5] 국가 상품 조회 resultCode={country_response.get('resultCode')!r}")
    except Exception as e:
        print(f"[{AGENT_NAME}][3.3.5] 국가 상품 조회 예외: {e}")
        state.add_ai_message("현재 내부 전산 오류로 나중에 다시 시도해주세요.")
        await set_cancelled(state, result=f"국가 상품 조회 오류: {e}")
        state.current_agent = None
        return state

    if country_response.get("resultCode") is not True:
        print(f"[{AGENT_NAME}][3.3.5] 국가 상품 조회 실패")
        state.add_ai_message("현재 내부 전산 오류로 나중에 다시 시도해주세요.")
        await set_cancelled(state, result="국가 상품 조회 resultCode=False")
        state.current_agent = None
        return state

    # 3.3.7: 필터링
    products    = country_response["resultValue"].get("products", [])
    daily_price = price / days if days else 0
    print(f"[{AGENT_NAME}][3.3.7] 전체 상품 수={len(products)}, daily_price={daily_price:.1f}, request_days={request_days}")

    alt_product_list = []
    skipped_useday = skipped_kaetong = skipped_silmyeong = skipped_price = 0
    for p in products:
        if p.get("useDay") != request_days:
            skipped_useday += 1
            continue
        if "개통희망일" in (p.get("description") or ""):
            skipped_kaetong += 1
            continue
        if "실명인증" in (p.get("name") or ""):
            skipped_silmyeong += 1
            continue
        p_use_day = p.get("useDay") or 1
        if p.get("price", 0) / p_use_day <= daily_price:
            skipped_price += 1
            continue
        alt_product_list.append(p)

    print(f"[{AGENT_NAME}][3.3.7] 필터링 결과: 통과={len(alt_product_list)}개")
    print(f"[{AGENT_NAME}][3.3.7] 탈락 상세: useDay불일치={skipped_useday}, 개통희망일={skipped_kaetong}, 실명인증={skipped_silmyeong}, 단가조건미달={skipped_price}")
    if alt_product_list:
        print(f"[{AGENT_NAME}][3.3.7] 통과 상품 목록:")
        for p in alt_product_list:
            code = p.get("code", p.get("productCode", ""))
            print(f"[{AGENT_NAME}][3.3.7]   - code={code!r}, name={p.get('name')!r}, price={p.get('price')}, useDay={p.get('useDay')}")

    if not alt_product_list:
        print(f"[{AGENT_NAME}][3.3.7] 조건 통과 상품 없음 → 종료")
        state.add_ai_message("죄송하지만 조건에 맞는 대체상품이 없습니다.")
        await set_cancelled(state, result="조건에 맞는 대체상품 없음")
        state.current_agent = None
        return state

    # AI 선정 전에 상위 20개로 제한 (가격 오름차순)
    alt_product_list_sorted = sorted(alt_product_list, key=lambda p: p.get("price", 0))[:20]
    print(f"[{AGENT_NAME}][3.3.8] AI 선정 입력 상품 수={len(alt_product_list_sorted)} (가격 오름차순 상위 20개)")

    # 3.3.8: AI 선정 (최대 5개)
    product_json = json.dumps(
        [
            {
                "code":   p.get("code", p.get("productCode", "")),
                "name":   p.get("name", ""),
                "price":  p.get("price", 0),
                "useDay": p.get("useDay", 0),
            }
            for p in alt_product_list_sorted
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
    async with session_event(state.session_id, EventType.LLM_GENERIC, "대체 상품 AI 선정",
                              task_id=state.task_id) as ev:
        llm_resp = await LLM_LIGHT.ainvoke([HumanMessage(content=ai_select_prompt)])
        ev.content = str(llm_resp.content)[:200]
        ev.llm = build_llm_meta(LLM_LIGHT.model, llm_resp.usage_metadata)
    print(f"[{AGENT_NAME}][3.3.8] AI 선정 LLM 원본 응답: {llm_resp.content!r}")
    try:
        selected_codes = _parse_llm_json(llm_resp.content)
        if not isinstance(selected_codes, list):
            raise ValueError(f"list 아님: {type(selected_codes)}")
        if not selected_codes:
            raise ValueError("AI가 대체상품을 매칭하지 못하고 빈 배열을 반환함")
        print(f"[{AGENT_NAME}][3.3.8] AI 선정 성공: {selected_codes}")
    except Exception as e:
        print(f"[{AGENT_NAME}][3.3.8] AI 선정 파싱 오류: {e} → fallback(상위 5개)")
        selected_codes = [
            p.get("code", p.get("productCode", "")) for p in alt_product_list_sorted[:5]
        ]
        print(f"[{AGENT_NAME}][3.3.8] fallback 선정: {selected_codes}")

    _set_param(state, "raw_product_list",    selected_codes)
    _set_param(state, "product_request_days", request_days)  # ★
    print(f"[{AGENT_NAME}][3.3] ══════════ select_product 완료: {selected_codes} ══════════")
    return state


# ── 3.4 상품 상세 조회 ────────────────────────────────────────────────────────

async def fetch_product_details(state: GraphState) -> GraphState:
    """상품 상세 조회 노드. raw_product_list 순회하며 product_list 완성."""
    params           = _get_params(state)
    raw_product_list = params.get("raw_product_list", [])
    product_list     = []

    for code in raw_product_list:
        try:
            async with session_event(state.session_id, EventType.API_CALL, "상품 상세 조회 API",
                                      task_id=state.task_id) as ev:
                response = await esim_client.call_esim_api_get(
                    f"https://wapi.wifidosirak.com/api/v1/product/{code}"
                )
                ev.content = f"code={code}, resultCode={response.get('resultCode')}"
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
            "OutOrderId":   f"{order_id}재발급",
            "Name":         order_info.get("name", ""),
            "ProductName":  first["product_name"],
            "ProductCode":  first["product_code"],
            "JoinRoute":    str(order_info.get("join_route_id", "")),
            "Qty":          params.get("request_qnt", 1),
            "Email":        order_info.get("email", ""),
            "Mobile":       order_info.get("mobile", ""),
            "PaymentPrice": 0,
            "UserID":       state.chat_context.get("user_id", ""),
        }
        _set_param(state, "current_payload",       payload)
        _set_param(state, "current_product_index", 0)

    p = _get_params(state).get("current_payload", {})

    lines = [
        "아래 내용으로 이심 재발급을 진행할까요?\n",
        f"• 외부주문번호: {p.get('OutOrderId', '')}",
        f"• 이름: {p.get('Name', '')}",
        f"• 이메일: {p.get('Email', '')}",
        f"• 전화번호: {p.get('Mobile', '')}",
        f"• 상품명: {p.get('ProductName', '')}",
        f"• 상품코드: {p.get('ProductCode', '')}",
        f"• 수량: {p.get('Qty', '')}",
        f"• 결제금액: {p.get('PaymentPrice', '')}원",
        "\n재발급을 원하시면 **실행** 또는 수정·상품변경 요청을 말씀해주세요.",
    ]
    state.add_ai_message("\n".join(lines))
    _set_param(state, "payload_shown",     True)
    _set_param(state, "payload_confirmed", False)
    await add_event(state.session_id, EventType.HITL_WAIT, "payload 컨펌 요청", task_id=state.task_id)
    await set_waiting(state, result="payload 컨펌 요청")
    return state


async def process_confirmation(state: GraphState) -> GraphState:
    """
    AI 노드 - 사용자 응답 의도 파악 및 payload 수정 (3.5.3).
    """
    params = _get_params(state)

    # ★ request_days 변경 감지 → 상품 재선정 트리거
    current_days = params.get("request_days")
    product_days = params.get("product_request_days")
    if product_days is not None and current_days != product_days:
        print(f"[{AGENT_NAME}] request_days 변경 감지: {product_days} → {current_days}, 상품 재선정 트리거")
        for key in ["raw_product_list", "product_list", "current_payload",
                    "product_request_days", "country_name", "needs_country"]:
            state.collected_parameters[AGENT_NAME].pop(key, None)
        state.collected_parameters[AGENT_NAME]["payload_shown"]     = False
        state.collected_parameters[AGENT_NAME]["payload_confirmed"] = False
        return state  # → route_after_confirmation → select_product로 빠짐

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
        '  "action": "confirm" | "modify" | "change_product" | "show",\n'
        '  "updated_payload": { },\n'
        f'  "next_product_index": {current_product_index},\n'
        '  "message_to_user": "..."\n'
        "}\n\n"
        "규칙:\n"
        "- confirm: '실행', '그대로 실행', '발송 실행', '실행해줘', '발송해'처럼 이심을 발송하라고 요청한 경우\n"
        "- show: '다시 보여줘', '보여줘', '내용 알려줘' 등 문맥상 조회를 요청했거나 이전 응답이 채팅방에 전달되지 않은 오류로 에이전트를 부르는 경우\n"
        "- modify: 특정 필드 수정 요청 (수정 가능 필드: Email, Mobile, Qty)\n"
        "- change_product: 상품 교체 요청 (next_product_index로 product_list에서 선택)\n"
        "- 수정 불가 필드(OutOrderId, UserID, JoinRoute, ProductCode, ProductName, PaymentPrice) "
        "변경 요청 시 action=modify, updated_payload 변경 없이, message_to_user에 '해당 항목은 수정이 불가합니다' 안내\n"
        "- updated_payload는 전체 payload를 반환 (수정된 버전 또는 기존 그대로)\n"
        "- 단순 조회 및 판단이 불가한 요청은 반드시 show로 분류할 것\n"
        "JSON만 반환."
    )

    async with session_event(state.session_id, EventType.LLM_GENERIC, "컨펌 응답 분석",
                              task_id=state.task_id) as ev:
        llm_resp = await LLM_LIGHT.ainvoke([HumanMessage(content=prompt)])
        ev.content = str(llm_resp.content)[:200]
        ev.llm = build_llm_meta(LLM_LIGHT.model, llm_resp.usage_metadata)
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
            updated_payload["PaymentPrice"] = 0
            _set_param(state, "current_payload",       updated_payload)
            _set_param(state, "current_product_index", next_index)
            if msg:
                state.add_ai_message(msg)
        _set_param(state, "payload_shown", False)

    elif action == "show":
        # 재발급 내용 다시 보여달라는 요청 → confirm_payload가 재표시되도록 payload_shown 리셋
        _set_param(state, "payload_shown", False)
        print(f"[{AGENT_NAME}] process_confirmation: show 요청 → confirm_payload 재표시")

    return state


# ── 3.6 이심 발송 ─────────────────────────────────────────────────────────────

async def send_esim(state: GraphState) -> GraphState:
    """
    이심 발송 API 호출 노드.
    request_qnt만큼 반복 호출 (건당 1개). 건당 최대 MAX_RETRY회 재시도.
    - 외부주문번호 중복 오류(400 포함): OutOrderId suffix 증가 후 재시도
    - 전체 완료 후 종합 리포트 출력
    """
    params      = _get_params(state)
    payload     = params.get("current_payload", {})
    request_qnt = params.get("request_qnt", 1)
    order_id    = params.get("order_id", "")

    print(f"[{AGENT_NAME}][3.6] ══════════ send_esim 진입 ══════════")
    print(f"[{AGENT_NAME}][3.6] request_qnt={request_qnt}, order_id={order_id!r}")
    print(f"[{AGENT_NAME}][3.6] current_payload={json.dumps(payload, ensure_ascii=False)}")

    # ── 결과 누적 ────────────────────────────────────────────────────────────
    results: list[dict] = []  # {"seq": int, "success": bool, "out_order_id": str, "reason": str}
    out_order_num = 1

    success            = False
    final_out_order_id = f"{order_id}재발급_{out_order_num}"
    fail_reason        = "MAX_RETRY 소진"

    for attempt in range(MAX_RETRY):
        out_order_id       = f"{order_id}재발급_{out_order_num}"
        final_out_order_id = out_order_id
        payload_i          = {**payload, "OutOrderId": out_order_id, "Qty": request_qnt}

        print(f"[{AGENT_NAME}][3.6] 시도 {attempt + 1}/{MAX_RETRY} | OutOrderId={out_order_id!r}")
        print(f"[{AGENT_NAME}][3.6] 전송 payload: {json.dumps(payload_i, ensure_ascii=False)}")

        try:
            async with session_event(state.session_id, EventType.API_CALL, "이심 발송 API",
                                      task_id=state.task_id) as ev:
                response = await esim_client.call_esim_api_post(
                    api_url=REGIST_URL,
                    json=payload_i,
                )
                ev.content = f"수량={request_qnt}, 시도={attempt + 1}, resultCode={response.get('resultCode')}, OutOrderId={out_order_id}"

            result_msg = response.get("resultMessage", "")
            print(f"[{AGENT_NAME}][3.6] 응답 resultCode={response.get('resultCode')!r}, resultMessage={result_msg!r}")
            print(f"[{AGENT_NAME}][3.6] 응답 전체: {json.dumps(response, ensure_ascii=False)}")

            if response.get("resultCode") is True:
                print(f"[{AGENT_NAME}][3.6] 발송 성공 (attempt={attempt + 1}, 수량={request_qnt})")
                success = True
                out_order_num += 1

                await insert_row(BQ_TABLE, {
                    "timestamp":      datetime.now(KST).isoformat(),
                    "order_id":       order_id,
                    "out_order_id":   out_order_id,
                    "product_code":   payload_i.get("ProductCode", ""),
                    "product_name":   payload_i.get("ProductName", ""),
                    "qty":            request_qnt,
                    "requester_id":   state.chat_context.get("user_id", ""),
                    "requester_name": state.chat_context.get("user_name", ""),
                })
                break

            elif "이미 처리된 외부주문번호" in result_msg:
                out_order_num += 1  # ← _1_1 대신 그냥 숫자 올림: _2, _3, ...
                fail_reason = result_msg
                print(f"[{AGENT_NAME}][3.6] 외부주문번호 중복 → {out_order_id!r} → {order_id}재발급_{out_order_num}로 재시도")

            else:
                fail_reason = result_msg or "resultCode=False"
                print(f"[{AGENT_NAME}][3.6] resultCode False → 재시도")

        except httpx.HTTPStatusError as e:
            # ★ 400 등 HTTP 오류 응답 바디에서 resultMessage 추출 시도
            result_msg = ""
            try:
                err_body   = e.response.json()
                result_msg = err_body.get("resultMessage", "")
                print(f"[{AGENT_NAME}][3.6] HTTP {e.response.status_code} 응답 바디: {err_body}")
            except Exception:
                print(f"[{AGENT_NAME}][3.6] HTTP {e.response.status_code} 바디 파싱 실패: {e.response.text}")

            if "이미 처리된 외부주문번호" in result_msg:
                out_order_num += 1
                fail_reason = result_msg
                print(f"[{AGENT_NAME}][3.6] 외부주문번호 중복 오류({e.response.status_code}) → {out_order_id!r} → {order_id}재발급_{out_order_num}로 재시도")
            else:
                fail_reason = result_msg or f"HTTP {e.response.status_code}"
                print(f"[{AGENT_NAME}][3.6] HTTP 오류 | attempt={attempt + 1}, status={e.response.status_code}")

        except Exception as e:
            fail_reason = f"{type(e).__name__}: {e}"
            print(f"[{AGENT_NAME}][3.6] 예외 발생 | attempt={attempt + 1}, error={fail_reason}")

    results.append({
        "seq":          1,
        "success":      success,
        "out_order_id": final_out_order_id,
        "reason":       "" if success else fail_reason,
    })
    print(f"[{AGENT_NAME}][3.6] 발송 결과: success={success}, OutOrderId={final_out_order_id!r}, reason={fail_reason!r}")

    # ── 종합 리포트 ───────────────────────────────────────────────────────────
    state.response.setdefault(AGENT_NAME, {})["send_results"] = results  # 결과를 GraphState에 저장하여 다음노드 상담등록에서 활용
    success_list = [r for r in results if r["success"]]
    fail_list    = [r for r in results if not r["success"]]
    product_name = payload.get("ProductName", "")

    success_cnt = request_qnt if success_list else 0
    lines = [f"**이심 재발급 발송 결과** ({success_cnt}/{request_qnt}건 처리)\n"]
    lines.append(f"• 상품명: {product_name}")

    for r in results:
        status = "✅ 성공" if r["success"] else "❌ 실패"
        line   = f"• {status} | 주문번호: {r['out_order_id']}"
        if not r["success"]:
            line += f" | 사유: {r['reason']}"
        lines.append(line)

    if fail_list:
        lines.append(f"\n{request_qnt}건 발송 실패 — 수동으로 재발급 처리가 필요합니다.")

    state.add_ai_message("\n".join(lines))

    if fail_list:
        print(f"[{AGENT_NAME}][3.6] 발송 실패 | 요청수량={request_qnt}")
        await add_event(state.session_id, EventType.CANCEL,
                        f"이심 발송 실패: {request_qnt}건",
                        task_id=state.task_id)
        await set_cancelled(state, result=f"이심 발송 실패: {[r['out_order_id'] for r in fail_list]}")
        return state

    _set_param(state, "send_success", True)
    print(f"[{AGENT_NAME}][3.6] ══════════ send_esim 전체 성공 ({request_qnt}건) ══════════")
    return state


# ── 3.7 상담 등록 ─────────────────────────────────────────────────────────────

async def register_consultation(state: GraphState) -> GraphState:
    """
    상담 등록 노드.
    device_agent에 register_consultation 작업 요청 후 interrupt로 대기.
    콜백 수신 시 재개되어 결과 처리.
    """
    params          = _get_params(state)
    thread_id       = state.chat_context.get("user_id", "")
    order_info      = params.get("order_info", {})
    payload         = params.get("current_payload", {})
    selected_iccids = params.get("selected_iccids", [])

    # ── LangGraph 재실행 방지 ────────────────────────────────────────────────
    job_id = params.get("_job_id")

    # interrupt() raise 시 _job_id가 checkpoint에 저장 안 되는 경우 → task_id로 복구
    # task_id는 다른 노드(update_parameters)에서 return으로 세팅되므로 checkpoint에 안전하게 저장됨
    if not job_id and state.task_id:
        recovered = await get_job_id_by_task_id(state.task_id)
        if recovered and recovered.startswith("__already_"):
            # 이미 완료된 job → device_agent 재전송 없이 interrupt만 대기
            print(f"[{AGENT_NAME}] task_id={state.task_id} → job 이미 완료, 재전송 차단")
            job_id = params.get("_job_id") or "__completed__"
        elif recovered:
            print(f"[{AGENT_NAME}] _job_id checkpoint 누락 → task_id로 복구: {recovered}")
            job_id = recovered
            _set_param(state, "_job_id", job_id)

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
            context={
                "user_name": state.chat_context.get("user_name"),
                "user_id":   state.chat_context.get("user_id", ""),
                "send_results": state.response.get(AGENT_NAME, {}).get("send_results", []),
                },
        )
        job_id = req.job_id
        _set_param(state, "_job_id", job_id)  # ★ interrupt() 전에 반드시 저장
        _set_param(state, "_job_sent_at", datetime.now(timezone.utc).isoformat())  # ★ 타임아웃 계산용

        await create_device_job(job_id, thread_id, task_id=state.task_id)

        try:
            async with session_event(state.session_id, EventType.DEVICE_JOB, "상담 등록 DeviceJob 요청",
                                      task_id=state.task_id) as ev:
                async with httpx.AsyncClient() as client:
                    resp = await client.post(
                        f"{DEVICE_JOB_URL}/device-job",
                        json=req.model_dump(),
                        headers={"X-Device-Job-Key": DEVICE_JOB_SECRET},
                        timeout=10.0,
                    )
                    resp.raise_for_status()
                ev.content = f"job_id: {job_id}"
        except Exception as e:
            state.add_ai_message(
                f"⚠️ PC 에이전트 연결에 실패했습니다. 잠시 후 다시 시도해주세요.\n({e})"
            )
            await set_cancelled(state, result=f"DeviceJob 전송 실패: {e}")
            state.current_agent = None
            return state

        state.add_ai_message("상담 등록 작업을 PC에이전트에 전달했습니다. 잠시만 기다려주세요.")
        await add_event(state.session_id, EventType.HITL_WAIT, "DeviceJob 콜백 대기", task_id=state.task_id)
        await set_waiting(state, result=f"DeviceJob 대기 중: {job_id}")

    # ── 콜백 타임아웃 체크 (재진입 시) ──────────────────────────────────────
    # 새 사용자 메시지가 오면 graph는 START부터 재실행되므로 register_consultation에 재진입함.
    # job이 이미 발송됐지만 JOB_CALLBACK_TIMEOUT 초 이상 콜백이 없으면 수동 처리 안내 후 종료.
    job_sent_at_str = params.get("_job_sent_at")
    if job_id and job_sent_at_str and not params.get("_callback_processed"):
        try:
            job_sent_at = datetime.fromisoformat(job_sent_at_str)
            elapsed = (datetime.now(timezone.utc) - job_sent_at).total_seconds()
            if elapsed > JOB_CALLBACK_TIMEOUT:
                print(f"[{AGENT_NAME}] DeviceJob 콜백 타임아웃 ({elapsed:.0f}초 경과) → 수동 처리 안내")
                p = payload
                lines = [
                    "⚠️ 이심 발송은 완료되었으나 상담 등록 응답 대기 시간이 초과되었습니다.\n",
                    f"• 외부주문번호: {p.get('OutOrderId', '')}",
                    f"• 수신자: {p.get('Name', '')}",
                    f"• 상품명: {p.get('ProductName', '')}",
                    f"• 상품코드: {p.get('ProductCode', '')}",
                    f"• 수량: {p.get('Qty', '')}",
                    f"• 이메일: {p.get('Email', '')}",
                    f"• 전화번호: {p.get('Mobile', '')}",
                    "\n수동으로 상담 등록을 확인해주세요.",
                ]
                state.add_ai_message("\n".join(lines))
                await add_event(state.session_id, EventType.COMPLETE,
                                "이심 발송 완료, 상담 등록 타임아웃", task_id=state.task_id)
                await set_completed(state, result="이심 발송 완료, 상담 등록 타임아웃")
                _set_param(state, "job_done", True)
                state.current_agent = None
                return state  # interrupt() 호출 없이 종료
        except Exception as e:
            print(f"[{AGENT_NAME}] 타임아웃 체크 오류: {e}")

    # interrupt — /device-callback 수신 시 자동 재개
    callback_payload: dict = interrupt({"job_id": job_id, "status": "waiting"})

    # 중복 실행 방지
    if params.get("_callback_processed"):
        state.current_agent = None
        return state
    _set_param(state, "_callback_processed", True)

    await add_event(state.session_id, EventType.HITL_RESUME, "DeviceJob 콜백 수신", task_id=state.task_id)
    callback = DeviceJobCallback(**callback_payload)

    if callback.status == "success":
        p = payload
        lines = [
            "✅ 이심 재발급 및 상담 등록이 완료되었습니다.\n",
            f"• 외부주문번호: {p.get('OutOrderId', '')}",
            f"• 수신자: {p.get('Name', '')}",
            f"• 상품명: {p.get('ProductName', '')}",
            f"• 상품코드: {p.get('ProductCode', '')}",
            f"• 수량: {p.get('Qty', '')}",
            f"• 이메일: {p.get('Email', '')}",
            f"• 전화번호: {p.get('Mobile', '')}",
        ]
        state.add_ai_message("\n".join(lines))
        await add_event(state.session_id, EventType.COMPLETE, "이심 재발급 및 상담 등록 완료", task_id=state.task_id)
        await set_completed(state, result="이심 재발급 및 상담 등록 완료")
    else:
        error_msg = callback.error_message or "알 수 없는 오류"
        lines = [
            "⚠️ 이심 발송은 완료되었으나 상담 등록 중 오류가 발생했습니다.\n",
            f"• 외부주문번호: {payload.get('OutOrderId', '')}",
            f"• 수신자: {payload.get('Name', '')}",
            f"• 상품명: {payload.get('ProductName', '')}",
            f"• 상품코드: {payload.get('ProductCode', '')}",
            f"• 수량: {payload.get('Qty', '')}",
            f"• 이메일: {payload.get('Email', '')}",
            f"• 전화번호: {payload.get('Mobile', '')}",
            f"\n수동으로 상담 등록을 진행해주세요. (사유: {error_msg})",
        ]
        state.add_ai_message("\n".join(lines))
        await set_completed(state, result=f"이심 발송 완료, 상담 등록 실패: {error_msg}")

    _set_param(state, "job_done", True)
    state.current_agent = None
    return state


# ── 취소 ─────────────────────────────────────────────────────────────────────

async def cancel_inquiry(state: GraphState) -> GraphState:
    """업무 취소 노드."""
    state.add_ai_message("이심 재발급 업무를 취소했습니다. 다른 문의가 있으시면 말씀해주세요.")
    await add_event(state.session_id, EventType.CANCEL, "업무 취소", task_id=state.task_id)
    await set_cancelled(state, result="담당자 취소 - 업무 취소")
    state.current_agent = None
    return state