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
from agents.marketing.agent import(
    register_meta_ads_agent,
)

async def register_all_agents() -> None:
    await register_bigquery_agent()
    await register_notion_agent()
    await register_drive_agent()
    await register_esim_replacement_agent()
    await register_meta_ads_agent()