"""Workspace file tools: confined reads, searches and reviewable, reversible edits."""

from __future__ import annotations

import difflib
import fnmatch
import json
import os
import re
import shutil
import subprocess
import sys
import sysconfig
import tempfile
import time
from pathlib import Path

IGNORE = {".git", ".turnloom", ".venv", "venv", "node_modules", "__pycache__", ".ssh", ".aws", ".gnupg"}
SECRETS = (".env", ".env.*", "*.pem", "*.key", "*.p12", "*.pfx", "id_rsa*", "id_ed25519*", "id_ecdsa*",
           ".netrc", ".npmrc", ".pypirc", ".git-credentials", "credentials.json", "secrets.*")
READ_LIMIT = 2 * 1024 * 1024
PROTECTED = "Protected path. Ask the operator to approve a shell command if this file is needed."


def canonical(name):
    """Windows opens `.env.` and `.env ` as `.env`, so compare the name the filesystem will use."""
    return (name.rstrip(". ") if os.name == "nt" else name).casefold()


def protected(name):
    lowered = canonical(name)
    if lowered in ("secrets.py", "secrets.md", ".env.example", ".env.sample", ".env.template"):
        return False
    return any(fnmatch.fnmatchcase(lowered, pattern) for pattern in SECRETS)


def schema(name, description, properties, required):
    return {"type": "function", "function": {"name": name, "description": description, "parameters": {
        "type": "object", "properties": properties, "required": required}}}


PATH = {"type": "string", "description": "Workspace-relative path"}
READ_TOOLS = [
    schema("list_files", "List a directory, or find files by glob such as **/*.py. Directories end with a slash.",
           {"path": {**PATH, "description": "Workspace-relative directory, default ."},
            "glob": {"type": "string", "description": "Optional glob relative to path"}}, []),
    schema("read_file", "Read a text file as numbered lines. Read before editing.",
           {"path": PATH, "line": {"type": "integer", "minimum": 1, "description": "First line, default 1"},
            "limit": {"type": "integer", "minimum": 1, "maximum": 2000, "description": "Line count, default 250"}}, ["path"]),
    schema("search_files", "Search file contents and return path:line: text matches. Literal by default.",
           {"query": {"type": "string"}, "path": {**PATH, "description": "Directory or file to search, default ."},
            "regex": {"type": "boolean", "description": "Treat query as a regular expression"},
            "glob": {"type": "string", "description": "Only search files matching this glob, such as *.py"}}, ["query"]),
]
WRITE_TOOLS = [
    schema("edit_file", "Replace old_string with new_string in a file. old_string must match exactly once unless "
           "replace_all is true, so include enough surrounding lines to be unique. Preferred for changing existing files.",
           {"path": PATH, "old_string": {"type": "string"}, "new_string": {"type": "string"},
            "replace_all": {"type": "boolean"}}, ["path", "old_string", "new_string"]),
    schema("write_file", "Create a file or replace its whole content. Use edit_file for targeted changes.",
           {"path": PATH, "content": {"type": "string"}}, ["path", "content"]),
]
NAMES = {item["function"]["name"] for item in READ_TOOLS + WRITE_TOOLS}


def find_program(name, workspace=None):
    """Locate an executable the way a shell would, except that the current directory and the
    workspace are never searched.

    On Windows a bare program name resolves in the current directory first, so a repository that
    ships `rg.cmd` or `git.cmd` would otherwise have it run in place of the real tool.
    """
    folders = [getattr(sys, "_MEIPASS", None), sysconfig.get_path("scripts"), *os.environ.get("PATH", "").split(os.pathsep)]
    suffixes = [item for item in os.environ.get("PATHEXT", ".COM;.EXE;.BAT;.CMD").split(";") if item] if os.name == "nt" else [""]
    trusted = Path(sysconfig.get_path("scripts")).resolve()
    barred = [Path.cwd().resolve()] + ([Path(workspace).resolve()] if workspace else [])
    for folder in folders:
        if not folder or not os.path.isabs(folder):
            continue
        try:
            base = Path(folder).resolve()
        except OSError:
            continue
        if base != trusted and any(base == item or base.is_relative_to(item) for item in barred):
            continue
        for suffix in suffixes:
            candidate = base / (name + suffix)
            if candidate.is_file() and (os.name == "nt" or os.access(candidate, os.X_OK)):
                return str(candidate)
    return None


