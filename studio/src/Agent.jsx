import React, { forwardRef, memo, useEffect, useImperativeHandle, useLayoutEffect, useMemo, useRef, useState } from "react";
import { api } from "./api.js";
import { configure, personaTitle, studio, usePersonas, useStudio } from "./store.js";
import { Confirm, Dialog } from "./Dialog.jsx";
import { DiffPanel, DiffView } from "./DiffView.jsx";
import { Prose } from "./Prose.jsx";
import { Caret } from "./icons.jsx";
import { diffStat } from "./diff.js";
import { ago, clock, count, family, lines, money, plural, visible } from "./util.js";

const KNOWN = ["list_files", "read_file", "search_files", "edit_file", "write_file", "recall", "mcp_search", "mcp_call"];

function Elapsed({ since }) {
  const [, tick] = useState(0);
  useEffect(() => {
    const timer = setInterval(() => tick((value) => value + 1), 1000);
    return () => clearInterval(timer);
  }, []);
  return <time className="num elapsed">{clock(Date.now() - since)}</time>;
}

// The catalog's own name for a persona id, falling back to the id written as words.
function usePersonaName(id) {
  const catalog = usePersonas().data;
  if (!id) return "";
  return catalog?.personas.find((row) => row.id === id)?.name || personaTitle(id);
}

function StagePersona({ id }) {
  return <span className="stage-persona">{usePersonaName(id)}</span>;
}

function Receipt({ message }) {
  const usage = message.usage || {};
  const parts = [];
  const persona = usePersonaName(message.stage ? "" : message.persona);
  if (persona) parts.push(persona);
  if (usage.prompt_tokens != null) parts.push(count(usage.prompt_tokens) + " tokens in");
  if (usage.completion_tokens != null) parts.push(count(usage.completion_tokens) + " out");
  if (usage.cached_tokens) parts.push(count(usage.cached_tokens) + " read from cache");
  if (message.cost != null) parts.push(message.cost === 0 ? "no charge" : money(message.cost) + " estimated");
  const status = message.status || "completed";
  return (
    <p className="receipt">
      <span className={"receipt-status " + status}>{status.replace(/_/g, " ")}</span>
      {parts.map((part) => (
        <span className="num" key={part}>
          {part}
        </span>
      ))}
    </p>
  );
}

const ToolLine = memo(function ToolLine({ message }) {
  const [open, setOpen] = useState(false);
  const rest = message.label?.startsWith(message.name)
    ? message.label.slice(message.name.length).trim()
    : message.label || "";
  return (
    <div className={"step" + (message.failed ? " failed" : "") + (open ? " open" : "")}>
      <button type="button" className="step-line" aria-expanded={open} onClick={() => setOpen(!open)}>
        <i className={"swatch f-" + family(message.name)} aria-hidden="true" />
        <Caret open={open} />
        <span className="step-name">{message.name}</span>
        <span className="step-rest">{rest}</span>
        {message.blocked ? (
          <span className="step-flag pending" title={message.blocked !== "policy" ? "Rule " + message.blocked : undefined}>
            {message.declined ? "policy asked, you declined" : "blocked by policy"}
          </span>
        ) : (
          message.failed && <span className="step-flag">failed</span>
        )}
      </button>
      {open &&
        (message.diff ? (
          <div className="scroll-box">
            <DiffView text={message.text} label={"Changes made by " + message.label} />
          </div>
        ) : (
          <pre className="output scroll-box" tabIndex={0} aria-label={"Output of " + (message.label || message.name)}>
            {visible(message.text || "No output.")}
          </pre>
        ))}
    </div>
  );
});

