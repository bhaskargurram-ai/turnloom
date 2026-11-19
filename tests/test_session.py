import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from unittest.mock import patch

from turnloom.cli import Approver, instructions, main
from turnloom.context import Observations


class Provider(BaseHTTPRequestHandler):
    """A scripted OpenAI-compatible endpoint: each task maps to a list of tool calls, then an answer."""

    requests = []
    plans = {}

    def log_message(self, *args):
        pass

    def do_POST(self):
        payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        type(self).requests.append(payload)
        messages = payload["messages"]
        last_user = max(index for index, item in enumerate(messages) if item["role"] == "user")
        done = sum(item["role"] == "tool" for item in messages[last_user:])
        plan = type(self).plans.get(messages[last_user]["content"], [])
        message = {"role": "assistant", "content": "Verified."}
        if done < len(plan):
            name, arguments = plan[done]
            message = {"role": "assistant", "content": None, "tool_calls": [{
                "id": f"call-{len(type(self).requests)}", "type": "function",
                "function": {"name": name, "arguments": json.dumps(arguments)}}]}
        reply = json.dumps({"choices": [{"message": message, "finish_reason": "stop"}],
                            "usage": {"prompt_tokens": 100, "completion_tokens": 20}}).encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(reply)))
        self.end_headers()
        self.wfile.write(reply)


