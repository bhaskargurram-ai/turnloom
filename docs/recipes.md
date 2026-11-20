# Recipes

Working examples for specific jobs. Each one says when you would want it, the commands, what to expect and the thing that most often goes wrong. Commands are written for a POSIX shell; a PowerShell version is shown where it differs.

All of them assume Turnloom is installed and, unless stated, a model is connected. See the [quick start](quickstart.md).

| Recipe | In one line |
| --- | --- |
| [Fix a failing test with approvals](#fix-a-failing-test-with-approvals) | Watch every edit and command |
| [Review code without changing anything](#review-code-without-changing-anything) | `--read-only` |
| [Run fully unattended in the Docker image](#run-fully-unattended-in-the-docker-image) | `--yes` inside a container |
| [Use a local model through Ollama](#use-a-local-model-through-ollama) | Nothing leaves the machine |
| [Set a spending budget and read the receipt](#set-a-spending-budget-and-read-the-receipt) | `--budget` |
| [Resume yesterday's session](#resume-yesterdays-session) | `--resume` |
| [Undo what the agent changed](#undo-what-the-agent-changed) | `/undo` and `turnloom undo` |
| [Use an MCP server](#use-an-mcp-server) | `--mcp` |
| [Let another agent delegate to Turnloom](#let-another-agent-delegate-to-turnloom) | `turnloom serve` |
| [Drive Turnloom from an editor](#drive-turnloom-from-an-editor) | `turnloom acp` |
| [Plan, build and review in stages](#plan-build-and-review-in-stages) | `turnloom team` |
| [Run a task in GitHub Actions](#run-a-task-in-github-actions) | The action at the repository root |
| [Compare two configurations](#compare-two-configurations) | `turnloom-eval` and `turnloom-compare` |
| [Put a Provio policy under the agent](#put-a-provio-policy-under-the-agent) | `provio.yaml` |

## Fix a failing test with approvals

**When.** A test fails, you want it fixed, and you want to see each change before it lands.

```sh
turnloom run "tests/test_parser.py fails. Find the cause and fix it without changing the test." --allow "pytest*" --allow "python -m pytest*"
```

**What to expect.** The agent reads and searches files without asking. Before each edit it shows a diff and asks `Apply this edit?`. Test commands that match an `--allow` pattern run without a prompt; any other command asks first. The run ends with the agent's report and a receipt that includes the number of files changed. The exit status is 0 when the task completes. If you do not like the result, `turnloom undo` puts the edited files back.

**What goes wrong.** A pattern never covers a command that chains, pipes or redirects. `pytest -q` matches `"pytest*"`. `cd tests && pytest -q` and `pytest -q > out.txt` do not, so they still ask. That is deliberate. Answer `a` at the prompt to allow that exact command for the rest of the session.

## Review code without changing anything

**When.** You want findings, not edits. Also the right mode for a first look at an unfamiliar repository, and for scripts, because it needs no terminal.

```sh
turnloom run "Review src/payments for bugs. Cite file and line for each finding." --persona reviewer --read-only
```

**What to expect.** No prompts. The agent has only the tools that list, read and search files. It has no edit tools, no shell and no MCP tools, so it cannot change anything. Add `--json` to get the result as one JSON object.

**What goes wrong.** A read-only session cannot run `git diff`, and the file tools do not open the `.git` folder, so a task such as "review my staged changes" has nothing to look at. Write the diff into the workspace first and ask for a review of that file:

```sh
git diff --staged --output=staged.diff
turnloom run "Review the change in staged.diff. Read the files it touches for context." --persona reviewer --read-only
```

Delete `staged.diff` afterwards.

## Run fully unattended in the Docker image

**When.** You want the agent to work without asking, and you do not want its commands to reach anything but one project.

```sh
export TURNLOOM_ENDPOINT=https://openrouter.ai/api/v1
export TURNLOOM_MODEL=MODEL_ID
export TURNLOOM_API_KEY=your-key
docker run --rm -v "$PWD:/workspace" \
  -e TURNLOOM_ENDPOINT -e TURNLOOM_MODEL -e TURNLOOM_API_KEY \
  ghcr.io/bhaskargurram-ai/turnloom:latest run "Fix the failing test" --yes
```

```powershell
$env:TURNLOOM_ENDPOINT = "https://openrouter.ai/api/v1"
$env:TURNLOOM_MODEL = "MODEL_ID"
$env:TURNLOOM_API_KEY = "your-key"
docker run --rm -v "${PWD}:/workspace" -e TURNLOOM_ENDPOINT -e TURNLOOM_MODEL -e TURNLOOM_API_KEY ghcr.io/bhaskargurram-ai/turnloom:latest run "Fix the failing test" --yes
```

**What to expect.** The image holds Turnloom with the `mcp` and `skills` extras, Git and ripgrep. Its entry point is `turnloom`, it runs as an unprivileged user, and it starts in `/workspace`, where your project is mounted. With `--yes` every command and edit is approved. The transcript is written to `.turnloom/` in your project, so `turnloom sessions` and `turnloom undo` work on the host afterwards. Each release publishes a version tag as well as `latest`; pin the version tag in scripts. `docker build -t turnloom .` in a checkout builds the same image locally.

To approve by hand inside the container, add `-it` to `docker run` and leave out `--yes`.

**What goes wrong.** The image has no credential store, so `turnloom connect` cannot save a key there. The connection has to come from the three variables. If one of `TURNLOOM_ENDPOINT` and `TURNLOOM_MODEL` is missing in the container, the run stops with `Custom inference requires both TURNLOOM_ENDPOINT and TURNLOOM_MODEL.` If both are missing it stops with `Choose your provider and model with turnloom connect.` Either way, check that the variables are set in the shell you start Docker from.

## Use a local model through Ollama

**When.** The code must not leave your machine, or you do not want to pay per token.

Start Ollama and install a model that supports tool calls. Then:

```sh
turnloom connect ollama
turnloom chat
```

**What to expect.** `connect` asks for no key. It reads the model list from `http://127.0.0.1:11434/v1` and shows it as a menu. Local servers do not report prices, so receipts show tokens without a cost. When the server reports no context window, Turnloom assumes 128,000 tokens. If your model's window is smaller, say so:

```sh
turnloom chat --context 32768
```

LM Studio, vLLM and llama.cpp work the same way with `turnloom connect lmstudio`, `vllm` or `llamacpp`, each on its default port. For another port or another server, set the endpoint and the model yourself:

```sh
export TURNLOOM_ENDPOINT=http://127.0.0.1:9000/v1
export TURNLOOM_MODEL=MODEL_NAME
turnloom chat
```

```powershell
$env:TURNLOOM_ENDPOINT = "http://127.0.0.1:9000/v1"
$env:TURNLOOM_MODEL = "MODEL_NAME"
turnloom chat
```

**What goes wrong.** `Could not reach Ollama (local) at http://127.0.0.1:11434/v1. Start the local server and try again.` means the server is not running or listens on another port. The other common problem is a model that cannot call tools. When a server does not report tool support, every model is listed, marked `tool support unreported`. Try a small task first, for example `turnloom run "List the files in this directory" --read-only`, and pick another model if it does not use the file tools.

## Set a spending budget and read the receipt

**When.** You want a task to stop before it costs more than you decided.

```sh
turnloom run "Split the config loader into smaller functions" --budget 0.25
```

If the saved connection has no prices, supply them in USD per million tokens:

```sh
turnloom run "Split the config loader into smaller functions" --budget 0.25 --input-rate 1 --output-rate 4
```

**What to expect.** Before each model request Turnloom estimates what the task has cost and what the next request would add. As the allowance runs low it shortens the reply limit to what is affordable. When the next request would not fit, the task stops with the status `budget_limit`, a sentence saying how much was spent, and exit status 1.

The receipt is the last line of every run. Its parts are separated by dots:

| Part | Meaning |
| --- | --- |
| Status | `completed`, `partial`, `stopped`, `error`, `timeout`, `step_limit` or `budget_limit` |
| `N input / N output tokens` | Tokens the provider reported for the whole task. `(N cached)` follows when the provider reported cached input. `(estimated: the provider omitted usage)` follows when Turnloom had to estimate. |
| `N context characters folded` | How much old tool output was replaced by previews |
| `N files changed` | Present when the agent edited files |
| `estimated $N` | Present when prices are known |

The budget applies to one task. In `chat`, every turn gets the full allowance again. In `team`, the three stages share it.

**What goes wrong.** With a model whose prices are unknown, which includes custom endpoints and local models, `--budget` alone stops with a message asking for `--input-rate` and `--output-rate`. The figure is an estimate from token counts and the prices saved when you connected, not your provider's bill. Run `turnloom connect` again to refresh the prices.

## Resume yesterday's session

**When.** You closed the terminal and want to continue with the same context.

```sh
turnloom sessions
turnloom chat --resume
turnloom chat --resume 8396
turnloom run "Now add a test for the empty case" --resume
```

`--resume` alone takes the most recent session in the workspace. With a value it takes the session whose ID starts with those characters.

**What to expect.** The header line ends with `resumed`. The model receives the earlier conversation, including tool results, rebuilt from the transcript. New turns are appended to the same file. The persona and the model are the ones of the command you run now, not necessarily the ones the session started with.

**What goes wrong.** `No saved session here. Run turnloom sessions to list them.` Sessions belong to a workspace and live in its `.turnloom/` folder. Change to the directory you worked in, or pass `--workspace`. `Session prefix 83 is ambiguous.` means two sessions start with those characters; type more of the ID.

## Undo what the agent changed

**When.** A turn edited files and you want them back as they were.

Inside `chat`:

```text
/diff
/undo
```

After a `run` or `team`, or after leaving the chat:

```sh
turnloom undo
turnloom undo 8396
```

In Studio, use **Revert edits** after a turn.

**What to expect.** One line per file: `Restored NAME`. A file the agent created is deleted. A file you changed after the agent's edit is not touched; it is reported as `Kept NAME: it changed after the agent's edit` and `turnloom undo` exits with status 1. Undo reaches back one turn. `turnloom undo` looks at the last turn of the session; if that turn edited nothing it prints `No file edits recorded for the last turn.`

**What goes wrong.** Undo knows only about edits made with the agent's file tools. Anything a shell command did, such as a formatter rewriting files, a code generator or an installed package, is not recorded and not reversed. Commit your work before a long session so that `git status` and `git diff` show everything that changed.

## Use an MCP server

**When.** The agent needs a tool that is not built in, such as a browser, an issue tracker or a database.

Install the MCP client with `pip install "turnloom[mcp]"`, or the `full` extra, which also installs the Git and Time reference servers. Write a configuration file, for example `servers.json`:

```json
{
  "mcpServers": {
    "time": {"command": "mcp-server-time", "args": ["--local-timezone", "UTC"]}
  }
}
```

Check the server without a model, then use it:

```sh
turnloom tools servers.json time
turnloom chat --mcp servers.json
```

**What to expect.** `turnloom tools` starts the server and prints JSON with `count`, `names` and the schemas of the five best matches. In a session the model gets two tools however many servers you configure: `mcp_search` lists a server's tools and `mcp_call` runs one. A server starts on first use. Every call asks for approval and shows the server, the tool and its arguments:

```text
MCP time / TOOL_NAME {ARGUMENTS}
Run this? [y]es / [n]o / [a]lways allow this exact request:
```

**What goes wrong.** The server starts but cannot find its token. A server process does not inherit your whole environment. List each variable it needs in `pass_env`, or set it in `env`, as described in [Configuration](configuration.md#mcp-servers). Two other things to check: `command` has to be a program on your `PATH` or a full path, and a `--read-only` session gets no MCP tools at all.

## Let another agent delegate to Turnloom

**When.** You work in Claude Code, Cursor or another agent that can use MCP servers, and you want it to read Turnloom's sessions or hand Turnloom a task.

Install `turnloom[mcp]` and connect a model. Then register Turnloom in the other agent as a local MCP server with the command `turnloom` and these arguments. In clients that read an `mcpServers` map the entry looks like this; where that file lives depends on the client:

```json
{
  "mcpServers": {
    "turnloom": {
      "command": "turnloom",
      "args": ["serve", "--workspace", "/absolute/path/to/project", "--allow-run"]
    }
  }
}
```

**What to expect.** The other agent sees these tools:

| Tool | Returns |
| --- | --- |
| `sessions` | The 100 most recent session IDs in the workspace |
| `receipt` | Turns, token totals, estimated cost and statuses for one session |
| `transcript` | A page of a session: tasks, tool calls with short previews, answers |
| `read_result` | A slice of one tool result from a saved session |
| `archive_output`, `recall` | Park a long text outside the calling agent's conversation and read it back by slice or search |
| `run_task` | Only with `--allow-run`. Runs a task with your connected model and returns the answer, usage, files changed and the session ID |

A delegated task uses your model connection and your tokens. It is read-only unless you add `--allow-edits`, `--allow-shell` or both. Nobody can be asked for approval over MCP, so those flags are the whole policy: anything not enabled is declined. `run_task` takes a persona (default `builder`) and a step limit (default 30, at most 100). The session is saved in `.turnloom/` like any other, so `turnloom replay` and `turnloom undo` work on it.

**What goes wrong.** `--allow-edits and --allow-shell extend --allow-run. Pass --allow-run as well.` The two extra flags do nothing by themselves. If the client cannot start the server at all, it probably cannot find `turnloom`: give the full path to the executable, or use your Python as the command with the arguments `["-m", "turnloom", "serve", ...]`.

## Drive Turnloom from an editor

**When.** Your editor supports the Agent Client Protocol and you want Turnloom's agent in its chat panel.

Install the `acp` extra into the environment that holds Turnloom, and connect a model:

```sh
python -m pip install "turnloom[acp]"
turnloom connect
```

In the editor, add a custom agent whose command is `turnloom` with the single argument `acp`. The [integration guide](integrations.md#editors-agent-client-protocol) has the settings for Zed and JetBrains IDEs. Add `"--persona", "reviewer"` to the arguments to change the persona.

**What to expect.** Each editor thread is a Turnloom session in the project the editor opens, saved under `.turnloom/`. Replies stream. File reads, searches, edits and commands appear as tool calls, and edits are shown as diffs. The editor offers three modes: Ask (the default), Auto-edit and Read-only. A permission prompt offers Allow, Reject and an "always" choice that covers either all edits in the session or that exact command. Prompts take text and attached text files, not images or audio. Stdio MCP servers the editor passes along are used; HTTP ones are skipped and named in the first reply.

The integration guide notes that this has been tested against the protocol SDK's own client and not yet inside each editor.

**What goes wrong.** The reply says `Turnloom has no model to use.` The editor started Turnloom without a connection it can see. Run `turnloom connect` in a terminal as the same user, or set `TURNLOOM_ENDPOINT`, `TURNLOOM_MODEL` and `TURNLOOM_API_KEY` in the agent's `env` in the editor's settings. Also save your buffers: Turnloom reads files from disk.

## Plan, build and review in stages

**When.** The task is large enough that you want a plan before any edit and a second look afterwards.

```sh
turnloom team "Add input validation to the signup form" --budget 0.50
```

**What to expect.** Three stages run in order in one workspace. *Plan* inspects the code and lists the changes and checks needed. *Build* does the work and asks for approvals like `run`. *Review* reads what changed and reports findings. Plan and review have no edit or shell tools. Each stage gets the task and a short report from the stages before it. They share the deadline and the budget. Tool activity is shown while the stages run. The text is not streamed: at the end the three reports are printed, each headed by its stage name, followed by one receipt for the whole run. `turnloom undo` afterwards restores the files the build stage edited.

With the `team` extra installed the stages run as a LangGraph graph. Without it they run in a plain loop with the same behaviour.

**What goes wrong.** The review stage cannot run the tests, because it is read-only. It reports what it could not verify. Ask for the checks in the task, so the build stage runs them, or run them yourself afterwards. Also note that `team` has no `--resume`, and that a stage which does not complete stops the run.

## Run a task in GitHub Actions

**When.** You want a review, a report or a fix on every push or pull request.

```yaml
jobs:
  review:
    runs-on: ubuntu-latest
    permissions:
      contents: read
    steps:
      - uses: actions/checkout@v7
      - id: turnloom
        uses: bhaskargurram-ai/turnloom@v0.7.2
        with:
          task: Review the code under src/ for bugs and report the three most important findings.
          persona: reviewer
          mode: read-only
          endpoint: https://openrouter.ai/api/v1
          model: ${{ vars.TURNLOOM_MODEL }}
          api-key: ${{ secrets.OPENROUTER_API_KEY }}
      - run: echo "${{ steps.turnloom.outputs.status }}"
```

Use the newest release tag after `@`.

**What to expect.** The action installs Turnloom, runs one task with `turnloom run --json`, and writes the answer to the job summary.

| Input | Default | Meaning |
| --- | --- | --- |
| `task` | required | What the agent should do |
| `endpoint`, `model` | required | The OpenAI-compatible base URL and the model ID |
| `api-key` | empty | The credential. Pass a secret. |
| `mode` | `read-only` | `read-only`, `auto-edit` (may edit files, commands are declined) or `unattended` (may edit and run commands) |
| `persona` | `builder` | A persona ID |
| `workspace` | `.` | The directory the agent works in |
| `max-steps` | 40 | Model calls allowed |
| `deadline` | 900 | Seconds allowed |
| `budget` | 0 | Estimated USD the task may spend, 0 for no limit. Added in 0.7.2. |
| `input-rate`, `output-rate` | empty | USD per million tokens, which a budget needs unless the model is free |
| `python-version` | 3.12 | The Python that runs Turnloom |
| `fail-on-incomplete` | `true` | Fail the step unless the task completes |

| Output | Holds |
| --- | --- |
| `status` | The final status |
| `answer` | The agent's final answer |
| `files-changed` | The paths the agent edited, one per line |
| `result-file` | The path of the full JSON result |

In `unattended` mode the agent runs commands on the runner without asking. Give the job only the permissions and secrets the task needs, and do not run it on pull requests from people you do not trust.

Without the action, the same thing is `turnloom run "..." --read-only --json` with `TURNLOOM_ENDPOINT`, `TURNLOOM_MODEL` and `TURNLOOM_API_KEY` set.

**Going further.** This repository runs the action on its own pull requests and issues: a review posted as a comment, triage of new issues, and an attempt at an issue when a maintainer labels it. [Agents in CI](automation.md) describes those workflows, how they stay safe on text from strangers, and how to copy them.

**What goes wrong.** The step fails although the agent produced an answer. `fail-on-incomplete` is on by default, and a task that ends as `partial`, for example at the step limit, is not complete. Raise `max-steps`, or set `fail-on-incomplete: "false"` and decide from `outputs.status` yourself. Separately, the action installs Turnloom without extras: in a repository that has a `provio.yaml`, the run stops because Provio is not installed.

## Compare two configurations

**When.** You changed a prompt, a model, a persona or the fold setting and want to know whether it helps, on tasks with a check you trust.

From a checkout of the repository, the shipped example compares folding off with folding on:

```sh
turnloom-eval examples/manifest.json --attempts 3 --no-fold --results results/control
turnloom-eval examples/manifest.json --attempts 3 --results results/fold
turnloom-compare results/control/trials.jsonl results/fold/trials.jsonl
```

**What to expect.** These commands call your model: every task runs `--attempts` times in each run. Each trial copies the task's folder to a temporary directory, runs the agent there with every command and edit approved, runs the task's check command, and prints a line such as `local-smoke/slugify-case attempt 1: PASS (completed)`. A run leaves `trials.jsonl`, `summary.json` and a `transcripts/` folder in its results directory. `summary.json` holds the macro score, a 95% interval, the number of trials passed and the estimated cost. `turnloom-compare` prints the paired difference between the two runs with its own 95% interval. An interval that contains zero means the runs cannot be told apart at this sample size.

[Compare two configurations](experiments.md) explains the manifest format and how to read the numbers.

**What goes wrong.** `results/control/trials.jsonl already exists. Choose a new --results directory for each run.` The runner never overwrites results. Use a fresh directory per run. `turnloom-compare` in turn refuses runs that do not have the same tasks and the same number of attempts.

## Put a Provio policy under the agent

**When.** Approvals are not enough: you want rules that refuse some calls outright, whatever anyone answers at the prompt, and a record of every decision.

Install the `guard` extra into the environment that holds Turnloom:

```sh
python -m pip install "turnloom[guard]"
```

Create `provio.yaml` at the root of the workspace. Studio's Policy view writes this starter policy for you; you can also save it by hand:

```yaml
version: 1
default: allow
packs: [floor, secrets-guard]
```

Then work as usual:

```sh
turnloom chat
```

**What to expect.** Turnloom prints `Provio policy gate on: provio.yaml` at the start. From then on each shell command, file read, file edit and MCP call goes to Provio before the approval prompt.

| Verdict | What happens in the terminal |
| --- | --- |
| deny | The tool does not run. The model is told which rule refused it and why. |
| ask | You are asked at the usual prompt. With `--yes` nobody is there to answer, so the call is refused. |
| allow | The call goes on to the usual approval: your prompt, or your `--auto-edit`, `--allow` and "always" choices. With `--yes` it runs. |
| redact | The call runs and the model receives Provio's redacted output. |

Every decision is appended to `.provio/ledger.jsonl`. If Provio cannot answer, the tool does not run. `turnloom doctor` shows `provio.yaml` on its `Policy` line when the file is found. Studio's Policy view shows the decisions, tests a command or a path against the policy, and checks the ledger. To run once without the policy, pass `--no-gate`.

The rules themselves are Provio's; its [documentation](https://github.com/writ-agent/provio) describes the policy language.

**What goes wrong.** `This workspace has a provio.yaml but Provio is not installed.` Turnloom refuses to run ungated in a workspace that has a policy. This happens after cloning a repository that ships a `provio.yaml`, and in the Docker image, which does not include the `guard` extra. Install `turnloom[guard]`, or pass `--no-gate` if you accept running without the policy.
