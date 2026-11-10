"""Turnloom's terminal interface."""

import argparse
import json
import math
import os
import sys
from contextlib import nullcontext
from dataclasses import asdict
from pathlib import Path
from uuid import uuid4

from turnloom import __version__
from turnloom.agent import Config, SYSTEM_PROMPT, run_agent
from turnloom.context import Observations
from turnloom.extensions import McpTools, skills
from turnloom import providers, roster


def instructions(role, extra, workspace=None):
    parts = [SYSTEM_PROMPT, "Older tool output may be folded. Use recall with the reference and offset to retrieve it."]
    persona = roster.body(role, workspace)
    if persona:
        parts.append(persona)
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
    providers.register(commands)
    listing = commands.add_parser("personas", help="List available specialists")
    listing.add_argument("--search", default="", help="Filter by name, division or description")
    listing.add_argument("--all", action="store_true", help="Include every division, not only engineering roles")
    commands.add_parser("demo", help="Try context folding and recall offline")
    commands.add_parser("replay", help="Inspect a saved session offline").add_argument("path", type=Path)
    commands.add_parser("skills", help="List skills from a local collection").add_argument("root", type=Path)
    probe = commands.add_parser("tools", help="Discover MCP tools without a model connection")
    probe.add_argument("config", type=Path)
    probe.add_argument("server")
    probe.add_argument("query", nargs="?", default="")
    commands.add_parser("serve", help="Expose local run receipts over MCP stdio").add_argument("--workspace", type=Path, default=Path.cwd())
    layer = commands.add_parser("proxy", help="Serve an OpenAI-compatible endpoint that folds context for another harness")
    layer.add_argument("--port", type=int, default=8787)
    layer.add_argument("--workspace", type=Path, default=Path.cwd(), help="Where the usage ledger is written")
    layer.add_argument("--keep", type=int, default=3, help="Newest tool results left unfolded")
    layer.add_argument("--no-fold", action="store_true", help="Meter only")
    layer.add_argument("--token", default="", help="Require this bearer token from clients")
    export = commands.add_parser("atif", help="Convert a session transcript to an ATIF trajectory")
    export.add_argument("transcript", type=Path)
    export.add_argument("output", type=Path)
    for name in ("run", "chat", "team"):
        sub = commands.add_parser(name, help={"run": "Run one task", "chat": "Start a chat", "team": "Plan, build and review with LangGraph"}[name])
        if name != "chat":
            sub.add_argument("task")
        sub.add_argument("--mcp", type=Path, help="Explicit trusted stdio server configuration")
        sub.add_argument("--skills-dir", type=Path)
        sub.add_argument("--skill", action="append", default=[], help="Load a named skill from --skills-dir")
        sub.add_argument("--workspace", type=Path, default=Path.cwd())
        sub.add_argument("--persona", default="builder", help="A persona id from turnloom personas")
        sub.add_argument("--persona-file", type=Path, help="Additional trusted Markdown instructions")
        sub.add_argument("--yes", action="store_true", help="Automatically approve shell commands")
        sub.add_argument("--no-gate", action="store_true", help="Skip the Provio policy gate even when the workspace has a provio.yaml")
        sub.add_argument("--budget", type=float, default=0, help="Estimated USD allowance per task")
        sub.add_argument("--input-rate", type=float, help="USD per million input tokens")
        sub.add_argument("--output-rate", type=float, help="USD per million output tokens")
        sub.add_argument("--context", type=int, default=128000)
        sub.add_argument("--reply", type=int, default=4096)
        sub.add_argument("--deadline", type=int, default=1800)
        sub.add_argument("--effort")
        sub.add_argument("--no-fold", action="store_true")
        sub.add_argument("--json", action="store_true", help="Print result as JSON")
    args = parser.parse_args(argv)
    if args.action in {"providers", "connection", "disconnect", "connect", "models"}:
        try:
            return providers.command(args)
        except (OSError, ValueError, EOFError) as error:
            parser.error(str(error))
    if args.action == "serve":
        from turnloom.server import serve
        try:
            serve(args.workspace)
        except ValueError as error:
            parser.error(str(error))
        return 0
    if args.action == "skills":
        try:
            for name, (description, _, _) in skills(args.root).items():
                print(f"{name}  {description}")
        except (OSError, ValueError) as error:
            parser.error(str(error))
        return 0
    if args.action == "tools":
        try:
            with McpTools(args.config, Path.cwd()) as tools:
                result = tools.dispatch("mcp_search", json.dumps({"server": args.server, "query": args.query}), None)
                print(json.dumps(result, indent=2, ensure_ascii=False))
                return int("error" in result)
        except (ValueError, OSError) as error:
            parser.error(str(error))
    if args.action == "personas":
        query = args.search.casefold()
        rows = [row for row in roster.catalog(Path.cwd()) if query in json.dumps(row, ensure_ascii=False).casefold()
                and (args.all or query or row["division"] in ("turnloom", "custom", "engineering", "testing", "security"))]
        for row in rows:
            print(f"{row['id']:<46} {row['division']:<18} {row['description'][:90]}".encode("ascii", "replace").decode())
        print(f"\n{len(rows)} personas. reviewer and architect are short names for the code reviewer and "
              "software architect. Add --all for every division.", file=sys.stderr)
        return 0
    if args.action == "replay":
        replay(args.path)
        return 0
    if args.action == "atif":
        from turnloom import atif
        try:
            trajectory = atif.export(args.transcript, args.output)
        except (OSError, ValueError) as error:
            parser.error(str(error))
        print(f"Wrote {len(trajectory['steps'])} steps to {args.output}")
        return 0
    if args.action == "proxy":
        from turnloom import proxy
        try:
            return proxy.serve(providers.resolve(), args.workspace.resolve(), args.port, max(0, args.keep), not args.no_fold, args.token)
        except (ValueError, OSError) as error:
            parser.error(str(error))
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
    try:
        roster.find(args.persona, args.workspace)
        if sys.stdin.isatty() and not any(os.environ.get(key) for key in ("HARNESS_MODEL", "HARNESS_ENDPOINT")) and not providers.profile():
            providers.command(parser.parse_args(["connect"]))
        connection = providers.resolve()
    except (ValueError, OSError, EOFError) as error:
        parser.error(str(error))
    if args.input_rate is None:
        args.input_rate = connection.input_rate
    if args.output_rate is None:
        args.output_rate = connection.output_rate
    rates = (args.input_rate, args.output_rate, args.budget)
    free_route = connection.free and args.input_rate == args.output_rate == 0
    if any(value < 0 or not math.isfinite(value) for value in rates) or (args.budget and min(rates) <= 0 and not free_route):
        parser.error("A budget requires positive finite input and output rates.")
    if not args.yes and not sys.stdin.isatty():
        parser.error("Interactive approvals require a terminal. For a trusted sandbox pass --yes.")
    trace = args.workspace.resolve() / ".turnloom" / (uuid4().hex + ".jsonl")
    config = Config(connection.endpoint, connection.model, connection.key, args.workspace.resolve(),
                    system_prompt=instructions(args.persona, args.persona_file, args.workspace), transcript=trace,
                    cache_rate=connection.cache_rate,
                    context_tokens=args.context, max_reply_tokens=args.reply, deadline_seconds=args.deadline,
                    reasoning_effort=args.effort, approve=None if args.yes else approve,
                    fold_history=not args.no_fold, budget_usd=args.budget,
                    input_rate=args.input_rate, output_rate=args.output_rate)
    try:
        if args.skill and not args.skills_dir:
            parser.error("--skill requires --skills-dir")
        if args.skill:
            catalog = skills(args.skills_dir)
            for name in dict.fromkeys(args.skill):
                if name not in catalog:
                    parser.error(f"Unknown skill: {name}")
                _, source, body = catalog[name]
                config.system_prompt += f"\n\nSkill {name}. Resource directory: {source.parent}\n{body}"
        from turnloom import guard
        gate = nullcontext()
        if not args.no_gate and guard.policy_path(config.workspace).is_file():
            if not guard.installed():
                parser.error('This workspace has a provio.yaml but Provio is not installed. Run python -m pip install '
                             '"turnloom[guard]", or pass --no-gate to run without the policy.')
            # With --yes nobody is there to answer, so an ask verdict is refused. Otherwise the operator confirms every call.
            gate = guard.Gate(config.workspace, approve=None if args.yes else approve, confirm=not args.yes, session=trace.stem)
            print("Provio policy gate on: provio.yaml", file=sys.stderr)
        with gate as config.gate, McpTools(args.mcp, config.workspace) if args.mcp else nullcontext() as connections:
            config.mcp = connections
            return converse(args, config, trace, archive)
    except (ValueError, OSError) as error:
        parser.error(str(error))


def converse(args, config, trace, archive):
    history = []
    print(f"Turnloom {__version__} · {args.persona} · {trace}", file=sys.stderr)
    while True:
        try:
            task = args.task if args.action != "chat" else input("\nturnloom › ").strip()
        except (KeyboardInterrupt, EOFError):
            return 0
        if args.action == "chat" and task == "/quit":
            return 0
        if not task.strip():
            if args.action != "chat":
                raise ValueError("Task cannot be empty")
            continue
        if args.action == "team":
            from turnloom.team import run_team
            result = run_team(task, config)
        else:
            result = run_agent(task, config, history, archive)
        if args.json:
            print(json.dumps(asdict(result), ensure_ascii=False))
        else:
            receipt(asdict(result))
            if args.input_rate or args.output_rate:
                print(f"Estimated cost at configured rates: ${result.estimated_usd:.6f}")
        if args.action != "chat" or result.status != "completed":
            return int(result.status != "completed")
