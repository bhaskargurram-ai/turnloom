"""Run stages in sequence, sharing one workspace, deadline and allowance.

Stages run through LangGraph when it is installed and through a plain loop when it is not.
The two paths behave the same; the graph exists so a team can be embedded in a larger
LangGraph application. https://github.com/langchain-ai/langgraph
"""

import time
from dataclasses import replace

from turnloom.agent import Result, run_agent, shorten

DEFAULT = [
    {"name": "plan", "read_only": True,
     "instruction": "Inspect the task and the code, then list the changes and checks needed. You cannot edit files or run commands."},
    {"name": "build", "read_only": False,
     "instruction": "Implement the task in the shared workspace and verify the result."},
    {"name": "review", "read_only": True,
     "instruction": "Read the files that changed and check them against the original requirements. Report findings and "
                    "what remains unverified. You cannot edit files or run commands."},
]


def normalize(stages):
    """Accept dictionaries or (name, instruction[, system_prompt]) tuples and return stage dictionaries."""
    rows = []
    for stage in stages or DEFAULT:
        if isinstance(stage, dict):
            rows.append({"name": stage["name"], "instruction": stage["instruction"], "prompt": stage.get("prompt"),
                         "read_only": stage.get("read_only") is True})
        else:
            rows.append({"name": stage[0], "instruction": stage[1], "prompt": stage[2] if len(stage) > 2 else None,
                         "read_only": False})
    if len({row["name"] for row in rows}) != len(rows) or not 1 <= len(rows) <= 8:
        raise ValueError("A team needs one to eight uniquely named stages.")
    return rows


def run_team(task, config, stages=None, on_stage=None, archive=None):
    """Run stages in order and stop at the first that does not complete.

    ``on_stage(name, status, result)`` is called when a stage starts (status "running", result None)
    and when it ends. A read-only stage is given no edit or shell tools at all.
    """
    rows = normalize(stages)
    started = time.monotonic()
    results, reports = [], []

    def execute(row):
        remaining = config.budget_usd - sum(item.estimated_usd for item in results)
        seconds = int(config.deadline_seconds - (time.monotonic() - started))
        if seconds < 1 or (config.budget_usd and remaining <= 0):
            return "timeout" if seconds < 1 else "budget_limit"
        current = replace(config, system_prompt=(row["prompt"] or config.system_prompt) + "\n\n" + row["instruction"],
                          deadline_seconds=seconds, budget_usd=max(0, remaining),
                          read_only=config.read_only or row["read_only"], continues=bool(results))
        prompt = task + ("\n\nPrior stage reports:\n" + "\n".join(reports) if reports else "")
        if on_stage:
            on_stage(row["name"], "running", None)
        result = run_agent(prompt, current, None, archive)
        results.append(result)
        reports.append(row["name"] + ": " + shorten(result.answer or " ".join(result.errors), 3000))
        if on_stage:
            on_stage(row["name"], result.status, result)
        return result.status

    status = graph_run(rows, execute)
    if status is None:
        status = "completed"
        for row in rows:
            status = execute(row)
            if status != "completed":
                break
    usage = {key: sum(item.usage.get(key, 0) for item in results)
             for key in ("prompt_tokens", "completion_tokens", "cached_tokens")}
    changed = list(dict.fromkeys(name for item in results for name in item.files_changed))
    return Result(status, "\n\n".join(reports), sum(item.steps for item in results),
                  round(time.monotonic() - started, 3), usage, [error for item in results for error in item.errors],
                  sum(item.estimated_usd for item in results), sum(item.folded_chars for item in results), changed)


def graph_run(rows, execute):
    """Run the stages as a LangGraph state graph. Returns None when LangGraph cannot be imported."""
    try:
        from typing import TypedDict

        from langgraph.graph import END, START, StateGraph
    except Exception:  # absent, or its native wheels are blocked by the operating system
        return None

    class TeamState(TypedDict):
        status: str

    graph = StateGraph(TeamState)
    for row in rows:
        graph.add_node(row["name"], lambda state, row=row: {"status": execute(row)})
    names = [row["name"] for row in rows]
    graph.add_edge(START, names[0])
    for current, following in zip(names, names[1:], strict=False):
        graph.add_conditional_edges(current, lambda state: "next" if state["status"] == "completed" else "stop",
                                    {"next": following, "stop": END})
    graph.add_edge(names[-1], END)
    return graph.compile().invoke({"status": "completed"})["status"]
