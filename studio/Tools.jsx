import React, { useEffect, useRef, useState } from "react";
import {
  BadgeCheck,
  Bookmark,
  CircleAlert,
  FileSearch,
  History,
  Play,
  Plug,
  RefreshCw,
  RotateCcw,
  ShieldCheck,
  Undo2,
} from "lucide-react";
import { ago, api, percent, useLoad } from "./api.js";

const CLASSES = {
  R0: ["Read only", "No effect to undo"],
  R1: ["Reversible", "Undone exactly"],
  R2: ["Compensable", "Undone approximately"],
  R3: ["Time-limited", "Reversible for a while"],
  R4: ["Irreversible", "Cannot be undone"],
};

export function RiskBadge({ level, label }) {
  const info = CLASSES[level] || [label || level, ""];
  return (
    <span className={"risk " + level} title={info[1]}>
      {level} · {label ? label[0].toUpperCase() + label.slice(1) : info[0]}
    </span>
  );
}

function Missing({ what }) {
  return (
    <div className="blank tall">
      <Plug />
      <p>
        {what} needs the companion packages. Install them with{" "}
        <code>python -m pip install "turnloom[studio]"</code> and restart
        Studio.
      </p>
    </div>
  );
}

export function ToolsView({ state, report }) {
  const [data, reload] = useLoad("servers", {}, state.settings.servers.join()),
    [tools, setTools] = useState({}),
    [loading, setLoading] = useState("");
  const enabled = state.settings.servers;
  async function discover(name) {
    setLoading(name);
    try {
      const list = await api("server_tools", { name });
      setTools((old) => ({ ...old, [name]: list }));
      reload();
    } catch (e) {
      report(e);
    } finally {
      setLoading("");
    }
  }
  const toggle = (name, on) =>
    api("settings", {
      servers: on ? [...enabled, name] : enabled.filter((x) => x !== name),
    }).catch(report);
  return (
    <div className="page">
      <header className="pagehead">
        <div>
          <h1>Tools</h1>
          <p>
            Model Context Protocol servers the agent can search and call. Each
            runs as a local process and every call asks for your approval.
            Servers marked <b>Unwind</b> are routed through the reversibility
            proxy, so their actions are classified, logged and undoable where
            the server allows it.
          </p>
        </div>
        <button onClick={reload} aria-label="Refresh servers">
          <RefreshCw size={14} /> Refresh
        </button>
      </header>
      {data?.error && <p className="error" role="alert">{data.error}</p>}
      <div className="servers">
        {(data?.servers || []).map((s) => (
          <section className="card server" key={s.name}>
            <div className="row">
              <h2>
                <Plug size={15} /> {s.name}
                {s.bundled && <i className="tag">Bundled</i>}
                {s.unwind && (
                  <i className="tag mint">
                    <ShieldCheck size={11} /> Unwind
                  </i>
                )}
                {s.running && <i className="tag live">Running</i>}
              </h2>
              <label className="toggle">
                <input
                  type="checkbox"
                  checked={s.enabled}
                  disabled={state.busy}
                  aria-label={"Give the agent " + s.name}
                  onChange={(e) => toggle(s.name, e.target.checked)}
                />
                <span>{s.enabled ? "Available to the agent" : "Off"}</span>
              </label>
            </div>
            <code className="command">{s.command}</code>
            {tools[s.name] ? (
              <ul className="toollist">
                {tools[s.name].map((t) => (
                  <li key={t.name}>
                    <b className="mono">{t.name}</b>
                    <span>{t.description.split("\n")[0]}</span>
                  </li>
                ))}
              </ul>
            ) : (
              <button className="ghost" disabled={!!loading} onClick={() => discover(s.name)}>
                <Play size={13} />
                {loading === s.name ? "Starting server…" : "Start and list tools"}
              </button>
            )}
          </section>
        ))}
      </div>
      {data && !data.servers.length && !data.error && (
        <p className="hint">No servers yet.</p>
      )}
      <section className="card">
        <h2>Add a server</h2>
        <p className="sub">
          Create <code>.turnloom/mcp.json</code> in this workspace. Add{" "}
          <code>"unwind": true</code> to route a server through Unwind. Treat
          the file as executable configuration: Studio starts each command.
        </p>
        <pre className="snippet">{`{
  "mcpServers": {
    "git": { "command": "mcp-server-git", "args": ["--repository", "."], "unwind": true },
    "time": { "command": "mcp-server-time" }
  }
}`}</pre>
      </section>
    </div>
  );
}

