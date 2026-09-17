"""
esim_fault_replacement 딥에이전트 tools.

기존 src/agent/esim/subagents/chat/esim_fault_replacement/node.py 에서
"사용자에게 묻고 기다리는" 부분(request_parameters, select_iccid의 목록표시,
confirm_payload, process_confirmation의 의도분류 LLM, validate_iccid의 선택파싱 LLM)은
전부 삭제했습니다. 그 역할은 이제 상위 딥에이전트의 ReAct 루프가 대신합니다.

이 파일에 남긴 건 순수 결정론적 비즈니스 로직뿐입니다:
  - 주문 조회
  - 상품코드 결정 (동일일수 / alt코드) — 안 되면 product-search 서브에이전트로 위임 신호만 리턴
  - 상품 상세 조회
  - payload 조립
  - 발송 API 호출 (재시도 포함)
  - 상담 등록 (device_agent 콜백 대기 — 진짜 비동기 대기라 interrupt() 유지)

TODO(마이그레이션 담당자):
  - 아래 import 경로는 기존 node.py 기준 그대로 가져왔습니다. 실제 레포 구조에 맞게 확인해주세요.
  - RunnableConfig에서 session_id/task_id/user_id/user_name을 어떻게 주입할지는
    BOA 메인 딥에이전트가 thread_id/configurable을 어떻게 구성하는지에 맞춰 조정이 필요합니다.
    (아래에서는 config["configurable"] 딕셔너리에 들어있다고 가정)
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

from google.cloud import bigquery as bq
from google.oauth2 import service_account
from typing import Annotated, Any

from integrations.device_job.device_job_manager import create_device_job, get_job_id_by_task_id
from tools.common.internal_esim_client import esim_client
from integrations.device_job.schemas import DeviceJobCallback, DeviceJobRequest

AGENT_NAME = "esim_fault_replacement"
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


def _cfg(config: RunnableConfig, key: str, default=""):
    return (config.get("configurable") or {}).get(key, default)


# ── 3.2 주문 조회 ─────────────────────────────────────────────────────────

@tool
async def esim_fetch_order(order_id: Annotated[str, "E로 시작하는 내부 주문번호. 예: E26031101886"]) -> dict:
    """
    주문번호(E로 시작하는 내부 주문번호)로 주문 상세를 조회한다.
    반환된 order_products 각 항목에는 product_code(외부코드), product_name, days(구매일수), price, iccid가 들어있다.
    """
    formatted_order_id = esim_client.encrypt(order_id)
    try:
        response = await esim_client.call_esim_api_get(
            f"https://wapi.wifidosirak.com/api/v1/order/{formatted_order_id}"
        )
    except Exception as e:
        return {"ok": False, "error": f"주문 조회 API 오류: {e}"}

    if response.get("resultCode") is not True:
        return {"ok": False, "error": "주문 조회 실패 (resultCode=False)"}

    rv = response["resultValue"]
    order_info = {
        "name": rv.get("name", ""),
        "email": rv.get("email", ""),
        "mobile": rv.get("mobile", ""),
        "status": rv.get("status", ""),
        "join_route_id": rv.get("joinRouteId", ""),
    }
    order_products = [
        {
            "product_code": item["productCode"],
            "product_name": item["productName"],
            "days": item["useDay"],
            "price": item["price"],
            "iccid": item.get("iccid", ""),
        }
        for item in rv.get("ordersDetail", [])
    ]
    return {"ok": True, "order_info": order_info, "order_products": order_products}


# ── 3.3 상품 결정 (동일일수 / alt코드) ────────────────────────────────────

@tool
async def esim_resolve_reissue_product(
    product_code_public: str,
    product_name: str,
    days: int,
    price: float,
    request_days: int,
) -> dict:
    """
    재발급용 상품코드를 결정한다.
    사용자의 요청으로 상품을 변경할 때는 이 tool을 사용하지 않고, product-search 서브에이전트에 위임해야 한다.
    1) 요청일수 == 구매일수면 기존 상품 그대로 사용.
    2) 다르면 같은 상품의 일수만 바꾼 대체코드(alt code)를 시도.
    3) 그것도 안 되면 status="needs_product_search"를 리턴 — 이 경우
       product-search 서브에이전트에 위임해서 국가 기반으로 대체 상품을 찾아야 한다.
       (이때 사용자에게 이심을 사용할 국가명을 먼저 물어봐야 함)
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
async def esim_get_product_detail(product_code: str) -> dict:
    """상품코드로 상품 상세(이름/가격)를 조회한다. payload 조립 전 최종 확정 코드에 대해 호출."""
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
    order_id: str,
    name: str,
    email: str,
    mobile: str,
    join_route_id: str,
    product_name: str,
    product_code: str,
    qty: int,
    config: RunnableConfig,
) -> dict:
    """
    발송용 payload를 조립해서 리턴한다. (실행은 하지 않음)
    이 결과를 사용자에게 보여주고 확답을 받은 뒤에만 esim_send를 호출할 것.
    """
    user_id = _cfg(config, "user_id")
    payload = {
        "OutOrderId": f"{order_id}재발급",
        "Name": name,
        "ProductName": product_name,
        "ProductCode": product_code,
        "JoinRoute": str(join_route_id), # 가입 경로 ID. 헤더에 들어가는 루트ID 아님.
        "Qty": qty,
        "Email": email,
        "Mobile": mobile,
        "PaymentPrice": 0,
        "UserID": user_id,
    }
    return {"ok": True, "payload": payload}


