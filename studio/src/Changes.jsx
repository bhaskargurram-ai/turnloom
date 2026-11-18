import React, { memo, useEffect, useState } from "react";
import { api } from "./api.js";
import { useLoad, useStudio } from "./store.js";
import { DiffPanel } from "./DiffView.jsx";
import { Reload } from "./icons.jsx";
import { plural } from "./util.js";

const LETTER = { added: "A", deleted: "D", renamed: "R", modified: "M" };

/** The working tree against the last commit: every file Git reports as changed, and its diff. */
export const Changes = memo(function Changes({ active, report, onOpen }) {
  const disk = useStudio((state) => state.disk);
  const { data: list, error, loading, reload } = useLoad("changes", {}, String(disk), active);
  const [path, setPath] = useState(null);
  const [change, setChange] = useState(null);
  const [problem, setProblem] = useState("");
  const files = list?.files || [];

  // Keep the selection while its file is still changed; otherwise show the first one.
  useEffect(() => {
    if (!list) return;
    if (!files.some((file) => file.path === path)) setPath(files[0]?.path || null);
  }, [list]);

  useEffect(() => {
    if (!path || !active) return setChange((old) => (path ? old : null));
    let live = true;
    setProblem("");
    api("change", { path }).then(
      (data) => live && setChange(data),
      (failure) => {
        if (!live) return;
        setChange(null);
        setProblem(failure.message);
      },
    );
    return () => {
      live = false;
    };
  }, [path, list, active]);

  const current = files.find((file) => file.path === path);
  return (
    <section className="page split-view" hidden={!active} aria-label="Changes">
      <div className="split-list">
        <header className="bar">
          <h2 className="label">Changed files{files.length ? ` (${files.length})` : ""}</h2>
          <span className="bar-tools">
            <button type="button" className="quiet square" aria-label="Refresh changes" title="Refresh changes" onClick={reload}>
              <Reload />
            </button>
          </span>
        </header>
        {error && (
          <p className="problem" role="alert">
            {error}
          </p>
        )}
        {list && !list.repository ? (
          <div className="empty">
            <p className="empty-title">Not a Git repository</p>
            <p>
              This view compares the workspace with its last commit. Run <code>git init</code> and make a first commit
              to use it. Edits from an agent turn are always listed in the agent log, with or without Git.
            </p>
          </div>
        ) : list && !files.length ? (
          <div className="empty">
            <p className="empty-title">No changes since the last commit</p>
            <p>Files the agent or you change appear here as soon as they differ from the last commit.</p>
          </div>
        ) : (
          <ul className="tree" aria-label="Changed files">
            {files.map((file) => (
              <li className={"tree-row" + (file.path === path ? " current" : "")} key={file.path}>
                <button
                  type="button"
                  className="tree-item"
                  title={`${file.path} (${file.status})`}
                  aria-current={file.path === path ? "true" : undefined}
                  onClick={() => setPath(file.path)}
                >
                  <span className={"git-state " + file.status} aria-hidden="true">
                    {LETTER[file.status] || "M"}
                  </span>
                  <span className="tree-name path">{file.path}</span>
                  <span className="sr-only">, {file.status}</span>
                </button>
              </li>
            ))}
          </ul>
        )}
        {list?.repository && files.length > 0 && (
          <p className="split-foot">
            {plural(files.length, "file")} differ from the last commit{files.length >= 400 ? " (first 400 shown)" : ""}.
          </p>
        )}
      </div>
      <div className="split-detail">
        <header className="bar">
          {path ? (
            <h2 className="doc-path" title={path}>
              <span className="doc-name">{path}</span>
              <span className="doc-dirty plain">{current?.status}</span>
            </h2>
          ) : (
            <h2 className="label">Review</h2>
          )}
          <span className="bar-tools">
            {path && current?.status !== "deleted" && (
              <button type="button" className="small" onClick={() => onOpen(path)}>
                Open in editor
              </button>
            )}
          </span>
        </header>
        {problem ? (
          <p className="problem" role="alert">
            {problem}
          </p>
        ) : change && change.path === path ? (
          <DiffPanel name={change.path} original={change.original} modified={change.modified} before="Last commit" after="Working tree" />
        ) : (
          <div className="empty">
            <p className="empty-title">{loading || path ? "Reading" : "Nothing to review"}</p>
            {!path && !loading && <p>Select a changed file to compare it with the last commit.</p>}
          </div>
        )}
      </div>
    </section>
  );
});
