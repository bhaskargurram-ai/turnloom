"""Turnloom's terminal interface."""

import argparse
import fnmatch
import json
import math
import os
import platform
import re
import shutil
import sys
import sysconfig
import time
from contextlib import nullcontext
from dataclasses import asdict
from importlib.util import find_spec
from pathlib import Path
from uuid import uuid4

from turnloom import __version__
from turnloom.agent import Config, SYSTEM_PROMPT, environment, load_history, project_instructions, run_agent
from turnloom.context import Observations
from turnloom.extensions import McpTools, project_skills, skills
from turnloom import providers, roster
from turnloom.tools import Workspace, unified


SESSION = re.compile("[a-f0-9]{32}")
HELP = ("/help  show these commands\n/diff  show files the last turn changed\n/undo  restore files the last turn edited\n"
        "/new   start a fresh conversation in this session\n/quit  leave the chat")


def instructions(role, extra, workspace=None):
    parts = [SYSTEM_PROMPT, "Older tool output may be folded into a preview with a reference. "
             "Use recall with the reference and an offset or a search string to read it again."]
    persona = roster.body(role, workspace)
    if persona:
        parts.append(persona)
    if extra:
        parts.append(extra.read_text(encoding="utf-8"))
    if workspace:
        parts.append(environment(workspace))
        parts.extend(text for text in (project_instructions(workspace), project_skills(workspace)) if text)
    return "\n\n".join(parts)


class Approver:
    """Ask the operator before commands, edits and MCP calls, honoring session-wide choices."""

    def __init__(self, yes=False, auto_edit=False, allow=()):
        self.yes = yes
        self.auto_edit = auto_edit
        self.allow = list(allow)
        self.always = set()

    def allowed(self, kind, detail):
        if self.yes or (kind == "edit" and self.auto_edit) or (kind, detail) in self.always:
            return True
        # A pattern never covers a command that chains, pipes, redirects or substitutes.
        simple = kind == "shell" and not re.search(r"[;&|<>`$()\n\r%^]", detail)
        return simple and any(fnmatch.fnmatchcase(detail, pattern) for pattern in self.allow)

    def __call__(self, kind, detail):
        if self.allowed(kind, detail):
            return True
        if not sys.stdin.isatty():
            return False
        shown = "".join(char if char in "\n\t" or char.isprintable() else repr(char)[1:-1] for char in detail)
        if kind == "edit":
            print(f"\n{shown}", file=sys.stderr)
            question = "Apply this edit? [y]es / [n]o / [a]lways allow edits: "
        else:
            print(f"\n$ {shown}" if kind == "shell" else f"\n{shown}", file=sys.stderr)
            question = "Run this? [y]es / [n]o / [a]lways allow this exact request: "
        print(question, end="", file=sys.stderr, flush=True)
        answer = sys.stdin.readline().strip().lower()
        if answer in ("a", "always"):
            if kind == "edit":
                self.auto_edit = True
            else:
                self.always.add((kind, detail))
            return True
        return answer in ("y", "yes")


def traces(workspace):
    folder = workspace.resolve() / ".turnloom"
    if not folder.is_dir() or folder.is_symlink():
        return []
    found = [path for path in folder.glob("*.jsonl") if SESSION.fullmatch(path.stem) and path.is_file()]
    return sorted(found, key=lambda path: path.stat().st_mtime, reverse=True)


def events(path):
    with path.open(encoding="utf-8") as source:
        for line in source:
            if line.endswith("\n"):
                try:
                    yield json.loads(line)
                except ValueError:
                    continue


def find_trace(workspace, wanted):
    available = traces(workspace)
    if wanted in (None, "", "last"):
        matches = available[:1]
    else:
        matches = [path for path in available if path.stem.startswith(wanted.lower())]
    if len(matches) != 1:
        raise ValueError("No saved session here. Run turnloom sessions to list them." if not matches
                         else f"Session prefix {wanted} is ambiguous.")
    return matches[0]


