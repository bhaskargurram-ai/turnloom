"""Serve Turnloom to other agents over MCP: receipts, archived context, and optional task delegation."""

import json
import re
from uuid import uuid4

SESSION = "[a-f0-9]{32}"


def serve(workspace, allow_run=False, allow_edits=False, allow_shell=False):
    try:
        from mcp.server.fastmcp import FastMCP
    except ImportError as error:
        raise ValueError("Install Turnloom with the mcp extra: pip install 'turnloom[mcp]'") from error
    if (allow_edits or allow_shell) and not allow_run:
        raise ValueError("--allow-edits and --allow-shell extend --allow-run. Pass --allow-run as well.")
    base = workspace.resolve()
    root = base / ".turnloom"
    server = FastMCP("Turnloom", instructions="Read Turnloom session receipts and transcripts for this workspace"
                     + (", or delegate a coding task to its agent with run_task." if allow_run else "."))

    def trace(session_id):
        if not re.fullmatch(SESSION, session_id):
            raise ValueError("Invalid session ID")
        path = root / (session_id + ".jsonl")
        if root.is_symlink() or not path.is_file() or path.resolve().parent != root:
            raise ValueError("Unknown session in this workspace")
        return path

    def events(session_id):
        with trace(session_id).open(encoding="utf-8") as stream:
            for line in stream:
                if not line.endswith("\n"):
                    break
                try:
                    yield json.loads(line)
                except ValueError:
                    continue

    @server.tool()
    def sessions() -> list[str]:
        """List the most recent 100 local Turnloom session IDs."""
        if root.is_symlink() or not root.is_dir():
            return []
        paths = (path for path in root.glob("*.jsonl") if re.fullmatch(SESSION, path.stem)
                 and path.is_file() and path.resolve().parent == root)
        return [path.stem for path in sorted(paths, key=lambda item: item.stat().st_mtime, reverse=True)[:100]]

    @server.tool()
    def receipt(session_id: str) -> dict:
        """Aggregate statuses, tokens and recorded cost across completed turns of one session."""
        totals = {"turns": 0, "prompt_tokens": 0, "completion_tokens": 0, "estimated_usd": 0.0, "statuses": {}}
        for event in events(session_id):
            if event.get("type") == "end":
                result = event["result"]
                totals["turns"] += 1
                for key in ("prompt_tokens", "completion_tokens"):
                    totals[key] += result["usage"].get(key, 0)
                totals["estimated_usd"] += result.get("estimated_usd", 0)
                status = result["status"]
                totals["statuses"][status] = totals["statuses"].get(status, 0) + 1
        return totals

    @server.tool()
    def transcript(session_id: str, offset: int = 0, limit: int = 40) -> dict:
        """Page through a session: tasks, tool calls with short result previews, and answers."""
        rows = []
        for event in events(session_id):
            kind = event.get("type")
            if kind == "start":
                rows.append({"type": "task", "text": event.get("task", "")})
            elif kind == "assistant":
                for call in event["message"].get("tool_calls") or []:
                    rows.append({"type": "call", "id": call["id"], "tool": call["function"]["name"],
                                 "arguments": call["function"]["arguments"][:400]})
            elif kind == "tool":
                text = json.dumps(event["result"], ensure_ascii=False)
                rows.append({"type": "result", "id": event["id"], "characters": len(text), "preview": text[:300]})
            elif kind == "end":
                rows.append({"type": "answer", "status": event["result"]["status"], "text": event["result"].get("answer", "")})
        start = max(0, offset)
        return {"total": len(rows), "offset": start, "events": rows[start:start + min(200, max(1, limit))]}

    @server.tool()
    def read_result(session_id: str, tool_call_id: str, offset: int = 0, length: int = 2000) -> dict:
        """Read a slice of one tool result from a saved session, whether or not it was folded out of the prompt."""
        for event in events(session_id):
            if event.get("type") == "tool" and event.get("id") == tool_call_id:
                text = json.dumps(event["result"], ensure_ascii=False)
                start = max(0, offset)
                return {"text": text[start:start + min(8000, max(1, length))], "total": len(text), "offset": start}
        raise ValueError("No tool result with that ID in this session")

    from turnloom.context import Observations
    archive = Observations(capacity=20_000_000)

    @server.tool()
    def archive_output(content: str) -> dict:
        """Park a long tool result outside the conversation. Returns a reference and a short preview to keep instead."""
        reference = archive.save(content)
        if not reference:
            raise ValueError("Content exceeds the archive capacity")
        return {"reference": reference, "preview": content[:180], "characters": len(content)}

    @server.tool()
    def recall(reference: str, offset: int = 0, length: int = 1600, search: str = "") -> dict:
        """Read archived content by reference: a slice of at most 8000 characters, or the lines around a search string."""
        return json.loads(archive.recall(json.dumps({"reference": reference, "offset": offset, "length": length,
                                                     "search": search})))

    if allow_run:
        from turnloom import providers
        from turnloom.agent import Config, run_agent
        from turnloom import roster
        from turnloom.cli import instructions

        def policy(kind, detail):
            return (kind == "edit" and allow_edits) or (kind == "shell" and allow_shell)

        @server.tool()
        def run_task(task: str, persona: str = "builder", max_steps: int = 30) -> dict:
            """Give Turnloom's agent a task in this workspace using the operator's connected model.
            It can read and search files. Edits and shell commands work only if the operator enabled them when
            starting the server. Returns the answer, token usage, files changed and the session ID for receipts."""
            roster.find(persona, base)
            if not task.strip():
                raise ValueError("task is empty")
            connection = providers.resolve()
            session_id = uuid4().hex
            config = Config(connection.endpoint, connection.model, connection.key, base,
                            system_prompt=instructions(persona, None, base), transcript=root / (session_id + ".jsonl"),
                            context_tokens=connection.context or 128000, max_reply_tokens=4096,
                            cache_rate=connection.cache_rate,
                            max_steps=min(100, max(1, max_steps)), approve=policy,
                            read_only=not (allow_edits or allow_shell),
                            input_rate=connection.input_rate, output_rate=connection.output_rate)
            result = run_agent(task, config)
            return {"session_id": session_id, "status": result.status, "answer": result.answer, "errors": result.errors,
                    "usage": result.usage, "estimated_usd": result.estimated_usd, "files_changed": result.files_changed}

    server.run(transport="stdio")
