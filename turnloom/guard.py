"""Gate every tool call through Provio's policy and record it in Provio's ledger.

Provio (https://github.com/writ-agent/provio) checks a pending tool call against
``provio.yaml`` and answers allow, deny, ask or redact. Turnloom asks it before a
shell command, a workspace read or an MCP tool call runs:

* deny   the tool does not run and the model reads the rule and its reason
* ask    the operator decides, seeing the rule that asked
* allow  the call runs; in ``confirm`` mode the operator is still asked first
* redact the call runs and the model receives Provio's redacted output

The gate fails closed. If Provio cannot answer, the tool does not run.
"""

import json
import shutil
import threading
import subprocess
import sys
import sysconfig
from importlib.util import find_spec
from pathlib import Path

STARTER = """# Provio policy for this workspace. https://github.com/writ-agent/provio
# Unmatched calls are allowed. The floor pack refuses disasters and asks before
# irreversible commands. The secrets guard keeps keys and credentials away from the agent.
version: 1
default: allow
packs: [floor, secrets-guard]
"""
POLICY = "provio.yaml"
LEDGER = Path(".provio") / "ledger.jsonl"


def installed():
    return find_spec("provio_sdk") is not None and binary() is not None


def binary():
    name = "provio.exe" if sys.platform == "win32" else "provio"
    bundled = Path(getattr(sys, "_MEIPASS", "")) / name if getattr(sys, "frozen", False) else None
    if bundled and bundled.is_file():
        return str(bundled)
    return shutil.which("provio", path=sysconfig.get_path("scripts")) or shutil.which("provio")


def policy_path(workspace):
    return Path(workspace) / POLICY


