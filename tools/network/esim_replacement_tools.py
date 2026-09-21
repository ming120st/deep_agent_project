"""
esim_replacement 딥에이전트 tools.
redis store 사용 시 workflow는 "esim_replacement_agent"로 지정.
"""
from __future__ import annotations

import json
import os
import re
from typing import Literal, Optional

import httpx
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool
from langgraph.types import interrupt
from langgraph.prebuilt import InjectedState

from google.cloud import bigquery as bq
from google.oauth2 import service_account
from typing import Annotated, Any

from integrations.device_job.device_job_manager import create_device_job, get_job_id_by_task_id
from tools.common.internal_esim_client import esim_client
from integrations.device_job.schemas import DeviceJobCallback, DeviceJobRequest
from core.database.redis import redis_put, redis_get, redis_delete, redis_exists


AGENT_NAME = "esim_replacement_agent"
REGIST_URL = "https://wapi.wifidosirak.com/api/v1/order/agentregist"
MAX_RETRY = 5
JOB_CALLBACK_TIMEOUT = 60  # DeviceJob 콜백 최대 대기 시간(초)

DEVICE_JOB_URL = os.getenv("DEVICE_JOB_URL", "")
DEVICE_JOB_SECRET = os.getenv("DEVICE_JOB_SECRET", "")

_BQ_SALES_PROJECT = os.getenv("BQ_SALES_PROJECT_ID", "daily-report-widemobile")
_BQ_SALES_DATASET = os.getenv("BQ_SALES_DATASET", "data_set")
_BQ_KEY_RAW = os.getenv("GOOGLE_SERVICE_ACCOUNT_KEY_PATH_GBQ", "")


# ── 내부 헬퍼 ──────────────────────────────────────────────────────────────

def _get_bq_sales_client() -> bq.Client:
    if _BQ_KEY_RAW:
        key_info = json.loads(_BQ_KEY_RAW)
        creds = service_account.Credentials.from_service_account_info(
            key_info, scopes=["https://www.googleapis.com/auth/bigquery"],
        )
        return bq.Client(project=_BQ_SALES_PROJECT, credentials=creds)
    return bq.Client(project=_BQ_SALES_PROJECT)


async def _resolve_internal_product_code(product_code_public: str) -> Optional[str]:
    """외부(숫자) 상품코드 → 내부 Product_Code 변환. 못 찾으면 None."""
    if not product_code_public:
        return None
    query = f"""
        SELECT Product_Code
        FROM `{_BQ_SALES_PROJECT}.{_BQ_SALES_DATASET}.esim_product_list`
        WHERE Product_Code_public = @pub_code
        LIMIT 1
    """
    job_config = bq.QueryJobConfig(
        query_parameters=[bq.ScalarQueryParameter("pub_code", "STRING", str(product_code_public))]
    )
    try:
        import asyncio
        loop = asyncio.get_event_loop()
        client = _get_bq_sales_client()

        def _run_query():
            rows = list(client.query(query, job_config=job_config).result())
            return rows[0]["Product_Code"] if rows else None

        return await loop.run_in_executor(None, _run_query)
    except Exception as e:
        print(f"[{AGENT_NAME}] BQ 코드 변환 오류: {e}")
        return None


def _cfg(config: RunnableConfig | None, key: str, default=""):
    if not config:
        return default
    return (config.get("configurable") or {}).get(key, default)


async def _fetch_order_detail(order_id: str) -> dict:
    """내부 헬퍼: 주문번호로 주문 상세 및 order_info를 조회한다."""
    if not order_id:
        return {"ok": False, "error": "주문번호가 비어있습니다."}
    formatted_order_id = esim_client.encrypt(order_id)
    try:
        response = await esim_client.call_esim_api_get(
            f"https://wapi.wifidosirak.com/api/v1/order/{formatted_order_id}"
        )
    except Exception as e:
        return {"ok": False, "error": f"주문 조회 API 오류: {e}"}

    if response.get("resultCode") is not True:
        return {"ok": False, "error": "주문 조회 실패 (resultCode=False)"}

    rv = response.get("resultValue", {})
    order_info = {
        "name": rv.get("name", ""),
        "email": rv.get("email", ""),
        "mobile": rv.get("mobile", ""),
        "status": rv.get("status", ""),
        "join_route_id": rv.get("joinRouteId", ""),
    }
    order_products = [
        {
            "product_code": item.get("productCode", ""),
            "product_name": item.get("productName", ""),
            "days": item.get("useDay", 0),
            "price": item.get("price", 0),
            "iccid": item.get("iccid", ""),
        }
        for item in rv.get("ordersDetail", [])
    ]
    await redis_put("esim_replacement_agent","order_detail", {"order_info": order_info, "order_products": order_products})
    return {"ok": True, "order_info": order_info, "order_products": order_products}


