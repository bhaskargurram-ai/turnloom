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

    def fold(self, messages, keep=3, tool="recall"):
        """Replace long tool results older than the newest `keep` with a preview and a reference."""
        eligible = [item for item in messages if item.get("role") == "tool"]
        removed = 0
        for item in eligible[:-keep] if keep else eligible:
            content = item["content"]
            if isinstance(content, list) and all(isinstance(part, dict) and part.get("type") == "text" for part in content):
                content = "\n".join(part.get("text", "") for part in content)
            if not isinstance(content, str) or len(content) < 800:
                continue
            reference = self.save(content)
            if reference:
                # The preview has to stand on its own: a model that has not been told about
                # folding must still see that text is missing and how to read it.
                preview = json.dumps({"reference": reference, "preview": content[:180], "ending": content[-120:],
                                      "characters": len(content), "tool": tool,
                                      "note": f"Folded: the middle is omitted. Call {tool} with this reference to read any part."})
                item["content"] = preview
                removed += len(content) - len(preview)
        return removed

    def recall(self, arguments):
        try:
            query = json.loads(arguments)
            content = self.records[query["reference"]]
            start = max(0, int(query.get("offset", 0)))
            length = min(4000, max(1, int(query.get("length", 1600))))
            needle = query.get("search")
        except (ValueError, KeyError, TypeError):
            return json.dumps({"error": "Invalid or expired reference. Reinspect the workspace."})
        if isinstance(needle, str) and needle:
            # Reading a long result slice by slice costs a model call each. Search goes straight there.
            lowered, target, matches, position = content.casefold(), needle.casefold(), [], 0
            while len(matches) < 5 and (position := lowered.find(target, position)) != -1:
                left = content.rfind("\n", 0, max(0, position - 160)) + 1
                right = content.find("\n", position + len(needle) + 160)
                right = len(content) if right == -1 else right
                matches.append({"offset": position, "text": content[left:min(right, left + 700)]})
                position += len(target)
            return json.dumps({"matches": matches, "count": lowered.count(target), "total": len(content)})
        return json.dumps({"text": content[start:start + length], "total": len(content), "offset": start})
