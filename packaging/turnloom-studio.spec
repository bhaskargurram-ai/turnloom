# PyInstaller spec for the Turnloom Studio desktop app.
#
#     python -m pip install -e ".[build-app]"
#     python -m PyInstaller packaging/turnloom-studio.spec --noconfirm
#
# Produces dist/Turnloom Studio/ with two executables that share one runtime:
#   Turnloom Studio.exe   the windowed app
#   turnloom-worker.exe   a console helper the app starts for the VerifyDoc and Unwind
#                         MCP processes, which talk over standard input and output

import shutil
import sys
import sysconfig
from pathlib import Path

from PyInstaller.utils.hooks import collect_all, collect_data_files, collect_submodules, copy_metadata

root = Path(SPECPATH).parent
datas = collect_data_files("turnloom")
binaries = []
hidden = collect_submodules("turnloom")
for package in ("verifydoc", "unwind", "mcp", "webview", "keyring", "langgraph", "tiktoken", "tiktoken_ext"):
    found = collect_all(package)
    datas += found[0]
    binaries += found[1]
    hidden += found[2]
# LiteLLM reads its price map and provider tables from package data at import.
datas += collect_data_files("litellm")
hidden += ["litellm", "tiktoken_ext.openai_public"]
for distribution in ("turnloom", "verifydoc", "rollback-mcp", "mcp", "litellm", "keyring"):
    datas += copy_metadata(distribution)
hidden += collect_submodules("provio_sdk")
for tool in ("rg", "provio"):
    found = shutil.which(tool, path=sysconfig.get_path("scripts"))
    if found:
        binaries.append((found, "."))

analysis = Analysis(
    [str(root / "packaging" / "entry.py")],
    pathex=[str(root)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hidden,
    excludes=["tkinter.test", "pytest", "IPython", "matplotlib", "torch", "tensorflow"],
    noarchive=False,
)
archive = PYZ(analysis.pure)
icon = str(root / "packaging" / "turnloom.ico")
app = EXE(archive, analysis.scripts, [], exclude_binaries=True, name="Turnloom Studio", console=False, icon=icon)
helper = EXE(archive, analysis.scripts, [], exclude_binaries=True, name="turnloom-worker", console=True, icon=icon)
COLLECT(app, helper, analysis.binaries, analysis.datas, name="Turnloom Studio")