@tool
async def esim_fetch_order(
    order_id: Annotated[str, "E로 시작하는 내부 주문번호. 예: E26031101886"]
    ) -> dict:
    """
    주문번호(E로 시작하는 내부 주문번호)로 주문 상세 내용을 조회한다.

    반환값:
    - order_info: 주문자 정보.
    - order_products: 주문 상품 목록.
    - selection_required: order_products가 2개 이상이면 true.
        true인 경우, 다음 단계(esim_resolve_reissue_product 등)로 절대 바로 넘어가지 말고
        반드시 사용자에게 order_products의 상품명과 iccid를 모두 보여준 뒤
        재발급할 항목을 선택받아라 (복수 선택 가능, 단 서로 다른 product_code는 함께 선택 불가).
    """
    result = await _fetch_order_detail(order_id)
    if result.get("ok"):
        products = result.get("order_products", [])
        result["selection_required"] = len(products) > 1
        if result["selection_required"]:
            result["action_required"] = (
                "구매 상품이 여러 개입니다. 다음 tool을 호출하기 전에, "
                "지금 바로 아래 order_products의 상품명과 iccid를 사용자에게 보여주고 "
                "재발급할 항목을 선택받으세요 (복수 선택 가능)."
                "단, 서로 다른 product_code는 함께 선택할 수 없습니다."
            )
    return result


# ── 3.3 상품 결정 (동일일수 / alt코드) ────────────────────────────────────

@tool
async def esim_resolve_reissue_product(
    product_code_public: Annotated[str, "공개용 상품코드"],
    product_name: Annotated[str, "상품명"],
    days: Annotated[int, "구매일수"],
    price: Annotated[float, "상품가격"],
    request_days: Annotated[int, "사용자가 요청한 일수"],
) -> dict:
    """
    재발급용 상품코드를 결정한다.

    사용 조건:
    - 재발급 요청이 들어오면 우선 이 tool을 사용하여 기존 상품의 일수 변형 코드가 있는지 확인한다.
    - 재발급 대상 상품과 종류는 동일한데 "일수만" 다시 맞추면 되는 경우에 사용한다.
    - 사용자가 일수 외 다른 조건의 상품으로 변경을 요청한 경우에는 이 tool을 쓰지 말고 product-search 서브에이전트에 위임해야 한다.
    
    반환값 (status로 분기)
    - status="resolved"
        - product_code: 재발급에 사용할 확정된 상품코드.
        - reason: 어떤 근거로 확정됐는지 (요청일수 일치 / 대체코드 사용).
    - status="needs_product_search"
        - 동일/대체 상품코드로 요청일수를 맞출 수 없는 경우.
        - 이 결과를 받으면 반드시 product-search 서브에이전트에 위임해야 하며,
          위임 전에 사용자에게 "이심을 사용할 국가"를 먼저 확인해야 한다.
        - search_context: product-search 서브에이전트에 그대로 전달해야 하는 컨텍스트.
            - original_product_name: 원래 상품명 (product-search 서브에이전트가 동일한 상품을 선택하지 않도록 전달필요).
            - request_days: 사용자가 원하는 일수.
            - min_daily_price: 이 값 이하의 일단가를 가진 상품은 후보에서 제외해야 한다
              (더 싼 상품으로 대체 시 고객 불만이 발생할 수 있으므로, 대체 상품 탐색 시 반드시 준수).
    """
    internal_code = await _resolve_internal_product_code(product_code_public) or product_code_public

    if request_days == days:
        return {
            "status": "resolved",
            "product_code": internal_code,
            "reason": "요청일수와 구매일수가 동일하여 기존 상품코드 그대로 사용",
        }

    alt_code = re.sub(r"\d+D$", f"{request_days}D", internal_code)
    if alt_code != internal_code:
        try:
            alt_response = await esim_client.call_esim_api_get(
                f"https://wapi.wifidosirak.com/api/v1/product/{alt_code}"
            )
            if alt_response.get("resultCode") is True:
                return {
                    "status": "resolved",
                    "product_code": alt_code,
                    "reason": "동일 상품의 일수 변형 코드가 존재하여 사용",
                }
        except Exception as e:
            print(f"[{AGENT_NAME}] alt 코드 조회 예외: {e}")

    daily_price = price / days if days else 0
    return {
        "status": "needs_product_search",
        "reason": "동일/대체 코드로 해결 불가 — product-search 서브에이전트에 위임 필요",
        "search_context": {
            "original_product_name": product_name,
            "request_days": request_days,
            "min_daily_price": daily_price,  # 이 단가 이하 상품은 후보에서 제외해야 함 (손실 방지)
        },
    }