const Entry = memo(function Entry({ message, turn, onConnect }) {
  if (message.role === "tool") return <ToolLine message={message} />;
  if (message.role === "user")
    return (
      <article className="entry request">
        <h3 className="label">
          Request <span className="num">{turn}</span>
        </h3>
        <p className="plain">{message.text}</p>
      </article>
    );
  if (message.role === "error")
    return (
      <article className="entry failure" role="alert">
        <h3 className="label">Turn failed</h3>
        <p className="plain">{message.text}</p>
        {/\b40[13]\b/.test(message.text) && (
          <p className="entry-actions">
            <span>The provider rejected the credentials.</span>
            <button type="button" className="small" onClick={onConnect}>
              Open connection
            </button>
          </p>
        )}
      </article>
    );
  if (message.role === "note")
    return (
      <p className="entry note plain">
        <span className="label">Note</span> {message.text}
      </p>
    );
  if (message.interim)
    return (
      <div className="entry interim">
        <Prose text={message.text} />
      </div>
    );
  if (message.summary)
    return (
      <article className="entry answer summary">
        <p className="plain">{message.text}</p>
        <Receipt message={message} />
      </article>
    );
  return (
    <article className={"entry answer" + (message.stage ? " staged" : "")}>
      {message.stage && (
        <h3 className="label">
          Stage {message.stage} <StagePersona id={message.persona} />
        </h3>
      )}
      <Prose text={message.text} />
      {message.note && <p className="partial-note">{message.note}</p>}
      <Receipt message={message} />
    </article>
  );
});

function Stages() {
  const stages = useStudio((state) => state.stages);
  if (!stages.length) return null;
  return (
    <ol className="stages-strip" aria-label="Team stages">
      {stages.map((stage, index) => (
        <li key={stage.name} className={"stage-" + stage.status}>
          <span className="num">{index + 1}</span> {stage.name}
          <span className="stage-state">{stage.status.replace(/_/g, " ")}</span>
        </li>
      ))}
    </ol>
  );
}

function Live({ onGrow }) {
  const busy = useStudio((state) => state.busy);
  const draft = useStudio((state) => state.draft);
  const running = useStudio((state) => state.running);
  const runningSince = useStudio((state) => state.runningSince);
  const busySince = useStudio((state) => state.busySince);
  const waiting = useStudio((state) => !!state.pending);
  const stopping = useStudio((state) => state.stopping);
  useLayoutEffect(onGrow);
  if (!busy) return null;
  const name = KNOWN.find((tool) => running.startsWith(tool)) || "shell";
  const rest = running.startsWith(name) ? running.slice(name.length).trim() : running;
  return (
    <div className="live" aria-live="off">
      <Stages />
      {draft && (
        <div className="entry draft" aria-label="Reply in progress">
          <Prose text={draft} />
        </div>
      )}
      {running ? (
        <p className="step-line running">
          <i className={"swatch f-" + family(name)} aria-hidden="true" />
          <span className="pulse" aria-hidden="true" />
          <span className="step-name">{name}</span>
          <span className="step-rest">{rest}</span>
          {waiting ? (
            <span className="step-flag pending">needs approval</span>
          ) : (
            <>
              <span className="sr-only">running for</span>
              <Elapsed since={runningSince} />
            </>
          )}
        </p>
      ) : (
        !draft && (
          <p className="step-line running">
            <span className="pulse" aria-hidden="true" />
            <span className="step-rest">{stopping ? "Stopping the turn" : "Waiting for the model"}</span>
            <Elapsed since={busySince} />
          </p>
        )
      )}
    </div>
  );
}

function FirstRun({ onConnect, onStart, hasFile }) {
  const model = useStudio((state) => state.model);
  const workspace = useStudio((state) => state.workspace);
  const loaded = useStudio((state) => state.loaded);
  if (!loaded) return null;
  return (
    <div className="first-run">
      <h3>Work on {workspace}</h3>
      <ol>
        <li className={model ? "done" : "now"}>
          <b>Connect a model</b>
          {model ? (
            <span>
              Connected to <code>{model}</code>.
            </span>
          ) : (
            <>
              <span>Pick a provider and a model. Local servers need no key.</span>
              <button type="button" className="primary small" onClick={onConnect}>
                Connect a model
              </button>
            </>
          )}
        </li>
        <li className={model ? "now" : ""}>
          <b>Describe a task</b>
          <span>
            Write it below, or start from one of these:{" "}
            <button
              type="button"
              className="link"
              onClick={() => onStart("Review this workspace and report the most important problems, with file and line evidence.")}
            >
              review this workspace
            </button>
            {hasFile && (
              <>
                {", "}
                <button
                  type="button"
                  className="link"
                  onClick={() => onStart("Explain what this file does and how it fits into the project: ", true)}
                >
                  explain the open file
                </button>
              </>
            )}
            .
          </span>
        </li>
        <li>
          <b>Approve each command and edit</b>
          <span>Nothing runs and no file changes until you approve it. Edits arrive as a diff.</span>
        </li>
        <li>
          <b>Check the result</b>
          <span>Every edit from a turn is listed with its diff, and one control reverts them.</span>
        </li>
      </ol>
    </div>
  );
}

