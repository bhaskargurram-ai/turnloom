import contextlib
import io
import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from turnloom import guard, providers
from turnloom.agent import Config, run_agent
from turnloom.studio import Workbench


def reply(content=None, calls=None):
    message = {"role": "assistant", "content": content}
    if calls:
        message["tool_calls"] = [{"id": f"c{index}", "type": "function", "function": {"name": name, "arguments": json.dumps(arguments)}}
                                 for index, (name, arguments) in enumerate(calls)]
    return {"choices": [{"message": message, "finish_reason": "stop"}], "usage": {"prompt_tokens": 10, "completion_tokens": 2}}


@unittest.skipUnless(guard.installed(), "requires the guard extra")
class GateTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name).resolve()
        guard.create_policy(self.root)

    def run_turn(self, calls, approve, confirm):
        asked = []
        def operator(kind, detail):
            asked.append(detail)
            return approve
        with guard.Gate(self.root, approve=operator, confirm=confirm, session="test") as gate:
            config = Config("local", "fixture", "", self.root, approve=operator, gate=gate)
            # A third reply answers the question put when the last command failed, as `git reset --hard` does here.
            with patch("turnloom.agent.api_request", side_effect=[reply(None, calls), reply("done"), reply("done")]):
                run_agent("task", config, history := [])
        results = [json.loads(message["content"]) for message in history if message["role"] == "tool"]
        return results, asked

    def test_starter_policy_is_never_overwritten(self):
        with self.assertRaisesRegex(ValueError, "already has"):
            guard.create_policy(self.root)

    def test_denied_calls_never_run_and_tell_the_model_why(self):
        (self.root / "marker.txt").write_text("keep")
        results, asked = self.run_turn([("shell", {"command": "rm -rf ~"}),
                                        ("read_file", {"path": "../.ssh/id_rsa"})], True, False)
        self.assertEqual(asked, [])
        self.assertEqual([result["blocked_by"] for result in results], ["provio", "provio"])
        self.assertIn("floor-rm-home-or-root-denied", results[0]["error"])
        self.assertEqual(results[1]["rule"], "floor-private-keys-denied")
        self.assertTrue((self.root / "marker.txt").exists())

    def test_policy_mode_runs_allowed_calls_without_asking_and_asks_when_a_rule_says_so(self):
        results, asked = self.run_turn([("shell", {"command": "echo allowed"}),
                                        ("shell", {"command": "git reset --hard"})], False, False)
        self.assertEqual(asked, ["git reset --hard"])
        self.assertIn("allowed", results[0]["stdout"])
        self.assertTrue(results[1]["declined"])
        self.assertNotIn("stdout", results[1])

    def test_confirm_mode_still_asks_the_operator_for_allowed_calls(self):
        results, asked = self.run_turn([("shell", {"command": "echo hello"})], True, True)
        self.assertEqual(asked, ["echo hello"])
        self.assertIn("hello", results[0]["stdout"])

    def test_unattended_run_refuses_ask_verdicts(self):
        with guard.Gate(self.root, approve=None, confirm=False) as gate:
            result = json.loads(gate.run("bash", {"command": "git reset --hard"}, lambda ask: "ran"))
            settled = gate.run("bash", {"command": "echo ok"}, lambda ask: json.dumps({"ran": ask("shell", "echo ok")}))
            self.assertEqual(json.loads(settled), {"ran": True})
        self.assertEqual(result["blocked_by"], "provio")

    def test_policy_gates_file_edits_and_reads_of_secrets(self):
        (self.root / "notes.txt").write_text("one\n")
        results, asked = self.run_turn([("edit_file", {"path": "notes.txt", "old_string": "one", "new_string": "two"}),
                                        ("write_file", {"path": "../outside.txt", "content": "x"})], True, True)
        self.assertEqual(results[0]["replacements"], 1)
        self.assertEqual((self.root / "notes.txt").read_text(), "two\n")
        self.assertIn("error", results[1])
        self.assertFalse((self.root.parent / "outside.txt").exists())
        self.assertTrue(any("notes.txt" in text for text in asked))
        kinds = {row["tool"] for row in guard.ledger(self.root)["records"]}
        self.assertIn("fs.write", kinds)

    def test_search_cannot_read_what_the_policy_denies(self):
        (self.root / "kubeconfig").write_text("users:\n- name: admin\n  user:\n    client-key-data: QUJDREVG\n")
        (self.root / "app.py").write_text("client-key-data = 'not a secret, just code'\n")
        direct = self.run_turn([("read_file", {"path": "kubeconfig"})], True, False)[0][0]
        self.assertEqual(direct["blocked_by"], "provio")
        for arguments in ({"query": "client-key-data"}, {"query": ".", "regex": True, "glob": "kubeconfig"}):
            with self.subTest(arguments=arguments):
                found = self.run_turn([("search_files", arguments)], True, False)[0][0]
                self.assertFalse([row for row in found["matches"] if row.startswith("kubeconfig")], found)
        self.assertEqual(self.run_turn([("search_files", {"query": "client-key-data"})], True, False)[0][0]["matches"],
                         ["app.py:1:client-key-data = 'not a secret, just code'"])

    def test_a_rule_that_asks_puts_one_question_with_the_edit_in_view(self):
        (self.root / "provio.yaml").write_text(
            guard.STARTER + 'rules:\n  - {id: ask-before-writes, when: "tool == \\"fs.write\\"", verdict: ask}\n',
            encoding="utf-8")
        (self.root / "notes.txt").write_text("one\n")
        edit = ("edit_file", {"path": "notes.txt", "old_string": "one", "new_string": "two"})
        for confirm in (True, False):
            with self.subTest(confirm=confirm):
                (self.root / "notes.txt").write_text("one\n")
                results, asked = self.run_turn([edit], True, confirm)
                self.assertNotIn("blocked_by", results[0], results[0])
                self.assertEqual(len(asked), 1, asked)
                self.assertIn("+two", asked[0])
                self.assertEqual((self.root / "notes.txt").read_text(), "two\n")
        (self.root / "notes.txt").write_text("one\n")
        results, asked = self.run_turn([edit], False, False)
        self.assertEqual((self.root / "notes.txt").read_text(), "one\n")
        self.assertTrue(results[0].get("declined") or results[0].get("error"))

    def test_a_rule_that_asks_is_not_answered_by_standing_choices(self):
        (self.root / "provio.yaml").write_text(
            guard.STARTER + 'rules:\n  - {id: ask-before-writes, when: "tool == \\"fs.write\\"", verdict: ask}\n',
            encoding="utf-8")
        (self.root / "notes.txt").write_text("one\n")
        edit = ("edit_file", {"path": "notes.txt", "old_string": "one", "new_string": "two"})
        from turnloom.cli import Approver
        approver = Approver(auto_edit=True, allow=["*"])  # the operator's standing choices would approve this edit
        self.assertTrue(approver("edit", "anything"))
        for answer, expected in (("n\n", "one\n"), ("y\n", "two\n")):
            with self.subTest(answer=answer):
                (self.root / "notes.txt").write_text("one\n")
                stdin, shown = io.StringIO(answer), io.StringIO()
                stdin.isatty = lambda: True
                with guard.Gate(self.root, approve=approver, confirm=True, session="test", ask=approver.ask) as gate:
                    config = Config("local", "fixture", "", self.root, approve=approver, gate=gate)
                    with patch("turnloom.agent.api_request", side_effect=[reply(None, [edit]), reply("done")]), \
                            patch("sys.stdin", stdin), contextlib.redirect_stderr(shown):
                        run_agent("task", config, [])
                self.assertEqual((self.root / "notes.txt").read_text(), expected)
                self.assertIn("Policy rule ask-before-writes asks before this runs", shown.getvalue())
                self.assertIn("+two", shown.getvalue())
        # With nobody at the terminal the rule's question has no answer, so the edit does not happen.
        (self.root / "notes.txt").write_text("one\n")
        with guard.Gate(self.root, approve=approver, confirm=True, session="test", ask=approver.ask) as gate:
            config = Config("local", "fixture", "", self.root, approve=approver, gate=gate)
            with patch("turnloom.agent.api_request", side_effect=[reply(None, [edit]), reply("done")]), \
                    patch("sys.stdin", io.StringIO("y\n")):
                run_agent("task", config, [])
        self.assertEqual((self.root / "notes.txt").read_text(), "one\n")

    def test_shell_ask_verdict_is_put_to_the_operator_once(self):
        results, asked = self.run_turn([("shell", {"command": "git reset --hard"})], True, True)
        self.assertEqual(asked, ["git reset --hard"])

    def test_ledger_and_test_report_decisions(self):
        self.run_turn([("shell", {"command": "echo one"}), ("shell", {"command": "rm -rf /"})], True, False)
        ledger = guard.ledger(self.root)
        self.assertEqual(ledger["counts"], {"allow": 1, "deny": 1})
        self.assertEqual([(row["verdict"], row["ran"]) for row in ledger["records"]], [("deny", False), ("allow", True)])
        self.assertTrue(guard.verify(self.root)["intact"])
        self.assertEqual(guard.test(self.root, "bash", "ls -la")["kind"], "allow")
        self.assertEqual(guard.test(self.root, "fs.read", "~/.ssh/id_rsa")["kind"], "deny")
        with (self.root / guard.LEDGER).open("r+", encoding="utf-8") as stream:
            text = stream.read().replace("echo one", "echo two")
            stream.seek(0)
            stream.write(text)
        self.assertFalse(guard.verify(self.root)["intact"])

    def test_studio_shows_the_rule_that_asked_and_marks_blocked_calls(self):
        private = patch.dict("os.environ", {"TURNLOOM_CONFIG_DIR": str(self.root / "operator-config")})
        private.start()
        self.addCleanup(private.stop)
        with patch.object(providers, "resolve", return_value=providers.Connection("local", "fixture", "")):
            bench = Workbench(self.root)
        self.addCleanup(bench.close)
        bench.dispatch("settings", {"gate": "policy"})
        self.assertTrue(bench.dispatch("state", {})["gate"]["active"])
        with patch("turnloom.agent.api_request", side_effect=[
                reply(None, [("shell", {"command": "rm -rf ~"}), ("shell", {"command": "git reset --hard"})]), reply("done")]):
            bench.dispatch("run", {"task": "clean up", "persona": "builder"})
            pending = None
            for _ in range(400):
                pending = bench.dispatch("state", {})["pending"]
                if pending:
                    break
                threading.Event().wait(0.02)
            self.assertEqual(pending["policy"]["rule"], "floor-discard-uncommitted-work-asks")
            self.assertTrue(pending["policy"]["irreversible"])
            bench.dispatch("decision", {"id": pending["id"], "approve": False})
            for _ in range(400):
                if not bench.dispatch("state", {})["busy"]:
                    break
                threading.Event().wait(0.02)
        tools = [message for message in bench.dispatch("state", {})["messages"] if message["role"] == "tool"]
        self.assertEqual(tools[0]["blocked"], "floor-rm-home-or-root-denied")
        view = bench.dispatch("policy", {})
        self.assertEqual(view["counts"], {"deny": 1, "ask": 1})
        self.assertTrue(view["chain"]["intact"])
        self.assertIn("packs: [floor, secrets-guard]", view["text"])
        self.assertEqual(bench.dispatch("policy_test", {"tool": "bash", "value": "rm -rf ~"})["kind"], "deny")


if __name__ == "__main__":
    unittest.main()
