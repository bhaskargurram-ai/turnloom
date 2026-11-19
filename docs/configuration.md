# Configuration

The terminal commands have no settings file. What Turnloom does is decided by four things:

1. **Flags** on the command line. They are listed in the [command reference](cli.md).
2. **Files in the workspace**, the project directory you run Turnloom in.
3. **Files in your user profile**: the saved model connection and Studio's settings.
4. **Environment variables.**

This page covers the last three.

## What wins when settings overlap

| Setting | Order, first match wins |
| --- | --- |
| Which model to call | `TURNLOOM_ENDPOINT` with `TURNLOOM_MODEL`, then the connection saved by `turnloom connect` |
| The key for a saved connection | The provider's environment variable, then the operating system's credential store |
| Prices and context window | `--input-rate`, `--output-rate` and `--context`, then the saved connection, then no prices and 128,000 tokens |
| Project instructions | `AGENTS.md`, then `CLAUDE.md` |

## Files in the workspace

| Path | Written by | What it is |
| --- | --- | --- |
| `AGENTS.md` or `CLAUDE.md` | You | Instructions added to every session in this workspace |
| `.agents/skills/NAME/SKILL.md`, `.claude/skills/NAME/SKILL.md` | You | Skills the model can read when a task calls for one |
| `.turnloom/SESSION_ID.jsonl` | Turnloom | One transcript per session |
| `.turnloom/usage.jsonl` | Studio and `turnloom proxy` | One line per model call: model, persona, tokens, estimated cost |
| `.turnloom/unwind.db` | Studio, with the `documents` extra | Unwind's ledger of MCP calls that can be undone |
| `.turnloom/.gitignore` | Turnloom | Keeps the three files above out of Git |
| `.turnloom/mcp.json` | You | MCP servers offered in Studio's Tools view |
| `.turnloom/personas/NAME.md` | You | Personas for this workspace |
| `provio.yaml` | You | A Provio policy checked before every tool call |
| `.provio/ledger.jsonl` | Provio | The record of every policy decision |

### Project instructions

Put an `AGENTS.md` at the root of the workspace. Its text is added to the agent's instructions in `run`, `chat`, `team`, Studio, `turnloom acp` and tasks delegated through `turnloom serve`.

- `CLAUDE.md` is read when there is no `AGENTS.md`, or when `AGENTS.md` is empty.
- Up to 24,000 characters are used. A longer file is cut in the middle, keeping its start and its end.
- Only a file that really sits in the workspace root is read. A link that points somewhere else is ignored.
- `turnloom-agent` and `turnloom-eval` do not read either file.

`turnloom doctor` shows which file was found on its `Project instructions` line.

### Skills

A skill is a folder holding a `SKILL.md` that starts with front matter:

```markdown
---
name: release-notes
description: Write release notes from the changelog in this project's house style.
---

Read CHANGELOG.md, then ...
```

`name` has to equal the folder name, and `description` has to be present. Skills under `.agents/skills/` and `.claude/skills/` are found automatically. For each one, the prompt gets a line with its name, the first 300 characters of its description and its path, for up to 40 skills. The model reads the `SKILL.md` with its file tools when a task matches, so a skill costs nothing until it is used.

If one `SKILL.md` in a folder is invalid, none of that folder's skills are listed. Check a folder with `turnloom skills .agents/skills`, which names the file at fault.

Without the `skills` extra, front matter is read as plain `key: value` lines. Install `turnloom[skills]` for multi-line YAML values.