@tool
async def esim_get_product_detail(
    product_code: Annotated[str, "조회할 상품코드"] 
    ) -> dict:
    """상품코드로 상품 상세(상품코드/상품명/가격)를 조회한다. payload 조립 전 최종 확정 코드에 대해 호출."""
    try:
        response = await esim_client.call_esim_api_get(
            f"https://wapi.wifidosirak.com/api/v1/product/{product_code}"
        )
    except Exception as e:
        return {"ok": False, "error": f"상품 상세 조회 오류: {e}"}

    if response.get("resultCode") is not True:
        return {"ok": False, "error": "상품 상세 조회 실패 (resultCode=False)"}

    rv = response["resultValue"]
    return {
        "ok": True,
        "product_code": rv.get("code", product_code),
        "product_name": rv.get("name", ""),
        "product_price": rv.get("price", 0),

    }

# ── 3.5 payload 조립 ───────────────────────────────────────────────────────

@tool
async def esim_build_confirmation(
    order_id: Annotated[str, "E로 시작하는 내부 주문번호 (예: E26031101886)"],
    name: Annotated[str, "수령인/구매자 이름 (order_info의 name)"],
    email: Annotated[str, "수령인/구매자 이메일 (order_info의 email)"],
    mobile: Annotated[str, "수령인/구매자 휴대폰 번호 (order_info의 mobile)"],
    join_route_id: Annotated[str | int, "주문의 가입경로 ID (order_info의 join_route_id)"],
    product_name: Annotated[str, "재발급할 확정 상품명 (esim_get_product_detail 결과의 product_name)"],
    product_code: Annotated[str | int, "재발급할 확정 상품코드 (esim_get_product_detail 결과의 product_code 숫자 코드 등)"],
    qty: Annotated[int, "재발급 수량"],
    config: RunnableConfig = None,
    state: Annotated[dict, InjectedState] = None,
) -> dict:
    """
    발송용 payload를 조립해서 리턴한다. (실행은 하지 않음)
    이 결과를 사용자에게 보여주고 확답을 받은 뒤에만 esim_send를 호출할 것.
    사용자에게 결과를 보여줄 때 메인에이전트에게 payload 항목을 빼먹지말고 모두 사용자에게 전달하라고 지시할 것.
    """
    # UserID 결정: Google Chat 요청자의 이메일 계정 (config 또는 state)
    user_id = (
        _cfg(config, "user_email")
        or (state.get("user_email") if isinstance(state, dict) else "")
        or _cfg(config, "user_id")
        or (state.get("user_id") if isinstance(state, dict) else "")
        or ""
    )
    # users/112... 형태의 Google Resource ID는 API 등록계정이 아니므로 배제
    if str(user_id).startswith("users/"):
        user_id = _cfg(config, "user_email") or (state.get("user_email") if isinstance(state, dict) else "") or ""

    print(f"[DEBUG esim_build_confirmation] config: {config}")
    print(f"[DEBUG esim_build_confirmation] state keys: {list(state.keys()) if isinstance(state, dict) else state}")
    print(f"[DEBUG esim_build_confirmation] resolved user_id: {user_id}")

    payload = {
        "OutOrderId": f"{order_id}재발급",
        "Name": name,
        "ProductName": product_name,
        "ProductCode": str(product_code),
        "JoinRoute": str(join_route_id),  # 가입 경로 ID. 헤더에 들어가는 루트ID 아님.
        "Qty": qty,
        "Email": email,
        "Mobile": mobile,
        "PaymentPrice": 0,
        "UserID": user_id,
    }
    print(f"[DEBUG esim_build_confirmation] generated payload: {payload}")
    return {"ok": True, "payload": payload}


# ── 3.6 이심 발송 ─────────────────────────────────────────────────────────

