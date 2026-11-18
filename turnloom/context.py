"""Keep recent observations in context and make older ones available on demand."""

import hashlib
import json
from collections import OrderedDict

FOLD_AT = 800


class Observations:
    def __init__(self, capacity=4_000_000):
        self.capacity = capacity
        self.records = OrderedDict()
        self.size = 0
        self.exchanges = []

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
        """Replace long tool results older than the newest `keep` with a preview and a reference.

        Results the model has not read yet (those after the latest assistant message) are never folded,
        so a batch of parallel calls reaches the model whole.
        """
        latest = max((index for index, item in enumerate(messages) if item.get("role") == "assistant"), default=None)
        tools = [(index, item) for index, item in enumerate(messages) if item.get("role") == "tool"]
        unread = [item for index, item in tools if latest is not None and index > latest]
        read = [item for index, item in tools if latest is None or index < latest]
        spare = max(0, keep - len(unread))
        removed = 0
        for item in read[:len(read) - spare] if spare else read:
            content = item["content"]
            if isinstance(content, list) and all(isinstance(part, dict) and part.get("type") == "text" for part in content):
                content = "\n".join(part.get("text", "") for part in content)
            if not isinstance(content, str) or len(content) < FOLD_AT:
                continue
            try:
                parsed = json.loads(content)
            except ValueError:
                parsed = None
            if isinstance(parsed, dict) and parsed.get("note") and parsed.get("tool") == tool \
                    and parsed.get("reference") in self.records:
                continue  # already a fold preview
            if isinstance(parsed, dict) and isinstance(parsed.get("reference"), str) and parsed["reference"] in self.records:
                # Already archived in full at capture: keep pointing at that record.
                reference = parsed["reference"]
                total = len(self.records[reference])
                self.records.move_to_end(reference)
            else:
                reference = self.save(content)
                total = len(content)
            if reference:
                # The preview has to stand on its own: a model that has not been told about
                # folding must still see that text is missing and how to read it.
                preview = json.dumps({"reference": reference, "preview": content[:180], "ending": content[-120:],
                                      "characters": total, "tool": tool,
                                      "note": f"Folded: the middle is omitted. Call {tool} with this reference to read any part."},
                                     ensure_ascii=False)
                if len(preview) < len(content):
                    item["content"] = preview
                    removed += len(content) - len(preview)
        return removed

    def recall(self, arguments):
        try:
            query = json.loads(arguments)
            content = self.records[query["reference"]]
        except (ValueError, KeyError, TypeError):
            return json.dumps({"error": "Unknown or expired reference. Run the tool again to see its output."})
        try:
            start = max(0, int(query.get("offset") or 0))
            length = min(8000, max(1, int(query.get("length") or 2000)))
        except (ValueError, TypeError):
            return json.dumps({"error": "offset and length must be integers."})
        self.records.move_to_end(query["reference"])
        needle = query.get("search")
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
