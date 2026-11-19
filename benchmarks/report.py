"""Turn collected results into a Markdown table and an SVG figure.

    python report.py results.json --svg results.svg --markdown results.md
"""

import argparse
import json
import math
import statistics
from html import escape
from pathlib import Path

NAMES = {"turnloom": "Turnloom", "codex": "Codex CLI", "goose": "Goose", "opencode": "OpenCode", "openhands": "OpenHands SDK",
         "mini-swe-agent": "mini-SWE-agent", "terminus-2": "Terminus 2", "aider": "Aider"}
PAPER, INK, MUTED, RULE, ACCENT, REST = "#f4f2ec", "#1c1b18", "#4d4a43", "#d9d5c9", "#1f3fbf", "#8c887b"
SANS = "system-ui,'Segoe UI',Helvetica,Arial,sans-serif"


def wilson(solved, total, z=1.96):
    """95% score interval for a pass rate. With twenty tasks it is wide, and the table says so."""
    if not total:
        return 0.0, 0.0
    rate = solved / total
    centre = rate + z * z / (2 * total)
    spread = z * math.sqrt(rate * (1 - rate) / total + z * z / (4 * total * total))
    return max(0.0, (centre - spread) / (1 + z * z / total)), min(1.0, (centre + spread) / (1 + z * z / total))


def summarise(rows):
    agents = {}
    for row in rows:
        agents.setdefault(row["agent"], []).append(row)
    table = []
    for agent, trials in agents.items():
        solved = sum(1 for trial in trials if trial["reward"] == 1.0)
        usd = sum(trial.get("usd", 0) for trial in trials)
        times = [trial["agent_seconds"] for trial in trials if trial["agent_seconds"] is not None]
        low, high = wilson(solved, len(trials))
        table.append({
            "agent": agent, "name": NAMES.get(agent, agent), "version": next((t["version"] for t in trials if t.get("version")), ""),
            "tasks": len(trials), "solved": solved, "low": low, "high": high, "usd": usd, "usd_per_task": usd / len(trials),
            "usd_per_solved": usd / solved if solved else None,
            "input": sum(t.get("input", 0) for t in trials) / len(trials), "cached": sum(t.get("cached", 0) for t in trials) / len(trials),
            "output": sum(t.get("output", 0) for t in trials) / len(trials), "calls": sum(t.get("requests", 0) for t in trials) / len(trials),
            "seconds": statistics.median(times) if times else None,
            "errors": sum(1 for t in trials if t.get("error")), "refused": sum(t.get("refused", 0) for t in trials),
            "unmetered": sum(t.get("unmetered", 0) for t in trials)})
    return sorted(table, key=lambda item: (-item["solved"], item["usd_per_task"]))


def markdown(table):
    lines = ["| Agent | Version | Solved | 95% interval | Cost per task | Cost per solved task | Input tokens per task (cached) "
             "| Output tokens per task | Model calls per task | Median agent time |",
             "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |"]
    for row in table:
        solved = "n/a" if row["usd_per_solved"] is None else f"${row['usd_per_solved']:.3f}"
        took = "n/a" if row["seconds"] is None else f"{row['seconds']:.0f} s"
        name = f"**{row['name']}**" if row["agent"] == "turnloom" else row["name"]
        lines.append(f"| {name} | {row['version'] or ''} | {row['solved']} of {row['tasks']} | {row['low']:.0%} to {row['high']:.0%} "
                     f"| ${row['usd_per_task']:.3f} | {solved} | {row['input']:,.0f} ({row['cached']:,.0f}) | {row['output']:,.0f} "
                     f"| {row['calls']:.1f} | {took} |")
    return "\n".join(lines) + "\n"


def grid(rows, table):
    """One row per task, one column per agent in ranked order: pass, fail, or how the trial ended."""
    order = [row["agent"] for row in table]
    cells = {(row["agent"], row["task"]): row for row in rows}
    tasks = sorted({row["task"] for row in rows}, key=lambda task: (-sum(1 for agent in order if (cells.get((agent, task)) or {}).get("reward") == 1.0), task))
    lines = ["| Task | " + " | ".join(NAMES.get(agent, agent) for agent in order) + " | Solved by |", "| --- |" + " --- |" * (len(order) + 1)]
    for task in tasks:
        marks = []
        for agent in order:
            cell = cells.get((agent, task))
            if cell is None:
                marks.append("not run")
            elif cell["reward"] == 1.0:
                marks.append("pass")
            elif cell.get("error"):
                marks.append("time limit" if cell["error"] == "AgentTimeoutError" else "error")
            else:
                marks.append("fail")
        lines.append(f"| `{task}` | " + " | ".join(marks) + f" | {marks.count('pass')} of {len(order)} |")
    return "\n".join(lines) + "\n"


