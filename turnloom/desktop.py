"""Turnloom Studio as a desktop application.

The interface runs in a native window: pywebview hosts it in the system web view
(WebView2 on Windows, WKWebView on macOS, WebKitGTK or Qt on Linux). Where no web
view is available Studio opens a chromeless Edge or Chrome app window, and failing
that a browser tab. https://github.com/r0x0r/pywebview
"""

import os
import subprocess
import sys
import threading
from importlib.resources import files
from pathlib import Path

from turnloom.studio import choose_folder, handoff, make_server, open_browser
from turnloom.tools import find_program

TITLE = "Turnloom Studio"


def internal(arguments):
    """Helper processes of the packaged app: `--internal <module> [args]` runs a bundled module's entry point."""
    module, rest = arguments[0], arguments[1:]
    sys.argv = [module, *rest]
    if module == "verifydoc":
        from verifydoc.mcp_server import main
        return main()
    if module == "turnloom.unwind_stdio":
        import asyncio
        from turnloom.unwind_stdio import serve
        return asyncio.run(serve(*rest))
    raise SystemExit(f"Unknown internal module: {module}")


def app_browser():
    """A Chromium browser that can open a URL as a standalone app window."""
    candidates = [find_program(name) for name in ("msedge", "chrome", "google-chrome", "chromium", "chromium-browser")]
    for root in (os.environ.get("ProgramFiles(x86)"), os.environ.get("ProgramFiles"), os.environ.get("LocalAppData")):
        if root:
            candidates += [Path(root) / "Microsoft/Edge/Application/msedge.exe", Path(root) / "Google/Chrome/Application/chrome.exe"]
    return next((str(path) for path in candidates if path and Path(path).is_file()), None)


def launch(workspace=None, argv=None):
    try:
        import webview
    except ImportError:
        webview = None
    state = {"window": None}

    def chooser():
        window = state["window"]
        if webview and window:
            kind = getattr(getattr(webview, "FileDialog", None), "FOLDER", None) or webview.FOLDER_DIALOG
            chosen = window.create_file_dialog(kind)
            return chosen[0] if chosen else None
        return choose_folder()

    root = workspace or choose_folder()
    if not root:
        return 0
    server, url = make_server(Path(root), chooser=chooser, desktop=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        if webview:
            icon = files("turnloom").joinpath("studio_assets", "icon.ico" if os.name == "nt" else "icon.png")
            window = state["window"] = webview.create_window(TITLE, url, width=1480, height=940, min_size=(960, 620),
                                                             background_color="#0d1117", text_select=True)
            # Studio's own Close control stops the server; closing the window does the same.
            threading.Thread(target=lambda: (thread.join(), window.destroy()), daemon=True).start()
            try:
                webview.start(icon=str(icon) if icon.is_file() else None)
                return 0
            except Exception as error:  # no usable web view runtime on this machine
                print(f"Native window unavailable ({error}). Falling back.", file=sys.stderr)
        browser = app_browser()
        if browser:
            profile = Path(os.environ.get("LOCALAPPDATA") or Path.home() / ".cache") / "turnloom" / "app-window"
            process = subprocess.Popen([browser, f"--app={handoff(url)}", f"--user-data-dir={profile}", "--window-size=1480,940",
                                        "--no-first-run", "--no-default-browser-check"])
            threading.Thread(target=lambda: (process.wait(), server.shutdown()), daemon=True).start()
        else:
            open_browser(url)
        thread.join()
        return 0
    finally:
        server.shutdown()
        server.server_close()


def main():
    if len(sys.argv) > 2 and sys.argv[1] == "--internal":
        return internal(sys.argv[2:])
    import argparse
    parser = argparse.ArgumentParser(description="Launch the Turnloom Studio desktop app")
    parser.add_argument("--workspace", type=Path)
    return launch(parser.parse_args().workspace)


if __name__ == "__main__":
    raise SystemExit(main())