const Transcript = memo(function Transcript({ onConnect, onStart, hasFile }) {
  const messages = useStudio((state) => state.messages);
  const session = useStudio((state) => state.session);
  const busy = useStudio((state) => state.busy);
  const box = useRef(null);
  const follow = useRef(true);
  const seen = useRef(0);
  const [behind, setBehind] = useState(false);

  // Follow new output only while the reader is already at the end.
  function stick() {
    const node = box.current;
    if (!node) return;
    if (follow.current) node.scrollTop = node.scrollHeight;
    else if (node.scrollHeight - node.scrollTop - node.clientHeight > 80) setBehind(true);
  }

  useLayoutEffect(() => {
    if (messages.length > seen.current && messages.at(-1)?.role === "user") follow.current = true;
    seen.current = messages.length;
    stick();
  }, [messages]);

  useLayoutEffect(() => {
    follow.current = true;
    setBehind(false);
    stick();
  }, [session]);

  let turn = 0;
  return (
    <div className="transcript-wrap">
      <div
        className="transcript"
        ref={box}
        role="log"
        aria-label="Agent log"
        tabIndex={0}
        onScroll={() => {
          const node = box.current;
          follow.current = node.scrollHeight - node.scrollTop - node.clientHeight < 40;
          if (follow.current && behind) setBehind(false);
        }}
      >
        {!messages.length && !busy && <FirstRun onConnect={onConnect} onStart={onStart} hasFile={hasFile} />}
        {messages.map((message, index) => {
          if (message.role === "user") turn += 1;
          return <Entry key={index} message={message} turn={turn} onConnect={onConnect} />;
        })}
        <Live onGrow={stick} />
      </div>
      {behind && (
        <button
          type="button"
          className="jump"
          onClick={() => {
            follow.current = true;
            setBehind(false);
            box.current.scrollTop = box.current.scrollHeight;
          }}
        >
          Jump to latest
        </button>
      )}
    </div>
  );
});

const KIND = {
  shell: {
    heading: "Run this command in the workspace?",
    always: "Approve and allow this exact command for this session",
    name: "Command",
  },
  edit: {
    heading: "Apply this edit?",
    always: "Approve and allow edits for this session",
    name: "Edit",
  },
  mcp: {
    heading: "Call this MCP tool?",
    always: "Approve and allow this exact call for this session",
    name: "Tool call",
  },
};

function mcpDetail(detail) {
  const brace = detail.indexOf("{");
  if (brace < 0) return detail;
  try {
    return detail.slice(0, brace).trim() + "\n" + JSON.stringify(JSON.parse(detail.slice(brace)), null, 2);
  } catch {
    return detail;
  }
}

export const RISK = {
  R0: "read only, nothing to undo",
  R1: "reversible, undone exactly",
  R2: "compensable, undone approximately",
  R3: "reversible for a limited time",
  R4: "irreversible",
};

export const riskLevel = (value) => (typeof value === "number" ? "R" + value : String(value || ""));

function Assessment({ risk, policy }) {
  if (!risk && !policy) return null;
  const level = risk ? riskLevel(risk.reversibility_class) : "";
  return (
    <dl className="assessment">
      {policy && (
        <>
          <dt>Policy</dt>
          <dd>
            <span className={"verdict " + policy.kind}>{policy.kind}</span>
            {policy.rule ? (
              <>
                {" "}
                by rule <code>{policy.rule}</code>
              </>
            ) : (
              " by the policy default"
            )}
            {policy.reason ? ": " + policy.reason : ""}
            {policy.location ? " (" + policy.location + ")" : ""}
            {policy.irreversible && <span className="tag warn">irreversible</span>}
          </dd>
        </>
      )}
      {risk && (
        <>
          <dt>Unwind</dt>
          <dd>
            <span className={"risk " + level}>{level}</span> {RISK[level] || risk.class_label || ""}
            {level !== "R0" && risk.can_undo === true && (risk.undo_via ? `; undone with ${risk.undo_via}` : "; can be undone")}
            {level !== "R0" && risk.can_undo === false && "; cannot be undone"}
            {level !== "R0" && risk.fidelity ? ` (${risk.fidelity})` : ""}.
            {risk.blast_radius ? ` Reach: ${typeof risk.blast_radius === "string" ? risk.blast_radius : JSON.stringify(risk.blast_radius)}.` : ""}
          </dd>
        </>
      )}
    </dl>
  );
}

