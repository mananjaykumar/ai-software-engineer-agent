"""LangGraph cyclical state machine orchestrating autonomous issue resolution."""

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from agent_core.agent.nodes import AgentNodes
from agent_core.agent.state import AgentState, AgentStepStatus


def route_after_drift_check(state: AgentState) -> str:
    """Routes to END if branch has drifted; otherwise proceeds to diagnose."""
    if state.get("drift_detected", False):
        return END
    return "diagnose"


def route_after_evaluation(state: AgentState) -> str:
    """Routes to END if approved or failed; otherwise loops back to plan for self-healing."""
    status = state.get("status")
    if status in (AgentStepStatus.AWAITING_APPROVAL, AgentStepStatus.FAILED):
        return END
    return "plan"


def build_agent_graph(
    nodes: AgentNodes,
    checkpointer: BaseCheckpointSaver | None = None,
) -> CompiledStateGraph:
    """Constructs and compiles the cyclical autonomous repair state graph."""
    workflow = StateGraph(AgentState)

    # 1. Register Graph Nodes
    workflow.add_node("drift_check", nodes.drift_check_node)
    workflow.add_node("diagnose", nodes.diagnose_node)
    workflow.add_node("plan", nodes.plan_node)
    workflow.add_node("patch", nodes.patch_node)
    workflow.add_node("verify", nodes.verify_node)
    workflow.add_node("evaluate", nodes.evaluate_node)

    # 2. Define Deterministic and Conditional Edges
    workflow.add_edge(START, "drift_check")

    workflow.add_conditional_edges(
        "drift_check",
        route_after_drift_check,
        {
            "diagnose": "diagnose",
            END: END,
        },
    )

    workflow.add_edge("diagnose", "plan")
    workflow.add_edge("plan", "patch")
    workflow.add_edge("patch", "verify")
    workflow.add_edge("verify", "evaluate")

    workflow.add_conditional_edges(
        "evaluate",
        route_after_evaluation,
        {
            "plan": "plan",
            END: END,
        },
    )

    # 3. Compile Graph with optional Checkpointer
    return workflow.compile(checkpointer=checkpointer)