def system_program(name):
    """A Windows system tool by its full path, so a same-named file elsewhere can never stand in for it."""
    return str(Path(os.environ.get("SystemRoot") or r"C:\Windows") / "System32" / name)


def write_atomic(path, data):
    """Replace a file in one step so a crash never leaves it half written."""
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(dir=path.parent, prefix=".turnloom-", suffix=".tmp")
    try:
        with os.fdopen(handle, "wb") as stream:
            stream.write(data)
        try:
            shutil.copymode(path, temporary)
        except OSError:
            if os.name != "nt":
                # A new file gets ordinary permissions, not the private mode of a temporary file.
                umask = os.umask(0)
                os.umask(umask)
                os.chmod(temporary, 0o666 & ~umask)
        os.replace(temporary, path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


def uses_crlf(text):
    """True only when every line ending is CRLF, so mixed files are left exactly as they are."""
    return "\r\n" in text and text.count("\r\n") == text.count("\n")


def unified(before, after, name, limit=12000):
    lines = difflib.unified_diff(before.splitlines(), after.splitlines(), "a/" + name, "b/" + name, lineterm="", n=3)
    text = "\n".join(lines)
    return text if len(text) <= limit else text[:limit] + f"\n...[{len(text) - limit} diff characters omitted]"


def tolerant(text, old, new):
    """Locate old line-by-line ignoring indentation and trailing space, then re-indent new to fit."""
    wanted = [line.strip() for line in old.strip("\n").split("\n")]
    lines = text.split("\n")
    if not any(wanted):
        return None
    hits = [start for start in range(len(lines) - len(wanted) + 1)
            if [line.strip() for line in lines[start:start + len(wanted)]] == wanted]
    if len(hits) != 1:
        return None
    start = hits[0]
    source = old.strip("\n").split("\n")
    anchor = next((index for index, line in enumerate(source) if line.strip()), 0)
    actual = lines[start + anchor][:len(lines[start + anchor]) - len(lines[start + anchor].lstrip())]
    given = source[anchor][:len(source[anchor]) - len(source[anchor].lstrip())]
    replacement = []
    for line in new.strip("\n").split("\n"):
        if not line.strip():
            replacement.append("")
        elif line.startswith(given):
            replacement.append(actual + line[len(given):])
        else:
            replacement.append(actual + line.lstrip())
    return "\n".join(lines[:start] + replacement + lines[start + len(wanted):])


class Workspace:
    """File access for one workspace root. Every path is resolved and checked before use."""

    def __init__(self, root, output_chars=16000):
        try:
            self.root = Path(root).resolve(strict=True)
        except OSError as error:
            raise ValueError(f"Workspace does not exist: {root}") from error
        if not self.root.is_dir():
            raise ValueError(f"Workspace is not a directory: {root}")
        self.output_chars = output_chars
        self.changes = []
        self.turn = 0

    def hidden(self, part, operator=False):
        """Ignored folders are closed to everyone. Secret-looking files stay open to the operator's own editor."""
        return canonical(part) in IGNORE or (not operator and protected(part))

    def resolve(self, name, exists=True, operator=False):
        if not isinstance(name, str) or not name.strip() or "\x00" in name:
            raise ValueError("Provide a workspace-relative path.")
        relative = Path(name)
        if relative.is_absolute() or relative.drive or relative.root or ".." in relative.parts:
            raise ValueError("Path outside the workspace.")
        if os.name == "nt" and any(":" in part for part in relative.parts):
            raise ValueError("Path outside the workspace.")
        if any(self.hidden(part, operator) for part in relative.parts):
            raise ValueError(PROTECTED)
        target = self.root / relative
        probe = target
        while not probe.exists() and probe != self.root:
            if exists:
                raise ValueError(f"No such file or directory: {name}")
            probe = probe.parent
        real = probe.resolve(strict=True)
        if not real.is_relative_to(self.root):
            raise ValueError("Path outside the workspace.")
        final = real / target.relative_to(probe) if probe != target else real
        if any(self.hidden(part, operator) for part in final.relative_to(self.root).parts):
            raise ValueError(PROTECTED)
        return final

    def name(self, path):
        return path.relative_to(self.root).as_posix() or "."

    def inside(self, path):
        """Whether a path's real location is in the workspace. Links and Windows junctions that lead out are not."""
        try:
            return Path(os.path.realpath(path)).is_relative_to(self.root)
        except (OSError, ValueError):
            return False

    def visible(self, path, operator=False):
        return not self.hidden(path.name, operator) and not path.is_symlink() and self.inside(path)

    def entries(self, name=".", operator=False):
        folder = self.resolve(name, operator=operator)
        if not folder.is_dir():
            raise ValueError(f"Not a directory: {name}")
        found = sorted((item for item in folder.iterdir() if self.visible(item, operator)),
                       key=lambda item: (not item.is_dir(), item.name.casefold()))
        return [{"name": item.name, "dir": item.is_dir(), "path": self.name(item)} for item in found]

    def walk(self, folder):
        for current, folders, names in os.walk(folder):
            base = Path(current)
            folders[:] = sorted(item for item in folders if self.visible(base / item))
            for item in sorted(names):
                if self.visible(base / item):
                    yield base / item

    def list_files(self, path=".", glob=None):
        if not glob:
            items = self.entries(path)
            return {"entries": [item["name"] + ("/" if item["dir"] else "") for item in items[:300]], "total": len(items)}
        if not isinstance(glob, str) or len(glob) > 200:
            raise ValueError("glob must be a short pattern such as **/*.py")
        folder = self.resolve(path)
        pattern = glob.replace("\\", "/")
        matches = []
        for item in self.walk(folder):
            relative = item.relative_to(folder).as_posix()
            if fnmatch.fnmatchcase(relative, pattern) or fnmatch.fnmatchcase(item.name, pattern) or (
                    pattern.startswith("**/") and fnmatch.fnmatchcase(relative, pattern[3:])):
                matches.append(self.name(item))
                if len(matches) > 300:
                    break
        return {"paths": matches[:300], "truncated": len(matches) > 300}

    def text(self, path, strict=False):
        if not path.is_file():
            raise ValueError(f"Not a file: {self.name(path)}")
        if path.stat().st_size > READ_LIMIT:
            raise ValueError("File exceeds the 2 MiB text limit. Use search_files or a shell command for a slice.")
        raw = path.read_bytes()
        if b"\x00" in raw:
            raise ValueError("This file contains binary data.")
        try:
            return raw.decode("utf-8")
        except UnicodeDecodeError as error:
            if strict:
                raise ValueError("File is not UTF-8 text. Edit it with a shell command.") from error
            return raw.decode("utf-8", errors="replace")

    def read_file(self, path, line=1, limit=250):
        target = self.resolve(path)
        lines = self.text(target).removeprefix("﻿").splitlines()
        first = max(1, int(line))
        count = min(2000, max(1, int(limit)))
        body, shown = [], 0
        size = 0
        for number, text in enumerate(lines[first - 1:first - 1 + count], first):
            row = f"{number}: {text}"
            size += len(row) + 1
            if size > self.output_chars:
                break
            body.append(row)
            shown += 1
        result = {"path": self.name(target), "line": first, "total_lines": len(lines), "text": "\n".join(body)}
        if first - 1 + shown < len(lines):
            result["next_line"] = first + shown
        return result

    def search_files(self, query, path=".", regex=False, glob=None, permit=None):
        """Search file contents. ``permit(path)`` may veto individual files, as a policy gate does."""
        if not isinstance(query, str) or not query.strip() or len(query) > 512:
            raise ValueError("Enter a search string of 1 to 512 characters.")
        if glob is not None and (not isinstance(glob, str) or len(glob) > 200 or glob.startswith("!")):
            raise ValueError("glob must be a short pattern such as *.py")
        target = self.resolve(path)
        if regex:
            try:
                re.compile(query)
            except re.error as error:
                raise ValueError(f"Invalid regular expression: {error}") from error
        rg = find_program("rg", self.root)
        lines = self.ripgrep(rg, query, target, regex, glob) if rg else None
        engine = "ripgrep"
        if lines is None:
            lines, engine = self.scan(query, target, regex, glob), "python"
        verdicts = {}

        def allowed(row):
            name = row.split(":", 1)[0]
            if name not in verdicts:
                location = self.root / name
                verdicts[name] = self.inside(location) and (permit is None or bool(permit(location)))
            return verdicts[name]
        lines = [row for row in lines if allowed(row)]
        text, kept = [], 0
        for row in lines[:200]:
            kept += len(row) + 1
            if kept > self.output_chars:
                break
            text.append(row)
        return {"matches": text, "count": len(text), "truncated": len(text) < len(lines), "engine": engine}

    def ripgrep(self, rg, query, target, regex, glob):
        args = [rg, "--no-config", "--line-number", "--no-heading", "--color", "never", "--max-columns", "300",
                "--max-columns-preview", "--max-filesize", "2M", "--max-count", "50", "--sort", "path",
                "--hidden", "--path-separator", "/", "--glob-case-insensitive"]
        if not regex:
            args.append("--fixed-strings")
        if glob:
            args.extend(["--glob", glob])
        # Later globs win in ripgrep, so the exclusions follow the caller's pattern.
        for folder in sorted(IGNORE):
            args.extend(["--glob", "!**/" + folder + "/**"])
        for pattern in SECRETS:
            args.extend(["--glob", "!**/" + pattern])
        try:
            done = subprocess.run([*args, "--", query, self.name(target)], cwd=self.root, capture_output=True,
                                  timeout=15, stdin=subprocess.DEVNULL)
        except (subprocess.TimeoutExpired, OSError):
            return None
        if done.returncode not in (0, 1):
            return None
        rows = done.stdout[:400000].decode("utf-8", errors="replace").splitlines()
        if target.is_file():
            return [self.name(target) + ":" + row for row in rows]
        return [row.removeprefix("./") for row in rows]

    def scan(self, query, target, regex, glob):
        matcher = re.compile(query if regex else re.escape(query))
        deadline = time.monotonic() + 15
        rows = []
        for item in ([target] if target.is_file() else self.walk(target)):
            if time.monotonic() > deadline or len(rows) > 200:
                break
            if glob and not (fnmatch.fnmatchcase(item.name, glob) or fnmatch.fnmatchcase(self.name(item), glob)):
                continue
            try:
                if item.stat().st_size > READ_LIMIT:
                    continue
                raw = item.read_bytes()
            except OSError:
                continue
            if b"\x00" in raw:
                continue
            found = 0
            for number, line in enumerate(raw.decode("utf-8", errors="replace").splitlines(), 1):
                if matcher.search(line):
                    rows.append(f"{self.name(item)}:{number}:{line[:300]}")
                    found += 1
                    if found >= 50:
                        break
        return rows

    def plan(self, name, arguments):
        """Compute a write without touching disk. Returns (path, before, after, replacements)."""
        target = self.resolve(arguments["path"], exists=False)
        if target.is_dir():
            raise ValueError(f"Path is a directory: {arguments['path']}")
        existing = target.exists()
        before = self.text(target, strict=True) if existing else None
        if name == "write_file":
            content = arguments["content"]
            if not isinstance(content, str):
                raise ValueError("content must be a string")
            after, count = content, 1
            if existing and uses_crlf(before) and "\r\n" not in content:
                after = content.replace("\n", "\r\n")
        else:
            old, new = arguments["old_string"], arguments["new_string"]
            if not isinstance(old, str) or not isinstance(new, str):
                raise ValueError("old_string and new_string must be strings")
            if not existing:
                raise ValueError(f"No such file: {arguments['path']}. Use write_file to create it.")
            if old == new:
                raise ValueError("old_string and new_string are identical.")
            if not old:
                raise ValueError("old_string is empty. Use write_file to replace the whole file.")
            crlf = uses_crlf(before)
            body = before.replace("\r\n", "\n") if crlf else before
            old, new = old.replace("\r\n", "\n"), new.replace("\r\n", "\n")
            count = body.count(old)
            if count == 0:
                body = tolerant(body, old, new)
                if body is None:
                    raise ValueError("old_string was not found. Read the file again and copy the exact lines, "
                                     "including indentation.")
                count = 1
            elif count > 1 and arguments.get("replace_all") is not True:
                raise ValueError(f"old_string matches {count} places. Add surrounding lines to make it unique, "
                                 "or set replace_all.")
            else:
                body = body.replace(old, new)
            after = body.replace("\n", "\r\n") if crlf else body
        if len(after.encode("utf-8")) > READ_LIMIT:
            raise ValueError("Result exceeds the 2 MiB text limit.")
        if before == after:
            raise ValueError("The file already has this content.")
        return target, before, after, count

    def apply(self, name, arguments, approve=None):
        target, before, after, count = self.plan(name, arguments)
        label = self.name(target)
        diff = unified(before or "", after, label)
        if approve and not approve("edit", ("Create " if before is None else "Edit ") + label + "\n" + diff):
            return {"error": "Operator declined the edit"}
        if (self.text(target, strict=True) if target.exists() else None) != before:
            raise ValueError("The file changed while awaiting approval. Read it again.")
        write_atomic(target, after.encode("utf-8"))
        self.changes.append({"path": label, "before": before, "after": after, "turn": self.turn})
        added = sum(1 for line in diff.splitlines() if line.startswith("+") and not line.startswith("+++"))
        removed = sum(1 for line in diff.splitlines() if line.startswith("-") and not line.startswith("---"))
        return {"path": label, "created": before is None, "replacements": count, "added": added, "removed": removed,
                "diff": unified(before or "", after, label, 4000)}

    def pending(self, turn=None, since=None):
        """Net change per file for one turn (default: the latest turn with edits), or for every
        turn after ``since`` when a single operator turn spans several agent runs."""
        if since is None and turn is None:
            turn = max((item["turn"] for item in self.changes), default=None)
        merged = {}
        for item in self.changes:
            if item["turn"] > since if since is not None else item["turn"] == turn:
                merged.setdefault(item["path"], {"path": item["path"], "before": item["before"]})["after"] = item["after"]
        return turn, list(merged.values())

    def undo(self, turn=None, since=None):
        """Restore files edited in a turn when they still hold the agent's version."""
        turn, files = self.pending(turn, since)
        restored, conflicts = [], []
        for item in files:
            target = self.resolve(item["path"], exists=False)
            try:
                current = self.text(target, strict=True) if target.exists() else None
            except ValueError:
                current = False
            if current != item["after"]:
                conflicts.append(item["path"])
                continue
            if item["before"] is None:
                target.unlink()
            else:
                write_atomic(target, item["before"].encode("utf-8"))
            restored.append(item["path"])
        def undone(item):
            return item["turn"] > since if since is not None else item["turn"] == turn
        self.changes = [item for item in self.changes if not undone(item) or item["path"] in conflicts]
        return {"restored": restored, "conflicts": conflicts}

    def dispatch(self, name, raw, approve=None, read_only=False, permit=None):
        try:
            arguments = json.loads(raw) if isinstance(raw, str) else raw
            if not isinstance(arguments, dict):
                raise ValueError("Tool arguments must be a JSON object.")
            if name == "list_files":
                return self.list_files(arguments.get("path") or ".", arguments.get("glob"))
            if name == "read_file":
                return self.read_file(arguments["path"], arguments.get("line") or 1, arguments.get("limit") or 250)
            if name == "search_files":
                return self.search_files(arguments["query"], arguments.get("path") or ".",
                                         arguments.get("regex") is True, arguments.get("glob"), permit)
            if name in ("edit_file", "write_file"):
                if read_only:
                    return {"error": "This session is read-only. Describe the change instead of applying it."}
                return self.apply(name, arguments, approve)
            return {"error": f"Unknown tool: {name}"}
        except KeyError as error:
            return {"error": f"Missing argument: {error.args[0]}"}
        except (ValueError, TypeError, OSError) as error:
            return {"error": str(error)}
