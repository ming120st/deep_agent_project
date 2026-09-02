# 파라미터 추출 함수

from pydantic import BaseModel
from langchain_core.messages import (
    SystemMessage,
    HumanMessage,
)
from core.llm.model_factory import LLM_LIGHT
from core.prompt.dependencies import prompt_service
from core.tracing.logger import get_logger

logger = get_logger(
    "wifi_line_change.parameter_extractor"
)


class WifiLineChangeParameters(BaseModel):
    device_number: str | None = None
    country_code: str | None = None
    expiry_date: str | None = None


async def extract_parameters(
    user_input: str,
    pending_parameter: str | None = None,
) -> WifiLineChangeParameters:

    system_prompt = await prompt_service.render(
        "wifi_line_change.parameter_extractor.system",
        pending_parameter=pending_parameter or "없음",
    )

    llm = LLM_LIGHT.with_structured_output(
        WifiLineChangeParameters,
        include_raw=True,
    )

    result = await llm.ainvoke([
        SystemMessage(
            content=system_prompt,
        ),
        HumanMessage(
            content=user_input,
        ),
    ])

    raw_message = result["raw"]
    parsed = result["parsed"]

    usage = raw_message.usage_metadata or {}

    logger.info(
         "parameters=%s input_tokens=%s output_tokens=%s total_tokens=%s",
        parsed.model_dump(),
        usage.get("input_tokens", 0),
        usage.get("output_tokens", 0),
        usage.get("total_tokens", 0),
    )

    return parsed