def list_sessions(workspace):
    rows = traces(workspace)[:30]
    if not rows:
        print("No saved sessions in this workspace.")
        return 0
    for path in rows:
        task, turns, status, model = "", 0, "running", ""
        for event in events(path):
            if event.get("type") == "start":
                task, model = task or event.get("task", ""), event.get("model", model)
            elif event.get("type") == "end":
                turns += 1
                status = event["result"].get("status", status)
        stamp = time.strftime("%Y-%m-%d %H:%M", time.localtime(path.stat().st_mtime))
        print(f"{path.stem[:8]}  {stamp}  {turns} turn{'s' * (turns != 1)}  {status:<12} {model}  {' '.join(task.split())[:70]}")
    return 0


def recorded_edits(path):
    """Edits made by the final turn of a saved session."""
    edits = []
    for event in events(path):
        if event.get("type") == "start":
            edits = []
        elif event.get("type") == "edit":
            edits.append({"path": event["path"], "before": event["before"], "after": event["after"], "turn": 0})
    return edits


def show_changes(space, turn=None):
    _, changed = space.pending(turn)
    if not changed:
        print("No file edits recorded for the last turn.")
    for item in changed:
        print(unified(item["before"] or "", item["after"], item["path"], 40000))
    return changed


def undo(space):
    outcome = space.undo()
    for name in outcome["restored"]:
        print(f"Restored {name}")
    for name in outcome["conflicts"]:
        print(f"Kept {name}: it changed after the agent's edit")
    if not outcome["restored"] and not outcome["conflicts"]:
        print("No file edits recorded for the last turn.")
    return int(bool(outcome["conflicts"]))


def replay(path):
    for event in events(path):
        kind = event.get("type")
        if kind == "start":
            print(f"\n{event['model']}  ·  {event['task']}")
        elif kind == "assistant":
            for call in event["message"].get("tool_calls") or []:
                print(f"  → {call['function']['name']} {call['function']['arguments']}")
        elif kind == "tool":
            print("  ←", json.dumps(event["result"], ensure_ascii=False)[:400])
        elif kind == "edit":
            print(f"  ✎ {event['path']}")
        elif kind == "end":
            receipt(event["result"])


def receipt(result, streamed=False, rates=False):
    if not streamed or not result["answer"]:
        print(result["answer"] or "\n".join(result["errors"]) or result["status"])
    usage = result["usage"]
    tokens = f"{usage['prompt_tokens']:,} input / {usage['completion_tokens']:,} output tokens"
    if usage.get("cached_tokens"):
        tokens += f" ({usage['cached_tokens']:,} cached)"
    if usage.get("estimated_calls"):
        tokens += " (estimated: the provider omitted usage)"
    parts = [result["status"], tokens, f"{result['folded_chars']:,} context characters folded"]
    changed = result.get("files_changed") or []
    if changed:
        parts.append(f"{len(changed)} file{'s' * (len(changed) != 1)} changed")
    if rates:
        parts.append(f"estimated ${result['estimated_usd']:.6f}")
    print("\n" + "  ·  ".join(parts))
    if result["answer"] and result["errors"]:
        print(result["errors"][-1])


