"""A sequential LangGraph workflow sharing one workspace, model and allowance."""

import time
from dataclasses import replace
from typing import TypedDict

from turnloom.agent import Result, run_agent, shorten


class TeamState(TypedDict):
    reports: list[str]
    status: str


def run_team(task, config):
    try:
        from langgraph.graph import END, START, StateGraph
    except ImportError as error:
        raise ValueError("Install Turnloom with the team extra.") from error
    started = time.monotonic()
    results = []
    roles = {
        "plan": "Inspect the task and identify the changes and checks needed. Leave files unchanged.",
        "build": "Implement the task in the shared workspace and verify the result.",
        "review": "Inspect the actual changes and check the original requirements. Report findings and verification.",
    }

    def stage(role):
        def execute(state):
            remaining = config.budget_usd - sum(item.estimated_usd for item in results)
            seconds = int(config.deadline_seconds - (time.monotonic() - started))
            if seconds < 1 or (config.budget_usd and remaining <= 0):
                return {"status": "timeout" if seconds < 1 else "budget_limit"}
            current = replace(config, system_prompt=config.system_prompt + "\n" + roles[role],
                              deadline_seconds=seconds, budget_usd=max(0, remaining))
            prompt = task + ("\n\nPrior stage reports:\n" + "\n".join(state["reports"]) if state["reports"] else "")
            result = run_agent(prompt, current)
            results.append(result)
            return {"reports": state["reports"] + [role + ": " + shorten(result.answer, 3000)],
                    "status": result.status}
        return execute

    graph = StateGraph(TeamState)
    for role in roles:
        graph.add_node(role, stage(role))
    graph.add_edge(START, "plan")
    def route(state):
        return "next" if state["status"] == "completed" else "stop"
    graph.add_conditional_edges("plan", route, {"next": "build", "stop": END})
    graph.add_conditional_edges("build", route, {"next": "review", "stop": END})
    graph.add_edge("review", END)
    state = graph.compile().invoke({"reports": [], "status": "completed"})
    usage = {key: sum(item.usage[key] for item in results) for key in ("prompt_tokens", "completion_tokens")}
    return Result(state["status"], "\n\n".join(state["reports"]), sum(item.steps for item in results),
                  time.monotonic() - started, usage, [error for item in results for error in item.errors],
                  sum(item.estimated_usd for item in results), sum(item.folded_chars for item in results))
