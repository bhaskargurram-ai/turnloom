![Turnloom — a coding agent with recoverable context](docs/banner.svg)

Turnloom is a Python terminal application for coding with a tool-capable Chat Completions model. Choose a specialist, approve commands as you work, then inspect the session receipt. The model can recover excerpts from older tool results after those results leave its prompt.

[Quick start](#quick-start) · [Agent loop](docs/design.md) · [Experiments](docs/experiments.md) · [Contributing](CONTRIBUTING.md)

## Quick start

From the repository root, with Python 3.11 or newer:

```sh
python -m pip install .
turnloom demo
turnloom personas
```

The offline demo folds a sample test log and recalls a slice by reference. For a live run, configure the base URL, model ID, and key for your provider. In PowerShell:

```powershell
$env:HARNESS_ENDPOINT = "https://your-provider.example/v1"
$env:HARNESS_MODEL = "your-tool-capable-model"
$env:HARNESS_API_KEY = Read-Host -MaskInput "Model key"
turnloom chat --workspace . --persona debugger
```

Use PowerShell 7.1+ for masked input. On Linux and macOS export the same environment variables. A local endpoint that accepts unauthenticated requests can use an empty key. Enter `/quit` to close a chat.

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
