# Design

Turnloom is one loop in `turnloom/agent.py`, a context store in `turnloom/context.py`, and file tools in `turnloom/tools.py`. The terminal (`cli.py`), Studio (`studio.py`), the MCP server (`server.py`), the headless entry point (`turnloom-agent`) and the evaluation runner (`evaluate.py`) all drive that same loop through a `Config`.

```mermaid
flowchart LR
  Task --> Prompt[System prompt<br/>persona, environment, AGENTS.md, skills]
  Prompt --> Fold[Fold and fit context]
  Fold --> Budget[Reply allowance and budget] --> Model
  Model -->|tool call| Policy{Provio policy}
  Policy -->|deny| Fold
  Policy -->|allow or ask| Gate{Approval}
  Gate -->|allowed| Tools[Files, shell, MCP, recall] --> Fold
  Gate -->|declined| Fold
  Model -->|answer| Receipt
  Tools -.full output.-> Archive[(Session archive)]
  Fold -.folded results.-> Archive
  Archive -.recall.-> Tools
  Model -.usage.-> Meter[(Usage ledger)]
```

## One step

Each step runs in this order:

1. **Stop checks.** The operator's stop flag, the deadline and the step limit.
2. **Fold.** Tool results older than the newest three and longer than 800 characters are replaced by a preview and a reference. The preview keeps the first 180 and last 120 characters, the length, and a note saying how to read the rest. Results that arrived after the latest assistant message are never folded, so a batch of parallel calls reaches the model whole.
3. **Fit.** The prompt is estimated at four characters per token, scaled by the ratio the provider reported on the previous call. If it exceeds the window, the oldest complete exchange is saved to the archive as one record and removed; its reference is appended to the system message and the transcript records a `context` event. The system prompt, the first task and the newest user message always stay. If only the newest exchange remains and it is still too large, its biggest tool result is archived and shortened in place.
4. **Reply allowance.** The reply limit is set to what remains of the window after the prompt, with a 20% margin on the estimate, so a provider that refuses input plus reply beyond its window still gets a request that fits.
5. **Budget.** With a budget set, the next request is priced from the estimate. The reply limit shrinks to what the remaining allowance affords, or the turn stops with `budget_limit`.
6. **Request.** One POST to `/chat/completions`. With a text callback the request streams and tool-call fragments are assembled by index. An endpoint that rejects streaming options is remembered and asked for whole replies. Retries cover 408, 409, 425, 429 and 5xx with backoff that honors `Retry-After`; a reply that has started to appear is never replayed, and a timed-out request is retried once. Endpoints of the form `litellm://provider` go through LiteLLM instead and return whole replies. On OpenRouter and LiteLLM routes to Claude models the system prompt is marked as a cache breakpoint.
7. **Reply.** A reply without tool calls is the answer. Tool calls run one at a time, each through the policy gate when one is active and then the approval gate.

## Tools

| Tool | Approval | Notes |
| --- | --- | --- |
| `list_files`, `read_file`, `search_files` | none | Confined to the workspace. `search_files` uses ripgrep when installed and a built-in scanner otherwise. |
| `edit_file`, `write_file` | yes, with a diff | `edit_file` replaces one exact match. If the text differs only by indentation or trailing space and matches one place, it is applied with the file's indentation. Line endings are preserved. Writes are atomic. |
| `shell` | yes | `cmd.exe` on Windows, `/bin/sh` elsewhere. A timeout or a stop ends the whole process tree. |
| `recall` | none | Reads a slice of an archived record, or up to five matches of a search string with their surroundings. |
| `mcp_search`, `mcp_call` | calls only | Present when MCP servers are configured. |

Every path is resolved before use. Paths that leave the workspace through `..`, an absolute path, a drive, a symlink or a Windows alternate data stream are refused. `.git`, `.turnloom`, virtual environments, `node_modules`, `.ssh`, `.aws` and `.gnupg` are closed to everyone. Files whose names look like secrets (`.env`, `*.pem`, `id_rsa*`, `.npmrc` and similar) are closed to the model's file tools and open to the operator's editor in Studio.

A read-only session offers the model only the read tools and `recall`.

## Approval and policy

`agent.permitted` is the approval gate: it asks the caller's `approve(kind, detail)` callback, where `kind` is `shell`, `edit` or `mcp`. With no callback, an action runs only if the caller set `auto_approve`. Nothing is approved by omission.

When a workspace has a `provio.yaml` and the `guard` extra is installed, `agent.gated` first hands the call to Provio as `bash`, `fs.read`, `fs.write` or the MCP tool's name. A denial returns the rule and its reason to the model. An ask verdict goes to the operator with the rule shown, or is refused when nobody can answer. An allowed call continues to the approval gate unless the operator chose to let the policy decide. If Provio cannot answer, the tool does not run.

## The archive

`Observations` maps a 16-character content digest to text and holds up to four million characters, evicting the least recently used record first. A reference is deterministic, so rebuilding a session from its transcript produces the same references.

Three things enter the archive: a tool result when it is folded, the complete output of a command when the prompt only received its head and tail (up to 400,000 characters per stream), and an exchange removed to fit the window. A folded message keeps pointing at the fullest record that exists for it.

## Guards

- Three identical tool calls in a row end tool use. The model is asked once more, without tools, to write up the evidence gathered, and the turn ends as `partial`.
- At the step limit the last call is spent the same way.
- Studio also caps tool calls per turn and ends tool use when the operator declines an action.
- An interrupted turn can leave tool calls without results. They are removed before the next request so the conversation stays valid.

## Transcript and usage

Each session appends to `<workspace>/.turnloom/<id>.jsonl`. Every event carries a timestamp. Events are `start` (task, model, system prompt), `assistant` (message and usage), `tool` (result), `edit` (path with content before and after), `context` (exchanges archived to fit the window) and `end` (the receipt). The folder gets a `.gitignore` containing `*`.

The transcript is the durable form of a session. `replay` prints it, `--resume` rebuilds the conversation from it, `undo` restores files from its `edit` events, `turnloom atif` converts it to an ATIF trajectory, and `turnloom serve` reads it for other agents. It contains file contents and command output, so treat it like the code it came from.

Studio and the proxy also append one line per model call to `.turnloom/usage.jsonl`: model, persona, tokens, cached tokens, estimated cost and the tokens folding kept out. The Cost view reads that ledger.

## Receipt

`Result` carries the status, the answer, steps, elapsed time, token usage, estimated cost at the configured rates, folded characters and the files changed. Cached prompt tokens are priced at the connection's cache rate when one is known. When a provider omits usage, the call is metered by estimate and the receipt says so.
