import json
from langchain.agents.middleware import AgentMiddleware, ModelRequest, ModelResponse
from langchain_core.messages import SystemMessage

from core.database.redis import redis_get


class EsimOrderPromptMiddleware(AgentMiddleware):
    """Redis에 저장된 주문 상세 정보를 LLM 호출 직전 system_prompt에 동적으로 주입하는 미들웨어."""

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler,
    ) -> ModelResponse:
        try:
            order_detail = await redis_get("esim_replacement_agent", "order_detail")
        except Exception:
            order_detail = None

        if order_detail:
            order_info = order_detail.get("order_info", {})
            order_products = order_detail.get("order_products", [])

            order_info_text = json.dumps(order_info, ensure_ascii=False, indent=2)
            order_products_text = json.dumps(order_products, ensure_ascii=False, indent=2)

            prompt_injection = (
                "\n\n[현재 조회된 주문 상세 정보]\n"
                f"1. 주문자 정보:\n{order_info_text}\n\n"
                f"2. 주문 상품 목록:\n{order_products_text}\n\n"
                "위 주문 정보를 참고하여 재발급 대상 확인 및 후속 작업을 진행하세요."
                "이미 확보한 위 주문 상세 정보는 필요한 경우가 아니면 메인 에이전트에게 다시 물어보지 마세요."
            )

            current_system = request.system_message
            if current_system:
                new_content = str(current_system.content) + prompt_injection
            else:
                new_content = prompt_injection

            request = request.override(
                system_message=SystemMessage(content=new_content)
            )

        return await handler(request)
