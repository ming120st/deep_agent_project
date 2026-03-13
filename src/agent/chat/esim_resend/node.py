from src.workflow.graph_infra.state import GraphState
from src.core.internal_esim_client import esim_client
from src.core.task_manager import set_waiting, set_completed, set_cancelled
import httpx

################################################################################
#  이심 재발송 업무 노드 (esim_resend)
#
#  청자: 내부 직원 (고객 CS 처리 담당자)
#
#  흐름:
#    ask_order_id
#      → fetch_order (자동)
#        → show_product_list          (상품 목록 보여주고 재발송 대상 선택 받기)
#          → ask_send_target          (이메일/전화번호 수신처 받기)
#            → resend_esim            (선택된 orderDetailId 순회하며 API 호출)
################################################################################

AGENT_NAME = "esim_resend"

RESEND_URL = "https://wapi.wifidosirak.com/api/v1/order/resend"


# ── 헬퍼 ─────────────────────────────────────────────────────────────────────

def _get_params(state: GraphState) -> dict:
    return state.collected_parameters.get(AGENT_NAME, {})


def _set_param(state: GraphState, key: str, value) -> None:
    if AGENT_NAME not in state.collected_parameters:
        state.collected_parameters[AGENT_NAME] = {}
    state.collected_parameters[AGENT_NAME][key] = value


# ── 노드 ─────────────────────────────────────────────────────────────────────

async def ask_order_id(state: GraphState) -> GraphState:
    """주문번호 요청 노드."""
    state.add_ai_message(
        "이심 재발송을 진행합니다.\n"
        "재발송할 **인트라넷 주문번호(E로 시작)**를 알려주세요."
    )
    await set_waiting(state, result="주문번호 입력 요청")
    return state


async def fetch_order(state: GraphState) -> GraphState:
    """
    주문 조회 노드 (자동 진행).
    성공 시 orders_detail 저장. 실패 시 order_id 초기화 → 다음 턴 ask_order_id 재라우팅.
    """
    params   = _get_params(state)
    order_id = params.get("order_id")

    formatted_order_id = esim_client.encrypt(order_id)

    try:
        response     = await esim_client.call_esim_api_get(
            f"https://wapi.wifidosirak.com/api/v1/order/{formatted_order_id}"
        )
        result_value  = response["resultValue"]
        orders_detail = result_value["ordersDetail"]

        if not orders_detail:
            raise ValueError("ordersDetail이 비어있습니다.")

        # 주문자 연락처: ask_send_target에서 기본값으로 제안
        _set_param(state, "orders_detail", orders_detail)
        _set_param(state, "order_contact", {
            "name":   result_value.get("name", ""),
            "email":  result_value.get("email", ""),
            "mobile": result_value.get("mobile", ""),
        })
        print(f"[{AGENT_NAME}] 주문 조회 성공 | order_id={order_id}, 상품 수={len(orders_detail)}")

    except (httpx.HTTPStatusError, KeyError, ValueError) as e:
        print(f"[{AGENT_NAME}] 주문 조회 실패 | order_id={order_id}, error={e}")
        # order_id 초기화 → route_after_fetch에서 "end", 다음 턴 ask_order_id 재라우팅
        state.collected_parameters.get(AGENT_NAME, {}).pop("order_id", None)
        state.add_ai_message(
            f"주문번호 **{order_id}**로 주문을 조회할 수 없습니다.\n"
            "주문번호를 다시 확인 후 입력해주세요."
        )

    return state


async def show_product_list(state: GraphState) -> GraphState:
    """
    조회된 상품 목록을 보여주고 재발송할 항목 선택을 요청하는 노드.
    각 상품의 productName, iccid, orderDetailId를 안내.
    """
    orders_detail = _get_params(state).get("orders_detail", [])

    lines = ["조회된 이심 상품 목록입니다. 재발송할 항목을 선택해주세요.\n"]
    for i, item in enumerate(orders_detail, start=1):
        product_name    = item.get("productName", "-")
        iccid           = item.get("iccid", "-") or "-"
        order_detail_id = item.get("orderDetailId", "-")
        status   = item.get("status", "")
        status_tag      = " ⚠️ (취소됨)" if status == "4101002" else " ⚠️ (주문대기상태)" if status == "4101003" else "✅ (재발송 가능)"
        lines.append(f"{i}. {product_name}\n   ICCID: {iccid}\n   주문상세ID: {order_detail_id}{status_tag}")

    lines.append("\n전부 다 재발송할까요?, 일부만 재발송 하시려면 번호 또는 상세주문번호를 알려주세요.")
    state.add_ai_message("\n".join(lines))
    await set_waiting(state, result="재발송 대상 선택 요청")
    return state


