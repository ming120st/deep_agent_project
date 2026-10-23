from deepagents import (
    create_deep_agent as create_official_deep_agent,
)
from langchain.agents.middleware import (
    TodoListMiddleware,
)
from deepagents.backends.filesystem import (
    FilesystemBackend,
)
from langgraph.checkpoint.base import (
    BaseCheckpointSaver,
)
from core.llm.model_service import (
    model_service,
)
from core.prompt.prompt_service import (
    prompt_service,
)
from core.deep_agent.state import (
    MainDeepAgentState,
)
from core.tracing.logger import (
    get_logger,
)

from registry.agent_registry import (
    get_subagents,
)

from tools.network.wifi_line_change import (
    submit_wifi_line_change,
)
from tools.network.wifi_usage_inquiry import (
    inquire_wifi_usage,
)
from tools.common.mail_tools import (
    send_email,
)
from tools.common.planning.skill import (
    get_planning_skills,
    get_planning_skill,
)
from tools.common.intranet_publish.tool import (
    publish_saved_intranet_post,
    check_intranet_post_status,
)
import os


logger = get_logger(
    "deep_agent"
)


async def create_deep_agent(
    checkpointer: BaseCheckpointSaver,
):
    system_prompt = await prompt_service.render(
        "deep_agent.main.system",
    )
    system_prompt += (
        "\n\n브라우저·마켓 관리자 로그인 요청은 market 서브에이전트에 위임하세요. "
        "market은 설정된 서버 자격증명을 로그인 도구 내부에서 사용합니다. "
        "위임 전에 사용자에게 ID·비밀번호를 요청하거나 직접 환경변수를 읽지 마세요. "
        "자격증명 누락은 도구가 credentials_required를 반환한 경우에만 안내하며, "
        "그때도 채팅으로 비밀번호를 받지 말고 서버 설정을 안내하세요. "
        "인증 판정 설정 누락은 계정정보 누락과 구분하세요."
    )

    subagents = await get_subagents()

    main_model = await model_service.get_model(
        "llm.light"
    )

    return create_official_deep_agent(
        model=main_model,
        subagents=subagents,
        middleware=[
            TodoListMiddleware(),
        ],
        tools=[
            submit_wifi_line_change,
            inquire_wifi_usage,
            send_email,

            # 등록된 Planning Skill 목록 조회
            get_planning_skills,

            # 특정 Planning Skill 본문 조회
            get_planning_skill,
            
            # MongoDB에 저장된 HTML 게시
            publish_saved_intranet_post,

            # 인트라넷 게시 상태 확인
            check_intranet_post_status,
        ],
        system_prompt=system_prompt,
        state_schema=MainDeepAgentState,
        checkpointer=checkpointer,
        backend=FilesystemBackend(
            root_dir=os.environ[
                "DEEP_AGENT_ROOT_DIR"
            ],
        ),
        name="deep_agent",
    )
