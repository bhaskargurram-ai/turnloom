"""Agent Skills and operator-selected MCP servers over stdio or streamable HTTP."""

import json
import os
import re
import threading
from contextlib import ExitStack, asynccontextmanager
from datetime import timedelta

SKILL_FOLDERS = (".agents/skills", ".claude/skills")


def front_matter(lines, path):
    """Parse SKILL.md metadata with PyYAML when installed, else as plain `key: value` lines."""
    try:
        import yaml
    except ImportError:
        meta = {}
        for line in lines:
            key, separator, value = line.partition(":")
            if separator and key.strip() and not line[:1].isspace():
                meta[key.strip()] = value.strip().strip("\"'")
        return meta
    try:
        return yaml.safe_load("\n".join(lines))
    except yaml.YAMLError as error:
        raise ValueError(f"Invalid YAML in {path}") from error


def skills(root):
    if not root.is_dir():
        raise ValueError(f"Missing skills directory: {root}")
    catalog = {}
    for path in sorted(root.glob("*/SKILL.md")):
        if not path.resolve().is_relative_to(root.resolve()):
            raise ValueError(f"Skill resolves outside its root: {path}")
        raw = path.read_text(encoding="utf-8-sig")
        lines = raw.splitlines()
        if not lines or lines[0] != "---" or "---" not in lines[1:]:
            raise ValueError(f"Missing skill front matter: {path}")
        end = lines.index("---", 1)
        meta = front_matter(lines[1:end], path)
        if not isinstance(meta, dict) or meta.get("name") != path.parent.name or not isinstance(meta.get("description"), str) \
                or not meta["description"]:
            raise ValueError(f"Invalid skill name or description: {path}")
        catalog[meta["name"]] = (meta["description"], path.resolve(), "\n".join(lines[end + 1:]))
    return catalog


def project_skills(workspace):
    """Describe skills checked into the project so the model can read one when a task calls for it."""
    rows = []
    for folder in SKILL_FOLDERS:
        root = workspace / folder
        if not root.is_dir():
            continue
        try:
            found = skills(root)
        except (ValueError, OSError):
            continue
        for name, (description, _, _) in found.items():
            rows.append(f"- {name}: {' '.join(description.split())[:300]} ({folder}/{name}/SKILL.md)")
    if not rows:
        return ""
    return ("Project skills\nThese instructions ship with the project. When a task matches one, read its SKILL.md "
            "with read_file before starting.\n" + "\n".join(rows[:40]))


@asynccontextmanager
async def connect(spec, workspace, startup_timeout=20):
    from mcp import ClientSession, StdioServerParameters

    wait = timedelta(seconds=startup_timeout)
    if "url" in spec:
        from mcp.client.streamable_http import streamablehttp_client
        headers = {}
        if spec.get("bearer_env"):
            token = os.environ.get(spec["bearer_env"])
            if not token:
                raise ValueError(f"Set {spec['bearer_env']} to authenticate with this server.")
            headers["Authorization"] = "Bearer " + token
        async with streamablehttp_client(spec["url"], headers=headers or None) as (read, write, _):
            async with ClientSession(read, write, read_timeout_seconds=wait) as session:
                await session.initialize()
                yield session
        return
    from mcp.client.stdio import stdio_client
    env = {name: os.environ[name] for name in spec.get("pass_env", []) if name in os.environ}
    params = StdioServerParameters(command=spec["command"], args=spec.get("args", []), cwd=str(workspace), env=env)
    async with stdio_client(params) as streams:
        async with ClientSession(*streams, read_timeout_seconds=wait) as session:
            await session.initialize()
            yield session