function Approval({ pending, report }) {
  const [sending, setSending] = useState(false);
  const node = useRef(null);
  const kind = KIND[pending.kind] || KIND.shell;
  const detail = pending.kind === "mcp" ? mcpDetail(pending.detail) : pending.detail;
  const isDiff = pending.kind === "edit" && /^(---|\+\+\+|@@) /m.test(detail);
  const stat = isDiff ? diffStat(detail) : null;

  useEffect(() => {
    setSending(false);
    // Bring the request to the keyboard unless the operator is in the middle of typing.
    const active = document.activeElement;
    const typing =
      active && (active.closest?.(".cm-editor") || active.tagName === "INPUT" || (active.tagName === "TEXTAREA" && active.value));
    if (!typing && !document.querySelector("dialog[open]")) node.current?.focus();
  }, [pending.id]);

  async function decide(approve, always = false) {
    setSending(true);
    try {
      await api("decision", { id: pending.id, approve, always });
    } catch (error) {
      setSending(false);
      report(error);
    }
  }

  return (
    <section className="approval" ref={node} tabIndex={-1} aria-label="Approval needed">
      <header className="approval-head">
        <span className="label warn">Approval needed</span>
        <h3>{pending.kind === "edit" && pending.title ? pending.title + "?" : kind.heading}</h3>
        <span className="approval-meta num">
          {stat ? `+${stat.added} −${stat.removed}` : `${plural(lines(detail), "line")}, ${plural(detail.length, "character")}`}
        </span>
      </header>
      <Assessment risk={pending.risk} policy={pending.policy} />
      <div className="approval-detail scroll-box" tabIndex={0} aria-label={kind.name + " to approve"}>
        {isDiff ? <DiffView text={detail} label={pending.title} /> : <pre className="output bare">{visible(detail)}</pre>}
      </div>
      <div className="approval-actions">
        <button type="button" className="primary" disabled={sending} onClick={() => decide(true)}>
          Approve
        </button>
        <button type="button" disabled={sending} onClick={() => decide(false)}>
          Reject
        </button>
        <button type="button" className="quiet" disabled={sending} onClick={() => decide(true, true)}>
          {kind.always}
        </button>
      </div>
      <p className="approval-note">
        The turn waits for your answer, with no time limit. Rejecting ends tool use for this turn and the agent reports
        what it has.
      </p>
    </section>
  );
}

function EditReview({ path, onPath, onClose, onOpen }) {
  const edits = useStudio((state) => state.edits);
  const [data, setData] = useState(null);
  const [error, setError] = useState("");
  useEffect(() => {
    let live = true;
    setData(null);
    setError("");
    api("edit_diff", { path }).then(
      (result) => live && setData(result),
      (failure) => live && setError(failure.message),
    );
    return () => {
      live = false;
    };
  }, [path]);
  const index = edits.findIndex((item) => item.path === path);
  return (
    <Dialog
      title={(data?.created ? "New file " : "Edits to ") + path}
      wide
      className="tall"
      onClose={onClose}
      footer={
        <>
          <span className="reader-meta">
            {edits.length > 1 ? `File ${index + 1} of ${edits.length} edited in this turn` : "The only file edited in this turn"}
          </span>
          {edits.length > 1 && (
            <>
              <button type="button" disabled={index <= 0} onClick={() => onPath(edits[index - 1].path)}>
                Previous file
              </button>
              <button type="button" disabled={index < 0 || index >= edits.length - 1} onClick={() => onPath(edits[index + 1].path)}>
                Next file
              </button>
            </>
          )}
          <button
            type="button"
            onClick={() => {
              onClose();
              onOpen(path);
            }}
          >
            Open in editor
          </button>
        </>
      }
    >
      {error ? (
        <p className="problem" role="alert">
          {error}
        </p>
      ) : !data ? (
        <p className="note-line">Reading the edit</p>
      ) : (
        <DiffPanel
          name={path}
          original={data.original}
          modified={data.modified}
          diff={data.diff}
          before="Before the turn"
          after="After the turn"
        />
      )}
    </Dialog>
  );
}

