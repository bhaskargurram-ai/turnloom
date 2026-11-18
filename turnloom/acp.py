"""Serve Turnloom to editors over the Agent Client Protocol: one conversation per session, reported as it runs."""

import asyncio
import json
import re
import sys
import threading
from collections import defaultdict
from concurrent.futures import Future
from contextlib import ExitStack
from itertools import count
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import url2pathname
from uuid import uuid4

from turnloom import __version__, guard, providers, roster
from turnloom.agent import Config, Result, load_history, run_agent
from turnloom.cli import SESSION, events, instructions
from turnloom.context import Observations
from turnloom.extensions import McpTools
from turnloom.tools import Workspace

MODES = {"ask": ("Ask", "Ask before edits and commands"),
         "auto-edit": ("Auto-edit", "Apply edits without asking and ask before commands"),
         "read-only": ("Read-only", "Explore and answer without edits or commands")}
TOOLS = {"list_files": ("search", "List files {path} {glob}"), "read_file": ("read", "Read {path}"),
         "search_files": ("search", "Search for {query}"), "edit_file": ("edit", "Edit {path}"),
         "write_file": ("edit", "Write {path}"), "shell": ("execute", "{command}"), "recall": ("read", "Recall {reference}"),
         "mcp_search": ("search", "Search {server} tools"), "mcp_call": ("other", "{server}: {name}")}
GATED = ("edit_file", "write_file", "shell", "mcp_call")  # Announced as pending: these wait for approval.
ALWAYS = {"edit": "Always allow edits in this session", "shell": "Always allow this exact command",
          "mcp": "Always allow this exact tool call"}
STEPS = 100  # Model calls per turn, as in `turnloom chat`.
GRACE = 2.0  # Seconds a withdrawn turn gets to stop before its request is given up as cancelled.


def sdk():
    """Import the protocol SDK on first use, so Turnloom itself runs without the acp extra."""
    try:
        import acp
    except ImportError as error:
        raise ValueError("Install Turnloom with the acp extra: pip install 'turnloom[acp]'") from error
    return acp


def said(text, role="agent"):
    return {"sessionUpdate": role + "_message_chunk", "content": {"type": "text", "text": text}}


def block(text):
    """Tool output inside a code fence longer than any it contains, so Markdown rendering leaves it alone."""
    fence = "`" * max(3, 1 + max(map(len, re.findall("`+", text)), default=0))
    return {"type": "content", "content": {"type": "text", "text": f"{fence}\n{text}\n{fence}"}}


def diff(path, before, after):
    return {"type": "diff", "path": str(path), "oldText": before, "newText": after}


def place(uri, root):
    """A file URI inside the workspace becomes the relative path the file tools take. Anything else stays as sent."""
    parts = urlsplit(uri)
    if parts.scheme == "file":
        try:
            return Path(url2pathname(parts.path)).resolve().relative_to(root).as_posix()
        except (ValueError, OSError):
            pass
    return uri


def task_text(blocks, root):
    """Flatten a prompt into the task: text as written, links by location, attached files in delimited blocks."""
    inline, attached = [], []
    for item in blocks:
        resource = getattr(item, "resource", None)
        if item.type == "text":
            inline.append(item.text)
        elif item.type == "resource_link":
            inline.append(f"[{item.name}]({place(item.uri, root)})")
        elif item.type == "resource" and isinstance(getattr(resource, "text", None), str):
            source = place(resource.uri, root)
            inline.append(source)
            attached.append(f'<attachment source="{source}">\n{resource.text}\n</attachment>')
        else:
            raise sdk().RequestError.invalid_params(
                {"details": "Turnloom reads text and text resources. Images, audio and binary files are not supported."})
    spoken = " ".join(piece.strip() for piece in inline if piece.strip())
    return "\n\n".join(part for part in (spoken, *attached) if part)


def tool_servers(offered):
    """Sort the editor's MCP servers into those Turnloom's client can launch and the reasons it skips the rest."""
    usable, skipped = {}, []
    for server in offered or []:
        if not getattr(server, "command", None):
            skipped.append(f"{server.name}: only stdio servers are supported")
        else:
            name = re.sub(r"[^A-Za-z0-9_-]", "_", server.name)[:60] or "server"
            while name in usable:
                name += "_"
            usable[name] = {"command": server.command, "args": list(server.args),
                            "env": {item.name: item.value for item in server.env or []}}
    return usable, skipped


