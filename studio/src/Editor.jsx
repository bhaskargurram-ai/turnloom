import React, { forwardRef, memo, useEffect, useImperativeHandle, useRef, useState } from "react";
import { Compartment, EditorState, Transaction } from "@codemirror/state";
import {
  crosshairCursor,
  drawSelection,
  dropCursor,
  EditorView,
  highlightActiveLine,
  highlightActiveLineGutter,
  highlightSpecialChars,
  keymap,
  lineNumbers,
  rectangularSelection,
} from "@codemirror/view";
import { defaultKeymap, history, historyKeymap, indentWithTab } from "@codemirror/commands";
import { bracketMatching, foldGutter, foldKeymap, indentOnInput, indentUnit, syntaxHighlighting } from "@codemirror/language";
import { highlightSelectionMatches, searchKeymap } from "@codemirror/search";
import { autocompletion, closeBrackets, closeBracketsKeymap, completionKeymap } from "@codemirror/autocomplete";
import { classHighlighter } from "@lezer/highlight";
import { python } from "@codemirror/lang-python";
import { javascript } from "@codemirror/lang-javascript";
import { markdown } from "@codemirror/lang-markdown";
import { json } from "@codemirror/lang-json";
import { html } from "@codemirror/lang-html";
import { css } from "@codemirror/lang-css";
import { yaml } from "@codemirror/lang-yaml";
import { api } from "./api.js";
import { useStudio } from "./store.js";
import { Confirm, Dialog } from "./Dialog.jsx";
import { DiffView } from "./DiffView.jsx";
import { unifiedDiff } from "./diff.js";

const LANGUAGES = [
  [/\.(py|pyi)$/i, "Python", () => python(), "    "],
  [/\.(m|c)?jsx?$/i, "JavaScript", (name) => javascript({ jsx: /x$/i.test(name) }), "  "],
  [/\.(m|c)?tsx?$/i, "TypeScript", (name) => javascript({ typescript: true, jsx: /x$/i.test(name) }), "  "],
  [/\.(md|markdown)$/i, "Markdown", () => markdown(), "  "],
  [/\.(json|jsonl|webmanifest)$/i, "JSON", () => json(), "  "],
  [/\.(html?|svg|xml)$/i, "HTML", () => html(), "  "],
  [/\.css$/i, "CSS", () => css(), "  "],
  [/\.ya?ml$/i, "YAML", () => yaml(), "  "],
];

function language(path) {
  const found = LANGUAGES.find(([pattern]) => pattern.test(path));
  return found
    ? { name: found[1], extension: [found[2](path), indentUnit.of(found[3])] }
    : { name: "Plain text", extension: [] };
}

// The usual editor behaviour, listed here so that only what Studio uses is bundled: line numbers,
// folding, history, bracket handling, search, completion from the language, multiple cursors.
const setup = [
  lineNumbers(),
  highlightActiveLineGutter(),
  highlightSpecialChars(),
  history(),
  foldGutter(),
  drawSelection(),
  dropCursor(),
  EditorState.allowMultipleSelections.of(true),
  indentOnInput(),
  bracketMatching(),
  closeBrackets(),
  autocompletion(),
  rectangularSelection(),
  crosshairCursor(),
  highlightActiveLine(),
  highlightSelectionMatches(),
  keymap.of([...closeBracketsKeymap, ...defaultKeymap, ...searchKeymap, ...historyKeymap, ...foldKeymap, ...completionKeymap, indentWithTab]),
];

const normal = (text) => text.replace(/\r\n?/g, "\n");

// Tab indents inside the editor, so the accessible description says how to leave it.
const named = (path) =>
  EditorView.contentAttributes.of({
    "aria-label": "Editor, " + path,
    "aria-description": "Press Escape, then Tab, to leave the editor.",
  });