# ── 3.6 이심 발송 ─────────────────────────────────────────────────────────

@tool
async def esim_send(payload: dict, request_qnt: int, order_id: str) -> dict:
    """
    이심 발송 API를 호출한다. 사용자의 명시적 "실행" 확답 없이는 절대 호출하지 말 것.
    (상위 딥에이전트 설정에서 interrupt_on으로 이 tool에 승인 게이트가 걸려 있어야 한다.)
    OutOrderId 중복 오류는 suffix를 올려가며 최대 MAX_RETRY회 자동 재시도한다.
    """
    results = []
    out_order_num = 1
    success = False
    final_out_order_id = f"{order_id}재발급_{out_order_num}"
    fail_reason = "MAX_RETRY 소진"

    for attempt in range(MAX_RETRY):
        out_order_id = f"{order_id}재발급_{out_order_num}"
        final_out_order_id = out_order_id
        payload_i = {**payload, "OutOrderId": out_order_id, "Qty": request_qnt}

        try:
            response = await esim_client.call_esim_api_post(api_url=REGIST_URL, json=payload_i)
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
            if "이미 처리된 외부주문번호" in result_msg:
                out_order_num += 1
                fail_reason = result_msg
            else:
                fail_reason = result_msg or f"HTTP {e.response.status_code}"
        except Exception as e:
            fail_reason = f"{type(e).__name__}: {e}"

    return {
        "success": success,
        "out_order_id": final_out_order_id,
        "reason": "" if success else fail_reason,
        "product_name": payload.get("ProductName", ""),
        "product_code": payload.get("ProductCode", ""),
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
    config: RunnableConfig,
) -> dict:
    """
    이심 발송 완료 후 상담 등록을 device_agent(PC 에이전트)에 요청하고 완료 콜백을 기다린다.
    이건 사용자 승인 대기가 아니라 외부 비동기 콜백 대기이므로 interrupt()를 직접 사용한다.
    send_success가 False면 호출하지 말 것 — 발송 실패 시 상담 등록 자체를 하지 않는다.
    """
    task_id = _cfg(config, "task_id")
    thread_id = _cfg(config, "user_id")

    # ── 멱등성 가드: 프레임워크(딥에이전트/구그래프 상관없이) task_id 기준 외부 DB로 판단 ──
    # interrupt() 재실행 시 이 함수 전체가 처음부터 다시 실행되므로,
    # "이미 device_agent에 요청을 보냈는가"는 반드시 이 함수 내부 변수가 아니라
    # 외부(DB)에 남긴 흔적으로 판단해야 한다. (원본 node.py의 _job_id 가드와 동일한 이유)
    job_id = await get_job_id_by_task_id(task_id) if task_id else None

    if not job_id:
        req = DeviceJobRequest(
            thread_id=thread_id,
            task="register_consultation",
            parameters={
                "order_id": order_id,
                "name": name,
                "product_name": product_name,
                "iccid_list": iccids,
                "memo": f"이심 재발급 완료. 발송 상품: {product_name}",
            },
            context={"user_id": thread_id},
        )
        job_id = req.job_id
        await create_device_job(job_id, thread_id, task_id=task_id)

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
            return {"ok": False, "error": f"PC 에이전트 연결 실패: {e}"}

    # interrupt — /device-callback 수신 시 Command(resume=...)로 재개
    callback_payload: dict = interrupt({"job_id": job_id, "status": "waiting"})
    callback = DeviceJobCallback(**callback_payload)

    if callback.status == "success":
        return {"ok": True, "job_id": job_id}
    return {"ok": False, "job_id": job_id, "error": callback.error_message or "알 수 없는 오류"}