def located(files, name):
    """The absolute path the editor needs for a workspace file, or None when the name does not stay inside the workspace."""
    try:
        return str(files.resolve(name, exists=False))
    except ValueError:
        return None


def opened(call_id, name, arguments, files):
    """The tool_call fields that announce a tool: a title, a kind, and the file it touches. Also returns its arguments."""
    try:
        params = json.loads(arguments)
    except ValueError:
        params = None
    params = params if isinstance(params, dict) else {}
    kind, title = TOOLS.get(name, ("other", name))
    words = defaultdict(str, {key: str(value) for key, value in params.items() if isinstance(value, (str, int))})
    fields = {"toolCallId": call_id, "name": name, "kind": kind, "status": "pending" if name in GATED else "in_progress",
              "title": " ".join(title.format_map(words).split())[:120] or name}
    path = located(files, params.get("path")) if name in ("read_file", "edit_file", "write_file") else None
    if path:
        line = params.get("line")
        fields["locations"] = [{"path": path, "line": line} if type(line) is int and line > 0 else {"path": path}]
    if name == "shell" and words["command"]:
        fields["content"] = [block(words["command"])]
    return fields, params


def output(name, outcome):
    """The part of a tool result worth reading in the editor."""
    if name == "shell" and "stdout" in outcome:
        printed = [part.rstrip() for part in (outcome["stdout"], outcome.get("stderr") or "") if part.strip()]
        return "\n".join([*printed, str(outcome.get("error") or f"exit code {outcome.get('exit_code')}")])
    if "error" in outcome:
        return str(outcome["error"])
    for key in ("text", "entries", "paths", "matches"):
        if key in outcome:
            return outcome[key] if isinstance(outcome[key], str) else "\n".join(map(str, outcome[key]))
    return json.dumps(outcome, indent=2, ensure_ascii=False)


def closed(name, outcome, change, files):
    """The tool_call fields that report a result: its status, and the diff it applied or the output it produced."""
    outcome = outcome if isinstance(outcome, dict) else {"result": outcome}
    failed = "error" in outcome or outcome.get("exit_code") not in (None, 0) or outcome.get("isError") is True
    path = located(files, change["path"]) if change and not failed else None
    content = diff(path, change["before"], change["after"]) if path else block(output(name, outcome))
    return {"status": "failed" if failed else "completed", "content": [content]}


def guarded(callback, fallback=None):
    """Wrap an agent callback so a failure while reporting is logged and answered, never raised into the turn."""
    def call(*arguments):
        try:
            return callback(*arguments)
        except Exception as error:
            print(f"turnloom acp: {callback.__name__} failed: {error!r}", file=sys.stderr)
            return fallback
    return call


class Session:
    """One Turnloom conversation: its workspace, history and archive, and what the editor has allowed so far."""

    def __init__(self, session_id, root, persona, told):
        self.id = session_id
        self.files = Workspace(root)
        self.instructions = instructions(persona, None, self.files.root)
        self.transcript = self.files.root / ".turnloom" / (session_id + ".jsonl")
        self.history, self.archive = [], Observations()
        self.mode, self.always = "ask", set()
        self.mcp, self.notes, self.told = None, [], told
        self.calls = count(1)
        self.lock, self.turns = asyncio.Lock(), []


