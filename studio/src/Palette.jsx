import React, { useEffect, useMemo, useRef, useState } from "react";
import { api } from "./api.js";
import { configure, personaTitle, studio, usePersonas, useStudio } from "./store.js";
import { Dialog } from "./Dialog.jsx";
import { VIEWS } from "./views.js";

const MOST = 60;
let listed = { key: "", files: [], partial: false };

// Walk the workspace breadth first, a folder per request, and stop at a sensible size.
async function workspaceFiles(key) {
  if (listed.key === key) return listed;
  const files = [];
  const queue = ["."];
  let folders = 0;
  while (queue.length && folders < 80 && files.length < 800) {
    const entries = await api("tree", { path: queue.shift() });
    folders += 1;
    for (const entry of entries) (entry.dir ? queue : files).push(entry.path);
  }
  listed = { key, files, partial: queue.length > 0 };
  return listed;
}

/** Ctrl+K: one field that finds a view, a file, a persona or a command. */
export function Palette({ onClose, onView, onOpen, onConnect, onWorkspace, onTheme, onCloseStudio, report }) {
  const busy = useStudio((state) => state.busy);
  const mode = useStudio((state) => state.settings.mode);
  const model = useStudio((state) => state.model);
  const disk = useStudio((state) => state.disk);
  const epoch = useStudio((state) => state.epoch);
  const personas = usePersonas().data?.personas || [];
  const [query, setQuery] = useState("");
  const [index, setIndex] = useState(0);
  const [files, setFiles] = useState(listed.key === `${epoch}:${disk}` ? listed : { files: [], partial: false });
  const list = useRef(null);

  useEffect(() => {
    let live = true;
    workspaceFiles(`${epoch}:${disk}`).then(
      (found) => live && setFiles(found),
      () => {},
    );
    return () => {
      live = false;
    };
  }, []);

  const items = useMemo(() => {
    const rows = [];
    rows.push({ group: "Go to", label: "Agent log", words: "view agent log chat", run: () => onView("agent") });
    for (const [name, label] of VIEWS) rows.push({ group: "Go to", label, words: "view " + name, run: () => onView(name) });
    rows.push(
      { group: "Command", label: model ? "Change the model" : "Connect a model", words: "provider connection key", run: onConnect },
      {
        group: "Command",
        label: "New session",
        words: "reset chat clear",
        off: busy && "a turn is running",
        run: () => api("reset", {}).catch(report),
      },
      { group: "Command", label: "Open another workspace", words: "folder switch project", off: busy && "a turn is running", run: onWorkspace },
      {
        group: "Command",
        label: mode === "team" ? "Run tasks with one agent" : "Run tasks with the team",
        words: "mode solo team pipeline stages",
        off: busy && "a turn is running",
        run: () => configure({ mode: mode === "team" ? "solo" : "team" }).catch(report),
      },
      { group: "Command", label: "Switch colour theme", words: "dark light auto appearance", keep: true, run: onTheme },
    );
    if (onCloseStudio) rows.push({ group: "Command", label: "Close Studio", words: "quit exit stop server", run: onCloseStudio });
    for (const path of files.files) rows.push({ group: "File", label: path, words: "open", mono: true, run: () => onOpen(path) });
    for (const row of personas)
      rows.push({
        group: "Persona",
        label: row.name,
        note: personaTitle(row.division),
        words: `use ${row.division} ${row.id}`,
        off: mode === "team" && "the team is on",
        run: () => studio.set({ picked: row.id }),
      });
    return rows;
  }, [files, personas, busy, mode, model]);

  const shown = useMemo(() => {
    const terms = query.toLowerCase().split(/\s+/).filter(Boolean);
    if (!terms.length) return items.filter((item) => item.group === "Go to" || item.group === "Command");
    const scored = [];
    for (const item of items) {
      const label = item.label.toLowerCase();
      const text = `${item.group} ${label} ${item.words} ${item.note || ""}`.toLowerCase();
      if (!terms.every((term) => text.includes(term))) continue;
      // Matches in the visible label rank above matches in the hidden keywords; earlier is better.
      const at = label.indexOf(terms[0]);
      scored.push([at < 0 ? 500 : at + (label.startsWith(terms[0]) ? -50 : 0), item]);
    }
    return scored.sort((a, b) => a[0] - b[0]).map((entry) => entry[1]);
  }, [items, query]);
  const visible = shown.slice(0, MOST);
  const current = Math.min(index, visible.length - 1);

  useEffect(() => {
    list.current?.querySelector('[aria-selected="true"]')?.scrollIntoView({ block: "nearest" });
  }, [current, query]);

  function run(item) {
    if (!item || item.off) return;
    if (!item.keep) onClose();
    item.run();
  }

  function onKey(event) {
    if (event.key === "ArrowDown") setIndex(Math.min(visible.length - 1, current + 1));
    else if (event.key === "ArrowUp") setIndex(Math.max(0, current - 1));
    else if (event.key === "Home" && !query) setIndex(0);
    else if (event.key === "End" && !query) setIndex(visible.length - 1);
    else if (event.key === "Enter") run(visible[current]);
    else return;
    event.preventDefault();
  }

  let group = "";
  return (
    <Dialog title="Search and commands" className="palette" onClose={onClose}>
      <input
        type="search"
        className="palette-input"
        role="combobox"
        aria-expanded="true"
        aria-controls="palette-list"
        aria-activedescendant={visible.length ? "palette-" + current : undefined}
        aria-label="Search views, files, personas and commands"
        placeholder="Type a view, a file name, a persona or a command"
        autoComplete="off"
        spellCheck={false}
        value={query}
        data-autofocus
        onChange={(event) => {
          setQuery(event.target.value);
          setIndex(0);
        }}
        onKeyDown={onKey}
      />
      <ul className="palette-list" id="palette-list" role="listbox" aria-label="Results" ref={list}>
        {visible.map((item, at) => {
          const head = item.group !== group ? item.group : null;
          group = item.group;
          return (
            <li
              key={item.group + item.label}
              id={"palette-" + at}
              role="option"
              aria-selected={at === current}
              aria-disabled={item.off ? "true" : undefined}
              className={head ? "first" : ""}
              onMouseMove={() => at !== current && setIndex(at)}
              onClick={() => run(item)}
            >
              <span className="palette-group">{head}</span>
              <span className={"palette-label" + (item.mono ? " mono" : "")}>{item.label}</span>
              <span className="palette-note">{item.off ? "not now: " + item.off : item.note}</span>
            </li>
          );
        })}
        {!visible.length && <li className="palette-none">Nothing matches "{query}".</li>}
      </ul>
      <p className="palette-foot">
        <span>
          <kbd>↑</kbd> <kbd>↓</kbd> to move, <kbd>Enter</kbd> to choose, <kbd>Esc</kbd> to close.
        </span>
        <span>
          {shown.length > MOST ? `First ${MOST} of ${shown.length} matches. ` : ""}
          {query ? "" : "Type to search files and personas."}
          {files.partial && query ? " Large workspace: not every file is listed." : ""}
        </span>
      </p>
    </Dialog>
  );
}