class McpTools:
    def __init__(self, path, workspace, *, servers=None, startup_timeout=20):
        self.startup_timeout = startup_timeout
        try:
            data = {"mcpServers": servers} if servers is not None else json.loads(path.read_text(encoding="utf-8-sig"))
        except ValueError as error:
            raise ValueError(f"MCP config is not valid JSON: {path}") from error
        self.servers = data.get("mcpServers") if isinstance(data, dict) else None
        if not isinstance(self.servers, dict) or not self.servers:
            raise ValueError("MCP config requires a nonempty mcpServers object.")
        for name, spec in self.servers.items():
            if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", name) or not isinstance(spec, dict):
                raise ValueError(f"Invalid MCP server: {name}")
            if "url" in spec:
                if set(spec) - {"url", "bearer_env"} or not isinstance(spec["url"], str) \
                        or not re.match(r"https?://", spec["url"]) or not isinstance(spec.get("bearer_env", ""), str):
                    raise ValueError(f"Invalid HTTP configuration for {name}. Use url and optionally bearer_env.")
                continue
            if set(spec) - {"command", "args", "pass_env"} or not isinstance(spec.get("command"), str) or not spec["command"]:
                raise ValueError(f"Invalid stdio configuration for {name}")
            for field in ("args", "pass_env"):
                values = spec.get(field, [])
                if not isinstance(values, list) or not all(isinstance(item, str) for item in values):
                    raise ValueError(f"{name}.{field} must be an array of strings")
            if path is not None:
                spec["args"] = [value.replace("${configDir}", str(path.resolve().parent)) for value in spec.get("args", [])]
        self.workspace = workspace
        self.enabled = set(self.servers)
        self.before_approval = None
        self.lock = threading.RLock()
        self.sessions = {}
        self.catalogs = {}
        self.stack = ExitStack()

    def __enter__(self):
        try:
            from anyio.from_thread import start_blocking_portal
        except ImportError as error:
            raise ValueError("Install Turnloom with the mcp extra: pip install 'turnloom[mcp]'") from error
        self.portal = self.stack.enter_context(start_blocking_portal())
        return self

    def __exit__(self, *exc):
        return self.stack.__exit__(*exc)

    def schema(self):
        server = {"type": "string", "enum": [name for name in self.servers if name in self.enabled]}
        def tool(name, description, properties):
            return {"type": "function", "function": {"name": name, "description": description,
                    "parameters": {"type": "object", "properties": properties, "required": list(properties)}}}
        return [
            tool("mcp_search", "List a configured server's tools. Returns every matching tool name and the input "
                 "schemas of the five best matches. An empty query lists all tools.",
                 {"server": server, "query": {"type": "string"}}),
            tool("mcp_call", "Call a discovered server tool with arguments matching its input schema.",
                 {"server": server, "name": {"type": "string"}, "arguments": {"type": "object"}}),
        ]

    def session(self, server):
        if server not in self.sessions:
            manager = self.portal.wrap_async_context_manager(
                connect(self.servers[server], self.workspace, self.startup_timeout))
            self.sessions[server] = self.stack.enter_context(manager)
        if server not in self.catalogs:
            self.catalogs[server] = self.portal.call(self._list, self.sessions[server])
        return self.sessions[server]

    def tools(self, server):
        """Every tool a server advertises, starting the server on first use."""
        with self.lock:
            self.session(server)
            return [{"name": item.name, "description": (item.description or "")[:700], "inputSchema": item.inputSchema}
                    for item in self.catalogs[server]]

    def direct(self, server, name, arguments, timeout=30):
        """Call a tool for the operator, outside a model turn. The caller is the approval."""
        with self.lock:
            try:
                result = self.portal.call(self.session(server).call_tool, name, arguments, timedelta(seconds=timeout))
            except Exception as error:
                self.forget(server, error)
                return {"error": f"{type(error).__name__}: {error}"}
            return {"isError": result.isError, "content": [part.text if part.type == "text" else
                    {"type": part.type} for part in result.content], "structuredContent": result.structuredContent}

    def forget(self, server, error):
        """Drop a broken connection so the next call starts the server again."""
        if server in self.sessions and not isinstance(error, (ValueError, TypeError)):
            self.sessions.pop(server, None)
            self.catalogs.pop(server, None)

    def dispatch(self, operation, raw, approve, remaining=30):
        with self.lock:
            return self._dispatch(operation, raw, approve, remaining)

    def _dispatch(self, operation, raw, approve, remaining):
        server = None
        try:
            params = json.loads(raw)
            server = params["server"]
            if server not in self.servers:
                return {"error": "Unknown server. Configured servers: " + ", ".join(self.servers)}
            if server not in self.enabled:
                return {"error": f"Server {server} is not enabled for this turn"}
            if remaining <= 0:
                return {"error": "Task deadline expired"}
            if operation == "mcp_search":
                self.session(server)
                query = str(params.get("query") or "").casefold()
                matches = [item for item in self.catalogs[server] if query in
                           (item.name + " " + (item.description or "")).casefold()]
                matches.sort(key=lambda item: (item.name.casefold() != query, query not in item.name.casefold(), item.name))
                return {"count": len(matches), "names": [item.name for item in matches][:200], "tools": [
                    {"name": item.name, "description": (item.description or "")[:700], "inputSchema": item.inputSchema}
                    for item in matches[:5]]}
            if not isinstance(params.get("arguments"), dict):
                return {"error": "arguments must be an object"}
            if params["name"] not in {item.name for item in self.catalogs.get(server, [])}:
                return {"error": "Search this server before calling a tool"}
            if approve and self.before_approval:
                try:
                    self.before_approval(server, params["name"], params["arguments"])
                except Exception:
                    pass
            if approve and not approve(
                    "mcp", f"MCP {server} / {params['name']} {json.dumps(params['arguments'], ensure_ascii=False)}"):
                return {"error": "Operator declined the tool call"}
            result = self.portal.call(self.session(server).call_tool, params["name"], params["arguments"],
                                      timedelta(seconds=max(1, min(remaining, 300))))
            return {"isError": result.isError, "content": [part.text if part.type == "text" else
                    {"type": part.type} for part in result.content], "structuredContent": result.structuredContent}
        except KeyError as error:
            return {"error": f"Missing argument: {error.args[0]}"}
        except Exception as error:
            self.forget(server, error)
            return {"error": f"{type(error).__name__}: {error}"}

    @staticmethod
    async def _list(session):
        catalog = []
        cursor = None
        seen = set()
        while True:
            page = await session.list_tools(cursor=cursor)
            catalog.extend(page.tools)
            cursor = page.nextCursor
            if not cursor:
                return catalog
            if cursor in seen or len(seen) >= 100:
                raise ValueError("Invalid or excessive tool pagination")
            seen.add(cursor)