def cli(workspace, *arguments, timeout=20):
    """Run the provio binary in a workspace. Returns (exit code, stdout, stderr)."""
    executable = binary()
    if not executable:
        raise ValueError('Provio is not installed. Run: python -m pip install "turnloom[guard]"')
    result = subprocess.run([executable, *arguments], cwd=workspace, capture_output=True, timeout=timeout,
                            stdin=subprocess.DEVNULL, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    return result.returncode, result.stdout.decode("utf-8", "replace"), result.stderr.decode("utf-8", "replace")


def create_policy(workspace):
    """Write the starter policy. An existing policy is never replaced."""
    target = policy_path(workspace)
    if target.exists():
        raise ValueError("This workspace already has a provio.yaml.")
    target.write_text(STARTER, encoding="utf-8", newline="\n")
    return target


def test(workspace, tool, value):
    """Judge one call without running or recording it: a shell command, a file path or a URL."""
    option = {"bash": [], "fs.read": ["--tool", "fs.read", "--path"], "fs.write": ["--tool", "fs.write", "--path"],
              "http": ["--tool", "http", "--url"]}.get(tool)
    if option is None or not isinstance(value, str) or not value.strip() or len(value) > 4000:
        raise ValueError("Enter a command, path or URL to test.")
    code, output, error = cli(workspace, "test", "--json", *option, *([value] if option else ["--", value]))
    try:
        verdict = json.loads(output)["verdict"]
    except (ValueError, KeyError, TypeError):
        raise ValueError((error or output or "Provio returned no verdict.").strip()[:600]) from None
    return {"kind": verdict.get("kind"), "rule": verdict.get("rule_id"), "location": verdict.get("location"),
            "reason": verdict.get("reason") or verdict.get("diff"), "irreversible": bool(verdict.get("irreversible"))}


def ledger(workspace, limit=300):
    """Recent decisions from the ledger file, newest first, each joined with its execution record."""
    path = Path(workspace) / LEDGER
    if not path.is_file():
        return {"records": [], "counts": {}, "total": 0}
    decisions, outcomes, total = [], {}, 0
    with path.open(encoding="utf-8") as source:
        for line in source:
            try:
                record = json.loads(line)
            except ValueError:
                continue
            total += 1
            if record.get("kind") == "decision":
                decisions.append(record)
            elif record.get("kind") == "execution":
                outcomes[record.get("call_id")] = record
    counts, rows = {}, []
    for record in decisions:
        kind = (record.get("verdict") or {}).get("kind") or "unknown"
        counts[kind] = counts.get(kind, 0) + 1
    for record in reversed(decisions[-limit:]):
        call, verdict = record.get("call") or {}, record.get("verdict") or {}
        arguments = call.get("args") or {}
        outcome = outcomes.get(record.get("call_id"))
        rows.append({"index": record.get("index"), "time": record.get("recorded_at"), "session": record.get("session_id"),
                     "tool": call.get("tool"), "server": (call.get("server") or {}).get("name"),
                     "detail": str(arguments.get("command") or arguments.get("path") or arguments.get("url")
                                   or json.dumps(arguments, ensure_ascii=False))[:300],
                     "verdict": verdict.get("kind"), "rule": record.get("rule_id"),
                     "reason": verdict.get("reason") or verdict.get("diff"), "approver": record.get("approver"),
                     "ran": outcome is not None, "exit": outcome.get("exit_status") if outcome else None})
    return {"records": rows, "counts": counts, "total": total}


def verify(workspace):
    """Check the ledger's hash chain. Returns whether it is intact and Provio's own summary line."""
    if not (Path(workspace) / LEDGER).is_file():
        return {"intact": True, "summary": "No ledger yet."}
    code, output, error = cli(workspace, "verify")
    return {"intact": code == 0, "summary": (output or error).strip().splitlines()[-1][:300] if (output or error).strip() else ""}


class Gate:
    """One Provio gateway process for a workspace.

    ``approve`` asks the operator and returns a bool. ``confirm`` keeps the operator in the loop
    for allowed calls too. ``notify`` receives the verdict just before the operator is asked.
    """

    def __init__(self, workspace, approve=None, confirm=True, session=None, notify=None):
        try:
            from provio_sdk import Caller, Provio
        except ImportError as error:
            raise ValueError('Provio is not installed. Run: python -m pip install "turnloom[guard]"') from error
        from turnloom import __version__
        if not policy_path(workspace).is_file():
            raise ValueError("This workspace has no provio.yaml. Create one in Studio's Policy view or with provio init.")
        self.approve, self.confirm, self.notify = approve, confirm, notify
        self.local = threading.local()
        self.provio = Provio(binary=binary(), policy=str(policy_path(workspace)), ledger=str(Path(workspace) / LEDGER),
                             cwd=str(workspace), session_id=session, approver=self.asked if approve else None,
                             approver_id="human:turnloom-operator", caller=Caller(agent="turnloom", agent_version=__version__))

    def close(self):
        self.provio.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    @staticmethod
    def summary(decision):
        return {"kind": decision.decision, "rule": decision.rule_id, "reason": decision.reason,
                "location": decision.location, "irreversible": decision.irreversible}

    def asked(self, request):
        """Provio's ask verdict: put the call and the rule that asked in front of the operator."""
        from provio_sdk import Approval
        self.local.asked = True
        if self.notify:
            self.notify(self.summary(request.decision))
        arguments = request.call.args
        text = arguments.get("command") or f"{request.call.tool} {json.dumps(dict(arguments), ensure_ascii=False)}"
        return Approval(bool(self.approve(str(text))), approver="human:turnloom-operator")

    def run(self, tool, arguments, execute, server=None):
        """Decide, then run ``execute(approve)`` and record it. Returns the tool's JSON result text.

        ``execute`` receives the approval callback it should use: None when the policy or the
        operator has already settled the question, the operator prompt when confirmation is on.
        """
        from provio_sdk import ProvioApprovalRejected, ProvioError, Server
        from provio_sdk.guard import refusal_text
        self.local.asked = False

        def guarded():
            # Reaching here means allow, redact, or an ask the operator granted.
            return execute(None if self.local.asked or not self.confirm else self.approve)

        try:
            result = self.provio.execute(tool, arguments, guarded,
                                         server=Server(name=server, transport="stdio") if server else None)
            try:
                json.loads(result)
            except ValueError:
                # Redaction works on text and can cut through the JSON a tool returned.
                result = json.dumps({"output": result, "redacted_by": "provio"}, ensure_ascii=False)
            return result
        except ProvioError as error:
            decision = getattr(error, "decision", None)
            return json.dumps({"error": refusal_text(error), "blocked_by": "provio",
                               "rule": decision.rule_id if decision else None,
                               "declined": isinstance(error, ProvioApprovalRejected) and bool(self.approve)}, ensure_ascii=False)
