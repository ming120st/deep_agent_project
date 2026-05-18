from typing import Literal

from langgraph.graph import END, START, StateGraph

from src.workflow.graph_infra.state import GraphState


def route_finance_domain(state: GraphState) -> str:
    agent_name = state.current_agent
    if not agent_name and state.team_input:
        agent_name = state.team_input.get("agent_id") or state.team_input.get("task")
    return "end"


workflow = StateGraph(GraphState)

workflow.add_conditional_edges(
    START,
    route_finance_domain,
    {
        "end": END,
    },
)

finance_domain_graph = workflow.compile()
