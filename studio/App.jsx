import "./src/theme.js";
import React, { useCallback, useEffect, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import { api } from "./src/api.js";
import { startPolling, stopPolling, switchWorkspace, useStudio } from "./src/store.js";
import { applyTheme, nextTheme, storedTheme } from "./src/theme.js";
import { Confirm } from "./src/Dialog.jsx";
import { Cross, Mark } from "./src/icons.jsx";
import { Code } from "./src/Code.jsx";
import { Agent } from "./src/Agent.jsx";
import { Changes } from "./src/Changes.jsx";
import { Agents } from "./src/Agents.jsx";
import { Cost } from "./src/Cost.jsx";
import { Policy } from "./src/Policy.jsx";
import { Tools } from "./src/Tools.jsx";
import { Documents } from "./src/Documents.jsx";
import { Ledger } from "./src/Ledger.jsx";
import { Connection } from "./src/Connection.jsx";
import { Palette } from "./src/Palette.jsx";
import { Closed, Expired, Gone, Guard, NoSession } from "./src/Screens.jsx";
import { VIEWS } from "./src/views.js";
import { clock, compact, money, percent, useMedia } from "./src/util.js";
import "./studio.css";
import "./views.css";
import "./loom.css";

const PAGES = { changes: Changes, agents: Agents, cost: Cost, policy: Policy, tools: Tools, documents: Documents, ledger: Ledger };

function Activity() {
  const busy = useStudio((state) => state.busy);
  const stopping = useStudio((state) => state.stopping);
  const waiting = useStudio((state) => !!state.pending);
  const since = useStudio((state) => state.busySince);
  const [, tick] = useState(0);
  useEffect(() => {
    if (!busy) return;
    const timer = setInterval(() => tick((value) => value + 1), 1000);
    return () => clearInterval(timer);
  }, [busy]);
  if (!busy) return <span>Ready</span>;
  return (
    <span className={waiting ? "attention" : ""}>
      {stopping ? "Stopping" : waiting ? "Approval needed" : "Working"}{" "}
      <span className="num">{clock(Date.now() - since)}</span>
    </span>
  );
}

/** The session's running totals, as a control that opens the Cost view. */
function Meter({ onView }) {
  const meter = useStudio((state) => state.meter);
  if (!meter.steps) return null;
  return (
    <button type="button" className="quiet meter-button" title="Open cost and tokens for this session" onClick={() => onView("cost")}>
      <span className="num">{compact(meter.prompt_tokens + meter.completion_tokens)} tokens</span>
      {meter.priced && <span className="num">{money(meter.cost)}</span>}
      <span className="num">{percent(meter.prompt, meter.window)}% of context</span>
    </button>
  );
}

function Workbench() {
  const link = useStudio((state) => state.link);
  const workspace = useStudio((state) => state.workspace);
  const model = useStudio((state) => state.model);
  const busy = useStudio((state) => state.busy);
  const waiting = useStudio((state) => !!state.pending);
  const session = useStudio((state) => state.session);
  const trace = useStudio((state) => state.trace);
  const version = useStudio((state) => state.version);
  const sessionKey = useStudio((state) => !!state.connection?.sessionKey);
  const gate = useStudio((state) => state.gate.active);
  const desktop = useStudio((state) => state.desktop);
  const epoch = useStudio((state) => state.epoch);
  const limits = useStudio((state) => state.limits);
  const [view, setView] = useState("code");
  const [pane, setPane] = useState("agent");
  const [visited, setVisited] = useState(() => new Set(["code"]));
  const [openPath, setOpenPath] = useState(null);
  const [dirty, setDirty] = useState(false);
  const [connecting, setConnecting] = useState(false);
  const [closing, setClosing] = useState(false);
  const [switching, setSwitching] = useState(""); // "" | "ask" | "waiting"
  const [palette, setPalette] = useState(false);
  const [notices, setNotices] = useState([]);
  const [theme, setTheme] = useState(storedTheme);
  const code = useRef(null);
  const narrow = useMedia("(max-width: 759px)");
  const gone = link === "gone";

  // An error may name the view where it can be put right; the notice then offers to go there.
  const report = useCallback((error, view) => {
    if (error?.name === "AbortError") return;
    const text = error?.message || String(error);
    const transport = error?.status === 0;
    setNotices((old) => [...old.filter((item) => item.text !== text), { text, transport, view }].slice(-3));
  }, []);
  const show = useCallback((name) => {
    if (name === "agent") return setPane("agent");
    setView(name);
    setPane("view");
    setVisited((old) => (old.has(name) ? old : new Set(old).add(name)));
  }, []);
  const openFile = useCallback(
    (path) => {
      show("code");
      code.current.open(path);
    },
    [show],
  );
  const connect = useCallback(() => setConnecting(true), []);
  const cycleTheme = useCallback(() => {
    setTheme((old) => {
      const next = nextTheme(old);
      applyTheme(next);
      return next;
    });
  }, []);

  useEffect(() => {
    const state = waiting ? "Approval needed · " : busy ? "Working · " : "";
    document.title = state + (workspace ? workspace + " · " : "") + "Turnloom Studio";
  }, [workspace, busy, waiting]);

  // A transport failure is old news once the server answers again.
  useEffect(() => {
    if (link === "live") setNotices((old) => (old.some((item) => item.transport) ? old.filter((item) => !item.transport) : old));
  }, [link]);

  useEffect(() => {
    if (sessionKey)
      setNotices((old) => [
        ...old,
        {
          text: "This computer has no credential store Turnloom can use, so the API key is held in memory only. Studio will ask for it again next time.",
          info: true,
        },
      ]);
  }, [sessionKey]);

  useEffect(() => {
    const onKey = (event) => {
      if ((event.ctrlKey || event.metaKey) && !event.altKey && !event.shiftKey && event.key.toLowerCase() === "k") {
        event.preventDefault();
        event.stopPropagation();
        setPalette((old) => !old);
      }
    };
    window.addEventListener("keydown", onKey, true);
    return () => window.removeEventListener("keydown", onKey, true);
  }, []);

  async function close() {
    setClosing(false);
    try {
      await api("close");
      stopPolling("closed");
    } catch (error) {
      report(error);
    }
  }

  const changeWorkspace = useCallback(async () => {
    setSwitching("waiting");
    try {
      if (await switchWorkspace()) {
        setNotices([]);
        setView("code");
        setVisited(new Set(["code"]));
      }
    } catch (error) {
      report(error);
    } finally {
      setSwitching("");
    }
  }, [report]);
  const askWorkspace = useCallback(() => (dirty ? setSwitching("ask") : changeWorkspace()), [dirty, changeWorkspace]);

  return (
    <>
      <div className="shell" data-pane={pane} inert={gone}>
        <header className="top">
          <p className="brand">
            <Mark />
            <span>Turnloom Studio</span>
          </p>
          <button
            type="button"
            className="quiet workspace"
            disabled={busy || !!switching}
            title={busy ? "Wait for the current turn to finish" : "Open another folder as the workspace"}
            onClick={askWorkspace}
          >
            <span className="sr-only">Workspace: </span>
            <span className="workspace-name">{workspace || "Workspace"}</span>
            <span className="workspace-action">{switching === "waiting" ? "choosing a folder" : "Change"}</span>
          </button>
          <span className="spacer" />
          <button type="button" className="palette-button" title="Search views, files, personas and commands" onClick={() => setPalette(true)}>
            <span>
              Search<span className="wide-only"> and commands</span>
            </span>
            <kbd>{/Mac|iPhone|iPad/.test(navigator.platform) ? "Cmd K" : "Ctrl K"}</kbd>
          </button>
          <button
            type="button"
            className={"model-button" + (model ? "" : " none")}
            title={model ? "Change the provider or model" : "Choose a provider and model"}
            onClick={connect}
          >
            <i className="lamp" aria-hidden="true" />
            <span className="sr-only">Model connection: </span>
            <span className="model-name">{model || "No model connected"}</span>
            <span className="model-action">{model ? "Change" : "Connect"}</span>
          </button>
          <button type="button" className="quiet theme-button" title="Colour theme. Auto follows the system setting" onClick={cycleTheme}>
            <span className="sr-only">Colour theme: </span>
            {{ auto: "Auto", light: "Light", dark: "Dark" }[theme]}
          </button>
        </header>
        <nav className="views" aria-label="Views">
          <div className="view-tabs">
            <button
              type="button"
              className="view-tab tab-agent"
              aria-current={narrow && pane === "agent" ? "page" : undefined}
              onClick={() => show("agent")}
            >
              Agent log
              {waiting && <span className="tab-flag">approval</span>}
            </button>
            {VIEWS.map(([name, label]) => (
              <button
                type="button"
                key={name}
                className="view-tab"
                aria-current={view === name && (!narrow || pane === "view") ? "page" : undefined}
                onClick={() => show(name)}
              >
                {label}
              </button>
            ))}
          </div>
          <Meter onView={show} />
        </nav>
        {notices.length > 0 && (
          <div className="notices">
            {notices.map((notice) => (
              <p className={"notice" + (notice.info ? " info" : "")} role={notice.info ? "status" : "alert"} key={notice.text}>
                <span>{notice.text}</span>
                {notice.view && (
                  <button
                    type="button"
                    className="small"
                    onClick={() => {
                      show(notice.view);
                      setNotices((old) => old.filter((item) => item !== notice));
                    }}
                  >
                    Open the {VIEWS.find(([name]) => name === notice.view)?.[1]} view
                  </button>
                )}
                <button
                  type="button"
                  className="quiet square"
                  aria-label="Dismiss message"
                  title="Dismiss"
                  onClick={() => setNotices((old) => old.filter((item) => item !== notice))}
                >
                  <Cross />
                </button>
              </p>
            ))}
          </div>
        )}
        <main className="body">
          <div className="view-pane">
            <Code ref={code} hidden={view !== "code"} report={report} onPath={setOpenPath} onDirty={setDirty} />
            {VIEWS.filter(([name]) => name !== "code" && visited.has(name)).map(([name]) => {
              const Page = PAGES[name];
              return (
                <Page
                  key={name + epoch}
                  active={view === name}
                  report={report}
                  onOpen={openFile}
                  onView={show}
                  onConnect={connect}
                  openPath={openPath}
                />
              );
            })}
          </div>
          <Agent openPath={openPath} onConnect={connect} onOpen={openFile} onView={show} report={report} />
        </main>
        <footer className="status">
          <span className={"link-state " + link}>
            <i className="lamp" aria-hidden="true" />
            {link === "live" ? "Connected to local server" : link === "starting" ? "Connecting" : "Reconnecting"}
          </span>
          <Activity />
          <span className="wide-only session num" title={"Session " + session + "\nTranscript: " + trace}>
            session {session.slice(0, 8)} <span className="dim">{trace}</span>
          </span>
          <span className="spacer" />
          {limits && (
            <span className="wide-only" title="The most one turn may use before the agent must write its answer">
              per turn: {limits.steps} model steps, {limits.tools} tool calls
            </span>
          )}
          {gate && <span className="wide-only">policy gate on</span>}
          {sessionKey && (
            <span className="wide-only attention" title="No OS credential store is available. The key is gone when Studio closes.">
              key in memory only
            </span>
          )}
          <span className="wide-only num dim">{version && "v" + version}</span>
          {desktop ? (
            <span className="dim">Close the window to stop Studio</span>
          ) : (
            <button type="button" className="quiet close-studio" onClick={() => setClosing(true)}>
              Close Studio
            </button>
          )}
        </footer>
      </div>
      {connecting && <Connection onClose={() => setConnecting(false)} />}
      {palette && (
        <Palette
          onClose={() => setPalette(false)}
          onView={show}
          onOpen={openFile}
          onConnect={connect}
          onWorkspace={askWorkspace}
          onTheme={cycleTheme}
          onCloseStudio={desktop ? null : () => setClosing(true)}
          report={report}
        />
      )}
      {closing && (
        <Confirm title="Close Studio" action="Close Studio" danger onClose={() => setClosing(false)} onConfirm={close}>
          <p>This stops the local server for this workspace. Sessions stay on disk and can be resumed next time.</p>
          {busy && (
            <p>
              <strong>A turn is running.</strong> Closing stops it now and ends any command it started.
            </p>
          )}
          {dirty && (
            <p>
              <strong>{openPath} has unsaved edits.</strong> They will be lost.
            </p>
          )}
        </Confirm>
      )}
      {switching === "ask" && (
        <Confirm
          title="Open another workspace"
          action="Discard and choose a folder"
          danger
          onClose={() => setSwitching("")}
          onConfirm={changeWorkspace}
        >
          <p>
            <strong>{openPath} has unsaved edits.</strong> Opening another folder discards them.
          </p>
        </Confirm>
      )}
      {gone && <Gone unsaved={dirty ? openPath : null} text={() => code.current?.text() || ""} />}
    </>
  );
}

function App() {
  const link = useStudio((state) => state.link);
  useEffect(() => {
    startPolling();
    return () => stopPolling();
  }, []);
  if (link === "none") return <NoSession />;
  if (link === "expired") return <Expired />;
  if (link === "closed") return <Closed />;
  return <Workbench />;
}

createRoot(document.getElementById("app")).render(
  <Guard>
    <App />
  </Guard>,
);
