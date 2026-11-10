"""Loopback workbench for one operator and one workspace."""

import argparse
import hashlib
import json
import os
import secrets
import shutil
import subprocess
import sys
import sysconfig
import tempfile
import threading
import webbrowser
from contextlib import nullcontext
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.resources import files
from pathlib import Path
from uuid import uuid4

from turnloom import providers
from turnloom.agent import Config, run_agent
from turnloom.cli import SPECIALISTS, instructions
from turnloom.context import Observations
from turnloom.extensions import McpTools


IGNORE = {".git", ".turnloom", ".venv", "node_modules", "__pycache__", ".ssh", ".aws"}
LIMIT = 512 * 1024


def companion_tools(workspace):
    scripts = sysconfig.get_path("scripts")
    proxy = shutil.which("unwind", path=scripts)
    upstream = shutil.which("verifydoc-mcp", path=scripts)
    if not proxy or not upstream:
        raise ValueError("Install turnloom[studio] and restart to enable Unwind and VerifyDoc.")
    ledger = workspace / ".turnloom" / "unwind.db"
    ledger.parent.mkdir(exist_ok=True)
    if os.name == "nt":
        spec = {"command": str(Path(sys.executable).with_name("python.exe")),
                "args": ["-I", "-m", "turnloom.unwind_stdio", str(ledger)]}
    else:
        spec = {"command": proxy, "args": ["run", "--db", str(ledger), "--", upstream]}
    return McpTools(None, workspace, servers={"verifydoc": spec}, startup_timeout=90)


class StudioArchive(Observations):
    def __init__(self):
        super().__init__()
        self.lock = threading.RLock()
        self.rows = []

    def fold(self, messages, keep=3):
        with self.lock:
            removed = super().fold(messages, keep)
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
        self.stop = threading.Event()
        self.trace = self.root / ".turnloom" / (uuid4().hex + ".jsonl")
        self.catalog = {}
        self.provider = None
        self.persona = "builder"
        try:
            self.connection = providers.resolve()
        except (ValueError, OSError):
            self.connection = None

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

    def approve(self, command):
        with self.lock:
            if self.stop.is_set():
                return False
            self.pending = {"command": command, "id": uuid4().hex}
            self.decision = False
            self.lock.wait_for(lambda: self.pending is None, timeout=180)
            self.pending = None
            return self.decision

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
            rg = shutil.which("rg", path=sysconfig.get_path("scripts")) or shutil.which("rg")
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
                                            stderr=subprocess.DEVNULL, timeout=8)
                except subprocess.TimeoutExpired as error:
                    raise ValueError("Search timed out. Select a narrower directory.") from error
                if result.returncode not in (0, 1):
                    raise ValueError("ripgrep could not search this path.")
                output.seek(0)
                return {"paths": output.read(8000).decode("utf-8", "replace").splitlines()[:100]}
        raise ValueError("Unknown workspace operation.")

    def tool_result(self, name, arguments, result):
        try:
            params = json.loads(arguments)
        except ValueError:
            params = {}
        params = params if isinstance(params, dict) else {}
        outcome = json.loads(result)
        label = params.get("command") or " ".join(str(params[k]) for k in ("action", "path", "query", "server", "name") if k in params) or name
        failed = isinstance(outcome, dict) and bool(outcome.get("error") or outcome.get("isError") or outcome.get("exit_code"))
        with self.lock:
            self.messages.append({"role": "tool", "text": result[:6000], "name": name, "label": str(label)[:240], "failed": failed})

    def start(self, data):
        task = data["task"].strip()
        role = data["persona"]
        if not task or len(task) > 32000 or role not in SPECIALISTS or not self.connection:
            raise ValueError("Connect a model and enter a task.")
        with self.lock:
            if self.busy:
                raise ValueError("An agent turn is already running.")
            tools = companion_tools(self.root) if data.get("companions") is True else nullcontext()
            self.stop.clear()
            if self.persona != role:
                self.history.clear()
                self.archive = StudioArchive()
            self.persona = role
            conn = self.connection
            config = Config(conn.endpoint, conn.model, conn.key, self.root, transcript=self.trace,
                            system_prompt=instructions(role, None) + f"\nYour workspace root is {self.root.name!r}. "
                            "For inspection use workspace list, read, and search first. Paths are relative to the root. "
                            f"The shell runs {'Windows cmd.exe' if os.name == 'nt' else '/bin/sh'}. "
                            "After a failed operation choose another approach. For a broad review, sample a few relevant files, "
                            "then report concrete findings with file and line evidence and clearly state the coverage.",
                            approve=self.approve, workspace_tool=self.inspect, max_steps=12, tool_limit=18,
                            stop_requested=self.stop.is_set, input_rate=conn.input_rate, output_rate=conn.output_rate,
                            on_tool_event=self.tool_result)
            self.messages.append({"role": "user", "text": task})
            self.busy = True
        def worker():
            try:
                with tools as config.mcp:
                    result = run_agent(task, config, self.history, self.archive)
                message = {"role": "assistant", "text": result.answer or " ".join(result.errors) or
                           ("Turn stopped by operator." if result.status == "stopped" else result.status),
                           "status": result.status, "note": result.errors[-1] if result.status == "partial" else "",
                           "usage": result.usage, "cost": result.estimated_usd if
                           (conn.input_rate or conn.output_rate or conn.free) else None}
            except Exception as error:
                message = {"role": "error", "text": str(error)}
            with self.lock:
                self.messages.append(message)
                if message.get("status") not in ("completed", "partial"):
                    self.history.clear()
                    self.archive = StudioArchive()
                self.busy = False
        threading.Thread(target=worker, daemon=True).start()
        return True

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
                return {"workspace": self.root.name, "model": self.connection.model if self.connection else "",
                        "busy": self.busy, "stopping": self.busy and self.stop.is_set(),
                        "pending": self.pending, "messages": self.messages[offset:],
                        "offset": offset, "snapshot": self.snapshot_number,
                        "loom": self.archive.snapshot() if self.messages else self.sample.snapshot(),
                        "sample": not self.messages}
        if action == "run":
            return self.start(data)
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
        if action == "providers":
            return [{"id": key, "name": providers.PROVIDERS[key][0], "requiresKey": bool(providers.PROVIDERS[key][2])}
                    for key in dict.fromkeys(providers.POPULAR + sorted(providers.PROVIDERS))]
        if action in ("models", "connect"):
            provider = data["provider"]
            if provider not in providers.PROVIDERS:
                raise ValueError("Unknown provider.")
            key = data.get("key") or providers.secret(provider, required=False)
            if action == "models":
                rows = providers.models(provider, key)
                with self.lock:
                    self.catalog[provider] = {row["model"]: row for row in rows}
                return rows
            if provider == "openrouter" and data.get("browser") and not key:
                from turnloom.openrouter import login
                key = login()
            if providers.PROVIDERS[provider][2] and not key:
                raise ValueError("Supply a provider key or sign in with OpenRouter.")
            if provider == "openrouter":
                providers.fetch(providers.PROVIDERS[provider][1] + "/key", key)
            with self.lock:
                if self.busy:
                    raise ValueError("Wait for the current turn to finish.")
                row = self.catalog.get(provider, {}).get(data["model"])
                if not row:
                    raise ValueError("Refresh models and choose one.")
                if key and (data.get("key") or data.get("browser")):
                    providers.vault().set_password("turnloom:" + provider, "api-key", key)
                providers.store_profile({"provider": provider, **row})
                self.connection = providers.Connection(providers.PROVIDERS[provider][1], row["model"], key,
                                                      row["input"] or 0, row["output"] or 0, row["tier"] == "free")
                self.history.clear()
                self.archive = StudioArchive()
            return True
        raise ValueError("Unknown action.")


