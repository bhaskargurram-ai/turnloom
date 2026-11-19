# Integration guide

The core install has what most sessions need. Extras add optional surfaces:

| Extra | Adds |
| --- | --- |
| `turnloom[mcp]` | MCP client (`--mcp`, `turnloom tools`) and MCP server (`turnloom serve`) |
| `turnloom[acp]` | Editor integration over the Agent Client Protocol (`turnloom acp`) |
| `turnloom[llm]` | LiteLLM, for native provider APIs such as Anthropic, Bedrock and Vertex AI |
| `turnloom[guard]` | Provio, the policy gate in front of tool calls |
| `turnloom[search]` | A bundled ripgrep binary for faster `search_files` |
| `turnloom[tokens]` | tiktoken, for exact token counts in Studio's Cost view |
| `turnloom[skills]` | PyYAML, for skills whose metadata uses multi-line YAML |
| `turnloom[team]` | LangGraph, to run a team as a state graph |
| `turnloom[desktop]` | pywebview, for Studio's native window |
| `turnloom[documents]` | VerifyDoc and the Unwind ledger |
| `turnloom[studio]` | `search`, `desktop`, `documents`, `guard` and `tokens` |
| `turnloom[full]` | Every terminal-side extra plus the Git and Time reference servers used below |

Install one with, for example, `pipx install "turnloom[full]"`.

