# Provio: decision observer and Turnloom notes

`0001-on-decision-observer-and-turnloom-notes.patch` is a change for [Provio](https://github.com/writ-agent/provio), made against `main` at `d21705a`. It applies cleanly with `git apply`.

| Part | What it does |
| --- | --- |
| `Provio(on_decision=...)` in the Python SDK | Calls an observer with `(call, decision, approval)` for every call about to run. `approval` is `None` when the policy allowed the call and the approver's answer when a human granted an ask. Turnloom needs that distinction to decide whether its operator has already been asked, and today tracks it by wrapping the approver. The observer cannot change the outcome. |
| Three tests in `adapters/python/tests/test_guard.py` | Sync and async paths, blocked calls not reported, an observer that raises does not stop the tool. |
| `docs/integrations/turnloom.md`, README row, changelog entry | How Turnloom maps its tools onto Provio's vocabulary, what each verdict does, and the residual risks. |

Checked by running the SDK's own suite with the patch applied: 51 passed, 2 skipped, with the framework adapters' tests left out because those frameworks were not installed. The Rust workspace is untouched.

Nothing has been pushed. To propose it:

```sh
git clone https://github.com/writ-agent/provio && cd provio
git checkout -b sdk-on-decision
git apply /path/to/0001-on-decision-observer-and-turnloom-notes.patch
```

Turnloom works with the released `provio-sdk` 0.1.5 and does not depend on this patch.
