"""Join Harbor's per-trial results with the meter's ledger into one row per agent and task.

    python collect.py --out OUT_DIR --ledger usage.jsonl [--json results.json]
"""

import argparse
import json
from datetime import datetime
from pathlib import Path


def seconds(span):
    if not span or not span.get("started_at") or not span.get("finished_at"):
        return None
    return (datetime.fromisoformat(span["finished_at"]) - datetime.fromisoformat(span["started_at"])).total_seconds()


def trial(path):
    """Reward, error and agent time for one job folder, or None while it has no finished trial."""
    for result in sorted(path.glob("*__*/result.json")):
        data = json.loads(result.read_text(encoding="utf-8"))
        rewards = (data.get("verifier_result") or {}).get("rewards") or {}
        error = data.get("exception_info") or {}
        return {"reward": rewards.get("reward"), "error": error.get("exception_type"), "agent_seconds": seconds(data.get("agent_execution")),
                "reported": {key: (data.get("agent_result") or {}).get(key) for key in ("n_input_tokens", "n_cache_tokens",
                                                                                            "n_output_tokens", "cost_usd")},
                "version": (data.get("agent_info") or {}).get("version")}
    return None


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", required=True)
    parser.add_argument("--ledger", required=True)
    parser.add_argument("--json")
    options = parser.parse_args()
    usage = {}
    for line in Path(options.ledger).read_text(encoding="utf-8").splitlines():
        entry = json.loads(line)
        row = usage.setdefault(entry["tag"], {"requests": 0, "failed": 0, "refused": 0, "unmetered": 0, "input": 0, "cached": 0,
                                              "output": 0, "reasoning": 0, "usd": 0.0, "models": {}})
        if entry.get("refused"):
            row["refused"] += 1
            continue
        row["requests"] += 1
        row["models"][entry.get("model") or "?"] = row["models"].get(entry.get("model") or "?", 0) + 1
        if "usd" in entry:
            for key in ("input", "cached", "output", "reasoning", "usd"):
                row[key] += entry[key]
        elif entry["status"] >= 400:
            row["failed"] += 1
        else:
            row["unmetered"] += 1
    rows = []
    for folder in sorted(Path(options.out).iterdir()):
        if not folder.is_dir():
            continue
        for job in sorted(item for item in folder.iterdir() if item.is_dir()):
            found = trial(job)
            if found is None:
                continue
            rows.append({"agent": folder.name, "task": job.name, **found, **usage.get(f"{folder.name}__{job.name}", {})})
    if options.json:
        Path(options.json).write_text(json.dumps(rows, indent=1), encoding="utf-8")
    print(f"{'agent':15} {'task':30} {'reward':>6} {'error':24} {'sec':>5} {'calls':>5} {'fail':>4} {'input':>8} {'cached':>8} {'output':>7} {'usd':>6}")
    for row in rows:
        took = "" if row["agent_seconds"] is None else f"{row['agent_seconds']:.0f}"
        print(f"{row['agent']:15} {row['task'][:30]:30} {row['reward']!s:>6} {(row['error'] or '')[:24]:24} {took:>5} "
              f"{row.get('requests', 0):>5} {row.get('failed', 0):>4} {row.get('input', 0):>8} {row.get('cached', 0):>8} "
              f"{row.get('output', 0):>7} {row.get('usd', 0):>6.3f}")


if __name__ == "__main__":
    main()
