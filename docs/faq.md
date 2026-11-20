# FAQ and troubleshooting

Answers here describe what the code does. Where a message is quoted, it is the text Turnloom prints.

- [Installing and connecting](#installing-and-connecting)
- [Privacy and safety](#privacy-and-safety)
- [Context and folding](#context-and-folding)
- [Files and commands](#files-and-commands)
- [Runs that stop early](#runs-that-stop-early)
- [Checking the setup](#checking-the-setup)
- [Reporting problems](#reporting-problems)

## Installing and connecting

### Which Python versions and operating systems are supported?

Python 3.11 or newer, on Windows, macOS and Linux. The test suite runs on all three systems, on Python 3.11 and 3.14. The core package has one dependency, `keyring`; everything else is an optional extra.

### Which providers and models work?

Any endpoint that speaks the OpenAI chat-completions format, with a model that supports tool calls.

| You have | Use |
| --- | --- |
| An account with a hosted provider | One of the 187 presets. `turnloom providers` lists them. |
| An OpenRouter account | `turnloom connect openrouter`, with sign-in through the browser |
| A model server on your machine | `turnloom connect ollama`, `lmstudio`, `vllm` or `llamacpp` |
| Any other OpenAI-compatible server | `TURNLOOM_ENDPOINT` and `TURNLOOM_MODEL` |
| Anthropic, Bedrock, Vertex AI, Azure OpenAI and other native APIs | The `llm` extra, then `turnloom connect litellm:anthropic` and its siblings |

Turnloom does not speak the Anthropic Messages API or the OpenAI Responses API directly. The presets are routing data: a name, an endpoint and a key variable. Most have not been checked one by one. [Connecting a model](providers.md) has the details.

### `turnloom connect` says a credential store is unavailable

`An OS credential store is unavailable. Set the provider's API key environment variable.` Turnloom saves keys only in Windows Credential Manager, macOS Keychain or a Linux secret service. On a machine without one, such as a container or a bare server, put the key in the provider's environment variable and name the model on the command line:

```sh
export GROQ_API_KEY=your-key
turnloom connect groq --model MODEL_ID
```

Or skip the saved connection and set `TURNLOOM_ENDPOINT`, `TURNLOOM_MODEL` and `TURNLOOM_API_KEY`.

### How do I switch models or remove a key?

Run `turnloom connect` again; the new choice replaces the old one. `turnloom connection` shows what is saved. `turnloom disconnect` clears it, and `turnloom disconnect --forget-key` also deletes that provider's key from the credential store. Turnloom never switches model by itself.

## Privacy and safety

### Does my code leave my machine?

Yes, to the model endpoint you connected. Turnloom itself sends it nowhere else.

- Everything the agent reads becomes part of a request to that endpoint: file contents, search results, command output and your tasks. If the code must stay on the machine, connect a local model. Its endpoint is on `127.0.0.1`.
- Turnloom sends no telemetry.
- Its other network requests carry no project content. They are: the provider's model list when you run `connect` or `models`; the public catalog at `models.dev`, cached for a day; and OpenRouter during browser sign-in and to check the key. Requests to OpenRouter carry headers naming Turnloom as the application.
- Transcripts stay on your disk, in `.turnloom/` in the workspace.

Commands the agent runs, MCP servers you configure and optional packages you install are separate programs with their own network behaviour.

### Where are my API keys, and can the agent read them?

Keys you enter are stored in the operating system's credential store, not in a file. `turnloom connection` and `turnloom doctor` never print them.

Two guards keep them away from the agent. Model credentials are removed from the environment of the commands it runs; the list is in [Configuration](configuration.md#what-the-agents-commands-can-see). And the file tools refuse files whose names usually hold secrets, such as `.env` and `*.pem`. These guard against accidents. They are not a boundary: a command you approve can read anything you can.

### How do approvals differ from a sandbox?

An approval decides *whether* an action happens. A sandbox limits *what an action can reach*. Turnloom has the first and not the second.

| | What limits it |
| --- | --- |
| The file tools | Confined to the workspace. Paths that leave it are refused. |
| An approved shell command | Nothing. It runs as you, with your files, your network and your permissions. |
| A Provio policy | A second check before the approval. It can refuse a call. It does not confine one that runs. |

So read a command before you approve it, and keep `--yes` for places where a wrong command cannot hurt: the Docker image or a disposable checkout. See [Run fully unattended in the Docker image](recipes.md#run-fully-unattended-in-the-docker-image). An optional sandbox for approved commands is on the [roadmap](../ROADMAP.md).

### What can the agent do without asking?

| Without asking | Only after approval |
| --- | --- |
| List, read and search files inside the workspace | Edit or create a file. You see the diff first. |
| Read back its own archived tool output | Run a shell command |
| | Call an MCP tool |

`--auto-edit`, `--allow` and `--yes` move things from the right column to the left. `--read-only` removes the right column.

## Context and folding

### What is folding, and how do I turn it off?

A long session fills the prompt with old tool output. Folding replaces it with short previews so the prompt stays small, without a summarizing model call.

- The three most recent tool results stay whole. So does any result the model has not read yet.
- An older result of 4,000 characters or more becomes a preview: its first 180 characters, its last 120, its length and a reference. Smaller results stay in the prompt, and so does a result the model fetched again after it was folded.
- The full text stays in the session's archive. The model reads any part back with its `recall` tool, by offset or by searching for a string. The transcript on disk always has the full result.

Turn it off for one run with `--no-fold` on `run`, `chat`, `team`, `turnloom-eval` or `proxy`. In Studio, switch off **Fold older tool output** in the Cost view.

Two things still happen with folding off. A command that prints more than 16,000 characters to one stream reaches the prompt as its head and tail, with the full output in the archive. And a conversation that outgrows the context window still has its oldest exchanges archived.

Folding rewrites earlier messages, so with a provider that caches prompts it trades some cache hits for a smaller prompt. To find out what it does on your tasks, run the same work with and without `--no-fold`. [Compare two configurations](experiments.md) shows how.

### What happens when the conversation outgrows the context window?

Turnloom moves the oldest complete exchanges to the archive and lists their references for the model, which can read them back with `recall`. The first task and your newest message always stay. The transcript records a `context` event each time.

If one turn alone is too large, its biggest tool result is shortened in place. If nothing more can be shortened, the turn ends with `The current turn exceeds the configured context window. Raise --context or choose a model with a larger window.`

When no window is known for the model, Turnloom assumes 128,000 tokens. Set the real size with `--context`.

### The model was told a reference is unknown or expired

The archive lives in memory and holds four million characters. When it is full, the record that was used longest ago is dropped, and `recall` answers `Unknown or expired reference. Run the tool again to see its output.` The model then runs the tool again. Resuming a session rebuilds the archive from the transcript.

## Files and commands

### Why was a path refused?

The file tools check every path before using it. The refusal goes to the model, and you see it as `failed:` on a tool line.

| Message | Cause | What to do |
| --- | --- | --- |
| `Path outside the workspace. The file tools reach only files under the project root.` | An absolute path, a drive letter, `..`, a link or junction that leads out of the workspace, or on Windows a name containing `:`. | Start Turnloom in a directory that contains what it needs, or pass `--workspace`. The agent can also use the shell for other paths, which asks for your approval. |
| `Protected path. Ask the operator to approve a shell command if this file is needed.` | The path goes through a closed folder or names a file that looks like a secret. | See the lists below. If the agent needs the content, approve a command that prints it, or copy the relevant part into a file it can read. |
| `No such file or directory: NAME` | The path does not exist. Paths are relative to the workspace root. | Nothing. The model usually lists the directory and tries again. |
| `File exceeds the 2 MiB text limit.` | The file tools read and write text files up to 2 MiB. | The agent can search the file or read a slice with a command. |
| `This file contains binary data.` | The file is not text. | Nothing to do with the file tools. |

Closed folders: `.git`, `.turnloom`, `.venv`, `venv`, `node_modules`, `__pycache__`, `.ssh`, `.aws`, `.gnupg`.

Names treated as secrets: `.env`, `.env.*`, `*.pem`, `*.key`, `*.p12`, `*.pfx`, `id_rsa*`, `id_ed25519*`, `id_ecdsa*`, `.netrc`, `.npmrc`, `.pypirc`, `.git-credentials`, `credentials.json`, `secrets.*`. The names `.env.example`, `.env.sample`, `.env.template`, `secrets.py` and `secrets.md` are allowed. Upper and lower case are treated alike.

Studio's own editor can open secret-looking files, because there it is you who opens them. The closed folders stay closed there too.

### What are the limits of the shell tool on Windows?

- **It runs `cmd.exe`.** That is true whichever shell you started Turnloom from, PowerShell and Git Bash included. The model is told so at the start of the session. On macOS and Linux the shell is `/bin/sh`.
- **One line per command.** A command with a line break is refused with `cmd.exe runs one line per command. Join steps with && or put them in a script with write_file and run that.`
- **Allow patterns rarely fit `cmd.exe` idioms.** `--allow` never covers a command containing `%`, `^`, `&`, a pipe or a redirect, so anything with `%VARIABLE%` or `&&` asks every time.

On every system:

- **No input.** A command cannot read from the keyboard. Its standard input is closed, so a program that prompts for an answer gets none.
- **A time limit.** A command gets 120 seconds unless the model asks for more, up to 600. A server or other long-running process has to be started in the background. At the limit the command and every process it started are ended.
- **Clipped output.** The prompt receives up to 16,000 characters of each output stream, head and tail. The rest is in the archive.

When you redirect Turnloom's own output to a file or a pipe on Windows, it is written as UTF-8.

### Why can a command not find my `GH_TOKEN` or my AWS credentials?

Commands the agent runs do not inherit model credentials or a short list of other tokens: `GH_TOKEN`, `GITHUB_TOKEN`, `GH_ENTERPRISE_TOKEN` and the `AWS_*` access variables are removed along with the provider keys. A tool that reads its credentials from an environment variable on that list will not find them. The full list is in [Configuration](configuration.md#what-the-agents-commands-can-see).

### Where are my sessions, and how do I delete one?

In `.turnloom/` in the workspace, one `.jsonl` file per session. `turnloom sessions` lists them. Delete a file to delete that session. The folder's `.gitignore` keeps them out of Git.

## Runs that stop early

### "Interactive approvals require a terminal"

`Interactive approvals require a terminal. Pass --read-only, --auto-edit (edits only), or --yes inside a trusted sandbox.` Standard input is not a terminal, for example in CI, in a pipe or under a scheduler, so nobody can answer a prompt. Choose a mode:

| Flag | Result without a terminal |
| --- | --- |
| `--read-only` | The agent explores and answers. Nothing needs approval. |
| `--auto-edit` | Edits apply. Commands are declined. |
| `--yes` | Everything is approved. Only inside a container or a disposable checkout. |

### Why did the task stop before it finished?

The receipt starts with the status, and a sentence after it says why. `run` exits with status 1 for all of these except `stopped`, which is 130.

| Status | Sentence | What to do |
| --- | --- | --- |
| `partial` | `Turn reached its step allowance.` | The agent used its last call to write up what it found. Continue with `--resume`, or raise `--max-steps`. |
| `partial` | `The same operation was requested three times in a row.` | The model was looping. Tool use ended and it wrote up what it had. Resume with a more specific instruction. |
| `partial` | `The reply reached its token limit and may be cut off. Raise the reply limit for longer answers.` | Raise `--reply`. |
| `step_limit` | `Reached the limit of N model calls.` | Raise `--max-steps`. |
| `timeout` | `The task deadline of N seconds passed.` | Raise `--deadline`. |
| `budget_limit` | `Stopped at the task allowance of ...` | Raise `--budget`. |
| `stopped` | `Interrupted from the keyboard.` | You pressed Ctrl+C. |
| `error` | `Model HTTP ...`, `Model transport failure: ...` or another message | See the next question. |

Whatever the status, the session is saved. `turnloom run "Continue" --resume` picks it up.

### What does a "Model HTTP" error mean?

The provider refused a request, and the text after the status code is the provider's own message.

Turnloom retries on its own when the status is 408, 409, 425, 429, 500, 502, 503, 504 or 529, up to three more times, waiting longer each time and honoring `Retry-After`. A dropped connection is retried the same way, and a timed-out request once. Any other status is reported at once, because repeating the request would not help.

| You see | Likely cause |
| --- | --- |
| `Model HTTP 401` or `403` | The key is wrong, expired or lacks access to this model. `turnloom doctor` shows where the key comes from. |
| `Model HTTP 404` | The endpoint URL or the model ID is wrong. |
| `Model HTTP 429` after retries | A rate limit or quota at the provider. |
| `Model endpoint returned a response that is not JSON. Check the endpoint URL.` | `TURNLOOM_ENDPOINT` points at something that is not an OpenAI-compatible API. It should be the base URL, such as `http://127.0.0.1:11434/v1`. |
| `The model returned neither an answer nor a tool call.` | The model sent an empty reply twice. Try again, or use another model. |

### The model answers but never uses a tool

The model probably does not support tool calls. `turnloom models PROVIDER` marks each model `tools listed` or `tool support unreported`. Pick one with `tools listed` where the provider reports it, and test local models with a small task first.

### Studio says the tab can no longer reach the local server

The Studio server has stopped. Opened with `--browser` or `--no-open`, it stops when you choose Close Studio, when you press Ctrl+C in its terminal, and after 15 minutes without use. Run `turnloom studio` again and resume the session from the session list.

## Checking the setup

### What does `turnloom doctor` check?

It reads local state only. It calls no model and does not test that the endpoint is reachable or the key is valid.

| Line | Shows |
| --- | --- |
| `Turnloom` | The installed version. |
| `Python` | The Python version and the operating system. |
| `Connection` | `custom endpoint URL / MODEL` when `TURNLOOM_ENDPOINT` and `TURNLOOM_MODEL` are set. Otherwise the saved provider and model, and where the key comes from: `environment`, `credential store`, `not required` or `MISSING`. `none. Run turnloom connect` when nothing is saved. `unreadable:` with a reason when the settings are broken. |
| `Credential store` | The store in use, such as `Windows`, `macOS` or `SecretService`, or `unavailable. Use provider environment variables`. |
| `ripgrep` | The path of `rg`, or `not found`. Without it file search uses a slower built-in scanner. The `search` extra installs it. |
| `MCP client and server` | The `mcp` extra. |
| `Editor protocol (ACP)` | The `acp` extra. |
| `YAML skill metadata` | The `skills` extra. |
| `LiteLLM gateway` | The `llm` extra. |
| `Provio policy gate` | The `guard` extra. |
| `LangGraph team graph` | The `team` extra. |
| `Exact token counts` | The `tokens` extra. |
| `Desktop window` | The `desktop` extra. |
| `VerifyDoc`, `Unwind ledger` | The `documents` extra. |
| `Workspace` | The directory being reported on. |
| `Project instructions` | `AGENTS.md`, `CLAUDE.md` or `none`. |
| `Policy` | `provio.yaml` or `none`. |
| `Saved sessions` | How many transcripts the workspace holds. |

"not installed" on an extra is only a problem if you want that feature. Install one with, for example, `python -m pip install "turnloom[mcp]"`.

## Reporting problems

### How do I report a bug?

Open an issue at <https://github.com/bhaskargurram-ai/turnloom/issues>. Include:

- the output of `turnloom --version` and `turnloom doctor`. Doctor prints no key, but it does show paths, your provider and your model; remove what you do not want public;
- your operating system and how you installed Turnloom;
- the smallest sequence of commands that shows the problem, and the message you got.

Check a transcript for secrets before attaching it: it contains file contents and command output. Questions and ideas go in [Discussions](https://github.com/bhaskargurram-ai/turnloom/discussions).

### How do I report a vulnerability?

Privately, through [GitHub security advisories](https://github.com/bhaskargurram-ai/turnloom/security/advisories/new). Do not open a public issue. [SECURITY.md](../SECURITY.md) says what to include and describes the security model.
