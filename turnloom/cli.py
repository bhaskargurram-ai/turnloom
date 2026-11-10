"""Turnloom's terminal interface."""

import argparse
import json
import math
import os
import sys
from dataclasses import asdict
from importlib.resources import files
from pathlib import Path
from uuid import uuid4

from turnloom import __version__
from turnloom.agent import Config, SYSTEM_PROMPT, run_agent
from turnloom.context import Observations


SPECIALISTS = {
    "builder": None,
    "reviewer": "engineering-code-reviewer.md",
    "architect": "engineering-software-architect.md",
    "debugger": None,
}


def instructions(role, extra):
    parts = [SYSTEM_PROMPT, "Older tool output may be folded. Use recall with the reference and offset to retrieve it."]
    if SPECIALISTS[role]:
        raw = files("turnloom").joinpath("personas", SPECIALISTS[role]).read_text(encoding="utf-8")
        parts.append(raw.split("---", 2)[-1].strip())
    elif role == "debugger":
        parts.append("Reproduce the symptom, isolate its first failing boundary, then verify the fix against the reproduction.")
    if extra:
        parts.append(extra.read_text(encoding="utf-8"))
    return "\n\n".join(parts)


def approve(command):
    if not sys.stdin.isatty():
        return False
    print(f"\n$ {command}", file=sys.stderr)
    return input("Run this command? [y/N] ").strip().lower() == "y"


def replay(path):
    with path.open(encoding="utf-8") as source:
        for line in source:
            event = json.loads(line)
            if event["type"] == "start":
                print(f"\n{event['model']}  ·  {event['task']}")
            elif event["type"] == "assistant":
                for call in event["message"].get("tool_calls") or []:
                    print(f"  → {call['function']['name']} {call['function']['arguments']}")
            elif event["type"] == "tool":
                print("  ←", json.dumps(event["result"], ensure_ascii=False)[:400])
            elif event["type"] == "end":
                receipt(event["result"])


def receipt(result):
    print(result["answer"] or "\n".join(result["errors"]))
    usage = result["usage"]
    print(f"\n{result['status']}  ·  {usage['prompt_tokens']:,} input / {usage['completion_tokens']:,} output"
          f"  ·  {result['folded_chars']:,} context characters folded")


def main(argv=None):
    parser = argparse.ArgumentParser(prog="turnloom", description="A coding agent with recoverable context.")
    parser.add_argument("--version", action="version", version=__version__)
    commands = parser.add_subparsers(dest="action", required=True)
    commands.add_parser("personas", help="List available specialists")
    commands.add_parser("demo", help="Try context folding and recall offline")
    commands.add_parser("replay", help="Inspect a saved session offline").add_argument("path", type=Path)
    for name in ("run", "chat"):
        sub = commands.add_parser(name, help="Run one task" if name == "run" else "Start an interactive session")
        if name == "run":
            sub.add_argument("task")
        sub.add_argument("--workspace", type=Path, default=Path.cwd())
        sub.add_argument("--persona", choices=SPECIALISTS, default="builder")
        sub.add_argument("--persona-file", type=Path, help="Additional trusted Markdown instructions")
        sub.add_argument("--yes", action="store_true", help="Automatically approve shell commands")
        sub.add_argument("--budget", type=float, default=0, help="Estimated USD allowance per task")
        sub.add_argument("--input-rate", type=float, default=0, help="USD per million input tokens")
        sub.add_argument("--output-rate", type=float, default=0, help="USD per million output tokens")
        sub.add_argument("--context", type=int, default=128000)
        sub.add_argument("--reply", type=int, default=4096)
        sub.add_argument("--deadline", type=int, default=1800)
        sub.add_argument("--effort")
        sub.add_argument("--no-fold", action="store_true")
        sub.add_argument("--json", action="store_true", help="Print result as JSON")
    args = parser.parse_args(argv)
    if args.action == "personas":
        print("builder    Implement and test changes\ndebugger   Reproduce and isolate failures\n"
              "reviewer   Review correctness and security\narchitect  Design interfaces and boundaries")
        return 0
    if args.action == "replay":
        replay(args.path)
        return 0
    archive = Observations()
    if args.action == "demo":
        messages = [{"role": "tool", "content": "passed: sample test\n" * 100}]
        messages.extend({"role": "tool", "content": "recent"} for _ in range(3))
        print(f"Folded {archive.fold(messages):,} characters of sample output.")
        reference = json.loads(messages[0]["content"])["reference"]
        print("Recovered excerpt:", json.loads(archive.recall(json.dumps({"reference": reference, "length": 19})))["text"])
        return 0
    if not args.workspace.is_dir() or args.deadline <= 0 or not 0 < args.reply < args.context - 600:
        parser.error("Provide a workspace directory, positive deadline and context with room for the reply.")
    rates = (args.input_rate, args.output_rate, args.budget)
    if any(value < 0 or not math.isfinite(value) for value in rates) or (args.budget and min(rates) <= 0):
        parser.error("A budget requires positive finite input and output rates.")
    model = os.environ.get("HARNESS_MODEL")
    endpoint = os.environ.get("HARNESS_ENDPOINT")
    if not model or not endpoint:
        parser.error("Set HARNESS_MODEL and HARNESS_ENDPOINT to your model and Chat Completions base URL.")
    if not args.yes and not sys.stdin.isatty():
        parser.error("Interactive approvals require a terminal. For a trusted sandbox pass --yes.")
    trace = args.workspace.resolve() / ".turnloom" / (uuid4().hex + ".jsonl")
    config = Config(endpoint, model, os.environ.get("HARNESS_API_KEY", ""), args.workspace.resolve(),
                    system_prompt=instructions(args.persona, args.persona_file), transcript=trace,
                    context_tokens=args.context, max_reply_tokens=args.reply, deadline_seconds=args.deadline,
                    reasoning_effort=args.effort, approve=None if args.yes else approve,
                    fold_history=not args.no_fold, budget_usd=args.budget,
                    input_rate=args.input_rate, output_rate=args.output_rate)
    history = []
    print(f"Turnloom {__version__} · {args.persona} · {trace}", file=sys.stderr)
    while True:
        try:
            task = args.task if args.action == "run" else input("\nturnloom › ").strip()
        except (KeyboardInterrupt, EOFError):
            return 0
        if args.action == "chat" and task == "/quit":
            return 0
        if not task.strip():
            if args.action == "run":
                parser.error("Task cannot be empty")
            continue
        result = run_agent(task, config, history, archive)
        if args.json:
            print(json.dumps(asdict(result), ensure_ascii=False))
        else:
            receipt(asdict(result))
            if args.input_rate or args.output_rate:
                print(f"Estimated cost at configured rates: ${result.estimated_usd:.6f}")
        if args.action == "run" or result.status != "completed":
            return int(result.status != "completed")
