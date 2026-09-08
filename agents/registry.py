from agents.common.bigquery_agent.agent import (
    register_bigquery_agent,
)
from agents.common.notion_agent.agent import (
    register_notion_agent,
)
from agents.common.drive.agent import (
    register_drive_agent,
)


def register_all_agents() -> None:
    register_bigquery_agent()
    register_notion_agent()
    register_drive_agent()