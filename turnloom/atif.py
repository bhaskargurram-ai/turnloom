"""Convert a Turnloom transcript to the Agent Trajectory Interchange Format (ATIF-v1.8).

Harbor and the trajectory scanners built on it read this file. Every model call becomes
one agent step carrying its tool calls, their paired results and the provider-reported
token usage. https://github.com/harbor-framework/harbor/blob/main/rfcs/0001-trajectory-format.md
"""

import json
from datetime import datetime, timezone
from pathlib import Path

from turnloom import __version__

SCHEMA = "ATIF-v1.8"


def stamp(event):
    return datetime.fromtimestamp(event.get("time") or 0, timezone.utc).isoformat(timespec="milliseconds")


def text(value):
    return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)


def convert(events, session_id=None, input_rate=0, output_rate=0):
    steps, totals, model = [], {"prompt": 0, "completion": 0, "cached": 0, "cost": 0.0}, None
    pending = {}

    def add(source, message, event, **fields):
        steps.append({"step_id": len(steps) + 1, "timestamp": stamp(event), "source": source, "message": message, **fields})
        return steps[-1]

    for event in events:
        kind = event.get("type")
        if kind == "start":
            model = event.get("model") or model
            if not steps and event.get("system_prompt"):
                add("system", event["system_prompt"], event)
            add("user", event.get("task") or "", event)
        elif kind == "assistant":
            message = event.get("message") or {}
            usage = event.get("usage") or {}
            details = usage.get("prompt_tokens_details") or {}
            cached = details.get("cached_tokens") if isinstance(details, dict) else 0
            metrics = {"prompt_tokens": int(usage.get("prompt_tokens") or 0),
                       "completion_tokens": int(usage.get("completion_tokens") or 0), "cached_tokens": int(cached or 0)}
            totals["prompt"] += metrics["prompt_tokens"]
            totals["completion"] += metrics["completion_tokens"]
            totals["cached"] += metrics["cached_tokens"]
            fields = {"model_name": model, "llm_call_count": 1, "metrics": metrics}
            reasoning = message.get("reasoning_content") or message.get("reasoning")
            if isinstance(reasoning, str) and reasoning:
                fields["reasoning_content"] = reasoning
            calls = []
            for call in message.get("tool_calls") or []:
                function = call.get("function") or {}
                try:
                    arguments = json.loads(function.get("arguments") or "{}")
                except ValueError:
                    arguments = {"raw": function.get("arguments")}
                calls.append({"tool_call_id": call.get("id") or f"call_{len(steps) + 1}_{len(calls)}",
                              "function_name": function.get("name") or "unknown",
                              "arguments": arguments if isinstance(arguments, dict) else {"value": arguments}})
            if calls:
                fields["tool_calls"] = calls
            step = add("agent", message.get("content") or "", event, **fields)
            pending = {call["tool_call_id"]: step for call in calls}
            totals["cost"] = event.get("estimated_usd") or totals["cost"]
        elif kind == "tool" and event.get("id") in pending:
            step = pending[event["id"]]
            step.setdefault("observation", {"results": []})["results"].append(
                {"source_call_id": event["id"], "content": text(event.get("result"))})
        elif kind == "context":
            add("system", "[COMPACTED HISTORY] " + (event.get("note") or ""), event)
    if not steps:
        raise ValueError("The transcript holds no steps.")
    trajectory = {"schema_version": SCHEMA, "agent": {"name": "turnloom", "version": __version__}, "steps": steps,
                  "final_metrics": {"total_prompt_tokens": totals["prompt"], "total_completion_tokens": totals["completion"],
                                    "total_cached_tokens": totals["cached"], "total_steps": len(steps)}}
    if model:
        trajectory["agent"]["model_name"] = model
    if session_id:
        trajectory["session_id"] = session_id
    if totals["cost"]:
        trajectory["final_metrics"]["total_cost_usd"] = round(totals["cost"], 6)
    return trajectory


def export(transcript, target, session_id=None):
    """Write the trajectory for a transcript. A partial final line from an interrupted run is skipped."""
    events = []
    with Path(transcript).open(encoding="utf-8") as source:
        for line in source:
            try:
                events.append(json.loads(line))
            except ValueError:
                break
    trajectory = convert(events, session_id or Path(transcript).stem)
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(target.suffix + ".tmp")
    temporary.write_text(json.dumps(trajectory, ensure_ascii=False, indent=1), encoding="utf-8")
    temporary.replace(target)
    return trajectory
