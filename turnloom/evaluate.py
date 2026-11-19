"""Run repeatable local trials on pristine task copies and report benchmark-macro scores."""

from __future__ import annotations

import argparse
import json
import os
import random
import shutil
import sys
import tempfile
from collections import defaultdict
from contextlib import nullcontext
from pathlib import Path
from typing import Any

from turnloom import roster
from turnloom.agent import FAIR_PLAY, Config, execute, run_agent
from turnloom.cli import instructions
from turnloom.extensions import McpTools
from turnloom.team import run_team
from turnloom.providers import resolve, setting

def score_command(command: str, workspace: Path, timeout: int) -> bool:
    """Run a task's check. `{python}` stands for this interpreter. The whole process tree ends at the timeout."""
    outcome, stdout, stderr = execute(command.replace("{python}", f'"{sys.executable}"'), workspace, None, timeout)
    stdout.close()
    stderr.close()
    return outcome.get("exit_code") == 0


def macro_score(rows: list[dict[str, Any]]) -> float:
    tasks: dict[tuple[str, str], list[float]] = defaultdict(list)
    for row in rows:
        tasks[(row["benchmark"], row["task_id"])].append(float(row["passed"]))
    benchmarks: dict[str, list[float]] = defaultdict(list)
    for (benchmark, _), attempts in tasks.items():
        benchmarks[benchmark].append(sum(attempts) / len(attempts))
    if not benchmarks:
        return 0.0
    return sum(sum(values) / len(values) for values in benchmarks.values()) / len(benchmarks)


def bootstrap_interval(rows: list[dict[str, Any]], samples: int = 2000, seed: int = 42) -> tuple[float, float]:
    by_benchmark: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    for row in rows:
        by_benchmark[row["benchmark"]][row["task_id"]].append(float(row["passed"]))
    if not by_benchmark:
        return (0.0, 0.0)
    generator = random.Random(seed)
    draws = []
    for _ in range(samples):
        benchmark_scores = []
        for task_map in by_benchmark.values():
            # Resample tasks, then attempts within each drawn task, so both sources of variance show.
            tasks = generator.choices(list(task_map.values()), k=len(task_map))
            scores = [sum(generator.choices(attempts, k=len(attempts))) / len(attempts) for attempts in tasks]
            benchmark_scores.append(sum(scores) / len(scores))
        draws.append(sum(benchmark_scores) / len(benchmark_scores))
    draws.sort()
    return draws[int(samples * 0.025)], draws[min(samples - 1, int(samples * 0.975))]


def validate_manifest(cases: list[dict[str, Any]], base: Path = Path(".")) -> None:
    """Check each case and resolve its workspace relative to the manifest's folder."""
    seen = set()
    for case in cases:
        if not isinstance(case, dict):
            raise ValueError("Each case must be a JSON object.")
        for key in ("benchmark", "id", "task", "workspace", "check"):
            if not isinstance(case.get(key), str) or not case[key]:
                raise ValueError(f"Case is missing {key}: {case}")
        identity = (case["benchmark"], case["id"])
        if identity in seen:
            raise ValueError(f"Duplicate case: {identity}")
        seen.add(identity)
        case["workspace"] = str((base / case["workspace"]).resolve())
        if not Path(case["workspace"]).is_dir():
            raise ValueError(f"Case workspace does not exist: {case['workspace']}")


