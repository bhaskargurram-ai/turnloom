import React, { useEffect, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import Markdown from "react-markdown";
import {
  ArrowUp,
  BookOpen,
  Check,
  ChevronDown,
  ChevronRight,
  CircleDot,
  Code2,
  FileCode2,
  FileText,
  Folder,
  FolderOpen,
  GitPullRequest,
  Layers3,
  Link2,
  RefreshCw,
  Save,
  Settings2,
  Sparkles,
  SquareTerminal,
  X,
} from "lucide-react";
import { basicSetup, EditorView } from "codemirror";
import { oneDark } from "@codemirror/theme-one-dark";
import { python } from "@codemirror/lang-python";
import { javascript } from "@codemirror/lang-javascript";
import { markdown } from "@codemirror/lang-markdown";
import { AgentApprovalCard } from "./node_modules/@cline/ui/dist/components/agent-approval-card.js";
import { AgentCommandOutput } from "./node_modules/@cline/ui/dist/components/agent-command-output.js";
import { AgentConversationHeader } from "./node_modules/@cline/ui/dist/components/agent-conversation-layout.js";
import "./studio.css";
import "./loom.css";

const token =
  location.hash.slice(1) || sessionStorage.getItem("studio-session");
if (token) sessionStorage.setItem("studio-session", token);
history.replaceState(null, "", location.pathname);
async function api(action, data = {}) {
  const response = await fetch("/api/" + action, {
    method: "POST",
    headers: {
      Authorization: "Bearer " + token,
      "Content-Type": "application/json",
    },
    body: JSON.stringify(data),
  });
  if (!response.ok) {
    const failure = await response.json().catch(() => ({}));
    throw Error(
      failure.error || "Studio session unavailable. Reopen the launcher.",
    );
  }
  return response.json();
}
function Mark() {
  return (
    <span className="mark">
      <Layers3 size={23} />
    </span>
  );
}
function FileTree({ nodes, choose, active, depth = 0, report }) {
  const [expanded, setExpanded] = useState({});
  async function click(node) {
    if (!node.dir) return choose(node.path);
    const children = expanded[node.path]
      ? null
      : await api("tree", { path: node.path });
    setExpanded((old) => ({ ...old, [node.path]: children }));
  }
  return nodes.map((node) => (
    <React.Fragment key={node.path}>
      <button
        className={"file " + (active === node.path ? "selected" : "")}
        style={{ paddingLeft: 12 + depth * 12 }}
        title={node.path}
        onClick={() => click(node).catch(report)}
      >
        {node.dir ? (
          expanded[node.path] ? (
            <ChevronDown size={12} />
          ) : (
            <ChevronRight size={12} />
          )
        ) : (
          <span className="spacer" />
        )}
        {node.dir ? (
          <Folder size={15} />
        ) : node.name.endsWith(".md") ? (
          <BookOpen size={15} />
        ) : (
          <FileCode2 size={15} />
        )}
        <span>{node.name}</span>
      </button>
      {expanded[node.path] && (
        <FileTree
          nodes={expanded[node.path]}
          choose={choose}
          depth={depth + 1}
          active={active}
          report={report}
        />
      )}
    </React.Fragment>
  ));
}
function Editor({ file, onChange, content }) {
  const host = useRef(null);
  useEffect(() => {
    if (!file) return;
    const extension = file.path.split(".").pop();
    const lang =
      extension === "py"
        ? python()
        : extension === "md"
          ? markdown()
          : ["js", "jsx", "ts", "tsx"].includes(extension)
            ? javascript({ jsx: true, typescript: extension.startsWith("ts") })
            : [];
    const view = new EditorView({
      parent: host.current,
      doc: file.text,
      extensions: [
        basicSetup,
        oneDark,
        lang,
        EditorView.lineWrapping,
        EditorView.updateListener.of((update) => {
          if (update.docChanged) {
            content.current = update.state.doc.toString();
            onChange(true);
          }
        }),
      ],
    });
    return () => view.destroy();
  }, [file]);
  return <div className="editor" ref={host} />;
}

function ContextLoom({ state, report }) {
  const [expanded, setExpanded] = useState(true),
    [inspect, setInspect] = useState(null);
  const rows = state.loom || [],
    total = rows.reduce((sum, r) => sum + r.before, 0),
    kept = rows.reduce((sum, r) => sum + r.after, 0);
  const dropped = Math.max(0, total - kept),
    visible = rows.slice(-12),
    height = 106;
  async function recall(row, offset = 0) {
    const result = await api("recall", {
      sample: !!state.sample,
      reference: row.reference,
      offset,
      length: 750,
    });
    if (result.error) throw Error(result.error);
    setInspect({ row, ...result });
  }
  return (
    <section className="loom">
      <div className="loomhead">
        <span>
          <Layers3 size={15} />
          Context Loom
        </span>
        <small>{state.sample ? "Sample run" : "Live context"}</small>
        <button
          onClick={() => setExpanded(!expanded)}
          aria-label={expanded ? "Collapse loom" : "Expand loom"}
        >
          {expanded ? <ChevronDown size={15} /> : <ChevronRight size={15} />}
        </button>
      </div>
      {expanded && (
        <div className="loomgrid">
          <div className="loomstat">
            <strong>
              {total ? Math.round((dropped * 100) / total) : 0}
              <em>%</em>
            </strong>
            <span>less tool text in prompt</span>
            <div>{dropped.toLocaleString()} characters folded</div>
          </div>
          <div className="chart">
            <div className="axis">
              <span>Tool output</span>
              <span>In prompt</span>
            </div>
            <svg
              viewBox={"0 0 320 " + height}
              preserveAspectRatio="none"
              role="img"
              aria-label="Widths show original and retained tool text"
            >
              {visible.map((r, i) => {
                const y = ((i + 0.5) * height) / visible.length,
                  thick = Math.min(
                    (height / visible.length) * 0.76,
                    2 + Math.sqrt(r.before) * 0.17,
                  );
                const end = Math.max(
                    1.2,
                    (thick * r.after) / Math.max(1, r.before),
                  ),
                  color = ["#87dfc8", "#afadf2", "#e3b783", "#79bdcf"][i % 4];
                return (
                  <path
                    key={r.id}
                    d={`M0 ${y - thick / 2} C100 ${y - thick / 2} 150 ${y - end / 2} 230 ${y - end / 2} L320 ${y - end / 2} V${y + end / 2} H230 C150 ${y + end / 2} 100 ${y + thick / 2} 0 ${y + thick / 2}Z`}
                    fill={color}
                  >
                    <title>
                      {r.tool}: {r.before} → {r.after} characters
                    </title>
                  </path>
                );
              })}
            </svg>
          </div>
          <div className="refs">
            {rows
              .filter((r) => r.reference)
              .slice(-5)
              .map((r) => (
                <button
                  key={r.id}
                  onClick={() => recall(r).catch(report)}
                  title="Read archived result"
                >
                  <Link2 size={12} />
                  {r.tool} <span>{r.reference.slice(0, 5)}</span>
                </button>
              ))}
            {!rows.some((r) => r.reference) && (
              <small>Recall links appear as older tool output folds.</small>
            )}
          </div>
        </div>
      )}
      {inspect && (
        <div className="scrim">
          <section
            className="dialog readback"
            role="dialog"
            aria-label="Archived result"
          >
            <div className="row">
              <h2>{inspect.row.tool} result</h2>
              <button
                aria-label="Close archived result"
                onClick={() => setInspect(null)}
              >
                <X />
              </button>
            </div>
            <small>
              {state.sample ? "Sample archive" : "Local archive"} ·{" "}
              {inspect.row.reference} · {inspect.total.toLocaleString()}{" "}
              characters
            </small>
            <pre>{inspect.text}</pre>
            <div className="row">
              <button
                disabled={!inspect.offset}
                onClick={() =>
                  recall(inspect.row, Math.max(0, inspect.offset - 750)).catch(
                    report,
                  )
                }
              >
                Previous
              </button>
              <span>
                {inspect.offset + 1}–
                {Math.min(inspect.total, inspect.offset + 750)}
              </span>
              <button
                disabled={inspect.offset + 750 >= inspect.total}
                onClick={() =>
                  recall(inspect.row, inspect.offset + 750).catch(report)
                }
              >
                Next
              </button>
            </div>
          </section>
        </div>
      )}
    </section>
  );
}
function Settings({ close, finish }) {
  const [choices, setChoices] = useState([]),
    [provider, setProvider] = useState("openrouter"),
    [rows, setRows] = useState([]),
    [key, setKey] = useState(""),
    [model, setModel] = useState(""),
    [filter, setFilter] = useState(""),
    [free, setFree] = useState(false),
    [working, setWorking] = useState(false),
    [error, setError] = useState("");
  useEffect(() => {
    api("providers")
      .then(setChoices)
      .catch((e) => setError(e.message));
  }, []);
  async function perform(fn) {
    setWorking(true);
    setError("");
    try {
      await fn();
    } catch (e) {
      setError(e.message);
    } finally {
      setWorking(false);
    }
  }
  const visible = rows.filter(
    (r) =>
      (!free || r.tier === "free") &&
      r.model.toLowerCase().includes(filter.toLowerCase()),
  );
  const selected = visible.some((r) => r.model === model)
    ? model
    : visible[0]?.model || "";
  return (
    <div className="scrim">
      <section
        className="dialog"
        role="dialog"
        aria-modal="true"
        aria-label="Model connection"
      >
        <div className="row">
          <h2>Model connection</h2>
          <button
            aria-label="Close connection"
            disabled={working}
            onClick={close}
          >
            <X />
          </button>
        </div>
        <label>
          Provider
          <select
            disabled={working}
            value={provider}
            onChange={(e) => {
              setProvider(e.target.value);
              setRows([]);
              setKey("");
              setError("");
            }}
          >
            {choices.map((p) => (
              <option key={p.id} value={p.id}>
                {p.name}
              </option>
            ))}
          </select>
        </label>
        {choices.find((p) => p.id === provider)?.requiresKey && (
          <label>
            API key
            <input
              type="password"
              autoComplete="off"
              placeholder={
                provider === "openrouter"
                  ? "Use browser sign-in or paste a key"
                  : "Use saved key or paste a new key"
              }
              value={key}
              onChange={(e) => setKey(e.target.value)}
            />
          </label>
        )}
        <button
          disabled={working}
          onClick={() =>
            perform(async () => {
              const list = await api("models", { provider, key });
              setRows(list);
              if (!list.length)
                setError("No models are available for this provider.");
            })
          }
        >
          Browse models
        </button>
        {!!rows.length && (
          <>
            <label>
              Find a model
              <input
                value={filter}
                onChange={(e) => setFilter(e.target.value)}
                placeholder="Search model names"
              />
            </label>
            <label className="check">
              <input
                type="checkbox"
                checked={free}
                onChange={(e) => setFree(e.target.checked)}
              />
              Free routes only
            </label>
            <label>
              Model
              <select
                value={selected}
                onChange={(e) => setModel(e.target.value)}
              >
                {visible.map((r) => (
                  <option key={r.model}>{r.model}</option>
                ))}
              </select>
            </label>
          </>
        )}
        {error && (
          <p role="alert" className="error">
            {error}
          </p>
        )}
        <button
          className="primary"
          disabled={!selected || working}
          onClick={() =>
            perform(async () => {
              await api("connect", {
                provider,
                key,
                model: selected,
                browser: provider === "openrouter" && !key,
              });
              finish();
            })
          }
        >
          {working
            ? "Connecting…"
            : provider === "openrouter" && !key
              ? "Connect with OpenRouter"
              : "Use connection"}
        </button>
      </section>
    </div>
  );
}
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
              {item.failed ? "× " : "✓ "}
              {item.label || item.name}
            </summary>
            <AgentCommandOutput isRunning={false} output={item.text} />
          </details>
        ))}
    </details>
  );
}
function App() {
  const [state, setState] = useState({
      workspace: "Workspace",
      model: "",
      messages: [],
    }),
    [nodes, setNodes] = useState([]),
    [file, setFile] = useState(null),
    [dirty, setDirty] = useState(false),
    [settings, setSettings] = useState(false),
    [error, setError] = useState(""),
    [task, setTask] = useState(""),
    [persona, setPersona] = useState("builder"),
    [attach, setAttach] = useState(false),
    [companions, setCompanions] = useState(false),
    [sending, setSending] = useState(false),
    [mobile, setMobile] = useState("agent"),
    [closed, setClosed] = useState(false);
  const content = useRef(""),
    transcript = useRef({ snapshot: 0, messages: [] }),
    last = useRef(null),
    reading = useRef(0),
    scroll = useRef(null);
  const report = (e) => setError(e.message || String(e));
  async function refresh() {
    const snap = await api("state", {
      since: transcript.current.messages.length,
    });
    const prior = transcript.current;
    if (snap.snapshot <= prior.snapshot) return;
    const messages = [
      ...prior.messages.slice(0, snap.offset),
      ...snap.messages,
    ];
    transcript.current = { ...snap, messages };
    setState(transcript.current);
  }
  async function open(path, force = false, navigate = true) {
    if (!force && dirty && !window.confirm("Discard your unsaved edits?"))
      return;
    const id = ++reading.current;
    const data = await api("read", { path });
    if (reading.current !== id) return;
    content.current = data.text;
    setFile({ ...data, path });
    setDirty(false);
    if (navigate) setMobile("editor");
  }
  async function tree() {
    const list = await api("tree", { path: "." });
    setNodes(list);
    return list;
  }
  async function save() {
    if (!file || !dirty) return;
    const data = await api("save", {
      path: file.path,
      version: file.version,
      text: content.current,
    });
    content.current = data.text;
    setFile({ ...data, path: file.path });
    setDirty(false);
  }
  useEffect(() => {
    tree()
      .then((list) => {
        const first =
          list.find((n) => n.name === "README.md") ||
          list.find((n) => !n.dir && /\.(py|js|md|txt)$/.test(n.name));
        if (first) return open(first.path, true, false);
      })
      .catch(report);
  }, []);
  useEffect(() => {
    if (closed) return;
    let live = true,
      timer;
    async function poll() {
      try {
        if (live) await refresh();
      } catch (e) {
        if (live) report(e);
      } finally {
        if (live) timer = setTimeout(poll, 750);
      }
    }
    poll();
    return () => {
      live = false;
      clearTimeout(timer);
    };
  }, [closed]);
  useEffect(() => {
    if (last.current && !state.busy) {
      tree().catch(report);
      if (file && !dirty) open(file.path, true, false).catch(report);
    }
    last.current = state.busy;
  }, [state.busy]);
  useEffect(() => {
    scroll.current?.scrollTo(0, scroll.current.scrollHeight);
  }, [state.messages.length, state.pending?.id]);
  useEffect(() => {
    const fn = (e) => {
      if ((e.ctrlKey || e.metaKey) && e.key === "s") {
        e.preventDefault();
        save().catch(report);
      }
    };
    window.addEventListener("keydown", fn);
    return () => window.removeEventListener("keydown", fn);
  }, [file, dirty]);
  useEffect(() => {
    const fn = (e) => {
      if (dirty) {
        e.preventDefault();
        e.returnValue = "";
      }
    };
    window.addEventListener("beforeunload", fn);
    return () => window.removeEventListener("beforeunload", fn);
  }, [dirty]);
  async function send() {
    if (!task.trim() || sending || state.busy) return;
    if (!state.model) {
      setSettings(true);
      return;
    }
    setSending(true);
    try {
      await api("run", {
        persona,
        companions,
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
  async function decide(approve) {
    try {
      await api("decision", { id: state.pending.id, approve });
      await refresh();
    } catch (e) {
      report(e);
    }
  }
  const usage = [...state.messages].reverse().find((m) => m.usage);
  if (closed)
    return (
      <main className="closed">
        <Mark />
        <h1>Studio closed</h1>
        <p>You can close this tab.</p>
      </main>
    );
  return (
    <div className={"shell mobile-" + mobile}>
      <header className="top">
        <Mark />
        <strong>
          Turnloom <em>Studio</em>
        </strong>
        <span className="divider" />
        <span className="root">
          <FolderOpen size={16} />
          {state.workspace}
        </span>
        <button
          className="connection"
          disabled={state.busy}
          onClick={() => setSettings(true)}
        >
          <i className={state.model ? "dot" : "dot off"} />
          {state.model || "Connect model"}
          <Settings2 size={15} />
        </button>
      </header>
      <nav className="mobile-tabs">
        {["files", "editor", "agent"].map((x) => (
          <button key={x} onClick={() => setMobile(x)}>
            {x}
          </button>
        ))}
      </nav>
      {error && (
        <div className="alert" role="alert">
          {error}
          <button onClick={() => setError("")} aria-label="Dismiss error">
            <X size={14} />
          </button>
        </div>
      )}
      <main className="panes">
        <aside className="explorer">
          <div className="bar">
            <b>Explorer</b>
            <button
              aria-label="Refresh files"
              onClick={() => tree().catch(report)}
            >
              <RefreshCw size={14} />
            </button>
          </div>
          <div className="rootname">
            <ChevronDown size={14} />
            <FolderOpen size={15} />
            {state.workspace}
          </div>
          <div className="files">
            <FileTree
              nodes={nodes}
              choose={open}
              active={file?.path}
              report={report}
            />
          </div>
        </aside>
        <section className="document">
          <div className="bar docbar">
            <b className="tab">
              <FileText size={15} />
              {file?.path.split("/").pop() || "Editor"}
              {dirty ? " •" : ""}
            </b>
            <button disabled={!dirty} onClick={() => save().catch(report)}>
              <Save size={14} /> Save
            </button>
          </div>
          {file ? (
            <>
              <div className="crumb">
                <Code2 size={14} />
                {file.path}
              </div>
              <Editor file={file} content={content} onChange={setDirty} />
            </>
          ) : (
            <div className="blank">
              <FileText />
              <p>Select a file in Explorer</p>
            </div>
          )}
          <ContextLoom state={state} report={report} />
          <div className="editorfoot">
            <span>{dirty ? "Unsaved changes" : "Workspace editor"}</span>
            <span>UTF-8</span>
          </div>
        </section>
        <section className="agent">
          <AgentConversationHeader
            className="bar"
            actions={
              <select
                aria-label="Agent persona"
                value={persona}
                disabled={state.busy}
                onChange={(e) => setPersona(e.target.value)}
              >
                {["builder", "reviewer", "architect", "debugger"].map((p) => (
                  <option key={p}>{p}</option>
                ))}
              </select>
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
                <article className={"msg " + m.role} key={i}>
                  <small>
                    {m.role === "user"
                      ? "YOU"
                      : m.role === "assistant"
                        ? "TURNLOOM"
                        : m.role.toUpperCase()}
                  </small>
                  {
                    <Markdown
                      skipHtml
                      components={{
                        img: () => null,
                        a: ({ children }) => <span>{children}</span>,
                      }}
                    >
                      {m.text}
                    </Markdown>
                  }
                  {m.note && <small>{m.note}</small>}
                </article>
              ),
            )}
            {state.busy && !state.pending && (
              <p className="working">
                <CircleDot size={14} /> Agent working…
              </p>
            )}
            {state.pending && (
              <AgentApprovalCard
                title="Run this command?"
                detail={state.pending.command}
                onApprove={() => decide(true)}
                onReject={() => decide(false)}
              />
            )}
          </div>
          <div className="compose">
            <label className="companions">
              <input
                type="checkbox"
                checked={companions}
                disabled={state.busy}
                onChange={(e) => setCompanions(e.target.checked)}
              />
              VerifyDoc via Unwind
            </label>
            <textarea
              aria-label="Message to agent"
              placeholder="Ask Turnloom to work on this project…"
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
              {state.busy ? (
                <button
                  disabled={state.stopping}
                  onClick={async () => {
                    try {
                      await api("stop");
                      await refresh();
                    } catch (e) {
                      report(e);
                    }
                  }}
                >
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
      </main>
      <footer>
        <span>
          <i className="dot" />
          Local workspace
        </span>
        <span>
          {state.busy
            ? state.stopping
              ? "Stopping after current operation"
              : state.pending
                ? "Awaiting approval"
                : "Working"
            : usage
              ? (
                  usage.usage.prompt_tokens + usage.usage.completion_tokens
                ).toLocaleString() +
                " tokens · " +
                (usage.cost == null
                  ? "cost unavailable"
                  : "$" + usage.cost.toFixed(5))
              : "Ready"}
        </span>
        <button
          disabled={state.busy}
          onClick={async () => {
            if (
              window.confirm(
                "Close the local Studio server? Unsaved editor changes will be lost.",
              )
            ) {
              try {
                await api("close");
                setDirty(false);
                setClosed(true);
              } catch (e) {
                report(e);
              }
            }
          }}
        >
          Close Studio
        </button>
      </footer>
      {settings && (
        <Settings
          close={() => setSettings(false)}
          finish={() => {
            setSettings(false);
            refresh().catch(report);
          }}
        />
      )}
    </div>
  );
}
createRoot(document.getElementById("app")).render(<App />);
