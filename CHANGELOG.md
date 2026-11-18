# Changelog

Notable changes to Turnloom. Versions follow [Semantic Versioning](https://semver.org); before 1.0 a minor version may change behavior.

## 0.6.0

The first public release. The agent gains real file tools, streaming, sessions that survive the process, and reversible edits. Studio is rebuilt on those.

### Added

- **File tools in every session.** `list_files`, `read_file`, `search_files`, `edit_file` and `write_file` are confined to the workspace. Edits show a diff for approval, keep line endings, and are written atomically. Before this release the terminal agent could only use the shell.
- **Undo.** `turnloom undo`, `/undo` in chat and Studio restore the files a turn edited. A file changed since is kept and reported.
- **Streaming.** Replies appear as they are written in the terminal and in Studio, with tool activity shown while it runs.
- **Sessions.** `turnloom sessions` lists them, `--resume` continues one with its tool results intact, and Studio can start, list and resume sessions.
- **Approval modes.** `--auto-edit`, `--read-only`, `--allow PATTERN`, and an "always" answer at the prompt. Studio has the same choices.
- **Project instructions and skills.** `AGENTS.md` (or `CLAUDE.md`) is read from the workspace root. Skills under `.agents/skills` and `.claude/skills` are offered to the model.
- **MCP over HTTP.** Server entries may be a streamable HTTP `url` with a `bearer_env`.
- **Turnloom as an MCP server for other agents.** `turnloom serve` adds `transcript` and `recall`, and with `--allow-run` a `run_task` tool.
- **GitHub Action and container image definition** for unattended runs.
- `turnloom studio`, `turnloom doctor`, `--max-steps`, `--events`, and chat commands `/diff`, `/undo`, `/new`, `/help`.
- Context window sizes from the model catalog, cached-token reporting, and `TURNLOOM_ENDPOINT`, `TURNLOOM_MODEL`, `TURNLOOM_API_KEY`.

### Changed

- **Commands and edits are declined unless something approves them.** A caller that sets no approval callback must opt in with `auto_approve`. Previously a missing callback meant "approve everything".
- **Context overflow archives instead of deleting.** Exchanges removed to fit the window stay recoverable through `recall`, and an oversized newest result is shortened in place rather than failing the turn.
- **Tool results the model has not read are never folded.** A batch of parallel calls used to lose all but its last three results before the model saw them.
- **Full command output is archived** when the prompt only receives its head and tail.
- A timeout or a stop ends a command's whole process tree. Stopping a turn interrupts a running command and an in-flight model reply.
- Chat continues after a failed turn instead of exiting, and Ctrl+C ends the turn rather than the program.
- Retries honor `Retry-After`, cover dropped connections, and retry a timed-out request once instead of four times.
- A truncated reply is returned as `partial`. An empty reply is retried once. Limits explain why they stopped the turn.
- Three identical tool calls in a row end tool use everywhere, not only in Studio. Repeating a check between edits is no longer treated as a loop.
- `turnloom team` runs its three stages without LangGraph, and planning and review are read-only by construction.
- Session traces get a `.gitignore`, so they stay out of the user's repository.
- Studio no longer shows sample data. It keeps the conversation when a turn fails or the persona changes, waits for approvals without a timer, stops by itself when unused, and opens without putting its token on the browser's command line.
- Command output is decoded as UTF-8 first. Redirected terminal output is UTF-8 on Windows.
- The evaluation interval resamples attempts as well as tasks, checks end their process tree at the timeout, and the example manifest is a task that runs.

### Removed

- The `team` extra and its LangGraph dependency.
- `turnloom demo`, which folded a hard-coded string. `turnloom doctor` is the offline check.
- The second, auto-approving command line in `python -m turnloom.agent`.
- Benchmark-specific rules from the default system prompt. The evaluation runner adds them to its own sessions.
- Thirteen provider presets that need a vendor-specific client or a token exchange.

### Upgrading from 0.5

- Scripts that used `turnloom run` without a terminal must pass `--read-only`, `--auto-edit` or `--yes`.
- `HARNESS_ENDPOINT`, `HARNESS_MODEL` and `HARNESS_API_KEY` still work. Prefer the `TURNLOOM_` names.
- `pip install "turnloom[team]"` no longer exists; `turnloom team` works with the base install.
- Code that builds a `Config` directly: `approve` now receives `(kind, detail)`, and unattended callers set `auto_approve=True`.

## 0.5.0 and earlier

Development releases that were not published: the terminal agent with shell and recall tools, provider connections with OpenRouter sign-in, MCP stdio servers, Agent Skills loading, the evaluation tools, and the first version of Studio.
