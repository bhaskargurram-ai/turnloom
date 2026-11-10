# Agent loop

```mermaid
flowchart LR
  Task --> Persona --> Context
  Context --> Budget --> Model
  Model -->|command| Approval --> Shell --> Context
  Model -->|reference| Recall --> Context
  Model -->|answer| Receipt
```

The CLI owns the conversation, selected persona, transcript path and in-memory observation store. The agent module owns the model protocol and tool loop. Each model call targets the configured endpoint and model. There is no background specialist inference.

Tool output is clipped at capture before entering history. A folded result keeps its first 180 and last 120 characters with a reference, and `recall` returns a slice by offset or the lines around a search string. The context store indexes returned observations by a content digest, keeps up to two million characters, and evicts older entries first. Recall reads the captured observation from that session. A subsequent command executes against the current workspace. At context pressure the agent removes old complete exchanges and preserves the original task and newest user instruction. Large individual requests may still exceed a provider's actual tokenizer limit.

Before each request the reply limit is set to what remains of the context window after the prompt, so a provider that refuses input plus reply beyond its window still gets a request that fits.

Each completed call records its usage and cumulative cost. Cached prompt tokens are priced at the connection's cache rate when one is known. Studio and the proxy also append every call to `.turnloom/usage.jsonl`. A transcript is an append-only JSONL file. Replay renders it without running commands. Replay is available after process exit. Interactive history and observation lookup live for the current chat.
