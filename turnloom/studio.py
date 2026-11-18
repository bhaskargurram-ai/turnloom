"""Loopback workbench for one operator and one workspace."""

import argparse
import hashlib
import json
import os
import secrets
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.resources import files
from importlib.util import find_spec
from pathlib import Path
from uuid import uuid4

from turnloom import __version__, gateway, guard, meter, providers, roster
from turnloom.agent import Config, load_history, noted, run_agent, trace_folder
from turnloom.cli import events, instructions, traces
from turnloom.context import Observations
from turnloom.extensions import McpTools
from turnloom.tools import IGNORE, Workspace, canonical, find_program, unified, uses_crlf, write_atomic


LIMIT = 512 * 1024
STEPS, TOOLS = 40, 80
IDLE_SECONDS = 15 * 60
KINDS = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8", ".css": "text/css; charset=utf-8",
         ".svg": "image/svg+xml", ".png": "image/png", ".ico": "image/x-icon", ".txt": "text/plain; charset=utf-8",
         ".woff2": "font/woff2"}
REVIEW = ("\n\nFor a broad review, sample the most relevant files, report concrete findings with file and line evidence, "
          "and state what you covered.")
DEFAULTS = {"budget": 0.0, "fold": True, "keep": 3, "reply": 8192, "effort": "", "mode": "solo", "auto_approve": False,
            "auto_edit": False, "cache": True, "servers": [], "trusted": {}, "gate": "confirm",
            "team": [{"name": "plan", "persona": "engineering-software-architect", "read_only": True,
                      "instruction": "Inspect the task and the code, then list the changes and checks needed."},
                     {"name": "build", "persona": "builder", "read_only": False,
                      "instruction": "Implement the task in the shared workspace and verify the result."},
                     {"name": "review", "persona": "engineering-code-reviewer", "read_only": True,
                      "instruction": "Read the files that changed and check them against the original requirements. "
                                     "Report findings and what remains unverified."}]}
SCHEMAS = {
    "Invoice": {"type": "object", "properties": {
        "invoice_number": {"type": "string"}, "invoice_date": {"type": "string"}, "vendor": {"type": "string"},
        "total": {"type": "number", "x-scoring": "numeric"}, "currency": {"type": "string"}}},
    "Receipt": {"type": "object", "properties": {
        "merchant": {"type": "string"}, "date": {"type": "string"},
        "total": {"type": "number", "x-scoring": "numeric"}, "tax": {"type": "number", "x-scoring": "numeric"}}},
    "Contract": {"type": "object", "properties": {
        "parties": {"type": "string"}, "effective_date": {"type": "string"}, "term": {"type": "string"},
        "governing_law": {"type": "string"}}},
}


def worker(module, *arguments):
    """The command that runs one of Turnloom's helper processes, from source or from the packaged app."""
    if getattr(sys, "frozen", False):
        helper = Path(sys.executable).with_name("turnloom-worker.exe" if os.name == "nt" else "turnloom-worker")
        return {"command": str(helper), "args": ["--internal", module, *arguments]}
    python = Path(sys.executable).with_name("python.exe") if os.name == "nt" else Path(sys.executable)
    return {"command": str(python), "args": ["-I", "-m", module, *arguments]}


def companions_installed():
    return bool(find_spec("verifydoc") and find_spec("unwind") and find_spec("mcp"))


def through_unwind(ledger, spec=None):
    """Wrap an MCP server command in Unwind's reversibility proxy. No spec means the bundled VerifyDoc server."""
    upstream = [spec["command"], *spec.get("args", [])] if spec else []
    wrapped = worker("turnloom.unwind_stdio", str(ledger), *upstream)
    for key in ("pass_env", "env"):
        if spec and spec.get(key):
            wrapped[key] = spec[key]
    return wrapped


def digest(spec):
    """A fingerprint of one server definition, so an enabled server stays enabled only while it is unchanged."""
    return hashlib.sha256(json.dumps(spec, sort_keys=True).encode("utf-8")).hexdigest()[:24]


def settings_path(root):
    """Where Studio keeps one workspace's settings: in the operator's own configuration folder.

    They decide what runs without being asked, so they must never be read from the workspace,
    where a cloned repository could supply them.
    """
    name = hashlib.sha256(str(root).encode("utf-8")).hexdigest()[:20] + ".json"
    return providers.profile_path().parent / "workspaces" / name


def companion_tools(workspace):
    if not companions_installed():
        raise ValueError("Install turnloom[documents] and restart to enable Unwind and VerifyDoc.")
    ledger = trace_folder(workspace / ".turnloom") / "unwind.db"
    return McpTools(None, workspace, servers={"verifydoc": through_unwind(ledger)}, startup_timeout=90)


def label_for(name, arguments):
    try:
        params = json.loads(arguments)
    except ValueError:
        params = {}
    params = params if isinstance(params, dict) else {}
    if params.get("command"):
        return str(params["command"])
    detail = " ".join(str(params[key]) for key in ("path", "glob", "query", "reference", "server", "name") if params.get(key))
    return (name + " " + detail).strip()


def readable(outcome, limit=8000):
    """Turn a tool result into text an operator can read, instead of one line of JSON."""
    if not isinstance(outcome, dict):
        text = json.dumps(outcome, ensure_ascii=False, indent=2)
    elif "stdout" in outcome or "stderr" in outcome:
        head = outcome.get("error") or f"exit code {outcome.get('exit_code')}"
        parts = [head] + [outcome[key].rstrip() for key in ("stdout", "stderr") if outcome.get(key)]
        text = "\n".join(parts)
    elif isinstance(outcome.get("error"), str):
        text = outcome["error"]
    elif isinstance(outcome.get("diff"), str):
        text = outcome["diff"]
    elif isinstance(outcome.get("text"), str):
        text = outcome["text"]
    elif any(isinstance(outcome.get(key), list) for key in ("matches", "entries", "paths")):
        rows = next(outcome[key] for key in ("matches", "entries", "paths") if isinstance(outcome.get(key), list))
        text = "\n".join(row if isinstance(row, str) else json.dumps(row, ensure_ascii=False) for row in rows) or "No results."
    else:
        text = json.dumps(outcome, ensure_ascii=False, indent=2)
    return text if len(text) <= limit else text[:limit] + f"\n… {len(text) - limit:,} more characters. Open the session trace for the rest."