def make_server(root):
    bench = Workbench(root)
    token = secrets.token_urlsafe(32)
    assets = files("turnloom").joinpath("studio_assets")

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
            self.send_header("Content-Security-Policy", "default-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; frame-ancestors 'none'; base-uri 'none'")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            name = self.path.lstrip("/")
            if name == "":
                name = "index.html"
            if name not in {"index.html", "studio.js", "studio.css"}:
                return self.send_error(404)
            body = assets.joinpath(name).read_bytes()
            self.reply(200, body, {"index.html": "text/html; charset=utf-8", "studio.js": "text/javascript", "studio.css": "text/css"}[name])

        def do_POST(self):
            host = f"127.0.0.1:{self.server.server_port}"
            if (self.headers.get("Host") != host or self.headers.get("Origin") not in (None, "http://" + host)
                or not secrets.compare_digest(self.headers.get("Authorization", "").encode(), ("Bearer " + token).encode())):
                return self.send_error(403)
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
                else:
                    result = bench.dispatch(self.path[5:], data)
                self.reply(200, json.dumps(result).encode(), "application/json")
            except ConnectionError:
                return
            except (ValueError, OSError, RuntimeError, KeyError, TypeError) as error:
                self.reply(400, json.dumps({"error": str(error)}).encode(), "application/json")

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    return server, f"http://127.0.0.1:{server.server_port}/#{token}"


def main():
    parser = argparse.ArgumentParser(description="Launch Turnloom Studio")
    parser.add_argument("--workspace", type=Path)
    args = parser.parse_args()
    root = args.workspace
    if root is None:
        import tkinter as tk
        from tkinter.filedialog import askdirectory
        window = tk.Tk()
        window.withdraw()
        chosen = askdirectory(title="Open workspace in Turnloom Studio", mustexist=True)
        window.destroy()
        if not chosen:
            return 0
        root = Path(chosen)
    server, url = make_server(root)
    with server:
        webbrowser.open(url)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