| Upstream project | Where it appears in Turnloom |
| --- | --- |
| [MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk) and [reference servers](https://github.com/modelcontextprotocol/servers) | `--mcp`, `turnloom tools`, `turnloom serve`, Studio's Tools view |
| [Agent Client Protocol](https://agentclientprotocol.com) | `turnloom acp` |
| [Agent Skills](https://github.com/agentskills/agentskills) | Project skills, `--skills-dir`, `--skill` |
| [Agency Agents](https://github.com/msitarzewski/agency-agents) | 283 vendored personas, pinned at one commit. `scripts/sync_personas.py` refreshes them. |
| [LiteLLM](https://github.com/BerriAI/litellm) | `turnloom connect litellm:PROVIDER` |
| [Provio](https://github.com/writ-agent/provio) | `turnloom/guard.py`, Studio's Policy view |
| [LangGraph](https://github.com/langchain-ai/langgraph) | `turnloom team`, Studio's team mode |
| [VerifyDoc](https://github.com/bhaskargurram-ai/verifydoc) and [Unwind](https://github.com/bhaskargurram-ai/unwind) (`pip install rollback-mcp`) | Studio's Documents view and Undo ledger |
| [Harbor](https://github.com/harbor-framework/harbor) | `integrations/harbor/`, `turnloom atif`. See [the leaderboard notes](leaderboard.md). |
| [pywebview](https://github.com/r0x0r/pywebview) and [PyInstaller](https://github.com/pyinstaller/pyinstaller) | The native window and the standalone app build. See [Desktop app](desktop.md). |

## Project instructions

Put an `AGENTS.md` at the root of your repository. Turnloom adds its content (up to 24,000 characters) to the agent's instructions on every session in that workspace. `CLAUDE.md` is read when there is no `AGENTS.md`. `turnloom doctor` shows which file was found.

Use it for what a new contributor would need to be told: how to run the tests, which directories are generated, naming conventions, what not to touch.

## Personas

`turnloom personas` lists the engineering, testing and security roles; `--all` lists every division and `--search` filters. Use one with `--persona ID`. `reviewer` and `architect` are short names for the code reviewer and the software architect. Only the selected persona's instructions enter the prompt.

A Markdown file in `<workspace>/.turnloom/personas/NAME.md` becomes the persona `custom-NAME`. Optional front matter sets its `name` and `description`.

## Agent Skills

A skill is a folder with a `SKILL.md` whose front matter has a `name` matching the folder and a `description`.

Skills checked into the workspace under `.agents/skills/` or `.claude/skills/` are discovered automatically. Turnloom lists each name and description in the prompt and the model reads the skill's `SKILL.md` with `read_file` when a task matches. Nothing is loaded until it is needed.

To use a skill collection kept elsewhere, load named skills into the prompt directly:

```sh
git clone https://github.com/anthropics/skills external-skills
git -C external-skills checkout 2c5958ecac7cee2b583967a2c4981d0f4a75c579
turnloom skills external-skills/skills
turnloom run "Test the local web app" --skills-dir external-skills/skills --skill webapp-testing
```

Skills are instructions the model will follow, so load only ones you trust. Scripts a skill ships run through the ordinary approved shell. Third-party skills keep their own licenses.

## MCP servers

Pass a JSON file with a `mcpServers` map. An entry is either a local process or a streamable HTTP endpoint:

```json
{
  "mcpServers": {
    "git": {"command": "mcp-server-git", "args": ["--repository", "."]},
    "time": {"command": "mcp-server-time", "args": ["--local-timezone", "UTC"], "pass_env": ["TZ"]},
    "docs": {"url": "https://mcp.example.com/mcp", "bearer_env": "DOCS_MCP_TOKEN"}
  }
}
```

- A process entry takes `command`, optional `args`, optional `pass_env` (names of environment variables the server may inherit) and optional `env` (values to set). `${configDir}` in an argument expands to the folder holding the file. The process starts in the workspace.
- An HTTP entry takes `url` and optional `bearer_env`, the name of an environment variable holding a bearer token. The token itself never goes in the file.

Treat the file as executable code: it names programs Turnloom will start. Turnloom does not load `.mcp.json` from a repository by itself for that reason.

```sh
turnloom tools examples/mcp.json git git_status          # inspect a server without a model
turnloom chat --mcp examples/mcp.json
```

In Studio, put the same file at `.turnloom/mcp.json` in the workspace and switch servers on in the Tools view. In that file, and only there, add `"unwind": true` to a process entry to route that server through Unwind; a file passed with `--mcp` is refused if it has that key.

The model gets two tools regardless of how many servers you configure: `mcp_search` lists a server's tool names and returns the schemas of the best five matches, and `mcp_call` invokes one. A server starts on first use and stays connected for the session. Every call asks for approval with its arguments shown. Results larger than the output limit are archived and reach the prompt as a preview with a reference. A server that fails is reconnected on the next call.

`examples/toolkit/` has a lockfile for the Playwright, Filesystem, Memory and Sequential Thinking servers. With Node 22 or newer: `npm ci --prefix examples/toolkit --ignore-scripts`, then `turnloom tools examples/toolkit/tools.json browser browser_navigate`.

## Editors (Agent Client Protocol)

`turnloom acp` runs Turnloom as an [Agent Client Protocol](https://agentclientprotocol.com) agent on stdin and stdout, so an editor can drive it. Install the `acp` extra and connect a model first.

**Zed.** Open Agent Settings, choose External Agents, Add Agent, Add Custom Agent, or add this to `settings.json`:

```json
{
  "agent_servers": {
    "Turnloom": {
      "type": "custom",
      "command": "turnloom",
      "args": ["acp"],
      "env": {}
    }
  }
}
```

**JetBrains IDEs.** In AI Chat choose Add Custom Agent, which opens `~/.jetbrains/acp.json`:

```json
{
  "agent_servers": {
    "Turnloom": { "command": "turnloom", "args": ["acp"], "env": {} }
  }
}
```

**Any other ACP client.** Launch the command `turnloom` with the argument `acp` and speak ACP over its stdio. If `turnloom` is not on the editor's `PATH`, give the full path to the executable, or use your Python with `["-m", "turnloom", "acp"]`. Add `"--persona", "reviewer"` to change the specialist.

Each editor thread is a Turnloom session in the project the editor opens, saved under `.turnloom/` like any other, so `turnloom sessions`, `turnloom undo` and `turnloom replay` work on it. Replies stream as they are written, and file reads, searches, edits and commands appear as tool calls. Edits are shown as diffs. A workspace's Provio policy applies here too.

Three modes are offered: **Ask** (the default) asks before every edit and command, **Auto-edit** applies edits and still asks before commands, **Read-only** explores and answers without changing anything. In a permission prompt, "Always allow edits in this session" switches the session to Auto-edit; "Always allow this exact command" covers only that command.

The model is the one you connected with `turnloom connect`, or `TURNLOOM_ENDPOINT`, `TURNLOOM_MODEL` and `TURNLOOM_API_KEY` set in the agent's `env`. Limits: prompts take text and attached text files, not images or audio. MCP servers passed by the editor are used when they are stdio servers; HTTP servers are named in the first reply and skipped. Turnloom reads files from disk, so save a buffer before asking about it.

This integration is tested against the protocol SDK's own client over real stdio. It has not yet been exercised inside each editor; please report what you find.

## Turnloom as an MCP server

Other agents can read Turnloom's sessions and hand it work. Add it to an MCP client with the command `turnloom` and the arguments `["serve", "--workspace", "/absolute/path/to/project"]`.

| Tool | Returns |
| --- | --- |
| `sessions` | The most recent session IDs in the workspace |
| `receipt` | Turns, token totals, estimated cost and statuses for a session |
| `transcript` | A page of a session's tasks, tool calls, result previews and answers |
| `read_result` | A slice of any tool result in a saved session, including ones folded out of the prompt |
| `archive_output`, `recall` | Park a long tool result outside the calling agent's conversation, and read it back by slice or search |
| `run_task` | Only with `--allow-run`: runs a task with your connected model and returns the answer, usage and files changed |

`run_task` is read-only unless you start the server with `--allow-edits`, `--allow-shell` or both. There is nobody to ask for approval over MCP, so these flags are the whole policy: anything not enabled is declined. Delegated tasks use your model connection and your tokens.

## Put Turnloom under another harness

```sh
turnloom proxy --port 8787 --keep 3
```

The proxy accepts OpenAI chat-completions requests on `http://127.0.0.1:8787/v1` and forwards them to your connected model. Use any placeholder API key, or pass `--token` to require one. The model name in the request is passed through; send `turnloom` to use the connected model.

| Harness setting | Value |
| --- | --- |
| OpenAI SDKs and frameworks built on them | `base_url="http://127.0.0.1:8787/v1"` or `OPENAI_BASE_URL` |
| Aider | `aider --openai-api-base http://127.0.0.1:8787/v1 --model openai/turnloom` |
| Agents with an "OpenAI-compatible" provider option | Enter the same base URL |

Of these, the OpenAI Python SDK is the one exercised in the test suite. The others use the same wire format and have not been run from this repository.

What the proxy does to a request:

1. Tool results older than the newest `--keep` and longer than 800 characters become a preview: the first 180 characters, the last 120, the length and a reference. The caller's own history is never modified.
2. The model gets a `turnloom_recall` tool. When it calls it, the proxy answers from its archive and asks the model again. The harness never sees these calls, and their tokens are included in the usage it receives.
3. If the model is still reading after eight rounds, the proxy sends the caller's unfolded history instead, so the answer is the one the harness would have had without the layer.
4. Each request is written to `.turnloom/usage.jsonl` in `--workspace`, tagged with the client's user agent. `GET /v1/turnloom/stats` returns running totals.

Limits: chat completions only, with no Responses or Anthropic Messages route. Streaming requests are answered with the finished reply as a short event stream, so text arrives at once and not token by token. The proxy listens on loopback and refuses requests that carry a browser `Origin` or a foreign `Host`. Folding trades prompt tokens for occasional extra model calls, and a weaker model may not think to read a folded result back. Measure on your tasks with `--no-fold` as the control.

## Continuous integration

The repository root is a composite GitHub Action. It installs Turnloom, runs one task and exposes the result:

```yaml
jobs:
  review:
    runs-on: ubuntu-latest
    permissions:
      contents: read
    steps:
      - uses: actions/checkout@v7
      - id: turnloom
        uses: bhaskargurram-ai/turnloom@v0.7.1
        with:
          task: Review the code under src/ for bugs and report the three most important findings.
          persona: reviewer
          mode: read-only
          endpoint: https://openrouter.ai/api/v1
          model: ${{ vars.TURNLOOM_MODEL }}
          api-key: ${{ secrets.OPENROUTER_API_KEY }}
      - run: echo "${{ steps.turnloom.outputs.status }}"
```

`mode` is `read-only` (default), `auto-edit` or `unattended`. The answer is written to the job summary and to `outputs.answer`; the full result is at the path in `outputs.result-file`. In `unattended` mode the agent runs commands on the runner without asking, so give the job only the permissions and secrets the task needs and do not run it on untrusted pull requests.

For workflows that post a review on a pull request, triage new issues and attempt an issue on a label, all without `unattended` mode, see [Agents in CI](automation.md).

Without the action, the same thing is `turnloom run "..." --read-only --json` with `TURNLOOM_ENDPOINT`, `TURNLOOM_MODEL` and `TURNLOOM_API_KEY` set.

## Containers

Each release publishes `ghcr.io/bhaskargurram-ai/turnloom`, an image with Turnloom, Git and ripgrep that runs as an unprivileged user. Mount a project at `/workspace`:

```sh
docker run --rm -it -v "$PWD:/workspace" \
  -e TURNLOOM_ENDPOINT -e TURNLOOM_MODEL -e TURNLOOM_API_KEY \
  ghcr.io/bhaskargurram-ai/turnloom:v0.7.1 run "Fix the failing test" --yes
```

The repository's `Dockerfile` builds the same image locally with `docker build -t turnloom .`.

Inside a container `--yes` is a reasonable choice: commands can only reach the mounted project and whatever network you allow. There is no credential store in the image, so pass the connection through the environment.

## Documents and the undo ledger

With `turnloom[documents]` installed, Studio's Documents view runs [VerifyDoc](https://github.com/bhaskargurram-ai/verifydoc) on a workspace file or pasted text with no model call: fields extracted against a JSON Schema, each with a confidence, the place it was read from, and an accept or review decision. Turning the `verifydoc` server on in the Tools view gives the agent the same tool.

[Unwind](https://github.com/bhaskargurram-ai/unwind) sits in front of an MCP server and classifies each call from R0, read only, to R4, irreversible. When the agent asks to call a tool behind Unwind, the approval shows that class and whether the call can be undone. The Undo ledger lists recorded actions and reverses them newest first or back to a checkpoint. The ledger lives at `.turnloom/unwind.db`.

Unwind covers MCP calls. File edits made by the agent's own tools are reverted with Turnloom's undo, and shell commands are not undoable by either.

## Team runs

```sh
turnloom team "Fix the failing parser test" --budget 0.10 --input-rate 1 --output-rate 4
```

Three stages run in order: plan, build, review. Planning and review are read-only by construction: those stages are given no edit or shell tools. Each stage receives the original task and short reports from the stages before it, and all share the deadline and the allowance. A stage that does not complete stops the run. The receipt totals all stages.

In Studio's Agents view a team is a list of stages you arrange, each with its own persona, instruction and read-only switch. With the `team` extra the stages run as a [LangGraph](https://github.com/langchain-ai/langgraph) state graph; without it they run in a plain loop with the same behavior.
