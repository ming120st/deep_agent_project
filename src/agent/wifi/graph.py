from typing import Literal

from langgraph.graph import END, START, StateGraph

from src.workflow.graph_infra.state import GraphState
from src.agent.wifi.subagents.chat.wifi_line_change.graph import wifi_line_change_graph
from src.agent.wifi.subagents.chat.wifi_usage_inquiry.graph import wifi_usage_inquiry_graph


WIFI_AGENT_NODES = {
    "wifi_usage_inquiry": "wifi_usage_inquiry",
    "wifi_line_change": "wifi_line_change",
}


def route_wifi_domain(state: GraphState) -> Literal[
    "wifi_usage_inquiry",
    "wifi_line_change",
    "end",
]:
    agent_name = state.current_agent
    if not agent_name and state.team_input:
        agent_name = state.team_input.get("agent_id") or state.team_input.get("task")

    if agent_name in WIFI_AGENT_NODES:
        return agent_name
    return "end"


workflow = StateGraph(GraphState)
workflow.add_node("wifi_usage_inquiry", wifi_usage_inquiry_graph)
workflow.add_node("wifi_line_change", wifi_line_change_graph)

workflow.add_conditional_edges(
    START,
    route_wifi_domain,
    {
        "wifi_usage_inquiry": "wifi_usage_inquiry",
        "wifi_line_change": "wifi_line_change",
        "end": END,
    },
)
workflow.add_edge("wifi_usage_inquiry", END)
workflow.add_edge("wifi_line_change", END)

wifi_domain_graph = workflow.compile()
