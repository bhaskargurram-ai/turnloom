"""Run one Turnloom task inside GitHub Actions and publish its result as step outputs and a job summary."""

import json
import os
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path

MODES = {"read-only": ["--read-only"], "auto-edit": ["--auto-edit"], "unattended": ["--yes"]}


def output(name, value):
    """Append one possibly multi-line output using the delimiter form GitHub documents."""
    target = os.environ.get("GITHUB_OUTPUT")
    if not target:
        return
    marker = "turnloom-" + uuid.uuid4().hex
    with open(target, "a", encoding="utf-8") as stream:
        stream.write(f"{name}<<{marker}\n{value}\n{marker}\n")


def main():
    for stream in (sys.stdout, sys.stderr):  # a runner's console may not be UTF-8, and an answer can hold any character
        stream.reconfigure(errors="replace")
    mode = os.environ.get("TURNLOOM_MODE", "read-only")
    if mode not in MODES:
        print(f"::error::mode must be one of: {', '.join(MODES)}")
        return 2
    task = os.environ.get("TURNLOOM_TASK", "").strip()
    if not task:
        print("::error::task is empty")
        return 2
    # -P keeps the working directory off the import path: the workspace holds files the agent may edit, and
    # none of them should be importable by the process that holds the model key.
    command = [sys.executable, "-P", "-m", "turnloom", "run", task, "--json", "--workspace", os.environ.get("TURNLOOM_WORKSPACE", "."),
               "--persona", os.environ.get("TURNLOOM_PERSONA", "builder"), "--max-steps", os.environ.get("TURNLOOM_MAX_STEPS", "40"),
               "--deadline", os.environ.get("TURNLOOM_DEADLINE", "900"), *MODES[mode]]
    for flag, name in (("--budget", "TURNLOOM_BUDGET"), ("--input-rate", "TURNLOOM_INPUT_RATE"), ("--output-rate", "TURNLOOM_OUTPUT_RATE")):
        if os.environ.get(name, "").strip() not in ("", "0"):
            command += [flag, os.environ[name].strip()]
    done = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace")
    sys.stderr.write(done.stderr)
    try:
        result = json.loads(done.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        print("::error::Turnloom did not produce a result. See the log above.")
        sys.stdout.write(done.stdout)
        return done.returncode or 1
    folder = Path(os.environ.get("RUNNER_TEMP") or tempfile.gettempdir())
    saved = folder / "turnloom-result.json"
    saved.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    answer = result.get("answer") or "\n".join(result.get("errors") or [])
    output("status", result["status"])
    output("answer", answer)
    output("files-changed", "\n".join(result.get("files_changed") or []))
    output("result-file", str(saved))
    usage = result.get("usage") or {}
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as stream:
            stream.write(f"### Turnloom: {result['status']}\n\n{answer}\n\n"
                         f"`{usage.get('prompt_tokens', 0):,}` input and `{usage.get('completion_tokens', 0):,}` output tokens "
                         f"in {result.get('steps', 0)} model calls.\n")
    print(answer)
    strict = os.environ.get("TURNLOOM_STRICT", "true").lower() != "false"
    return 1 if strict and result["status"] != "completed" else 0


if __name__ == "__main__":
    raise SystemExit(main())