def doctor(workspace):
    def line(label, value):
        print(f"{label:<22}{value}")
    line("Turnloom", __version__)
    line("Python", f"{platform.python_version()} on {platform.system()} {platform.release()}")
    try:
        record = providers.profile()
        custom = providers.custom_endpoint()
        if custom:
            line("Connection", f"custom endpoint {custom[0]} / {custom[1]}")
        elif record:
            variable = providers.PROVIDERS[record["provider"]][2]
            source = "not required" if not variable else "environment" if os.environ.get(variable) else \
                "credential store" if providers.secret(record["provider"], required=False) else "MISSING"
            line("Connection", f"{record['provider']} / {record['model']} (key: {source})")
        else:
            line("Connection", "none. Run turnloom connect")
    except (ValueError, OSError) as error:
        line("Connection", f"unreadable: {error}")
    try:
        line("Credential store", type(providers.vault()).__module__.rsplit(".", 1)[-1])
    except Exception:
        line("Credential store", "unavailable. Use provider environment variables")
    rg = shutil.which("rg", path=sysconfig.get_path("scripts")) or shutil.which("rg")
    line("ripgrep", rg or "not found. search_files uses the slower built-in scanner")
    extras = {"mcp": "MCP client and server", "acp": "Editor protocol (ACP)", "yaml": "YAML skill metadata",
              "litellm": "LiteLLM gateway", "provio_sdk": "Provio policy gate", "langgraph": "LangGraph team graph",
              "tiktoken": "Exact token counts", "webview": "Desktop window", "verifydoc": "VerifyDoc", "unwind": "Unwind ledger"}
    for module, label in extras.items():
        line(label, "installed" if find_spec(module) else "not installed")
    root = workspace.resolve()
    line("Workspace", root)
    line("Project instructions", next((name for name in ("AGENTS.md", "CLAUDE.md") if (root / name).is_file()), "none"))
    line("Policy", "provio.yaml" if (root / "provio.yaml").is_file() else "none")
    line("Saved sessions", len(traces(root)))
    return 0


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            # A redirected stream on Windows defaults to the ANSI code page, which cannot carry model output.
            if stream.isatty():
                stream.reconfigure(errors="replace")
            else:
                stream.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(prog="turnloom", description="A coding agent with recoverable context.")
    parser.add_argument("--version", action="version", version=__version__)
    commands = parser.add_subparsers(dest="action", required=True)
    providers.register(commands)
    listing = commands.add_parser("personas", help="List available specialists")
    listing.add_argument("--search", default="", help="Filter by name, division or description")
    listing.add_argument("--all", action="store_true", help="Include every division, not only engineering roles")
    commands.add_parser("replay", help="Inspect a saved session offline").add_argument("path", type=Path)
    commands.add_parser("skills", help="List skills from a local collection").add_argument("root", type=Path)
    probe = commands.add_parser("tools", help="Discover MCP tools without a model connection")
    probe.add_argument("config", type=Path)
    probe.add_argument("server")
    probe.add_argument("query", nargs="?", default="")
    serve = commands.add_parser("serve", help="Serve Turnloom to other agents over MCP stdio")
    serve.add_argument("--workspace", type=Path, default=Path.cwd())
    serve.add_argument("--allow-run", action="store_true", help="Let clients delegate read-only tasks to the connected model")
    serve.add_argument("--allow-edits", action="store_true", help="With --allow-run, also let delegated tasks edit files")
    serve.add_argument("--allow-shell", action="store_true", help="With --allow-run, also let delegated tasks run commands")
    editor = commands.add_parser("acp", help="Serve Turnloom to editors over the Agent Client Protocol on stdio")
    editor.add_argument("--workspace", type=Path, default=Path.cwd())
    editor.add_argument("--persona", default="builder", help="A persona id from turnloom personas")
    layer = commands.add_parser("proxy", help="Serve an OpenAI-compatible endpoint that folds context for another harness")
    layer.add_argument("--port", type=int, default=8787)
    layer.add_argument("--workspace", type=Path, default=Path.cwd(), help="Where the usage ledger is written")
    layer.add_argument("--keep", type=int, default=3, help="Newest tool results left unfolded")
    layer.add_argument("--no-fold", action="store_true", help="Meter only")
    layer.add_argument("--token", default="", help="Require this bearer token from clients")
    export = commands.add_parser("atif", help="Convert a session transcript to an ATIF trajectory")
    export.add_argument("transcript", type=Path)
    export.add_argument("output", type=Path)
    for name, text in (("sessions", "List saved sessions in a workspace"), ("doctor", "Check the local setup"),
                       ("studio", "Open the browser workbench"), ("undo", "Restore files edited by a session's last turn")):
        sub = commands.add_parser(name, help=text)
        sub.add_argument("--workspace", type=Path, default=Path.cwd())
        if name == "undo":
            sub.add_argument("session", nargs="?", help="Session ID prefix, default the most recent")
        if name == "studio":
            sub.add_argument("--browser", action="store_true", help="Open in the default browser instead of an app window")
            sub.add_argument("--no-open", action="store_true", help="Print the session URL and open nothing, for remote shells")
            sub.add_argument("--port", type=int, default=0, help="Loopback port for --browser and --no-open, default a free one")
    for name in ("run", "chat", "team"):
        sub = commands.add_parser(name, help={"run": "Run one task", "chat": "Start a chat", "team": "Plan, build and review in three stages"}[name])
        if name != "chat":
            sub.add_argument("task")
        sub.add_argument("--mcp", type=Path, help="Explicit trusted stdio server configuration")
        sub.add_argument("--skills-dir", type=Path)
        sub.add_argument("--skill", action="append", default=[], help="Load a named skill from --skills-dir")
        sub.add_argument("--workspace", type=Path, default=Path.cwd())
        sub.add_argument("--persona", default="builder", help="A persona id from turnloom personas")
        sub.add_argument("--persona-file", type=Path, help="Additional trusted Markdown instructions")
        sub.add_argument("--yes", action="store_true", help="Approve every command, edit and tool call")
        sub.add_argument("--no-gate", action="store_true", help="Skip the Provio policy gate even when the workspace has a provio.yaml")
        sub.add_argument("--auto-edit", action="store_true", help="Approve file edits; still ask before commands")
        sub.add_argument("--read-only", action="store_true", help="Explore and answer without edits or commands")
        sub.add_argument("--allow", action="append", default=[], metavar="PATTERN",
                         help="Approve simple commands matching a glob such as 'pytest*'. Repeatable")
        sub.add_argument("--budget", type=float, default=0, help="Estimated USD allowance per task")
        sub.add_argument("--input-rate", type=float, help="USD per million input tokens")
        sub.add_argument("--output-rate", type=float, help="USD per million output tokens")
        sub.add_argument("--context", type=int, help="Context window in tokens, default from the model catalog or 128000")
        sub.add_argument("--reply", type=int, default=4096)
        sub.add_argument("--deadline", type=int, default=1800)
        sub.add_argument("--max-steps", type=int, default=100, help="Model calls allowed per task, 0 for no limit")
        sub.add_argument("--effort")
        sub.add_argument("--no-fold", action="store_true")
        sub.add_argument("--no-stream", action="store_true", help="Print the answer when it is complete")
        sub.add_argument("--json", action="store_true", help="Print the final result as one JSON object")
        sub.add_argument("--events", action="store_true", help="Print each session event as a JSON line while it happens")
        if name != "team":
            sub.add_argument("--resume", nargs="?", const="last", metavar="SESSION",
                             help="Continue a saved session, default the most recent")
    args = parser.parse_args(argv)
    if args.action in {"providers", "connection", "disconnect", "connect", "models"}:
        try:
            return providers.command(args)
        except KeyboardInterrupt:
            parser.error("Cancelled.")
        except (OSError, ValueError, EOFError) as error:
            parser.error(str(error))
    if args.action == "serve":
        from turnloom.server import serve as serve_mcp
        try:
            serve_mcp(args.workspace, allow_run=args.allow_run, allow_edits=args.allow_edits, allow_shell=args.allow_shell)
        except ValueError as error:
            parser.error(str(error))
        return 0
    if args.action == "acp":
        from turnloom.acp import serve as serve_acp
        try:
            return serve_acp(args.workspace, args.persona)
        except ValueError as error:
            parser.error(str(error))
    if args.action == "studio":
        from turnloom.studio import launch
        return launch(args.workspace, args.port, "none" if args.no_open else "browser" if args.browser else "window")
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
            print(f"{row['id']:<46} {row['division']:<18} {row['description'][:90]}")
        print(f"\n{len(rows)} personas. reviewer and architect are short names for the code reviewer and "
              "software architect. Add --all for every division.", file=sys.stderr)
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
    if args.action == "replay":
        try:
            replay(args.path)
        except (OSError, KeyError) as error:
            parser.error(f"Cannot replay {args.path}: {error}")
        return 0
    if args.action in ("sessions", "doctor", "undo"):
        if not args.workspace.is_dir():
            parser.error("Provide a workspace directory.")
        if args.action == "sessions":
            return list_sessions(args.workspace)
        if args.action == "doctor":
            return doctor(args.workspace)
        try:
            space = Workspace(args.workspace)
            space.changes = recorded_edits(find_trace(args.workspace, args.session))
            return undo(space)
        except (ValueError, OSError) as error:
            parser.error(str(error))
    archive = Observations()
    if not args.workspace.is_dir() or args.deadline <= 0 or args.max_steps < 0:
        parser.error("Provide a workspace directory, a positive deadline and a step limit of zero or more.")
    try:
        roster.find(args.persona, args.workspace)
        if sys.stdin.isatty() and not providers.custom_endpoint() and not providers.profile():
            providers.command(parser.parse_args(["connect"]))
        connection = providers.resolve()
    except KeyboardInterrupt:
        parser.error("Cancelled.")
    except (ValueError, OSError, EOFError) as error:
        parser.error(str(error))
    if args.context is None:
        args.context = connection.context or 128000
    if not 0 < args.reply < args.context - 2000:
        parser.error("Choose a context window with room for the reply.")
    if args.input_rate is None:
        args.input_rate = connection.input_rate
    if args.output_rate is None:
        args.output_rate = connection.output_rate
    rates = (args.input_rate, args.output_rate, args.budget)
    free_route = connection.free and args.input_rate == args.output_rate == 0
    if any(value < 0 or not math.isfinite(value) for value in rates) or (args.budget and min(rates) <= 0 and not free_route):
        parser.error("A budget requires positive finite input and output rates.")
    if not (args.yes or args.read_only or args.auto_edit) and not sys.stdin.isatty():
        parser.error("Interactive approvals require a terminal. Pass --read-only, --auto-edit (edits only), "
                     "or --yes inside a trusted sandbox.")
    workspace = args.workspace.resolve()
    try:
        resumed = find_trace(workspace, args.resume) if getattr(args, "resume", None) else None
    except ValueError as error:
        parser.error(str(error))
    trace = resumed or workspace / ".turnloom" / (uuid4().hex + ".jsonl")
    approver = Approver(args.yes, args.auto_edit, args.allow)
    try:
        config = Config(connection.endpoint, connection.model, connection.key, workspace,
                        system_prompt=instructions(args.persona, args.persona_file, workspace), transcript=trace,
                        context_tokens=args.context, max_reply_tokens=args.reply, deadline_seconds=args.deadline,
                        reasoning_effort=args.effort, approve=approver, max_steps=args.max_steps,
                        fold_history=not args.no_fold, budget_usd=args.budget, read_only=args.read_only,
                        input_rate=args.input_rate, output_rate=args.output_rate, cache_rate=connection.cache_rate,
                        files=Workspace(workspace))
        if args.events:
            config.on_event = lambda event: print(json.dumps(event, ensure_ascii=False), flush=True)
        if args.skill and not args.skills_dir:
            parser.error("--skill requires --skills-dir")
        if args.skill:
            catalog = skills(args.skills_dir)
            for name in dict.fromkeys(args.skill):
                if name not in catalog:
                    parser.error(f"Unknown skill: {name}")
                _, source, body = catalog[name]
                config.system_prompt += f"\n\nSkill {name}. Resource directory: {source.parent}\n{body}"
        history = load_history(resumed, config.system_prompt) if resumed else []
        from turnloom import guard
        gate = nullcontext()
        if not args.no_gate and guard.policy_path(config.workspace).is_file():
            if not guard.installed():
                parser.error('This workspace has a provio.yaml but Provio is not installed. Run python -m pip install '
                             '"turnloom[guard]", or pass --no-gate to run without the policy.')
            # With --yes nobody is there to answer, so an ask verdict is refused. Otherwise the operator confirms as usual.
            gate = guard.Gate(config.workspace, approve=None if args.yes else approver, confirm=not args.yes, session=trace.stem)
            print("Provio policy gate on: provio.yaml", file=sys.stderr)
        with gate as config.gate, McpTools(args.mcp, config.workspace) if args.mcp else nullcontext() as connections:
            config.mcp = connections
            return converse(args, config, trace, archive, history)
    except (ValueError, OSError) as error:
        parser.error(str(error))