const Edits = memo(function Edits({ report, onOpen }) {
  const edits = useStudio((state) => state.edits);
  const busy = useStudio((state) => state.busy);
  const session = useStudio((state) => state.session);
  const [viewing, setViewing] = useState(null);
  const [asking, setAsking] = useState(false);
  const [kept, setKept] = useState([]);
  useEffect(() => setKept([]), [session, busy]);
  if (!edits.length) return null;
  const added = edits.reduce((sum, item) => sum + item.added, 0);
  const removed = edits.reduce((sum, item) => sum + item.removed, 0);

  async function revert() {
    setAsking(false);
    try {
      const outcome = await api("revert", {});
      setKept(outcome.conflicts);
    } catch (error) {
      report(error);
    }
  }

  return (
    <section className="changes" aria-label="Edits from the latest turn">
      <header>
        <h3 className="label">Edits from the latest turn</h3>
        <span className="num">
          {plural(edits.length, "file")} <span className="add">+{added}</span> <span className="del">−{removed}</span>
        </span>
        <button
          type="button"
          className="small"
          disabled={busy}
          title={busy ? "Wait for the current turn to finish" : "Restore these files to their content before the turn"}
          onClick={() => setAsking(true)}
        >
          Revert edits
        </button>
      </header>
      <ul>
        {edits.map((item) => (
          <li key={item.path}>
            <button type="button" className="link change-path" title="Review this edit" onClick={() => setViewing(item.path)}>
              {item.path}
            </button>
            {item.created && <span className="tag">new file</span>}
            {kept.includes(item.path) && <span className="tag warn">not restored: changed since the edit</span>}
            <span className="num change-stat">
              <span className="add">+{item.added}</span> <span className="del">−{item.removed}</span>
            </span>
          </li>
        ))}
      </ul>
      {viewing && <EditReview path={viewing} onPath={setViewing} onClose={() => setViewing(null)} onOpen={onOpen} />}
      {asking && (
        <Confirm title="Revert the agent's edits" action="Revert edits" onClose={() => setAsking(false)} onConfirm={revert}>
          <p>
            Restores {plural(edits.length, "file")} to the content from before the latest turn. A file you changed
            after the agent edited it is left as it is, and listed.
          </p>
        </Confirm>
      )}
    </section>
  );
});

