"""Compare two evaluation runs with paired task bootstrap intervals."""

from __future__ import annotations

import argparse
import json
import random
from collections import defaultdict
from pathlib import Path
from typing import Any


def read_trials(path: Path) -> dict[str, dict[str, list[float]]]:
    tasks: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    with path.open(encoding="utf-8") as source:
        for line in source:
            if line.strip():
                row = json.loads(line)
                tasks[row["benchmark"]][row["task_id"]].append(float(row["passed"]))
    if not tasks:
        raise ValueError(f"No trials in {path}")
    return tasks


def paired_difference(
    baseline: dict[str, dict[str, list[float]]],
    candidate: dict[str, dict[str, list[float]]],
    samples: int = 5000,
    seed: int = 42,
) -> dict[str, Any]:
    if set(baseline) != set(candidate):
        raise ValueError("Runs have different benchmark sets")
    if not baseline:
        raise ValueError("Runs contain no trials")
    benchmark_differences = {}
    paired: dict[str, list[tuple[list[float], list[float]]]] = {}

    def mean(values):
        return sum(values) / len(values)

    for benchmark in baseline:
        if set(baseline[benchmark]) != set(candidate[benchmark]):
            raise ValueError(f"Runs have different tasks in {benchmark}")
        pairs = []
        for task_id in baseline[benchmark]:
            first = baseline[benchmark][task_id]
            second = candidate[benchmark][task_id]
            if len(first) != len(second):
                raise ValueError(f"Attempt count differs for {benchmark}/{task_id}")
            pairs.append((first, second))
        paired[benchmark] = pairs
        benchmark_differences[benchmark] = mean([mean(second) - mean(first) for first, second in pairs])
    difference = mean(list(benchmark_differences.values()))
    generator = random.Random(seed)
    draws = []
    for _ in range(samples):
        draw = []
        for pairs in paired.values():
            # Resample tasks, then attempts within each side of a drawn task.
            chosen = generator.choices(pairs, k=len(pairs))
            draw.append(mean([mean(generator.choices(second, k=len(second))) - mean(generator.choices(first, k=len(first)))
                              for first, second in chosen]))
        draws.append(mean(draw))
    draws.sort()
    return {
        "macro_difference": difference,
        "ci_95": [draws[int(samples * 0.025)], draws[min(samples - 1, int(samples * 0.975))]],
        "benchmark_differences": benchmark_differences,
        "tasks": sum(len(pairs) for pairs in paired.values()),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Compare baseline and candidate evaluation runs")
    parser.add_argument("baseline", type=Path, help="Baseline trials.jsonl")
    parser.add_argument("candidate", type=Path, help="Candidate trials.jsonl")
    arguments = parser.parse_args()
    try:
        report = paired_difference(read_trials(arguments.baseline), read_trials(arguments.candidate))
    except (OSError, ValueError, KeyError) as error:
        parser.error(f"Cannot compare these runs: {error}")
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