@tool
async def esim_send(
    payload: dict,
    request_qnt: int,
    order_id: str,
    config: RunnableConfig = None,
    state: Annotated[dict, InjectedState] = None,
) -> dict:
    """
    이심 발송 API를 호출한다. 사용자의 명시적 확답 없이는 절대 호출하지 말 것.
    OutOrderId 중복 오류는 suffix를 올려가며 최대 MAX_RETRY회 자동 재시도한다.
    """
    target_order_id = order_id or re.sub(r"재발급.*$", "", str(payload.get("OutOrderId", "")))
    payload_to_send = dict(payload)

    # 요청자 UserID (Google Chat 요청자의 widemobile 이메일 계정)
    requester_user_id = (
        _cfg(config, "user_email")
        or (state.get("user_email") if isinstance(state, dict) else "")
        or _cfg(config, "user_id")
        or (state.get("user_id") if isinstance(state, dict) else "")
        or ""
    )
    if str(requester_user_id).startswith("users/"):
        requester_user_id = _cfg(config, "user_email") or (state.get("user_email") if isinstance(state, dict) else "") or ""

    print(f"[DEBUG esim_send] config: {config}")
    print(f"[DEBUG esim_send] state keys: {list(state.keys()) if isinstance(state, dict) else state}")
    print(f"[DEBUG esim_send] incoming payload: {payload}")
    print(f"[DEBUG esim_send] requester_user_id: {requester_user_id}")

    # UserID가 비어있거나 Google Resource ID(users/...) 형태이면 요청자의 이메일 계정으로 설정 (주문자 이메일 사용 금지)
    current_uid = payload_to_send.get("UserID", "")
    if not current_uid or str(current_uid).startswith("users/"):
        payload_to_send["UserID"] = requester_user_id

    print(f"[DEBUG esim_send] final payload_to_send: {payload_to_send}")

    results = []
    out_order_num = 1
    success = False
    final_out_order_id = f"{target_order_id}재발급_{out_order_num}"
    fail_reason = "MAX_RETRY 소진"

    for attempt in range(MAX_RETRY):
        out_order_id = f"{target_order_id}재발급_{out_order_num}"
        final_out_order_id = out_order_id
        payload_i = {**payload_to_send, "OutOrderId": out_order_id, "Qty": request_qnt}
        print(f"[DEBUG esim_send] [{attempt + 1}/{MAX_RETRY}] Request POST {REGIST_URL}: {json.dumps(payload_i, ensure_ascii=False)}")

        try:
            response = await esim_client.call_esim_api_post(api_url=REGIST_URL, json=payload_i)
            print(f"[DEBUG esim_send] [{attempt + 1}/{MAX_RETRY}] Response: {response}")
            result_msg = response.get("resultMessage", "")

            if response.get("resultCode") is True:
                success = True
                out_order_num += 1
                break
            elif "이미 처리된 외부주문번호" in result_msg:
                out_order_num += 1
                fail_reason = result_msg
            else:
                fail_reason = result_msg or "resultCode=False"

        except httpx.HTTPStatusError as e:
            try:
                err_body = e.response.json()
                result_msg = err_body.get("resultMessage", "")
            except Exception:
                result_msg = ""
            print(f"[DEBUG esim_send] [{attempt + 1}/{MAX_RETRY}] HTTPStatusError: {e}, body: {result_msg}")
            if "이미 처리된 외부주문번호" in result_msg:
                out_order_num += 1
                fail_reason = result_msg
            else:
                fail_reason = result_msg or f"HTTP {e.response.status_code}"
        except Exception as e:
            print(f"[DEBUG esim_send] [{attempt + 1}/{MAX_RETRY}] Exception: {e}")
            fail_reason = f"{type(e).__name__}: {e}"

    return {
        "success": success,
        "out_order_id": final_out_order_id,
        "reason": "" if success else fail_reason,
        "product_name": payload_to_send.get("ProductName", ""),
        "product_code": payload_to_send.get("ProductCode", ""),
        "qty": request_qnt,
    }


# ── 3.7 상담 등록 (device_agent 콜백 대기) ─────────────────────────────────