function Highlighted({ text, fields, active, select }) {
  const refs = useRef({});
  useEffect(() => {
    refs.current[active]?.scrollIntoView({ block: "center", behavior: "smooth" });
  }, [active]);
  const spans = fields
    .filter((f) => f.grounding?.char_span && f.grounding.page === 0)
    .map((f) => ({ start: f.grounding.char_span[0], end: f.grounding.char_span[1], field: f }))
    .filter((s) => s.end > s.start && s.end <= text.length)
    .sort((a, b) => a.start - b.start);
  const parts = [];
  let cursor = 0;
  for (const span of spans) {
    if (span.start < cursor) continue;
    parts.push(text.slice(cursor, span.start));
    parts.push(
      <mark
        key={span.field.path}
        ref={(node) => (refs.current[span.field.path] = node)}
        className={span.field.decision + (active === span.field.path ? " active" : "")}
        title={span.field.path}
        onClick={() => select(span.field.path)}
      >
        {text.slice(span.start, span.end)}
      </mark>,
    );
    cursor = span.end;
  }
  parts.push(text.slice(cursor));
  return <pre className="source">{parts}</pre>;
}

export function DocumentsView({ state, file, report }) {
  const [meta] = useLoad("schemas", {}, "once"),
    [source, setSource] = useState("file"),
    [path, setPath] = useState(file?.path || ""),
    [text, setText] = useState(""),
    [schema, setSchema] = useState(""),
    [template, setTemplate] = useState(""),
    [threshold, setThreshold] = useState(0.8),
    [running, setRunning] = useState(false),
    [result, setResult] = useState(null),
    [active, setActive] = useState(""),
    [error, setError] = useState("");
  useEffect(() => {
    if (meta && !schema) pick(Object.keys(meta.schemas)[0], meta);
  }, [meta]);
  useEffect(() => {
    if (file && !path) setPath(file.path);
  }, [file]);
  function pick(name, from = meta) {
    setTemplate(name);
    setSchema(JSON.stringify(from.schemas[name], null, 2));
  }
  async function run() {
    setRunning(true);
    setError("");
    try {
      JSON.parse(schema);
      const data = await api("verify", {
        schema,
        threshold,
        ...(source === "file" ? { path } : { text }),
      });
      setResult(data);
      setActive("");
    } catch (e) {
      setError(e.name === "SyntaxError" ? "The schema is not valid JSON: " + e.message : e.message);
    } finally {
      setRunning(false);
    }
  }
  if (meta && !meta.installed) return <Missing what="Document verification" />;
  const fields = result?.fields || [];
  return (
    <div className="page">
      <header className="pagehead">
        <div>
          <h1>Documents</h1>
          <p>
            <b>VerifyDoc</b> extracts fields against a schema and scores each
            one: a calibrated confidence, where in the source it was read, and
            whether to accept it or send it to review. Runs locally through{" "}
            <b>Unwind</b> with no model call.
          </p>
        </div>
      </header>
      <div className="grid2 docs">
        <section className="card">
          <h2><FileSearch size={15} /> Document</h2>
          <div className="segmented" role="group" aria-label="Document source">
            {[["file", "Workspace file"], ["text", "Pasted text"]].map(([key, label]) => (
              <button key={key} className={source === key ? "on" : ""} aria-pressed={source === key} onClick={() => setSource(key)}>
                {label}
              </button>
            ))}
          </div>
          {source === "file" ? (
            <label className="field wide">
              <span>Path in workspace<small>text, Markdown or PDF</small></span>
              <input value={path} onChange={(e) => setPath(e.target.value)} placeholder="docs/invoice.txt" aria-label="Document path" />
            </label>
          ) : (
            <textarea className="paste" value={text} onChange={(e) => setText(e.target.value)} placeholder="Paste the document text here" aria-label="Document text" />
          )}
          <label className="field wide">
            <span>Auto-accept at confidence<small>{Math.round(threshold * 100)}% and above</small></span>
            <input type="range" min="0.5" max="0.99" step="0.01" value={threshold} onChange={(e) => setThreshold(+e.target.value)} aria-label="Acceptance threshold" />
          </label>
        </section>
        <section className="card">
          <div className="row">
            <h2>Schema</h2>
            <div className="chips">
              {Object.keys(meta?.schemas || {}).map((name) => (
                <button key={name} className={template === name ? "on" : ""} onClick={() => pick(name)}>{name}</button>
              ))}
            </div>
          </div>
          <textarea className="schema mono" spellCheck="false" value={schema} aria-label="JSON Schema"
            onChange={(e) => { setSchema(e.target.value); setTemplate(""); }} />
        </section>
      </div>
      <div className="row end">
        {error && <p className="error" role="alert">{error}</p>}
        <button className="primary" disabled={running || state.busy || !(source === "file" ? path.trim() : text.trim())} onClick={run}>
          <BadgeCheck size={15} /> {running ? "Verifying…" : "Verify document"}
        </button>
      </div>
      {result && (
        <div className="grid2 docs">
          <section className="card">
            <div className="row">
              <h2>Fields</h2>
              <span className="verdict">
                <i className="tag mint">{result.n_accepted} accepted</i>
                <i className={"tag " + (result.n_review ? "warn" : "")}>{result.n_review} to review</i>
              </span>
            </div>
            <table className="table extracted">
              <thead><tr><th>Field</th><th>Value</th><th>Confidence</th><th>Decision</th></tr></thead>
              <tbody>
                {fields.map((f) => (
                  <tr key={f.path} className={active === f.path ? "active" : ""} onClick={() => setActive(f.path)}>
                    <td className="mono">{f.path}</td>
                    <td>{f.value == null || f.value === "" ? <em>not found</em> : String(f.value)}</td>
                    <td>
                      <span className="confidence"><i style={{ width: f.confidence * 100 + "%" }} className={f.decision} /></span>
                      {Math.round(f.confidence * 100)}%
                    </td>
                    <td>
                      <span className={"decision " + f.decision}>
                        {f.decision === "accept" ? <BadgeCheck size={13} /> : <CircleAlert size={13} />}
                        {f.decision === "accept" ? "Accept" : "Review"}
                      </span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            <p className="sub">{result.summary}</p>
          </section>
          <section className="card">
            <h2>Source grounding</h2>
            {result.text ? (
              <Highlighted text={result.text} fields={fields} active={active} select={setActive} />
            ) : (
              <p className="hint">
                This document has no text preview here. Grounding is reported as
                page and position:{" "}
                {fields.filter((f) => f.grounding).map((f) => `${f.path} on page ${f.grounding.page + 1}`).join(", ") || "none found"}.
              </p>
            )}
          </section>
        </div>
      )}
    </div>
  );
}

export function LedgerView({ state, report }) {
  const [tick, setTick] = useState(0),
    [data, reload] = useLoad("ledger", {}, tick + ":" + state.busy),
    [checkpoints, setCheckpoints] = useState([]),
    [label, setLabel] = useState(""),
    [working, setWorking] = useState(""),
    [outcome, setOutcome] = useState(null);
  async function act(name, action, payload) {
    setWorking(name);
    try {
      const result = await api(action, payload);
      if (action === "checkpoint") {
        setCheckpoints((old) => [result, ...old]);
        setLabel("");
      } else setOutcome(result.undone || []);
      setTick((n) => n + 1);
    } catch (e) {
      report(e);
    } finally {
      setWorking("");
    }
  }
  if (!state.companions) return <Missing what="The undo ledger" />;
  const entries = data?.entries || [],
    undoable = entries.filter((e) => e.status === "active" && e.class !== "R0").length;
  return (
    <div className="page">
      <header className="pagehead">
        <div>
          <h1>Undo ledger</h1>
          <p>
            <b>Unwind</b> records every tool call that passes through it with a
            reversibility class, and can take actions back newest first. The
            log lives at <code>.turnloom/unwind.db</code> and survives
            restarts.
          </p>
        </div>
        <button onClick={reload} aria-label="Refresh ledger"><RefreshCw size={14} /> Refresh</button>
      </header>
      <div className="kpis five">
        {Object.entries(CLASSES).map(([level, [name, detail]]) => (
          <div className={"kpi " + level} key={level}>
            <span className="kpi-label"><RiskBadge level={level} /></span>
            <strong>{entries.filter((e) => e.class === level).length}</strong>
            <small>{detail}</small>
          </div>
        ))}
      </div>
      <div className="grid2 ledger">
        <section className="card">
          <h2><History size={15} /> Recorded actions</h2>
          <table className="table">
            <thead><tr><th>When</th><th>Tool</th><th>Class</th><th>Status</th><th>Undo with</th></tr></thead>
            <tbody>
              {entries.map((e) => (
                <tr key={e.id} title={e.arguments}>
                  <td>{ago(e.time)}</td>
                  <td className="mono">{e.server}/{e.tool}</td>
                  <td><RiskBadge level={e.class} /></td>
                  <td><span className={"state " + e.status}>{e.status.replace(/_/g, " ")}</span></td>
                  <td className="mono">{e.inverse || "–"}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {!entries.length && (
            <p className="hint">
              Nothing recorded yet. Read-only calls such as document
              verification leave no entry, because there is nothing to undo.
              Actions that change something appear here.
            </p>
          )}
        </section>
        <section className="card">
          <h2><Undo2 size={15} /> Take actions back</h2>
          <p className="sub">
            Undo runs each action's inverse through the same server.{" "}
            {undoable} recorded {undoable === 1 ? "action is" : "actions are"} still undoable.
          </p>
          <div className="row left">
            <button className="primary" disabled={!!working || state.busy || !undoable} onClick={() => act("undo", "undo", { n: 1 })}>
              <RotateCcw size={14} /> {working === "undo" ? "Undoing…" : "Undo last action"}
            </button>
          </div>
          <h3><Bookmark size={14} /> Checkpoints</h3>
          <p className="sub">Mark this point, then return to it later. Checkpoints last until Studio closes.</p>
          <div className="row left">
            <input value={label} onChange={(e) => setLabel(e.target.value)} placeholder="Label, for example before refactor" aria-label="Checkpoint label" />
            <button disabled={!!working || state.busy} onClick={() => act("checkpoint", "checkpoint", { label })}>
              {working === "checkpoint" ? "Saving…" : "Create checkpoint"}
            </button>
          </div>
          <ul className="checkpoints">
            {checkpoints.map((c) => (
              <li key={c.checkpoint_id}>
                <span><b>{c.label}</b><small>{ago(c.ts)}</small></span>
                <button disabled={!!working || state.busy} onClick={() => act(c.checkpoint_id, "undo", { checkpoint: c.checkpoint_id })}>
                  {working === c.checkpoint_id ? "Undoing…" : "Undo back to here"}
                </button>
              </li>
            ))}
          </ul>
          {outcome && (
            <div className="outcome" role="status">
              {outcome.length ? outcome.map((o, i) => (
                <p key={i}><b className="mono">{o.tool}</b> {o.outcome.replace(/_/g, " ")}{o.reason ? ": " + o.reason : ""}</p>
              )) : <p>Nothing to undo since that point.</p>}
            </div>
          )}
        </section>
      </div>
    </div>
  );
}
