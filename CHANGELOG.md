# Changelog

Notable changes to Turnloom. Versions follow [Semantic Versioning](https://semver.org); before 1.0 a minor version may change behavior.

## Unreleased

### Added

- **Agents in this repository's CI.** Three workflows run Turnloom on its own issues and pull requests: a review posted as one comment, triage of new issues, and an attempt at an issue on a branch when a maintainer labels it. The job that holds the model key cannot write or run commands, the agent's edits are executed only in a job with no secret, and `scripts/agent_reply.py` makes an answer safe to post. They are off until a `TURNLOOM_API_KEY` secret is set. [Agents in CI](docs/automation.md) describes the design and how to copy it.
- The GitHub Action takes `budget`, `input-rate` and `output-rate`, so a task in CI can be given a spending limit.

### Security

- The GitHub Action starts Turnloom with the working directory off Python's import path. Before, a file in the workspace named like a module that Turnloom imports later, including one the agent wrote in `auto-edit` mode, could be loaded by the process that holds the model key.

### Fixed

- The GitHub Action no longer fails on a Windows runner when the answer holds a character the console cannot encode.

## 0.7.1

A release shaped by running Turnloom beside seven other agents on Terminal-Bench tasks, and by writing the reference documentation. Both turned up things to fix.

### Security

- **A policy rule that asks is always put to you.** With a `provio.yaml` in the workspace, a rule with the verdict `ask` was answered without a prompt when `--auto-edit`, an `--allow` pattern or an earlier "always" already covered the call. In the terminal, Studio and editors the question is now asked for that call, names the rule, and offers no "always". Under `turnloom serve` nobody is present to answer, so the call is refused, as it already was under `--yes`.

### Changed

- A file tool asked for a path outside the workspace now says that the file tools reach only the project root and, in a session that has a shell, that the shell is the tool for other paths. Before, the bare refusal left the model retrying or guessing.
- `turnloom-agent` tells the model that the session is unattended: nobody will act on advice in its reply, so it has to run the commands and start the services itself. Before, some tasks ended with instructions for a person who was not there.
- The shell tool's description says that a long-running process belongs in the background.
- `--budget` without known prices says how to supply them: pass `--input-rate` and `--output-rate`, or run `turnloom connect` again.
- `turnloom-agent` no longer insists on an API key, so it works with a local endpoint that needs none.
- The GitHub Action installs the `guard` extra, so a repository with a `provio.yaml` runs under its policy instead of stopping with an error.
- Help text for the flags that had none: `--workspace`, `--reply`, `--deadline`, `--effort`, `--no-fold`, `--skills-dir`, `--tier`, `--search` and the proxy's `--port`.

### Fixed

- An endpoint that refuses `max_tokens` and asks for `max_completion_tokens`, as OpenAI models do behind a gateway or proxy, is remembered for the rest of the process. Before, every model call was sent twice.
- `turnloom personas` says "1 persona" when there is one.

### Documentation

- New pages: [quick start](docs/quickstart.md), [command reference](docs/cli.md), [configuration](docs/configuration.md), [recipes](docs/recipes.md) and [FAQ](docs/faq.md), with an [index](docs/README.md).
- The README is rewritten around a first run, with copy-and-paste examples, and its links now work on PyPI.
- [Benchmarks](benchmarks/README.md): the method, scripts and results of the comparison with other agents.
- Corrections where a page disagreed with the code: the order of the `HARNESS_*` and `HLB_*` variables, what an MCP server inherits from your environment, where the `unwind` key is accepted, and examples that asked a read-only session to review a Git diff it cannot see.

### Project

- A roadmap, a code of conduct, a contributor guide that walks through a first pull request, and starter issues.

## 0.7.0

The first public release. The agent gains real file tools, streaming, sessions that survive the process, and reversible edits. Studio is redesigned and rebuilt on those. Turnloom can now be driven from editors and from other agents.

### Added

- **File tools in every session.** `list_files`, `read_file`, `search_files`, `edit_file` and `write_file` are confined to the workspace. Edits show a diff for approval, keep line endings, and are written atomically. Before this release the terminal agent could only use the shell.
- **Undo for edits.** `turnloom undo`, `/undo` in chat and Studio restore the files a turn edited. A file changed since is kept and reported.
- **Streaming.** Replies appear as they are written in the terminal, in Studio and in editors, with tool activity shown while it runs.
- **Sessions.** `turnloom sessions` lists them, `--resume` continues one with its tool results intact, and Studio can start, list and resume sessions.
- **Approval modes.** `--auto-edit`, `--read-only`, `--allow PATTERN`, and an "always" answer at the prompt. Studio has the same choices.
- **Editors.** `turnloom acp` serves the Agent Client Protocol, for Zed, JetBrains IDEs and other ACP clients.
- **Project instructions and skills.** `AGENTS.md` (or `CLAUDE.md`) is read from the workspace root. Skills under `.agents/skills` and `.claude/skills` are offered to the model.
- **MCP over HTTP.** Server entries may be a streamable HTTP `url` with a `bearer_env`, and process entries may set `env`.
- **Turnloom as an MCP server for other agents.** `turnloom serve` adds `transcript` and `read_result`, and with `--allow-run` a `run_task` tool.
- **GitHub Action and container image definition** for unattended runs.
- `turnloom studio`, `turnloom doctor`, `--max-steps`, `--events`, and chat commands `/diff`, `/undo`, `/new`, `/help`.
- Studio can create, rename and delete files, shows what a turn edited with a review and a revert, and has light and dark themes.
- `TURNLOOM_ENDPOINT`, `TURNLOOM_MODEL` and `TURNLOOM_API_KEY`.

### Changed

- **Commands and edits are declined unless something approves them.** A caller that sets no approval callback must opt in with `auto_approve`. Previously a missing callback meant "approve everything".
- **The policy gate covers file edits** as `fs.write`, alongside shell commands, reads and MCP calls. Searches return only lines from files the policy allows, delegated tasks through `turnloom serve` are gated, and a rule that asks is put to the operator once, with an edit's diff in view. Studio refuses to run ungated when a policy file exists and Provio is missing.
- **Programs are never taken from the project folder.** ripgrep, Git, Provio and `taskkill` are resolved from `PATH` and the interpreter's scripts folder only. On Windows a bare program name used to resolve in the current directory first.
- Directory junctions leading out of the workspace are no longer listed or searched. Read-only sessions no longer offer MCP tools.
- Studio remembers an enabled MCP server together with its definition and switches it off when the definition changes. Choosing "always" on an edit lasts for the session and is not saved.
- **Context overflow archives instead of deleting.** Exchanges removed to fit the window stay recoverable through `recall`, and an oversized newest result is shortened in place rather than failing the turn.
- **Tool results the model has not read are never folded.** A batch of parallel calls used to lose all but its last three results before the model saw them.
- **Full command output is archived** when the prompt only receives its head and tail.
- A timeout or a stop ends a command's whole process tree. Stopping a turn interrupts a running command and an in-flight model reply.
- Chat continues after a failed turn instead of exiting, and Ctrl+C ends the turn rather than the program.
- Retries honor `Retry-After`, cover dropped connections, and retry a timed-out request once instead of four times.
- A truncated reply is returned as `partial`. An empty reply is retried once. Limits explain why they stopped the turn.
- Three identical tool calls in a row end tool use everywhere, not only in Studio. Repeating a check between edits is no longer treated as a loop.
- `turnloom team` runs without LangGraph installed, and planning and review stages are read-only by construction. Studio's team stages gain a read-only switch.
- Session traces, the usage ledger and the undo database get a `.gitignore`, so they stay out of the user's repository. `.turnloom/mcp.json` and `.turnloom/personas/` remain visible to Git so a team can share them.
- **Studio settings moved out of the workspace** into the operator's configuration folder. A `.turnloom/studio.json` shipped in a repository could previously turn on auto-approve or enable MCP servers; that file is no longer read.
- Studio no longer shows sample data. It keeps the conversation when a turn fails or the persona changes, waits for approvals without a timer, stops by itself when left unused in a browser, and opens without putting its token on a browser's command line. Closing Studio stops a running turn.
- `turnloom-studio` is a console command, so its help and errors are visible. `turnloom-studio-gui` is the windowless variant. `--no-open` and `--port` support remote shells.
- The proxy refuses requests that carry a browser `Origin` or a foreign `Host`.
- Command output is decoded as UTF-8 first. Redirected terminal output is UTF-8 on Windows.
- The evaluation interval resamples attempts as well as tasks, checks end their process tree at the timeout, and the example manifest is a task that runs.
- The `desktop` extra no longer installs PyInstaller; `build-app` does. `studio` and `full` are composed from the smaller extras.

### Removed

- `turnloom demo`, which folded a hard-coded string. `turnloom doctor` is the offline check.
- Benchmark-specific rules from the default system prompt. `turnloom-agent` and the evaluation runner add them to their own sessions.
- Thirteen provider presets that need a vendor-specific client or a token exchange.
- The Cline UI components and the Tailwind-dependent styling in Studio.

### Upgrading from 0.6

- Scripts that used `turnloom run` without a terminal must pass `--read-only`, `--auto-edit` or `--yes`.
- `HARNESS_ENDPOINT`, `HARNESS_MODEL` and `HARNESS_API_KEY` still work. Prefer the `TURNLOOM_` names.
- Code that builds a `Config` directly: `approve` now receives `(kind, detail)`, and unattended callers set `auto_approve=True`. `workspace_tool` is replaced by the built-in file tools.
- A PyInstaller build installs `.[build-app]` instead of `.[studio,full,desktop]`.
- Studio settings saved by 0.6 in `.turnloom/studio.json` are not carried over. Set them again once; the old file can be deleted.
- MCP clients of `turnloom serve`: the tools are now `sessions`, `receipt`, `transcript`, `read_result`, `archive_output`, `recall` and optionally `run_task`.

## 0.6.0

Tagged on GitHub and not published to a package index.

- Studio rebuilt around a view rail and a command palette, opening in a native window through pywebview, with a PyInstaller spec for a standalone app.
- New Studio views: Changes, Agents (283 vendored Agency Agents personas and a team builder), Cost, Policy, Tools, Documents and Undo ledger.
- Every model call metered into `.turnloom/usage.jsonl`, with cached input priced at the cache rate and each request's reply limit fitted to the remaining context window.
- Provio gates shell commands, workspace reads and MCP tool calls when the workspace has a `provio.yaml`, and fails closed.
- LiteLLM reaches native provider APIs as `litellm:<provider>`.
- `turnloom proxy` serves an OpenAI-compatible endpoint that folds and meters requests from other harnesses.
- Folded previews keep their ending, and `recall` can search.
- A Harbor installed-agent adapter, the headless `turnloom-agent` entry point and ATIF export.

## 0.5.0 and earlier

Development versions: the terminal agent with shell and recall tools, provider connections with OpenRouter sign-in, MCP stdio servers, Agent Skills loading, the evaluation tools, and the first version of Studio.