class StudioArchive(Observations):
    """An observation store that also reports, per tool result, how much text the prompt still carries."""

    def __init__(self):
        super().__init__()
        self.lock = threading.RLock()
        self.rows = []

    def fold(self, messages, keep=3, tool="recall"):
        with self.lock:
            removed = super().fold(messages, keep, tool)
            calls = {call["id"]: call["function"] for message in messages for call in (message.get("tool_calls") or [])}
            rows = []
            for message in messages:
                if message["role"] != "tool":
                    continue
                content = message["content"]
                try:
                    item = json.loads(content)
                except ValueError:
                    item = {}
                ref = item.get("reference") if isinstance(item, dict) else None
                if not isinstance(ref, str) or ref not in self.records:
                    ref = None
                size = max(len(self.records[ref]), len(content)) if ref else len(content)
                function = calls.get(message["tool_call_id"]) or {}
                name = function.get("name") or "tool"
                rows.append({"id": f"{len(rows)}:{message['tool_call_id']}", "tool": name,
                             "label": label_for(name, function.get("arguments") or "{}")[:160],
                             "before": size, "after": len(content), "reference": ref})
            self.rows = rows
            return removed

    def snapshot(self):
        with self.lock:
            archived = [{"id": reference, "tool": "exchange", "label": "earlier exchange", "before": size, "after": 0,
                         "reference": reference} for reference, size in self.exchanges if reference in self.records]
            return archived + self.rows


