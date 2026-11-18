# Contributing

Bug reports with a small reproduction and focused pull requests are welcome. For anything larger than a fix, open an issue first so the direction can be agreed before you write the code. Security problems go through the private channel in [SECURITY.md](SECURITY.md).

## Set up

```sh
git clone https://github.com/bhaskargurram-ai/turnloom
cd turnloom
python -m venv .venv
.venv/bin/python -m pip install -e ".[full]"      # Windows: .venv\Scripts\python
.venv/bin/python -m unittest discover -s tests
ruff check .
```

The suite needs no network and no model: tests script the model's replies. [AGENTS.md](AGENTS.md) maps the code and lists the rules a change has to follow; it is written for coding agents and is the quickest orientation for people too.

## Studio

The browser interface lives in `studio/` and is bundled into `turnloom/studio_assets/`, which the Python package ships so users never need Node.

```sh
cd studio
npm ci
npm run build                      # writes ../turnloom/studio_assets
npx playwright install chromium    # once
PYTHON=../.venv/bin/python npm run test:browser
```

Commit the rebuilt assets with your source change. The browser test drives a real Studio server with a scripted model through every view. Set `BROWSER_CHANNEL=chrome` to use an installed Chrome. The Documents and Policy steps run when the `documents` and `guard` extras are installed and are skipped otherwise.

The standalone desktop app is built with `packaging/build.ps1` or the steps in [docs/desktop.md](docs/desktop.md).

## What a pull request needs

- A test that fails without the change.
- For changes to prompts, folding or cost accounting: the control settings, model, sample size and the measured difference. `turnloom-eval` and `turnloom-compare` exist for this; see [docs/experiments.md](docs/experiments.md).
- Documentation updated in the same pull request when behavior or a flag changes, and a line in [CHANGELOG.md](CHANGELOG.md).
- No credentials or private task data in any trace you attach.

## Releasing

1. Update `__version__` in `turnloom/__init__.py` and the changelog.
2. Tag `vX.Y.Z` and push the tag.
3. The release workflow builds the wheel and source archive, installs the wheel in a clean environment, attaches the files to a GitHub release and pushes the container image to GHCR.
4. Publish to PyPI. With trusted publishing configured (see the comment in `.github/workflows/release.yml`) the workflow does it. Otherwise download the release files and upload exactly those: `gh release download vX.Y.Z -p "turnloom-*" -D dist && twine upload dist/*`.
