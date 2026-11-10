"""The persona roster: two native roles, the vendored Agency Agents catalog, and workspace personas."""

import json
import re
from functools import cache
from importlib.resources import files
from pathlib import Path

NATIVE = [
    {"id": "builder", "name": "Builder", "division": "turnloom", "emoji": "🛠️", "color": "mint",
     "description": "Implements the smallest correct change and verifies it with a focused check.",
     "vibe": "Reads before editing. Runs the test before saying done.", "file": None},
    {"id": "debugger", "name": "Debugger", "division": "turnloom", "emoji": "🔬", "color": "orange",
     "description": "Reproduces the symptom, isolates the first failing boundary, then verifies the fix.",
     "vibe": "No fix without a reproduction.", "file": None},
]
DEBUGGER = "Reproduce the symptom, isolate its first failing boundary, then verify the fix against the reproduction."
ALIASES = {"reviewer": "engineering-code-reviewer", "architect": "engineering-software-architect"}
FEATURED = ["builder", "debugger", "engineering-code-reviewer", "engineering-software-architect",
            "security-appsec-engineer", "engineering-senior-developer", "engineering-frontend-developer",
            "engineering-backend-architect", "engineering-devops-automator", "engineering-technical-writer",
            "testing-reality-checker", "engineering-rapid-prototyper"]


@cache
def upstream():
    data = json.loads(files("turnloom").joinpath("personas", "catalog.json").read_text(encoding="utf-8"))
    return data


def front_matter(text):
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---" or "---" not in [line.strip() for line in lines[1:]]:
        return {}, text
    end = next(index for index, line in enumerate(lines[1:], 1) if line.strip() == "---")
    meta = {}
    for line in lines[1:end]:
        key, found, value = line.partition(":")
        if found and key.strip() and not key.startswith((" ", "\t")):
            meta[key.strip()] = value.strip().strip("\"'")
    return meta, "\n".join(lines[end + 1:]).strip()


def workspace_personas(workspace):
    """Markdown files in <workspace>/.turnloom/personas become selectable roles."""
    folder = Path(workspace) / ".turnloom" / "personas" if workspace else None
    rows = []
    if folder and folder.is_dir():
        for path in sorted(folder.glob("*.md"))[:200]:
            if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", path.stem) or path.stat().st_size > 200_000:
                continue
            meta, _ = front_matter(path.read_text(encoding="utf-8-sig"))
            rows.append({"id": "custom-" + path.stem, "name": meta.get("name") or path.stem.replace("-", " ").title(),
                         "description": meta.get("description") or "Workspace persona", "division": "custom",
                         "emoji": meta.get("emoji", "✨"), "color": meta.get("color", ""), "vibe": meta.get("vibe", ""),
                         "file": str(path)})
    return rows


def catalog(workspace=None):
    order = {key: index for index, key in enumerate(FEATURED)}
    rows = NATIVE + workspace_personas(workspace) + upstream()["personas"]
    return sorted(rows, key=lambda row: (order.get(row["id"], len(order)), row["division"] != "custom"))


def find(role, workspace=None):
    role = ALIASES.get(role, role)
    for row in NATIVE + upstream()["personas"]:
        if row["id"] == role:
            return row
    for row in workspace_personas(workspace):
        if row["id"] == role:
            return row
    raise ValueError(f"Unknown persona: {role}. Run turnloom personas to list them.")


def body(role, workspace=None):
    """The instructions a persona adds to the base prompt, or an empty string for Builder."""
    row = find(role, workspace)
    if row["id"] == "debugger":
        return DEBUGGER
    if not row["file"]:
        return ""
    if row["division"] == "custom":
        raw = Path(row["file"]).read_text(encoding="utf-8-sig")
    else:
        raw = files("turnloom").joinpath("personas", *row["file"].split("/")).read_text(encoding="utf-8")
    return front_matter(raw)[1]
