"""Keep recent observations in context and make older ones available on demand."""

import hashlib
import json
from collections import OrderedDict


class Observations:
    def __init__(self, capacity=2_000_000):
        self.capacity = capacity
        self.records = OrderedDict()
        self.size = 0

    def save(self, content):
        reference = hashlib.sha256(content.encode()).hexdigest()[:16]
        if reference in self.records:
            self.records.move_to_end(reference)
            return reference
        if len(content) > self.capacity:
            return None
        self.records[reference] = content
        self.size += len(content)
        while self.size > self.capacity:
            _, evicted = self.records.popitem(last=False)
            self.size -= len(evicted)
        return reference

    def fold(self, messages, keep=3):
        eligible = [item for item in messages if item.get("role") == "tool"]
        removed = 0
        for item in eligible[:-keep]:
            content = item["content"]
            if len(content) < 600:
                continue
            reference = self.save(content)
            if reference:
                preview = json.dumps({"reference": reference, "preview": content[:180],
                                      "characters": len(content), "tool": "recall"})
                item["content"] = preview
                removed += len(content) - len(preview)
        return removed

    def recall(self, arguments):
        try:
            query = json.loads(arguments)
            content = self.records[query["reference"]]
            start = max(0, int(query.get("offset", 0)))
            length = min(4000, max(1, int(query.get("length", 1600))))
        except (ValueError, KeyError, TypeError):
            return json.dumps({"error": "Invalid or expired reference. Reinspect the workspace."})
        return json.dumps({"text": content[start:start + length], "total": len(content), "offset": start})
