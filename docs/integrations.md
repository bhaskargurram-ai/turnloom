# Integration guide

From the repository root, activate a Python virtual environment and install the features you want. Use `python -m pip install ".[full]"` for the Python toolkit or select individual extras such as `.[mcp]`, `.[team]` or `.[skills]`. Activate the environment so its server executables are on PATH. Node servers have a separate lockfile.

| Upstream | Turnloom interface | What runs locally |
| --- | --- | --- |
| [LangGraph](https://github.com/langchain-ai/langgraph) | `turnloom team` | Three sequential stages share the workspace, deadline and estimated USD allowance. Each sees the original task and short prior reports. A failed stage stops the graph. |
| [MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk) | `--mcp FILE`, `turnloom tools`, `turnloom serve` | Client and server negotiate the protocol over stdio. The client opens a selected server on first use and retains its session through chat. |
| [MCP reference servers](https://github.com/modelcontextprotocol/servers) | Git, Time, Filesystem, Memory, Sequential Thinking | Separate processes provide workspace inspection, timezone conversion, file operations, a knowledge graph and a reasoning tool. |
| [Playwright MCP](https://github.com/microsoft/playwright-mcp) | Browser server | A headless, isolated browser profile supports navigation and page inspection. Returned text and structured data enter context. Binary content is represented by type. |
| [Agent Skills](https://github.com/agentskills/agentskills) and [Anthropic Skills](https://github.com/anthropics/skills) | `skills`, `--skills-dir`, `--skill` | Read metadata from a local collection, load selected Markdown into the prompt, and resolve resources relative to the skill directory. |
| [Agency Agents](https://github.com/msitarzewski/agency-agents) | `--persona ID`, Studio's Agents view | 283 specialist prompts ship with Turnloom, pinned at one commit. `scripts/sync_personas.py` refreshes them from a checkout. See the included upstream license. |
| [LiteLLM](https://github.com/BerriAI/litellm) | `turnloom connect litellm:PROVIDER`, the `llm` extra | Translates Turnloom's chat-completions requests for native APIs such as Anthropic, Bedrock and Vertex AI. Its public price map also fills in prices and context windows that an endpoint does not list. |
| [Provio](https://github.com/writ-agent/provio) | `turnloom/guard.py`, Studio's Policy view, the `guard` extra | A policy gate in front of every shell command, workspace read and MCP tool call, through the `provio-sdk` gateway. Decisions land in Provio's hash-chained ledger. |
| [VerifyDoc](https://github.com/bhaskargurram-ai/verifydoc) | Studio's Documents view, **VerifyDoc via Unwind** | Field extraction with confidence, grounding and an accept or review decision, served over MCP. |
| [Unwind](https://github.com/bhaskargurram-ai/unwind) | Studio's Undo ledger, `"unwind": true` on a server | A reversibility proxy in front of an MCP server: classifies calls, logs them, and undoes what the server can reverse. |
| [Harbor](https://github.com/harbor-framework/harbor) | `integrations/harbor/turnloom_harbor.py`, `turnloom atif` | Runs the headless agent as an installed agent and reads its ATIF trajectory. See [the leaderboard notes](leaderboard.md). |
| [pywebview](https://github.com/r0x0r/pywebview) and [PyInstaller](https://github.com/pyinstaller/pyinstaller) | `turnloom-studio`, `packaging/` | The native window and the standalone app build. See [Desktop app](desktop.md). |

Studio's interface is built from [CodeMirror](https://github.com/codemirror/dev) with its merge view, [Cline's UI components](https://github.com/cline/cline), [Recharts](https://github.com/recharts/recharts), [cmdk](https://github.com/pacocoursey/cmdk), [react-resizable-panels](https://github.com/bvaughn/react-resizable-panels), [Sonner](https://github.com/emilkowalski/sonner), [react-markdown](https://github.com/remarkjs/react-markdown) and [Lucide](https://github.com/lucide-icons/lucide).

## Put Turnloom under another harness

```sh
turnloom proxy --port 8787 --keep 3
```

The proxy accepts OpenAI chat-completions requests on `http://127.0.0.1:8787/v1` and forwards them to your connected model. Use any placeholder API key, or pass `--token` to require one. The model name in the request is passed through. Send `turnloom` to use the connected model.

| Harness setting | Value |
| --- | --- |
| OpenAI SDKs, LangChain, LangGraph, CrewAI, AutoGen, smolagents | `base_url="http://127.0.0.1:8787/v1"` or `OPENAI_BASE_URL` |
| Aider | `aider --openai-api-base http://127.0.0.1:8787/v1 --model openai/turnloom` |
| OpenHands, Cline, Continue, Goose, mini-swe-agent | Choose the OpenAI-compatible provider and enter the same base URL |

Of these, the OpenAI Python SDK is the one exercised in the test suite and against a live model. The others use the same wire format and have not been run from this repository.

What the proxy does to a request:

1. Tool results older than the newest `--keep` and longer than 800 characters become a preview: the first 180 characters, the last 120, the length and a reference. The caller's own history is never modified.
2. The model gets a `turnloom_recall` tool. When it calls it, the proxy answers from its archive and asks the model again. The harness never sees these calls, and their tokens are included in the usage it receives.
3. If the model is still reading after eight rounds, the proxy sends the caller's unfolded history instead, so the answer is the one the harness would have had without the layer.
4. Each request is written to `.turnloom/usage.jsonl` in `--workspace`, tagged with the client's user agent. `GET /v1/turnloom/stats` returns running totals.

Limits: chat completions only, with no Responses or Anthropic Messages route. Streaming requests are answered with the finished reply as a short event stream, so text arrives at once and not token by token. Folding trades prompt tokens for occasional extra model calls, and a weaker model may not think to read a folded result back. Measure on your tasks with `--no-fold` as the control.

For harnesses that take MCP servers, `turnloom serve` also exposes `archive_output` and `recall` tools beside its receipts, so an agent can park a long result itself.

## Try a tool before using inference

```sh
turnloom tools examples/mcp.json time get_current_time
turnloom tools examples/mcp.json git git_status
npm ci --prefix examples/toolkit --ignore-scripts
turnloom tools examples/toolkit/tools.json browser browser_navigate
```

For the Node toolkit use Node 22+ and a Playwright-compatible browser for browser actions. Set `MEMORY_FILE_PATH` to an absolute file in an existing private directory to keep your graph across package reinstalls.

## Run a team

```sh
turnloom team "Fix the failing parser test" --workspace . --mcp examples/mcp.json --budget 0.10 --input-rate 1 --output-rate 4
```

Set the model and endpoint as in the quick start. Replace the illustrative token prices with your provider's prices. The plan and review instructions ask those stages to inspect the workspace. The operator's tool approvals apply in all three stages. Each stage logs its own model usage. The terminal shows the total. Compare sequential and single-agent runs with `turnloom-eval tasks.json --team --results results/team` and a matched control. Use only tools and network access permitted by the benchmark.

## Connect another MCP server

In Studio, put the config at `.turnloom/mcp.json` in the workspace and switch servers on in the Tools view. Add `"unwind": true` to an entry to route that server through Unwind. From the terminal, pass an explicit JSON config with a named `mcpServers` map. Each entry requires a command and can supply an array of arguments and environment variable names in `pass_env`. `${configDir}` expands to the directory holding the config. The process starts in the task workspace with the SDK's baseline environment plus the named values. Treat the config as executable code. Server startup occurs on discovery. Interactive approval applies before a model invokes a server tool.

The model receives two fixed tool definitions and the configured server names. Search returns up to five matching schemas. Large results enter the existing folding and recall path. Server-side APIs may have their own charges outside the model token meter.

## Load existing skills

```sh
git clone https://github.com/anthropics/skills external-skills
git -C external-skills checkout 2c5958ecac7cee2b583967a2c4981d0f4a75c579
turnloom skills external-skills/skills
turnloom run "Test the local web app" --skills-dir external-skills/skills --skill webapp-testing
```

Select trusted instructions and install the resources a skill requires. Auxiliary scripts run through the ordinary approved shell. Third-party skills retain their individual license terms.

## Read receipts from another agent

Configure its MCP client with command `turnloom` and arguments `["serve", "--workspace", "/absolute/path/to/project"]`. The `sessions` tool lists recent IDs. The `receipt` tool returns token counts, completion statuses and estimated cost for an ID. This endpoint exposes numerical run metadata and performs no model inference.

## Reproduce the checks

With the full extra installed, run `python -m unittest discover -s tests -v`. Coverage includes a real stdio client/server round trip, approval denial, and a LangGraph workflow driven by deterministic model replies. The development smoke run also called all six companion servers on Windows with Python 3.12, MCP SDK 1.30.0, LangGraph 1.2.20, the pinned Python servers and the checked-in npm lockfile.
