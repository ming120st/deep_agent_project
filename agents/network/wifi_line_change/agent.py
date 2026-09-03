from agents.network.wifi_line_change.graph import (
    build_wifi_line_change_graph,
)
from registry.agent_registry import (
    register_agent,
)


wifi_line_change_agent = (
    build_wifi_line_change_graph()
)


register_agent(
    name="wifi_line_change",
    description=(
        "와이파이 단말기의 SIM/회선을 변경하는 "
        "네트워크 업무 Agent"
    ),
    runnable=wifi_line_change_agent,
)