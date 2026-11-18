# Compare two configurations

`turnloom-eval` runs each task of a manifest several times on fresh copies of its workspace and scores the result with a command you supply. `turnloom-compare` reports the paired difference between two runs. Use them to check a change to the prompt, the fold policy, a model or a persona against a control before you rely on it.

## Run the shipped example

[`examples/manifest.json`](../examples/manifest.json) has one small task with a real failing test:

```sh
turnloom-eval examples/manifest.json --attempts 3 --no-fold --results results/control
turnloom-eval examples/manifest.json --attempts 3 --results results/fold
turnloom-compare results/control/trials.jsonl results/fold/trials.jsonl
```

Each trial copies the task folder into a temporary directory, runs the agent there with every command and edit approved, runs the check, and deletes the copy. The runner uses your saved connection unless you pass `--endpoint` and `--model`. One trivial task tells you the pipeline works; it does not tell you anything about folding, which only matters in long sessions.

## Write a manifest

A manifest is a JSON array of cases:

```json
{
  "benchmark": "parser",
  "id": "unicode-escape",
  "task": "The escape tests fail. Fix the tokenizer.",
  "workspace": "tasks/parser",
  "check": "{python} -m pytest -q tests/test_escape.py",
  "deadline_seconds": 900,
  "check_timeout_seconds": 120
}
```

`workspace` is resolved relative to the manifest. `check` passes when it exits with status 0; `{python}` expands to the interpreter running the evaluation. The check's whole process tree is ended at its timeout. Keep each task folder pristine and put results outside it. Trials execute model-written commands without asking, so evaluate inside a container or a disposable machine when tasks come from someone else.

## Read the numbers

`summary.json` reports the macro score: each task is scored by its mean over attempts, tasks are averaged within a benchmark, and benchmarks are weighted equally. The 95% interval is a bootstrap that resamples tasks and then attempts within each task. With few tasks the interval is wide; that is the honest answer, not a defect.

`turnloom-compare` requires the same tasks and attempt counts in both runs and reports the paired difference with its interval. An interval that contains zero means the runs are not distinguishable at this sample size.

Freeze the model, provider, reasoning effort, reply limit, deadline and task versions across the two runs, and change one thing. Compare input tokens, output tokens, cached tokens, elapsed time and failures alongside the score: a configuration that passes as often with fewer tokens is the result worth reporting. After you choose a configuration, confirm it on tasks you did not use to choose.

Evaluation sessions add one instruction that interactive sessions do not carry: do not fetch solutions or tailor code to a hidden grader.