function Sessions({ onClose }) {
  const busy = useStudio((state) => state.busy);
  const [rows, setRows] = useState(null);
  const [error, setError] = useState("");
  useEffect(() => {
    api("sessions").then(setRows, (failure) => setError(failure.message));
  }, []);
  async function act(action, data) {
    try {
      await api(action, data);
      onClose();
    } catch (failure) {
      setError(failure.message);
    }
  }
  return (
    <Dialog
      title="Sessions"
      wide
      onClose={onClose}
      footer={
        <>
          <span className="reader-meta">Sessions are transcripts in .turnloom inside the workspace.</span>
          <button type="button" className="primary" disabled={busy} onClick={() => act("reset", {})}>
            New session
          </button>
        </>
      }
    >
      {busy && <p className="note-line">A turn is running. Stop it or let it finish before switching sessions.</p>}
      {error && (
        <p className="problem" role="alert">
          {error}
        </p>
      )}
      {rows === null && !error ? (
        <p className="note-line">Reading sessions</p>
      ) : rows && !rows.length ? (
        <div className="empty">
          <p className="empty-title">No saved sessions</p>
          <p>A session is saved when you send its first task. It can be resumed here later with its full history.</p>
        </div>
      ) : (
        rows && (
          <table className="grid sessions">
            <thead>
              <tr>
                <th scope="col">First request</th>
                <th scope="col" className="num">
                  Turns
                </th>
                <th scope="col">Last used</th>
                <th scope="col">
                  <span className="sr-only">Action</span>
                </th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => (
                <tr key={row.id} className={row.current ? "current" : ""}>
                  <td>
                    <span className="session-task">{row.task}</span>
                    <span className="session-id num">{row.id.slice(0, 8)}</span>
                  </td>
                  <td className="num">{row.turns}</td>
                  <td>{ago(row.time)}</td>
                  <td>
                    {row.current ? (
                      <span className="tag">open now</span>
                    ) : (
                      <button
                        type="button"
                        className="small"
                        disabled={busy}
                        aria-label={"Resume session: " + row.task}
                        onClick={() => act("resume", { id: row.id })}
                      >
                        Resume
                      </button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )
      )}
    </Dialog>
  );
}

const GATE = { confirm: "confirm each call", policy: "policy decides", off: "off" };

const Composer = memo(
  forwardRef(function Composer({ openPath, onConnect, onView, report }, handle) {
    const model = useStudio((state) => state.model);
    const busy = useStudio((state) => state.busy);
    const stopping = useStudio((state) => state.stopping);
    const persona = useStudio((state) => state.picked || state.persona);
    const settings = useStudio((state) => state.settings);
    const gate = useStudio((state) => state.gate);
    const always = useStudio((state) => state.always);
    const serversOn = useStudio((state) => state.servers_on);
    const catalog = usePersonas().data;
    const [text, setText] = useState("");
    const [readOnly, setReadOnly] = useState(false);
    const [sending, setSending] = useState(false);
    const [confirming, setConfirming] = useState(false);
    const area = useRef(null);
    const team = settings.mode === "team";

    const choices = useMemo(() => {
      const rows = catalog?.personas || [];
      const featured = new Set(catalog?.featured || []);
      const shown = rows.filter((row) => featured.has(row.id) || row.division === "custom" || row.id === persona);
      return shown.length ? shown : [{ id: persona, name: personaTitle(persona) }];
    }, [catalog, persona]);

    useImperativeHandle(handle, () => ({
      fill(value) {
        setText(value);
        area.current.focus();
        requestAnimationFrame(() => area.current?.setSelectionRange(value.length, value.length));
      },
    }));

    async function send() {
      if (!model) return onConnect();
      if (!text.trim() || sending || busy) return;
      setSending(true);
      try {
        await api("run", { task: text.trim(), persona, readOnly });
        setText("");
      } catch (error) {
        // The server names the Policy view when a policy file is present but its gate cannot run.
        report(error, /Policy view/.test(error.message) ? "policy" : undefined);
      } finally {
        setSending(false);
      }
    }

    function mention() {
      const node = area.current;
      const start = node.selectionStart ?? text.length;
      const end = node.selectionEnd ?? text.length;
      const before = text.slice(0, start);
      const insert = (before && !/\s$/.test(before) ? " " : "") + openPath + " ";
      setText(before + insert + text.slice(end));
      node.focus();
      requestAnimationFrame(() => node.setSelectionRange(start + insert.length, start + insert.length));
    }

    const set = (patch) => configure(patch).catch(report);

    return (
      <form
        className="composer"
        aria-label="New task"
        onSubmit={(event) => {
          event.preventDefault();
          send();
        }}
      >
        {settings.auto_approve && (
          <p className="banner warn slim" role="status">
            Auto-approve is on. Commands, edits and tool calls run without asking.
          </p>
        )}
        <textarea
          ref={area}
          aria-label="Task for the agent"
          aria-describedby="composer-hint"
          placeholder={model ? "Describe a task for the agent" : "Connect a model, then describe a task"}
          rows={3}
          value={text}
          maxLength={32000}
          onChange={(event) => setText(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
              event.preventDefault();
              send();
            }
          }}
        />
        <div className="composer-options">
          {team ? (
            <span className="field-inline">
              <span>Team</span>
              <button type="button" className="link" title="Edit the team in the Agents view" onClick={() => onView("agents")}>
                {settings.team.map((stage) => stage.name).join(", ")}
              </button>
            </span>
          ) : (
            <label className="field-inline">
              <span>Persona</span>
              <select
                value={persona}
                disabled={busy}
                onChange={(event) =>
                  event.target.value === "" ? onView("agents") : studio.set({ picked: event.target.value })
                }
              >
                {choices.map((row) => (
                  <option key={row.id} value={row.id}>
                    {row.name}
                  </option>
                ))}
                <option value="">All personas</option>
              </select>
            </label>
          )}
          <label className="check" title="The agent gets read and search tools only: no edits, commands or MCP tools">
            <input type="checkbox" checked={readOnly} disabled={busy} onChange={(event) => setReadOnly(event.target.checked)} />
            Read-only turn
          </label>
          <label className="check" title="Apply file edits without asking, in every session of this workspace. Commands and tool calls still need approval">
            <input
              type="checkbox"
              checked={settings.auto_edit}
              disabled={busy || settings.auto_approve}
              onChange={(event) => set({ auto_edit: event.target.checked })}
            />
            Auto-approve edits
          </label>
          <label className="check" title="Run every command, edit and tool call without asking">
            <input
              type="checkbox"
              checked={settings.auto_approve}
              disabled={busy}
              onChange={(event) => (event.target.checked ? setConfirming(true) : set({ auto_approve: false }))}
            />
            Auto-approve everything
          </label>
        </div>
        {(serversOn.length > 0 || gate.active || always.edits || always.requests > 0) && (
          <p className="composer-context">
            {always.edits && !settings.auto_edit && !settings.auto_approve && (
              <span className="attention" title="You chose this on an edit approval. A new session clears it">
                Edits allowed for this session
              </span>
            )}
            {always.requests > 0 && !settings.auto_approve && (
              <span title="Exact commands or tool calls you allowed on an approval. A new session clears them">
                {plural(always.requests, "exact request")} allowed for this session
              </span>
            )}
            {serversOn.length > 0 && (
              <button type="button" className="link" onClick={() => onView("tools")}>
                MCP servers on: {serversOn.join(", ")}
              </button>
            )}
            {gate.active && (
              <button type="button" className="link" onClick={() => onView("policy")}>
                Policy gate: {GATE[gate.mode] || gate.mode}
              </button>
            )}
          </p>
        )}
        <div className="composer-send">
          <span className="hint" id="composer-hint">
            {busy ? "A turn is running. Write the next task meanwhile." : "Enter sends. Shift+Enter adds a line."}
          </span>
          <button
            type="button"
            className="small quiet"
            disabled={!openPath}
            title={openPath ? "Insert the path of the open file into the task" : "Open a file first"}
            onClick={mention}
          >
            Mention open file
          </button>
          {busy ? (
            <button type="button" className="stop" disabled={stopping} onClick={() => api("stop").catch(report)}>
              {stopping ? "Stopping" : "Stop turn"}
            </button>
          ) : model ? (
            <button type="submit" className="primary" disabled={!text.trim() || sending}>
              {team ? "Send to team" : "Send"}
            </button>
          ) : (
            <button type="button" className="primary" onClick={onConnect}>
              Connect a model
            </button>
          )}
        </div>
        {confirming && (
          <Confirm
            title="Auto-approve everything"
            action="Turn on auto-approve"
            danger
            onClose={() => setConfirming(false)}
            onConfirm={() => {
              setConfirming(false);
              set({ auto_approve: true });
            }}
          >
            <p>
              Every shell command, file edit and MCP tool call the agent asks for will run at once, without showing it
              to you first.
            </p>
            <p>
              A policy gate still refuses what its rules deny. Edits can be reverted after the turn; commands cannot.
              The setting is saved with this workspace until you turn it off.
            </p>
          </Confirm>
        )}
      </form>
    );
  }),
);

export const Agent = memo(function Agent({ openPath, onConnect, onOpen, onView, report }) {
  const pending = useStudio((state) => state.pending);
  const busy = useStudio((state) => state.busy);
  const composer = useRef(null);
  const [sessions, setSessions] = useState(false);
  const path = useRef(openPath);
  path.current = openPath;
  const start = useMemo(
    () => (text, withFile) => composer.current?.fill(withFile && path.current ? text + path.current : text),
    [],
  );
  return (
    <section className="pane agent" aria-label="Agent">
      <header className="bar">
        <h2 className="label">Agent log</h2>
        <span className="bar-tools">
          <button type="button" className="small quiet" onClick={() => setSessions(true)}>
            Sessions
          </button>
          <button
            type="button"
            className="small quiet"
            disabled={busy}
            title={busy ? "Wait for the current turn to finish" : "Start an empty session. This one stays resumable"}
            onClick={() => api("reset", {}).catch(report)}
          >
            New session
          </button>
        </span>
      </header>
      <Transcript onConnect={onConnect} onStart={start} hasFile={!!openPath} />
      {pending && <Approval pending={pending} report={report} />}
      <Edits report={report} onOpen={onOpen} />
      <Composer ref={composer} openPath={openPath} onConnect={onConnect} onView={onView} report={report} />
      {sessions && <Sessions onClose={() => setSessions(false)} />}
    </section>
  );
});
