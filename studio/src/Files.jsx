import React, { memo, useCallback, useEffect, useRef, useState } from "react";
import { api } from "./api.js";
import { useStudio } from "./store.js";
import { Confirm } from "./Dialog.jsx";
import { Caret, FileIcon, FolderIcon, Pencil, Plus, Reload, Trash } from "./icons.jsx";

function NameField({ label, initial, onSubmit, onCancel, hint }) {
  const [value, setValue] = useState(initial);
  const input = useRef(null);
  useEffect(() => {
    const node = input.current;
    node.focus();
    const slash = initial.lastIndexOf("/") + 1;
    const dot = initial.lastIndexOf(".");
    node.setSelectionRange(slash, dot > slash ? dot : initial.length);
  }, []);
  return (
    <form
      className="name-field"
      onSubmit={(event) => {
        event.preventDefault();
        if (value.trim()) onSubmit(value.trim());
      }}
    >
      <input
        ref={input}
        aria-label={label}
        value={value}
        spellCheck={false}
        autoComplete="off"
        placeholder="folder/name.ext"
        onChange={(event) => setValue(event.target.value)}
        onKeyDown={(event) => {
          if (event.key === "Escape") {
            event.stopPropagation();
            onCancel();
          }
        }}
      />
      <div className="name-actions">
        <span className="hint">{hint}</span>
        <button type="submit" className="small">
          {label.startsWith("New") ? "Create" : "Rename"}
        </button>
        <button type="button" className="small quiet" onClick={onCancel}>
          Cancel
        </button>
      </div>
    </form>
  );
}

/**
 * The workspace tree. Folder contents live in one map keyed by path, so a refresh re-reads
 * the root and every open folder and the view never shows a stale listing.
 */
