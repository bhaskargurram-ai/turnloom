"""Vendor Agency Agents personas from a pinned upstream checkout.

    git clone https://github.com/msitarzewski/agency-agents upstream
    git -C upstream checkout <commit>
    python scripts/sync_personas.py upstream

Copies every persona that carries `name` and `description` front matter into
turnloom/personas/<division>/ and writes catalog.json, which Studio and the CLI
read instead of parsing three hundred files at startup.
"""

import json
import shutil
import subprocess
import sys
from pathlib import Path

SKIP = {".github", "examples", "integrations", "scripts"}


def front_matter(text):
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return None
    meta = {}
    for line in lines[1:]:
        if line.strip() == "---":
            return meta
        key, found, value = line.partition(":")
        if found and key.strip() and not key.startswith((" ", "\t")):
            meta[key.strip()] = value.strip().strip("\"'")
    return None


def main(source):
    source = Path(source).resolve()
    target = Path(__file__).resolve().parent.parent / "turnloom" / "personas"
    commit = subprocess.run(["git", "-C", str(source), "rev-parse", "HEAD"], capture_output=True, text=True, check=True).stdout.strip()
    for old in target.iterdir():
        if old.is_dir():
            shutil.rmtree(old)
        elif old.suffix == ".md":
            old.unlink()
    catalog = []
    for path in sorted(source.rglob("*.md")):
        relative = path.relative_to(source)
        if len(relative.parts) < 2 or relative.parts[0] in SKIP:
            continue
        text = path.read_text(encoding="utf-8-sig")
        meta = front_matter(text)
        if not meta or not meta.get("name") or not meta.get("description"):
            continue
        division = relative.parts[0]
        destination = target / division / path.name
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(text, encoding="utf-8", newline="\n")
        catalog.append({"id": path.stem, "name": meta["name"], "description": meta["description"],
                        "division": division, "emoji": meta.get("emoji", ""), "color": meta.get("color", ""),
                        "vibe": meta.get("vibe", ""), "file": f"{division}/{path.name}"})
    shutil.copyfile(source / "LICENSE", target / "LICENSE")
    (target / "catalog.json").write_text(json.dumps({"source": "https://github.com/msitarzewski/agency-agents",
                                                     "commit": commit, "personas": catalog}, indent=1, ensure_ascii=False) + "\n",
                                         encoding="utf-8", newline="\n")
    print(f"Vendored {len(catalog)} personas from {commit[:12]} into {target}")


if __name__ == "__main__":
    main(*sys.argv[1:])
