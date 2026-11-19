# Agents in this repository's CI

Turnloom works on its own repository. Three workflows run it on pull requests and issues, using the [action at the repository root](recipes.md#run-a-task-in-github-actions). This page says what each one does, how to turn them on, what they cost, and how they are kept from doing harm. It ends with how to use them in your own repository.

All three are off until the repository has a `TURNLOOM_API_KEY` secret. Without it each run ends with a notice and does nothing.

## What runs

| Workflow | Starts when | The agent can | What comes out |
| --- | --- | --- | --- |
| [Agent review](../.github/workflows/agent-review.yml) | Someone with write access opens or updates a pull request, or a maintainer adds the label `agent: review` to anyone's pull request | Read the base branch and the pull request's diff. No edits, no commands. | One comment on the pull request, replaced on each run |
| [Agent triage](../.github/workflows/agent-triage.yml) | Anyone but the owner opens an issue, or a maintainer runs it by hand for an issue number | Read the repository, the issue and the titles of the open issues. No edits, no commands. | Labels from a fixed list, and one comment naming the code involved |
| [Agent attempt](../.github/workflows/agent-attempt.yml) | A maintainer adds the label `agent: attempt` to an issue | Read and edit a copy of the repository. No commands. | A branch named `agent/issue-N-run-M` and a comment saying whether the linter and the tests pass on it |

A comment from an agent ends with a line saying that Turnloom wrote it, from what, in which run, and with how many model calls and tokens. It is a suggestion. Nothing an agent writes merges, closes or approves anything.

### How an attempt goes

1. The agent reads the issue and its comments and edits files. It cannot run anything.
2. A separate job applies the edits and runs `ruff check .` and the test suite.
3. If they fail, the agent gets one more pass, with the end of the output to read.
4. The checks run again.
5. The edits are pushed to a new branch, and a comment on the issue links to it and reports the checks. If they still fail, the comment shows the end of their output.

A person opens the pull request from that branch, which runs the full test matrix. To drop an attempt, delete its branch. To ask for another, add the label again: it is taken off after every run, and the comments on the issue, including the last attempt's, are part of what the agent reads.

## Turn it on

1. Make an API key for an OpenAI-compatible provider. Make one for this purpose alone, and set a spending limit on it at the provider.
2. Store it as a repository secret:

   ```sh
   gh secret set TURNLOOM_API_KEY
   ```

3. Set repository variables for anything you want to change. All are optional.

   | Variable | Default | Meaning |
   | --- | --- | --- |
   | `TURNLOOM_ENDPOINT` | `https://api.openai.com/v1` | The provider's base URL |
   | `TURNLOOM_MODEL` | `gpt-5.1` | The model ID |
   | `TURNLOOM_INPUT_RATE` | `1.25` | USD per million input tokens for that model |
   | `TURNLOOM_OUTPUT_RATE` | `10` | USD per million output tokens for that model |
   | `TURNLOOM_REVIEW_BUDGET` | `0.50` | USD one review may spend |
   | `TURNLOOM_TRIAGE_BUDGET` | `0.25` | USD one triage may spend |
   | `TURNLOOM_ATTEMPT_BUDGET` | `1.00` | USD one pass of an attempt may spend. An attempt has at most two passes. |

   The two rates are how a budget is turned into tokens. Change them when you change the model.

4. Make sure the labels `agent: review` and `agent: attempt` exist.

A free model works too, such as one of OpenRouter's `:free` models that lists tool support (`turnloom models openrouter --tier free` shows them). Set the two rates to a small nominal price such as `0.05` and `0.2`: at the default rates a free model that reads a lot stops at its budget before it answers. Run the triage workflow by hand on one issue first and read what it posts, because free models vary a great deal. This repository runs on one; its first triage took 19 model calls and 173,000 input tokens where `gpt-5.1` took 5 and 22,000.

To turn everything off again, delete the secret. Runs that are already in progress finish. To turn one workflow off, disable it under the repository's Actions tab.

## What it costs

These are single runs on this repository with `gpt-5.1` in November 2026, before the workflows were switched on. Expect yours to differ.

| Run | Model calls | Input tokens | Cost |
| --- | --- | --- | --- |
| Review of a 1,178-line diff | 6 | 74,000 | 0.10 USD |
| Triage of a bug report | 5 | 22,000 | 0.03 USD |
| Attempt, first pass | 14 and 22 | 87,000 and 139,000 | 0.12 and 0.19 USD |
| Attempt, repair pass | 20 | 113,000 | 0.15 USD |

A run stops when it reaches its budget, its limit on model calls or its time limit, and the comment says which. The budget is an estimate made from token counts and the rates you set, so treat the provider's own spending limit as the real one.

## How it is kept safe

An agent in CI reads text that strangers wrote: an issue, a pull request description, a diff. The design assumes that such text can talk the model into anything, and limits what "anything" can reach.

- **The job that holds the model key cannot write and cannot run commands.** Review and triage run in `read-only` mode. An attempt runs in `auto-edit` mode, where the agent edits files and every command is declined. These jobs get a token that can only read.
- **A different job posts the result, and it never talks to the model.** It treats the agent's answer as text. [`scripts/agent_reply.py`](../scripts/agent_reply.py) makes mentions inert, escapes markup, turns images and links that leave the repository into plain text, and cuts the answer at 6,000 characters. Labels are applied only if they are on the list in the workflow.
- **A pull request's code is never checked out.** The review runs on `pull_request_target`, in the base branch, and the change reaches the agent as a diff file.
- **The agent's edits are executed in one place.** That is the check job. It has no secret, and its token can only read.
- **The code that runs is never the code the agent edited.** In an attempt the action is installed from an untouched checkout while the agent edits a second copy, and the runner starts Python with `-P` so that nothing in the workspace is importable. The job that pushes applies the edits in a separate working tree.
- **Edits under `.github/` are not pushed,** and the edits reach `main` only through a pull request that a person opens.
- **Starting a run is limited.** A review of an outsider's pull request and every attempt need a label, which only people with triage access can add, and the label is taken off after each run. Triage runs for anyone who opens an issue, so its budget is the limit: a flood of issues costs at most the triage budget for each.
- **The agent sees what you see.** HTML comments are removed from the issue or description before the agent reads it, because they are invisible on the page.
- **Text from an issue or pull request never reaches a shell.** The workflows pass numbers, and fetch the text into files.

[`tests/test_automation.py`](../tests/test_automation.py) checks these rules against the workflow files, so a change that breaks one fails the suite.

### What this does not cover

- An agent that has been talked into something can still write a wrong or misleading comment. The footer says what wrote it for that reason.
- The check result in an attempt's comment comes from a job that ran the agent's code. Treat "pass" as a hint. The test run on the pull request is the one that counts.
- An attempt cannot run anything itself, so it works best on a small, well-described issue. In two trials on this repository, one produced a change that passed the linter and the suite on the first pass. The other did not solve its issue after the repair pass, and its comment would have said so. A sandbox for commands ([#16](https://github.com/bhaskargurram-ai/turnloom/issues/16)) is what would let the agent run the tests itself.
- The provider sees whatever the agent reads. Here that is a public repository.

## Use it in your own repository

The workflows are written for this repository but carry little that is specific to it.

1. Copy the four files `.github/workflows/agent-*.yml` and `scripts/agent_reply.py`.
2. Replace each `uses: ./` with the action at a commit you have read, such as `uses: bhaskargurram-ai/turnloom@<commit>`. The `budget` inputs are newer than release 0.7.1, so use a commit of `main` until the next release.
3. In `agent-check.yml`, replace the install, lint and test commands with your project's.
4. In `agent-triage.yml`, replace the `LABELS` list with your labels.
5. In `agent-review.yml`, replace the list of project rules in the task with your own, or point it at your `AGENTS.md`.
6. Follow [Turn it on](#turn-it-on).

The single-step version, a review on every push with no comment posted, is in [Recipes](recipes.md#run-a-task-in-github-actions).
