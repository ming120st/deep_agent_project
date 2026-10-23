from agents.common.bigquery_agent.agent import (
    register_bigquery_agent,
)
from agents.common.notion_agent.agent import (
    register_notion_agent,
)
from agents.common.drive.agent import (
    register_drive_agent,
)
from agents.esim_replacement_agent.agent import (
    register_esim_replacement_agent,
)
from agents.marketing.agent import (
    register_meta_ads_agent,
)
from agents.common.search_agent.agent import (
    register_search_agent,
)
from agents.common.html_agent.agent import (
    register_html_agent,
)
from agents.market.agent import (
    register_market_agent,
)


AGENT_LOADERS = {
    "bigquery": register_bigquery_agent,
    "notion": register_notion_agent,
    "google_workspace": register_drive_agent,
    "esim_replacement": (
        register_esim_replacement_agent
    ),
    "meta_ads": register_meta_ads_agent,
    "web_search": register_search_agent,
    "html_agent":register_html_agent,
    "market" : register_market_agent,
}

async def register_all_agents() -> None:
    print(
        "[DEBUG] register_all_agents 호출됨",
        flush=True,
    )

    print(
        "[DEBUG] 등록 대상:",
        list(AGENT_LOADERS.keys()),
        flush=True,
    )

    for agent_name, loader in AGENT_LOADERS.items():
        print(
            f"[DEBUG] 등록 시작: {agent_name}",
            flush=True,
        )

        await loader()

        print(
            f"[DEBUG] 등록 완료: {agent_name}",
            flush=True,
        )


async def reload_agent(
    agent_name: str,
) -> None:
    """
    지정된 SubAgent만 다시 등록한다.

    Prompt Version 변경 감지 후
    최신 Prompt / Skill을 반영하기 위해 사용한다.
    """

    loader = AGENT_LOADERS.get(
        agent_name
    )

    if loader is None:
        raise ValueError(
            "Agent loader not found: "
            f"{agent_name}"
        )

    await loader()