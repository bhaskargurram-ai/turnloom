## Change

What a user can now do, or what no longer goes wrong. Link the issue: `Closes #`.

## How it was checked

The commands you ran and what they showed. For changes to prompts, folding or cost accounting, include the control and candidate measurements.

## Checklist

- [ ] A test fails without this change
- [ ] `python -m unittest discover -s tests` and `ruff check .` pass
- [ ] Docs and CHANGELOG.md updated where behavior or a flag changed
- [ ] `turnloom/studio_assets/` rebuilt if anything under `studio/` changed
- [ ] No credentials or private code in any trace attached
