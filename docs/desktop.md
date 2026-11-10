# Desktop app

Turnloom Studio runs as a desktop application. The interface is hosted in a native window by [pywebview](https://github.com/r0x0r/pywebview): WebView2 on Windows, WKWebView on macOS, and WebKitGTK or Qt on Linux. The agent, your files and your keys stay in a local Python process that the window talks to over loopback with a per-launch token.

## Run it

| You have | Do this |
| --- | --- |
| The built app | Open `Turnloom Studio.exe` in the app folder and choose a workspace |
| A checkout on Windows | Double-click **Open Turnloom Studio.cmd** |
| A Python environment | `python -m pip install ".[studio]"`, then `turnloom-studio --workspace .` |

`turnloom-studio --browser` opens Studio in a browser tab instead. If no web view runtime is present, Studio opens a standalone Edge or Chrome app window, and a browser tab as the last resort.

Use the workspace name in the title bar, or **Open another workspace** in the command palette (Ctrl+K), to switch folders without restarting.

## Build the app

From the repository root on the platform you are building for:

```powershell
python -m pip install -e ".[studio,full,desktop]"
npm ci --prefix studio
npm run build --prefix studio
python -m PyInstaller packaging/turnloom-studio.spec --noconfirm
```

On Windows, `packaging/build.ps1` runs the same steps. The result is the folder `dist/Turnloom Studio/`. Ship the whole folder.

| File | Role |
| --- | --- |
| `Turnloom Studio.exe` | The windowed app |
| `turnloom-worker.exe` | A console helper the app starts for the VerifyDoc and Unwind processes, which speak MCP over standard input and output |
| `_internal/` | The Python runtime, Turnloom, the persona catalog, ripgrep and the bundled libraries |

The build is about 450 MB, most of it LiteLLM and the document libraries VerifyDoc depends on. It also carries the ripgrep and Provio binaries.

## What the build has and has not been through

Checked on Windows 11 with Python 3.14: the app starts, the window renders Studio, and the helper serves VerifyDoc through Unwind. The executables are not code-signed, so SmartScreen warns on first launch on another machine. macOS and Linux builds use the same spec and have not been built or run. There is no installer or auto-update.
