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
        def operator(command):
            asked.append(command)
            return approve
        with guard.Gate(self.root, approve=operator, confirm=confirm, session="test") as gate:
            config = Config("local", "fixture", "", self.root, approve=operator, gate=gate,
                            workspace_tool=lambda params: {"read": params.get("path")})
            with patch("turnloom.agent.api_request", side_effect=[reply(None, calls), reply("done")]):
                run_agent("task", config, history := [])
        results = [json.loads(message["content"]) for message in history if message["role"] == "tool"]
        return results, asked

    def test_starter_policy_is_never_overwritten(self):
        with self.assertRaisesRegex(ValueError, "already has"):
            guard.create_policy(self.root)

    def test_denied_calls_never_run_and_tell_the_model_why(self):
        (self.root / "marker.txt").write_text("keep")
        results, asked = self.run_turn([("shell", {"command": "rm -rf ~"}),
                                        ("workspace", {"action": "read", "path": "../.ssh/id_rsa"})], True, False)
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
            result = json.loads(gate.run("bash", {"command": "git reset --hard"}, lambda approve: "ran"))
            self.assertEqual(json.loads(gate.run("bash", {"command": "echo ok"}, lambda approve: json.dumps({"ran": True}))), {"ran": True})
        self.assertEqual(result["blocked_by"], "provio")

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