async def ask_send_target(state: GraphState) -> GraphState:
    """
    수신처 입력 요청 노드.
    주문 조회 시 확보한 이메일/전화번호를 기본값으로 제안.
    """
    params        = _get_params(state)
    order_contact = params.get("order_contact", {})
    name          = order_contact.get("name", "") or ""
    email         = order_contact.get("email", "") or ""
    mobile        = order_contact.get("mobile", "") or ""

    contact_lines = []
    if email:
        contact_lines.append(f"• 이메일: {email}")
    if mobile:
        contact_lines.append(f"• 전화번호: {mobile}")

    if contact_lines:
        contact_info = "\n".join(contact_lines)
        name_str     = f"({name})" if name else ""
        message = (
            f"주문자{name_str} 연락처 정보입니다.\n"
            f"{contact_info}\n\n"
            "이 수신처로 발송할까요?.\n"
            "혹시 다른 수신처로 발송하시려면 이메일 또는 전화번호를 입력해주세요. "
            "(둘 중 하나 이상은 필요합니다!)"
        )
    else:
        message = (
            "주문에 등록된 연락처 정보가 없습니다.\n"
            "재발송할 **수신처**를 직접 입력해주세요.\n"
            "예) 이메일: hong@example.com / 전화번호: 01012345678"
        )

    state.add_ai_message(message)
    await set_waiting(state, result="재발송 수신처 확인 요청")
    return state


async def cancel_inquiry(state: GraphState) -> GraphState:
    """업무 취소 노드."""
    state.add_ai_message(
        "이심 재발송 업무를 취소했습니다. 다른 문의가 있으시면 말씀해주세요."
    )
    await set_cancelled(state, result="담당자 취소 - 업무 취소")
    state.current_agent = None
    return state


async def resend_esim(state: GraphState) -> GraphState:
    """
    재발송 API 호출 노드.
    selected_order_detail_ids 리스트를 순회하며 각각 API 호출.
    resultMessage를 수집하여 결과 요약을 반환.
    """
    params                    = _get_params(state)
    selected_ids: list        = params.get("selected_order_detail_ids", [])
    email: str                = params.get("email", "") or ""
    mobile: str               = params.get("mobile", "") or ""

    results = []  # (orderDetailId, resultMessage, success)

    for order_detail_id in selected_ids:
        payload = {
            "orderDetailId": order_detail_id,
            "email":         email,
            "mobile":        mobile,
        }
        try:
            response       = await esim_client.call_esim_api_post(RESEND_URL, json=payload)
            result_code    = response.get("resultCode", False)
            result_message = response.get("resultMessage", "응답 없음")
            print(f"[{AGENT_NAME}] 재발송 결과 | id={order_detail_id}, code={result_code}, msg={result_message}")
            results.append((order_detail_id, result_message, result_code))

        except Exception as e:
            print(f"[{AGENT_NAME}] 재발송 오류 | id={order_detail_id}, error={e}")
            results.append((order_detail_id, f"오류 발생: {e}", False))

    # 결과 메시지 조합
    total    = len(results)
    success  = sum(1 for _, _, ok in results if ok)
    lines    = [f"이심 재발송 처리 결과 ({success}/{total} 성공)\n"]

    for order_detail_id, result_message, ok in results:
        icon = "✅" if ok else "❌"
        lines.append(f"{icon} {order_detail_id}\n   {result_message}")

    state.add_ai_message("\n".join(lines))
    _set_param(state, "resend_success", success == total)

    if success == total:
        await set_completed(state, result=f"이심 재발송 완료 ({total}건)")
    else:
        await set_cancelled(state, result=f"이심 재발송 부분 실패 ({success}/{total})")

    state.current_agent = None
    return state
