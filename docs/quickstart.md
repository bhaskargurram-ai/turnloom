# Quick start

This page takes you from nothing installed to a first result, then shows the things you will use every day: approvals, undo, resuming a session, Studio and the transcript. Allow about five minutes if you already have Python and a model to connect.

## Before you start

You need two things:

- **Python 3.11 or newer.** Check with `python --version`.
- **A model that supports tool calls.** Any of these works: an account with a hosted provider, an OpenRouter account, or a model server on your own machine such as Ollama or LM Studio.

## 1. Install

```sh
pipx install turnloom
```

Other ways to install:

| You use | Command |
| --- | --- |
| pipx | `pipx install turnloom` |
| uv | `uv tool install turnloom` |
| uv, without installing | `uvx turnloom chat` |
| pip | Create a virtual environment first, as shown below |
| Docker | `ghcr.io/bhaskargurram-ai/turnloom`, as shown below |
| Windows, without a terminal | Clone the repository and double-click **Open Turnloom Studio.cmd** |

With pip, on macOS or Linux:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install turnloom
.venv/bin/turnloom --version
```

With pip, on Windows (PowerShell):

```powershell
py -3 -m venv .venv
.venv\Scripts\python -m pip install turnloom
.venv\Scripts\turnloom --version
```

With Docker, the image's entry point is the `turnloom` command, so anything after the image name is passed to it:

```sh
docker run --rm ghcr.io/bhaskargurram-ai/turnloom --version
```

The image has no credential store and is meant for unattended runs. [Run fully unattended in the Docker image](recipes.md#run-fully-unattended-in-the-docker-image) shows how to pass a connection and mount a project.

**Open Turnloom Studio.cmd** sits at the root of the repository. It needs Python 3.11 or newer on the machine. The first run creates a `.venv` folder inside the checkout, installs Turnloom with the `studio` extra, and opens Studio with a folder dialog. Later runs open Studio straight away.

### Check the install

```sh
turnloom doctor
```

`doctor` calls no model. On a Windows machine with several extras installed it prints something like this (your versions and paths will differ):

```text
Turnloom              0.7.0
Python                3.14.3 on Windows 11
Connection            openai / gpt-5.1 (key: credential store)
Credential store      Windows
ripgrep               C:\Users\you\project\.venv\Scripts\rg.EXE
MCP client and server installed
Editor protocol (ACP) installed
YAML skill metadata   installed
LiteLLM gateway       not installed
Provio policy gate    installed
LangGraph team graph  installed
Exact token counts    installed
Desktop window        not installed
VerifyDoc             installed
Unwind ledger         installed
Workspace             C:\Users\you\project
Project instructions  AGENTS.md
Policy                none
Saved sessions        2
```

Straight after installing, the `Connection` line reads `none. Run turnloom connect`. "not installed" on an optional feature is not an error. [The FAQ](faq.md#what-does-turnloom-doctor-check) explains each line.

## 2. Connect a model

```sh
turnloom connect
```

`connect` shows a numbered list of providers, twelve at a time. At the prompt `Number, search text, /next, /all or /quit:` type a number to choose, or part of a name to filter the list. Then it does the same for that provider's models. What happens in between depends on the provider:

| Provider | What `connect` asks for |
| --- | --- |
| OpenRouter | After you pick a model, press Enter to sign in through your browser, or type `key` to paste an API key. |
| A local server (`ollama`, `lmstudio`, `vllm`, `llamacpp`) | Nothing. The server has to be running on its default port. |
| Any other provider | It prints the endpoint the key will be sent to, then asks for the key once. Typing is hidden. |

Keys you type are saved in your operating system's credential store, not in a file. When the choice is saved, `connect` prints `Selected PROVIDER / MODEL (SOURCE).`

To see the list of providers without connecting, run `turnloom providers`. It prints one line per preset, starting like this:

```text
openrouter  OpenRouter  https://openrouter.ai/api/v1
huggingface  Hugging Face  https://router.huggingface.co/v1
ollama  Ollama (local)  http://127.0.0.1:11434/v1
lmstudio  LM Studio (local)  http://127.0.0.1:1234/v1
vllm  vLLM (local)  http://127.0.0.1:8000/v1
llamacpp  llama.cpp (local)  http://127.0.0.1:8080/v1
groq  Groq  https://api.groq.com/openai/v1
deepseek  DeepSeek  https://api.deepseek.com
google  Gemini  https://generativelanguage.googleapis.com/v1beta/openai
openai  OpenAI  https://api.openai.com/v1
```

You can skip the menus by naming the provider and the exact model ID. The key is then read from the provider's environment variable:

```sh
export OPENAI_API_KEY=your-key
turnloom connect openai --model gpt-5.1
```

```powershell
$env:OPENAI_API_KEY = "your-key"
turnloom connect openai --model gpt-5.1
```

A key that comes from the environment is used but not copied into the credential store, so keep the variable set for later runs.

`turnloom connection` prints the saved choice. [Connecting a model](providers.md) covers local models, custom endpoints and providers that need LiteLLM.

If you skip this step, `turnloom run` and `turnloom chat` start the same menus the first time you use them in a terminal.

## 3. Run a first task

Change to a project directory. Turnloom works in the directory you start it from; that directory is the *workspace*.

A read-only task is a safe first run. The agent can list, read and search files and nothing else, so there is nothing to approve:

```sh
cd path/to/your/project
turnloom run "Explain what this project does and how to run its tests" --read-only
```

What you see:

1. A header line with the Turnloom version, the persona (`builder`), the model, the approval mode and the first eight characters of the session ID.
2. One line for each tool the agent uses, starting with `→` and naming the tool and its main argument, followed by a line starting with `←` with the outcome and how long it took.
3. The answer, streamed as the model writes it.
4. A receipt line: the final status, input and output tokens, how many characters of context were folded, how many files changed, and an estimated cost when the model's prices are known.

The header and the tool lines go to standard error. The answer and the receipt go to standard output. `turnloom run` exits with status 0 when the task completes.

Now a task that changes something. Leave out `--read-only` and Turnloom asks before each edit and each command:

```sh
turnloom run "Fix the failing test in tests/test_parser.py"
```

## 4. Answer approval prompts

Reading and searching files in the workspace needs no approval. Editing a file, running a command and calling an MCP tool do.

Before a command runs, Turnloom prints the command and asks:

```text
$ python -m unittest -q
Run this? [y]es / [n]o / [a]lways allow this exact request:
```

Before an edit lands, it prints the diff and asks:

```text
Edit greet.py
--- a/greet.py
+++ b/greet.py
@@ -1,2 +1,2 @@
 def greet(name):
