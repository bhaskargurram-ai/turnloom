"""Turn the result of an agent run in CI into a comment that is safe to post under the workflow's name.

The agent reads text written by strangers: an issue, a pull request description, a diff. Its answer may
therefore carry whatever that text talked it into. Before the answer is posted, this script removes what
a comment could be misused for: mentions that notify people, markup, images and links that lead away
from the repository. It also reads the labels a triage run proposes and keeps only the ones allowed.
"""

import argparse
import json
import re
import sys
from pathlib import Path

LIMIT = 6000
CODE = re.compile(r"(```.*?```|~~~.*?~~~|`[^`\n]+`)", re.S)
LINK = re.compile(r"(!?)\[([^\]\n]*)\]\(\s*<?([^)\s>]+)>?[^)\n]*\)")
ADDRESS = re.compile(r"(?<![`\w])(?:https?://|www\.)[^\s<>()\[\]`]+")
MARKER = "<!-- turnloom-agent:{kind} -->"
SOURCES = {"review": "this pull request", "triage": "this issue", "attempt": "this issue"}
LIMITS = {"budget_limit": "its spending limit", "step_limit": "its limit on model calls", "timeout": "its time limit"}


def calm(text, repository, limit=LIMIT):
    """Return the answer with mentions, markup and outside links made inert. Code spans are left as written."""
    home = f"https://github.com/{repository}".lower()

    def link(match):
        image, label, target = match.groups()
        if not image and (target.lower() == home or target.lower().startswith(home + "/") or target.startswith("#")):
            return match.group(0)
        return f"{label} (`{target}`)" if label else f"`{target}`"

    def address(match):
        found = match.group(0)
        trailing = found[len(found.rstrip(".,;:!?")):]
        found = found[:len(found) - len(trailing)] if trailing else found
        inside = found.lower() == home or found.lower().startswith(home + "/")
        return (found if inside else f"`{found}`") + trailing

    def prose(part):
        part = LINK.sub(link, part)
        part = "".join(piece if index % 2 else ADDRESS.sub(address, piece) for index, piece in enumerate(CODE.split(part)))
        part = part.replace("<", "&lt;")
        return re.sub(r"@(?=[A-Za-z0-9])", "@​", part)

    text = text.replace("\r\n", "\n").strip()
    if len(text) > limit:
        text = text[:limit].rsplit("\n", 1)[0] + "\n\n(The rest of the answer was cut for length.)"
    if text.count("```") % 2:
        text += "\n```"
    return "".join(piece if index % 2 else prose(piece) for index, piece in enumerate(CODE.split(text)))


def proposed(answer, allowed):
    """Split a triage answer into the allowed labels it names on its first line and the comment that follows."""
    first, _, rest = answer.strip().partition("\n")
    match = re.match(r"\s*\**labels\**\s*:\**(.*)", first, re.I)
    if not match:
        return [], answer.strip()
    known = {name.lower(): name for name in allowed}
    named = [known[item.strip(" `*").lower()] for item in match.group(1).split(",") if item.strip(" `*").lower() in known]
    return list(dict.fromkeys(named)), rest.strip()


def excerpt(log, lines=40, limit=3000):
    """Return the end of a check log as a code block that its contents cannot close."""
    tail = "\n".join(log.replace("\r\n", "\n").replace("`", "'").strip().splitlines()[-lines:])[-limit:]
    return f"```text\n{tail}\n```" if tail else ""


def combined(results):
    """Fold the results of several passes into one: the last pass's answer and status, everybody's usage."""
    usage = {name: sum((item.get("usage") or {}).get(name, 0) for item in results) for name in ("prompt_tokens", "completion_tokens")}
    return {**results[-1], "steps": sum(item.get("steps", 0) for item in results), "usage": usage}


def comment(kind, result, repository, run_url="", note="", log=""):
    """Build the comment for one run: the calmed answer, an optional note and log, and a footer saying what wrote it."""
    usage = result.get("usage") or {}
    spent = (f"{result.get('steps', 0)} model calls, {usage.get('prompt_tokens', 0):,} input and "
             f"{usage.get('completion_tokens', 0):,} output tokens")
    run = f"[this run]({run_url})" if run_url else "a workflow run"
    means = {"review": "without running the code", "triage": "without running any code",
             "attempt": "editing files without being able to run anything itself"}[kind]
    footer = (f"Written by [Turnloom](https://github.com/{repository}) from {SOURCES[kind]} in {run}, {means}: {spent}. "
              "It is a suggestion for the people here, not a decision by the maintainers.")
    body = calm(result.get("answer") or "", repository)
    if result.get("status") in LIMITS:
        body += f"\n\nThe run stopped at {LIMITS[result['status']]}, so this may be incomplete."
    parts = [MARKER.format(kind=kind), body, note.strip(), excerpt(log), "---", f"<sub>{footer}</sub>"]
    return "\n\n".join(part for part in parts if part) + "\n"


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("kind", choices=sorted(SOURCES))
    parser.add_argument("--result", type=Path, required=True, nargs="+",
                        help="The JSON result file the action wrote; several for several passes, in order")
    parser.add_argument("--repository", required=True, help="owner/name")
    parser.add_argument("--out", type=Path, required=True, help="Where to write the comment")
    parser.add_argument("--run-url", default="")
    parser.add_argument("--note", default="", help="Trusted text added after the answer, such as where a branch was pushed")
    parser.add_argument("--log", type=Path, help="Output of a check on the agent's edits; its end is shown as a code block")
    parser.add_argument("--allowed", default="", help="Comma-separated labels a triage run may apply")
    parser.add_argument("--labels-out", type=Path, help="Where to write the labels to apply, one per line")
    args = parser.parse_args()
    try:
        result = combined([json.loads(path.read_text(encoding="utf-8")) for path in args.result])
        log = args.log.read_text(encoding="utf-8", errors="replace") if args.log else ""
    except (OSError, ValueError) as error:
        print(f"::error::The agent's result could not be read: {error}")
        return 1
    if not (result.get("answer") or "").strip():
        print(f"::notice::The agent gave no answer (status {result.get('status')}).")
        if not args.note.strip():
            return 3
        result["answer"] = "The agent finished without a report."
    labels = []
    if args.kind == "triage":
        labels, result["answer"] = proposed(result["answer"], [item.strip() for item in args.allowed.split(",") if item.strip()])
    if args.labels_out:
        args.labels_out.write_text("".join(label + "\n" for label in labels), encoding="utf-8", newline="\n")
    args.out.write_text(comment(args.kind, result, args.repository, args.run_url, args.note, log), encoding="utf-8", newline="\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
