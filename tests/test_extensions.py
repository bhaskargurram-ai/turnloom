import importlib.util
import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from unittest.mock import patch

from turnloom.agent import Config, run_agent
from turnloom.extensions import McpTools


@unittest.skipUnless(importlib.util.find_spec("mcp"), "Install the mcp extra")
class ConnectorTests(unittest.TestCase):
    def test_model_discovers_and_calls_live_receipt_server(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            trace = root / ".turnloom" / ("a" * 32 + ".jsonl")
            trace.parent.mkdir()
            trace.write_text(json.dumps({"type": "end", "result": {"status": "completed",
                            "usage": {"prompt_tokens": 91, "completion_tokens": 5}}}) + "\n")
            config_file = root / "tools.json"
            config_file.write_text(json.dumps({"mcpServers": {"local": {"command": sys.executable,
                                   "args": ["-m", "turnloom", "serve", "--workspace", str(root)]}}}))
            def request(config, messages, deadline, **options):
                round_number = sum(item["role"] == "tool" for item in messages)
                if round_number == 0:
                    name, args = "mcp_search", {"server": "local", "query": "receipt"}
                elif round_number == 1:
                    self.assertEqual(json.loads(messages[-1]["content"])["tools"][0]["name"], "receipt")
                    name, args = "mcp_call", {"server": "local", "name": "receipt", "arguments": {"session_id": "a" * 32}}
                else:
                    result = json.loads(messages[-1]["content"])
                    self.assertFalse(result["isError"])
                    self.assertIn("91", json.dumps(result["content"]))
                    return {"choices": [{"message": {"content": "Run inspected"}}]}
                return {"choices": [{"message": {"tool_calls": [{"id": str(round_number), "type": "function",
                         "function": {"name": name, "arguments": json.dumps(args)}}]}}]}
            with McpTools(config_file, Path.cwd()) as hub:
                config = Config("unused", "fixture", "", root, mcp=hub, auto_approve=True)
                with patch("turnloom.agent.api_request", side_effect=request):
                    result = run_agent("Inspect the run", config)
                self.assertEqual(result.status, "completed", result.errors)
                denied = hub.dispatch("mcp_call", '{"server":"local","name":"receipt","arguments":{}}',
                                      lambda kind, detail: False)
                self.assertIn("declined", denied["error"])
                listing = hub.dispatch("mcp_search", '{"server":"local","query":""}', None)
                self.assertEqual(sorted(listing["names"]),
                                 ["archive_output", "read_result", "recall", "receipt", "sessions", "transcript"])
                parked = hub.dispatch("mcp_call", json.dumps({"server": "local", "name": "archive_output",
                                                              "arguments": {"content": "needle in a long log " * 50}}), None)
                reference = json.loads(parked["content"][0])["reference"] if not parked.get("structuredContent") else \
                    (parked["structuredContent"].get("result") or parked["structuredContent"])["reference"]
                found = hub.dispatch("mcp_call", json.dumps({"server": "local", "name": "recall", "arguments": {
                    "reference": reference, "search": "needle"}}), None)
                self.assertIn("needle", json.dumps(found))
                self.assertIn("Unknown server", hub.dispatch("mcp_search", '{"server":"absent","query":""}', None)["error"])

    def test_served_agent_runs_delegated_tasks_read_only_and_exposes_its_archive(self):
        """Another MCP client delegates a task to `turnloom serve --allow-run` and reads the result back."""
        seen = []

        class Model(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                seen.append(payload)
                tools = sum(item["role"] == "tool" for item in payload["messages"])
                plan = [("read_file", {"path": "notes.txt"}), ("write_file", {"path": "hacked.txt", "content": "x"}),
                        ("shell", {"command": "echo unsafe"})]
                message = {"role": "assistant", "content": "The note says hello."}
                if tools < len(plan):
                    message = {"role": "assistant", "content": None, "tool_calls": [{"id": f"c{tools}", "type": "function",
                               "function": {"name": plan[tools][0], "arguments": json.dumps(plan[tools][1])}}]}
                body = json.dumps({"choices": [{"message": message, "finish_reason": "stop"}],
                                   "usage": {"prompt_tokens": 40, "completion_tokens": 8}}).encode()
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        with HTTPServer(("127.0.0.1", 0), Model) as model, tempfile.TemporaryDirectory() as directory:
            thread = threading.Thread(target=model.serve_forever, daemon=True)
            thread.start()
            root = Path(directory).resolve()
            (root / "notes.txt").write_text("hello\n")
            settings = {"TURNLOOM_ENDPOINT": f"http://127.0.0.1:{model.server_port}/v1", "TURNLOOM_MODEL": "fixture",
                        "TURNLOOM_API_KEY": "k", "TURNLOOM_CONFIG_DIR": str(root / "config")}
            spec = {"command": sys.executable, "args": ["-m", "turnloom", "serve", "--workspace", str(root), "--allow-run"],
                    "pass_env": [*settings, "SYSTEMROOT", "PATH"]}
            try:
                with patch.dict(os.environ, settings), McpTools(None, root, servers={"turnloom": spec}) as hub:
                    def call(name, **arguments):
                        hub.dispatch("mcp_search", '{"server":"turnloom","query":""}', None)
                        payload = json.dumps({"server": "turnloom", "name": name, "arguments": arguments})
                        outcome = hub.dispatch("mcp_call", payload, None, 60)
                        self.assertFalse(outcome.get("error") or outcome.get("isError"), outcome)
                        data = outcome["structuredContent"] or json.loads(outcome["content"][0])
                        return data["result"] if set(data) == {"result"} else data
                    done = call("run_task", task="What does notes.txt say?")
                    self.assertEqual((done["status"], done["answer"]), ("completed", "The note says hello."))
                    self.assertEqual(done["files_changed"], [])
                    self.assertFalse((root / "hacked.txt").exists())
                    offered = [tool["function"]["name"] for tool in seen[0]["tools"]]
                    self.assertEqual(offered, ["list_files", "read_file", "search_files", "recall"])
                    events = call("transcript", session_id=done["session_id"])
                    kinds = [event["type"] for event in events["events"]]
                    self.assertEqual(kinds, ["task", "call", "result", "call", "result", "call", "result", "answer"])
                    read = call("read_result", session_id=done["session_id"], tool_call_id="c0")
                    self.assertIn("1: hello", read["text"])
                    self.assertEqual(call("receipt", session_id=done["session_id"])["prompt_tokens"], 160)
            finally:
                model.shutdown()
                thread.join()

    def test_streamable_http_server_is_reachable_with_a_bearer_token(self):
        with tempfile.TemporaryDirectory() as directory:
            with socket.socket() as probe:
                probe.bind(("127.0.0.1", 0))
                port = probe.getsockname()[1]
            script = Path(directory) / "remote.py"
            script.write_text(
                "import sys\nfrom mcp.server.fastmcp import FastMCP\n"
                f"server = FastMCP('remote', host='127.0.0.1', port={port}, log_level='ERROR')\n"
                "@server.tool()\ndef shout(text: str) -> str:\n    '''Upper-case text.'''\n    return text.upper()\n"
                "server.run(transport='streamable-http')\n", encoding="utf-8")
            process = subprocess.Popen([sys.executable, str(script)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            try:
                for _ in range(100):
                    with socket.socket() as attempt:
                        if attempt.connect_ex(("127.0.0.1", port)) == 0:
                            break
                    time.sleep(0.1)
                servers = {"remote": {"url": f"http://127.0.0.1:{port}/mcp", "bearer_env": "REMOTE_TOKEN"}}
                with McpTools(None, Path(directory), servers=servers) as hub:
                    missing = hub.dispatch("mcp_search", '{"server":"remote","query":""}', None)
                    self.assertIn("REMOTE_TOKEN", missing["error"])
                with patch.dict(os.environ, {"REMOTE_TOKEN": "t"}), McpTools(None, Path(directory), servers=servers) as hub:
                    self.assertEqual(hub.dispatch("mcp_search", '{"server":"remote","query":"shout"}', None)["names"], ["shout"])
                    payload = '{"server":"remote","name":"shout","arguments":{"text":"quiet"}}'
                    self.assertIn("QUIET", json.dumps(hub.dispatch("mcp_call", payload, None, 30)))
            finally:
                process.kill()
                process.wait()


class WorkflowTests(unittest.TestCase):
    def test_team_preserves_task_shares_usage_and_enforces_read_only_stages(self):
        from turnloom.team import run_team
        prompts = []
        def model(config, messages, deadline, **options):
            prompts.append((config.budget_usd, messages[-1]["content"], config.read_only))
            return {"choices": [{"message": {"content": f"stage {len(prompts)}"}}],
                    "usage": {"prompt_tokens": 100, "completion_tokens": 20}}
        config = Config("unused", "fixture", "", Path.cwd(), input_rate=1, output_rate=2, budget_usd=0.1)
        with patch("turnloom.agent.api_request", side_effect=model):
            result = run_team("Fix parser", config)
        self.assertEqual(result.status, "completed")
        self.assertEqual(result.usage["prompt_tokens"], 300)
        self.assertLess(prompts[-1][0], prompts[0][0])
        self.assertIn("build: stage 2", prompts[-1][1])
        self.assertEqual([read_only for _, _, read_only in prompts], [True, False, True])

    def test_team_stops_when_a_stage_fails(self):
        from turnloom.team import run_team
        config = Config("unused", "fixture", "", Path.cwd())
        with patch("turnloom.agent.api_request", side_effect=RuntimeError("Model HTTP 500")) as model:
            result = run_team("Fix parser", config)
        self.assertEqual((result.status, model.call_count), ("error", 1))
        self.assertIn("plan: Model HTTP 500", result.answer)


class SkillTests(unittest.TestCase):
    def test_skills_load_with_and_without_yaml(self):
        from turnloom.extensions import skills
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "deploy").mkdir()
            lines = ["---", "name: deploy", 'description: "Ship a release"', "---", "Body text"]
            (root / "deploy" / "SKILL.md").write_text("\n".join(lines), encoding="utf-8")
            for available in (True, False):
                with self.subTest(yaml=available), patch.dict(sys.modules, {} if available else {"yaml": None}):
                    catalog = skills(root)
                    self.assertEqual(catalog["deploy"][0], "Ship a release")
                    self.assertEqual(catalog["deploy"][2], "Body text")
            (root / "broken").mkdir()
            (root / "broken" / "SKILL.md").write_text("no front matter", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "front matter"):
                skills(root)


class ConfigTests(unittest.TestCase):
    def test_server_definitions_are_validated(self):
        base = Path.cwd()
        good = {"docs": {"url": "https://example.test/mcp", "bearer_env": "DOCS_TOKEN"},
                "local": {"command": "server", "args": ["--flag"], "pass_env": ["HOME"]}}
        self.assertEqual(sorted(McpTools(None, base, servers=good).servers), ["docs", "local"])
        for bad in [{"x": {"url": "ftp://example.test"}}, {"x": {"url": "https://e.test", "command": "y"}},
                    {"x": {"command": ""}}, {"bad name": {"command": "y"}}, {"x": {"command": "y", "args": "z"}}, {}]:
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                McpTools(None, base, servers=bad)