export const Files = memo(function Files({ active, onOpen, onRenamed, onDeleted, report }) {
  const workspace = useStudio((state) => state.workspace);
  const busy = useStudio((state) => state.busy);
  const disk = useStudio((state) => state.disk);
  const [listing, setListing] = useState(null);
  const [open, setOpen] = useState(() => new Set());
  const [creating, setCreating] = useState(false);
  const [renaming, setRenaming] = useState(null);
  const [removing, setRemoving] = useState(null);
  const openRef = useRef(open);
  openRef.current = open;
  const sequence = useRef(0);

  const reload = useCallback(async () => {
    const id = ++sequence.current;
    const folders = [".", ...openRef.current];
    const results = await Promise.all(
      folders.map((path) =>
        api("tree", { path }).then(
          (entries) => [path, entries],
          (error) => {
            if (path === ".") throw error;
            return [path, null];
          },
        ),
      ),
    );
    if (id !== sequence.current) return;
    const next = {};
    const gone = [];
    for (const [path, entries] of results) {
      if (entries) next[path] = entries;
      else gone.push(path);
    }
    setListing(next);
    if (gone.length)
      setOpen((old) => {
        const kept = new Set(old);
        for (const path of gone) kept.delete(path);
        return kept;
      });
  }, []);

  useEffect(() => {
    reload().catch(report);
  }, [disk]);

  async function toggle(path) {
    const next = new Set(open);
    if (next.has(path)) {
      next.delete(path);
      setOpen(next);
      return;
    }
    const entries = await api("tree", { path });
    next.add(path);
    setListing((old) => ({ ...old, [path]: entries }));
    setOpen(next);
  }

  async function reveal(path) {
    // Open every folder above a new or renamed file so it is visible after the refresh.
    const parts = path.split("/").slice(0, -1);
    const next = new Set(openRef.current);
    for (let depth = 1; depth <= parts.length; depth++) next.add(parts.slice(0, depth).join("/"));
    openRef.current = next;
    setOpen(next);
    await reload();
  }

  async function create(path) {
    const made = await api("create", { path });
    setCreating(false);
    await reveal(made.path);
    onOpen(made.path);
  }

  async function rename(from, to) {
    if (to === from) return setRenaming(null);
    const moved = await api("rename", { path: from, to });
    setRenaming(null);
    await reveal(moved.path);
    onRenamed(from, moved.path);
  }

  async function remove(path) {
    await api("delete", { path });
    setRemoving(null);
    await reload();
    onDeleted(path);
  }

  function arrows(event) {
    if (!["ArrowDown", "ArrowUp", "ArrowLeft", "ArrowRight"].includes(event.key)) return;
    const row = event.target.closest(".tree-row");
    if (!row || event.target.tagName === "INPUT") return;
    const rows = [...event.currentTarget.querySelectorAll(".tree-row > .tree-item")];
    const index = rows.indexOf(row.querySelector(".tree-item"));
    const item = rows[index];
    if (!item || event.target !== item) return;
    event.preventDefault();
    if (event.key === "ArrowDown") rows[Math.min(rows.length - 1, index + 1)].focus();
    else if (event.key === "ArrowUp") rows[Math.max(0, index - 1)].focus();
    else if (item.hasAttribute("aria-expanded")) {
      const expanded = item.getAttribute("aria-expanded") === "true";
      if (expanded !== (event.key === "ArrowRight")) item.click();
    }
  }

  const waiting = "Wait for the current turn to finish";
  const rows = [];
  (function walk(path, depth) {
    for (const node of listing?.[path] || []) {
      const expanded = node.dir && open.has(node.path);
      rows.push(
        <li className={"tree-row" + (active === node.path ? " current" : "")} key={node.path}>
          {renaming === node.path ? (
            <NameField
              label={"Rename " + node.name}
              initial={node.path}
              hint="Path in the workspace"
              onCancel={() => setRenaming(null)}
              onSubmit={(to) => rename(node.path, to).catch(report)}
            />
          ) : (
            <>
              <button
                type="button"
                className="tree-item"
                style={{ paddingLeft: 8 + depth * 14 }}
                title={node.path}
                aria-expanded={node.dir ? expanded : undefined}
                aria-current={active === node.path ? "true" : undefined}
                onClick={() => (node.dir ? toggle(node.path).catch(report) : onOpen(node.path))}
              >
                {node.dir ? <Caret open={expanded} /> : <span className="tree-gap" />}
                {node.dir ? <FolderIcon /> : <FileIcon />}
                <span className="tree-name">{node.name}</span>
              </button>
              {!node.dir && (
                <span className="tree-tools">
                  <button
                    type="button"
                    className="quiet square"
                    aria-label={"Rename " + node.path}
                    title={busy ? waiting : "Rename or move"}
                    disabled={busy}
                    onClick={() => setRenaming(node.path)}
                  >
                    <Pencil />
                  </button>
                  <button
                    type="button"
                    className="quiet square"
                    aria-label={"Delete " + node.path}
                    title={busy ? waiting : "Delete"}
                    disabled={busy}
                    onClick={() => setRemoving(node.path)}
                  >
                    <Trash />
                  </button>
                </span>
              )}
            </>
          )}
        </li>,
      );
      if (expanded) {
        if (listing[node.path] && !listing[node.path].length)
          rows.push(
            <li className="tree-empty" key={node.path + "/"} style={{ paddingLeft: 34 + depth * 14 }}>
              empty
            </li>,
          );
        walk(node.path, depth + 1);
      }
    }
  })(".", 0);

  return (
    <section className="pane files" aria-label="Files">
      <header className="bar">
        <h2 className="label" title={workspace}>
          {workspace || "Files"}
        </h2>
        <span className="bar-tools">
          <button
            type="button"
            className="quiet square"
            aria-label="New file"
            title={busy ? waiting : "New file"}
            disabled={busy}
            onClick={() => setCreating(true)}
          >
            <Plus />
          </button>
          <button
            type="button"
            className="quiet square"
            aria-label="Refresh files"
            title="Refresh files"
            onClick={() => reload().catch(report)}
          >
            <Reload />
          </button>
        </span>
      </header>
      {creating && (
        <NameField
          label="New file path"
          initial={active && active.includes("/") ? active.slice(0, active.lastIndexOf("/") + 1) : ""}
          hint="Folders are created as needed"
          onCancel={() => setCreating(false)}
          onSubmit={(path) => create(path).catch(report)}
        />
      )}
      {listing && !rows.length && !creating ? (
        <div className="empty">
          <p className="empty-title">This folder is empty</p>
          <p>Create a file here, or describe what you want and let the agent write the first files.</p>
          <button type="button" disabled={busy} onClick={() => setCreating(true)}>
            New file
          </button>
        </div>
      ) : (
        <ul className="tree" onKeyDown={arrows}>
          {rows}
        </ul>
      )}
      {removing && (
        <Confirm
          title="Delete file"
          action="Delete"
          danger
          onClose={() => setRemoving(null)}
          onConfirm={() =>
            remove(removing).catch((error) => {
              setRemoving(null);
              report(error);
            })
          }
        >
          <p>
            Delete <code>{removing}</code> from disk? Studio cannot bring it back.
          </p>
        </Confirm>
      )}
    </section>
  );
});
