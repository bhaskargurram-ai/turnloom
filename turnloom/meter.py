"""Token counting, a per-workspace usage ledger and the numbers behind Studio's cost view."""

import json
import threading
import time
from collections import defaultdict
from functools import cache
from pathlib import Path


@cache
def _encoding():
    try:
        import tiktoken
        return tiktoken.get_encoding("o200k_base")
    except Exception:  # absent package, or no cached vocabulary while offline
        return None


def count(text):
    """Tokens in text. tiktoken's o200k vocabulary when installed, otherwise four characters per token."""
    if not text:
        return 0
    encoding = _encoding()
    if encoding is None or len(text) > 400_000:
        return len(text) // 4 + 1
    return len(encoding.encode(text, disallowed_special=()))


def exact():
    return _encoding() is not None


def text_of(message):
    content = message.get("content")
    if isinstance(content, list):
        content = " ".join(part.get("text", "") for part in content if isinstance(part, dict))
    parts = [content or ""]
    for call in message.get("tool_calls") or []:
        parts.append(json.dumps(call.get("function", {}), ensure_ascii=False))
    return "\n".join(parts)


def composition(messages):
    """Split the prompt a model would receive into the parts an operator can act on."""
    parts = {"system": 0, "user": 0, "assistant": 0, "tools": 0, "folded": 0}
    for message in messages:
        tokens = count(text_of(message)) + 4
        role = message.get("role")
        if role == "tool":
            content = message.get("content")
            folded = isinstance(content, str) and content.startswith('{"reference"')
            parts["folded" if folded else "tools"] += tokens
        elif role in parts:
            parts[role] += tokens
    return parts


def price(usage, input_rate, output_rate, cache_rate=None):
    """Estimated USD for one usage record. Cached prompt tokens bill at cache_rate when the route reports one."""
    cached = min(usage.get("cached_tokens", 0), usage.get("prompt_tokens", 0))
    fresh = usage.get("prompt_tokens", 0) - cached
    reading = input_rate if cache_rate is None else cache_rate
    return (fresh * input_rate + cached * reading + usage.get("completion_tokens", 0) * output_rate) / 1_000_000


class Ledger:
    """Append-only record of model calls for one workspace, kept beside its transcripts."""

    def __init__(self, workspace):
        self.path = Path(workspace) / ".turnloom" / "usage.jsonl"
        self.lock = threading.Lock()

    def add(self, **record):
        record = {"time": round(time.time(), 3), **record}
        with self.lock:
            from turnloom.agent import trace_folder
            trace_folder(self.path.parent)
            with self.path.open("a", encoding="utf-8") as output:
                output.write(json.dumps(record, ensure_ascii=False) + "\n")
        return record

    def rows(self, limit=20000):
        try:
            with self.lock, self.path.open(encoding="utf-8") as source:
                lines = source.readlines()[-limit:]
        except FileNotFoundError:
            return []
        rows = []
        for line in lines:
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if isinstance(row, dict) and isinstance(row.get("time"), (int, float)):
                rows.append(row)
        return rows

    def summary(self, days=14):
        rows = self.rows()
        totals = {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0, "cached_tokens": 0, "cost": 0.0,
                  "saved_tokens": 0, "saved_cost": 0.0, "unpriced_calls": 0}
        by_day, by_model, by_persona = defaultdict(lambda: defaultdict(float)), defaultdict(lambda: defaultdict(float)), defaultdict(lambda: defaultdict(float))
        horizon = time.time() - days * 86400
        for row in rows:
            numbers = {key: row.get(key) or 0 for key in ("prompt_tokens", "completion_tokens", "cached_tokens", "saved_tokens")}
            cost = row.get("cost")
            saved = row.get("saved_cost") or 0
            totals["calls"] += 1
            totals["unpriced_calls"] += cost is None
            totals["cost"] += cost or 0
            totals["saved_cost"] += saved
            for key, value in numbers.items():
                totals[key] += value
            groups = [(by_model, row.get("model") or "unknown"), (by_persona, row.get("persona") or "unknown")]
            if row["time"] >= horizon:
                groups.append((by_day, time.strftime("%Y-%m-%d", time.localtime(row["time"]))))
            for group, key in groups:
                group[key]["calls"] += 1
                group[key]["cost"] += cost or 0
                group[key]["tokens"] += numbers["prompt_tokens"] + numbers["completion_tokens"]
                group[key]["saved_tokens"] += numbers["saved_tokens"]
        def table(group, label, order):
            return sorted(({label: key, **{name: round(value, 6) for name, value in values.items()}}
                           for key, values in group.items()), key=order)
        return {"totals": {key: round(value, 6) for key, value in totals.items()},
                "days": table(by_day, "day", lambda item: item["day"]),
                "models": table(by_model, "model", lambda item: -item["tokens"])[:12],
                "personas": table(by_persona, "persona", lambda item: -item["tokens"])[:12]}
