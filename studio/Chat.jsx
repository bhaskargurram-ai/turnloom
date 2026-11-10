import React, { useEffect, useRef, useState } from "react";
import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";
import {
  ArrowUp,
  Check,
  ChevronRight,
  CircleDashed,
  CircleDot,
  FileCode2,
  GitPullRequest,
  Layers3,
  Link2,
  LoaderCircle,
  MessageSquarePlus,
  ShieldCheck,
  Sparkles,
  Users,
  X,
} from "lucide-react";
import { AgentApprovalCard } from "./node_modules/@cline/ui/dist/components/agent-approval-card.js";
import { AgentCommandOutput } from "./node_modules/@cline/ui/dist/components/agent-command-output.js";
import { AgentConversationHeader } from "./node_modules/@cline/ui/dist/components/agent-conversation-layout.js";
import { api, tokens, usd } from "./api.js";
import { Avatar } from "./Personas.jsx";
import { RiskBadge } from "./Tools.jsx";

function activityGroups(messages) {
  const groups = [];
  for (const message of messages) {
    if (message.role !== "tool") {
      groups.push(message);
    } else if (groups.at(-1)?.role === "activity") {
      groups.at(-1).items.push(message);
    } else {
      groups.push({ role: "activity", items: [message] });
    }
  }
  return groups;
}

function Activity({ items }) {
  const [expanded, setExpanded] = useState(false);
  const failed = items.filter((item) => item.failed).length;
  return (
    <details
      className="activity"
      onToggle={(e) => setExpanded(e.currentTarget.open)}
    >
      <summary>
        {items.length} tool {items.length === 1 ? "action" : "actions"}
        {failed > 0 && <span> · {failed} unsuccessful</span>}
      </summary>
      {expanded &&
        items.map((item, i) => (
          <details key={i} className="activity-item">
            <summary>
              {item.blocked ? "⛔ " : item.failed ? "× " : "✓ "}
              {item.label || item.name}
              {item.blocked && <i className="tag warn">blocked by {item.blocked}</i>}
            </summary>
            <AgentCommandOutput isRunning={false} output={item.text} />
          </details>
        ))}
    </details>
  );
}

function Stages({ stages, personas }) {
  return (
    <ol className="pipeline" aria-label="Team progress">
      {stages.map((stage) => {
        const persona = personas.find((p) => p.id === stage.persona);
        return (
          <li key={stage.name} className={stage.status}>
            {stage.status === "running" ? (
              <LoaderCircle size={14} className="spin" />
            ) : stage.status === "completed" ? (
              <Check size={14} />
            ) : stage.status === "queued" ? (
              <CircleDashed size={14} />
            ) : (
              <X size={14} />
            )}
            <span>
              <b>{stage.name}</b>
              <small>{persona?.name || stage.persona}</small>
            </span>
          </li>
        );
      })}
    </ol>
  );
}