-    return "Hello " + name
+    return f"Hello, {name}!"
Apply this edit? [y]es / [n]o / [a]lways allow edits:
```

A new file is shown the same way, headed `Create` in place of `Edit`.

| You type | On a command or MCP call | On an edit |
| --- | --- | --- |
| `y` or `yes` | Runs it this once. | Applies this edit. |
| `a` or `always` | Runs it, and stops asking about that exact command or call for the rest of the session. | Applies it, and stops asking about edits for the rest of the session. |
| `n`, Enter or anything else | Does not run it. The model is told you declined and carries on. | Does not apply it. The model is told you declined and carries on. |

Flags change who is asked. `--auto-edit` approves edits and still asks about commands. `--allow "pytest*"` approves simple commands that match the pattern. `--read-only` removes edits and commands altogether. `--yes` approves everything and belongs in a container or a disposable checkout. The [command reference](cli.md#approval-flags) has the details.

Approval is not a sandbox. An approved command runs as you, with your files and your network. Read it first.

## 5. Chat across several turns

```sh
turnloom chat
```

`chat` prints the same header, then waits at the prompt `turnloom ›`. Type a task and press Enter. The conversation carries over from one turn to the next. These commands work at the prompt:

```text
/help  show these commands
/diff  show files the last turn changed
/undo  restore files the last turn edited
/new   start a fresh conversation in this session
/quit  leave the chat
```

Ctrl+C while the agent is working ends the turn and returns you to the prompt. Ctrl+C at the prompt leaves the chat.

## 6. Undo an edit

Inside `chat`, `/diff` shows what the last turn changed and `/undo` puts those files back:

```text
turnloom › /undo
Restored greet.py
```

After a `run`, or after you have left the chat, use the `undo` command from the same directory. It restores the files edited by the last turn of the most recent session:

```sh
turnloom undo
```

Undo covers edits made with the agent's file tools. It does not reverse what a shell command did. A file you changed after the agent edited it is left alone and reported as `Kept NAME: it changed after the agent's edit`.

## 7. Resume a session

Every session is saved in the workspace. List them:

```sh
turnloom sessions
```

Each line shows the first eight characters of the session ID, when it was last used, the number of turns, the last status, the model and the start of the first task:

```text
83966603  2026-11-10 14:02  1 turn  completed    gpt-5.1  Fix the failing test in tests/test_parser.py
```

Continue the most recent session, or pick one by the start of its ID:

```sh
turnloom chat --resume
turnloom chat --resume 8396
turnloom run "Now add a test for the empty case" --resume
```

The header of a resumed session ends with `resumed`. Sessions belong to a workspace, so run these from the same directory as before, or pass `--workspace`.

## 8. Open Studio

Studio is the same agent in a window, with a file editor, diffs to approve, a cost view and the session list.

```sh
turnloom studio
```

This opens Studio for the current directory. The native window needs the `desktop` extra, which `pipx install "turnloom[studio]"` includes. Without it Studio opens in a standalone Edge or Chrome window, or in a browser tab if neither is found.

| Command | Result |
| --- | --- |
| `turnloom studio` | Opens a window for the current directory. |
| `turnloom studio --browser` | Opens a tab in your default browser. Prints the session address and how to stop the server. |
| `turnloom studio --no-open` | Prints the session address and opens nothing. For a remote shell with a forwarded port. |
| `turnloom studio --workspace path/to/project` | Opens another directory. |

Studio listens on `127.0.0.1` only. Opened with `--browser` or `--no-open`, it stops when you choose Close Studio, press Ctrl+C in the terminal, or leave it unused for 15 minutes. [Desktop app](desktop.md) has more.

## 9. Find the transcript

Each session is one JSON Lines file in the workspace:

```text
<workspace>/.turnloom/<session id>.jsonl
```

The session ID is 32 hexadecimal characters; `turnloom sessions` shows the first eight. The file records every task, model reply, tool result and edit, and the receipt of each turn. Print one in readable form without calling a model:

```sh
turnloom replay .turnloom/SESSION_ID.jsonl
```

The first time a session is saved, Turnloom writes `.turnloom/.gitignore` so that transcripts stay out of version control. A transcript contains file contents and command output from your project. Treat it like the code it came from.

## Next steps

| You want to | Read |
| --- | --- |
| Copy a working example for a specific job | [Recipes](recipes.md) |
| Look up a flag or a default | [Command reference](cli.md) |
| Give the agent project instructions, skills, personas or MCP servers | [Configuration](configuration.md) |
| Use a local model or a custom endpoint | [Connecting a model](providers.md) |
| Understand an error or a refusal | [FAQ and troubleshooting](faq.md) |
