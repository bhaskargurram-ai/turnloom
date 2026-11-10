import contextlib
import io
import json
import os
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from unittest.mock import patch

from turnloom.cli import instructions, main
from turnloom.context import Observations


class SessionTests(unittest.TestCase):
    def test_fold_and_recall_preserve_tool_pair(self):
        archive = Observations()
        message = {"role": "tool", "tool_call_id": "first", "content": "abcdefghij" * 200}
        history = [message] + [{"role": "tool", "content": "recent"} for _ in range(3)]
        self.assertGreater(archive.fold(history), 1000)
        self.assertEqual(message["tool_call_id"], "first")
        key = json.loads(message["content"])["reference"]
        result = archive.recall(json.dumps({"reference": key, "offset": 9, "length": 4}))
        self.assertEqual(json.loads(result)["text"], "jabc")
        self.assertEqual(archive.fold(history), 0)

    def test_specialists_load_individually(self):
        self.assertIn("Software Architect", instructions("architect", None))
        self.assertIn("Code Reviewer Agent", instructions("reviewer", None))
        self.assertNotIn("Code Reviewer Agent", instructions("builder", None))

    def test_cli_through_http_shell_and_replay(self):
        requests = []

        class Provider(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                requests.append(payload)
                message = {"role": "assistant", "content": "Verified."}
                if len(requests) == 1:
                    message.update(content=None, tool_calls=[{
                        "id": "probe", "type": "function",
                        "function": {"name": "shell", "arguments": '{"command":"echo turnloom-test"}'},
                    }])
                reply = {"choices": [{"message": message, "finish_reason": "stop"}],
                         "usage": {"prompt_tokens": 100, "completion_tokens": 20}}
                self.send_response(200)
                self.end_headers()
                self.wfile.write(json.dumps(reply).encode())

        with HTTPServer(("127.0.0.1", 0), Provider) as server:
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                settings = {"HARNESS_ENDPOINT": f"http://127.0.0.1:{server.server_port}/v1",
                            "HARNESS_MODEL": "fixture", "HARNESS_API_KEY": "test-only"}
                with tempfile.TemporaryDirectory(dir=Path.cwd()) as workspace, patch.dict(os.environ, settings):
                    output = io.StringIO()
                    with contextlib.redirect_stdout(output), contextlib.redirect_stderr(io.StringIO()):
                        code = main(["run", "Verify shell", "--workspace", workspace, "--yes", "--json",
                                     "--input-rate", "1", "--output-rate", "4", "--budget", "0.005"])
                        self.assertEqual(code, 0)
                        result = json.loads(output.getvalue())
                        self.assertAlmostEqual(result["estimated_usd"], 0.00036)
                        self.assertIn("turnloom-test", requests[1]["messages"][-1]["content"])
                        self.assertLess(requests[0]["max_tokens"], 4096)
                        trace, = Path(workspace).glob(".turnloom/*.jsonl")
                        self.assertEqual(main(["replay", str(trace)]), 0)
                        self.assertIn("Verified.", output.getvalue())
                        self.assertEqual(main(["run", "Stop early", "--workspace", workspace, "--yes",
                                               "--input-rate", "1", "--output-rate", "4", "--budget", "0.000001"]), 1)
                        self.assertEqual(len(requests), 2)
            finally:
                server.shutdown()
                thread.join()