class Progress:
    """Show streamed text on stdout and tool activity on stderr."""

    def __init__(self, config, live):
        self.streamed = False
        self.open_line = False
        self.started = 0.0
        if live:
            config.on_text = self.text
        config.on_tool_start = self.start
        config.on_tool_event = self.finish

    def text(self, delta):
        sys.stdout.write(delta)
        sys.stdout.flush()
        self.streamed = True
        self.open_line = not delta.endswith("\n")

    def newline(self):
        if self.open_line:
            sys.stdout.write("\n")
            sys.stdout.flush()
            self.open_line = False

    def start(self, name, arguments):
        self.newline()
        self.streamed = False
        self.started = time.monotonic()
        try:
            params = json.loads(arguments)
        except ValueError:
            params = {}
        params = params if isinstance(params, dict) else {}
        label = params.get("command") or params.get("path") or params.get("query") or params.get("reference") or \
            " ".join(str(params[key]) for key in ("server", "name") if key in params)
        print(f"  → {name} {' '.join(str(label).split())[:110]}".rstrip(), file=sys.stderr, flush=True)

    def finish(self, name, arguments, result):
        outcome = json.loads(result)
        outcome = outcome if isinstance(outcome, dict) else {}
        if outcome.get("error"):
            note = "failed: " + " ".join(str(outcome["error"]).split())[:100]
        elif "exit_code" in outcome:
            note = f"exit {outcome['exit_code']}"
        elif "added" in outcome:
            note = f"+{outcome['added']} −{outcome['removed']}"
        else:
            note = "done"
        print(f"  ← {note} ({time.monotonic() - self.started:.1f}s)", file=sys.stderr, flush=True)


