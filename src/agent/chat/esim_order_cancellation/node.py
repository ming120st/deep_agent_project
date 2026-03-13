from src.workflow.graph_infra.state import GraphState
from src.core.internal_esim_client import esim_client
from src.core.task_manager import set_waiting, set_completed, set_cancelled
import httpx

################################################################################
#  이심 주문취소 업무 노드
#
#  흐름:
#    ask_order_id → fetch_order → confirm_cancelable → cancel_order
#                              ↘ handle_non_cancelable
#                 ↘ (조회실패) → END (다음 턴 ask_order_id 재라우팅)
################################################################################

AGENT_NAME = "esim_order_cancellation"


# ── 헬퍼 ─────────────────────────────────────────────────────────────────────

def _get_params(state: GraphState) -> dict:
    return state.collected_parameters.get(AGENT_NAME, {})


def _set_param(state: GraphState, key: str, value) -> None:
    if AGENT_NAME not in state.collected_parameters:
        state.collected_parameters[AGENT_NAME] = {}
    state.collected_parameters[AGENT_NAME][key] = value


# ── 노드 ─────────────────────────────────────────────────────────────────────

async def ask_order_id(state: GraphState) -> GraphState:
    """주문번호 요청 노드. 최초 진입 또는 주문번호 미수집 시 실행."""
    state.add_ai_message(
        "이심 주문취소를 진행하겠습니다!\n"
        "E로 시작하는 인트라넷 주문번호를 알려주세요.\n\n"
        "📌 안내사항\n"
        "• 시락이 에이전트를 통한 이심 취소는 구매 상품 전체 취소신청만 가능합니다.\n"
        "• 취소신청/승인/불가 처리가 없는 주문만 취소신청이 가능하며 설치 이력이 없는 경우에만 환불이 진행됩니다.\n"
        "• 부분 취소는 인트라넷에서 직접 처리해주세요."
    )
    await set_waiting(state, result="주문번호 입력 요청")
    return state


async def fetch_order(state: GraphState) -> GraphState:
    """주문 조회 노드. 성공 시 cancelable/order_info 설정, 실패 시 order_id 초기화."""
    order_id = _get_params(state).get("order_id")
    formatted_order_id = esim_client.encrypt(order_id)

    try:
        response = await esim_client.call_esim_api_get(
            f"https://wapi.wifidosirak.com/api/v1/order/{formatted_order_id}"
        )
        result_value = response["resultValue"]
        order_status    = result_value["status"]
        order_date      = result_value["orderDate"]
        orders_detail   = result_value["ordersDetail"]
        product_count   = len(orders_detail)
        cancelable_count = sum(
            1 for item in orders_detail if item.get("cancelStatus", "") == ""
        )

        # 주문상태가 정상(4101001)이고 전체 상품이 취소 이력 없는 경우만 취소 가능
        cancelable = (order_status == "4101001" and product_count == cancelable_count)

        _set_param(state, "cancelable", cancelable)
        _set_param(state, "order_info", {
            "order_date":    order_date,
            "product_count": product_count,
        })
        print(f"[{AGENT_NAME}] 주문 조회 성공 | order_id={order_id}, cancelable={cancelable}")

    except httpx.HTTPStatusError as e:
        # 조회 실패: order_id 초기화 → route_after_fetch에서 END, 다음 턴 ask_order_id 재라우팅
        print(f"[{AGENT_NAME}] 주문 조회 실패 | order_id={order_id}, error={e}")
        state.collected_parameters.get(AGENT_NAME, {}).pop("order_id", None)
        state.add_ai_message(
            "알려주신 주문번호로 주문을 조회할 수 없습니다.\n"
            "E로 시작하는 주문번호를 정확하게 다시 입력해주세요.\n\n"
            "뒤에 0001 또는 0002와 같은 시퀀스번호가 있다면 떼고 주문번호만 알려주세요\n"
        )

    return state


