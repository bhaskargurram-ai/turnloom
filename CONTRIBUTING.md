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
| Terminal and chat | `turnloom/cli.py` |
| Tool calls and model requests | `turnloom/agent.py` |
| Observation folding and recall | `turnloom/context.py` |
| Specialist instructions | `turnloom/personas/` |
| Experiment scoring | `turnloom/evaluate.py`, `turnloom/compare.py` |

For prompt or cost changes, include the control settings, sample size, model, and measured tradeoff. Add regression coverage for changed behavior. Preserve upstream attribution when updating a persona. Remove credentials and private task data from any shared trace.
