from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from .nodes import WorkflowNodes
from .state import WorkflowState


def build_graph(nodes: WorkflowNodes, checkpointer=None):
    graph = StateGraph(WorkflowState)
    graph.add_node("initial_research", nodes.initial_research)
    graph.add_node("initial_profile", nodes.initial_profile)
    graph.add_node("initial_review", nodes.initial_review)
    graph.add_node("initial_decision", nodes.initial_decision)
    graph.add_node("follow_research", nodes.follow_research)
    graph.add_node("follow_profile", nodes.follow_profile)
    graph.add_node("follow_review", nodes.follow_review)
    graph.add_node("promote", nodes.promote)
    graph.add_node("finalize", nodes.finalize)
    graph.add_edge(START, "initial_research")
    graph.add_edge("initial_research", "initial_profile")
    graph.add_edge("initial_profile", "initial_review")
    graph.add_edge("initial_review", "initial_decision")
    graph.add_conditional_edges(
        "initial_decision",
        lambda state: "follow_research" if state["follow_target"] != "none" else "finalize",
    )
    graph.add_edge("follow_research", "follow_profile")
    graph.add_edge("follow_profile", "follow_review")
    graph.add_edge("follow_review", "promote")
    graph.add_edge("promote", "finalize")
    graph.add_edge("finalize", END)
    return graph.compile(checkpointer=checkpointer)
