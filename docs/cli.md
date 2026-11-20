# Command reference

Every command, every flag and every default. For worked examples see [Recipes](recipes.md). For files and environment variables see [Configuration](configuration.md).

```text
turnloom [-h] [--version] COMMAND ...
```

`python -m turnloom` is the same program. On Windows inside a virtual environment that is `.venv\Scripts\python -m turnloom`. Every command accepts `-h` or `--help`.

| Group | Commands |
| --- | --- |
| [Connect a model](#connect-a-model) | `providers`, `models`, `connect`, `connection`, `disconnect` |
| [Do work](#do-work) | `run`, `chat`, `team` |
| [Sessions](#sessions) | `sessions`, `replay`, `undo`, `atif` |
| [Inspect the setup](#inspect-the-setup) | `doctor`, `personas`, `skills`, `tools` |
| [Other front ends](#other-front-ends) | `studio`, `serve`, `acp`, `proxy` |
| [Other entry points](#other-entry-points) | `turnloom-studio`, `turnloom-studio-gui`, `turnloom-agent`, `turnloom-eval`, `turnloom-compare` |

## Exit status

| Status | Meaning |
| --- | --- |
| 0 | The command succeeded. For `run` and `team`, the task completed. |
| 1 | `run` and `team`: the task ended in any state other than completed or stopped. `undo`: at least one file was kept because it had changed. `tools`: the server returned an error. `studio`: it could not start. |
| 2 | A usage or setup problem, such as a missing argument, no model connection or an unknown persona. One line on standard error starting `turnloom: error:` says what to fix. |
| 130 | `run` and `team`: you stopped the task with Ctrl+C. |

## Connect a model

### `turnloom providers`

Lists the provider presets: an ID, a display name and the endpoint. With the `llm` extra installed it also lists the `litellm:` providers.

```text
turnloom providers [--search SEARCH]
```

| Flag | Default | Effect |
| --- | --- | --- |
| `--search TEXT` | empty | Show only lines containing this text. Case does not matter. |

```sh
turnloom providers --search groq
```

```text
groq  Groq  https://api.groq.com/openai/v1
```

### `turnloom models`

Lists a provider's models without saving anything. Models that report no tool support are left out.

```text
turnloom models [--tier {all,free,paid,unknown}] [--search SEARCH] [--open-weights] PROVIDER
```

| Argument or flag | Default | Effect |
| --- | --- | --- |
| `PROVIDER` | required | A preset ID from `turnloom providers`, or `litellm:<provider>` for a native API. |
| `--tier` | `all` | Show only `free`, `paid` or `unknown` models. |
| `--search TEXT` | empty | Show only model IDs containing this text. |
| `--open-weights` | off | Show only model IDs that Models.dev marks as open weights. |

```sh
turnloom models openrouter --tier free --search qwen
```

Each line is a model ID followed by its tier, its prices in USD per million input and output tokens when known, and whether tool support is listed.

### `turnloom connect`

Chooses a provider and a model and saves the choice. With no arguments it shows menus. The key you type goes to the operating system's credential store.

```text
turnloom connect [--tier {all,free,paid,unknown}] [--search SEARCH] [--open-weights] [--model MODEL] [PROVIDER]
```

| Argument or flag | Default | Effect |
| --- | --- | --- |
| `PROVIDER` | chosen from a menu | A preset ID from `turnloom providers`, or `litellm:<provider>`. |
| `--model ID` | chosen from a menu | Select this exact catalog ID without the model menu. |
| `--tier` | `all` | Limit the model list to one tier. |
| `--search TEXT` | empty | Limit the model list to IDs containing this text. |
| `--open-weights` | off | Limit the model list to open-weight models. |

In a menu, type a number to choose, any text to filter, `/next` for the next twelve, `/all` to clear the filter or `/quit` to cancel.

Without a terminal, `connect` cannot show menus or ask for a key. Give the provider and `--model`, and set the provider's key variable:

```sh
export GROQ_API_KEY=your-key
turnloom connect groq --model MODEL_ID
```

### `turnloom connection`

Prints the saved choice as JSON: provider, model, prices, tier, context window and where the catalog entry came from. It never contains the key. With nothing saved it prints `No saved connection.`

```sh
turnloom connection
```

### `turnloom disconnect`

Clears the saved choice.

```text
turnloom disconnect [--forget-key]
```

| Flag | Default | Effect |
| --- | --- | --- |
| `--forget-key` | off | Also delete this provider's key from the credential store. |

```sh
turnloom disconnect --forget-key
```

## Do work

`run`, `chat` and `team` take the same flags, with two differences: `chat` has no `task` argument, and `team` has no `--resume`.

```text
turnloom run  [flags] TASK
turnloom chat [flags]
turnloom team [flags] TASK
```

All three need a model connection. In a terminal with nothing connected they start `turnloom connect` first.

### Workspace and instructions

| Flag | Default | Effect |
| --- | --- | --- |
| `--workspace DIR` | current directory | The project the agent works in. File tools are confined to it, and commands start in it. |
| `--persona ID` | `builder` | The role added to the instructions. IDs come from `turnloom personas`. `reviewer` and `architect` are short names. |
| `--persona-file FILE` | none | A Markdown file of extra instructions, added after the persona. Use only text you trust. |
| `--skills-dir DIR` | none | A folder of skills, each in its own subfolder with a `SKILL.md`. |
| `--skill NAME` | none | Load this skill from `--skills-dir` into the instructions. Repeatable. Requires `--skills-dir`. |
| `--mcp FILE` | none | A JSON file of MCP servers the agent may use. Needs the `mcp` extra. The format is in [Configuration](configuration.md#mcp-servers). |

### Approval flags

With none of these flags, Turnloom asks in the terminal before each command, each edit and each MCP call.

| Flag | Default | Effect |
| --- | --- | --- |
| `--auto-edit` | off | Approve file edits. Commands and MCP calls still ask. |
| `--allow PATTERN` | none | Approve shell commands that match this glob, such as `"pytest*"`. Repeatable. The pattern is matched against the whole command and is case-sensitive. It never covers a command containing a semicolon, `&`, a pipe, `<`, `>`, a backtick, `$`, parentheses, `%`, `^` or a line break. |
| `--read-only` | off | Give the agent no edit tools, no shell and no MCP tools. It can list, read, search and answer. |
| `--yes` | off | Approve every command, edit and MCP call. Use it only inside a container or a disposable checkout. |
| `--no-gate` | off | Skip the Provio policy gate even when the workspace has a `provio.yaml`. |

When standard input is not a terminal there is nobody to ask, so one of `--read-only`, `--auto-edit` or `--yes` is required. With `--auto-edit` alone in that situation, commands are declined.

When the workspace has a `provio.yaml`, the policy is checked before the approval. See [Put a Provio policy under the agent](recipes.md#put-a-provio-policy-under-the-agent).

### Limits and cost

In `chat` these apply to each turn separately.

| Flag | Default | Effect |
| --- | --- | --- |
| `--max-steps N` | 100 | Model calls allowed per task. 0 means no limit. At the limit the last call is spent writing up what was found. |
| `--deadline SECONDS` | 1800 | Wall-clock time allowed per task. |
| `--budget USD` | 0, no budget | Estimated spend allowed per task. The turn stops before a request that would pass it. Needs prices for the model. |
| `--input-rate USD` | from the saved connection | Price per million input tokens. |
| `--output-rate USD` | from the saved connection | Price per million output tokens. |
| `--reply TOKENS` | 4096 | Longest reply the model may write in one call. It has to leave more than 2000 tokens of `--context` free. |
| `--context TOKENS` | from the model catalog, else 128000 | The model's context window. |
| `--effort VALUE` | not sent | Sent to the model as `reasoning_effort` on every request, or as `reasoning.effort` with `--transport responses`. |
| `--no-fold` | off | Keep old tool results whole in the prompt. See [the FAQ](faq.md#what-is-folding-and-how-do-i-turn-it-off). |

### Model transport

| Flag | Default | Effect |
| --- | --- | --- |
| `--transport chat` | the default, unless `TURNLOOM_TRANSPORT` says otherwise | Send requests to `/chat/completions`. Every OpenAI-compatible endpoint has it. |
| `--transport responses` | off | Send requests to `/responses`, OpenAI's Responses API. |

With `responses`, the reasoning a model does before a tool call comes back encrypted. Turnloom saves it in the session transcript and sends it again on the following requests, so the model can continue from it. Requests carry `store: false`, so the conversation is not kept on the provider's side between requests. Everything else is the same under both transports: tools, folding, limits, streaming, the receipt and the `--json` result.

Use `responses` only with an endpoint that has a Responses API. One that does not answers with HTTP 404, and Turnloom says to use the chat transport. A `litellm://` connection takes `chat` only.

A saved session can be resumed under either transport. The chat transport leaves the saved reasoning out of its requests. If saved reasoning was encrypted for another key or organization and the endpoint refuses it, Turnloom drops it and repeats the request.

### Output

| Flag | Default | Effect |
| --- | --- | --- |
| `--no-stream` | off | Print the answer when it is complete, not as it is written. |
| `--json` | off | Print the final result of each task as one JSON object on standard output. Tool lines and the receipt are not printed. |
| `--events` | off | Print each session event as a JSON line on standard output while it happens. Tool lines and the receipt are not printed. |

Without `--json` or `--events`, the answer and the receipt go to standard output. The header line, tool activity and approval prompts go to standard error.

The `--json` object has these fields:

| Field | Meaning |
| --- | --- |
| `status` | `completed`, `partial`, `stopped`, `error`, `timeout`, `step_limit` or `budget_limit`. |
| `answer` | The agent's final text. |
| `steps` | Model calls made. |
| `elapsed_seconds` | Wall-clock time for the task. |
| `usage` | `prompt_tokens`, `completion_tokens` and `cached_tokens`. `estimated_calls` appears when the provider omitted usage and Turnloom estimated it. |
| `errors` | Why the task stopped early, as a list of sentences. |
| `estimated_usd` | Estimated cost at the rates in use. 0 when no rates are known. |
| `folded_chars` | Characters of old tool output replaced by previews. |
| `files_changed` | Workspace paths the agent's file tools edited in this task. |
| `truncated` | `true` when the reply hit its token limit. |

### Session

| Flag | Default | Effect |
| --- | --- | --- |
| `--resume [SESSION]` | off | Continue a saved session. With no value, the most recent one in the workspace. With a value, the session whose ID starts with it. Not available on `team`. |

### `turnloom run`

Runs one task and exits.

```sh
turnloom run "Fix the failing test in tests/test_parser.py" --allow "pytest*" --budget 0.25
```

### `turnloom chat`

Starts a conversation that continues across turns.

```sh
turnloom chat --auto-edit
```

At the `turnloom ›` prompt:

| Command | Effect |
| --- | --- |
| `/help` | Show these commands. |
| `/diff` | Show the files the last turn changed, as diffs. |
| `/undo` | Restore the files the last turn edited. |
| `/new` | Start a fresh conversation in the same session file. |
| `/quit` or `/exit` | Leave the chat. Ctrl+C or end-of-file at the prompt does the same. |

`/diff` and `/undo` cover edits made since this `chat` process started. For edits from an earlier process use `turnloom undo`.

### `turnloom team`

Runs one task in three stages that share the workspace, the deadline and the budget: plan, build, review. Plan and review are read-only. A stage that does not complete stops the run.

```sh
turnloom team "Add input validation to the signup form" --budget 0.50
```

The answer is not streamed. When the run ends, the report of each stage is printed, followed by one receipt that totals all stages. `--persona` applies to every stage, and `--max-steps` applies to each stage separately.

## Sessions

### `turnloom sessions`

Lists the 30 most recently used sessions in a workspace: ID prefix, time, turns, last status, model and the start of the first task.

```text
turnloom sessions [--workspace WORKSPACE]
```

| Flag | Default | Effect |
| --- | --- | --- |
| `--workspace DIR` | current directory | The workspace to list. |

```sh
turnloom sessions
```

### `turnloom replay`

Prints a saved session without calling a model: each task with its model, each tool call with the start of its result, each edited path, and each turn's receipt.

```text
turnloom replay PATH
```

| Argument | Effect |
| --- | --- |
| `PATH` | A transcript file, normally `.turnloom/<session id>.jsonl`. |

```sh
turnloom replay .turnloom/SESSION_ID.jsonl
```

### `turnloom undo`

Restores the files edited by the last turn of a saved session. The stages of a `team` run count as one turn.

```text
turnloom undo [--workspace WORKSPACE] [SESSION]
```

| Argument or flag | Default | Effect |
| --- | --- | --- |
| `SESSION` | the most recent session | The start of a session ID. |
| `--workspace DIR` | current directory | The workspace the session belongs to. |

```sh
turnloom undo 8396
```

It prints `Restored NAME` for each file put back. A file that no longer holds the agent's version is left alone and reported as `Kept NAME: it changed after the agent's edit`, and the exit status is 1. A file the agent created is deleted.

### `turnloom atif`

Converts a transcript to an ATIF trajectory (schema `ATIF-v1.8`), the format Harbor reads.

```text
turnloom atif TRANSCRIPT OUTPUT
```

| Argument | Effect |
| --- | --- |
| `TRANSCRIPT` | A session transcript, `.turnloom/<session id>.jsonl`. |
| `OUTPUT` | The JSON file to write. |

```sh
turnloom atif .turnloom/SESSION_ID.jsonl trajectory.json
```

## Inspect the setup

### `turnloom doctor`

Reports the version, Python, the connection, the credential store, ripgrep, each optional feature and the state of the workspace. It calls no model and prints no key.

```text
turnloom doctor [--workspace WORKSPACE]
```

| Flag | Default | Effect |
| --- | --- | --- |
| `--workspace DIR` | current directory | The workspace to report on. |

```sh
turnloom doctor
```

[The FAQ](faq.md#what-does-turnloom-doctor-check) explains each line.

### `turnloom personas`

Lists personas: ID, division and description. By default it shows Turnloom's own two, the personas in `.turnloom/personas/` of the current directory, and the engineering, testing and security roles.

```text
turnloom personas [--search SEARCH] [--all]
```

| Flag | Default | Effect |
| --- | --- | --- |
| `--search TEXT` | empty | Show personas whose name, division or description contains this text, from every division. |
| `--all` | off | Show every division. |

```sh
turnloom personas --search security
```

### `turnloom skills`

Lists the skills in a folder: one line per skill with its name and description.

```text
turnloom skills ROOT
```

| Argument | Effect |
| --- | --- |
| `ROOT` | A folder whose subfolders each hold a `SKILL.md`. |

```sh
turnloom skills external-skills/skills
```

### `turnloom tools`

Starts one MCP server from a configuration file and prints its tools as JSON, without a model connection. Needs the `mcp` extra.

```text
turnloom tools CONFIG SERVER [QUERY]
```

| Argument | Default | Effect |
| --- | --- | --- |
| `CONFIG` | required | The MCP configuration file. |
| `SERVER` | required | A server name from that file. |
| `QUERY` | empty | Show only tools whose name or description contains this text. Empty lists all tools. |

```sh
turnloom tools examples/mcp.json git git_status
```

The output has `count`, `names` (every matching tool) and `tools` (the five best matches with their input schemas).

## Other front ends

### `turnloom studio`

Opens Studio, the workbench, for one workspace.

```text
turnloom studio [--workspace WORKSPACE] [--browser] [--no-open] [--port PORT]
```

| Flag | Default | Effect |
| --- | --- | --- |
| `--workspace DIR` | current directory | The project to open. |
| `--browser` | off | Open in the default browser, not in an app window. |
| `--no-open` | off | Print the session address and open nothing. For remote shells. |
| `--port PORT` | 0, a free port | The loopback port for `--browser` and `--no-open`. |

```sh
turnloom studio --browser --port 8765
```

Studio always listens on `127.0.0.1`.

### `turnloom serve`

Runs Turnloom as an MCP server on standard input and output, so another agent can read this workspace's sessions and, if you allow it, hand Turnloom a task. Needs the `mcp` extra.

```text
turnloom serve [--workspace WORKSPACE] [--allow-run] [--allow-edits] [--allow-shell]
```

| Flag | Default | Effect |
| --- | --- | --- |
| `--workspace DIR` | current directory | The workspace to serve. |
| `--allow-run` | off | Offer the `run_task` tool. Delegated tasks are read-only and use your connected model. |
| `--allow-edits` | off | With `--allow-run`, let delegated tasks edit files. |
| `--allow-shell` | off | With `--allow-run`, let delegated tasks run commands. |

```sh
turnloom serve --workspace /absolute/path/to/project --allow-run
```

The tools it offers are listed in [Let another agent delegate to Turnloom](recipes.md#let-another-agent-delegate-to-turnloom).

### `turnloom acp`

Runs Turnloom as an Agent Client Protocol agent on standard input and output, so an editor can drive it. Needs the `acp` extra.

```text
turnloom acp [--workspace WORKSPACE] [--persona PERSONA]
```

| Flag | Default | Effect |
| --- | --- | --- |
| `--workspace DIR` | current directory | The base directory. Each session opens in the directory the editor names. |
| `--persona ID` | `builder` | The persona for every session. |

```sh
turnloom acp --persona reviewer
```

### `turnloom proxy`

Serves an OpenAI-compatible chat-completions endpoint on `127.0.0.1` that forwards another program's requests to your connected model, folding old tool results on the way and recording usage.

```text
turnloom proxy [--port PORT] [--workspace WORKSPACE] [--keep KEEP] [--no-fold] [--token TOKEN]
```

| Flag | Default | Effect |
| --- | --- | --- |
| `--port PORT` | 8787 | The loopback port. |
| `--workspace DIR` | current directory | Where the usage ledger `.turnloom/usage.jsonl` is written. |
| `--keep N` | 3 | Newest tool results left unfolded. |
| `--no-fold` | off | Do not fold. Only meter. |
| `--token TOKEN` | none | Require clients to send this bearer token. |

```sh
turnloom proxy --port 8787 --keep 3
```

Point the other program's OpenAI base URL at `http://127.0.0.1:8787/v1`. [The integration guide](integrations.md#put-turnloom-under-another-harness) describes what the proxy does to a request.

## Other entry points

### `turnloom-studio` and `turnloom-studio-gui`

`turnloom-studio` takes the same flags as `turnloom studio`, with one difference: when `--workspace` is left out it asks for a folder in a dialog. `turnloom-studio-gui` is the same program started without a console window on Windows.

```text
turnloom-studio [--workspace WORKSPACE] [--browser] [--no-open] [--port PORT]
```

```sh
turnloom-studio --workspace path/to/project
```

### `turnloom-agent`

Runs one task unattended and prints the result as one JSON object. It is the entry point for benchmark harnesses. Every command and edit is approved, no workspace policy is consulted, and neither the saved connection nor `AGENTS.md` is read. Use it only inside a sandbox.

```text
turnloom-agent [--task TASK] [--workspace WORKSPACE] [--model MODEL] [--endpoint ENDPOINT] [--api-key API_KEY]
               [--deadline-seconds N] [--max-steps N] [--context-tokens N] [--max-reply-tokens N]
               [--reasoning-effort VALUE] [--transport {chat,responses}] [--atif PATH] [--system-prompt-file FILE]
               [--output-chars N] [--transcript PATH]
```

| Flag | Default | Effect |
| --- | --- | --- |
| `--task TEXT` | read from standard input | The task. |
| `--workspace DIR` | current directory | The project directory. |
| `--model ID` | `TURNLOOM_MODEL`, then `HARNESS_MODEL` | The model ID. Required. |
| `--endpoint URL` | `TURNLOOM_ENDPOINT`, then `HARNESS_ENDPOINT`, `OPENAI_BASE_URL`, `OPENAI_API_BASE` | The OpenAI-compatible base URL. Required. |
| `--api-key KEY` | `TURNLOOM_API_KEY`, then `HARNESS_API_KEY`, `OPENAI_API_KEY` | The credential. Required, even for an endpoint that ignores it. |
| `--deadline-seconds N` | `HARNESS_DEADLINE_SECONDS`, else 86400 | Wall-clock time allowed. |
| `--max-steps N` | 0, no limit | Model calls allowed. |
| `--context-tokens N` | `HARNESS_CONTEXT_TOKENS`, then `HLB_CONTEXT_WINDOW`, else 128000 | The context window. |
| `--max-reply-tokens N` | `HARNESS_MAX_REPLY_TOKENS`, then `HLB_MAX_OUTPUT_TOKENS`, else 16384 | Longest reply per call. |
| `--reasoning-effort VALUE` | `HARNESS_REASONING_EFFORT`, then `HLB_REASONING_EFFORT`, else not sent | Sent as `reasoning_effort`. |
| `--transport chat` or `--transport responses` | `TURNLOOM_TRANSPORT`, else `chat` | Chat Completions or OpenAI's Responses API. See [Model transport](#model-transport). |
| `--atif PATH` | none | Also write an ATIF trajectory, rewritten after every model call. |
| `--transcript PATH` | none, or the `--atif` path with a `.jsonl` suffix | Where to write the session transcript. |
| `--system-prompt-file FILE` | the built-in prompt | Replace the base system prompt with this file. |
| `--output-chars N` | 16000 | Characters of one tool result the prompt receives before it is clipped. |

```sh
export TURNLOOM_ENDPOINT=https://openrouter.ai/api/v1
export TURNLOOM_MODEL=MODEL_ID
export TURNLOOM_API_KEY=your-key
turnloom-agent --task "Make the tests in tests/ pass" --atif logs/trajectory.json
```

The exit status is 0 when the task completes and 1 otherwise.

### `turnloom-eval`

Runs every task of a manifest several times on fresh copies of its workspace, scores each trial with the task's check command, and writes `trials.jsonl`, `summary.json` and one transcript per trial. Every command and edit is approved, so run it on disposable task copies only.

```text
turnloom-eval [--results RESULTS] [--attempts N] [--model MODEL] [--endpoint ENDPOINT] [--api-key API_KEY]
              [--deadline-seconds N] [--max-steps N] [--context-tokens N] [--max-reply-tokens N]
              [--reasoning-effort VALUE] [--persona-file FILE] [--persona PERSONA] [--team] [--mcp MCP]
              [--no-fold] [--output-chars N] [--input-usd-per-million USD] [--output-usd-per-million USD]
              MANIFEST
```

| Argument or flag | Default | Effect |
| --- | --- | --- |
| `MANIFEST` | required | A JSON array of cases. The format is in [Compare two configurations](experiments.md). |
| `--results DIR` | `results` | Where results are written. It must not already hold a `trials.jsonl`, and it must be outside every task workspace. |
| `--attempts N` | 3 | Trials per task. |
| `--model ID` | `TURNLOOM_MODEL`, then `HARNESS_MODEL`, else the saved connection | The model. Give it together with `--endpoint`. |
| `--endpoint URL` | `TURNLOOM_ENDPOINT`, then `HARNESS_ENDPOINT`, else the saved connection | The endpoint. Give it together with `--model`. |
| `--api-key KEY` | `TURNLOOM_API_KEY`, then `HARNESS_API_KEY` | The credential for `--endpoint`. Not used with the saved connection. |
| `--deadline-seconds N` | 1800 | Time allowed per trial. A case's own `deadline_seconds` wins. |
| `--max-steps N` | 0, no limit | Model calls allowed per trial. |
| `--context-tokens N` | 128000 | The context window. |
| `--max-reply-tokens N` | 8192 | Longest reply per call. |
| `--reasoning-effort VALUE` | not sent | Sent as `reasoning_effort`. |
| `--persona ID` | `builder` | The persona for every trial. |
| `--persona-file FILE` | none | Extra trusted Markdown instructions. `--system-prompt-file` is a second name for the same flag. |
| `--team` | off | Run each trial as a three-stage team run. |
| `--mcp FILE` | none | An MCP configuration for each trial. |
| `--no-fold` | off | Turn folding off, for a control run. |
| `--output-chars N` | 16000 | Characters of one tool result the prompt receives before it is clipped. |
| `--input-usd-per-million USD` | from the saved connection, else 0 | Input price used for the cost estimate. |
| `--output-usd-per-million USD` | from the saved connection, else 0 | Output price used for the cost estimate. |

```sh
turnloom-eval examples/manifest.json --attempts 3 --results results/fold
```

### `turnloom-compare`

Reports the paired difference in score between two `turnloom-eval` runs, with a 95% bootstrap interval. Both runs need the same tasks and the same number of attempts.

```text
turnloom-compare BASELINE CANDIDATE
```

| Argument | Effect |
| --- | --- |
| `BASELINE` | The `trials.jsonl` of the control run. |
| `CANDIDATE` | The `trials.jsonl` of the run you are testing. |

```sh
turnloom-compare results/control/trials.jsonl results/fold/trials.jsonl
```

## Environment variables

| Variable | Read by | Meaning |
| --- | --- | --- |
| `TURNLOOM_ENDPOINT` | Every command that needs a model | An OpenAI-compatible base URL. Set it together with `TURNLOOM_MODEL`. The pair takes precedence over the saved connection. |
| `TURNLOOM_MODEL` | Every command that needs a model | The model ID at that endpoint. |
| `TURNLOOM_API_KEY` | Every command that needs a model | The credential for `TURNLOOM_ENDPOINT`. May be empty for a local server, except with `turnloom-agent`. |
| `HARNESS_ENDPOINT`, `HARNESS_MODEL`, `HARNESS_API_KEY` | The same commands | Older names for the three variables above. Used when the `TURNLOOM_` one is not set. |
| `TURNLOOM_TRANSPORT` | Every command that calls a model, except `proxy` | `chat` or `responses`. The default for `--transport`, and the only way to choose the transport in Studio, `serve`, `acp` and `turnloom-eval`. See [Model transport](#model-transport). |
| `TURNLOOM_CONFIG_DIR` | Every command | The folder for the saved connection, the catalog cache and Studio's settings. See [Configuration](configuration.md#files-in-your-user-profile). |
| A provider's key variable, such as `OPENROUTER_API_KEY`, `OPENAI_API_KEY` or `GROQ_API_KEY` | `connect`, `models` and every command that uses the saved connection | That provider's key. It takes precedence over the credential store. `turnloom connect` names the variable when a key is missing. |
| `OPENAI_BASE_URL`, `OPENAI_API_BASE` | `turnloom-agent` | The endpoint, when neither `TURNLOOM_ENDPOINT` nor `HARNESS_ENDPOINT` is set. |
| `OPENAI_API_KEY` | `turnloom-agent` | The key, when neither `TURNLOOM_API_KEY` nor `HARNESS_API_KEY` is set. |
| `HARNESS_DEADLINE_SECONDS` | `turnloom-agent` | Default for `--deadline-seconds`. |
| `HARNESS_CONTEXT_TOKENS`, `HLB_CONTEXT_WINDOW` | `turnloom-agent` | Default for `--context-tokens`. The first one set wins. |
| `HARNESS_MAX_REPLY_TOKENS`, `HLB_MAX_OUTPUT_TOKENS` | `turnloom-agent` | Default for `--max-reply-tokens`. The first one set wins. |
| `HARNESS_REASONING_EFFORT`, `HLB_REASONING_EFFORT` | `turnloom-agent` | Default for `--reasoning-effort`. The first one set wins. |
| Variables named in an MCP configuration | `--mcp`, `tools`, Studio | `pass_env` names variables a server process may inherit. `bearer_env` names the variable holding an HTTP server's token. |
| `APPDATA` (Windows), `XDG_CONFIG_HOME` (elsewhere) | Every command | Where the configuration folder goes when `TURNLOOM_CONFIG_DIR` is not set. |

[Configuration](configuration.md#environment-variables) lists the same variables with more detail, and the ones the GitHub Action sets.
