import json
import io
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from turnloom.evaluate import bootstrap_interval, macro_score
from turnloom.compare import paired_difference
from turnloom.agent import Config, api_request, run_agent, run_shell, shorten


class HarnessTests(unittest.TestCase):
    def test_agent_executes_tool_and_finishes(self):
        config = Config("https://example.test/v1", "test-model", "secret", Path(__file__).parent)
        responses = iter([
            {"choices": [{"message": {"role": "assistant", "content": None, "tool_calls": [{
                "id": "call-1", "type": "function", "function": {"name": "shell", "arguments": json.dumps({"command": "echo ready"})},
            }]}}], "usage": {"prompt_tokens": 12, "completion_tokens": 7}},
            {"choices": [{"message": {"role": "assistant", "content": "Done"}}], "usage": {"prompt_tokens": 15, "completion_tokens": 3}},
        ])
        sent_messages = []

        def fake_request(_config, messages, _deadline):
            sent_messages.append(json.loads(json.dumps(messages)))
            return next(responses)

        with patch("turnloom.agent.api_request", side_effect=fake_request):
            result = run_agent("Check the shell", config)
        self.assertEqual(result.status, "completed")
        self.assertEqual(result.answer, "Done")
        self.assertEqual(result.usage["prompt_tokens"], 27)
        self.assertEqual(sent_messages[1][-1]["role"], "tool")
        self.assertIn("ready", sent_messages[1][-1]["content"])

    def test_invalid_shell_arguments_are_returned_to_model(self):
        config = Config("https://example.test/v1", "test-model", "secret", Path(__file__).parent)
        result = json.loads(run_shell("{}", config, float("inf")))
        self.assertIn("error", result)

    def test_api_request_uses_chat_completions_and_tool_schema(self):
        config = Config("https://example.test/v1", "test-model", "secret", Path(__file__).parent)
        response = io.BytesIO(b'{"choices": [{"message": {"role": "assistant", "content": "ok"}}]}')
        with patch("urllib.request.urlopen", return_value=response) as urlopen:
            api_request(config, [{"role": "user", "content": "hello"}], time.monotonic() + 5)
        request = urlopen.call_args.args[0]
        payload = json.loads(request.data)
        self.assertEqual(request.full_url, "https://example.test/v1/chat/completions")
        self.assertEqual(payload["tools"][0]["function"]["name"], "shell")
        self.assertEqual(request.get_header("Authorization"), "Bearer secret")

    def test_output_keeps_head_and_tail(self):
        output = shorten("a" * 50 + "z" * 50, 20)
        self.assertTrue(output.startswith("a" * 10))
        self.assertTrue(output.endswith("z" * 10))
        self.assertIn("omitted", output)

    def test_macro_weights_benchmarks_equally(self):
        rows = [
            {"benchmark": "one", "task_id": "a", "passed": True},
            {"benchmark": "two", "task_id": "b", "passed": False},
            {"benchmark": "two", "task_id": "c", "passed": False},
        ]
        self.assertEqual(macro_score(rows), 0.5)
        lower, upper = bootstrap_interval(rows, samples=100)
        self.assertEqual((lower, upper), (0.5, 0.5))

    def test_paired_difference_keeps_benchmark_weights(self):
        baseline = {"one": {"a": [0, 0, 0]}, "two": {"b": [1, 1, 1], "c": [1, 1, 1]}}
        candidate = {"one": {"a": [1, 1, 1]}, "two": {"b": [1, 1, 1], "c": [1, 1, 1]}}
        report = paired_difference(baseline, candidate, samples=100)
        self.assertEqual(report["macro_difference"], 0.5)
        self.assertEqual(report["ci_95"], [0.5, 0.5])


if __name__ == "__main__":
    unittest.main()
