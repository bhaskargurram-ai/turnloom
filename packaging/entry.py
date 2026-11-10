"""Entry point of the packaged Turnloom Studio app and of its console helper."""

import multiprocessing
import os
import sys

if __name__ == "__main__":
    multiprocessing.freeze_support()
    # A windowed executable has no console streams. Libraries that print must not fail on that.
    for name in ("stdout", "stderr"):
        if getattr(sys, name) is None:
            setattr(sys, name, open(os.devnull, "w", encoding="utf-8"))
    from turnloom.desktop import main

    raise SystemExit(main())
