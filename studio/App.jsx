import React, { useEffect, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import { Command } from "cmdk";
import { Toaster, toast } from "sonner";
import { Panel, PanelGroup, PanelResizeHandle } from "react-resizable-panels";
import {
  Bot,
  ChartNoAxesCombined,
  Code2,
  FileSearch,
  FolderOpen,
  GitCompareArrows,
  History,
  Layers3,
  MessageSquarePlus,
  Plug,
  Power,
  Search,
  Settings2,
  ShieldCheck,
  Users,
} from "lucide-react";
import { api, percent, tokens, usd, useLoad } from "./api.js";
import { ChangesView, CodeView } from "./Workspace.jsx";
import { CostView } from "./Cost.jsx";
import { Avatar, PersonasView } from "./Personas.jsx";
import { DocumentsView, LedgerView, ToolsView } from "./Tools.jsx";
import { PolicyView } from "./Policy.jsx";
import { Connect } from "./Connect.jsx";
import { Chat } from "./Chat.jsx";
import "./studio.css";
import "./loom.css";

const VIEWS = [
  ["code", "Code", Code2],
  ["changes", "Changes", GitCompareArrows],
  ["agents", "Agents", Bot],
  ["cost", "Cost", ChartNoAxesCombined],
  ["policy", "Policy", ShieldCheck],
  ["tools", "Tools", Plug],
  ["documents", "Documents", FileSearch],
  ["ledger", "Undo ledger", History],
];

function Mark() {
  return (
    <span className="mark">
      <Layers3 size={21} />
    </span>
  );
}

function Ring({ value }) {
  const r = 7,
    c = 2 * Math.PI * r;
  return (
    <svg width="18" height="18" viewBox="0 0 18 18" aria-hidden="true">
      <circle cx="9" cy="9" r={r} fill="none" stroke="#33404a" strokeWidth="2.5" />
      <circle
        cx="9"
        cy="9"
        r={r}
        fill="none"
        stroke={value > 0.85 ? "#ef8f8f" : value > 0.6 ? "#e3b783" : "#87dfc8"}
        strokeWidth="2.5"
        strokeLinecap="round"
        strokeDasharray={`${Math.min(1, value) * c} ${c}`}
        transform="rotate(-90 9 9)"
      />
    </svg>
  );
}

function Palette({ open, setOpen, run, personas, nodes }) {
  return (
    <Command.Dialog
      open={open}
      onOpenChange={setOpen}
      label="Command palette"
      className="palette"
      overlayClassName="scrim"
    >
      <Command.Input placeholder="Go to a view, pick a persona, open a file…" />
      <Command.List>
        <Command.Empty>Nothing matches.</Command.Empty>
        <Command.Group heading="Go to">
          {VIEWS.map(([key, label, Icon]) => (
            <Command.Item key={key} value={"go " + label} onSelect={() => run({ view: key })}>
              <Icon size={15} /> {label}
            </Command.Item>
          ))}
        </Command.Group>
        <Command.Group heading="Actions">
          <Command.Item value="connect model provider" onSelect={() => run({ connect: true })}>
            <Settings2 size={15} /> Connect a model
          </Command.Item>
          <Command.Item value="new chat reset" onSelect={() => run({ reset: true })}>
            <MessageSquarePlus size={15} /> New chat
          </Command.Item>
          <Command.Item value="open workspace folder" onSelect={() => run({ workspace: true })}>
            <FolderOpen size={15} /> Open another workspace
          </Command.Item>
          <Command.Item value="team pipeline mode" onSelect={() => run({ mode: "team" })}>
            <Users size={15} /> Run as a team pipeline
          </Command.Item>
          <Command.Item value="solo one agent mode" onSelect={() => run({ mode: "solo" })}>
            <Bot size={15} /> Run as one agent
          </Command.Item>
        </Command.Group>
        <Command.Group heading="Files">
          {nodes
            .filter((n) => !n.dir)
            .map((n) => (
              <Command.Item key={n.path} value={"file " + n.path} onSelect={() => run({ file: n.path })}>
                <Code2 size={15} /> {n.path}
              </Command.Item>
            ))}
        </Command.Group>
        <Command.Group heading="Personas">
          {personas.map((p) => (
            <Command.Item key={p.id} value={`persona ${p.name} ${p.division}`} onSelect={() => run({ persona: p.id })}>
              <Avatar persona={p} size={20} /> {p.name}
              <small>{p.division}</small>
            </Command.Item>
          ))}
        </Command.Group>
      </Command.List>
    </Command.Dialog>
  );
}

function App() {
  const [state, setState] = useState(null),
    [nodes, setNodes] = useState([]),
    [file, setFile] = useState(null),
    [dirty, setDirty] = useState(false),
    [connect, setConnect] = useState(false),
    [palette, setPalette] = useState(false),
    [view, setView] = useState("code"),
    [persona, setPersona] = useState("builder"),
    [mobile, setMobile] = useState("agent"),
    [closed, setClosed] = useState(false);
  const [gallery] = useLoad("personas", {}, state?.workspace || "");
  const content = useRef(""),
    transcript = useRef({ snapshot: 0, messages: [] }),
    last = useRef(null),
    reading = useRef(0);
  const report = (e) => toast.error(e.message || String(e));
  const personas = gallery?.personas || [];

  async function refresh() {
    const prior = transcript.current;
    const snap = await api("state", { since: prior.messages.length });
    if (snap.snapshot <= prior.snapshot && snap.workspace === prior.workspace)
      return;
    const fresh = snap.workspace !== prior.workspace;
    const messages = fresh
      ? snap.messages
      : [...prior.messages.slice(0, snap.offset), ...snap.messages];
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
    if (navigate) {
      setView("code");
      setMobile("view");
    }
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
  async function loadWorkspace() {
    setFile(null);
    const list = await tree();
    const first =
      list.find((n) => n.name === "README.md") ||
      list.find((n) => !n.dir && /\.(py|js|md|txt)$/.test(n.name));
    if (first) await open(first.path, true, false);
  }
  async function switchWorkspace() {
    if (dirty && !window.confirm("Discard your unsaved edits?")) return;
    const result = await api("workspace");
    if (!result.changed) return;
    transcript.current = { snapshot: 0, messages: [] };
    setDirty(false);
    await refresh();
    await loadWorkspace();
  }
  useEffect(() => {
    loadWorkspace().catch(report);
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
    if (!state) return;
    if (last.current && !state.busy) {
      tree().catch(report);
      if (file && !dirty) open(file.path, true, false).catch(report);
    }
    last.current = state.busy;
  }, [state?.busy]);
  useEffect(() => {
    if (state?.persona && !transcript.current.personaSeen) {
      transcript.current.personaSeen = true;
      setPersona(state.persona);
    }
  }, [state?.persona]);
  useEffect(() => {
    const fn = (e) => {
      const mod = e.ctrlKey || e.metaKey;
      if (mod && e.key === "s") {
        e.preventDefault();
        save().catch(report);
      } else if (mod && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setPalette((x) => !x);
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

  function command(action) {
    setPalette(false);
    if (action.view) go(action.view);
    if (action.connect) setConnect(true);
    if (action.persona) setPersona(action.persona);
    if (action.file) open(action.file).catch(report);
    if (action.reset) api("reset").then(refresh).catch(report);
    if (action.mode)
      api("settings", { mode: action.mode }).then(refresh).catch(report);
    if (action.workspace) switchWorkspace().catch(report);
  }
  function go(next) {
    setView(next);
    setMobile("view");
  }
  async function close() {
    if (
      !window.confirm(
        "Close the local Studio server? Unsaved editor changes will be lost.",
      )
    )
      return;
    try {
      await api("close");
      setDirty(false);
      setClosed(true);
    } catch (e) {
      report(e);
    }
  }

  if (closed)
    return (
      <main className="closed">
        <Mark />
        <h1>Studio closed</h1>
        <p>You can close this window.</p>
      </main>
    );
  if (!state)
    return (
      <main className="closed">
        <Mark />
        <p>Opening workspace…</p>
        <Toaster theme="dark" position="top-center" richColors />
      </main>
    );
  const meter = state.meter,
    fill = meter.prompt / meter.window;
  const shared = { state, report };
  return (
    <div className={"shell mobile-" + mobile}>
      <header className="top">
        <Mark />
        <strong>
          Turnloom <em>Studio</em>
        </strong>
        <span className="divider" />
        <button
          className="root"
          title="Open another workspace"
          disabled={state.busy}
          onClick={() => switchWorkspace().catch(report)}
        >
          <FolderOpen size={16} />
          {state.workspace}
        </button>
        <button
          className="palettebutton"
          onClick={() => setPalette(true)}
          aria-label="Open command palette"
        >
          <Search size={14} />
          <span>Search views, personas, files</span>
          <kbd>Ctrl K</kbd>
        </button>
        <button
          className="meterchip"
          onClick={() => go("cost")}
          title="Open cost and tokens"
          aria-label="Session cost and context use"
        >
          <Ring value={fill} />
          <span>
            {tokens(meter.prompt_tokens + meter.completion_tokens)} tokens
          </span>
          {meter.priced && <b>{usd(meter.cost)}</b>}
          <i>{percent(meter.prompt, meter.window)}% context</i>
        </button>
        <button
          className="connection"
          disabled={state.busy}
          onClick={() => setConnect(true)}
        >
          <i className={state.model ? "dot" : "dot off"} />
          {state.model || "Connect model"}
          <Settings2 size={15} />
        </button>
      </header>
      <nav className="mobile-tabs">
        {[
          ["view", VIEWS.find((v) => v[0] === view)[1]],
          ["agent", "Chat"],
        ].map(([key, label]) => (
          <button key={key} onClick={() => setMobile(key)} className={mobile === key ? "on" : ""}>
            {label}
          </button>
        ))}
      </nav>
      <div className="body">
        <nav className="rail" aria-label="Views">
          {VIEWS.map(([key, label, Icon]) => (
            <button
              key={key}
              className={view === key ? "on" : ""}
              aria-current={view === key ? "page" : undefined}
              aria-label={label}
              title={label}
              onClick={() => go(key)}
            >
              <Icon size={19} />
              <span>{label}</span>
            </button>
          ))}
          <span className="railspace" />
          <button aria-label="Close Studio" title="Close Studio" disabled={state.busy} onClick={close}>
            <Power size={18} />
            <span>Close Studio</span>
          </button>
        </nav>
        <PanelGroup direction="horizontal" autoSaveId="turnloom-shell" className="panes">
          <Panel minSize={35} order={1} className="viewpane">
            {view === "code" && (
              <CodeView
                {...shared}
                nodes={nodes}
                file={file}
                dirty={dirty}
                setDirty={setDirty}
                content={content}
                open={(path) => open(path).catch(report)}
                save={save}
                reloadTree={() => tree().catch(report)}
              />
            )}
            {view === "changes" && (
              <ChangesView busy={state.busy} open={(path) => open(path).catch(report)} report={report} />
            )}
            {view === "agents" && (
              <PersonasView {...shared} gallery={gallery} persona={persona} choose={setPersona} />
            )}
            {view === "cost" && <CostView {...shared} openConnect={() => setConnect(true)} />}
            {view === "policy" && (
              <PolicyView {...shared} open={(path) => open(path).catch(report)} reloadTree={() => tree().catch(report)} />
            )}
            {view === "tools" && <ToolsView {...shared} />}
            {view === "documents" && <DocumentsView {...shared} file={file} />}
            {view === "ledger" && <LedgerView {...shared} />}
          </Panel>
          <PanelResizeHandle className="grip" />
          <Panel defaultSize={32} minSize={22} maxSize={55} order={2} className="agentpane">
            <Chat
              {...shared}
              personas={personas}
              persona={persona}
              file={file}
              go={go}
              refresh={refresh}
              needModel={() => setConnect(true)}
            />
          </Panel>
        </PanelGroup>
      </div>
      <footer>
        <span>
          <i className="dot" />
          Local workspace · v{state.version}
          {state.gate.active ? " · Provio gate on" : ""}
          {state.desktop ? " · desktop" : ""}
        </span>
        <span>
          {state.busy
            ? state.stopping
              ? "Stopping after current operation"
              : state.pending
                ? "Awaiting approval"
                : "Working"
            : meter.steps
              ? `${(meter.prompt_tokens + meter.completion_tokens).toLocaleString()} tokens · ${meter.priced ? "$" + meter.cost.toFixed(5) : "cost unavailable"}`
              : "Ready"}
        </span>
        <span>{dirty ? "Unsaved changes" : "UTF-8"}</span>
      </footer>
      {connect && (
        <Connect
          current={state.connection}
          close={() => setConnect(false)}
          finish={() => {
            setConnect(false);
            refresh().catch(report);
          }}
        />
      )}
      <Palette open={palette} setOpen={setPalette} run={command} personas={personas} nodes={nodes} />
      <Toaster theme="dark" position="top-center" richColors closeButton />
    </div>
  );
}
createRoot(document.getElementById("app")).render(<App />);