@tool
async def esim_register_consultation(
    order_id: str,
    name: str,
    product_name: str,
    iccids: list[str],
    send_success: bool,
    send_results: list[dict],
    config: RunnableConfig,
) -> dict:
    """
    이심 발송 완료 후 상담 등록을 device_agent(PC 에이전트)에 요청하고 완료 콜백을 기다린다.
    이건 사용자 승인 대기가 아니라 외부 비동기 콜백 대기이므로 interrupt()를 직접 사용한다.
    send_success가 False면 호출하지 말 것 — 발송 실패 시 상담 등록 자체를 하지 않는다.
    send_results에는 esim_send 반환값을 목록으로 감싸서 전달할 것.
    예: send_results=[esim_send의 반환값]
    """
    if not send_success:
        return {
            "ok": False,
            "error": "이심 발송이 실패하여 상담 등록을 요청하지 않았습니다.",
        }

    if not name and order_id:
        order_res = await _fetch_order_detail(order_id)
        if order_res.get("ok"):
            name = order_res.get("order_info", {}).get("name", "")

    task_id = _cfg(config, "task_id")
    thread_id = _cfg(config, "thread_id")
    user_id = _cfg(config, "user_id") or thread_id
    user_name = _cfg(config, "user_name") or name
    space_id = _cfg(config, "space_id")
    google_thread_name = _cfg(config, "google_thread_name")

    if not task_id:
        return {
            "ok": False,
            "error": "task_id가 없습니다.",
        }

    if not thread_id:
        return {
            "ok": False,
            "error": "thread_id가 없습니다.",
        }

    normalized_send_results = []
    for seq, result in enumerate(send_results, start=1):
        if not isinstance(result, dict):
            return {
                "ok": False,
                "error": "send_results는 발송 결과 객체의 목록이어야 합니다.",
            }

        normalized_send_results.append(
            {
                "seq": result.get("seq", seq),
                "out_order_id": result.get("out_order_id", ""),
                "success": result.get("success", False),
                "reason": result.get("reason", ""),
            }
        )

    if not normalized_send_results:
        return {
            "ok": False,
            "error": "상담 등록에 필요한 발송 결과가 없습니다.",
        }

    # ── 멱등성 가드: 프레임워크(딥에이전트/구그래프 상관없이) task_id 기준 외부 DB로 판단 ──
    # interrupt() 재실행 시 이 함수 전체가 처음부터 다시 실행되므로,
    # "이미 device_agent에 요청을 보냈는가"는 반드시 이 함수 내부 변수가 아니라
    # 외부(DB)에 남긴 흔적으로 판단해야 한다. (원본 node.py의 _job_id 가드와 동일한 이유)
    job_id = await get_job_id_by_task_id(task_id) if task_id else None

    if not job_id:
        request = DeviceJobRequest(
            thread_id=thread_id,
            task="register_consultation",
            parameters={
                "order_id": order_id,
                "name": name,
                "product_name": product_name,
                "iccids": iccids,
                "iccid_list": iccids,
                "send_success": send_success,
                "memo": f"이심 재발급 완료. 발송 상품: {product_name}",
            },
            context={
                "user_id": user_id,
                "user_name": user_name,
                "send_results": normalized_send_results,
            },
        )
        job_id = request.job_id
        await create_device_job(
            job_id,
            thread_id,
            task_id=task_id,
            space_id=space_id,
            google_thread_name=google_thread_name,
            user_id=user_id,
        )

        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    f"{DEVICE_JOB_URL}/device-job",
                    json=request.model_dump(),
                    headers={"X-Device-Job-Key": DEVICE_JOB_SECRET},
                    timeout=10.0,
                )
                print(
                    "[EsimRegisterConsultation] device-job response:",
                    response.status_code,
                    response.text,
                )
                response.raise_for_status()

                print(
                    "[EsimRegisterConsultation] DeviceJob 전송 성공",
                    response.status_code,
                    response.text,
                )
        except Exception as e:
            return {
                "ok": False,
                "job_id": job_id,
                "error": f"PC 에이전트 작업 요청 실패: {e}",
            }

    # interrupt — /device-callback 수신 시 Command(resume=...)로 재개
    callback_payload: dict = interrupt(
        {
            "job_id": job_id,
            "status": "waiting",
            "message": "이심 재발급 상담 등록 작업 진행 중",
        }
    )
    print(
        "[EsimRegisterConsultation] interrupt resume:",
        callback_payload,
    )
    callback = DeviceJobCallback(**callback_payload)

    if callback.status == "error":
        return {
            "ok": False,
            "job_id": job_id,
            "error": callback.error_message or "UNKNOWN",
        }

    result = callback.result or {}

    if result.get("success") is False:
        return {
            "ok": False,
            "job_id": job_id,
            "error": result.get("reason", "UNKNOWN"),
        }

    return {
        "ok": True,
        "job_id": job_id,
        **result,
    }
