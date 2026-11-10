"""Start a disposable Studio workspace for the Playwright smoke test."""

import json
import tempfile
from pathlib import Path
from unittest.mock import patch

from turnloom import providers
from turnloom.studio import make_server


def model(config, messages, deadline, allow_tools=True):
    if not allow_tools:
        return {"choices": [{"message": {"content": "Reviewed available evidence."}}]}
    last_user = max(i for i, message in enumerate(messages) if message["role"] == "user")
    index = sum(item["role"] == "tool" for item in messages[last_user:])
    if messages[last_user]["content"] == "Verify receipt":
        if index == 2:
            verified = "n_accepted" in messages[-1]["content"]
            return {"choices": [{"message": {"content": "Receipt verified." if verified else "Verification failed."}}]}
        name = "mcp_search" if index == 0 else "mcp_call"
        params = {"server": "verifydoc", "query": "verify_extraction"} if index == 0 else {
            "server": "verifydoc", "name": "verify_extraction", "arguments": {"document": "Total: 42",
             "schema": json.dumps({"type": "object", "properties": {"total": {"type": "number"}}})}}
        call = {"type": "function", "id": str(index), "function": {"name": name, "arguments": json.dumps(params)}}
        return {"choices": [{"message": {"tool_calls": [call]}}]}
    command = "echo " + ("x" * 900 if index == 0 else f"check-{index}")
    call = {"type": "function", "id": str(index), "function": {"name": "shell",
            "arguments": json.dumps({"command": command})}}
    return {"choices": [{"message": {"tool_calls": [call]}}]}


if __name__ == "__main__":
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        (root / "README.md").write_text("Studio workspace", encoding="utf-8")
        with patch.object(providers, "resolve", return_value=providers.Connection("local", "Browser fixture", "")), \
             patch("turnloom.agent.api_request", side_effect=model):
            server, url = make_server(root)
            print(url, flush=True)
            with server:
                server.serve_forever()
