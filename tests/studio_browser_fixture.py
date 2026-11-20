"""Start disposable Studio workspaces for the Playwright test.

Prints one JSON line with the session URLs, then serves until killed. Nothing here reaches the
network or the operator's own configuration: the model is a script, the provider catalog is
canned, and settings live in a temporary folder.
"""

import itertools
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from unittest.mock import patch

CONFIG = tempfile.mkdtemp(prefix="turnloom-fixture-config-")
os.environ["TURNLOOM_CONFIG_DIR"] = CONFIG
os.environ["LITELLM_LOCAL_MODEL_COST_MAP"] = "True"
for name in ("TURNLOOM_ENDPOINT", "TURNLOOM_MODEL", "TURNLOOM_API_KEY", "HARNESS_ENDPOINT", "HARNESS_MODEL"):
    os.environ.pop(name, None)

from turnloom import gateway, guard, providers  # noqa: E402
from turnloom.studio import companions_installed, make_server  # noqa: E402

CATALOG = [
    {"model": "fixture-coder", "input": 0.5, "output": 1.5, "tier": "paid", "tools": True, "source": "fixture", "context": 64000},
    {"model": "fixture-coder-mini", "input": 0.1, "output": 0.3, "tier": "paid", "tools": True, "source": "fixture", "context": 32000},
    {"model": "fixture-free", "input": 0, "output": 0, "tier": "free", "tools": True, "source": "fixture", "context": 16000},
    {"model": "fixture-unlisted", "input": None, "output": None, "tier": "unknown", "tools": None, "source": "fixture", "context": None},
]
# One very long line holding an ANSI escape and a right-to-left override, to check that the
# approval card scrolls and shows hidden characters by name.
LONG_COMMAND = "echo " + " ".join(f"part-{index:03d}" for index in range(260)) + f" {chr(27)}[31mred{chr(27)}[0m {chr(0x202E)}desrever"
SLOW_COMMAND = f'"{sys.executable}" -c "import time; time.sleep(2); print(\'slow done\')"'
calls = itertools.count(1)


