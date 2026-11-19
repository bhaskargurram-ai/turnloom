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
- **Programs come from your system, not the project.** Turnloom finds ripgrep, Git and Provio on `PATH` and in its own scripts folder and never in the current directory or the workspace, so a project cannot substitute its own `rg.cmd` or `git.cmd`. Directory links and Windows junctions that lead out of the workspace are not followed.
- **Read-only means read-only.** A read-only session has no edit, shell or MCP tools.
- **Allow patterns are deliberately narrow.** `--allow "pytest*"` never applies to a command containing `;`, `&`, `|`, `<`, `>`, `` ` ``, `$`, parentheses, `%`, `^` or a newline.
- **Model output and file contents are untrusted input.** A file or web page can contain text written to steer the agent. The system prompt tells the model to treat tool output as data, and the approval gate is what actually protects you: a steered model still has to get its command past you.
- **Secrets.** Model API keys are stored in the operating system's credential store and are removed from the environment that commands inherit. Other secrets in your environment are not removed. The file tools refuse names that usually hold secrets (`.env`, `*.pem`, `*.key`, `id_rsa*`, `.npmrc`, `.netrc`, `credentials.json` and similar) and the `.git`, `.ssh`, `.aws` and `.gnupg` folders. This is a guard against accidents, not a boundary: an approved command can read anything you can.
- **A policy can sit under the approvals.** With a `provio.yaml` in the workspace and the `guard` extra installed, every shell command, file read, file edit and MCP call is first checked by [Provio](https://github.com/writ-agent/provio). A denied call never runs, a call the policy cannot judge never runs, and every decision is written to a hash-chained ledger. If the workspace has a policy file but Provio is not installed, the terminal, editors and Studio refuse to run until you install it or explicitly turn the gate off. This is a second check, not a sandbox: an allowed command still runs with your permissions.
- **Traces contain your code.** `.turnloom/*.jsonl` holds file contents and command output. Turnloom writes a `.gitignore` in that folder covering traces, the usage ledger and the undo database. Remove credentials before sharing a trace.

## Studio

Studio is a web server for one person on one machine.

- It binds to 127.0.0.1 and cannot be configured to listen elsewhere. The port is random unless you pass `--port`.
- Every API request needs a random token created at launch. The token reaches a browser through a private temporary file, not the command line, and lives in the tab's session storage. In the native window it never leaves the process.
- Requests must carry the server's own `Host` and, when present, `Origin`. That blocks cross-site requests and DNS rebinding. No CORS headers are sent.
- Pages are served with a Content Security Policy that allows scripts and connections only from Studio itself. Model output is rendered as text or sanitized Markdown, never as HTML.
- The server stops when you close the window or choose Close Studio. Opened in a browser, it also stops by itself after 15 minutes with no request and no running turn.
- **Auto-approve** in Studio approves every command, edit and tool call, like `--yes`. It is off by default and meant for disposable workspaces.
- Studio keeps each workspace's settings (approval switches, policy mode, enabled MCP servers, budget) in your own configuration folder, never in the workspace. A repository you clone cannot arrive with actions pre-approved or servers pre-enabled.

Another program running as your user on the same machine is outside this model: it could read the token from the browser's storage or act directly.

## MCP

An MCP configuration names programs Turnloom starts and URLs it connects to, so treat the file as code. The terminal only reads the file you pass with `--mcp`. Studio lists the servers in the workspace's `.turnloom/mcp.json` and starts one only after you switch it on or open its tool list there; read the command shown before you do that in a repository you did not write. Switching a server on trusts the definition it has at that moment: if the file later changes that server's command, it is switched off until you turn it on again. Process servers do not inherit your environment. They get the short list the MCP SDK gives every server (`PATH` and the home and profile variables, never API keys) plus the variables you name in `pass_env`. Every tool call asks for approval with its arguments shown.

`turnloom acp` gives the editor that launched it the same abilities you have in the terminal, with the editor's permission prompts as the approval gate.

`turnloom proxy` forwards another program's model requests to your connected model, using your key. It listens on loopback, refuses requests that carry a browser `Origin` or a foreign `Host`, and can require a token with `--token`. Any local program can still use it while it runs, so stop it when you are done.

`turnloom-agent` and `turnloom-eval` approve every command and edit by design and do not consult a workspace policy. They exist for benchmark sandboxes and disposable task copies and should not be pointed at a directory you care about.

`turnloom serve` exposes session metadata and archived tool output to the MCP client that launched it. With `--allow-run` that client can spend your model tokens; with `--allow-edits` or `--allow-shell` it can change files or run commands in the workspace through the agent. A workspace's Provio policy applies to that delegated work. Enable those only for clients you trust.

## Supply chain

The core depends on `keyring` only. Optional extras are listed in `pyproject.toml`. The Studio bundle is built from the lockfile in `studio/` and continuous integration fails when the committed bundle differs from a fresh build. Licenses of bundled JavaScript are in `turnloom/studio_assets/THIRD-PARTY.txt`.

Turnloom sends no telemetry. Its only network requests are to the model endpoint you connect, to that provider's model list, to models.dev for catalog data, and to OpenRouter during browser sign-in. LiteLLM's own telemetry is switched off when Turnloom loads it.
