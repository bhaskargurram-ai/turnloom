import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

BENCHMARKS = Path(__file__).resolve().parents[1] / "benchmarks"


def load(name):
    spec = importlib.util.spec_from_file_location("bench_" + name, BENCHMARKS / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class BenchmarkScriptTests(unittest.TestCase):
    def test_the_task_sample_is_seeded_and_respects_the_limits(self):
        run = load("run")
        with tempfile.TemporaryDirectory() as folder:
            for index in range(12):
                task = Path(folder) / f"task-{index:02}"
                task.mkdir()
                seconds, memory = (900, 2048) if index < 9 else (3600, 2048) if index < 11 else (900, 8192)
                task.joinpath("task.toml").write_text(f"[agent]\ntimeout_sec = {seconds}.0\n[environment]\nmemory_mb = {memory}\n",
                                                      encoding="utf-8")
            names, eligible = run.sample(folder, 5, seed=0)
            self.assertEqual(eligible, 9)
            self.assertEqual(len(set(names)), 5)
            self.assertTrue(all(name < "task-09" for name in names))
            self.assertEqual(run.sample(folder, 5, seed=0)[0], names)
            # A longer sample extends a shorter one, so a pilot on the first tasks is part of the full run.
            self.assertEqual(run.sample(folder, 8, seed=0)[0][:5], names)

    def test_results_join_trials_with_the_ledger(self):
        collect = load("collect")
        with tempfile.TemporaryDirectory() as folder:
            trial = Path(folder) / "out" / "turnloom" / "fix-git" / "fix-git__abc"
            trial.mkdir(parents=True)
            trial.joinpath("result.json").write_text(json.dumps({
                "verifier_result": {"rewards": {"reward": 1.0}}, "exception_info": None, "agent_info": {"version": "0.7.1"},
                "agent_execution": {"started_at": "2026-11-18T10:00:00", "finished_at": "2026-11-18T10:01:30"}}), encoding="utf-8")
            running = Path(folder) / "out" / "turnloom" / "still-running"
            running.mkdir()
            self.assertEqual(collect.trial(trial.parent)["reward"], 1.0)
            self.assertEqual(collect.trial(trial.parent)["agent_seconds"], 90.0)
            self.assertIsNone(collect.trial(running))

    def test_summary_ranks_by_solved_then_cost_and_counts_errors_as_unsolved(self):
        report = load("report")
        rows = [{"agent": "turnloom", "task": "a", "reward": 1.0, "error": None, "agent_seconds": 50, "usd": 0.10, "version": "0.7.1"},
                {"agent": "turnloom", "task": "b", "reward": None, "error": "AgentTimeoutError", "agent_seconds": 900, "usd": 0.30},
                {"agent": "codex", "task": "a", "reward": 1.0, "error": None, "agent_seconds": 40, "usd": 0.05},
                {"agent": "codex", "task": "b", "reward": 0.0, "error": None, "agent_seconds": 60, "usd": 0.05},
                {"agent": "aider", "task": "a", "reward": 0.0, "error": None, "agent_seconds": 10, "usd": 0.01},
                {"agent": "aider", "task": "b", "reward": 0.0, "error": None, "agent_seconds": 10, "usd": 0.01}]
        table = report.summarise(rows)
        self.assertEqual([row["agent"] for row in table], ["codex", "turnloom", "aider"])
        self.assertEqual((table[1]["solved"], table[1]["errors"]), (1, 1))
        self.assertAlmostEqual(table[1]["usd_per_task"], 0.20)
        self.assertIsNone(table[2]["usd_per_solved"])
        low, high = report.wilson(10, 20)
        self.assertTrue(0.29 < low < 0.31 and 0.69 < high < 0.71)
        self.assertEqual(report.wilson(0, 0), (0.0, 0.0))
        figure = report.figure(table, "model", "title")
        self.assertIn("Turnloom", figure)
        self.assertIn("$0.200", figure)
        self.assertIn("| **Turnloom** | 0.7.1 | 1 of 2 |", report.markdown(table))

    def test_meter_reads_usage_from_both_api_shapes(self):
        try:
            meter = load("meter")
        except ImportError:
            self.skipTest("requires aiohttp")
        chat = {"prompt_tokens": 100, "completion_tokens": 20, "prompt_tokens_details": {"cached_tokens": 60},
                "completion_tokens_details": {"reasoning_tokens": 5}}
        responses = {"input_tokens": 100, "output_tokens": 20, "input_tokens_details": {"cached_tokens": 60},
                     "output_tokens_details": {"reasoning_tokens": 5}}
        self.assertEqual(meter.counts(chat), (100, 60, 20, 5))
        self.assertEqual(meter.counts(responses), (100, 60, 20, 5))
        self.assertEqual(meter.counts({"prompt_tokens": 7, "completion_tokens": 1}), (7, 0, 1, 0))
        self.assertEqual(meter.price("gpt-5.1-2025-11-13"), (1.25, 0.125, 10.0))
        self.assertIsNone(meter.price("some-other-model"))


if __name__ == "__main__":
    unittest.main()
