"""Loopback workbench for one operator and one workspace."""

import argparse
import hashlib
import json
import os
import secrets
import shutil
import sqlite3
import subprocess
import sys
import sysconfig
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
from turnloom.agent import Config, run_agent
from turnloom.cli import instructions
from turnloom.context import Observations
from turnloom.extensions import McpTools


IGNORE = {".git", ".turnloom", ".venv", "node_modules", "__pycache__", ".ssh", ".aws"}
LIMIT = 512 * 1024
KINDS = {".html": "text/html; charset=utf-8", ".js": "text/javascript", ".css": "text/css", ".svg": "image/svg+xml",
         ".png": "image/png", ".ico": "image/x-icon", ".txt": "text/plain; charset=utf-8", ".woff2": "font/woff2"}
DEFAULTS = {"budget": 0.0, "fold": True, "keep": 3, "reply": 8192, "effort": "", "mode": "solo", "auto_approve": False,
            "cache": True, "servers": [], "gate": "confirm",
            "team": [{"name": "plan", "persona": "engineering-software-architect",
                      "instruction": "Inspect the task and identify the changes and checks needed. Leave files unchanged."},
                     {"name": "build", "persona": "builder",
                      "instruction": "Implement the task in the shared workspace and verify the result."},
                     {"name": "review", "persona": "engineering-code-reviewer",
                      "instruction": "Inspect the actual changes and check the original requirements. Report findings and verification."}]}
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
    if spec and spec.get("pass_env"):
        wrapped["pass_env"] = spec["pass_env"]
    return wrapped


def companion_tools(workspace):
    if not companions_installed():
        raise ValueError("Install turnloom[studio] and restart to enable Unwind and VerifyDoc.")
    ledger = workspace / ".turnloom" / "unwind.db"
    ledger.parent.mkdir(exist_ok=True)
    return McpTools(None, workspace, servers={"verifydoc": through_unwind(ledger)}, startup_timeout=90)


class StudioArchive(Observations):
    def __init__(self):
        super().__init__()
        self.lock = threading.RLock()
        self.rows = []

    def fold(self, messages, keep=3, tool="recall"):
        with self.lock:
            removed = super().fold(messages, keep, tool)
            names = {call["id"]: call["function"]["name"] for message in messages
                     for call in (message.get("tool_calls") or [])}
            rows = []
            for message in messages:
                if message["role"] != "tool":
                    continue
                content = message["content"]
                try:
                    item = json.loads(content)
                except ValueError:
                    item = {}
                ref = item.get("reference") if isinstance(item, dict) and item.get("tool") == "recall" else None
                if not isinstance(ref, str) or ref not in self.records:
                    ref = None
                size = len(self.records[ref]) if ref else len(content)
                rows.append({"id": message["tool_call_id"], "tool": names.get(message["tool_call_id"], "tool"),
                             "before": size, "after": len(content), "reference": ref})
            self.rows = rows
            return removed

    def snapshot(self):
        with self.lock:
            return self.rows.copy()


def sample_archive():
    archive = StudioArchive()
    logs = [("tests", "\n".join(f"PASS parser case {index:03d} · 4 assertions" for index in range(160))),
            ("build", "\n".join(f"Compiled module {index:03d} in 12 ms" for index in range(100))),
            ("search", "\n".join(f"src/parser.py:{index}: token matched" for index in range(35))),
            ("status", "Working tree clean"), ("lint", "No issues found"), ("verify", "6 suites passed")]
    messages = []
    for name, output in logs:
        messages.extend([{"role": "assistant", "tool_calls": [{"id": name, "function": {"name": name}}]},
                         {"role": "tool", "tool_call_id": name, "content": output}])
    archive.fold(messages)
    return archive