def reply(text=None, tools=(), usage=(1200, 60, 0)):
    message = {"role": "assistant", "content": text}
    if tools:
        message["tool_calls"] = [{"id": f"call-{next(calls)}", "type": "function",
                                  "function": {"name": name, "arguments": json.dumps(arguments)}} for name, arguments in tools]
    prompt, completion, cached = usage
    return {"choices": [{"message": message, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": prompt, "completion_tokens": completion,
                      "prompt_tokens_details": {"cached_tokens": cached}}}


def say(on_text, text, pause=0.06):
    """Stream a reply a word at a time, the way a provider would, and return it whole."""
    if on_text:
        for word in text.split(" "):
            on_text(word + " ")
            time.sleep(pause)
    return text


def model(config, messages, deadline, allow_tools=True, on_text=None):
    last_user = max(index for index, message in enumerate(messages) if message["role"] == "user")
    task = messages[last_user]["content"]
    step = sum(item["role"] == "tool" for item in messages[last_user:])
    prompt = 900 + 350 * step
    if not allow_tools:
        return reply(say(on_text, "Summary of what was done before tool use ended: the last action was declined, "
                                  "so nothing further ran."), usage=(prompt, 30, 400))
    if "Fail with 401" in task:
        raise RuntimeError("Model HTTP 401: invalid api key")
    if "Bump the value" in task:
        if step == 0:
            return reply(say(on_text, "I will read the file before changing it."), [("read_file", {"path": "app.py"})],
                         (prompt, 24, 0))
        if step == 1:
            return reply(None, [("edit_file", {"path": "app.py", "old_string": "value = 1", "new_string": "value = 2"})],
                         (prompt, 30, 800))
        return reply(say(on_text, "Done. `value` is now 2 in `app.py`.\n\n- Read the file\n- Changed one line\n\n"
                                  "See the [Python docs](https://docs.python.org/3/), a [local note](file:///etc/hosts) "
                                  "and `__init__.py` for context."),
                     usage=(prompt, 44, 1100))
    if "Run the tests" in task:
        return reply(say(on_text, "Running the test suite."), [("shell", {"command": "python -m pytest -q"})], (prompt, 20, 0))
    if "Start a long check" in task:
        return reply(None, [("shell", {"command": "echo never-runs"})], (prompt, 12, 0))
    if "Show a long command" in task:
        return reply(None, [("shell", {"command": LONG_COMMAND})], (prompt, 12, 0))
    if "Slow command" in task:
        if step < 2:
            return reply(None, [("shell", {"command": SLOW_COMMAND})], (prompt, 18, 600))
        return reply(say(on_text, "The slow command ran twice."), usage=(prompt, 16, 900))
    if "Read everything" in task:
        # Sixty reads in one turn, two per model reply, to fill the Loom with rows. Each read in each pass
        # starts at a line of its own: a read that repeats one still in view is answered with a note and
        # is never folded, and this scenario is here to fill the Loom with folded originals.
        if step < 60:
            earlier = next((index for index, word in enumerate(("first", "second", "third", "fourth")) if word in task), 0)
            reads = [("read_file", {"path": f"notes/n{1 + (step + offset) % 6}.md",
                                    "line": 1 + (step + offset) // 6 + 10 * earlier}) for offset in (0, 1)]
            return reply(None, reads, (prompt, 12, 0))
        return reply(say(on_text, "Read the notes again."), usage=(prompt, 8, 0))
    if "Survey the notes" in task:
        plan = [("list_files", {"path": "notes"}), ("search_files", {"query": "thread", "path": "notes"})]
        plan += [("read_file", {"path": f"notes/n{index}.md"}) for index in range(1, 7)]
        if step < len(plan):
            return reply(None, [plan[step]], (prompt, 14, 300 * step))
        return reply(say(on_text, "Surveyed six notes. Each one describes a stage of weaving.\n\n"
                                  "| Note | Stage |\n| --- | --- |\n| n1 | warping |\n| n6 | finishing |"),
                     usage=(prompt, 28, 2000))
    if "Team task" in task:
        if "\nbuild:" in task:
            return reply(say(on_text, "Review: team.txt holds the agreed greeting. Nothing else changed."), usage=(prompt, 22, 0))
        if "\nplan:" in task:
            if step == 0:
                return reply(None, [("write_file", {"path": "team.txt", "content": "hello from the team\n"})], (prompt, 20, 0))
            return reply(say(on_text, "Built: wrote team.txt."), usage=(prompt, 12, 0))
        if step == 0:
            return reply(None, [("read_file", {"path": "app.py"})], (prompt, 10, 0))
        return reply(say(on_text, "Plan: write a greeting to team.txt, then review it."), usage=(prompt, 18, 0))
    if "Verify the receipt" in task:
        if step == 0:
            return reply(None, [("mcp_search", {"server": "verifydoc", "query": "verify_extraction"})], (prompt, 10, 0))
        if step == 1:
            schema = {"type": "object", "properties": {"total": {"type": "number"}}}
            return reply(None, [("mcp_call", {"server": "verifydoc", "name": "verify_extraction", "arguments": {
                "document": "Total: 42", "schema": json.dumps(schema)}})], (prompt, 26, 0))
        verified = "n_accepted" in messages[-1]["content"]
        return reply(say(on_text, "Receipt verified." if verified else "Verification failed."), usage=(prompt, 8, 0))
    if "Policy denial" in task:
        if step == 0:
            return reply(None, [("read_file", {"path": "~/.ssh/id_rsa"})], (prompt, 10, 0))
        return reply(say(on_text, "The policy handled the read."), usage=(prompt, 8, 0))
    if "Policy check" in task:
        if step == 0:
            return reply(None, [("shell", {"command": "git reset --hard"})], (prompt, 10, 0))
        return reply(say(on_text, "The policy handled the command."), usage=(prompt, 8, 0))
    return reply(say(on_text, "Nothing to do for that request."), usage=(prompt, 8, 0))


def catalog(provider, key=""):
    if provider.startswith(gateway.PREFIX):
        return gateway.models(provider)
    if provider in ("ollama", "openrouter", "deepseek") or key:
        return [dict(row) for row in CATALOG]
    raise ValueError(f"{providers.info(provider)[0]} model discovery returned HTTP 401. Check the provider credential and access.")


def refuse_vault():
    raise ValueError("No credential store in the fixture.")


def populate(root):
    (root / "README.md").write_text("# Fixture project\n\nStudio workspace for the browser test.\n", encoding="utf-8")
    (root / "app.py").write_text("value = 1\n\n\ndef total(items):\n    return sum(items) + value\n", encoding="utf-8")
    (root / "long.py").write_text("".join(f"line_{index} = {index}\n" for index in range(1, 401)), encoding="utf-8")
    (root / "config.json").write_text('{\n  "name": "fixture",\n  "threads": 4\n}\n', encoding="utf-8")
    (root / "settings.yaml").write_text("name: fixture\nthreads: 4\n", encoding="utf-8")
    (root / "src").mkdir()
    (root / "src" / "util.js").write_text("export const double = (value) => value * 2;\n", encoding="utf-8")
    (root / "src" / "style.css").write_text("body {\n  margin: 0;\n}\n", encoding="utf-8")
    (root / "docs").mkdir()
    (root / "docs" / "receipt.txt").write_text(
        "Corner Cafe\nDate: 2026-03-14\nItems: 2 coffee, 1 bun\nTax: 3.40\nTotal: 42.50\n", encoding="utf-8")
    (root / "notes").mkdir()
    stages = ["warping", "beaming", "threading", "sleying", "weaving", "finishing"]
    for index, stage in enumerate(stages, 1):
        body = "\n".join(f"{line}. In {stage}, each thread is checked, counted and tied before the next pass begins."
                         for line in range(1, 101 + index * 3))
        (root / "notes" / f"n{index}.md").write_text(f"# Note {index}: {stage}\n\n{body}\n", encoding="utf-8")


def commit(root):
    """Make the workspace a Git repository with one commit, so the Changes view has a baseline."""
    git = shutil.which("git")
    if not git:
        return False
    identity = ["-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", "-c", "commit.gpgsign=false",
                "-c", "core.autocrlf=false"]
    for arguments in (["init", "-q"], ["add", "-A"], [*identity, "commit", "-q", "-m", "Fixture baseline"]):
        done = subprocess.run([git, *arguments], cwd=root, capture_output=True, stdin=subprocess.DEVNULL)
        if done.returncode:
            return False
    return True


if __name__ == "__main__":
    with tempfile.TemporaryDirectory(prefix="turnloom-fixture-") as temp:
        root, empty, second = Path(temp) / "loomworks", Path(temp) / "empty-project", Path(temp) / "second-project"
        guardless = Path(temp) / "policy-without-provio"
        for folder in (root, empty, second, guardless):
            folder.mkdir()
        (guardless / "README.md").write_text("# A workspace with a policy file\n", encoding="utf-8")
        (guardless / "provio.yaml").write_text("version: 1\ndefault: allow\n", encoding="utf-8")
        populate(root)
        (second / "README.md").write_text("# Second project\n\nOpened by the workspace switch.\n", encoding="utf-8")
        has_git = commit(root)
        with patch.object(providers, "resolve", side_effect=ValueError("No connection in the fixture.")), \
             patch.object(providers, "models", side_effect=catalog), \
             patch.object(providers, "secret", side_effect=lambda provider, required=True: "sk-saved" if provider == "deepseek" else ""), \
             patch.object(providers, "vault", side_effect=refuse_vault), \
             patch.object(providers, "fetch", return_value={}), \
             patch("turnloom.agent.api_request", side_effect=model):
            server, url = make_server(root, chooser=lambda: str(second))
            other, other_url = make_server(empty, desktop=True)

            def serve_other():
                # Closing the socket when the server stops is what makes a closed Studio refuse connections.
                with other:
                    other.serve_forever()
            threading.Thread(target=serve_other, daemon=True).start()

            # A third workspace behaves as if Provio were missing: it has a policy file and a model,
            # but its gate reports that the package is not installed.
            third, third_url = make_server(guardless)
            bench = third.holder["bench"]
            bench.connection = providers.connection("ollama", CATALOG[0], "", lookup=False)
            bench.gate_state = lambda: {"installed": False, "policy": True, "mode": bench.settings["gate"], "active": False}
            threading.Thread(target=third.serve_forever, daemon=True).start()
            print(json.dumps({"url": url, "empty": other_url, "guardless": third_url, "root": str(root), "git": has_git, "guard": guard.installed(),
                              "companions": companions_installed(), "litellm": gateway.available()}), flush=True)
            try:
                with server:
                    server.serve_forever()
            finally:
                other.shutdown()
                third.shutdown()
                shutil.rmtree(CONFIG, ignore_errors=True)