Skills kept outside the workspace are loaded by name with `--skills-dir` and `--skill`. See the [command reference](cli.md#workspace-and-instructions).

### The `.turnloom` folder

Turnloom creates `.turnloom/` in the workspace the first time a session is saved. The agent's own file tools cannot read or write inside it.

**Transcripts.** Each session appends to `.turnloom/SESSION_ID.jsonl`, where the ID is 32 hexadecimal characters. Every line is one JSON event with a `time` and a `type`:

| `type` | Holds |
| --- | --- |
| `start` | The task, the model, the Turnloom version and the full system prompt |
| `assistant` | One model reply with its tool calls, the provider's usage numbers and the running cost estimate |
| `tool` | The result of one tool call |
| `edit` | A file path with its content before and after an edit |
| `context` | A note that old exchanges were archived to fit the context window |
| `end` | The receipt for the turn: status, answer, tokens, cost, files changed |

`turnloom sessions`, `replay`, `undo`, `atif`, `--resume` and `turnloom serve` all read these files. Deleting a file deletes that session. A transcript contains file contents and command output, so remove secrets before sharing one.

**The usage ledger.** Studio and `turnloom proxy` append one line per model call to `.turnloom/usage.jsonl`. Studio's Cost view reads it. `run`, `chat` and `team` do not write to it; their numbers are in the receipt and the transcript.

**The `.gitignore`.** It is written once and never overwritten:

```text
# Session traces and local state hold file contents and command output. Keep them out of version control.
*.jsonl
*.db
*.db-*
.gitignore
```

`mcp.json` and `personas/` are not matched, so a team can commit them.

### MCP servers

MCP servers are configured in a JSON file with an `mcpServers` map. There are two places for it:

| Where | Used by | How servers are trusted |
| --- | --- | --- |
| Any file, passed with `--mcp FILE` or as the first argument of `turnloom tools` | `run`, `chat`, `team`, `turnloom-eval` and `turnloom tools` | Passing the file is the trust. Turnloom never loads an MCP file from a repository by itself. |
| `.turnloom/mcp.json` | Studio | Each server stays off until you switch it on in the Tools view. If its definition changes later, it is switched off again. |

```json
{
  "mcpServers": {
    "git": {"command": "mcp-server-git", "args": ["--repository", "."]},
    "time": {"command": "mcp-server-time", "args": ["--local-timezone", "UTC"], "pass_env": ["TZ"]},
    "docs": {"url": "https://mcp.example.com/mcp", "bearer_env": "DOCS_MCP_TOKEN"}
  }
}
```

A server name may use letters, digits, `_` and `-`, up to 64 characters. An entry is one of two kinds.

A local process:

| Key | Required | Meaning |
| --- | --- | --- |
| `command` | yes | The program to start. |
| `args` | no | A list of arguments. `${configDir}` in an argument becomes the folder holding the configuration file. |
| `pass_env` | no | Names of environment variables the server may inherit from your shell. |
| `env` | no | Variables to set for the server, as a map of names to strings. |
| `unwind` | no | Studio only. `true` routes the server through Unwind. A file passed with `--mcp` must not contain this key. |

A streamable HTTP endpoint:

| Key | Required | Meaning |
| --- | --- | --- |
| `url` | yes | An `http://` or `https://` address. |
| `bearer_env` | no | The name of an environment variable that holds a bearer token. The token itself never goes in the file. |

A process server starts in the workspace directory. It does not see your whole environment: it gets a small set of system variables such as `PATH`, plus the names in `pass_env` and the values in `env`. A server that needs a token from your shell has to list that variable in `pass_env`.

Treat the file as code. It names programs Turnloom will start.

### Personas

A Markdown file at `.turnloom/personas/NAME.md` becomes the persona `custom-NAME`. `NAME` may use letters, digits, `_` and `-`. The body of the file is added to the agent's instructions when you select it.

For example, save this as `.turnloom/personas/migration-reviewer.md`:

```markdown
---
name: Migration reviewer
description: Reviews database migrations for locking and rollback problems.
---

Role: migration reviewer. For every migration, state which locks it takes ...
```

The front matter is optional. `name` and `description` are shown by `turnloom personas` and in Studio. `emoji`, `color` and `vibe` are also read, for Studio's persona cards.

```sh
turnloom personas --search migration
turnloom run "Review migrations/0042_add_index.sql" --persona custom-migration-reviewer --read-only
```

`turnloom personas` looks in the current directory, so run it from the workspace.

### Provio policy

A `provio.yaml` at the workspace root turns on the policy gate in `run`, `chat`, `team`, Studio, `turnloom acp` and `turnloom serve`, provided the `guard` extra is installed. Each shell command, file read, file edit and MCP call is checked against the policy before the approval prompt. Decisions are recorded in `.provio/ledger.jsonl`.

If the file is present and Provio is not installed, Turnloom refuses to run and says so. Install `turnloom[guard]`, or pass `--no-gate` to run once without the policy. `turnloom-agent` and `turnloom-eval` do not consult the policy.

The starter policy that Studio's Policy view creates is:

```yaml
# Provio policy for this workspace. https://github.com/writ-agent/provio
# Unmatched calls are allowed. The floor pack refuses disasters and asks before
# irreversible commands. The secrets guard keeps keys and credentials away from the agent.
version: 1
default: allow
packs: [floor, secrets-guard]
```

The policy language belongs to [Provio](https://github.com/writ-agent/provio); see its documentation for rules. [Put a Provio policy under the agent](recipes.md#put-a-provio-policy-under-the-agent) walks through a first run.

## Files in your user profile

Turnloom keeps a small configuration folder outside any workspace.

| System | Folder |
| --- | --- |
| Windows | `%APPDATA%\turnloom` |
| macOS and Linux | `$XDG_CONFIG_HOME/turnloom`, or `~/.config/turnloom` when that variable is not set |
| Any, when `TURNLOOM_CONFIG_DIR` is set | The folder that variable names |

| File | Holds |
| --- | --- |
| `connection.json` | The choice saved by `turnloom connect`: provider, model, prices, tier and context window. It does not hold the key. |
| `models-dev.json` | A copy of the public Models.dev catalog, fetched again when it is more than a day old. |
| `workspaces/HASH.json` | Studio's settings for one workspace. The name is derived from the workspace path. |

Set `TURNLOOM_CONFIG_DIR` to a different folder to keep a second, separate profile:

```sh
TURNLOOM_CONFIG_DIR=~/.config/turnloom-work turnloom connect
```

```powershell
$env:TURNLOOM_CONFIG_DIR = "$env:APPDATA\turnloom-work"
turnloom connect
```

### Where keys are stored

Keys you type into `turnloom connect` or Studio go to the operating system's credential store, under the service name `turnloom:PROVIDER` and the account `api-key`.

| System | Store |
| --- | --- |
| Windows | Credential Manager |
| macOS | Keychain |
| Linux | Secret Service, KWallet or libsecret |

On a machine with no credential store, such as a container or a bare server, set the provider's environment variable. `turnloom connect` names the variable. `turnloom disconnect --forget-key` deletes a stored key.

### Studio's settings

Studio saves its switches for each workspace in `workspaces/HASH.json` in the configuration folder. They are never read from the workspace, so a repository you clone cannot arrive with actions already approved or servers already on.

| Setting | Default | Range |
| --- | --- | --- |
| Budget per turn, in USD | 0, no budget | 0 to 1000 |
| Fold older tool output | on | on or off |
| Tool results kept unfolded | 3 | 1 to 20 |
| Reply limit, in tokens | 8192 | 256 to 400,000 |
| Reasoning effort | not sent | `minimal`, `low`, `medium`, `high` |
| Mode | solo | solo or team |
| Auto-approve edits | off | on or off |
| Auto-approve everything | off | on or off |
| Prompt caching mark | on | on or off |
| Policy gate | confirm each call | confirm each call, policy decides, off |
| Enabled MCP servers | none | Server names, each tied to the definition it had when you switched it on |
| Team stages | plan, build, review | One to eight stages, each with a name, a persona, an instruction and a read-only switch |

Studio also applies limits of its own to each turn: 40 model calls, 80 tool calls and four hours.

When Studio falls back to an Edge or Chrome app window, that browser keeps a profile in `%LOCALAPPDATA%\turnloom\app-window` on Windows, or `~/.cache/turnloom/app-window` elsewhere.

## Environment variables

### Model connection

| Variable | Meaning |
| --- | --- |
| `TURNLOOM_ENDPOINT` | An OpenAI-compatible base URL, such as `http://127.0.0.1:11434/v1`. `/chat/completions` is added for you. |
| `TURNLOOM_MODEL` | The model ID at that endpoint. |
| `TURNLOOM_API_KEY` | The credential for that endpoint. Leave it unset for a server that needs none. |
| `HARNESS_ENDPOINT`, `HARNESS_MODEL`, `HARNESS_API_KEY` | Older names for the same three. Each is used when its `TURNLOOM_` twin is not set. |
| A provider's key variable | The key for a saved connection, for example `OPENROUTER_API_KEY`, `OPENAI_API_KEY`, `GROQ_API_KEY`, `GEMINI_API_KEY` or `HF_TOKEN`. It takes precedence over the credential store. |
| `TURNLOOM_CONFIG_DIR` | The configuration folder. |

`TURNLOOM_ENDPOINT` and `TURNLOOM_MODEL` have to be set together. With only one of them, every command that needs a model stops with `Custom inference requires both TURNLOOM_ENDPOINT and TURNLOOM_MODEL.` When both are set they replace the saved connection in `run`, `chat`, `team`, Studio, `serve`, `acp` and `proxy`.

A custom endpoint comes with no prices and no context window. Turnloom assumes 128,000 tokens, and `--budget` needs `--input-rate` and `--output-rate`.

```sh
export TURNLOOM_ENDPOINT=http://127.0.0.1:4000/v1
export TURNLOOM_MODEL=my-model
turnloom chat
```

```powershell
$env:TURNLOOM_ENDPOINT = "http://127.0.0.1:4000/v1"
$env:TURNLOOM_MODEL = "my-model"
turnloom chat
```

### Variables read only by `turnloom-agent`

`turnloom-agent` is run by benchmark harnesses, which set their own names. For each setting, the first variable that is set wins.

| Setting | Variables, in order | If none is set |
| --- | --- | --- |
| Endpoint | `TURNLOOM_ENDPOINT`, `HARNESS_ENDPOINT`, `OPENAI_BASE_URL`, `OPENAI_API_BASE` | The command stops |
| Model | `TURNLOOM_MODEL`, `HARNESS_MODEL` | The command stops |
| Key | `TURNLOOM_API_KEY`, `HARNESS_API_KEY`, `OPENAI_API_KEY` | The command stops |
| Deadline in seconds | `HARNESS_DEADLINE_SECONDS` | 86400 |
| Context window | `HARNESS_CONTEXT_TOKENS`, `HLB_CONTEXT_WINDOW` | 128000 |
| Longest reply | `HARNESS_MAX_REPLY_TOKENS`, `HLB_MAX_OUTPUT_TOKENS` | 16384 |
| Reasoning effort | `HARNESS_REASONING_EFFORT`, `HLB_REASONING_EFFORT` | Not sent |

A flag on the command line overrides its variable. No other command reads `OPENAI_BASE_URL`, `OPENAI_API_BASE`, the `HLB_*` names, or the `HARNESS_*` names in the last four rows.

### Variables the GitHub Action sets

The action at the root of the repository turns its inputs into these variables for `scripts/run_action.py`, which then calls `turnloom run`. You do not set them yourself.

| Input | Variable | Default |
| --- | --- | --- |
| `task` | `TURNLOOM_TASK` | required |
| `endpoint` | `TURNLOOM_ENDPOINT` | required |
| `model` | `TURNLOOM_MODEL` | required |
| `api-key` | `TURNLOOM_API_KEY` | empty |
| `mode` | `TURNLOOM_MODE` | `read-only` |
| `persona` | `TURNLOOM_PERSONA` | `builder` |
| `workspace` | `TURNLOOM_WORKSPACE` | `.` |
| `max-steps` | `TURNLOOM_MAX_STEPS` | 40 |
| `deadline` | `TURNLOOM_DEADLINE` | 900 |
| `fail-on-incomplete` | `TURNLOOM_STRICT` | `true` |

### What the agent's commands can see

Commands the agent runs inherit your environment with model credentials taken out. These are removed:

- every provider preset's key variable, such as `OPENROUTER_API_KEY` or `GROQ_API_KEY`;
- `TURNLOOM_API_KEY`, `HARNESS_API_KEY`, `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `AZURE_OPENAI_API_KEY`, `AZURE_API_KEY`, `AZURE_AI_API_KEY`, `GEMINI_API_KEY`, `GOOGLE_API_KEY`, `HF_TOKEN`, `COHERE_API_KEY`, `WATSONX_APIKEY`, `DATABRICKS_API_KEY`, `REPLICATE_API_KEY`;
- `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_SESSION_TOKEN`, `AWS_BEARER_TOKEN_BEDROCK`;
- `GH_TOKEN`, `GITHUB_TOKEN`, `GH_ENTERPRISE_TOKEN`;
- any variable whose value is the key in use.

Everything else in your environment reaches the command. This is a guard against accidents, not a boundary.

## Built-in limits

These are fixed in the terminal commands. Knowing them explains most surprises.

| Limit | Value |
| --- | --- |
| Tool results kept whole before folding | The newest 3. Older ones of 800 characters or more are folded. |
| Command output the prompt receives | 16,000 characters each of standard output and standard error. The full output, up to 400,000 characters per stream, goes to the archive. |
| Session archive | 4,000,000 characters. The least recently used record is dropped first. |
| Command timeout | 120 seconds unless the model asks for more, up to 600. |
| File the file tools will read or write | 2 MiB of text. Binary files are refused. |
| `read_file` | 250 lines per call by default, 2000 at most. |
| `search_files` | 200 matching lines, 50 per file. |
| `list_files` | 300 entries. |