class Workbench:
    def __init__(self, root):
        self.root = root.resolve(strict=True)
        if not self.root.is_dir():
            raise ValueError("Choose a workspace directory.")
        self.lock = threading.Condition()
        self.busy = False
        self.messages = []
        self.snapshot_number = 0
        self.history = []
        self.archive = StudioArchive()
        self.sample = sample_archive()
        self.pending = None
        self.decision = False
        self.risk = None
        self.verdict = None
        self.stop = threading.Event()
        self.trace = self.root / ".turnloom" / (uuid4().hex + ".jsonl")
        self.catalog = {}
        self.persona = "builder"
        self.ledger = meter.Ledger(self.root)
        self.steps = []
        self.context = {}
        self.stages = []
        self.mcp = None
        self.mcp_error = ""
        self.unwound = set()
        self.settings = json.loads(json.dumps(DEFAULTS))
        try:
            saved = json.loads((self.root / ".turnloom" / "studio.json").read_text(encoding="utf-8"))
            self.configure(saved, persist=False)
        except (OSError, ValueError, KeyError, TypeError):
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

    # ---- workspace files -------------------------------------------------

    def path(self, name):
        relative = Path(name)
        if relative.is_absolute() or any(part in IGNORE or part == ".." for part in relative.parts):
            raise ValueError("Path outside the workspace view.")
        path = (self.root / relative).resolve(strict=True)
        if not path.is_relative_to(self.root) or any(part in IGNORE for part in path.relative_to(self.root).parts):
            raise ValueError("Path outside the workspace view.")
        return path

    def edit(self, action, data):
        path = self.path(data["path"])
        if action == "tree":
            entries = sorted((p for p in path.iterdir() if p.name not in IGNORE and not p.is_symlink()),
                             key=lambda p: (not p.is_dir(), p.name.casefold()))
            return [{"name": p.name, "dir": p.is_dir(), "path": p.relative_to(self.root).as_posix()} for p in entries]
        if not path.is_file() or path.stat().st_size > LIMIT:
            raise ValueError("The editor supports UTF-8 text files up to 512 KiB.")
        raw = path.read_bytes()
        if b"\x00" in raw:
            raise ValueError("This file contains binary data.")
        version = hashlib.sha256(raw).hexdigest()
        if action == "save":
            if version != data["version"]:
                raise ValueError("The file changed on disk. Reload it before saving.")
            content = data["text"].replace("\r\n", "\n")
            if b"\r\n" in raw:
                content = content.replace("\n", "\r\n")
            raw = content.encode("utf-8")
            if len(raw) > LIMIT:
                raise ValueError("File exceeds the editor limit.")
            path.write_bytes(raw)
            version = hashlib.sha256(raw).hexdigest()
        return {"text": raw.decode("utf-8"), "version": version}

    def git(self, *arguments):
        executable = shutil.which("git")
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
            if not any(part in IGNORE for part in Path(name).parts):
                rows.append({"path": name, "status": "added" if "?" in code or "A" in code else
                             "deleted" if "D" in code else "renamed" if "R" in code else "modified"})
        return {"repository": True, "files": rows[:400]}

    def change(self, name):
        relative = Path(name)
        if relative.is_absolute() or any(part in IGNORE or part == ".." for part in relative.parts):
            raise ValueError("Path outside the workspace view.")
        shown = self.git("show", "HEAD:" + relative.as_posix())
        original = shown.stdout if shown.returncode == 0 else b""
        target = self.root / relative
        modified = target.read_bytes() if target.is_file() and target.resolve().is_relative_to(self.root) else b""
        if max(len(original), len(modified)) > LIMIT or b"\x00" in original or b"\x00" in modified:
            raise ValueError("This change is binary or larger than the 512 KiB review limit.")
        return {"path": relative.as_posix(), "original": original.decode("utf-8", "replace").replace("\r\n", "\n"),
                "modified": modified.decode("utf-8", "replace").replace("\r\n", "\n")}

    # ---- approvals -------------------------------------------------------

    def approve(self, command):
        with self.lock:
            if self.stop.is_set():
                return False
            if self.settings["auto_approve"]:
                self.risk = self.verdict = None
                return True
            self.pending = {"command": command, "id": uuid4().hex, "kind": "mcp" if command.startswith("MCP ") else "shell",
                            "risk": self.risk, "policy": self.verdict}
            self.risk = self.verdict = None
            self.decision = False
            self.lock.wait_for(lambda: self.pending is None, timeout=180)
            self.pending = None
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

    def inspect(self, params):
        action = params["action"]
        path = self.path(params.get("path", "."))
        relative = path.relative_to(self.root)
        if any(part.startswith(".") for part in relative.parts):
            raise ValueError("Use an approved command to inspect hidden files.")
        if action == "list":
            items = [e for e in self.edit("tree", {"path": str(relative)}) if not e["name"].startswith(".")]
            return {"entries": [e["name"] + ("/" if e["dir"] else "") for e in items[:120]], "total": len(items)}
        if action == "read":
            line = max(1, int(params.get("line", 1)))
            lines = self.edit("read", {"path": str(relative)})["text"].splitlines()
            numbered = (f"{index}: {text}" for index, text in enumerate(lines[line-1:line+79], line))
            return {"line": line, "total_lines": len(lines), "text": "\n".join(numbered)[:10000]}
        if action == "search":
            rg = shutil.which("rg", path=getattr(sys, "_MEIPASS", None) or sysconfig.get_path("scripts")) or shutil.which("rg")
            if not rg:
                raise ValueError("Install ripgrep for workspace search, or use list and read.")
            query = params["query"]
            if not isinstance(query, str) or not query.strip() or len(query) > 512:
                raise ValueError("Enter a search string of 1 to 512 characters.")
            args = [rg, "--no-config", "--fixed-strings", "--files-with-matches", "--max-filesize", "512K"]
            for folder in sorted(IGNORE | {"dist", "build"}):
                args.extend(["--glob", "!" + folder + "/**", "--glob", "!**/" + folder + "/**"])
            with tempfile.TemporaryFile() as output:
                try:
                    result = subprocess.run([*args, "--", query, str(relative)], cwd=self.root, stdout=output,
                                            stderr=subprocess.DEVNULL, timeout=8,
                                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                except subprocess.TimeoutExpired as error:
                    raise ValueError("Search timed out. Select a narrower directory.") from error
                if result.returncode not in (0, 1):
                    raise ValueError("ripgrep could not search this path.")
                output.seek(0)
                return {"paths": output.read(8000).decode("utf-8", "replace").splitlines()[:100]}
        raise ValueError("Unknown workspace operation.")

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
            spec["args"] = [str(value).replace("${configDir}", str(path.parent)) for value in spec.get("args", [])]
            if guarded and find_spec("unwind"):
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
                    raise ValueError("No MCP servers. Install turnloom[studio] or add .turnloom/mcp.json to the workspace.")
                (self.root / ".turnloom").mkdir(exist_ok=True)
                manager = McpTools(None, self.root, servers=specs, startup_timeout=90)
                manager.__enter__()
                manager.before_approval = self.assess
                self.mcp, self.unwound = manager, unwound
            self.mcp.enabled = {name for name in self.mcp.servers if name in self.settings["servers"]}
            return self.mcp

    def close(self):
        with self.lock:
            manager, self.mcp = self.mcp, None
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
        return {"companions": companions_installed(), "config": ".turnloom/mcp.json", "servers": [
            {"name": name, "command": Path(spec["command"]).name + " " + " ".join(spec.get("args", []))[:160],
             "enabled": name in self.settings["servers"], "unwind": name in unwound, "bundled": name == "verifydoc",
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
            item = json.loads(payload)
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
        if self.busy:
            raise ValueError("Wait for the current turn to finish.")
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
        for key in ("fold", "auto_approve", "cache"):
            if key in data:
                settings[key] = data[key] is True
        if data.get("effort") in ("", "minimal", "low", "medium", "high"):
            settings["effort"] = data["effort"]
        if data.get("mode") in ("solo", "team"):
            settings["mode"] = data["mode"]
        if data.get("gate") in ("confirm", "policy", "off"):
            settings["gate"] = data["gate"]
        if isinstance(data.get("servers"), list):
            settings["servers"] = [name for name in data["servers"] if isinstance(name, str)][:32]
        if isinstance(data.get("team"), list):
            team = []
            for stage in data["team"][:8]:
                name = str(stage["name"]).strip()[:24]
                roster.find(stage["persona"], self.root)
                if not name or any(name == other["name"] for other in team):
                    raise ValueError("Give each team stage a unique name.")
                team.append({"name": name, "persona": stage["persona"], "instruction": str(stage["instruction"]).strip()[:2000]})
            if not team:
                raise ValueError("A team needs at least one stage.")
            settings["team"] = team
        if persist:
            target = self.root / ".turnloom" / "studio.json"
            target.parent.mkdir(exist_ok=True)
            target.write_text(json.dumps(settings, indent=1), encoding="utf-8")
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

    def tool_result(self, name, arguments, result):
        try:
            params = json.loads(arguments)
        except ValueError:
            params = {}
        params = params if isinstance(params, dict) else {}
        outcome = json.loads(result)
        label = params.get("command") or " ".join(str(params[k]) for k in ("action", "path", "query", "server", "name") if k in params) or name
        failed = isinstance(outcome, dict) and bool(outcome.get("error") or outcome.get("isError") or outcome.get("exit_code"))
        blocked = outcome.get("rule") or "policy" if isinstance(outcome, dict) and outcome.get("blocked_by") == "provio" else ""
        with self.lock:
            self.messages.append({"role": "tool", "text": result[:6000], "name": name, "label": str(label)[:240],
                                  "failed": failed, "blocked": blocked})

    def prompt(self, role):
        return (instructions(role, None, self.root) + f"\nYour workspace root is {self.root.name!r}. "
                "For inspection use workspace list, read, and search first. Paths are relative to the root. "
                f"The shell runs {'Windows cmd.exe' if os.name == 'nt' else '/bin/sh'}. "
                "After a failed operation choose another approach. For a broad review, sample a few relevant files, "
                "then report concrete findings with file and line evidence and clearly state the coverage.")

    def start(self, data):
        task = data["task"].strip()
        role = roster.find(data["persona"], self.root)["id"]
        if not task or len(task) > 32000 or not self.connection:
            raise ValueError("Connect a model and enter a task.")
        with self.lock:
            if self.busy:
                raise ValueError("An agent turn is already running.")
            settings = dict(self.settings)
            team = settings["mode"] == "team"
            if "companions" in data:
                wanted = set(settings["servers"]) | {"verifydoc"} if data["companions"] is True else set(settings["servers"]) - {"verifydoc"}
                settings["servers"] = self.settings["servers"] = sorted(wanted)
            tools = None
            if settings["servers"]:
                try:
                    tools = self.tools()
                    tools = tools if tools.enabled else None
                except ValueError:
                    if data.get("companions") is True:
                        raise
            gate = None
            if self.gate_state()["active"]:
                gate = guard.Gate(self.root, approve=self.approve, confirm=settings["gate"] == "confirm",
                                  session=self.trace.stem, notify=self.note_verdict)
            self.stop.clear()
            if self.persona != role or team:
                self.history.clear()
                self.archive = StudioArchive()
            self.persona = role
            conn = self.connection
            priced = self.priced()
            current = {"persona": role}
            config = Config(conn.endpoint, conn.model, conn.key, self.root, transcript=self.trace,
                            system_prompt=self.prompt(role), approve=self.approve, workspace_tool=self.inspect,
                            max_steps=12, tool_limit=18, stop_requested=self.stop.is_set,
                            input_rate=conn.input_rate, output_rate=conn.output_rate, cache_rate=conn.cache_rate,
                            on_tool_event=self.tool_result, mcp=tools, gate=gate, context_tokens=self.window(),
                            max_reply_tokens=min(settings["reply"], self.window() // 2),
                            reasoning_effort=settings["effort"] or None, fold_history=settings["fold"],
                            fold_keep=settings["keep"], cache_prompt=settings["cache"],
                            budget_usd=settings["budget"] if priced and (conn.free or (conn.input_rate and conn.output_rate)) else 0,
                            on_step=lambda event: self.record_step(event, current["persona"]))
            self.messages.append({"role": "user", "text": task})
            self.stages = [{"name": stage["name"], "persona": stage["persona"], "status": "queued"}
                           for stage in settings["team"]] if team else []
            self.busy = True

        def on_stage(name, status, result):
            with self.lock:
                for stage in self.stages:
                    if stage["name"] == name:
                        stage["status"] = status
                        current["persona"] = stage["persona"]
                        if result is not None:
                            self.messages.append({"role": "assistant", "text": result.answer or " ".join(result.errors) or status,
                                                  "status": status, "stage": name, "persona": stage["persona"],
                                                  "usage": result.usage, "cost": result.estimated_usd if priced else None})

        def run():
            try:
                if team:
                    from turnloom.team import run_team
                    stages = [(stage["name"], stage["instruction"], self.prompt(stage["persona"])) for stage in settings["team"]]
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
            except Exception as error:
                message = {"role": "error", "text": str(error)}
            if gate:
                try:
                    gate.close()
                except Exception:
                    pass
            with self.lock:
                self.messages.append(message)
                if message.get("status") not in ("completed", "partial") or team:
                    self.history.clear()
                    if not team:
                        self.archive = StudioArchive()
                self.busy = False
        threading.Thread(target=run, daemon=True).start()
        return True

    def reset(self):
        with self.lock:
            if self.busy:
                raise ValueError("Wait for the current turn to finish.")
            self.messages, self.history, self.steps, self.context, self.stages = [], [], [], {}, []
            self.archive = StudioArchive()
            self.trace = self.root / ".turnloom" / (uuid4().hex + ".jsonl")
        return True

    def describe(self):
        conn = self.connection
        if not conn:
            return None
        return {"model": conn.model, "provider": conn.provider, "input": conn.input_rate, "output": conn.output_rate,
                "cache": conn.cache_rate, "context": conn.context, "free": conn.free, "priced": self.priced(),
                "gateway": conn.endpoint.startswith(gateway.SCHEME)}

    def dispatch(self, action, data):
        if action == "recall":
            archive = self.sample if data.get("sample") is True else self.archive
            with archive.lock:
                return json.loads(archive.recall(json.dumps(data)))
        if action in ("tree", "read", "save"):
            with self.lock:
                return self.edit(action, data)
        if action == "state":
            with self.lock:
                offset = int(data.get("since", 0))
                if not 0 <= offset <= len(self.messages):
                    offset = 0
                self.snapshot_number += 1
                totals = {key: sum(step[key] or 0 for step in self.steps)
                          for key in ("prompt_tokens", "completion_tokens", "cached_tokens", "cost", "saved_tokens")}
                last = self.steps[-1] if self.steps else None
                return {"workspace": self.root.name, "model": self.connection.model if self.connection else "",
                        "connection": self.describe(), "version": __version__,
                        "busy": self.busy, "stopping": self.busy and self.stop.is_set(),
                        "pending": self.pending, "messages": self.messages[offset:],
                        "offset": offset, "snapshot": self.snapshot_number,
                        "loom": self.archive.snapshot() if self.messages else self.sample.snapshot(),
                        "sample": not self.messages, "persona": self.persona, "settings": self.settings,
                        "stages": self.stages, "companions": companions_installed(), "gate": self.gate_state(),
                        "meter": {**totals, "steps": len(self.steps), "priced": self.priced(), "window": self.window(),
                                  "prompt": last["prompt_tokens"] if last else 0, "context": self.context,
                                  "spark": [step["prompt_tokens"] for step in self.steps[-40:]]}}
        if action == "run":
            return self.start(data)
        if action == "reset":
            return self.reset()
        if action == "stop":
            with self.lock:
                if self.busy:
                    self.stop.set()
                    self.pending = None
                    self.decision = False
                    self.lock.notify_all()
            return True
        if action == "decision":
            with self.lock:
                if not self.pending or self.pending["id"] != data["id"]:
                    raise ValueError("Approval has expired.")
                self.decision = data["approve"] is True
                self.pending = None
                self.lock.notify_all()
            return True
        if action == "personas":
            return {"source": roster.upstream()["source"], "commit": roster.upstream()["commit"],
                    "featured": roster.FEATURED, "personas": [{key: row[key] for key in
                    ("id", "name", "description", "division", "emoji", "color", "vibe")} for row in roster.catalog(self.root)]}
        if action == "persona":
            row = roster.find(data["id"], self.root)
            return {"id": row["id"], "name": row["name"], "text": roster.body(row["id"], self.root)[:40000] or
                    "Turnloom's base instructions: read the task, inspect before editing, make the smallest correct change, verify."}
        if action == "settings":
            with self.lock:
                if self.busy:
                    raise ValueError("Wait for the current turn to finish.")
                return self.configure(data)
        if action == "usage":
            return self.usage()
        if action == "policy":
            return self.policy()
        if action == "policy_create":
            guard.create_policy(self.root)
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
            adapters = ["text-search"]
            return {"schemas": SCHEMAS, "adapters": adapters, "installed": companions_installed()}
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
                     "group": "Popular" if key in providers.POPULAR else "OpenAI-compatible"}
                    for key in dict.fromkeys(providers.POPULAR + sorted(providers.PROVIDERS))]
            if gateway.available():
                for key, name, count in gateway.providers():
                    variable = gateway.key_variable(key)
                    rows.append({"id": key, "name": name, "requiresKey": bool(variable), "group": "Native APIs via LiteLLM",
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
            if provider == "openrouter" and data.get("browser") and not key:
                from turnloom.openrouter import login
                key = login()
            if variable and not key:
                raise ValueError("Supply a provider key or sign in with OpenRouter.")
            if provider == "openrouter":
                providers.fetch(endpoint + "/key", key)
            with self.lock:
                if self.busy:
                    raise ValueError("Wait for the current turn to finish.")
                row = self.catalog.get(provider, {}).get(data["model"])
                if not row:
                    raise ValueError("Refresh models and choose one.")
                if key and (data.get("key") or data.get("browser")):
                    providers.vault().set_password("turnloom:" + provider, "api-key", key)
                providers.store_profile({"provider": provider, **row})
                self.connection = providers.connection(provider, row, key)
                self.history.clear()
                self.archive = StudioArchive()
            return True
        raise ValueError("Unknown action.")


def choose_folder():
    import tkinter as tk
    from tkinter.filedialog import askdirectory
    window = tk.Tk()
    window.withdraw()
    window.attributes("-topmost", True)
    chosen = askdirectory(title="Open workspace in Turnloom Studio", mustexist=True)
    window.destroy()
    return chosen or None


def make_server(root, chooser=None, desktop=False):
    holder = {"bench": Workbench(root)}
    token = secrets.token_urlsafe(32)
    assets = files("turnloom").joinpath("studio_assets")
    names = {item.name for item in assets.iterdir() if Path(item.name).suffix in KINDS}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def reply(self, code, body, kind):
            self.send_response(code)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Content-Security-Policy", "default-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; font-src 'self' data:; frame-ancestors 'none'; base-uri 'none'")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            name = self.path.split("?")[0].lstrip("/") or "index.html"
            if name not in names:
                return self.send_error(404)
            self.reply(200, assets.joinpath(name).read_bytes(), KINDS[Path(name).suffix])

        def do_POST(self):
            host = f"127.0.0.1:{self.server.server_port}"
            if (self.headers.get("Host") != host or self.headers.get("Origin") not in (None, "http://" + host)
                or not secrets.compare_digest(self.headers.get("Authorization", "").encode(), ("Bearer " + token).encode())):
                return self.send_error(403)
            bench = holder["bench"]
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= LIMIT * 4 or not self.path.startswith("/api/"):
                    raise ValueError("Invalid request.")
                data = json.loads(self.rfile.read(length))
                if not isinstance(data, dict):
                    raise ValueError("Expected a JSON object.")
                if self.path == "/api/close":
                    if bench.busy:
                        raise ValueError("Wait for the current turn to finish.")
                    threading.Thread(target=self.server.shutdown, daemon=True).start()
                    result = True
                elif self.path == "/api/workspace":
                    if bench.busy:
                        raise ValueError("Wait for the current turn to finish.")
                    chosen = (chooser or choose_folder)()
                    if chosen:
                        replacement = Workbench(Path(chosen))
                        replacement.connection = bench.connection
                        holder["bench"] = replacement
                        bench.close()
                    result = {"changed": bool(chosen)}
                else:
                    result = bench.dispatch(self.path[5:], data)
                    if self.path == "/api/state":
                        result["desktop"] = desktop
                self.reply(200, json.dumps(result).encode(), "application/json")
            except ConnectionError:
                return
            except (ValueError, OSError, RuntimeError, KeyError, TypeError) as error:
                self.reply(400, json.dumps({"error": str(error)}).encode(), "application/json")

    class Server(ThreadingHTTPServer):
        daemon_threads = True

        def server_close(self):
            super().server_close()
            holder["bench"].close()

    server = Server(("127.0.0.1", 0), Handler)
    server.holder = holder
    return server, f"http://127.0.0.1:{server.server_port}/#{token}"


def main():
    parser = argparse.ArgumentParser(description="Launch Turnloom Studio")
    parser.add_argument("--workspace", type=Path)
    parser.add_argument("--browser", action="store_true", help="Open in the default browser instead of an app window")
    args = parser.parse_args()
    if not args.browser:
        from turnloom import desktop
        return desktop.launch(args.workspace)
    root = args.workspace or choose_folder()
    if not root:
        return 0
    server, url = make_server(Path(root))
    with server:
        webbrowser.open(url)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
