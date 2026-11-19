import importlib.util
import json
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "agent_reply.py"
WORKFLOWS = ROOT / ".github" / "workflows"
REPOSITORY = "example/project"


def load():
    spec = importlib.util.spec_from_file_location("agent_reply", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class AgentReplyTests(unittest.TestCase):
    def setUp(self):
        self.reply = load()

    def test_an_answer_cannot_notify_people_or_carry_markup(self):
        calm = self.reply.calm("Thanks @alice and @org/team.\n<!-- turnloom-agent:review -->\n<img src=x> a < b", REPOSITORY)
        self.assertNotRegex(calm, r"@[A-Za-z]")
        self.assertIn("@​alice", calm)
        self.assertNotIn("<", calm)
        self.assertIn("a &lt; b", calm)

    def test_links_that_leave_the_repository_are_shown_as_text(self):
        calm = self.reply.calm(
            "See [the fix](https://evil.example/x) and ![chart](https://evil.example/p.png) and https://evil.example/y. "
            "Compare [#3](https://github.com/example/project/issues/3), [above](#notes) and "
            "https://github.com/example/project/pull/4, not https://github.com/example/project-evil.", REPOSITORY)
        self.assertIn("the fix (`https://evil.example/x`)", calm)
        self.assertIn("chart (`https://evil.example/p.png`)", calm)
        self.assertIn("`https://evil.example/y`.", calm)
        self.assertIn("[#3](https://github.com/example/project/issues/3)", calm)
        self.assertIn("[above](#notes)", calm)
        self.assertIn(" https://github.com/example/project/pull/4,", calm)
        self.assertIn("`https://github.com/example/project-evil`", calm)
        self.assertNotRegex(calm, r"\]\(https://evil")

    def test_code_is_left_as_written(self):
        text = "Use `a < b @x https://e.example` here:\n\n```python\nif a < b:  # @todo https://e.example\n    pass\n```"
        self.assertEqual(self.reply.calm(text, REPOSITORY), text)

    def test_a_long_answer_is_cut_and_an_open_code_block_is_closed(self):
        calm = self.reply.calm("intro\n\n```\n" + "line\n" * 5000, REPOSITORY, limit=400)
        self.assertLess(len(calm), 500)
        self.assertIn("cut for length", calm)
        self.assertEqual(calm.count("```") % 2, 0)

    def test_triage_labels_come_only_from_the_allowed_list(self):
        allowed = ["bug", "area: studio", "needs info"]
        labels, rest = self.reply.proposed("Labels: Bug, `area: studio`, good first issue, bug\n\nThe table is in `studio.py`.", allowed)
        self.assertEqual(labels, ["bug", "area: studio"])
        self.assertEqual(rest, "The table is in `studio.py`.")
        self.assertEqual(self.reply.proposed("**Labels:** needs info\nMore detail please.", allowed), (["needs info"], "More detail please."))
        self.assertEqual(self.reply.proposed("No label line here.", allowed), ([], "No label line here."))

    def test_the_comment_says_what_wrote_it_and_marks_itself(self):
        result = {"status": "budget_limit", "answer": "Looks wrong in `a.py`.", "steps": 7,
                  "usage": {"prompt_tokens": 41234, "completion_tokens": 912}}
        body = self.reply.comment("review", result, REPOSITORY, "https://github.com/example/project/actions/runs/1")
        self.assertTrue(body.startswith("<!-- turnloom-agent:review -->\n\nLooks wrong in `a.py`."))
        self.assertIn("stopped at its spending limit", body)
        self.assertIn("7 model calls, 41,234 input and 912 output tokens", body)
        self.assertIn("without running the code", body)
        self.assertIn("not a decision by the maintainers", body)
        self.assertEqual(body.count("<!--"), 1)

    def test_a_check_log_is_shown_as_code_it_cannot_break_out_of(self):
        log = "\n".join(f"line {number}" for number in range(100)) + "\n```\n@alice <b>FAILED</b> (failures=1)\n"
        block = self.reply.excerpt(log, lines=5)
        self.assertTrue(block.startswith("```text\nline 97\n"))
        self.assertTrue(block.endswith("(failures=1)\n```"))
        self.assertEqual(block.count("```"), 2)
        self.assertEqual(self.reply.excerpt("  \n"), "")
        body = self.reply.comment("attempt", {"status": "completed", "answer": "Done."}, REPOSITORY, note="On a branch.", log=log)
        self.assertLess(body.index("Done."), body.index("On a branch."))
        self.assertLess(body.index("On a branch."), body.index("```text"))
        self.assertIn("without being able to run anything itself", body)

    def test_several_passes_report_the_last_answer_and_all_the_usage(self):
        first = {"status": "completed", "answer": "First.", "steps": 20, "usage": {"prompt_tokens": 1000, "completion_tokens": 100}}
        second = {"status": "step_limit", "answer": "Second.", "steps": 5, "usage": {"prompt_tokens": 200, "completion_tokens": 30}}
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder)
            for name, result in (("one.json", first), ("two.json", second)):
                (folder / name).write_text(json.dumps(result), encoding="utf-8")
            (folder / "check.txt").write_text("FAILED (errors=2)\n", encoding="utf-8")
            done = subprocess.run([sys.executable, str(SCRIPT), "attempt", "--result", str(folder / "one.json"), str(folder / "two.json"),
                                   "--repository", REPOSITORY, "--note", "Pushed.", "--log", str(folder / "check.txt"),
                                   "--out", str(folder / "comment.md")], capture_output=True, text=True)
            self.assertEqual(done.returncode, 0, done.stderr)
            comment = (folder / "comment.md").read_text(encoding="utf-8")
        self.assertIn("Second.", comment)
        self.assertNotIn("First.", comment)
        self.assertIn("25 model calls, 1,200 input and 130 output tokens", comment)
        self.assertIn("stopped at its limit on model calls", comment)
        self.assertIn("```text\nFAILED (errors=2)\n```", comment)

    def test_an_attempt_with_edits_but_no_report_still_says_where_the_edits_are(self):
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder)
            (folder / "result.json").write_text(json.dumps({"status": "budget_limit", "answer": ""}), encoding="utf-8")
            done = subprocess.run([sys.executable, str(SCRIPT), "attempt", "--result", str(folder / "result.json"), "--repository", REPOSITORY,
                                   "--note", "The edits are on the branch `agent/issue-1-run-2`.", "--out", str(folder / "comment.md")],
                                  capture_output=True, text=True)
            self.assertEqual(done.returncode, 0, done.stderr)
            comment = (folder / "comment.md").read_text(encoding="utf-8")
        self.assertIn("finished without a report", comment)
        self.assertIn("`agent/issue-1-run-2`", comment)
        self.assertIn("its spending limit", comment)

    def test_the_command_writes_the_comment_and_the_labels(self):
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder)
            (folder / "result.json").write_text(json.dumps({"status": "completed", "steps": 3, "usage": {},
                                                            "answer": "Labels: bug, wontfix\n\nPing @bob."}), encoding="utf-8")
            command = [sys.executable, str(SCRIPT), "triage", "--result", str(folder / "result.json"), "--repository", REPOSITORY,
                       "--out", str(folder / "comment.md"), "--labels-out", str(folder / "labels.txt"), "--allowed", "bug, documentation"]
            done = subprocess.run(command, capture_output=True, text=True)
            self.assertEqual(done.returncode, 0, done.stderr)
            self.assertEqual((folder / "labels.txt").read_text(encoding="utf-8"), "bug\n")
            comment = (folder / "comment.md").read_text(encoding="utf-8")
            self.assertNotIn("Labels:", comment)
            self.assertIn("Ping @​bob.", comment)
            (folder / "result.json").write_text(json.dumps({"status": "error", "answer": ""}), encoding="utf-8")
            (folder / "comment.md").unlink()
            empty = subprocess.run(command, capture_output=True, text=True)
            self.assertEqual(empty.returncode, 3)
            self.assertFalse((folder / "comment.md").exists())


