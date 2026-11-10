# Integration guide

From the repository root, activate a Python virtual environment and install the features you want. Use `python -m pip install ".[full]"` for the Python toolkit or select individual extras such as `.[mcp]`, `.[team]` or `.[skills]`. Activate the environment so its server executables are on PATH. Node servers have a separate lockfile.

| Upstream | Turnloom interface | What runs locally |
| --- | --- | --- |
| [LangGraph](https://github.com/langchain-ai/langgraph) | `turnloom team` | Three sequential stages share the workspace, deadline and estimated USD allowance. Each sees the original task and short prior reports. A failed stage stops the graph. |
| [MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk) | `--mcp FILE`, `turnloom tools`, `turnloom serve` | Client and server negotiate the protocol over stdio. The client opens a selected server on first use and retains its session through chat. |
| [MCP reference servers](https://github.com/modelcontextprotocol/servers) | Git, Time, Filesystem, Memory, Sequential Thinking | Separate processes provide workspace inspection, timezone conversion, file operations, a knowledge graph and a reasoning tool. |
| [Playwright MCP](https://github.com/microsoft/playwright-mcp) | Browser server | A headless, isolated browser profile supports navigation and page inspection. Returned text and structured data enter context. Binary content is represented by type. |
| [Agent Skills](https://github.com/agentskills/agentskills) and [Anthropic Skills](https://github.com/anthropics/skills) | `skills`, `--skills-dir`, `--skill` | Read metadata from a local collection, load selected Markdown into the prompt, and resolve resources relative to the skill directory. |
| [Agency Agents](https://github.com/msitarzewski/agency-agents) | `--persona architect`, `--persona reviewer` | Two pinned specialist prompts ship with Turnloom. See the included upstream license. |

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

Pass an explicit JSON config with a named `mcpServers` map. Each entry requires a command and can supply an array of arguments and environment variable names in `pass_env`. `${configDir}` expands to the directory holding the config. The process starts in the task workspace with the SDK's baseline environment plus the named values. Treat the config as executable code. Server startup occurs on discovery. Interactive approval applies before a model invokes a server tool.

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