// Colours come from the page's custom properties, so the editor follows the theme toggle.
const chrome = EditorView.theme({
  "&": { height: "100%", color: "var(--ink)", backgroundColor: "var(--surface)" },
  "&.cm-focused": { outline: "none" },
  ".cm-scroller": { fontFamily: "var(--mono)", fontSize: "13px", lineHeight: "1.55", overflow: "auto" },
  ".cm-content": { caretColor: "var(--accent)", padding: "8px 0" },
  ".cm-cursor, .cm-dropCursor": { borderLeftColor: "var(--accent)", borderLeftWidth: "2px" },
  ".cm-gutters": {
    backgroundColor: "var(--surface)",
    color: "var(--ink-3)",
    border: "none",
    borderRight: "1px solid var(--rule)",
  },
  ".cm-lineNumbers .cm-gutterElement": { padding: "0 10px 0 12px", minWidth: "36px" },
  ".cm-activeLine": { backgroundColor: "var(--line-active)" },
  ".cm-activeLineGutter": { backgroundColor: "var(--line-active)", color: "var(--ink)" },
  "&.cm-focused > .cm-scroller > .cm-selectionLayer .cm-selectionBackground, .cm-selectionBackground, .cm-content ::selection":
    { backgroundColor: "var(--selection)" },
  ".cm-selectionMatch": { backgroundColor: "var(--match)" },
  ".cm-matchingBracket, &.cm-focused .cm-matchingBracket": {
    backgroundColor: "transparent",
    outline: "1px solid var(--rule-strong)",
  },
  ".cm-foldPlaceholder": { backgroundColor: "var(--sunken)", border: "1px solid var(--rule)", color: "var(--ink-2)" },
  ".cm-panels": { backgroundColor: "var(--sunken)", color: "var(--ink)", borderColor: "var(--rule)" },
  ".cm-panels.cm-panels-bottom": { borderTop: "1px solid var(--rule)" },
  ".cm-panel input, .cm-panel button": { fontFamily: "var(--sans)", fontSize: "12px" },
  ".cm-searchMatch": { backgroundColor: "var(--match)", outline: "1px solid var(--rule-strong)" },
  ".cm-searchMatch.cm-searchMatch-selected": { backgroundColor: "var(--selection)" },
  ".cm-tooltip": {
    backgroundColor: "var(--surface)",
    color: "var(--ink)",
    border: "1px solid var(--rule-strong)",
    borderRadius: "2px",
  },
  ".cm-tooltip-autocomplete ul li[aria-selected]": { backgroundColor: "var(--accent)", color: "var(--on-accent)" },
});

/**
 * One editor view lives for the whole page. Opening a file swaps its state; saving and
 * agent turns only send change transactions, so scroll, focus, selection and undo survive.
 */
