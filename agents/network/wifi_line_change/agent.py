from agents.network.wifi_line_change.graph import (
    build_wifi_line_change_graph,
)
from registry.agent_registry import (
    register_agent,
)


wifi_line_change_agent = (
    build_wifi_line_change_graph()
)

wifi_line_change_agent = register_agent(
    name="wifi_line_change",
    description=(
        "와이파이 단말기의 SIM/회선을 변경하는 "
        "네트워크 업무 Agent"
    ),
)(wifi_line_change_agent)