class Turn:
    """One prompt. The agent runs on a worker thread; what it does reaches the editor through one ordered queue."""

    def __init__(self, session, client):
        self.session, self.client = session, client
        self.loop = asyncio.get_running_loop()
        self.outbox = asyncio.Queue()
        self.stop = threading.Event()
        self.asking = None
        self.call = None
        self.streamed = False

    def post(self, item):
        """Queue an item for the editor from the worker thread. False once the event loop is gone."""
        try:
            self.loop.call_soon_threadsafe(self.outbox.put_nowait, item)
        except RuntimeError:
            self.stop.set()
            return False
        return True

    def text(self, delta):
        self.streamed = True
        self.post(said(delta))

    def thought(self, delta):
        self.post({"sessionUpdate": "agent_thought_chunk", "content": {"type": "text", "text": delta}})

    def started(self, name, arguments):
        session = self.session
        fields, params = opened(f"call-{next(session.calls)}", name, arguments, session.files)
        if fields["kind"] == "edit":
            try:
                fields["content"] = [diff(*session.files.plan(name, params)[:3])]
            except (KeyError, ValueError, TypeError, OSError):
                pass  # The tool itself tells the model what is wrong with the request.
        self.streamed = False
        self.call = fields, len(session.files.changes)
        self.post({"sessionUpdate": "tool_call", **fields})

    def finished(self, name, arguments, result):
        (fields, known), self.call = self.call, None
        changes = self.session.files.changes
        change = changes[-1] if len(changes) > known else None
        self.post({"sessionUpdate": "tool_call_update", "toolCallId": fields["toolCallId"],
                   **closed(name, json.loads(result), change, self.session.files)})

    def approve(self, kind, detail):
        """Decide an edit, command or MCP call on the worker thread, asking the editor unless the session already allows it."""
        session, fields = self.session, self.call[0]
        if self.stop.is_set() or (session.mode == "read-only" and kind != "mcp"):
            return False
        if not (kind == "edit" and session.mode == "auto-edit") and (kind, detail) not in session.always:
            answer = Future()
            options = [("allow", "Allow", "allow_once"), ("always", ALWAYS[kind], "allow_always"), ("reject", "Reject", "reject_once")]
            request = {"tool_call": {**fields, "content": fields.get("content") or [block(detail)]},
                       "options": [{"optionId": key, "name": label, "kind": hint} for key, label, hint in options]}
            if not self.post((request, answer)):
                return False
            choice = answer.result()
            if choice not in ("allow", "always"):
                return False
            if choice == "always" and kind == "edit":
                session.mode = "auto-edit"
                self.post({"sessionUpdate": "current_mode_update", "currentModeId": session.mode})
            elif choice == "always":
                session.always.add((kind, detail))
        self.post({"sessionUpdate": "tool_call_update", "toolCallId": fields["toolCallId"], "status": "in_progress"})
        return True

    def attempt(self, task):
        """Run the agent with the operator's connected model. Returns its Result, or the reason there is no model."""
        session = self.session
        try:
            link = providers.resolve()
        except (ValueError, OSError) as error:
            return f"Turnloom has no model to use. {error}\n\nRun `turnloom connect` in a terminal, then send your message again."
        root = session.files.root
        config = Config(link.endpoint, link.model, link.key, root, system_prompt=session.instructions,
                        transcript=session.transcript, files=session.files, mcp=session.mcp,
                        context_tokens=link.context or 128000, max_reply_tokens=4096, max_steps=STEPS,
                        input_rate=link.input_rate, output_rate=link.output_rate, cache_rate=link.cache_rate,
                        read_only=session.mode == "read-only",
                        stop_requested=self.stop.is_set, approve=guarded(self.approve, False), on_text=guarded(self.text),
                        on_thought=guarded(self.thought),
                        on_tool_start=guarded(self.started), on_tool_event=guarded(self.finished))
        if not guard.policy_path(root).is_file():
            return run_agent(task, config, session.history, session.archive)
        if not guard.installed():
            return ("This workspace has a provio.yaml but Provio is not installed, so its policy cannot be applied. "
                    "Run `pip install \"turnloom[guard]\"` and send your message again.")
        with guard.Gate(root, approve=config.approve, confirm=True, session=session.id) as config.gate:
            return run_agent(task, config, session.history, session.archive)

    def work(self, task):
        """The worker thread. Whatever happens it posts how the turn ended, so the turn never waits on a dead thread."""
        try:
            outcome = self.attempt(task)
        except Exception as error:
            outcome = f"Turnloom could not run this turn: {error!r}"
        self.post(outcome)

    async def send(self, update):
        try:
            await self.client.session_update(session_id=self.session.id, update=update)
        except ConnectionError:
            self.halt()
        except Exception as error:
            print(f"turnloom acp: dropped an update: {error}", file=sys.stderr)

    async def ask(self, request, answer):
        """Put a permission question to the editor. Only a chosen option is an answer: a cancelled, failed or
        withdrawn request leaves the worker with None, which it treats as a refusal."""
        choice = None
        try:
            if not self.stop.is_set():
                reply = await self.client.request_permission(session_id=self.session.id, **request)
                choice = getattr(reply.outcome, "option_id", None)
        except Exception as error:
            print(f"turnloom acp: permission request failed: {error}", file=sys.stderr)
        finally:
            if not answer.done():
                answer.set_result(choice)

    def halt(self):
        """End the turn: stop the agent and withdraw a question the editor has not answered."""
        self.stop.set()
        if self.asking and not self.asking.done():
            self.asking.cancel()

    async def run(self, task):
        """Run the turn to its end and return the protocol's stop reason. Turns in one session run one at a time."""
        session = self.session
        try:
            async with session.lock:
                if self.stop.is_set():
                    return "cancelled"
                news = [note for note in session.notes if note not in session.told]
                if news:
                    session.told.update(news)
                    await self.send(said("Turnloom did not connect these MCP servers from the editor:\n"
                                         + "".join(f"- {note}\n" for note in news) + "\n"))
                threading.Thread(target=self.work, args=(task,), daemon=True).start()
                while not isinstance(outcome := await self.outbox.get(), (Result, str)):
                    if isinstance(outcome, tuple):
                        self.asking = asyncio.ensure_future(self.ask(*outcome))
                    else:
                        await self.send(outcome)
                if self.call:
                    await self.send({"sessionUpdate": "tool_call_update", "toolCallId": self.call[0]["toolCallId"],
                                     "status": "failed"})
                reason, closing = "end_turn", [outcome]
                if isinstance(outcome, Result):
                    if outcome.status == "step_limit" or (outcome.status == "partial" and outcome.steps >= STEPS):
                        reason = "max_turn_requests"
                    elif outcome.truncated:
                        reason = "max_tokens"
                    closing = [outcome.answer] if outcome.answer and not self.streamed else []
                    if outcome.errors and outcome.status != "completed" and not self.stop.is_set():
                        closing.append(outcome.errors[-1] if outcome.answer else "\n".join(outcome.errors))
                if closing:
                    await self.send(said(("\n\n" if self.streamed else "") + "\n\n".join(closing)))
                return "cancelled" if self.stop.is_set() else reason
        finally:
            self.halt()
            session.turns.remove(self)


