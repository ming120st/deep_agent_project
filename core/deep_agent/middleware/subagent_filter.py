from __future__ import annotations

from typing import Any

from langchain.agents.middleware import (
    AgentMiddleware,
    ModelRequest,
    ModelResponse,
)
from langchain_core.messages import (
    SystemMessage,
    ToolMessage,
)

from core.deep_agent.state import (
    MainDeepAgentState,
)
from core.tracing.logger import get_logger
from registry.agent_search import (
    search_subagents,
)

logger = get_logger("subagent_filter")

class SubAgentFilterMiddleware(AgentMiddleware):
    state_schema = MainDeepAgentState

    async def abefore_agent(
        self,
        state,
        runtime,
    ) -> dict[str, Any] | None:

        query = state.get("current_input")

        if not query:
            return None

        candidates = await search_subagents(
            query=query,
            top_k=5,
        )

        logger.info(
            "[SUBAGENT_FILTER] query=%s candidates=%s",
            query,
            [
                item["name"]
                for item in candidates
            ],
        )

        return {
            "subagent_candidates": candidates,
        }

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler,
    ) -> ModelResponse:
        logger.info(
            "[MODEL_MESSAGES] %s",
            [
                {
                    "type": type(m).__name__,
                    "content_length": len(str(m.content)),
                }
                for m in request.messages
            ],
        )
        logger.info(
            "[MODEL_MESSAGE_DETAIL] %s",
            [
                {
                    "type": type(message).__name__,
                    "content": getattr(
                        message,
                        "content",
                        None,
                    ),
                    "tool_calls": getattr(
                        message,
                        "tool_calls",
                        None,
                    ),
                }
                for message in request.messages
            ],
        )
        logger.info(
            "[MODEL_TOOLS] count=%s names=%s",
            len(request.tools or []),
            [
                getattr(tool, "name", str(tool))
                for tool in (request.tools or [])
            ],
        )
    
        candidates = request.state.get(
            "subagent_candidates",
            [],
        )
    
        logger.info(
            "[SUBAGENT_PROMPT_INJECT] candidates=%s",
            [
                item["name"]
                for item in candidates
            ],
        )
    
        # 후보가 없으면 원본 request 그대로 호출
        if not candidates:
            response = await handler(
                request.override(tools=[])
            )
    
        else:
            available_agents = "\n".join(
                (
                    f"- {item['name']}: "
                    f"{item['description']}"
                )
                for item in candidates
            )
    
            filter_prompt = (
                "\n\n"
                "현재 요청에서 사용할 수 있는 "
                "SubAgent 후보:\n"
                f"{available_agents}\n\n"
                "task 도구를 사용할 경우 "
                "subagent_type은 반드시 위 후보의 "
                "name 중 하나만 사용하세요."
            )
    
            system_message = request.system_message
    
            if system_message:
                content = (
                    str(system_message.content)
                    + filter_prompt
                )
            else:
                content = filter_prompt
    
            tools = []
    
            for tool in request.tools or []:
                if getattr(tool, "name", None) != "task":
                    continue
                
                task_description = (
                    "현재 요청에 적합한 SubAgent에게 "
                    "업무를 위임합니다.\n\n"
                    "사용 가능한 SubAgent:\n"
                    f"{available_agents}\n\n"
                    "subagent_type에는 반드시 위 목록의 "
                    "name을 사용하세요."
                )
    
                tools.append(
                    tool.model_copy(
                        update={
                            "description": task_description,
                        }
                    )
                )
    
            logger.info(
                "[SUBAGENT_MODEL_CONTEXT] task_available=%s agents=%s",
                any(
                    getattr(tool, "name", None) == "task"
                    for tool in tools
                ),
                [
                    item["name"]
                    for item in candidates
                ],
            )
    
            new_request = request.override(
                system_message=SystemMessage(
                    content=content,
                ),
                tools=tools,
            )
    
            response = await handler(new_request)
    
        # 후보 유무와 상관없이 항상 실행
        for message in response.result:
            usage = getattr(
                message,
                "usage_metadata",
                None,
            )
    
            if usage:
                logger.info(
                    "[TOKEN_USAGE_RAW] %s",
                    usage,
                )
    
                logger.info(
                    "[TOKEN_USAGE] input=%s output=%s total=%s",
                    usage.get("input_tokens"),
                    usage.get("output_tokens"),
                    usage.get("total_tokens"),
                )
    
        return response
