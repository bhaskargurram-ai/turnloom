import importlib.util
import json
import sys
import tempfile
import unittest
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
            def request(config, messages, deadline):
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
                config = Config("unused", "fixture", "", root, mcp=hub)
                with patch("turnloom.agent.api_request", side_effect=request):
                    result = run_agent("Inspect the run", config)
                self.assertEqual(result.status, "completed", result.errors)
                denied = hub.dispatch("mcp_call", '{"server":"local","name":"receipt","arguments":{}}', lambda _: False)
                self.assertIn("declined", denied["error"])


@unittest.skipUnless(importlib.util.find_spec("langgraph"), "Install the team extra")
class WorkflowTests(unittest.TestCase):
    def test_team_preserves_task_and_shares_usage(self):
        from turnloom.team import run_team
        prompts = []
        def model(config, messages, deadline):
            prompts.append((config.budget_usd, messages[-1]["content"]))
            return {"choices": [{"message": {"content": f"stage {len(prompts)}"}}],
                    "usage": {"prompt_tokens": 100, "completion_tokens": 20}}
        config = Config("unused", "fixture", "", Path.cwd(), input_rate=1, output_rate=2, budget_usd=0.1)
        with patch("turnloom.agent.api_request", side_effect=model):
            result = run_team("Fix parser", config)
        self.assertEqual(result.status, "completed")
        self.assertEqual(result.usage["prompt_tokens"], 300)
        self.assertLess(prompts[-1][0], prompts[0][0])
        self.assertIn("build: stage 2", prompts[-1][1])