class Agent:
    """The protocol surface: each method answers one request or notification from the editor."""

    def __init__(self, base, persona):
        self.base, self.persona = base, persona
        self.sessions = {}
        self.servers = ExitStack()
        self.told = set()
        self.client = None

    def on_connect(self, client):
        self.client = client

    def session(self, session_id):
        if session_id not in self.sessions:
            raise sdk().RequestError(-32002, "Resource not found", {"sessionId": session_id})
        return self.sessions[session_id]

    def open(self, session_id, cwd, servers):
        """Create the conversation behind a session ID, rooted at the directory the editor names."""
        root = self.base / cwd
        if not root.is_dir():
            raise sdk().RequestError.invalid_params({"details": f"cwd is not an existing directory: {cwd}"})
        self.halt(session_id)
        session = self.sessions[session_id] = Session(session_id, root, self.persona, self.told)
        usable, session.notes = tool_servers(servers)
        if usable:
            try:
                session.mcp = self.servers.enter_context(McpTools(None, session.files.root, servers=usable))
            except ValueError as error:
                session.notes.append(str(error))
        return session

    def halt(self, session_id):
        session = self.sessions.get(session_id)
        for turn in list(session.turns) if session else ():
            turn.halt()

    def close(self):
        """Stop whatever still runs and shut down the MCP servers the sessions started."""
        for session_id in self.sessions:
            self.halt(session_id)
        self.servers.close()

    @staticmethod
    def modes(session):
        return {"current_mode_id": session.mode, "available_modes": [
            {"id": key, "name": name, "description": about} for key, (name, about) in MODES.items()]}

    async def initialize(self, **_):
        """Advertise what is implemented and nothing more. Turnloom speaks protocol version 1 whatever is proposed."""
        return sdk().InitializeResponse(
            protocol_version=1, agent_info={"name": "Turnloom", "version": __version__}, auth_methods=[],
            agent_capabilities={"load_session": True, "mcp_capabilities": {"http": False, "sse": False},
                                "prompt_capabilities": {"image": False, "audio": False, "embedded_context": True}})

    async def new_session(self, cwd, mcp_servers=None, **_):
        session = self.open(uuid4().hex, cwd, mcp_servers)
        return sdk().NewSessionResponse(session_id=session.id, modes=self.modes(session))

    async def load_session(self, cwd, session_id, mcp_servers=None, **_):
        """Continue a saved Turnloom session: rebuild its context, then replay it to the editor before answering."""
        path = self.base / cwd / ".turnloom" / (session_id + ".jsonl")
        if not SESSION.fullmatch(session_id) or path.parent.is_symlink() or not path.is_file():
            raise sdk().RequestError(-32002, "Resource not found", {"sessionId": session_id})
        session = self.open(session_id, cwd, mcp_servers)
        try:
            session.history = load_history(path, session.instructions)
            await self.replay(session, path)
        except (OSError, KeyError, TypeError) as error:
            del self.sessions[session_id]
            raise sdk().RequestError.internal_error({"details": f"The saved session cannot be read: {error!r}"})
        return sdk().LoadSessionResponse(modes=self.modes(session))

    async def replay(self, session, path):
        """Show the editor a saved conversation: each message, and each finished tool together with its result."""
        calls, edit, spoken = {}, None, None
        for event in events(path):
            kind, update = event.get("type"), None
            if kind == "start":
                update = said(event["task"], "user")
            elif kind == "assistant":
                spoken = event["message"].get("content")
                calls = {call["id"]: call["function"] for call in event["message"].get("tool_calls") or []}
                update = said(spoken) if spoken else None
            elif kind == "edit":
                edit = event
            elif kind == "tool" and event["id"] in calls:
                function = calls.pop(event["id"])
                fields, _ = opened(f"call-{next(session.calls)}", function["name"], function["arguments"], session.files)
                update = {"sessionUpdate": "tool_call", **fields, **closed(function["name"], event["result"], edit, session.files)}
                edit = None
            elif kind == "end" and event["result"].get("status") in ("partial", "step_limit"):
                # The same closing answer load_history restores to the model's context.
                answer = event["result"].get("answer")
                update = said(answer) if answer and answer != spoken else None
            if update:
                await self.client.session_update(session_id=session.id, update=update)

    async def set_session_mode(self, session_id, mode_id, **_):
        session = self.session(session_id)
        if mode_id not in MODES:
            raise sdk().RequestError.invalid_params({"details": f"Unknown mode: {mode_id}"})
        session.mode = mode_id

    async def prompt(self, session_id, prompt, **_):
        session = self.session(session_id)
        task = task_text(prompt, session.files.root)
        if not task:
            raise sdk().RequestError.invalid_params({"details": "The prompt has no text."})
        turn = Turn(session, self.client)
        session.turns.append(turn)
        running = asyncio.ensure_future(turn.run(task))
        try:
            reason = await asyncio.shield(running)
        except asyncio.CancelledError:
            # The editor withdrew the request or went away. Stop the agent and give it a moment to wind down,
            # so the answer can still be a proper cancelled turn. Past that the turn finishes on its own.
            turn.halt()
            await asyncio.wait({running}, timeout=GRACE)
            if not running.done():
                raise
            reason = running.result()
        return sdk().PromptResponse(stop_reason=reason)

    async def cancel(self, session_id, **_):
        self.halt(session_id)


def serve(default_workspace, persona="builder"):
    """Run the agent on stdin and stdout until the editor disconnects. Nothing but protocol frames goes to stdout."""
    acp = sdk()
    if not default_workspace.is_dir():
        raise ValueError(f"Workspace does not exist: {default_workspace}")
    roster.find(persona, default_workspace)
    agent = Agent(default_workspace.resolve(), persona)
    try:
        asyncio.run(acp.run_agent(agent))
    except KeyboardInterrupt:
        return 130
    finally:
        agent.close()
    return 0
