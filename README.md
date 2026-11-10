![Turnloom — a coding agent with recoverable context](docs/banner.svg)

Turnloom is a local coding workbench for people who want to see what their agent keeps in context. Open a repository, choose a specialist and model, and review commands beside the file you're editing. When a long tool result leaves the prompt, you and the agent can still read it from the session archive.

[Quick start](#quick-start) · [Agent loop](docs/design.md) · [Experiments](docs/experiments.md) · [Contributing](CONTRIBUTING.md)

## Quick start

On Windows, double-click **Open Turnloom Studio.cmd** in the checkout and choose a workspace folder. The launcher uses Python 3.11+ and installs Studio into a local virtual environment on first launch. On other platforms, or from an existing Python environment, run:

```sh
python -m pip install .
turnloom-studio --workspace .
```

Studio opens in your browser. Choose the model control in the top bar to connect a provider. Open a file, select a persona in the agent panel, and send a task. Use **Close Studio** in the footer to stop the local server.

![Turnloom Studio with the workspace editor, Context Loom and agent panel](docs/studio-preview.png)

**Read the Context Loom from left to right.** Each ribbon starts at the size of a tool result and ends at the size retained in the prompt. Click a reference to read the archived output without spending a model turn. The initial view is labeled **Sample run**. After you send a task, the inspector follows your session. Its percentage measures characters removed from tool results. The session receipt reports model usage separately.

The editor uses CodeMirror 6. The agent panel uses Cline's conversation header, command output and approval components, connected to Turnloom's own agent loop. [CodeMirror](https://github.com/codemirror/dev) · [Cline UI](https://github.com/cline/cline/tree/92b9f722a16d2a03c1153e12f9a0e759455f509d/sdk/packages/ui)

### Terminal

From the repository root, with Python 3.11 or newer:

```sh
python -m pip install .
turnloom demo
turnloom personas
```

For a live session, pick a provider and a model in the terminal. The first interactive chat also opens the picker automatically.

```sh
turnloom connect
turnloom chat --workspace . --persona debugger
```

Choose OpenRouter to sign in through your browser, or choose Ollama to browse models on your running local server. Try `turnloom models openrouter --tier free --open-weights` to browse free open-weight routes. Enter `/quit` to close a chat. [Provider setup and coverage](docs/providers.md)

| Command | Use it for |
| --- | --- |
| `turnloom run "Fix the failing parser test"` | Implement and verify a change |
| `turnloom chat --persona reviewer` | Review a diff and discuss findings across turns |
| `turnloom run "Design a migration" --persona architect` | Explore interfaces and migration steps |
| `turnloom replay PATH_TO_JSONL` | Inspect tool calls, observations and receipts offline |
| `turnloom-eval --help` | Run repeated trials on pristine task copies |
| `turnloom-compare --help` | Compare paired task outcomes |

## Control spend

**Optional integrations.** Install `python -m pip install ".[full]"` to run the LangGraph team, load Agent Skills, and connect MCP servers. Try `turnloom tools examples/mcp.json git git_status` after activating the virtual environment. Git, time, browser, filesystem, memory and reasoning configurations are included. [Integration guide](docs/integrations.md)

```sh
turnloom run "Find the failing test" --input-rate 1 --output-rate 4 --budget 0.10
```

The sample rates are illustrative USD per million tokens. Supply your provider's rates. Each step subtracts reported usage from the task allowance, estimates the next prompt, and scales the maximum reply to the remaining allowance. Token estimates, retries and provider billing differences can cause the invoice to exceed the estimate. In chat, each submitted task starts its own allowance.

The default preserves the latest three tool results. It folds older long results into a preview and a reference. `recall` retrieves a slice locally. Use `--no-fold` for an A/B comparison. The receipt counts removed characters and provider-reported tokens separately.

## Workspaces and privacy

Commands run with the operating system permissions of the process. Review the command before approving it. Use `--yes` only inside a trusted or isolated task environment. The evaluation runner approves commands automatically. Tool output and task content are sent to your configured model. Full local traces can contain repository content and secrets printed by commands. Each session prints its trace path under `.turnloom/`, which Git ignores.

## Personas and licensing

Builder and Debugger are native roles. Reviewer and Architect load Markdown from Agency Agents, pinned at commit `8ae6164602c95ca78828b148b9052460ee426885`. Only the selected profile enters the prompt. Extend a role with `--persona-file PATH`. [Upstream repository](https://github.com/msitarzewski/agency-agents/tree/8ae6164602c95ca78828b148b9052460ee426885) · [Upstream license](turnloom/personas/LICENSE) · [Turnloom license](LICENSE)