def bar(x, y, width, height, color):
    """A bar that is square at the baseline and rounded at the data end."""
    if width <= 0:
        return ""
    radius = min(4, width)
    return (f'<path d="M{x} {y}h{width - radius:.1f}a{radius} {radius} 0 0 1 {radius} {radius}v{height - 2 * radius}'
            f'a{radius} {radius} 0 0 1 -{radius} {radius}h-{width - radius:.1f}z" fill="{color}"/>')


def figure(table, model, title):
    width, row_height, top = 960, 34, 112
    height = top + row_height * len(table) + 70
    name_x, solved_x, solved_w, cost_x, cost_w = 32, 200, 300, 610, 230
    tasks = max(row["tasks"] for row in table)
    dearest = max(row["usd_per_task"] for row in table) or 1
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" role="img" aria-labelledby="t d" font-family="{SANS}">',
           f'<title id="t">{escape(title)}</title>',
           '<desc id="d">' + escape("; ".join(f"{row['name']} solved {row['solved']} of {row['tasks']} at ${row['usd_per_task']:.3f} per task"
                                             for row in table)) + "</desc>",
           f'<rect width="{width}" height="{height}" fill="{PAPER}"/>',
           f'<rect x="1" y="1" width="{width - 2}" height="{height - 2}" fill="none" stroke="{INK}" stroke-width="2"/>',
           f'<text x="{name_x}" y="44" font-size="22" font-weight="700" fill="{INK}">{escape(title)}</text>',
           f'<text x="{name_x}" y="68" font-size="14" fill="{MUTED}">{escape(model)}</text>',
           f'<text x="{solved_x}" y="{top - 12}" font-size="13" font-weight="600" fill="{INK}">Tasks solved, of {tasks}</text>',
           f'<text x="{cost_x}" y="{top - 12}" font-size="13" font-weight="600" fill="{INK}">Model cost per task, USD (lower is better)</text>',
           f'<path d="M{solved_x} {top - 4}v{row_height * len(table) + 4}M{cost_x} {top - 4}v{row_height * len(table) + 4}" stroke="{INK}" stroke-width="1"/>']
    for index, row in enumerate(table):
        y = top + index * row_height
        ours = row["agent"] == "turnloom"
        color, weight = (ACCENT, "700") if ours else (REST, "400")
        middle = y + row_height / 2
        out.append(f'<path d="M{name_x} {y + row_height}H{width - 32}" stroke="{RULE}" stroke-width="1"/>')
        out.append(f'<text x="{name_x}" y="{middle + 5}" font-size="15" font-weight="{weight}" fill="{INK}">{escape(row["name"])}</text>')
        length = solved_w * row["solved"] / tasks
        out.append(bar(solved_x, middle - 7, length, 14, color))
        out.append(f'<text x="{solved_x + length + 8}" y="{middle + 5}" font-size="14" font-weight="{weight}" fill="{INK}">{row["solved"]}</text>')
        length = cost_w * row["usd_per_task"] / dearest
        out.append(bar(cost_x, middle - 7, length, 14, color))
        out.append(f'<text x="{cost_x + length + 8}" y="{middle + 5}" font-size="14" font-weight="{weight}" fill="{INK}">${row["usd_per_task"]:.3f}</text>')
    foot = top + row_height * len(table) + 28
    out.append(f'<text x="{name_x}" y="{foot}" font-size="12.5" fill="{MUTED}">One attempt per task. Every agent used the same model and reasoning effort; '
               'tokens are the provider\'s own usage figures, priced at list rates.</text>')
    out.append(f'<text x="{name_x}" y="{foot + 18}" font-size="12.5" fill="{MUTED}">With {tasks} tasks, a difference of two or three solved is within '
               'chance. Method, per-task results and scripts: benchmarks/README.md</text>')
    out.append("</svg>")
    return "\n".join(part for part in out if part) + "\n"


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("results")
    parser.add_argument("--svg")
    parser.add_argument("--markdown")
    parser.add_argument("--grid")
    parser.add_argument("--model", default="Terminal-Bench 2.1, seeded sample of tasks · gpt-5.1, reasoning effort medium")
    parser.add_argument("--title", default="Eight coding agents, one model, the same tasks")
    options = parser.parse_args()
    rows = json.loads(Path(options.results).read_text(encoding="utf-8"))
    table = summarise(rows)
    text = markdown(table)
    print(text)
    if options.markdown:
        Path(options.markdown).write_text(text, encoding="utf-8", newline="\n")
    if options.grid:
        Path(options.grid).write_text(grid(rows, table), encoding="utf-8", newline="\n")
    if options.svg:
        Path(options.svg).write_text(figure(table, options.model, options.title), encoding="utf-8", newline="\n")


if __name__ == "__main__":
    main()