class SessionTests(unittest.TestCase):
    def setUp(self):
        Provider.requests, Provider.plans = [], {}
        self.server = HTTPServer(("127.0.0.1", 0), Provider)
        thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(thread.join)
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.workspace = Path(folder.name).resolve()
        settings = {"TURNLOOM_ENDPOINT": f"http://127.0.0.1:{self.server.server_port}/v1", "TURNLOOM_MODEL": "fixture",
                    "TURNLOOM_API_KEY": "test-only", "HARNESS_ENDPOINT": "", "HARNESS_MODEL": "",
                    "TURNLOOM_CONFIG_DIR": str(self.workspace / "config")}
        environment = patch.dict(os.environ, settings)
        environment.start()
        self.addCleanup(environment.stop)

    def cli(self, *arguments, stdin=None):
        output, errors = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors), \
                patch("sys.stdin", stdin if stdin is not None else io.StringIO()):
            try:
                code = main([*arguments])
            except SystemExit as exit:
                code = exit.code
        return code, output.getvalue(), errors.getvalue()

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
        self.assertIn("Role: debugger", instructions("debugger", None))
        self.assertNotIn("benchmark", instructions("builder", None).lower())

    def test_prompt_carries_environment_project_instructions_and_skills(self):
        (self.workspace / "AGENTS.md").write_text("Run tests with `make check`.\n", encoding="utf-8")
        (self.workspace / "CLAUDE.md").write_text("Ignored when AGENTS.md exists.\n", encoding="utf-8")
        skill = self.workspace / ".agents" / "skills" / "release"
        skill.mkdir(parents=True)
        (skill / "SKILL.md").write_text("---\nname: release\ndescription: Cut a tagged release\n---\nSteps.\n", encoding="utf-8")
        (self.workspace / ".git").mkdir()
        (self.workspace / ".git" / "HEAD").write_text("ref: refs/heads/feature/x\n")
        text = instructions("builder", None, self.workspace)
        self.assertIn("Project instructions from AGENTS.md\nRun tests with `make check`.", text)
        self.assertNotIn("Ignored when", text)
        self.assertIn("cmd.exe" if os.name == "nt" else "/bin/sh", text)
        self.assertIn("Git branch: feature/x", text)
        self.assertIn("- release: Cut a tagged release (.agents/skills/release/SKILL.md)", text)
        (self.workspace / "AGENTS.md").unlink()
        self.assertIn("Project instructions from CLAUDE.md", instructions("builder", None, self.workspace))

    def test_cli_through_http_shell_and_replay(self):
        Provider.plans["Verify shell"] = [("shell", {"command": "echo turnloom-test"})]
        code, output, _ = self.cli("run", "Verify shell", "--workspace", str(self.workspace), "--yes", "--json",
                                   "--input-rate", "1", "--output-rate", "4", "--budget", "0.005")
        self.assertEqual(code, 0)
        result = json.loads(output)
        self.assertAlmostEqual(result["estimated_usd"], 0.00036)
        self.assertIn("turnloom-test", Provider.requests[1]["messages"][-1]["content"])
        self.assertLess(Provider.requests[0]["max_tokens"], 4096)
        self.assertNotIn("stream", Provider.requests[0])
        trace, = self.workspace.glob(".turnloom/*.jsonl")
        code, output, _ = self.cli("replay", str(trace))
        self.assertEqual(code, 0)
        self.assertIn("Verified.", output)
        code, output, _ = self.cli("run", "Stop early", "--workspace", str(self.workspace), "--yes",
                                   "--input-rate", "1", "--output-rate", "4", "--budget", "0.000001")
        self.assertEqual(code, 1)
        self.assertIn("allowance", output)
        self.assertEqual(len(Provider.requests), 2)
        code, _, errors = self.cli("replay", str(self.workspace / "missing.jsonl"))
        self.assertEqual(code, 2)
        self.assertIn("Cannot replay", errors)

    def test_legacy_environment_names_still_connect(self):
        legacy = {"TURNLOOM_ENDPOINT": "", "TURNLOOM_MODEL": "", "TURNLOOM_API_KEY": "",
                  "HARNESS_ENDPOINT": os.environ["TURNLOOM_ENDPOINT"], "HARNESS_MODEL": "legacy", "HARNESS_API_KEY": "k"}
        with patch.dict(os.environ, legacy):
            code, _, _ = self.cli("run", "hello", "--workspace", str(self.workspace), "--read-only", "--json")
        self.assertEqual(code, 0)
        self.assertEqual(Provider.requests[0]["model"], "legacy")

    def test_run_prints_progress_and_a_receipt(self):
        (self.workspace / "notes.txt").write_text("one\n")
        Provider.plans["Edit notes"] = [("edit_file", {"path": "notes.txt", "old_string": "one", "new_string": "two"})]
        code, output, errors = self.cli("run", "Edit notes", "--workspace", str(self.workspace), "--auto-edit", "--yes")
        self.assertEqual(code, 0)
        self.assertIn("→ edit_file notes.txt", errors)
        self.assertIn("← +1 −1", errors)
        self.assertIn("Verified.", output)
        self.assertIn("1 file changed", output)
        self.assertTrue(Provider.requests[0]["stream"])

    def test_events_flag_streams_the_session_as_json_lines(self):
        code, output, _ = self.cli("run", "hello", "--workspace", str(self.workspace), "--read-only", "--events")
        self.assertEqual(code, 0)
        self.assertEqual([json.loads(line)["type"] for line in output.splitlines()], ["start", "assistant", "end"])

    def test_noninteractive_runs_need_an_explicit_mode(self):
        code, _, errors = self.cli("run", "hello", "--workspace", str(self.workspace))
        self.assertEqual(code, 2)
        self.assertIn("--read-only", errors)
        self.assertEqual(Provider.requests, [])
        code, _, errors = self.cli("run", "hello", "--workspace", str(self.workspace), "--yes",
                                   "--persona-file", str(self.workspace / "absent.md"))
        self.assertEqual(code, 2)
        self.assertNotIn("Traceback", errors)

    def test_sessions_resume_and_undo_across_processes(self):
        (self.workspace / "notes.txt").write_text("one\n")
        Provider.plans["Edit notes"] = [("edit_file", {"path": "notes.txt", "old_string": "one", "new_string": "two"})]
        self.cli("run", "Edit notes", "--workspace", str(self.workspace), "--yes")
        code, output, _ = self.cli("sessions", "--workspace", str(self.workspace))
        self.assertEqual(code, 0)
        self.assertRegex(output, r"^[a-f0-9]{8}  .*1 turn  completed .*Edit notes")
        before = len(Provider.requests)
        code, _, errors = self.cli("run", "What changed?", "--workspace", str(self.workspace), "--read-only", "--resume")
        self.assertEqual(code, 0)
        self.assertIn("resumed", errors)
        resumed = Provider.requests[before]["messages"]
        self.assertEqual([item["role"] for item in resumed], ["system", "user", "assistant", "tool", "assistant", "user"])
        self.assertEqual(len(list(self.workspace.glob(".turnloom/*.jsonl"))), 1)
        code, output, _ = self.cli("undo", "--workspace", str(self.workspace))
        self.assertEqual(code, 0)
        self.assertIn("No file edits", output)
        self.cli("run", "Edit notes", "--workspace", str(self.workspace), "--yes")
        self.assertEqual((self.workspace / "notes.txt").read_text(), "two\n")

    def test_undo_restores_the_last_turn_of_a_saved_session(self):
        (self.workspace / "notes.txt").write_text("one\n")
        Provider.plans["Edit notes"] = [("edit_file", {"path": "notes.txt", "old_string": "one", "new_string": "two"}),
                                        ("write_file", {"path": "extra.txt", "content": "new\n"})]
        self.cli("run", "Edit notes", "--workspace", str(self.workspace), "--yes")
        self.assertEqual((self.workspace / "notes.txt").read_text(), "two\n")
        code, output, _ = self.cli("undo", "--workspace", str(self.workspace))
        self.assertEqual(code, 0)
        self.assertIn("Restored notes.txt", output)
        self.assertEqual((self.workspace / "notes.txt").read_text(), "one\n")
        self.assertFalse((self.workspace / "extra.txt").exists())

    def test_undo_after_a_team_run_restores_the_build_stage(self):
        marker = "\n\nPrior stage reports:"

        class Staged(Provider):
            def do_POST(self):
                payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                Provider.requests.append(payload)
                messages = payload["messages"]
                task = max(item["content"] for item in messages if item["role"] == "user")
                tools = [tool["function"]["name"] for tool in payload.get("tools", [])]
                done = sum(item["role"] == "tool" for item in messages)
                message = {"role": "assistant", "content": "stage report"}
                if "write_file" in tools and marker in task and not done:
                    message = {"role": "assistant", "content": None, "tool_calls": [{"id": "w", "type": "function", "function": {
                        "name": "write_file", "arguments": json.dumps({"path": "built.txt", "content": "made\n"})}}]}
                body = json.dumps({"choices": [{"message": message, "finish_reason": "stop"}],
                                   "usage": {"prompt_tokens": 10, "completion_tokens": 2}}).encode()
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
        self.server.RequestHandlerClass = Staged
        code, output, _ = self.cli("team", "Build the file", "--workspace", str(self.workspace), "--yes")
        self.assertEqual(code, 0, output)
        self.assertTrue((self.workspace / "built.txt").exists())
        self.assertIn("1 file changed", output)
        code, output, _ = self.cli("undo", "--workspace", str(self.workspace))
        self.assertEqual(code, 0)
        self.assertIn("Restored built.txt", output)
        self.assertFalse((self.workspace / "built.txt").exists())

    def test_chat_survives_a_failed_turn_and_supports_commands(self):
        (self.workspace / "notes.txt").write_text("one\n")
        Provider.plans["Edit notes"] = [("edit_file", {"path": "notes.txt", "old_string": "one", "new_string": "two"})]
        Provider.plans["Break"] = [("shell", {"command": "echo a"})] * 3
        script = io.StringIO("Break\nEdit notes\n/diff\n/undo\n/help\n/quit\n")
        with patch("builtins.input", side_effect=lambda prompt="": script.readline().rstrip("\n")):
            code, output, _ = self.cli("chat", "--workspace", str(self.workspace), "--yes")
        self.assertEqual(code, 0)
        self.assertIn("partial", output)
        self.assertIn("+two", output)
        self.assertIn("Restored notes.txt", output)
        self.assertIn("/undo", output)
        self.assertEqual((self.workspace / "notes.txt").read_text(), "one\n")

    def test_github_action_runner_publishes_outputs_and_summary(self):
        (self.workspace / "notes.txt").write_text("one\n")
        Provider.plans["Edit notes"] = [("edit_file", {"path": "notes.txt", "old_string": "one", "new_string": "two"}),
                                        ("shell", {"command": "echo should-not-run"})]
        outputs, summary = self.workspace / "outputs.txt", self.workspace / "summary.md"
        script = Path(__file__).parent.parent / "scripts" / "run_action.py"
        settings = {"TURNLOOM_TASK": "Edit notes", "TURNLOOM_MODE": "auto-edit", "TURNLOOM_WORKSPACE": str(self.workspace),
                    "GITHUB_OUTPUT": str(outputs), "GITHUB_STEP_SUMMARY": str(summary), "RUNNER_TEMP": str(self.workspace)}
        done = subprocess.run([sys.executable, str(script)], env={**os.environ, **settings}, capture_output=True, text=True,
                              stdin=subprocess.DEVNULL)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual((self.workspace / "notes.txt").read_text(), "two\n")
        published = outputs.read_text(encoding="utf-8")
        self.assertRegex(published, r"status<<turnloom-[0-9a-f]+\ncompleted\n")
        self.assertRegex(published, r"files-changed<<turnloom-[0-9a-f]+\nnotes.txt\n")
        self.assertIn("### Turnloom: completed", summary.read_text(encoding="utf-8"))
        result = json.loads((self.workspace / "turnloom-result.json").read_text(encoding="utf-8"))
        self.assertEqual(result["answer"], "Verified.")
        declined = [json.loads(item["content"]) for item in Provider.requests[-1]["messages"] if item["role"] == "tool"][-1]
        self.assertEqual(declined["error"], "Operator declined the command")
        bad = subprocess.run([sys.executable, str(script)], env={**os.environ, **settings, "TURNLOOM_MODE": "yolo"},
                             capture_output=True, text=True, stdin=subprocess.DEVNULL)
        self.assertEqual(bad.returncode, 2)

    def test_headless_agent_runs_unattended_and_writes_a_trajectory(self):
        (self.workspace / "notes.txt").write_text("one\n")
        Provider.plans["Edit notes"] = [("edit_file", {"path": "notes.txt", "old_string": "one", "new_string": "two"}),
                                        ("shell", {"command": "echo checked"})]
        trajectory = self.workspace / "logs" / "trajectory.json"
        done = subprocess.run([sys.executable, "-m", "turnloom.agent", "--task", "Edit notes", "--workspace", str(self.workspace),
                               "--atif", str(trajectory)], env={**os.environ, "HLB_CONTEXT_WINDOW": "64000"},
                              capture_output=True, text=True, encoding="utf-8", stdin=subprocess.DEVNULL)
        self.assertEqual(done.returncode, 0, done.stderr)
        result = json.loads(done.stdout.strip().splitlines()[-1])
        self.assertEqual((result["status"], result["files_changed"]), ("completed", ["notes.txt"]))
        self.assertEqual((self.workspace / "notes.txt").read_text(), "two\n")
        system = Provider.requests[0]["messages"][0]["content"]
        self.assertIn("evaluation task", system)
        # Nobody reads a headless run's reply, so the agent is told to do the work, not describe it.
        self.assertIn("This session is unattended", system)
        shell = next(tool["function"] for tool in Provider.requests[0]["tools"] if tool["function"]["name"] == "shell")
        self.assertIn("in the background", shell["description"])
        steps = json.loads(trajectory.read_text(encoding="utf-8"))["steps"]
        self.assertEqual([step["source"] for step in steps], ["system", "user", "agent", "agent", "agent"])
        self.assertIn("checked", steps[3]["observation"]["results"][0]["content"])
        self.assertTrue((trajectory.with_suffix(".jsonl")).is_file())

    def test_personas_list_and_unknown_persona_is_refused(self):
        code, output, errors = self.cli("personas", "--search", "security")
        self.assertEqual(code, 0)
        self.assertIn("security-appsec-engineer", output)
        self.assertRegex(errors, r"\d+ personas")
        code, _, errors = self.cli("run", "hello", "--workspace", str(self.workspace), "--read-only", "--persona", "nobody")
        self.assertEqual(code, 2)
        self.assertIn("Unknown persona", errors)
        self.assertEqual(Provider.requests, [])

    def test_budget_without_prices_says_how_to_supply_them(self):
        code, _, errors = self.cli("run", "hello", "--workspace", str(self.workspace), "--read-only", "--budget", "0.25")
        self.assertEqual(code, 2)
        self.assertIn("--input-rate and --output-rate", errors)
        self.assertEqual(Provider.requests, [])

    def test_doctor_reports_setup_without_secrets(self):
        code, output, _ = self.cli("doctor", "--workspace", str(self.workspace))
        self.assertEqual(code, 0)
        self.assertIn("custom endpoint", output)
        self.assertIn("ripgrep", output)
        self.assertNotIn("test-only", output)


