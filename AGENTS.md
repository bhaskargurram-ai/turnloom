# Working on Turnloom

Turnloom is a small coding agent: one loop, a context store and a handful of tools. Keep it small. A change that adds a dependency to the core or a second way to do something needs a strong reason.

## Layout

| Path | Role |
| --- | --- |
| `turnloom/agent.py` | The loop: model requests, streaming, retries, tool dispatch, context fitting, the transcript |
| `turnloom/context.py` | `Observations`: fold, archive, recall |
| `turnloom/tools.py` | Workspace file tools, path confinement, edits and undo |
| `turnloom/cli.py` | The `turnloom` command, approvals, sessions, receipts |
| `turnloom/studio.py` | The local web server behind Studio |
| `turnloom/desktop.py`, `packaging/` | The native window and the standalone app build |
| `turnloom/providers.py`, `openrouter.py`, `gateway.py` | Provider presets, model discovery, credentials, sign-in, LiteLLM routes |
| `turnloom/extensions.py`, `server.py`, `acp.py` | Skills and the MCP client; Turnloom as an MCP server; Turnloom as an ACP agent |
| `turnloom/guard.py` | The Provio policy gate |
| `turnloom/meter.py`, `proxy.py` | Token counting and the usage ledger; the folding proxy for other harnesses |
| `turnloom/roster.py`, `personas/` | Native and vendored personas. `scripts/sync_personas.py` refreshes the vendored set |
| `turnloom/evaluate.py`, `compare.py`, `team.py`, `atif.py` | Evaluation runner, comparison, staged runs, ATIF export |
| `integrations/` | The Harbor adapter and patches offered upstream to Provio and Unwind |
| `studio/` | Studio's React source. `npm run build` writes `turnloom/studio_assets/` |
| `tests/` | `unittest` suites. No network; the model is always scripted |

## Commands

```sh
python -m pip install -e ".[full]"            # add ,documents for the VerifyDoc and Unwind tests
python -m unittest discover -s tests          # the whole suite, about a minute
python -m unittest tests.test_agent -v        # one module
ruff check .
cd studio && npm ci && npm run build          # after changing anything under studio/
```

## Rules

- The core imports only the standard library and `keyring`. Optional features import their package lazily and raise a `ValueError` naming the extra when it is missing.
- Every behavior change comes with a test that fails without it. Tests script the model by patching `turnloom.agent.api_request` or by running a local HTTP server; they never call a real provider.
- Anything that touches a path goes through `Workspace.resolve`. Do not join user or model input to the workspace root yourself.
- Anything that runs a command goes through `agent.execute`, so timeouts and stops end the whole process tree.
- Commands, edits and MCP calls pass the policy gate and then the approval gate (`agent.gated`, `agent.permitted`). A new tool with side effects must too.
- Text shown to people is plain and specific: say what happened and what to do next. No tracebacks for expected failures.
- Windows is a first-class platform. Do not assume `/bin/sh`, forward slashes in output, or UTF-8 consoles.
- Commit the rebuilt `turnloom/studio_assets/` together with any change under `studio/`. Continuous integration rebuilds and compares.
- Personas under `turnloom/personas/` are vendored. Change them upstream and refresh with `scripts/sync_personas.py`, which keeps the license and records the commit. Keep `turnloom/data/providers.json` attributable to Models.dev.
