"""Turn a title into a URL slug."""

import re


def slugify(title):
    words = re.findall(r"[a-z0-9]+", title)
    return "-".join(words)
