import asyncio
from langgraph.graph import StateGraph, START, END
from src.workflow.graph_infra.state import GraphState

def node1(state: GraphState):
    state.add_ai_message("First message")
    return state

workflow = StateGraph(GraphState)
workflow.add_node("n1", node1)
workflow.add_edge(START, "n1")
workflow.add_edge("n1", END)
app = workflow.compile()

async def main():
    initial = GraphState()
    res = await app.ainvoke(initial)
    print("MESSAGES COUNT:", len(res['messages']))
    print("LAST MESSAGE:", res['messages'][-1] if res['messages'] else None)

asyncio.run(main())