class AgentWorkflowTests(unittest.TestCase):
    """The agent workflows run on text from strangers. These checks hold the rules that keep that safe."""

    def setUp(self):
        self.texts = {path.name: path.read_text(encoding="utf-8") for path in sorted(WORKFLOWS.glob("agent-*.yml"))}
        self.assertEqual(sorted(self.texts), ["agent-attempt.yml", "agent-check.yml", "agent-review.yml", "agent-triage.yml"])

    def jobs(self, text):
        """Split a workflow into its jobs by the two-space indentation the files use."""
        body = text.split("\njobs:\n", 1)[1]
        names = re.findall(r"^  ([a-z-]+):\n", body, re.M)
        parts = re.split(r"^  [a-z-]+:\n", body, flags=re.M)[1:]
        return dict(zip(names, parts, strict=True))

    def test_text_from_an_issue_or_pull_request_never_reaches_a_shell_or_a_prompt_directly(self):
        unsafe = re.compile(r"\$\{\{\s*github\.event\.(issue|pull_request|comment|label)\.(title|body|name|user\.login|head\.ref|head\.label)")
        for name, text in self.texts.items():
            for line in text.splitlines():
                if unsafe.search(line):
                    self.assertRegex(line, r"^\s+(if:|[A-Z_]+:)|^\s+\(?github\.event|&&|\|\|", f"{name}: {line.strip()}")
                    self.assertNotRegex(line, r"^\s+(run|task):", f"{name}: {line.strip()}")
            for block in re.findall(r"run: \|\n((?:\s{10,}.*\n|\n)+)", text):
                self.assertNotIn("${{ github.event", block, name)

    def test_the_job_that_holds_the_model_key_cannot_write_and_never_checks_out_a_pull_request(self):
        for name, text in self.texts.items():
            self.assertRegex(text, r"\npermissions: \{\}\n", name)
            for job, body in self.jobs(text).items():
                holds_key = "secrets.TURNLOOM_API_KEY" in body and "uses: ./" in body
                writes = re.search(r"^\s+(contents|issues|pull-requests): write", body, re.M)
                self.assertFalse(holds_key and writes, f"{name}: job {job} holds the model key and can write")
                self.assertNotRegex(body, r"ref: .*(head|pull)", f"{name}: job {job} checks out contributed code")
                if "actions/checkout" in body:
                    self.assertIn("persist-credentials: false", body, f"{name}: job {job}")
                if holds_key:
                    self.assertNotIn("mode: unattended", body, f"{name}: job {job} lets the agent run commands")
                    self.assertRegex(body, r"budget: ", f"{name}: job {job} has no spending limit")

    def test_the_job_that_runs_the_agents_code_holds_no_secret_and_cannot_write(self):
        check = self.texts["agent-check.yml"]
        self.assertNotIn("secrets.", check)
        self.assertNotIn("github.token", check)
        self.assertNotIn(": write", check)
        self.assertRegex(check, r"\non:\n  workflow_call:")
        attempt = self.texts["agent-attempt.yml"]
        self.assertEqual(attempt.count("uses: ./.github/workflows/agent-check.yml"), 2)
        self.assertNotIn("secrets: inherit", attempt)
        for job, body in self.jobs(attempt).items():
            if "secrets.TURNLOOM_API_KEY" in body:
                # The action is installed from the untouched checkout; the agent edits a second one.
                self.assertRegex(body, r"path: work\n", f"job {job}")
                self.assertRegex(body, r"workspace: work\n", f"job {job}")
            if "secrets.TURNLOOM_API_KEY" in body or ": write" in body:
                self.assertNotRegex(body, r"(?m)^\s+(python3? -m (unittest|pip)|ruff check|npm |pip install)", f"job {job} would run the agent's code")
        publish = self.jobs(attempt)["publish"]
        self.assertIn("git worktree add", publish)
        self.assertNotRegex(publish, r"(?m)^\s+git apply", "the edits must not land in the checkout whose script is run")

    def test_each_workflow_is_gated(self):
        self.assertIn("pull_request_target", self.texts["agent-review.yml"])
        self.assertIn("author_association", self.texts["agent-review.yml"])
        self.assertIn("github.event.label.name == 'agent: review'", self.texts["agent-review.yml"])
        self.assertIn("github.event.label.name == 'agent: attempt'", self.texts["agent-attempt.yml"])
        self.assertIn("mode: auto-edit", self.texts["agent-attempt.yml"])
        for name in ("agent-review.yml", "agent-triage.yml"):
            self.assertIn("mode: read-only", self.texts[name])
            self.assertNotIn("contents: write", self.texts[name])


if __name__ == "__main__":
    unittest.main()