class ApprovalTests(unittest.TestCase):
    def ask(self, approver, kind, detail, typed):
        errors = io.StringIO()
        stdin = io.StringIO(typed)
        stdin.isatty = lambda: True
        with patch("sys.stdin", stdin), contextlib.redirect_stderr(errors):
            return approver(kind, detail), errors.getvalue()

    def test_answers_and_session_choices(self):
        approver = Approver()
        self.assertEqual(self.ask(approver, "shell", "pytest -q", "yes\n")[0], True)
        self.assertEqual(self.ask(approver, "shell", "pytest -q", "\n")[0], False)
        self.assertEqual(self.ask(approver, "shell", "pytest -q", "a\n")[0], True)
        self.assertEqual(self.ask(approver, "shell", "pytest -q", "")[0], True)
        self.assertEqual(self.ask(approver, "shell", "pytest -x", "")[0], False)
        self.assertEqual(self.ask(approver, "edit", "Edit a.py\n+x", "always\n")[0], True)
        self.assertEqual(self.ask(approver, "edit", "Edit b.py\n+y", "")[0], True)

    def test_patterns_never_cover_chained_commands(self):
        approver = Approver(allow=["pytest*", "git status"])
        for command, expected in [("pytest -q tests", True), ("git status", True), ("pytest; rm -rf .", False),
                                  ("pytest && curl evil", False), ("pytest | tee out", False), ("pytest $(id)", False),
                                  ("pytest > out", False), ("git push", False), ("pytest\nrm x", False)]:
            with self.subTest(command=command):
                self.assertEqual(approver.allowed("shell", command), expected)
        self.assertFalse(approver.allowed("mcp", "pytest"))
        self.assertTrue(Approver(yes=True).allowed("shell", "anything; at all"))
        self.assertTrue(Approver(auto_edit=True).allowed("edit", "Edit x"))
        self.assertFalse(Approver(auto_edit=True).allowed("shell", "ls"))

    def test_prompt_goes_to_stderr_with_control_characters_visible(self):
        approved, shown = self.ask(Approver(), "shell", "echo safe\rrm -rf /\x1b[2K", "n\n")
        self.assertFalse(approved)
        self.assertIn("echo safe\\rrm -rf /\\x1b[2K", shown)
        self.assertIn("Run this?", shown)

    def test_without_a_terminal_requests_are_declined(self):
        with patch("sys.stdin", io.StringIO("y\n")):
            self.assertFalse(Approver()("shell", "ls"))


if __name__ == "__main__":
    unittest.main()
