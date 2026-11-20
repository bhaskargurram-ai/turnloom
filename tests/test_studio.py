import json
import tempfile
import threading
import time
import unittest
import urllib.parse
import urllib.request
from http.client import HTTPConnection
from importlib.util import find_spec
from pathlib import Path
from unittest.mock import patch

from turnloom import providers
from turnloom.studio import Workbench, companion_tools, make_server


def reply(content=None, calls=()):
    message = {"role": "assistant", "content": content}
    if calls:
        message["tool_calls"] = [{"id": f"{name}-{index}-{time.monotonic_ns()}", "type": "function",
                                  "function": {"name": name, "arguments": json.dumps(arguments)}}
                                 for index, (name, arguments) in enumerate(calls)]
    return {"choices": [{"message": message, "finish_reason": "stop"}], "usage": {"prompt_tokens": 50, "completion_tokens": 10}}


class StudioTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name).resolve()
        connection = providers.Connection("http://model.invalid/v1", "fixture-model", "")
        resolution = patch.object(providers, "resolve", return_value=connection)
        resolution.start()
        self.addCleanup(resolution.stop)
        private = patch.dict("os.environ", {"TURNLOOM_CONFIG_DIR": str(self.root / "operator-config")})
        private.start()
        self.addCleanup(private.stop)
        self.bench = Workbench(self.root)
        self.addCleanup(self.bench.close)

    def state(self, **data):
        return self.bench.dispatch("state", {"session": self.bench.trace.stem, **data})

    def wait(self, condition, seconds=10):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            snapshot = self.state()
            if condition(snapshot):
                return snapshot
            time.sleep(0.02)
        self.fail(f"Condition not reached. Last state: {snapshot}")

    def run_turn(self, task, *replies, **options):
        script = list(replies)

        def model(config, messages, deadline, allow_tools=True, on_text=None):
            item = script.pop(0)
            if isinstance(item, tuple):
                for piece in item[0]:
                    on_text(piece)
                item = item[1]
            return item
        patcher = patch("turnloom.agent.api_request", side_effect=model)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.bench.dispatch("run", {"task": task, "persona": "builder", **options})

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

    def test_operator_sees_dotfiles_but_never_ignored_folders(self):
        (self.root / ".env").write_text("TOKEN=1")
        (self.root / ".git").mkdir()
        (self.root / ".git" / "config").write_text("x")
        names = [entry["name"] for entry in self.bench.dispatch("tree", {"path": "."})]
        self.assertEqual(names, [".env"])
        self.assertEqual(self.bench.dispatch("read", {"path": ".env"})["text"], "TOKEN=1")
        with self.assertRaisesRegex(ValueError, "Protected"):
            self.bench.dispatch("read", {"path": ".git/config"})
        self.assertIn("Protected", self.bench.files.dispatch("read_file", '{"path": ".env"}')["error"])

    def test_new_session_starts_empty_without_sample_data(self):
        state = self.state()
        self.assertEqual((state["messages"], state["loom"], state["edits"], state["busy"]), ([], [], [], False))
        self.assertNotIn("sample", state)
        self.assertEqual(state["model"], "fixture-model")

    def test_turn_streams_text_shows_edits_for_approval_and_can_be_undone(self):
        (self.root / "app.py").write_text("value = 1\n")
        edit = ("edit_file", {"path": "app.py", "old_string": "value = 1", "new_string": "value = 2"})
        self.run_turn("Bump the value", (["Reading ", "the file."], reply("Reading the file.", [("read_file", {"path": "app.py"})])),
                      reply(calls=[edit]), (["Done", "."], reply("Done.")))
        pending = self.wait(lambda snapshot: snapshot["pending"])
        self.assertEqual((pending["pending"]["kind"], pending["pending"]["title"]), ("edit", "Edit app.py"))
        self.assertIn("+value = 2", pending["pending"]["detail"])
        self.assertEqual((self.root / "app.py").read_text(), "value = 1\n")
        interim = [item for item in pending["messages"] if item.get("interim")]
        self.assertEqual(interim[0]["text"], "Reading the file.")
        self.bench.dispatch("decision", {"id": pending["pending"]["id"], "approve": True})
        done = self.wait(lambda snapshot: not snapshot["busy"])
        self.assertEqual((self.root / "app.py").read_text(), "value = 2\n")
        self.assertEqual(done["messages"][-1]["text"], "Done.")
        self.assertEqual(done["draft"], "")
        self.assertEqual(done["edits"], [{"path": "app.py", "created": False, "added": 1, "removed": 1}])
        tool = [item for item in done["messages"] if item["role"] == "tool"][-1]
        self.assertTrue(tool["diff"])
        self.assertIn("app.py (+1 −1)", tool["label"])
        review = self.bench.dispatch("edit_diff", {"path": "app.py"})
        self.assertIn("+value = 2", review["diff"])
        self.assertEqual((review["original"], review["modified"]), ("value = 1\n", "value = 2\n"))
        self.assertEqual(self.bench.dispatch("revert", {}), {"restored": ["app.py"], "conflicts": []})
        self.assertEqual((self.root / "app.py").read_text(), "value = 1\n")
        after = self.state()
        self.assertEqual(after["edits"], [])
        self.assertEqual(after["messages"][-1]["role"], "note")

    def test_always_approval_covers_later_edits_only(self):
        first = ("write_file", {"path": "a.txt", "content": "a\n"})
        second = ("write_file", {"path": "b.txt", "content": "b\n"})
        self.run_turn("Write files", reply(calls=[first]), reply(calls=[second]),
                      reply(calls=[("shell", {"command": "echo hi"})]), reply("Finished."))
        pending = self.wait(lambda snapshot: snapshot["pending"])
        self.bench.dispatch("decision", {"id": pending["pending"]["id"], "approve": True, "always": True})
        pending = self.wait(lambda snapshot: snapshot["pending"] and snapshot["pending"]["kind"] == "shell")
        self.assertTrue((self.root / "b.txt").exists())
        self.assertEqual(pending["always"], {"edits": True, "requests": 0})
        self.assertFalse(pending["settings"]["auto_edit"])
        self.bench.dispatch("decision", {"id": pending["pending"]["id"], "approve": False})
        done = self.wait(lambda snapshot: not snapshot["busy"])
        self.assertEqual(done["messages"][-1]["status"], "partial")
        # "Always" lasts for the session. It is not written to the workspace's saved settings.
        self.bench.dispatch("settings", {"budget": 2})
        self.assertFalse(Workbench(self.root).settings["auto_edit"])
        self.bench.dispatch("reset", {})
        self.assertEqual(self.state()["always"], {"edits": False, "requests": 0})

    def test_long_poll_returns_when_state_changes(self):
        revision = self.state()["revision"]
        started = time.monotonic()
        threading.Timer(0.3, lambda: self.bench.dispatch("settings", {"auto_edit": True})).start()
        changed = self.state(wait=True, revision=revision)
        self.assertLess(time.monotonic() - started, 5)
        self.assertGreater(changed["revision"], revision)
        self.assertTrue(changed["settings"]["auto_edit"])

    def test_identical_tool_batch_is_bounded_and_summarized(self):
        calls = [("list_files", {})] * 63
        self.run_turn("Review code", reply(calls=calls), reply("No files found."))
        done = self.wait(lambda snapshot: not snapshot["busy"])
        self.assertEqual(sum(item["role"] == "tool" for item in done["messages"]), 2)
        self.assertEqual((done["messages"][-1]["text"], done["messages"][-1]["status"]), ("No files found.", "partial"))

    def test_stop_wakes_pending_approval_and_keeps_the_conversation(self):
        self.run_turn("Run it", reply(calls=[("shell", {"command": "echo never"})]))
        self.wait(lambda snapshot: snapshot["pending"])
        self.bench.dispatch("stop", {})
        done = self.wait(lambda snapshot: not snapshot["busy"])
        self.assertIsNone(done["pending"])
        self.assertEqual(done["messages"][-1]["text"], "Turn stopped by operator.")
        self.assertEqual([item["role"] for item in self.bench.history], ["system", "user", "assistant", "tool"])

    def test_failed_turn_is_shown_as_an_error_and_history_survives(self):
        self.run_turn("First", reply("Answer one."))
        self.wait(lambda snapshot: not snapshot["busy"] and snapshot["messages"])
        with patch("turnloom.agent.api_request", side_effect=RuntimeError("Model HTTP 401: bad key")):
            self.bench.dispatch("run", {"task": "Second", "persona": "builder"})
            done = self.wait(lambda snapshot: not snapshot["busy"] and snapshot["messages"][-1]["role"] == "error")
        self.assertIn("bad key", done["messages"][-1]["text"])
        self.assertEqual(self.bench.history[2]["content"], "Answer one.")

    def test_sessions_can_be_listed_resumed_and_reset(self):
        self.run_turn("Inspect the tree", reply(calls=[("list_files", {})]), reply("Empty project."))
        self.wait(lambda snapshot: not snapshot["busy"] and snapshot["messages"])
        first = self.bench.trace.stem
        listed = self.bench.dispatch("sessions", {})
        self.assertEqual([(row["id"], row["task"], row["turns"], row["current"]) for row in listed],
                         [(first, "Inspect the tree", 1, True)])
        self.bench.dispatch("reset", {})
        fresh = self.state()
        self.assertNotEqual(fresh["session"], first)
        self.assertEqual(fresh["messages"], [])
        self.bench.dispatch("resume", {"id": first})
        back = self.state()
        self.assertEqual(back["session"], first)
        self.assertEqual([item["role"] for item in back["messages"]], ["user", "tool", "assistant"])
        self.assertEqual([item["role"] for item in self.bench.history], ["system", "user", "assistant", "tool", "assistant"])
        with self.assertRaisesRegex(ValueError, "no longer available"):
            self.bench.dispatch("resume", {"id": "f" * 32})

    def test_loom_reports_folded_results_and_serves_recall(self):
        commands = [reply(calls=[("write_file", {"path": f"f{index}.txt", "content": "x" * 5000})]) for index in range(5)]
        self.bench.settings["auto_edit"] = True
        self.run_turn("Write five files", *commands, reply("Written."))
        done = self.wait(lambda snapshot: not snapshot["busy"])
        folded = [row for row in done["loom"] if row["reference"]]
        self.assertTrue(folded)
        self.assertLess(folded[0]["after"], folded[0]["before"])
        recalled = self.bench.dispatch("recall", {"reference": folded[0]["reference"], "offset": 0, "length": 80})
        self.assertEqual(recalled["total"], folded[0]["before"])
        self.assertIn("f0.txt", recalled["text"])

    @unittest.skipUnless(find_spec("verifydoc") and find_spec("unwind"), "requires the studio extra")
    def test_real_verifydoc_extraction_through_unwind(self):
        with companion_tools(self.root) as tools:
            def call(operation, **params):
                return tools.dispatch(operation, json.dumps({"server": "verifydoc", **params}), lambda kind, detail: True, 60)
            entries = call("mcp_search", query="verify_extraction")
            self.assertIn("verify_extraction", [t["name"] for t in entries["tools"]])
            self.assertGreater(call("mcp_search", query="unwind.history")["count"], 0)
            result = call("mcp_call", name="verify_extraction", arguments={"document": "Total: 42",
                          "schema": json.dumps({"type": "object", "properties": {"total": {"type": "number"}}})})
            self.assertFalse(result.get("error") or result.get("isError"), result)
            self.assertIn("n_accepted", json.dumps(result))
        self.assertTrue((self.root / ".turnloom" / "unwind.db").exists())

    def test_connect_uses_selected_catalog_entry(self):
        data = {"provider": "ollama", "model": "local-fixture"}
        row = {"model": data["model"], "input": None, "output": None, "tier": "free", "context": 32768}
        with patch.object(providers, "models", return_value=[row]), patch.object(providers, "store_profile") as save:
            with self.assertRaisesRegex(ValueError, "Refresh"):
                self.bench.dispatch("connect", data)
            self.bench.dispatch("models", data)
            self.bench.dispatch("connect", data)
            save.assert_called_once_with({"provider": "ollama", **row})
        self.assertEqual(self.state()["model"], "local-fixture")
        self.assertEqual(self.bench.connection.context, 32768)

    def test_bad_requests_get_readable_errors(self):
        for action, data, expected in [("run", {"task": " ", "persona": "builder"}, "Enter a task"),
                                       ("run", {"task": "x", "persona": "wizard"}, "Unknown persona"),
                                       ("decision", {"id": "nope", "approve": True}, "expired"),
                                       ("unknown", {}, "Unknown action")]:
            with self.subTest(action=action), self.assertRaisesRegex(ValueError, expected):
                self.bench.dispatch(action, data)


class ServerTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        private = patch.dict("os.environ", {"TURNLOOM_CONFIG_DIR": str(Path(folder.name) / "operator-config")})
        private.start()
        self.addCleanup(private.stop)
        with patch.object(providers, "resolve", side_effect=ValueError("none")):
            self.server, url = make_server(Path(folder.name))
        self.token = url.split("#")[1]
        self.addCleanup(self.server.server_close)
        thread = threading.Thread(target=self.server.serve_forever)
        thread.start()
        self.addCleanup(thread.join)
        self.addCleanup(self.server.shutdown)

    def request(self, method, path, body=None, headers=None, token=True):
        client = HTTPConnection("127.0.0.1", self.server.server_port, timeout=5)
        try:
            sent = {"Authorization": "Bearer " + self.token} if token else {}
            sent.update(headers or {})
            client.request(method, path, body, sent)
            response = client.getresponse()
            return response.status, response.read(), dict(response.getheaders())
        finally:
            client.close()

    def test_api_requires_token_and_checks_origin_and_host(self):
        port = self.server.server_port
        self.assertEqual(self.request("POST", "/api/state", "{}", token=False)[0], 403)
        self.assertEqual(self.request("POST", "/api/state", "{}", {"Authorization": "Bearer wrong"}, token=False)[0], 403)
        self.assertEqual(self.request("POST", "/api/state", "{}", {"Origin": "https://example.org"})[0], 403)
        self.assertEqual(self.request("POST", "/api/state", "{}", {"Host": f"attacker.example:{port}"})[0], 403)
        self.assertEqual(self.request("GET", "/", headers={"Host": f"attacker.example:{port}"})[0], 403)
        status, body, _ = self.request("POST", "/api/state", "{}", {"Origin": f"http://127.0.0.1:{port}"})
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["model"], "")
        self.assertEqual(self.request("POST", "/api/state", "{}", {"Host": f"localhost:{port}"})[0], 200)

    def test_pages_carry_security_headers_and_unknown_paths_are_missing(self):
        status, body, headers = self.request("GET", "/")
        self.assertEqual(status, 200)
        self.assertIn(b"Turnloom Studio", body)
        self.assertIn("frame-ancestors 'none'", headers["Content-Security-Policy"])
        self.assertEqual(headers["X-Content-Type-Options"], "nosniff")
        self.assertEqual(self.request("GET", "/../turnloom/agent.py")[0], 404)
        self.assertEqual(self.request("GET", "/studio.js?v=1")[0], 200)

    def test_browser_handoff_keeps_the_token_off_the_command_line(self):
        from turnloom import studio
        opened = []
        url = f"http://127.0.0.1:{self.server.server_port}/#{self.token}"
        with patch.object(studio.webbrowser, "open", side_effect=lambda target: opened.append(target) or True):
            self.assertTrue(studio.open_browser(url))
        self.assertTrue(opened[0].startswith("file:"))
        self.assertNotIn(self.token, opened[0])
        page = Path(urllib.request.url2pathname(urllib.parse.urlsplit(opened[0]).path))
        self.addCleanup(page.unlink, missing_ok=True)
        self.assertIn(url, page.read_text(encoding="utf-8"))

    def test_malformed_bodies_are_rejected_without_a_traceback(self):
        for body in ("[]", "not json", ""):
            with self.subTest(body=body):
                status, payload, _ = self.request("POST", "/api/state", body)
                self.assertEqual(status, 400)
                self.assertIn("error", json.loads(payload))
        status, payload, _ = self.request("POST", "/api/read", "{}")
        self.assertEqual((status, json.loads(payload)["error"]), (400, "Missing field: path"))


if __name__ == "__main__":
    unittest.main()