def main() -> int:
    parser = argparse.ArgumentParser(prog="turnloom-eval", description="Run Turnloom on each task in a manifest, each in its own copy of the workspace, and record the results")
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--results", type=Path, default=Path("results"))
    parser.add_argument("--attempts", type=int, default=3)
    parser.add_argument("--model", default=setting("MODEL") or None)
    parser.add_argument("--endpoint", default=setting("ENDPOINT") or None)
    parser.add_argument("--api-key", default=os.environ.get("TURNLOOM_API_KEY", os.environ.get("HARNESS_API_KEY")))
    parser.add_argument("--deadline-seconds", type=int, default=1800)
    parser.add_argument("--max-steps", type=int, default=0)
    parser.add_argument("--context-tokens", type=int, default=128000)
    parser.add_argument("--max-reply-tokens", type=int, default=8192)
    parser.add_argument("--reasoning-effort")
    parser.add_argument("--persona-file", "--system-prompt-file", dest="persona_file", type=Path,
                        help="Additional trusted Markdown instructions appended to the persona")
    parser.add_argument("--persona", default="builder", help="A persona id from turnloom personas")
    parser.add_argument("--team", action="store_true")
    parser.add_argument("--mcp", type=Path, help="Trusted MCP configuration for each trial")
    parser.add_argument("--no-fold", action="store_true")
    parser.add_argument("--output-chars", type=int, default=16000)
    parser.add_argument("--input-usd-per-million", type=float)
    parser.add_argument("--output-usd-per-million", type=float)
    arguments = parser.parse_args()
    try:
        connection = resolve(arguments.endpoint, arguments.model, arguments.api_key)
    except (OSError, ValueError) as error:
        parser.error(str(error))
    if arguments.input_usd_per_million is None:
        arguments.input_usd_per_million = connection.input_rate
    if arguments.output_usd_per_million is None:
        arguments.output_usd_per_million = connection.output_rate
    if arguments.attempts < 1:
        parser.error("--attempts must be positive")
    try:
        cases = json.loads(arguments.manifest.read_text(encoding="utf-8"))
        if not isinstance(cases, list) or not cases:
            raise ValueError("Manifest must be a nonempty JSON array")
        validate_manifest(cases, arguments.manifest.resolve().parent)
        roster.find(arguments.persona)
        prompt = instructions(arguments.persona, arguments.persona_file) + FAIR_PLAY
    except (OSError, ValueError) as error:
        parser.error(str(error))
    for case in cases:
        if arguments.results.resolve().is_relative_to(Path(case["workspace"]).resolve()):
            parser.error("Place results outside every pristine task workspace.")
        if any(not all(ch.isalnum() or ch in "-_." for ch in case[key]) for key in ("id", "benchmark")):
            parser.error("Use simple names for benchmark and task IDs.")
    arguments.results.mkdir(parents=True, exist_ok=True)
    rows = []
    trial_path = arguments.results / "trials.jsonl"
    if trial_path.exists():
        parser.error(f"{trial_path} already exists. Choose a new --results directory for each run.")
    trial_path.touch()
    for case in cases:
        for attempt in range(1, arguments.attempts + 1):
            with tempfile.TemporaryDirectory(prefix="harness-trial-", dir=arguments.results) as temporary:
                workspace = Path(temporary) / "workspace"
                shutil.copytree(case["workspace"], workspace, ignore=shutil.ignore_patterns(".git", "__pycache__", ".pytest_cache"))
                transcript = arguments.results / "transcripts" / f"{case['benchmark']}-{case['id']}-{attempt}.jsonl"
                config = Config(
                    endpoint=connection.endpoint, model=connection.model,
                    api_key=connection.key, workspace=workspace,
                    deadline_seconds=int(case.get("deadline_seconds", arguments.deadline_seconds)),
                    max_steps=arguments.max_steps,
                    context_tokens=arguments.context_tokens,
                    max_reply_tokens=arguments.max_reply_tokens,
                    reasoning_effort=arguments.reasoning_effort,
                    transcript=transcript,
                    output_chars=arguments.output_chars,
                    system_prompt=prompt,
                    fold_history=not arguments.no_fold,
                    auto_approve=True,
                )
                with McpTools(arguments.mcp, workspace.resolve()) if arguments.mcp else nullcontext() as connections:
                    config.mcp = connections
                    runner = run_team if arguments.team else run_agent
                    result = runner(case["task"], config)
                passed = result.status == "completed" and score_command(case["check"], workspace, int(case.get("check_timeout_seconds", 120)))
                cost = (
                    result.usage["prompt_tokens"] * arguments.input_usd_per_million
                    + result.usage["completion_tokens"] * arguments.output_usd_per_million
                ) / 1_000_000
                row = {
                    "benchmark": case["benchmark"], "task_id": case["id"],
                    "attempt": attempt, "passed": passed, "agent_status": result.status,
                    "elapsed_seconds": result.elapsed_seconds, "usage": result.usage,
                    "estimated_cost_usd": round(cost, 6), "errors": result.errors,
                    "transcript": str(transcript),
                }
                rows.append(row)
                with trial_path.open("a", encoding="utf-8") as output:
                    output.write(json.dumps(row) + "\n")
                print(f"{case['benchmark']}/{case['id']} attempt {attempt}: {'PASS' if passed else 'FAIL'} ({result.status})", flush=True)
    lower, upper = bootstrap_interval(rows)
    summary = {
        "macro_score": macro_score(rows), "ci_95": [lower, upper],
        "trials": len(rows), "passed": sum(row["passed"] for row in rows), "estimated_cost_usd": round(sum(row["estimated_cost_usd"] for row in rows), 6),
        "benchmarks": {
            name: macro_score([row for row in rows if row["benchmark"] == name])
            for name in sorted({row["benchmark"] for row in rows})
        },
    }
    (arguments.results / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
