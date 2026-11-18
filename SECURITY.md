# Security

## Reporting a vulnerability

Report privately through [GitHub security advisories](https://github.com/bhaskargurram-ai/turnloom/security/advisories/new). Please include the version (`turnloom --version`), your operating system, and the smallest sequence that reproduces the problem. Do not open a public issue for a vulnerability. You should get a first response within a week. Fixes ship in the next release and are credited in the changelog unless you ask otherwise.

The latest release is the supported version.

## What Turnloom does on your machine

Turnloom lets a language model read files, propose edits and propose commands in one directory you choose. You are the control: commands, edits and MCP calls run only after you approve them, unless you chose a mode that approves them for you.

| The agent can, without asking | Only after approval |
| --- | --- |
| List, read and search files inside the workspace | Edit or create a file (you see the diff first) |
| Read back its own archived tool output | Run a shell command |
| | Call an MCP tool |

What that means in practice:

- **Workspace reads go to your model provider.** Anything the agent reads becomes part of a request to the endpoint you connected. Use a local model for code that must not leave the machine.
- **Approved commands are not sandboxed.** A command runs as your user with your file and network access. Read it before approving. `--yes` approves everything and is meant for containers and disposable checkouts; the repository's `Dockerfile` exists for that.
- **Allow patterns are deliberately narrow.** `--allow "pytest*"` never applies to a command containing `;`, `&`, `|`, `<`, `>`, `` ` ``, `$`, parentheses, `%`, `^` or a newline.
- **Model output and file contents are untrusted input.** A file or web page can contain text written to steer the agent. The system prompt tells the model to treat tool output as data, and the approval gate is what actually protects you: a steered model still has to get its command past you.
- **Secrets.** Model API keys are stored in the operating system's credential store and are removed from the environment that commands inherit. Other secrets in your environment are not removed. The file tools refuse names that usually hold secrets (`.env`, `*.pem`, `*.key`, `id_rsa*`, `.npmrc`, `.netrc`, `credentials.json` and similar) and the `.git`, `.ssh`, `.aws` and `.gnupg` folders. This is a guard against accidents, not a boundary: an approved command can read anything you can.
- **Traces contain your code.** `.turnloom/*.jsonl` holds file contents and command output. Turnloom writes a `.gitignore` in that folder. Remove credentials before sharing a trace.

## Studio

Studio is a web server for one person on one machine.

- It binds to 127.0.0.1 on a random port and cannot be configured to listen elsewhere.
- Every API request needs a random token created at launch. The token reaches the browser through a private temporary file, not the command line, and lives in the tab's session storage.
- Requests must carry the server's own `Host` and, when present, `Origin`. That blocks cross-site requests and DNS rebinding. No CORS headers are sent.
- Pages are served with a Content Security Policy that allows scripts and connections only from Studio itself. Model output is rendered as text or sanitized Markdown, never as HTML.
- The server stops when you choose Close Studio, and by itself after 15 minutes with no request and no running turn.

Another program running as your user on the same machine is outside this model: it could read the token from the browser's storage or act directly.

## MCP

An MCP configuration names programs Turnloom starts and URLs it connects to, so treat the file as code. Turnloom never loads one implicitly from a repository. Process servers inherit only the environment variables named in `pass_env`. Every tool call asks for approval with its arguments shown.

`turnloom serve` exposes session metadata and archived tool output to the MCP client that launched it. With `--allow-run` that client can spend your model tokens; with `--allow-edits` or `--allow-shell` it can change files or run commands in the workspace through the agent. Enable those only for clients you trust.

## Supply chain

The core depends on `keyring` only. Optional extras are listed in `pyproject.toml`. The Studio bundle is built from the lockfile in `studio/` and continuous integration fails when the committed bundle differs from a fresh build. Licenses of bundled JavaScript are in `turnloom/studio_assets/THIRD-PARTY.txt`.

Turnloom sends no telemetry. Its only network requests are to the model endpoint you connect, to that provider's model list, to models.dev for catalog data, and to OpenRouter during browser sign-in.