export function Chat({
  state,
  personas,
  persona,
  file,
  go,
  refresh,
  report,
  needModel,
}) {
  const [task, setTask] = useState(""),
    [attach, setAttach] = useState(false),
    [sending, setSending] = useState(false);
  const scroll = useRef(null);
  const settings = state.settings,
    team = settings.mode === "team",
    companions = settings.servers.includes("verifydoc"),
    current = personas.find((p) => p.id === persona);
  useEffect(() => {
    scroll.current?.scrollTo(0, scroll.current.scrollHeight);
  }, [state.messages.length, state.pending?.id]);
  async function send() {
    if (!task.trim() || sending || state.busy) return;
    if (!state.model) return needModel();
    setSending(true);
    try {
      await api("run", {
        persona,
        task:
          task.trim() +
          (attach && file ? "\nActive file on disk: " + file.path : ""),
      });
      setTask("");
      await refresh();
    } catch (e) {
      report(e);
    } finally {
      setSending(false);
    }
  }
  async function act(action, data) {
    try {
      await api(action, data);
      await refresh();
    } catch (e) {
      report(e);
    }
  }
  const risk = state.pending?.risk,
    policy = state.pending?.policy;
  return (
    <section className="agent">
      <AgentConversationHeader
        className="bar"
        actions={
          <>
            <button
              className="personachip"
              aria-label="Agent persona"
              title="Choose a persona or set up a team"
              onClick={() => go("agents")}
            >
              {team ? (
                <>
                  <Users size={15} /> Team of {settings.team.length}
                </>
              ) : (
                <>
                  <Avatar persona={current} size={20} />
                  {current?.name || persona}
                </>
              )}
              <ChevronRight size={13} />
            </button>
            <button
              aria-label="New chat"
              title="New chat"
              disabled={state.busy || !state.messages.length}
              onClick={() => act("reset")}
            >
              <MessageSquarePlus size={15} />
            </button>
          </>
        }
      >
        <b>
          <Sparkles size={16} /> Agent
        </b>
      </AgentConversationHeader>
      <div className="transcript" ref={scroll}>
        {!state.messages.length && (
          <div className="intro">
            <div className="woven">
              <Layers3 size={34} />
            </div>
            <h1>Work on {state.workspace}</h1>
            <button
              onClick={() =>
                setTask(
                  "Review the current changes and identify the most important issues.",
                )
              }
            >
              <GitPullRequest />
              Review changes
              <ChevronRight />
            </button>
            <button
              disabled={!file}
              onClick={() => {
                setAttach(true);
                setTask("Explain this file and its role in the project.");
              }}
            >
              <FileCode2 />
              Explain active file
              <ChevronRight />
            </button>
          </div>
        )}
        {activityGroups(state.messages).map((m, i) =>
          m.role === "activity" ? (
            <Activity key={i} items={m.items} />
          ) : (
            <article
              className={"msg " + m.role + (m.summary ? " summary" : "")}
              key={i}
            >
              <small>
                {m.role === "user"
                  ? "YOU"
                  : m.role === "assistant"
                    ? m.stage
                      ? `${m.stage.toUpperCase()} · ${personas.find((p) => p.id === m.persona)?.name || m.persona}`
                      : "TURNLOOM"
                    : m.role.toUpperCase()}
              </small>
              <Markdown
                skipHtml
                remarkPlugins={[remarkGfm]}
                components={{
                  img: () => null,
                  a: ({ children }) => <span>{children}</span>,
                }}
              >
                {m.text}
              </Markdown>
              {m.note && <small>{m.note}</small>}
              {m.usage && (
                <small className="receipt">
                  {tokens(m.usage.prompt_tokens)} in ·{" "}
                  {tokens(m.usage.completion_tokens)} out
                  {m.usage.cached_tokens
                    ? ` · ${tokens(m.usage.cached_tokens)} cached`
                    : ""}
                  {m.cost != null ? " · " + usd(m.cost) : ""}
                </small>
              )}
            </article>
          ),
        )}
        {!!state.stages.length && (
          <Stages stages={state.stages} personas={personas} />
        )}
        {state.busy && !state.pending && (
          <p className="working">
            <CircleDot size={14} /> Agent working…
          </p>
        )}
        {state.pending && (
          <AgentApprovalCard
            title={
              state.pending.kind === "mcp"
                ? "Call this tool?"
                : "Run this command?"
            }
            meta={
              risk && (
                <RiskBadge
                  level={risk.reversibility_class}
                  label={risk.class_label}
                />
              )
            }
            description={
              policy
                ? `Provio asks: ${policy.reason || policy.rule}${policy.irreversible ? " This cannot be undone." : ""} (rule ${policy.rule})`
                : risk &&
              `Unwind: ${risk.can_undo ? "can undo with " + risk.undo_via + (risk.fidelity ? " (" + risk.fidelity + ")" : "") : risk.reversibility_class === "R0" ? "read only, nothing to undo" : "cannot undo this"} · reach: ${risk.blast_radius}`
            }
            detail={state.pending.command}
            onApprove={() =>
              act("decision", { id: state.pending.id, approve: true })
            }
            onReject={() =>
              act("decision", { id: state.pending.id, approve: false })
            }
          />
        )}
      </div>
      <div className="compose">
        <div className="composeopts">
          {state.companions && (
            <label className="companions">
              <input
                type="checkbox"
                checked={companions}
                disabled={state.busy}
                onChange={(e) =>
                  act("settings", {
                    servers: e.target.checked
                      ? [...settings.servers, "verifydoc"]
                      : settings.servers.filter((x) => x !== "verifydoc"),
                  })
                }
              />
              VerifyDoc via Unwind
            </label>
          )}
          <label
            className="companions"
            title="Run shell commands and tool calls without asking. Use only in a workspace you trust."
          >
            <input
              type="checkbox"
              checked={settings.auto_approve}
              disabled={state.busy}
              onChange={(e) =>
                act("settings", { auto_approve: e.target.checked })
              }
            />
            <ShieldCheck size={13} />
            Auto-approve
          </label>
        </div>
        <textarea
          aria-label="Message to agent"
          placeholder={
            team
              ? "Give the team a task…"
              : "Ask Turnloom to work on this project…"
          }
          value={task}
          onChange={(e) => setTask(e.target.value)}
          onKeyDown={(e) => {
            if ((e.ctrlKey || e.metaKey) && e.key === "Enter") send();
          }}
        />
        <div className="composerfoot">
          <button
            disabled={!file}
            className={attach ? "attached" : ""}
            onClick={() => setAttach(!attach)}
          >
            <Link2 size={14} />
            {attach ? "File attached" : "Attach file"}
          </button>
          <span className="keys-hint">Ctrl+Enter to send</span>
          {state.busy ? (
            <button disabled={state.stopping} onClick={() => act("stop")}>
              {state.stopping ? "Stopping…" : "Stop turn"}
            </button>
          ) : (
            <button
              className="send"
              aria-label="Send message"
              disabled={!task.trim() || state.busy || sending}
              onClick={send}
            >
              <ArrowUp size={19} />
            </button>
          )}
        </div>
      </div>
    </section>
  );
}
