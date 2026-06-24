from typing import Literal

from langgraph.graph import END, START, StateGraph

from src.workflow.graph_infra.state import GraphState
from src.agent.esim.subagents.chat.esim_fault_replacement.graph import esim_fault_replacement_graph
from src.agent.esim.subagents.chat.esim_order_cancellation.graph import esim_order_cancellation_graph
from src.agent.esim.subagents.chat.esim_resend.graph import esim_resend_graph
from src.agent.esim.subagents.chat.esim_early_termination.graph import esim_early_termination_graph


ESIM_AGENT_NODES = {
    "esim_fault_replacement": "esim_fault_replacement",
    "esim_order_cancellation": "esim_order_cancellation",
    "esim_resend": "esim_resend",
    "esim_early_termination": "esim_early_termination",
}


def route_esim_domain(state: GraphState) -> Literal[
    "esim_fault_replacement",
    "esim_order_cancellation",
    "esim_resend",
    "esim_early_termination",
    "end",
]:
    agent_name = state.current_agent
    if not agent_name and state.team_input:
        agent_name = state.team_input.get("agent_id") or state.team_input.get("task")

    if agent_name in ESIM_AGENT_NODES:
        return agent_name
    return "end"


workflow = StateGraph(GraphState)
workflow.add_node("esim_fault_replacement", esim_fault_replacement_graph)
workflow.add_node("esim_order_cancellation", esim_order_cancellation_graph)
workflow.add_node("esim_resend", esim_resend_graph)
workflow.add_node("esim_early_termination", esim_early_termination_graph)

workflow.add_conditional_edges(
    START,
    route_esim_domain,
    {
        "esim_fault_replacement": "esim_fault_replacement",
        "esim_order_cancellation": "esim_order_cancellation",
        "esim_resend": "esim_resend",
        "esim_early_termination": "esim_early_termination",
        "end": END,
    },
)
workflow.add_edge("esim_fault_replacement", END)
workflow.add_edge("esim_order_cancellation", END)
workflow.add_edge("esim_resend", END)
workflow.add_edge("esim_early_termination", END)

esim_domain_graph = workflow.compile()