def converse(args, config, trace, archive, history):
    chat = args.action == "chat"
    quiet = args.json or args.events
    live = not quiet and not args.no_stream and args.action != "team"
    progress = Progress(config, live) if not quiet else None
    mode = "read-only" if args.read_only else "auto-approve" if args.yes else "ask before acting"
    print(f"Turnloom {__version__}  ·  {args.persona}  ·  {config.model}  ·  {mode}  ·  session {trace.stem[:8]}"
          + ("  ·  resumed" if history else ""), file=sys.stderr)
    if chat:
        print("Type a task, or /help for commands.", file=sys.stderr)
    while True:
        try:
            task = args.task if not chat else input("\nturnloom › ").strip()
        except (KeyboardInterrupt, EOFError):
            return 0
        if chat and task.startswith("/"):
            if task in ("/quit", "/exit"):
                return 0
            if task == "/undo":
                undo(config.files)
            elif task == "/diff":
                show_changes(config.files)
            elif task == "/new":
                history.clear()
                archive = Observations()
                print("Started a fresh conversation.")
            else:
                print(HELP)
            continue
        if not task.strip():
            if not chat:
                raise ValueError("Task cannot be empty")
            continue
        if args.action == "team":
            from turnloom.team import run_team
            result = run_team(task, config)
        else:
            result = run_agent(task, config, history, archive)
        if args.json:
            print(json.dumps(asdict(result), ensure_ascii=False))
        elif progress:
            progress.newline()
            receipt(asdict(result), progress.streamed, bool(args.input_rate or args.output_rate))
        if not chat:
            return 130 if result.status == "stopped" else int(result.status != "completed")
