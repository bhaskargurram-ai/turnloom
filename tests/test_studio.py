import json
import tempfile
import threading
import unittest
from importlib.util import find_spec
from http.client import HTTPConnection
from pathlib import Path
from unittest.mock import patch

from turnloom import providers
from turnloom.studio import Workbench, companion_tools, make_server
from turnloom.agent import Config, run_agent


class StudioTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        resolution = patch.object(providers, "resolve", return_value=None)
        resolution.start()
        self.addCleanup(resolution.stop)
        self.bench = Workbench(self.root)

    def test_edited_file_checks_version_and_workspace(self):
        path = self.root / "hello.py"
        path.write_bytes(b"first\r\n")
        initial = self.bench.dispatch("read", {"path": path.name})
        changes = {"path": path.name, "text": "second\n", "version": initial["version"]}
        self.bench.dispatch("save", changes)
        self.assertEqual(path.read_bytes(), b"second\r\n")
        with self.assertRaisesRegex(ValueError, "changed on disk"):
            self.bench.dispatch("save", changes)
        with self.assertRaisesRegex(ValueError, "outside the workspace"):
            self.bench.dispatch("tree", {"path": ".."})

    def test_repo_read_includes_lines_and_blocks_hidden_files(self):
        (self.root / "app.py").write_text("a\nb\n")
        (self.root / ".env").write_text("private")
        listing = self.bench.inspect({"action": "list"})
        self.assertEqual(listing["entries"], ["app.py"])
        self.assertEqual(self.bench.inspect({"action": "read", "path": "app.py", "line": 2})["text"], "2: b")
        with self.assertRaisesRegex(ValueError, "hidden"):
            self.bench.inspect({"action": "read", "path": ".env"})

    def test_identical_tool_batch_is_bounded_and_summarized(self):
        config = Config("local", "fixture", "", self.root, workspace_tool=self.bench.inspect, tool_limit=18)
        calls = [{"id": str(i), "function": {"name": "workspace", "arguments": '{"action":"list"}'}} for i in range(63)]
        with patch("turnloom.agent.api_request", side_effect=[
            {"choices": [{"message": {"tool_calls": calls}}]},
            {"choices": [{"message": {"content": "No files found."}}]},
        ]) as request, patch.object(config, "workspace_tool", wraps=config.workspace_tool) as inspect:
            result = run_agent("Review code", config)
            self.assertEqual(inspect.call_count, 2)
            self.assertFalse(request.call_args.kwargs["allow_tools"])
            self.assertEqual(result.answer, "No files found.")

    def test_stop_wakes_pending_approval(self):
        self.bench.busy = True
        with self.bench.lock:
            result = []
            waiter = threading.Thread(target=lambda: result.append(self.bench.approve("unused")))
            waiter.start()
        import time
        for _ in range(100):
            if self.bench.dispatch("state", {})["pending"]:
                break
            time.sleep(.01)
        self.assertIsNotNone(self.bench.dispatch("state", {})["pending"])
        self.bench.dispatch("stop", {})
        waiter.join(2)
        self.assertEqual(result, [False])
        self.assertIsNone(self.bench.dispatch("state", {})["pending"])

    @unittest.skipUnless(find_spec("verifydoc") and find_spec("unwind"), "requires the studio extra")
    def test_real_verifydoc_extraction_through_unwind(self):
        with companion_tools(self.root) as tools:
            def call(operation, **params):
                return tools.dispatch(operation, json.dumps({"server": "verifydoc", **params}), lambda _: True, 60)
            entries = call("mcp_search", query="verify_extraction")
            self.assertIn("verify_extraction", [t["name"] for t in entries["tools"]])
            self.assertGreater(call("mcp_search", query="unwind.history")["count"], 0)
            result = call("mcp_call", name="verify_extraction", arguments={"document": "Total: 42",
                          "schema": json.dumps({"type": "object", "properties": {"total": {"type": "number"}}})})
            self.assertFalse(result.get("error") or result.get("isError"), result)
            self.assertIn("n_accepted", json.dumps(result))
        self.assertTrue((self.root / ".turnloom" / "unwind.db").exists())

    def test_sample_reference_reads_from_its_own_archive(self):
        state = self.bench.dispatch("state", {})
        self.assertTrue(state["sample"])
        row = next(row for row in state["loom"] if row["reference"])
        self.assertLess(row["after"], row["before"])
        query = {"reference": row["reference"], "offset": 700, "length": 25}
        sample = self.bench.dispatch("recall", {**query, "sample": True})
        self.assertEqual(sample["total"], row["before"])
        self.assertEqual(len(sample["text"]), 25)
        self.assertIn("error", self.bench.dispatch("recall", query))

    def test_connect_uses_selected_catalog_entry(self):
        data = {"provider": "ollama", "model": "local-fixture"}
        row = {"model": data["model"], "input": None, "output": None, "tier": "free"}
        with patch.object(providers, "models", return_value=[row]), patch.object(providers, "store_profile") as save:
            with self.assertRaisesRegex(ValueError, "Refresh"):
                self.bench.dispatch("connect", data)
            self.bench.dispatch("models", data)
            self.bench.dispatch("connect", data)
            save.assert_called_once_with({"provider": "ollama", **row})
        self.assertEqual(self.bench.dispatch("state", {})["model"], "local-fixture")

    def test_api_requires_token_and_checks_origin(self):
        server, url = make_server(self.root)
        self.addCleanup(server.server_close)
        thread = threading.Thread(target=server.serve_forever)
        thread.start()
        self.addCleanup(thread.join)
        self.addCleanup(server.shutdown)
        headers = {"Authorization": "Bearer " + url.split("#")[1]}
        for request_headers, expected in [({}, 403), ({**headers, "Origin": "https://example.org"}, 403), (headers, 200)]:
            with self.subTest(expected=expected):
                client = HTTPConnection("127.0.0.1", server.server_port, timeout=5)
                try:
                    client.request("POST", "/api/state", "{}", request_headers)
                    response = client.getresponse()
                    self.assertEqual(response.status, expected)
                    if expected == 200:
                        self.assertTrue(json.loads(response.read())["sample"])
                finally:
                    client.close()