export const Editor = memo(
  forwardRef(function Editor({ report, onPath, onDirty }, handle) {
    const disk = useStudio((state) => state.disk);
    const busy = useStudio((state) => state.busy);
    const host = useRef(null);
    const view = useRef(null);
    const lang = useRef(new Compartment());
    const label = useRef(new Compartment());
    const current = useRef(null); // {path, version, saved: Text, language}
    const saving = useRef(false);
    const reading = useRef(0);
    const [path, setPath] = useState(null);
    const [dirty, setDirty] = useState(false);
    const [cursor, setCursor] = useState({ line: 1, column: 1, selected: 0 });
    const [conflict, setConflict] = useState(null); // {text, version} | {missing: true}
    const [comparing, setComparing] = useState(null);
    const [leaving, setLeaving] = useState(null);
    const [status, setStatus] = useState("");
    const [loading, setLoading] = useState(false);

    function mark(next) {
      setDirty((old) => (old === next ? old : next));
      onDirty(next);
    }

    function listener(update) {
      const file = current.current;
      if (!file) return;
      if (update.docChanged) mark(!update.state.doc.eq(file.saved));
      if (update.docChanged || update.selectionSet) {
        const range = update.state.selection.main;
        const line = update.state.doc.lineAt(range.head);
        const next = { line: line.number, column: range.head - line.from + 1, selected: range.to - range.from };
        setCursor((old) =>
          old.line === next.line && old.column === next.column && old.selected === next.selected ? old : next,
        );
      }
    }

    function stateFor(name, text) {
      const kind = language(name);
      return {
        kind,
        state: EditorState.create({
          doc: normal(text),
          extensions: [
            setup,
            chrome,
            syntaxHighlighting(classHighlighter),
            EditorView.lineWrapping,
            lang.current.of(kind.extension),
            label.current.of(named(name)),
            EditorView.updateListener.of(listener),
          ],
        }),
      };
    }

    useEffect(() => {
      view.current = new EditorView({ parent: host.current, state: EditorState.create({ doc: "" }) });
      return () => view.current.destroy();
    }, []);

    function show(name, data) {
      const { kind, state } = stateFor(name, data.text);
      view.current.setState(state);
      view.current.scrollDOM.scrollTop = 0;
      current.current = { path: name, version: data.version, saved: state.doc, language: kind.name };
      setPath(name);
      onPath(name);
      mark(false);
      setConflict(null);
      setStatus("");
      setCursor({ line: 1, column: 1, selected: 0 });
    }

    async function load(name) {
      const id = ++reading.current;
      setLoading(true);
      try {
        const data = await api("read", { path: name });
        if (id === reading.current) show(name, data);
      } finally {
        if (id === reading.current) setLoading(false);
      }
    }

    function close() {
      reading.current += 1;
      current.current = null;
      view.current.setState(EditorState.create({ doc: "" }));
      setPath(null);
      onPath(null);
      mark(false);
      setConflict(null);
    }

    function isDirty() {
      const file = current.current;
      return !!file && !view.current.state.doc.eq(file.saved);
    }

    // Replace the buffer with new text as one change, keeping the caret and scroll position.
    function replace(text, version) {
      const file = current.current;
      const before = view.current.state.doc.toString();
      const after = normal(text);
      let head = 0;
      const limit = Math.min(before.length, after.length);
      while (head < limit && before.charCodeAt(head) === after.charCodeAt(head)) head += 1;
      let tail = 0;
      while (
        tail < limit - head &&
        before.charCodeAt(before.length - 1 - tail) === after.charCodeAt(after.length - 1 - tail)
      )
        tail += 1;
      if (before !== after)
        view.current.dispatch({
          changes: { from: head, to: before.length - tail, insert: after.slice(head, after.length - tail) },
          annotations: Transaction.addToHistory.of(false),
        });
      file.version = version;
      file.saved = view.current.state.doc;
      mark(false);
    }

    // Compare the open file with disk. A clean buffer follows the disk; a dirty one is never overwritten.
    async function sync() {
      const file = current.current;
      if (!file || saving.current) return;
      let data;
      try {
        data = await api("read", { path: file.path });
      } catch (error) {
        if (current.current !== file) return;
        if (/no longer exists|No such file/i.test(error.message)) setConflict({ missing: true });
        return;
      }
      if (current.current !== file || saving.current) return;
      if (data.version === file.version) {
        setConflict((old) => (old ? null : old));
        return;
      }
      if (isDirty()) {
        setConflict({ text: data.text, version: data.version });
      } else {
        replace(data.text, data.version);
        setConflict(null);
        setStatus("Reloaded from disk");
      }
    }

    async function save(force) {
      const file = current.current;
      if (!file || saving.current) return;
      if (!force && !isDirty()) return;
      const doc = view.current.state.doc;
      saving.current = true;
      setStatus("Saving");
      try {
        const data = await api("save", {
          path: file.path,
          version: force?.version || file.version,
          text: doc.toString(),
        });
        if (current.current !== file) return;
        file.version = data.version;
        file.saved = doc;
        // Anything typed while the request was in flight is still in the buffer and still unsaved.
        mark(!view.current.state.doc.eq(doc));
        setConflict(null);
        setStatus("Saved");
      } catch (error) {
        if (current.current !== file) return;
        setStatus("");
        if (/changed on disk/i.test(error.message)) {
          saving.current = false;
          const data = await api("read", { path: file.path });
          if (current.current === file) setConflict({ text: data.text, version: data.version });
        } else if (/no longer exists|No such file/i.test(error.message)) {
          setConflict({ missing: true });
        } else {
          throw error;
        }
      } finally {
        saving.current = false;
      }
    }

    async function recreate() {
      const file = current.current;
      await api("create", { path: file.path });
      const data = await api("read", { path: file.path });
      if (current.current !== file) return;
      setConflict(null);
      await save({ version: data.version });
    }

    useImperativeHandle(handle, () => ({
      open(name) {
        if (current.current?.path === name) return view.current.focus();
        if (isDirty()) return setLeaving(name);
        load(name).catch(report);
      },
      renamed(from, to) {
        const file = current.current;
        if (file?.path !== from) return;
        const kind = language(to);
        file.path = to;
        file.language = kind.name;
        view.current.dispatch({
          effects: [
            lang.current.reconfigure(kind.extension),
            label.current.reconfigure(named(to)),
          ],
        });
        setPath(to);
        onPath(to);
      },
      deleted(name) {
        if (current.current?.path === name) close();
      },
      text() {
        return current.current ? view.current.state.doc.toString() : "";
      },
    }));

    useEffect(() => {
      sync().catch(() => {});
    }, [disk]);

    useEffect(() => {
      const onFocus = () => sync().catch(() => {});
      const onKey = (event) => {
        if ((event.ctrlKey || event.metaKey) && !event.altKey && event.key.toLowerCase() === "s") {
          event.preventDefault();
          save().catch(report);
        }
      };
      const onLeave = (event) => {
        if (isDirty()) {
          event.preventDefault();
          event.returnValue = "";
        }
      };
      window.addEventListener("focus", onFocus);
      window.addEventListener("keydown", onKey);
      window.addEventListener("beforeunload", onLeave);
      return () => {
        window.removeEventListener("focus", onFocus);
        window.removeEventListener("keydown", onKey);
        window.removeEventListener("beforeunload", onLeave);
      };
    }, []);

    useEffect(() => {
      if (!status || status === "Saving") return;
      const timer = setTimeout(() => setStatus(""), 2500);
      return () => clearTimeout(timer);
    }, [status]);

    const name = path ? path.split("/").pop() : "";
    const folder = path && path.includes("/") ? path.slice(0, path.lastIndexOf("/") + 1) : "";

    return (
      <section className="pane document" aria-label="Editor">
        <header className="bar">
          {path ? (
            <h2 className="doc-path" title={path}>
              <span className="doc-folder">{folder}</span>
              <span className="doc-name">{name}</span>
              {dirty && <span className="doc-dirty">unsaved</span>}
            </h2>
          ) : (
            <h2 className="label">Editor</h2>
          )}
          <span className="bar-tools">
            <button
              type="button"
              className="small"
              disabled={!path || !dirty}
              title="Save (Ctrl+S)"
              onMouseDown={(event) => event.preventDefault()}
              onClick={() => {
                // Saving from the toolbar leaves the caret where it was, ready for more typing.
                view.current.focus();
                save().catch(report);
              }}
            >
              Save
            </button>
          </span>
        </header>
        {conflict && path && (
          <div className="banner warn" role="alert">
            {conflict.missing ? (
              <>
                <p>
                  <strong>{name} was removed from disk.</strong> Your text is still here.
                </p>
                <span className="banner-actions">
                  <button
                    type="button"
                    className="small"
                    disabled={busy}
                    title={busy ? "Wait for the current turn to finish" : undefined}
                    onClick={() => recreate().catch(report)}
                  >
                    Save as a new file
                  </button>
                  <button type="button" className="small" onClick={close}>
                    Close without saving
                  </button>
                </span>
              </>
            ) : (
              <>
                <p>
                  <strong>{name} changed on disk</strong> while you have unsaved edits. Nothing was overwritten.
                </p>
                <span className="banner-actions">
                  <button type="button" className="small" onClick={() => setComparing(conflict)}>
                    View diff
                  </button>
                  <button type="button" className="small" onClick={() => {
                      replace(conflict.text, conflict.version);
                      setConflict(null);
                      setStatus("Reloaded from disk");
                    }}>
                    Reload from disk
                  </button>
                  <button type="button" className="small" onClick={() => save({ version: conflict.version }).catch(report)}>
                    Overwrite with my version
                  </button>
                </span>
              </>
            )}
          </div>
        )}
        <div className="editor-host" ref={host} hidden={!path} />
        {!path && (
          <div className="empty">
            <p className="empty-title">{loading ? "Opening" : "No file open"}</p>
            <p>
              Choose a file from the list to read or edit it here. Files the agent changes update in place, and your
              unsaved edits are never overwritten.
            </p>
          </div>
        )}
        <footer className="doc-foot">
          {path ? (
            <>
              <span className="num">
                Ln {cursor.line}, Col {cursor.column}
                {cursor.selected ? ` (${cursor.selected} selected)` : ""}
              </span>
              <span>{current.current?.language}</span>
              <span>UTF-8</span>
              <span className="doc-state" role="status">
                {dirty ? "Unsaved changes" : status || "Saved"}
              </span>
            </>
          ) : (
            <span>Text files up to 512 KiB</span>
          )}
        </footer>
        {comparing && (
          <Dialog title={"Disk and your version of " + name} wide onClose={() => setComparing(null)}>
            <Compare before={comparing.text} after={view.current.state.doc.toString()} name={path} />
          </Dialog>
        )}
        {leaving && (
          <Confirm
            title="Discard unsaved edits"
            action="Discard and open"
            danger
            onClose={() => setLeaving(null)}
            onConfirm={() => {
              const target = leaving;
              setLeaving(null);
              load(target).catch(report);
            }}
          >
            <p>
              <code>{path}</code> has unsaved edits. Opening <code>{leaving}</code> discards them.
            </p>
          </Confirm>
        )}
      </section>
    );
  }),
);

function Compare({ before, after, name }) {
  const text = unifiedDiff(before, after, name);
  if (text === null)
    return <p className="note-line">The two versions differ in too many lines to show a readable diff.</p>;
  if (!text) return <p className="note-line">The two versions have the same lines. Only line endings differ.</p>;
  return (
    <>
      <p className="note-line">
        Lines marked <b>−</b> are on disk now. Lines marked <b>+</b> are your unsaved version.
      </p>
      <DiffView text={text} label="Disk compared with your version" />
    </>
  );
}
