"""Run agents on Terminal-Bench tasks through Harbor, one job per agent and task.

One job per pair lets the metering pass-through (meter.py) attribute every model call to a
trial by its URL prefix. Each agent gets the same model, the same throwaway token and the
same endpoint; nothing else about an agent is configured, so each runs with its own defaults.

    python run.py --tasks TASKS_DIR --out OUT_DIR --token TOKEN --agents turnloom codex --names fix-git ...

A pair whose result.json already exists is skipped, so an interrupted run can be resumed.
"""

import argparse
import os
import random
import subprocess
import sys
import time
import tomllib
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

MODEL = "openai/gpt-5.1"
# "host" marks an agent whose loop runs in the Harbor process, not in the sandbox. Turnloom is told the model's
# context window and reply limit because it has no table of its own; the other agents look them up.
AGENTS = {
    "turnloom": {"agent": "turnloom_harbor:Turnloom",
                 "extra": ["--ae", "HLB_CONTEXT_WINDOW=400000", "--ae", "HLB_MAX_OUTPUT_TOKENS=128000"]},
    "codex": {"agent": "codex"},
    "aider": {"agent": "aider"},
    "openhands": {"agent": "openhands-sdk", "llm_env": True},
    "mini-swe-agent": {"agent": "mini-swe-agent"},
    "goose": {"agent": "goose"},
    "opencode": {"agent": "opencode"},
    "terminus-2": {"agent": "terminus-2", "host": True},
}


def sample(tasks, count, seed, max_timeout=900, max_memory=4096):
    """The benchmark's task subset: a seeded sample of the tasks that fit the time and memory limits."""
    eligible = []
    for path in sorted(Path(tasks).glob("*/task.toml")):
        data = tomllib.loads(path.read_text(encoding="utf-8"))
        if data["agent"]["timeout_sec"] <= max_timeout and data["environment"].get("memory_mb", 0) <= max_memory:
            eligible.append(path.parent.name)
    return random.Random(seed).sample(eligible, min(count, len(eligible))), len(eligible)


def run_pair(options, name, task):
    spec = AGENTS[name]
    out = Path(options.out) / name
    if (out / task / "result.json").is_file():
        return name, task, "kept"
    out.mkdir(parents=True, exist_ok=True)
    tag = f"{name}__{task}"
    host = "127.0.0.1" if spec.get("host") else "host.docker.internal"
    url = f"http://{host}:{options.port}/{tag}/v1"
    command = [options.harbor, "run", "-p", str(Path(options.tasks) / task), "-a", spec["agent"], "-m", MODEL, "-o", str(out),
               "--job-name", task, "-n", "1", "-y", "-q", "--agent-setup-timeout-multiplier", str(options.setup_multiplier),
               "--ae", f"OPENAI_API_KEY={options.token}", "--ae", f"OPENAI_BASE_URL={url}", *spec.get("extra", [])]
    if name == "turnloom":
        command += ["--ak", f"version={options.turnloom}"]
    if spec.get("llm_env"):  # OpenHands names the same two facts differently
        command += ["--ae", f"LLM_API_KEY={options.token}", "--ae", f"LLM_BASE_URL={url}"]
    if spec.get("host"):
        command += ["--ak", f"api_base={url}"]
    env = {**os.environ, "OPENAI_API_KEY": options.token, "OPENAI_BASE_URL": url, "OPENAI_API_BASE": url, "PYTHONUTF8": "1",
           "PYTHONIOENCODING": "utf-8", "NO_COLOR": "1", "PYTHONPATH": options.adapters}
    started = time.time()
    with open(out / (task + ".log"), "w", encoding="utf-8") as log:
        try:
            code = subprocess.run(command, env=env, stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                  timeout=options.job_timeout).returncode
        except subprocess.TimeoutExpired:
            code = "timeout"
    return name, task, f"exit {code} in {time.time() - started:.0f}s"


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--tasks", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--token", required=True)
    parser.add_argument("--port", type=int, default=8377)
    parser.add_argument("--harbor", default="harbor")
    parser.add_argument("--adapters", default="", help="directory holding turnloom_harbor.py")
    parser.add_argument("--agents", nargs="+", default=list(AGENTS))
    parser.add_argument("--names", nargs="*", help="task names; default is the seeded sample")
    parser.add_argument("--count", type=int, default=16)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--concurrent", type=int, default=4)
    parser.add_argument("--job-timeout", type=int, default=3600)
    parser.add_argument("--setup-multiplier", type=float, default=4.0,
                        help="applied to every agent's install time limit; installs share one machine and one network")
    parser.add_argument("--skip", type=int, default=0, help="leave out this many tasks from the start of the sample")
    parser.add_argument("--turnloom", default="main", help="the Turnloom commit or tag to install")
    parser.add_argument("--list", action="store_true", help="print the sample and stop")
    options = parser.parse_args()
    names, eligible = (options.names, None) if options.names else sample(options.tasks, options.count, options.seed)
    names = names[options.skip:]
    if os.name == "nt":
        # Ask Windows not to sleep while trials run. A request by this process that ends with it; no setting changes.
        import ctypes
        ctypes.windll.kernel32.SetThreadExecutionState(0x80000000 | 0x00000001)
    if options.list:
        print(f"{len(names)} of {eligible} eligible tasks:", " ".join(names))
        return
    # Interleave agents so a partial run covers every agent on the same tasks.
    pairs = [(name, task) for task in names for name in options.agents]
    with ThreadPoolExecutor(options.concurrent) as pool:
        for name, task, outcome in pool.map(lambda pair: run_pair(options, *pair), pairs):
            print(f"{time.strftime('%H:%M:%S')} {name:16} {task:36} {outcome}", flush=True)


if __name__ == "__main__":
    sys.exit(main())
