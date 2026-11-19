# Contributing

Turnloom is a small project that wants contributors. This page takes you from a first look to a merged pull request.

Security problems do not go through issues or pull requests. Report them privately as [SECURITY.md](SECURITY.md) describes.

## Ways to help

| You have | You can |
| --- | --- |
| Ten minutes | Try the [quick start](docs/quickstart.md) and open an issue for anything that was unclear or broken. Reports from a first run are the most useful ones. |
| A provider key, an editor or a machine the maintainer lacks | Pick a "help wanted" item from the [roadmap](ROADMAP.md): confirm a provider preset, run `turnloom acp` in your editor, build the desktop app on macOS. |
| An afternoon | Take a [good first issue](https://github.com/bhaskargurram-ai/turnloom/issues?q=is%3Aissue+is%3Aopen+label%3A%22good+first+issue%22). Comment on it so nobody doubles up. |
| An idea | Start a thread in [Discussions](https://github.com/bhaskargurram-ai/turnloom/discussions). For anything larger than a fix, agree the direction in an issue before writing code. |

Documentation fixes, tests for behavior that has none, and bug reports with a small reproduction are all contributions.

## Set up

You need Python 3.11 or newer and Git. Node 22 is needed only if you change Studio's front end.

```sh
git clone https://github.com/YOUR-NAME/turnloom      # your fork
cd turnloom
python -m venv .venv
.venv/bin/python -m pip install -e ".[full]" ruff     # Windows: .venv\Scripts\python
.venv/bin/python -m unittest discover -s tests        # about a minute
.venv/bin/python -m ruff check .
```

The suite needs no network and no model: tests script the model's replies. [AGENTS.md](AGENTS.md) maps the code and lists the rules a change has to follow. It is written for coding agents and is the quickest orientation for people too.

To try your checkout against a real model, connect one with `turnloom connect` and run `turnloom chat` in a scratch folder. `turnloom doctor` shows which optional features are installed.

## Your first pull request

1. **Branch.** `git switch -c short-description` from an up-to-date `main`.
2. **Write the test first.** A behavior change needs a test that fails without it. Tests live in `tests/`, use `unittest`, and script the model by patching `turnloom.agent.api_request` or by running a local HTTP server. Find the nearest existing test and copy its shape.
3. **Make the change.** Keep it to one thing. The rules that most often matter:
   - The core imports only the standard library and `keyring`. An optional feature imports its package lazily and raises a `ValueError` naming the extra when it is missing.
   - Anything that touches a path goes through `Workspace.resolve`. Anything that runs a command goes through `agent.execute`.
   - A tool with side effects passes the policy gate and the approval gate.
   - Text shown to people says what happened and what to do next.
   - Windows is a first-class platform: no `/bin/sh` assumptions, no forward slashes in output, no assuming a UTF-8 console.
4. **Check it.** Run the suite and `ruff check .`. Update the docs if behavior or a flag changed, and add a line to [CHANGELOG.md](CHANGELOG.md) under the next version.
5. **Commit.** One or a few commits whose subject says what changes for the user, in the imperative: "Remember the reply-limit parameter per endpoint", not "fix bug". Explain the why in the body when it is not obvious.
6. **Open the pull request.** Push your branch and open it against `main`. The template asks what changed and how you checked it. Link the issue it closes.

A draft pull request is welcome when you want feedback before finishing.

## What happens next

Continuous integration runs on every pull request:

| Job | Checks |
| --- | --- |
| `python` | Lint and the suite on Linux, Windows and macOS, on Python 3.11 and 3.14 |
| `integrations` | The suite with every optional extra, and the MCP reference servers |
| `minimal` | The core with no extras installed |
| `package` | The wheel and source archive build, pass `twine check`, and install cleanly |
| `studio` | The committed Studio bundle matches a fresh build, and the browser test passes |
| `image` | The container image builds and runs |

A maintainer reviews when the checks are green, usually within a week. Review comments are about the change, never about you. Expect questions about the test and about edge cases on Windows. Small follow-up commits are fine; the pull request is squashed when it merges. If a week passes with no response, a comment on the pull request is the right nudge.

## Studio

The front end is React, bundled with esbuild into `turnloom/studio_assets/`. After changing anything under `studio/`:

```sh
cd studio
npm ci
npm run build                      # writes ../turnloom/studio_assets
npx playwright install chromium    # once
PYTHON=../.venv/bin/python npm run test:browser
```

Commit the rebuilt assets with your source change; continuous integration rebuilds and compares. The browser test drives a real Studio server with a scripted model through every view. Set `BROWSER_CHANNEL=chrome` to use an installed Chrome. The Documents and Policy steps run when the `documents` and `guard` extras are installed and are skipped otherwise.

The standalone desktop app is built with `packaging/build.ps1` or the steps in [docs/desktop.md](docs/desktop.md).

## Changes to prompts, folding or cost accounting

These change how the agent behaves on every task, so a pull request needs evidence: the control settings, the model, the sample size and the measured difference. `turnloom-eval` and `turnloom-compare` exist for this; see [docs/experiments.md](docs/experiments.md). The scripts under [benchmarks/](benchmarks/README.md) compare Turnloom with other agents on Terminal-Bench tasks.

Never attach a trace that contains credentials or private code. `.turnloom/*.jsonl` holds file contents and command output.

## Reporting a bug

Use the bug report form. It asks for what you ran, what you expected, what you saw, the smallest steps that reproduce it, and the output of `turnloom doctor`, which contains no keys. The exact error text matters more than a description of it.

## Vendored material

Personas under `turnloom/personas/` come from [Agency Agents](https://github.com/msitarzewski/agency-agents). Change them upstream and refresh with `scripts/sync_personas.py`, which keeps the license and records the commit. Provider presets in `turnloom/data/providers.json` derive from [Models.dev](https://models.dev); fix a wrong preset there.

## Conduct and licensing

Everyone taking part follows the [code of conduct](CODE_OF_CONDUCT.md). By contributing you agree that your contribution is released under the project's [MIT License](LICENSE).

## Releasing (maintainers)

1. Update `__version__` in `turnloom/__init__.py` and the changelog.
2. Tag `vX.Y.Z` and push the tag.
3. The release workflow builds the wheel and source archive, installs the wheel in a clean environment, attaches the files to a GitHub release and pushes the container image to GHCR.
4. Publish to PyPI. With trusted publishing configured (see the comment in `.github/workflows/release.yml`) the workflow does it. Otherwise download the release files and upload exactly those: `gh release download vX.Y.Z -p "turnloom-*" -D dist && twine upload dist/*`.
