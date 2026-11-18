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
| `turnloom/providers.py`, `openrouter.py` | Provider presets, model discovery, credentials, sign-in |
| `turnloom/extensions.py`, `server.py` | Skills and the MCP client; Turnloom as an MCP server |
| `turnloom/evaluate.py`, `compare.py`, `team.py` | Evaluation runner, comparison, the three-stage run |
| `studio/` | Studio's React source. `npm run build` writes `turnloom/studio_assets/` |
| `tests/` | `unittest` suites. No network; the model is always scripted |

## Commands

```sh
python -m pip install -e ".[mcp,skills,search]"
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
- Commands, edits and MCP calls pass the approval gate (`agent.permitted`). A new tool with side effects must too.
- Text shown to people is plain and specific: say what happened and what to do next. No tracebacks for expected failures.
- Windows is a first-class platform. Do not assume `/bin/sh`, forward slashes in output, or UTF-8 consoles.
- Commit the rebuilt `turnloom/studio_assets/` together with any change under `studio/`. Continuous integration rebuilds and compares.
- Do not edit `turnloom/personas/*.md` or `turnloom/data/providers.json` by hand without keeping their upstream attribution accurate.
