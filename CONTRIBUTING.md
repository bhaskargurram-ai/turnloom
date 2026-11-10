# Contributing

Start with a small reproduction and a focused change. Run the offline demo and test suite from the repository root.

The [CI template](docs/ci/test.yml) runs these checks on Windows and Linux. To enable it, copy it to `.github/workflows/test.yml` using an account authorized to write workflows.

```sh
python -m pip install -e .
python -m unittest discover -s tests -v
turnloom demo
```

| Area | Entry point |
| --- | --- |
| Studio interface and Context Loom | `studio/App.jsx`, `studio/studio.css`, `studio/loom.css` |
| Local Studio server and editor API | `turnloom/studio.py` |
| Terminal and chat | `turnloom/cli.py` |
| Tool calls and model requests | `turnloom/agent.py` |
| Observation folding and recall | `turnloom/context.py` |
| Specialist instructions | `turnloom/personas/` |
| Experiment scoring | `turnloom/evaluate.py`, `turnloom/compare.py` |

For prompt or cost changes, include the control settings, sample size, model, and measured tradeoff. Add regression coverage for changed behavior. Preserve upstream attribution when updating a persona. Remove credentials and private task data from any shared trace.

To work on Studio, run `npm ci` and `npm run build` from the `studio` directory. Commit the rebuilt files in `turnloom/studio_assets` with the source change. The wheel ships these assets and their third-party notices so users only need Python. Launch `turnloom-studio --workspace .` from the repository root to inspect the result.

For Studio release checks, install `python -m pip install -e ".[studio,full]"` before running the Python suite. The Studio integration test starts Unwind and VerifyDoc in a disposable workspace and calls the extraction tool over MCP. From `studio/`, run `npx playwright install chromium` followed by `npm run test:browser`. Set `PYTHON` to the virtual environment interpreter if necessary. Set `BROWSER_CHANNEL=chrome` to use locally installed Chrome. The browser test uses a scripted model and exercises real workspace edits, approvals, cancellation and archive recall.