async def confirm_cancelable(state: GraphState) -> GraphState:
    """취소 가능 안내 및 진행 의사 확인 노드."""
    order_info    = _get_params(state).get("order_info", {})
    order_date    = order_info.get("order_date", "")
    product_count = order_info.get("product_count", 0)

    state.add_ai_message(
        f"주문 조회 결과, 고객님이 {order_date}에 구매하신 이심 {product_count}개 전체 취소신청이 가능합니다.\n\n"
        "📌 유의사항\n"
        "• 시락이를 통한 취소는 구매 상품 전체 취소만 가능합니다.\n"
        "• BC·CM 등 일부 상품은 즉시 취소승인 및 플랜 취소 처리됩니다.\n"
        "• 취소 신청 후 담당부서에서 설치 이력을 확인하며, 설치 이력이 있을 경우 취소환불이 거절된다는 점을 안내해주세요.\n\n"
        "구매 상품 전체 취소신청을 진행하시겠습니까?"
    )
    await set_waiting(state, result="취소 진행 의사 확인 요청")
    return state


async def handle_non_cancelable(state: GraphState) -> GraphState:
    """취소 불가 안내 노드. 이미 취소 요청/처리 중인 이심이 있는 경우."""
    order_info = _get_params(state).get("order_info", {})
    order_date = order_info.get("order_date", "")

    state.add_ai_message(
        f"{order_date}에 구매하신 이심 중 이미 취소 요청이 되었거나 처리 진행 중인 이심이 있어 "
        "취소 접수가 불가합니다.\n"
    )
    await set_cancelled(state, result="취소 불가 - 처리 진행 중인 이심 존재")
    state.current_agent = None
    return state


async def cancel_inquiry(state: GraphState) -> GraphState:
    """취소 진행 의사 없음 또는 업무 취소 노드."""
    state.add_ai_message(
        "취소신청 업무가 중단되었습니다.\n"
    )
    await set_cancelled(state, result="사용자 취소 의사 없음 - 업무 취소")
    state.current_agent = None
    return state


async def cancel_order(state: GraphState) -> GraphState:
    """이심 취소 API 호출 노드."""
    order_id = _get_params(state).get("order_id")

    try:
        cancel_payload = {
            "orderId":      order_id,
            "userId":       "0",
            "insuranceYN":  "N",
            "cancelReason": "5801007",
            "cancelMemo":   "시락이 에이전트를 통한 직원의 취소요청 처리",
        }
        cancel_response = await esim_client.call_esim_api_post(
            "https://wapi.wifidosirak.com/api/v1/order/cancelorder",
            json=cancel_payload,
        )
        print(f"[{AGENT_NAME}] 취소 API 결과: {cancel_response}")

        if cancel_response.get("resultCode") is True:
            _set_param(state, "cancel_success", True)
            state.add_ai_message(
                "이심 취소 신청이 완료되었습니다. ✅\n"
                "취소상태는 따로 확인해주세요!"
                "일부 상품은 즉시 주문취소 및 플랜취소 처리되며, 나머지는 담당자 확인 후 설치 이력이 없다면 취소가 승인되고 환불이 진행됩니다.\n"
            )
            await set_completed(state, result="이심 취소 신청 접수 완료")

        else:
            _set_param(state, "cancel_success", False)
            state.add_ai_message(
                "죄송합니다. 취소 접수 중 문제가 발생했습니다.\n"
                "AI팀에 알려주세요"
            )
            await set_cancelled(state, result="이심 취소 API 실패 - resultCode False")

    except Exception as e:
        print(f"[{AGENT_NAME}] cancel_order 오류: {e}")
        _set_param(state, "cancel_success", False)
        state.add_ai_message(
            "죄송합니다. 취소 처리 중 오류가 발생했습니다.\n"
            "AI팀에 알려주세요"
        )
        await set_cancelled(state, result=f"이심 취소 오류: {e}")

    state.current_agent = None
    return state