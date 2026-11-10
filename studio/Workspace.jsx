import React, { useEffect, useRef, useState } from "react";
import {
  BookOpen,
  ChevronDown,
  ChevronRight,
  Code2,
  FileCode2,
  FileDiff,
  FileText,
  Folder,
  FolderOpen,
  GitCompareArrows,
  RefreshCw,
  Save,
} from "lucide-react";
import { Panel, PanelGroup, PanelResizeHandle } from "react-resizable-panels";
import { basicSetup, EditorView } from "codemirror";
import { EditorState } from "@codemirror/state";
import { MergeView } from "@codemirror/merge";
import { oneDark } from "@codemirror/theme-one-dark";
import { python } from "@codemirror/lang-python";
import { javascript } from "@codemirror/lang-javascript";
import { markdown } from "@codemirror/lang-markdown";
import { json } from "@codemirror/lang-json";
import { html } from "@codemirror/lang-html";
import { css } from "@codemirror/lang-css";
import { yaml } from "@codemirror/lang-yaml";
import { rust } from "@codemirror/lang-rust";
import { api, useLoad } from "./api.js";
import { ContextLoom } from "./Loom.jsx";

function language(path) {
  const extension = path.split(".").pop().toLowerCase();
  if (extension === "py") return python();
  if (extension === "md") return markdown();
  if (["js", "jsx", "mjs", "cjs", "ts", "tsx"].includes(extension))
    return javascript({ jsx: true, typescript: extension.startsWith("ts") });
  if (extension === "json") return json();
  if (["html", "htm", "svg", "xml"].includes(extension)) return html();
  if (extension === "css") return css();
  if (["yml", "yaml"].includes(extension)) return yaml();
  if (extension === "rs") return rust();
  return [];
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
    const view = new EditorView({
      parent: host.current,
      doc: file.text,
      extensions: [
        basicSetup,
        oneDark,
        language(file.path),
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

export function CodeView({
  state,
  nodes,
  file,
  dirty,
  setDirty,
  content,
  open,
  save,
  reloadTree,
  report,
}) {
  return (
    <PanelGroup direction="horizontal" autoSaveId="turnloom-code">
      <Panel
        defaultSize={24}
        minSize={14}
        maxSize={45}
        className="explorer"
        order={1}
      >
        <div className="bar">
          <b>Explorer</b>
          <button aria-label="Refresh files" onClick={reloadTree}>
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
      </Panel>
      <PanelResizeHandle className="grip" />
      <Panel minSize={30} className="document" order={2}>
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
      </Panel>
    </PanelGroup>
  );
}

function Diff({ change }) {
  const host = useRef(null);
  useEffect(() => {
    const shared = [
      basicSetup,
      oneDark,
      language(change.path),
      EditorView.lineWrapping,
      EditorView.editable.of(false),
      EditorState.readOnly.of(true),
    ];
    const view = new MergeView({
      parent: host.current,
      a: { doc: change.original, extensions: shared },
      b: { doc: change.modified, extensions: shared },
      collapseUnchanged: { margin: 3, minSize: 6 },
      gutter: true,
    });
    return () => view.destroy();
  }, [change]);
  return <div className="diff" ref={host} />;
}

export function ChangesView({ busy, open, report }) {
  const [list, reload] = useLoad("changes", {}, busy ? "busy" : "idle"),
    [change, setChange] = useState(null);
  const files = list?.files || [];
  async function show(path) {
    setChange(await api("change", { path }));
  }
  useEffect(() => {
    if (!list) return;
    const still = change && files.some((f) => f.path === change.path);
    if (still) show(change.path).catch(report);
    else if (files[0]) show(files[0].path).catch(report);
    else setChange(null);
  }, [list]);
  return (
    <PanelGroup direction="horizontal" autoSaveId="turnloom-changes">
      <Panel defaultSize={26} minSize={16} maxSize={45} className="explorer">
        <div className="bar">
          <b>Changes{files.length ? " · " + files.length : ""}</b>
          <button aria-label="Refresh changes" onClick={reload}>
            <RefreshCw size={14} />
          </button>
        </div>
        <div className="files">
          {files.map((f) => (
            <button
              key={f.path}
              className={"file " + (change?.path === f.path ? "selected" : "")}
              title={f.path}
              onClick={() => show(f.path).catch(report)}
            >
              <FileDiff size={15} />
              <span>{f.path}</span>
              <i className={"status " + f.status}>
                {f.status[0].toUpperCase()}
              </i>
            </button>
          ))}
          {list && !list.repository && (
            <p className="hint">
              This workspace is not a Git repository. Run <code>git init</code>{" "}
              to review what the agent changes.
            </p>
          )}
          {list?.repository && !files.length && (
            <p className="hint">
              No changes since the last commit. Edits the agent makes appear
              here when its turn ends.
            </p>
          )}
        </div>
      </Panel>
      <PanelResizeHandle className="grip" />
      <Panel minSize={30} className="document">
        <div className="bar docbar">
          <b className="tab">
            <GitCompareArrows size={15} />
            {change ? change.path : "Review"}
          </b>
          {change && (
            <button onClick={() => open(change.path)}>
              <Code2 size={14} /> Open in editor
            </button>
          )}
        </div>
        {change ? (
          <>
            <div className="crumb sides">
              <span>Last commit</span>
              <span>Working tree</span>
            </div>
            <Diff change={change} />
          </>
        ) : (
          <div className="blank">
            <GitCompareArrows />
            <p>Select a changed file to compare it with the last commit</p>
          </div>
        )}
      </Panel>
    </PanelGroup>
  );
}