class Workbench:
    def __init__(self, root):
        self.files = Workspace(root)
        self.root = self.files.root
        self.lock = threading.Condition()
        self.revision = 0
        self.snapshot_number = 0
        self.busy = False
        self.messages = []
        self.history = []
        self.archive = StudioArchive()
        self.pending = None
        self.decision = False
        self.risk = None
        self.verdict = None
        self.stop = threading.Event()
        self.trace = self.new_trace()
        self.draft = ""
        self.running = ""
        self.notified = 0.0
        self.always = set()
        self.session_edits = False
        self.turn_base = 0
        self.session_key = ""
        self.catalog = {}
        self.persona = "builder"
        self.ledger = meter.Ledger(self.root)
        self.steps = []
        self.context = {}
        self.stages = []
        self.mcp = None
        self.unwound = set()
        self.settings = json.loads(json.dumps(DEFAULTS))
        try:
            saved = json.loads(settings_path(self.root).read_text(encoding="utf-8"))
            self.configure(saved.get("settings") or {}, persist=False)
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            pass
        try:
            self.connection = providers.resolve()
        except (ValueError, OSError):
            self.connection = None
        if self.connection and self.connection.provider and not self.connection.context:
            threading.Thread(target=self.enrich, args=(self.connection,), daemon=True).start()

    def enrich(self, basic):
        """Fill in the context window and cache price off the startup path."""
        try:
            detailed = providers.resolve(lookup=True)
        except Exception:
            return
        with self.lock:
            if self.connection is basic and detailed and detailed.model == basic.model:
                self.connection = detailed
                self.touch()

    def new_trace(self):
        return self.root / ".turnloom" / (uuid4().hex + ".jsonl")

    def touch(self):
        """Publish a state change to every waiting browser request. Call with the lock held."""
        self.revision += 1
        self.lock.notify_all()

    def idle(self):
        if self.busy:
            raise ValueError("Wait for the current turn to finish.")

    # ---- workspace files -------------------------------------------------

    def path(self, name):
        return self.files.resolve(name, operator=True)

    def edit(self, action, data):
        if action == "tree":
            return self.files.entries(data["path"], operator=True)
        path = self.path(data["path"])
        if not path.is_file() or path.stat().st_size > LIMIT:
            raise ValueError("The editor supports UTF-8 text files up to 512 KiB.")
        raw = path.read_bytes()
        if b"\x00" in raw:
            raise ValueError("This file contains binary data.")
        version = hashlib.sha256(raw).hexdigest()
        try:
            if action == "save":
                if version != data["version"]:
                    raise ValueError("The file changed on disk. Reload it before saving.")
                if not isinstance(data["text"], str):
                    raise ValueError("text must be a string.")
                content = data["text"].replace("\r\n", "\n")
                if uses_crlf(raw.decode("utf-8")):
                    content = content.replace("\n", "\r\n")
                raw = content.encode("utf-8")
                if len(raw) > LIMIT:
                    raise ValueError("File exceeds the editor limit.")
                write_atomic(path, raw)
                version = hashlib.sha256(raw).hexdigest()
            return {"text": raw.decode("utf-8"), "version": version}
        except UnicodeDecodeError as error:
            raise ValueError("The editor supports UTF-8 text files up to 512 KiB.") from error

    def manage(self, action, data):
        """Create, rename or delete one file on the operator's request."""
        if action == "create":
            path = self.files.resolve(data["path"], exists=False, operator=True)
            if path.exists():
                raise ValueError("A file or folder with that name already exists.")
            write_atomic(path, b"")
            return {"path": self.files.name(path)}
        path = self.path(data["path"])
        if not path.is_file():
            raise ValueError("Only files can be renamed or deleted here.")
        if action == "delete":
            path.unlink()
            return True
        target = self.files.resolve(data["to"], exists=False, operator=True)
        if target.exists():
            raise ValueError("A file or folder with that name already exists.")
        target.parent.mkdir(parents=True, exist_ok=True)
        path.rename(target)
        return {"path": self.files.name(target)}

    def git(self, *arguments):
        executable = find_program("git", self.root)
        if not executable:
            raise ValueError("Install Git to review changes.")
        return subprocess.run([executable, "-c", "core.quotepath=off", *arguments], cwd=self.root, capture_output=True,
                              timeout=15, stdin=subprocess.DEVNULL, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))

    def changes(self):
        """Files that differ from the last commit, as Git reports them."""
        result = self.git("status", "--porcelain=v1", "-z", "--untracked-files=all")
        if result.returncode:
            return {"repository": False, "files": []}
        rows, parts = [], result.stdout.decode("utf-8", "replace").split("\0")
        index = 0
        while index < len(parts):
            entry = parts[index]
            index += 1
            if len(entry) < 4:
                continue
            code, name = entry[:2], entry[3:]
            if code[0] in "RC":
                index += 1
            if not any(canonical(part) in IGNORE for part in Path(name).parts):
                rows.append({"path": name, "status": "added" if "?" in code or "A" in code else
                             "deleted" if "D" in code else "renamed" if "R" in code else "modified"})
        return {"repository": True, "files": rows[:400]}

    def change(self, name):
        """One file's committed and current text for the side-by-side review."""
        target = self.files.resolve(name, exists=False, operator=True)
        relative = self.files.name(target)
        shown = self.git("show", "HEAD:" + relative)
        original = shown.stdout if shown.returncode == 0 else b""
        modified = target.read_bytes() if target.is_file() else b""
        if max(len(original), len(modified)) > LIMIT or b"\x00" in original or b"\x00" in modified:
            raise ValueError("This change is binary or larger than the 512 KiB review limit.")
        return {"path": relative, "original": original.decode("utf-8", "replace").replace("\r\n", "\n"),
                "modified": modified.decode("utf-8", "replace").replace("\r\n", "\n")}

    def edits(self):
        """Files the agent's own tools edited in the latest turn, net per file."""
        if not self.messages:
            return []
        _, changed = self.files.pending(since=self.turn_base)
        rows = []
        for item in changed:
            diff = unified(item["before"] or "", item["after"], item["path"], 200000).splitlines()
            rows.append({"path": item["path"], "created": item["before"] is None,
                         "added": sum(line.startswith("+") and not line.startswith("+++") for line in diff),
                         "removed": sum(line.startswith("-") and not line.startswith("---") for line in diff)})
        return rows

    # ---- approvals -------------------------------------------------------

    def approve(self, kind, detail):
        with self.lock:
            if self.stop.is_set():
                return False
            settings = self.settings
            edits = kind == "edit" and (settings["auto_edit"] or self.session_edits)
            if settings["auto_approve"] or edits or (kind, detail) in self.always:
                self.risk = self.verdict = None
                return True
            title, _, body = detail.partition("\n") if kind == "edit" else ("", "", detail)
            request = {"id": uuid4().hex, "kind": kind, "title": title, "detail": body,
                       "risk": self.risk, "policy": self.verdict}
            self.risk = self.verdict = None
            self.pending = request
            self.decision = False
            self.touch()
            # No timer: the request stays open until the operator answers or stops the turn.
            self.lock.wait_for(lambda: self.pending is not request)
            return self.decision

    def assess(self, server, name, arguments):
        """Ask Unwind what a pending tool call would do before the operator decides."""
        self.risk = None
        if server not in self.unwound or name.startswith("unwind."):
            return
        outcome = self.mcp.direct(server, "unwind.preview", {"tool": name, "args": arguments}, 10)
        preview = self.payload(outcome)
        if isinstance(preview, dict) and preview.get("reversibility_class"):
            self.risk = {key: preview.get(key) for key in ("reversibility_class", "class_label", "blast_radius",
                                                           "can_undo", "undo_via", "fidelity", "reason")}

    def gate_state(self):
        active = guard.installed() and guard.policy_path(self.root).is_file() and self.settings["gate"] != "off"
        return {"installed": guard.installed(), "policy": guard.policy_path(self.root).is_file(),
                "mode": self.settings["gate"], "active": active}

    def note_verdict(self, verdict):
        with self.lock:
            self.verdict = verdict

    def policy(self):
        """Everything the Policy view shows: the gate's state, the policy text, the ledger and its integrity."""
        state = self.gate_state()
        path = guard.policy_path(self.root)
        text = path.read_text(encoding="utf-8", errors="replace")[:60000] if state["policy"] else ""
        if not state["installed"]:
            return {**state, "text": text, "records": [], "counts": {}, "total": 0, "chain": None}
        return {**state, "text": text, **guard.ledger(self.root), "chain": guard.verify(self.root)}

    # ---- MCP servers, VerifyDoc and Unwind --------------------------------

    def server_specs(self):
        """Servers from <workspace>/.turnloom/mcp.json plus the bundled document server."""
        specs, ledger = {}, self.root / ".turnloom" / "unwind.db"
        path = self.root / ".turnloom" / "mcp.json"
        configured = {}
        if path.is_file():
            data = json.loads(path.read_text(encoding="utf-8-sig"))
            configured = data.get("mcpServers") if isinstance(data, dict) else None
            if not isinstance(configured, dict):
                raise ValueError(".turnloom/mcp.json requires an mcpServers object.")
        unwound = set()
        if companions_installed():
            specs["verifydoc"] = through_unwind(ledger)
            unwound.add("verifydoc")
        for name, spec in configured.items():
            if name in specs or not isinstance(spec, dict):
                raise ValueError(f"Invalid MCP server: {name}")
            spec = dict(spec)
            guarded = spec.pop("unwind", False) is True
            if "command" in spec:
                spec["args"] = [str(value).replace("${configDir}", str(path.parent)) for value in spec.get("args", [])]
            if guarded and "command" in spec and find_spec("unwind"):
                specs[name] = through_unwind(ledger, spec)
                unwound.add(name)
            else:
                specs[name] = spec
        return specs, unwound

    def tools(self):
        """The long-lived MCP connection manager shared by agent turns and Studio's own panels."""
        with self.lock:
            if self.mcp is None:
                specs, unwound = self.server_specs()
                if not specs:
                    raise ValueError("No MCP servers. Install turnloom[documents] or add .turnloom/mcp.json to the workspace.")
                trace_folder(self.root / ".turnloom")
                manager = McpTools(None, self.root, servers=specs, startup_timeout=90)
                manager.__enter__()
                manager.before_approval = self.assess
                self.mcp, self.unwound = manager, unwound
            self.mcp.enabled = {name for name, spec in self.mcp.servers.items() if self.trusted(name, spec)}
            return self.mcp

    def servers_on(self):
        """Names of the servers the agent can use on the next turn."""
        try:
            specs = self.mcp.servers if self.mcp else self.server_specs()[0]
        except (ValueError, OSError):
            return []
        return [name for name, spec in specs.items() if self.trusted(name, spec)]

    def trusted(self, name, spec):
        """A server runs for the agent only if the operator switched on this exact definition of it."""
        return name in self.settings["servers"] and self.settings["trusted"].get(name) == digest(spec)

    def close(self):
        with self.lock:
            manager, self.mcp = self.mcp, None
            self.touch()
        if manager:
            try:
                manager.__exit__(None, None, None)
            except Exception:
                pass

    @staticmethod
    def payload(outcome):
        """The structured or JSON-text body of an MCP tool result."""
        if outcome.get("error"):
            raise ValueError(outcome["error"])
        structured = outcome.get("structuredContent")
        if isinstance(structured, dict) and structured:
            value = structured.get("result", structured)
            if isinstance(value, str):
                try:
                    return json.loads(value)
                except ValueError:
                    return value
            return value
        for part in outcome.get("content") or []:
            if isinstance(part, str):
                try:
                    return json.loads(part)
                except ValueError:
                    if outcome.get("isError"):
                        raise ValueError(part[:600]) from None
                    return part
        return None

    def servers(self):
        try:
            specs, unwound = (self.mcp.servers, self.unwound) if self.mcp else self.server_specs()
        except (ValueError, OSError) as error:
            return {"error": str(error), "servers": [], "companions": companions_installed()}
        def summary(spec):
            if "url" in spec:
                return spec["url"]
            return (Path(spec["command"]).name + " " + " ".join(spec.get("args", [])))[:160]
        return {"companions": companions_installed(), "config": ".turnloom/mcp.json", "servers": [
            {"name": name, "command": summary(spec), "enabled": self.trusted(name, spec),
             "changed": name in self.settings["servers"] and not self.trusted(name, spec),
             "unwind": name in unwound, "bundled": name == "verifydoc",
             "running": bool(self.mcp and name in self.mcp.sessions)} for name, spec in specs.items()]}

    def verify(self, data):
        """Run VerifyDoc on a workspace file or pasted text, through Unwind, with no model in the loop."""
        schema = data["schema"]
        if isinstance(schema, str):
            schema = json.loads(schema)
        if not isinstance(schema, dict) or schema.get("type") != "object":
            raise ValueError("The schema must be a JSON Schema object.")
        text = data.get("text") or ""
        if data.get("path"):
            path = self.path(data["path"])
            document = str(path)
            try:
                text = self.edit("read", {"path": data["path"]})["text"]
            except ValueError:
                text = ""
        elif text.strip():
            document = text if len(text) >= 1024 or not Path(text).expanduser().exists() else text + "\n"
        else:
            raise ValueError("Choose a document or paste its text.")
        threshold = min(1.0, max(0.0, float(data.get("threshold", 0.8))))
        arguments = {"document": document, "schema": json.dumps(schema), "threshold": threshold,
                     "adapter": data.get("adapter") or "text-search"}
        manager = self.tools()
        manager.tools("verifydoc")
        result = self.payload(manager.direct("verifydoc", "verify_extraction", arguments, 180))
        if not isinstance(result, dict) or "fields" not in result:
            raise ValueError("VerifyDoc returned no fields.")
        return {**result, "text": text.replace("\r\n", "\n")[:200_000]}

    def unwind_ledger(self, limit=200):
        """Read Unwind's durable log straight from its database, so history shows before any server starts."""
        path = self.root / ".turnloom" / "unwind.db"
        if not path.is_file():
            return {"entries": [], "stats": {}, "live": bool(self.mcp and self.mcp.sessions)}
        connection = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True, timeout=3)
        try:
            rows = connection.execute("SELECT payload FROM undo_entries ORDER BY ts DESC LIMIT ?", (limit,)).fetchall()
        except sqlite3.Error:
            rows = []
        finally:
            connection.close()
        entries, stats = [], {}
        for (payload,) in rows:
            try:
                item = json.loads(payload)
            except ValueError:
                continue
            plan = item.get("plan") or {}
            status = item.get("status") or "active"
            stats[status] = stats.get(status, 0) + 1
            level = item.get("rev_class")
            entries.append({"id": item.get("id"), "time": item.get("ts"), "server": item.get("server"), "tool": item.get("tool"),
                            "class": f"R{level}" if isinstance(level, int) else str(level), "status": status,
                            "expires": item.get("expires_at"), "inverse": plan.get("inverse_tool"),
                            "residue": plan.get("residue") or [], "session": item.get("session_id"),
                            "arguments": json.dumps(item.get("args") or {}, ensure_ascii=False)[:600]})
        return {"entries": entries, "stats": stats, "live": bool(self.mcp and self.mcp.sessions)}

    def unwind_call(self, name, arguments):
        self.idle()
        manager = self.tools()
        server = next((key for key in manager.servers if key in self.unwound), None)
        if not server:
            raise ValueError("No server is routed through Unwind.")
        manager.tools(server)
        return self.payload(manager.direct(server, name, arguments, 60))

    # ---- settings, usage and personas -------------------------------------

    def configure(self, data, persist=True):
        settings = self.settings
        if "budget" in data:
            settings["budget"] = min(1000.0, max(0.0, float(data["budget"] or 0)))
        if "keep" in data:
            settings["keep"] = min(20, max(1, int(data["keep"])))
        if "reply" in data:
            settings["reply"] = min(400_000, max(256, int(data["reply"])))
        for key in ("fold", "auto_approve", "auto_edit", "cache"):
            if key in data:
                settings[key] = data[key] is True
        if data.get("effort") in ("", "minimal", "low", "medium", "high"):
            settings["effort"] = data["effort"]
        if data.get("mode") in ("solo", "team"):
            settings["mode"] = data["mode"]
        if data.get("gate") in ("confirm", "policy", "off"):
            settings["gate"] = data["gate"]
        if isinstance(data.get("trusted"), dict) and not persist:
            settings["trusted"] = {name: value for name, value in data["trusted"].items()
                                   if isinstance(name, str) and isinstance(value, str)}
        if isinstance(data.get("servers"), list):
            settings["servers"] = [name for name in data["servers"] if isinstance(name, str)][:32]
            if persist:
                # Switching a server on trusts the definition it has right now, not whatever replaces it later.
                try:
                    specs = self.mcp.servers if self.mcp else self.server_specs()[0]
                except (ValueError, OSError):
                    specs = {}
                settings["trusted"] = {name: digest(specs[name]) for name in settings["servers"] if name in specs}
        if isinstance(data.get("team"), list):
            team = []
            for stage in data["team"][:8]:
                name = str(stage["name"]).strip()[:24]
                roster.find(stage["persona"], self.root)
                if not name or any(name == other["name"] for other in team):
                    raise ValueError("Give each team stage a unique name.")
                team.append({"name": name, "persona": stage["persona"], "read_only": stage.get("read_only") is True,
                             "instruction": str(stage["instruction"]).strip()[:2000]})
            if not team:
                raise ValueError("A team needs at least one stage.")
            settings["team"] = team
        if persist:
            record = {"workspace": str(self.root), "settings": settings}
            write_atomic(settings_path(self.root), json.dumps(record, indent=1).encode("utf-8"))
        return settings

    def priced(self):
        conn = self.connection
        return bool(conn and (conn.input_rate or conn.output_rate or conn.free))

    def window(self):
        return (self.connection.context if self.connection and self.connection.context else 0) or 128000

    def record_step(self, event, persona):
        """Meter one model call: session timeline, prompt composition and the durable ledger."""
        conn = self.connection
        folded = sum(row["before"] - row["after"] for row in self.archive.snapshot())
        saved = folded // 4
        cost = event["cost"] if self.priced() else None
        row = {"step": len(self.steps) + 1, "prompt_tokens": event["prompt_tokens"],
               "completion_tokens": event["completion_tokens"], "cached_tokens": event["cached_tokens"],
               "cost": cost, "saved_tokens": saved, "persona": persona}
        parts = meter.composition(self.history) if self.history else {}
        with self.lock:
            self.steps.append(row)
            if parts:
                self.context = parts
            self.touch()
        self.ledger.add(model=conn.model, persona=persona, session=self.trace.stem,
                        **{key: row[key] for key in ("prompt_tokens", "completion_tokens", "cached_tokens", "cost", "saved_tokens")},
                        saved_cost=saved * conn.input_rate / 1e6 if self.priced() else 0)

    def usage(self):
        summary = self.ledger.summary()
        conn = self.connection
        session = {key: sum(step[key] or 0 for step in self.steps)
                   for key in ("prompt_tokens", "completion_tokens", "cached_tokens", "cost")}
        alternatives = []
        if conn and conn.provider:
            rows = self.catalog.get(conn.provider)
            if rows is None and conn.provider.startswith(gateway.PREFIX):
                rows = self.catalog[conn.provider] = {row["model"]: row for row in gateway.models(conn.provider)}
            tokens = (session["prompt_tokens"] or 100_000, session["completion_tokens"] or 10_000)
            current = (tokens[0] * conn.input_rate + tokens[1] * conn.output_rate) / 1e6
            for row in (rows or {}).values():
                if row["model"] == conn.model or row.get("tools") is False or row.get("input") is None or row.get("output") is None:
                    continue
                projected = (tokens[0] * row["input"] + tokens[1] * row["output"]) / 1e6
                if projected < current or (not self.priced() and row.get("tier") == "free"):
                    alternatives.append({"model": row["model"], "input": row["input"], "output": row["output"],
                                         "projected": round(projected, 6), "context": row.get("context")})
            alternatives.sort(key=lambda row: row["projected"])
            alternatives = {"basis": {"prompt_tokens": tokens[0], "completion_tokens": tokens[1], "current": round(current, 6),
                                      "measured": bool(session["prompt_tokens"])},
                            "catalog": rows is not None, "models": alternatives[:6]}
        return {**summary, "session": session, "steps": self.steps[-200:], "alternatives": alternatives or None,
                "exact_tokens": meter.exact(), "priced": self.priced()}

    # ---- agent turns -------------------------------------------------------

    def on_text(self, delta):
        with self.lock:
            self.draft += delta
            if time.monotonic() - self.notified > 0.08:
                self.notified = time.monotonic()
                self.touch()

    def tool_started(self, name, arguments):
        with self.lock:
            if self.draft.strip():
                self.messages.append({"role": "assistant", "text": self.draft, "interim": True})
            self.draft = ""
            self.running = label_for(name, arguments)[:240]
            self.touch()

    def tool_result(self, name, arguments, result):
        outcome = json.loads(result)
        outcome = outcome if isinstance(outcome, dict) else {}
        failed = bool(outcome.get("error") or outcome.get("isError") or outcome.get("exit_code"))
        blocked = (outcome.get("rule") or "policy") if outcome.get("blocked_by") == "provio" else ""
        message = {"role": "tool", "text": readable(outcome), "name": name, "label": label_for(name, arguments)[:240],
                   "failed": failed, "blocked": blocked, "declined": outcome.get("declined") is True}
        if outcome.get("diff"):
            message.update(diff=True, label=f"{name} {outcome['path']} (+{outcome['added']} −{outcome['removed']})")
        with self.lock:
            self.messages.append(message)
            self.running = ""
            self.touch()

    def prompt(self, role):
        return instructions(role, None, self.root) + REVIEW

    def start(self, data):
        task = data["task"]
        if not isinstance(task, str):
            raise ValueError("Enter a task of up to 32,000 characters.")
        task = task.strip()
        role = roster.find(data["persona"], self.root)["id"]
        if not self.connection:
            raise ValueError("Connect a model before sending a task.")
        if not task or len(task) > 32000:
            raise ValueError("Enter a task of up to 32,000 characters.")
        with self.lock:
            if self.busy:
                raise ValueError("An agent turn is already running.")
            settings = dict(self.settings)
            team = settings["mode"] == "team"
            if "companions" in data:
                wanted = set(settings["servers"]) | {"verifydoc"} if data["companions"] is True else set(settings["servers"]) - {"verifydoc"}
                settings["servers"] = self.settings["servers"] = sorted(wanted)
                if data["companions"] is True and companions_installed():
                    self.settings["trusted"]["verifydoc"] = digest(through_unwind(self.root / ".turnloom" / "unwind.db"))
            tools = None
            if settings["servers"]:
                try:
                    tools = self.tools()
                    tools = tools if tools.enabled else None
                except ValueError:
                    if data.get("companions") is True:
                        raise
            gate = None
            state = self.gate_state()
            if state["policy"] and not state["installed"] and settings["gate"] != "off":
                raise ValueError('This workspace has a provio.yaml but Provio is not installed. Install "turnloom[guard]", '
                                 "or turn the gate off in the Policy view to run without the policy.")
            if state["active"]:
                gate = guard.Gate(self.root, approve=self.approve, confirm=settings["gate"] == "confirm",
                                  session=self.trace.stem, notify=self.note_verdict)
            self.stop.clear()
            if team:
                self.history.clear()
            self.persona = role
            conn = self.connection
            priced = self.priced()
            current = {"persona": role}
            config = Config(conn.endpoint, conn.model, conn.key, self.root, transcript=self.trace,
                            system_prompt=self.prompt(role), approve=self.approve, files=self.files,
                            max_steps=STEPS, tool_limit=TOOLS, stop_requested=self.stop.is_set, deadline_seconds=4 * 3600,
                            input_rate=conn.input_rate, output_rate=conn.output_rate, cache_rate=conn.cache_rate,
                            on_text=self.on_text, on_tool_start=self.tool_started, on_tool_event=self.tool_result,
                            mcp=tools, gate=gate, context_tokens=self.window(), read_only=data.get("readOnly") is True,
                            max_reply_tokens=min(settings["reply"], self.window() // 2),
                            reasoning_effort=settings["effort"] or None, fold_history=settings["fold"],
                            fold_keep=settings["keep"], cache_prompt=settings["cache"],
                            budget_usd=settings["budget"] if priced and (conn.free or (conn.input_rate and conn.output_rate)) else 0,
                            on_step=lambda event: self.record_step(event, current["persona"]))
            if self.history and self.history[0]["role"] == "system":
                self.history[0]["content"] = noted(config.system_prompt, self.archive)
            self.turn_base = self.files.turn
            self.messages.append({"role": "user", "text": task})
            self.stages = [{"name": stage["name"], "persona": stage["persona"], "status": "queued"}
                           for stage in settings["team"]] if team else []
            self.busy = True
            self.draft = self.running = ""
            self.touch()

        def on_stage(name, status, result):
            with self.lock:
                for stage in self.stages:
                    if stage["name"] == name:
                        stage["status"] = status
                        current["persona"] = stage["persona"]
                        if result is not None:
                            self.draft = ""
                            self.messages.append({"role": "assistant", "text": result.answer or " ".join(result.errors) or status,
                                                  "status": status, "stage": name, "persona": stage["persona"],
                                                  "usage": result.usage, "cost": result.estimated_usd if priced else None})
                self.touch()

        def run():
            message = {"role": "error", "text": "The turn ended unexpectedly."}
            try:
                if team:
                    from turnloom.team import run_team
                    stages = [{"name": stage["name"], "instruction": stage["instruction"], "prompt": self.prompt(stage["persona"]),
                               "read_only": stage.get("read_only") is True} for stage in settings["team"]]
                    result = run_team(task, config, stages, on_stage, self.archive)
                    message = {"role": "assistant", "status": result.status, "usage": result.usage, "summary": True,
                               "text": f"Team finished: {result.status}." if result.status == "completed" else
                               "Team stopped: " + (" ".join(result.errors) or result.status),
                               "cost": result.estimated_usd if priced else None}
                else:
                    result = run_agent(task, config, self.history, self.archive)
                    message = {"role": "assistant", "text": result.answer or " ".join(result.errors) or
                               ("Turn stopped by operator." if result.status == "stopped" else result.status),
                               "status": result.status, "note": result.errors[-1] if result.status == "partial" else "",
                               "usage": result.usage, "cost": result.estimated_usd if priced else None, "persona": role}
                if result.status not in ("completed", "partial", "stopped"):
                    message["role"] = "error"
            except BaseException as error:
                message = {"role": "error", "text": f"{type(error).__name__}: {error}"}
            finally:
                if gate:
                    try:
                        gate.close()
                    except Exception:
                        pass
                # Whatever happened, the workbench must come back to idle.
                with self.lock:
                    self.messages.append(message)
                    if team:
                        self.history.clear()
                    self.busy = False
                    self.pending = None
                    self.draft = self.running = ""
                    self.touch()
        threading.Thread(target=run, daemon=True).start()
        return True

    def reset(self):
        """Start an empty session in the same workspace."""
        with self.lock:
            self.idle()
            self.clear()
            self.trace = self.new_trace()
            self.touch()
        return True

    def clear(self):
        self.messages, self.steps, self.context, self.stages = [], [], {}, []
        self.history.clear()
        self.archive = StudioArchive()
        self.files.changes = []
        self.turn_base = self.files.turn
        self.always.clear()
        self.session_edits = False
        self.draft = self.running = ""

    def sessions(self):
        rows = []
        for path in traces(self.root)[:40]:
            task, turns = "", 0
            for event in events(path):
                if event.get("type") == "start":
                    task = task or event.get("task", "")
                elif event.get("type") == "end":
                    turns += 1
            if task:
                rows.append({"id": path.stem, "task": " ".join(task.split())[:120], "turns": turns,
                             "time": path.stat().st_mtime, "current": path == self.trace})
        return rows

    def resume(self, ident):
        path = next((item for item in traces(self.root) if item.stem == ident), None)
        if path is None:
            raise ValueError("That session is no longer available.")
        history = load_history(path, self.prompt(self.persona))
        feed, names = [], {}
        for event in events(path):
            kind = event.get("type")
            if kind == "start":
                feed.append({"role": "user", "text": event["task"]})
            elif kind == "assistant":
                for call in event["message"].get("tool_calls") or []:
                    names[call["id"]] = (call["function"]["name"], call["function"]["arguments"])
            elif kind == "tool":
                name, arguments = names.get(event["id"], (event.get("name", "tool"), "{}"))
                outcome = event["result"] if isinstance(event["result"], dict) else {}
                failed = bool(outcome.get("error") or outcome.get("isError") or outcome.get("exit_code"))
                feed.append({"role": "tool", "name": name, "label": label_for(name, arguments)[:240], "failed": failed,
                             "text": readable(event["result"]), "diff": bool(outcome.get("diff")), "blocked": ""})
            elif kind == "end":
                result = event["result"]
                failed = result["status"] not in ("completed", "partial", "stopped")
                feed.append({"role": "error" if failed else "assistant", "status": result["status"],
                             "usage": result.get("usage"), "cost": None,
                             "text": result.get("answer") or " ".join(result.get("errors") or []) or result["status"]})
        self.clear()
        self.history.extend(history)
        self.messages = feed
        self.trace = path
        self.archive.fold(self.history, self.settings["keep"])

    def describe(self):
        conn = self.connection
        if not conn:
            return None
        return {"model": conn.model, "provider": conn.provider, "input": conn.input_rate, "output": conn.output_rate,
                "cache": conn.cache_rate, "context": conn.context, "free": conn.free, "priced": self.priced(),
                "gateway": conn.endpoint.startswith(gateway.SCHEME), "sessionKey": bool(self.session_key)}

    def state(self, data):
        with self.lock:
            if data.get("wait") is True and data.get("revision") == self.revision:
                self.lock.wait_for(lambda: self.revision != data["revision"], timeout=25)
            offset = data.get("since", 0)
            if not isinstance(offset, int) or isinstance(offset, bool) or not 0 <= offset <= len(self.messages) \
                    or data.get("session") not in (None, self.trace.stem):
                offset = 0
            self.snapshot_number += 1
            totals = {key: sum(step[key] or 0 for step in self.steps)
                      for key in ("prompt_tokens", "completion_tokens", "cached_tokens", "cost", "saved_tokens")}
            last = self.steps[-1] if self.steps else None
            return {"workspace": self.root.name, "model": self.connection.model if self.connection else "",
                    "connection": self.describe(), "version": __version__,
                    "busy": self.busy, "stopping": self.busy and self.stop.is_set(),
                    "pending": self.pending, "messages": self.messages[offset:],
                    "offset": offset, "snapshot": self.snapshot_number, "revision": self.revision,
                    "session": self.trace.stem, "trace": f".turnloom/{self.trace.name}",
                    "draft": self.draft, "running": self.running,
                    "loom": self.archive.snapshot(), "edits": self.edits(),
                    "always": {"edits": self.session_edits, "requests": len(self.always)},
                    "servers_on": self.servers_on(),
                    "persona": self.persona, "settings": self.settings,
                    "stages": self.stages, "companions": companions_installed(), "gate": self.gate_state(),
                    "limits": {"steps": STEPS, "tools": TOOLS},
                    "meter": {**totals, "steps": len(self.steps), "priced": self.priced(), "window": self.window(),
                              "prompt": last["prompt_tokens"] if last else 0, "context": self.context,
                              "spark": [step["prompt_tokens"] for step in self.steps[-40:]]}}

    def dispatch(self, action, data):
        if action == "recall":
            archive = self.archive
            with archive.lock:
                found = json.loads(archive.recall(json.dumps(data)))
            if found.get("error"):
                raise ValueError("That archived result is no longer held in memory. Resume the session to rebuild it.")
            return found
        if action in ("tree", "read", "save"):
            with self.lock:
                return self.edit(action, data)
        if action in ("create", "rename", "delete"):
            with self.lock:
                self.idle()
                return self.manage(action, data)
        if action == "state":
            return self.state(data)
        if action == "run":
            return self.start(data)
        if action in ("reset", "new"):
            return self.reset()
        if action == "stop":
            with self.lock:
                if self.busy:
                    self.stop.set()
                    self.pending = None
                    self.decision = False
                    self.touch()
            return True
        if action == "decision":
            with self.lock:
                if not self.pending or self.pending["id"] != data["id"]:
                    raise ValueError("Approval has expired.")
                self.decision = data["approve"] is True
                if self.decision and data.get("always") is True:
                    if self.pending["kind"] == "edit":
                        # For this session only. The saved setting changes only through the settings action.
                        self.session_edits = True
                    else:
                        self.always.add((self.pending["kind"], self.pending["detail"]))
                self.pending = None
                self.touch()
            return True
        if action == "edit_diff":
            with self.lock:
                _, changed = self.files.pending(since=self.turn_base)
                item = next((item for item in changed if item["path"] == data["path"]), None)
                if item is None:
                    raise ValueError("No recorded edit for that file.")
                return {"path": item["path"], "original": (item["before"] or "").replace("\r\n", "\n"),
                        "modified": item["after"].replace("\r\n", "\n"), "created": item["before"] is None,
                        "diff": unified(item["before"] or "", item["after"], item["path"], 200000)}
        if action == "revert":
            with self.lock:
                self.idle()
                outcome = self.files.undo(since=self.turn_base)
                parts = []
                if outcome["restored"]:
                    parts.append("Restored " + ", ".join(outcome["restored"]) + ".")
                if outcome["conflicts"]:
                    parts.append("Kept " + ", ".join(outcome["conflicts"]) + " because it changed after the agent's edit.")
                self.messages.append({"role": "note", "text": " ".join(parts) or "No edits to restore."})
                if outcome["restored"] and self.history:
                    self.history.append({"role": "user", "content": "The operator reverted your edits to: "
                                         + ", ".join(outcome["restored"]) + ". Those files are back to their earlier content."})
                    self.history.append({"role": "assistant", "content": "Understood. I will read those files again before relying on them."})
                self.touch()
                return outcome
        if action in ("sessions", "resume"):
            with self.lock:
                if action == "sessions":
                    return self.sessions()
                self.idle()
                self.resume(data["id"])
                self.touch()
            return True
        if action == "personas":
            return {"source": roster.upstream()["source"], "commit": roster.upstream()["commit"],
                    "featured": roster.FEATURED, "personas": [{key: row[key] for key in
                    ("id", "name", "description", "division", "emoji", "color", "vibe")} for row in roster.catalog(self.root)]}
        if action == "persona":
            row = roster.find(data["id"], self.root)
            return {"id": row["id"], "name": row["name"], "text": roster.body(row["id"], self.root)[:40000]}
        if action == "settings":
            with self.lock:
                self.idle()
                settings = self.configure(data)
                self.touch()
                return settings
        if action == "usage":
            return self.usage()
        if action == "policy":
            return self.policy()
        if action == "policy_create":
            guard.create_policy(self.root)
            with self.lock:
                self.touch()
            return self.policy()
        if action == "policy_test":
            return guard.test(self.root, data.get("tool") or "bash", data.get("value"))
        if action == "changes":
            return self.changes()
        if action == "change":
            return self.change(data["path"])
        if action == "servers":
            return self.servers()
        if action == "server_tools":
            if data["name"] not in self.tools().servers:
                raise ValueError("Unknown server.")
            return self.mcp.tools(data["name"])
        if action == "schemas":
            return {"schemas": SCHEMAS, "adapters": ["text-search"], "installed": companions_installed()}
        if action == "verify":
            return self.verify(data)
        if action == "ledger":
            return self.unwind_ledger()
        if action == "undo":
            arguments = {"to_checkpoint": data["checkpoint"]} if data.get("checkpoint") else {"n": max(1, int(data.get("n", 1)))}
            return self.unwind_call("unwind.undo", arguments)
        if action == "checkpoint":
            return self.unwind_call("unwind.checkpoint", {"label": str(data.get("label") or "")[:80]})
        if action == "providers":
            rows = [{"id": key, "name": providers.PROVIDERS[key][0], "requiresKey": bool(providers.PROVIDERS[key][2]),
                     "endpoint": providers.PROVIDERS[key][1], "variable": providers.PROVIDERS[key][2],
                     "saved": bool(providers.PROVIDERS[key][2]) and bool(providers.secret(key, required=False)),
                     "group": "Popular" if key in providers.POPULAR else "OpenAI-compatible"}
                    for key in dict.fromkeys(providers.POPULAR + sorted(providers.PROVIDERS))]
            if gateway.available():
                for key, name, count in gateway.providers():
                    variable = gateway.key_variable(key)
                    rows.append({"id": key, "name": name, "requiresKey": bool(variable), "group": "Native APIs via LiteLLM",
                                 "endpoint": gateway.SCHEME + key.removeprefix(gateway.PREFIX), "variable": variable,
                                 "saved": bool(variable) and bool(providers.secret(key, required=False)),
                                 "models": count, "environment": gateway.requirements(key)})
            return rows
        if action in ("models", "connect"):
            provider = data["provider"]
            if not providers.known(provider):
                raise ValueError("Unknown provider.")
            name, endpoint, variable = providers.info(provider)
            key = data.get("key") or providers.secret(provider, required=False)
            if action == "models":
                rows = providers.models(provider, key)
                for row in rows if gateway.available() and not provider.startswith(gateway.PREFIX) else []:
                    # Many endpoints list models without prices or limits. LiteLLM's public map fills
                    # them in, and prices only when the entry is for this same provider.
                    if row.get("input") is not None and row.get("context"):
                        continue
                    listed = gateway.lookup(row["model"], provider)
                    if not listed:
                        continue
                    row["context"] = row.get("context") or listed.get("context")
                    if row.get("input") is None and listed.get("same_provider") and listed.get("input") is not None:
                        row.update(input=listed["input"], output=listed["output"], cache_read=listed.get("cache_read"),
                                   tier=listed["tier"] if row.get("tier") in (None, "unknown") else row["tier"],
                                   source=row.get("source", "live catalog") + " · prices from LiteLLM's public map")
                with self.lock:
                    self.catalog[provider] = {row["model"]: row for row in rows}
                return rows
            with self.lock:
                self.idle()
                row = self.catalog.get(provider, {}).get(data["model"])
                if not row:
                    raise ValueError("Refresh models and choose one.")
            if provider == "openrouter" and data.get("browser") and not key:
                from turnloom.openrouter import login
                key = login()
            if variable and not key:
                raise ValueError("Supply a provider key or sign in with OpenRouter.")
            if provider == "openrouter":
                providers.fetch(endpoint + "/key", key)
            with self.lock:
                self.idle()
                self.session_key = ""
                if key and (data.get("key") or data.get("browser")):
                    try:
                        providers.vault().set_password("turnloom:" + provider, "api-key", key)
                    except Exception:
                        # No OS credential store: hold the key in memory until Studio closes.
                        self.session_key = key
                providers.store_profile({"provider": provider, **row})
                self.connection = providers.connection(provider, row, self.session_key or key)
                self.touch()
            return True
        raise ValueError("Unknown action.")


def choose_folder():
    """Ask for a workspace in a native dialog. Without a display toolkit, use the current directory."""
    try:
        import tkinter as tk
        from tkinter.filedialog import askdirectory
        window = tk.Tk()
        window.withdraw()
        window.attributes("-topmost", True)
        chosen = askdirectory(title="Open workspace in Turnloom Studio", mustexist=True)
        window.destroy()
        return chosen or None
    except Exception:
        return str(Path.cwd())


def make_server(root, chooser=None, desktop=False, port=0):
    holder = {"bench": Workbench(root), "seen": time.monotonic()}
    token = secrets.token_urlsafe(32)
    assets = files("turnloom").joinpath("studio_assets")
    names = {item.name for item in assets.iterdir() if Path(item.name).suffix in KINDS}

    class Handler(BaseHTTPRequestHandler):
        timeout = 30

        def log_message(self, *args):
            pass

        def hosts(self):
            return {f"127.0.0.1:{self.server.server_port}", f"localhost:{self.server.server_port}"}

        def reply(self, code, body, kind):
            self.send_response(code)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Cross-Origin-Opener-Policy", "same-origin")
            self.send_header("Cross-Origin-Resource-Policy", "same-origin")
            self.send_header("Content-Security-Policy", "default-src 'self'; style-src 'self' 'unsafe-inline'; "
                             "img-src 'self' data:; font-src 'self' data:; connect-src 'self'; object-src 'none'; "
                             "form-action 'none'; frame-ancestors 'none'; base-uri 'none'")
            self.end_headers()
            self.wfile.write(body)

        def fail(self, code, text):
            self.reply(code, json.dumps({"error": text}).encode(), "application/json")

        def refuse(self, code):
            # Read a modest body before answering so the client receives the status instead of a reset.
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                length = -1
            if 0 < length <= LIMIT * 4:
                self.rfile.read(length)
            elif length != 0:
                self.close_connection = True
            self.reply(code, b"", "text/plain")

        def do_GET(self):
            if self.headers.get("Host") not in self.hosts():
                return self.refuse(403)
            name = self.path.split("?", 1)[0].lstrip("/") or "index.html"
            if name not in names:
                return self.refuse(404)
            self.reply(200, assets.joinpath(name).read_bytes(), KINDS[Path(name).suffix])

        def do_POST(self):
            origins = {None} | {"http://" + host for host in self.hosts()}
            if (self.headers.get("Host") not in self.hosts() or self.headers.get("Origin") not in origins
                    or not secrets.compare_digest(self.headers.get("Authorization", "").encode(), ("Bearer " + token).encode())):
                return self.refuse(403)
            bench = holder["bench"]
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= LIMIT * 4 or not self.path.startswith("/api/"):
                    self.close_connection = True
                    raise ValueError("Invalid request.")
                data = json.loads(self.rfile.read(length))
                if not isinstance(data, dict):
                    raise ValueError("Expected a JSON object.")
                holder["seen"] = time.monotonic()
                if self.path == "/api/close":
                    # Closing ends a running turn: its command is killed and its model request abandoned.
                    with bench.lock:
                        bench.stop.set()
                        bench.pending = None
                        bench.touch()
                        bench.lock.wait_for(lambda: not bench.busy, timeout=5)
                    threading.Thread(target=self.server.shutdown, daemon=True).start()
                    result = True
                elif self.path == "/api/workspace":
                    with bench.lock:
                        bench.idle()
                    chosen = (chooser or choose_folder)()
                    if chosen:
                        replacement = Workbench(Path(chosen))
                        replacement.connection = bench.connection
                        replacement.session_key = bench.session_key
                        holder["bench"] = replacement
                        bench.close()
                    result = {"changed": bool(chosen)}
                else:
                    result = bench.dispatch(self.path[5:], data)
                    if self.path == "/api/state":
                        result["desktop"] = desktop
                self.reply(200, json.dumps(result).encode(), "application/json")
            except (ConnectionError, TimeoutError):
                return
            except KeyError as error:
                self.fail(400, "Missing field: " + str(error.args[0] if error.args else error))
            except FileNotFoundError:
                self.fail(400, "That file no longer exists. Refresh the explorer.")
            except PermissionError:
                self.fail(400, "The operating system denied access to that file.")
            except OSError as error:
                self.fail(400, error.strerror or type(error).__name__)
            except (ValueError, RuntimeError, TypeError) as error:
                self.fail(400, str(error) or type(error).__name__)
            except Exception as error:
                self.fail(500, f"Studio hit an unexpected {type(error).__name__}. The session is still running.")

    class Server(ThreadingHTTPServer):
        daemon_threads = True

        def server_close(self):
            super().server_close()
            holder["bench"].close()

    server = Server(("127.0.0.1", port), Handler)
    server.holder = holder
    return server, f"http://127.0.0.1:{server.server_port}/#{token}"


def watch_idle(server, limit=IDLE_SECONDS, interval=30):
    """Stop a server nobody is using: no browser request for `limit` seconds and no turn running."""
    def idle():
        return not server.holder["bench"].busy and time.monotonic() - server.holder["seen"] > limit

    def loop():
        strikes = 0
        while strikes < 2:
            time.sleep(interval)
            # Two checks in a row, so a laptop waking from sleep gets time to reconnect.
            strikes = strikes + 1 if idle() else 0
        server.shutdown()
    threading.Thread(target=loop, daemon=True).start()


def handoff(url):
    """A private page that forwards a browser to the session.

    Opening this file instead of the session URL keeps the token off the browser's command line,
    which other users of the machine can read. The file is readable by its owner only and removed
    after half a minute. Returns its file:// address.
    """
    handle, name = tempfile.mkstemp(prefix="turnloom-studio-", suffix=".html")
    with os.fdopen(handle, "w", encoding="utf-8") as page:
        page.write('<!doctype html><meta charset="utf-8"><title>Turnloom Studio</title>'
                   f'<meta http-equiv="refresh" content="0;url={url}"><p>Opening Turnloom Studio…</p>')
    threading.Timer(30, lambda: Path(name).unlink(missing_ok=True)).start()
    return Path(name).as_uri()


def open_browser(url):
    return webbrowser.open(handoff(url))


def report(text):
    """Tell the operator about a startup failure, in a dialog when there is no console to print to."""
    if sys.stderr:
        print(text, file=sys.stderr)
        return
    try:
        import tkinter as tk
        from tkinter import messagebox
        window = tk.Tk()
        window.withdraw()
        messagebox.showerror("Turnloom Studio", text)
        window.destroy()
    except Exception:
        pass


def launch(workspace=None, port=0, mode="window"):
    """Start Studio. ``mode`` is "window" (native window, falling back to a browser), "browser" or "none"."""
    if mode == "window":
        from turnloom import desktop
        try:
            return desktop.launch(workspace)
        except (OSError, ValueError) as error:
            report(f"Studio could not start: {error}")
            return 1
    root = workspace or choose_folder()
    if not root:
        return 0
    try:
        server, url = make_server(Path(root), port=port)
    except (OSError, ValueError) as error:
        report(f"Studio could not start: {error}")
        return 1
    with server:
        if sys.stdout:
            print(f"Turnloom Studio {__version__} for {root}\n{url}\nIt stops when you choose Close Studio, press Ctrl+C, "
                  f"or leave it unused for {IDLE_SECONDS // 60} minutes.", flush=True)
        if mode == "browser":
            open_browser(url)
        watch_idle(server)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
    return 0


def main():
    parser = argparse.ArgumentParser(prog="turnloom-studio", description="Launch Turnloom Studio")
    parser.add_argument("--workspace", type=Path, help="Project directory. Omit to choose one in a dialog")
    parser.add_argument("--browser", action="store_true", help="Open in the default browser instead of an app window")
    parser.add_argument("--no-open", action="store_true", help="Print the session URL and open nothing, for remote shells")
    parser.add_argument("--port", type=int, default=0, help="Loopback port for --browser and --no-open, default a free one")
    args = parser.parse_args()
    return launch(args.workspace, args.port, "none" if args.no_open else "browser" if args.browser else "window")


if __name__ == "__main__":
    raise SystemExit(main())
