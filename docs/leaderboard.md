# Running Turnloom on the Harness Leaderboard

The [Harness Leaderboard](https://huggingface.co/spaces/joelniklaus/harness-leaderboard) compares coding-agent harnesses on the same models. It runs each harness through [Harbor](https://github.com/harbor-framework/harbor) on Terminal-Bench 2.1 and DeepSWE v1.1, meters tokens with a proxy inside each sandbox, and scans every trajectory with [atif-scan](https://github.com/huggingface/atif-scan).

**Status: Turnloom is not on the board.** The board has no submission form. Its README says runs are launched privately by the owner, and its decisions file lists a community submission flow as out of scope for now. Harnesses have been added on the owner's choice and on public requests. What this repository provides is everything the owner, or you, need to run Turnloom the way the board runs the others.

## What is ready

| Piece | Where | Checked how |
| --- | --- | --- |
| Headless agent | `turnloom-agent`, also `python -m turnloom.agent` | Unit tests, plus a live run against a hosted model |
| Harbor adapter | [`integrations/harbor/turnloom_harbor.py`](../integrations/harbor/turnloom_harbor.py) | Run under Harbor 0.24.0, the board's pinned version, on Terminal-Bench 2.1 tasks in Docker. Results and method are in [benchmarks](../benchmarks/README.md). |
| ATIF trajectory | `--atif PATH`, `turnloom atif TRANSCRIPT OUTPUT` | Accepted by `python -m harbor.utils.trajectory_validator` |

The headless agent reads the same settings the board gives every harness.

| Fact | Variable | Fallback |
| --- | --- | --- |
| Endpoint and key | `TURNLOOM_ENDPOINT`, `TURNLOOM_API_KEY` | `HARNESS_ENDPOINT`, `HARNESS_API_KEY`, then `OPENAI_BASE_URL`, `OPENAI_API_KEY` |
| Context window | `HARNESS_CONTEXT_TOKENS` | `HLB_CONTEXT_WINDOW`, then 128,000 |
| Longest reply | `HARNESS_MAX_REPLY_TOKENS` | `HLB_MAX_OUTPUT_TOKENS`, then 16,384 |
| Reasoning effort | `HARNESS_REASONING_EFFORT` | `HLB_REASONING_EFFORT`, then unset |

Behavior that matters under the board's rules:

- **No sampling overrides.** Requests carry no temperature or top-p. Reasoning effort goes flat on the chat-completions request.
- **Strict context windows.** Each request sets its reply limit to what remains of the window after the prompt, with a 20% margin on the estimate. Routes that refuse input plus reply beyond the window get a request that fits.
- **Compaction is recorded.** Older long tool results fold into previews that the model reads back with `recall`. When whole exchanges leave the prompt, the trajectory gets a system step starting `[COMPACTED HISTORY]`.
- **Reasoning is returned** in the field it arrived in, `reasoning_content` or `reasoning`.
- **Trajectory integrity.** The system prompt and task are the first steps. Every model call is one agent step with real timestamps, its tool calls, their full paired results and provider-reported tokens. The file is rewritten after every call, so a run stopped at the time limit still leaves a trajectory.
- **Cheating rule.** The headless agent's system prompt forbids fetching benchmark solutions, answer keys, other agents' trajectories and upstream code that contains the answer. Interactive sessions do not carry this rule.
- **Unattended by design.** `turnloom-agent` approves every command and edit, because a benchmark sandbox has nobody to ask. Do not run it outside a sandbox.

## Run it yourself

Harbor needs Docker or another supported sandbox. Tag a release first so the adapter installs a fixed version.

```sh
python -m pip install "harbor==0.24.0"
export PYTHONPATH=integrations/harbor
harbor run -d terminal-bench@2.1 -a turnloom_harbor:Turnloom -m openai/MODEL_ID \
  --ae OPENAI_API_KEY=$KEY --ae OPENAI_BASE_URL=https://PROVIDER/v1 \
  --ae HLB_CONTEXT_WINDOW=1048576 --ae HLB_MAX_OUTPUT_TOKENS=131072 \
  --ak version=v0.7.2 --n-attempts 3
```

The dataset name is the one Harbor's registry uses. This repository's own runs use a checkout of the task repository instead; see [benchmarks](../benchmarks/README.md). The board itself pins the task repositories by commit: `harbor-framework/terminal-bench-2-1` at `d49e28f` and `datacurve-ai/deep-swe` at `0b9fabb`.

The adapter installs Turnloom with `uv tool install` from this repository at the tag you pass as `version`, on its own Python 3.12, and finishes every download during install. The run phase needs only the model endpoint, which is what DeepSWE's offline agent phase allows.

## Before asking for a run

1. Use a tagged release, `v0.7.2` or later, so the version can be pinned.
2. Run both benchmarks locally on one of the board's models with three attempts, and keep the trajectories.
3. Run `atif-scan` over them and read anything it flags.
4. Share the adapter, the pinned tag and your local numbers with the board's owner. The board's notes put a new harness at about 330 USD of inference plus GPU time, and they rank open-source harnesses ahead of closed ones.

Local scores are not leaderboard scores. Only the board's own runs, on its infrastructure with its metering proxy, appear there.
