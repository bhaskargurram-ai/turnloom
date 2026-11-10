"""Expose local Turnloom receipts to other MCP clients."""

import json
import re
from pathlib import Path


def serve(workspace):
    try:
        from mcp.server.fastmcp import FastMCP
    except ImportError as error:
        raise ValueError("Install Turnloom with the mcp extra.") from error
    server = FastMCP("Turnloom receipts")
    root = workspace.resolve() / ".turnloom"

    @server.tool()
    def sessions() -> list[str]:
        """List the most recent 100 local Turnloom session IDs."""
        if root.is_symlink():
            raise ValueError("Trace directory must be inside the workspace")
        paths = (path for path in root.glob("*.jsonl") if re.fullmatch("[a-f0-9]{32}", path.stem)
                 and path.is_file() and path.resolve().parent == root)
        return [path.stem for path in sorted(paths, key=lambda item: item.stat().st_mtime, reverse=True)[:100]]

    @server.tool()
    def receipt(session_id: str) -> dict:
        """Aggregate statuses, tokens and recorded cost across completed turns of one session."""
        if not re.fullmatch("[a-f0-9]{32}", session_id):
            raise ValueError("Invalid session ID")
        path = root / (session_id + ".jsonl")
        if root.is_symlink() or path.resolve().parent != root:
            raise ValueError("Trace must be inside the configured workspace")
        totals = {"turns": 0, "prompt_tokens": 0, "completion_tokens": 0, "estimated_usd": 0.0, "statuses": {}}
        with path.open(encoding="utf-8") as stream:
            for line in stream:
                if not line.endswith("\n"):
                    break
                event = json.loads(line)
                if event["type"] == "end":
                    result = event["result"]
                    totals["turns"] += 1
                    for key in ("prompt_tokens", "completion_tokens"):
                        totals[key] += result["usage"].get(key, 0)
                    totals["estimated_usd"] += result.get("estimated_usd", 0)
                    status = result["status"]
                    totals["statuses"][status] = totals["statuses"].get(status, 0) + 1
        return totals

    server.run(transport="stdio")
