# Compare two configurations

Prepare a manifest following [the example](../examples/manifest.json). Use absolute pristine workspace paths, trusted post-run check commands, and a separate output directory per configuration. Run in an isolated environment for tasks that execute untrusted code.

```sh
turnloom-eval tasks.json --attempts 3 --no-fold --results results/control
turnloom-eval tasks.json --attempts 3 --results results/fold
turnloom-compare results/control/trials.jsonl results/fold/trials.jsonl
```

Freeze model, provider, reasoning effort, reply limits, rates, deadlines and task versions across runs. Score each task by its mean over attempts, then average task scores within each benchmark and weight benchmarks equally. Compare the paired difference interval alongside input tokens, output tokens, elapsed time and failures. Use a held-out task set after choosing a configuration.

The local runner copies files into a new directory per trial. The official Harness Leaderboard also requires its Harbor environment, metering proxy, model controls and trajectory audit. Official evaluation and a leaderboard score are outstanding.
