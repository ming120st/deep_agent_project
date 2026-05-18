from typing import Literal

from langgraph.graph import END, START, StateGraph

from src.workflow.graph_infra.state import GraphState
from src.agent.analytics.subagents.chat.bigquery_agent.agent import bigquery_agent_node


ANALYTICS_AGENT_NODES = {
    "bigquery_agent": "bigquery_agent",
}


def route_analytics_domain(state: GraphState) -> Literal[
    "bigquery_agent",
    "end",
]:
    agent_name = state.current_agent
    if not agent_name and state.team_input:
        agent_name = state.team_input.get("agent_id") or state.team_input.get("task")

    if agent_name in ANALYTICS_AGENT_NODES:
        return agent_name
    return "end"


workflow = StateGraph(GraphState)
workflow.add_node("bigquery_agent", bigquery_agent_node)

workflow.add_conditional_edges(
    START,
    route_analytics_domain,
    {
        "bigquery_agent": "bigquery_agent",
        "end": END,
    },
)
workflow.add_edge("bigquery_agent", END)

analytics_domain_graph = workflow.compile()
