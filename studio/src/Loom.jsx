import React, { memo, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { api } from "./api.js";
import { useStudio } from "./store.js";
import { Dialog } from "./Dialog.jsx";
import { DiffView } from "./DiffView.jsx";
import { Caret } from "./icons.jsx";
import { count, family, FAMILY_LABEL, FAMILY_ORDER, plural } from "./util.js";

const SPACING = 4; // pixels between warp threads where nothing has been folded
const PAGE = 8000; // the most one recall request returns
const EAGER = 64000; // archives up to this size are fetched whole when opened

const toolName = (row) => (row.tool === "exchange" ? "earlier exchange" : row.tool);

// The label names the call ("read_file app.py"); a shell row's label is the bare command.
function described(row) {
  const name = toolName(row);
  const label = row.label || "";
  if (row.tool === "exchange" || !label || label === name) return [name, ""];
  return [name, label.startsWith(name) ? label.slice(name.length).trim() : label];
}

/**
 * The figure: a warp of evenly spaced threads on the left, one per equal share of the
 * original tool text. Each thread runs to where its text sits in the prompt now, so a
 * folded result pulls its threads into a tight cord and an archived exchange ends at the fold.
 */
function Figure({ rows, total, kept, hover, onHover, onPick }) {
  const frame = useRef(null);
  const [size, setSize] = useState({ width: 0, height: 0 });
  useLayoutEffect(() => {
    const node = frame.current;
    const measure = () => {
      const box = node.getBoundingClientRect();
      setSize((old) =>
        Math.abs(old.width - box.width) < 1 && Math.abs(old.height - box.height) < 1
          ? old
          : { width: box.width, height: box.height },
      );
    };
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(node);
    return () => observer.disconnect();
  }, []);

  const { width, height } = size;
  const threads = useMemo(() => {
    if (!width || !height || !total) return [];
    const amount = Math.max(1, Math.floor(height / SPACING));
    const top = (height - (height * kept) / total) / 2;
    const out = [];
    let index = 0;
    let before = 0;
    let after = 0;
    for (let strand = 0; strand < amount; strand++) {
      const left = ((strand + 0.5) * height) / amount;
      const position = (left / height) * total;
      while (index < rows.length - 1 && position >= before + rows[index].before) {
        before += rows[index].before;
        after += rows[index].after;
        index += 1;
      }
      const row = rows[index];
      const share = row.before ? Math.min(1, (position - before) / row.before) : 0;
      const right = top + ((after + share * row.after) / total) * height;
      out.push({ row, left, right, cut: row.after === 0 });
    }
    return out;
  }, [rows, total, kept, width, height]);

  const foldStart = width * 0.36;
  const foldEnd = width * 0.64;

  function nearest(event) {
    const box = frame.current.getBoundingClientRect();
    const x = event.clientX - box.left;
    const y = event.clientY - box.top;
    let best = null;
    let distance = 9;
    for (const thread of threads) {
      if (thread.cut && x > foldEnd + 4) continue;
      const ratio = Math.min(1, Math.max(0, (x - foldStart) / (foldEnd - foldStart)));
      const at = thread.left + (thread.right - thread.left) * ratio;
      if (Math.abs(at - y) < distance) {
        distance = Math.abs(at - y);
        best = thread.row;
      }
    }
    return best;
  }

  return (
    <div className="loom-figure">
      <div className="loom-axis" aria-hidden="true">
        <span>
          Tool output
          <b className="num">{count(total)}</b>
        </span>
        <span>
          In prompt
          <b className="num">{count(kept)}</b>
        </span>
      </div>
      <div
        className={"loom-frame" + (hover ? " hovering" : "")}
        ref={frame}
        onMouseMove={(event) => onHover(nearest(event)?.id || null)}
        onMouseLeave={() => onHover(null)}
        onClick={(event) => {
          const row = nearest(event);
          if (row) onPick(row);
        }}
      >
        <svg
          width={width}
          height={height}
          role="img"
          aria-label={
            `Context Loom figure. ${count(total)} characters of tool output on the left narrow to ` +
            `${count(kept)} characters still in the prompt on the right. The table lists every row.`
          }
        >
          <line className="loom-beam" x1="0.5" x2="0.5" y1="0" y2={height} />
          <line className="loom-beam" x1={Math.max(0, width - 0.5)} x2={Math.max(0, width - 0.5)} y1="0" y2={height} />
          <line className="loom-reed" x1={foldStart} x2={foldStart} y1="0" y2={height} />
          <line className="loom-reed" x1={foldEnd} x2={foldEnd} y1="0" y2={height} />
          {threads.map((thread, index) => (
            <path
              key={index}
              className={"thread f-" + family(thread.row.tool) + (hover === thread.row.id ? " lit" : "")}
              d={
                `M0 ${thread.left.toFixed(1)}H${foldStart.toFixed(1)}L${foldEnd.toFixed(1)} ${thread.right.toFixed(1)}` +
                (thread.cut ? "" : `H${width.toFixed(1)}`)
              }
            />
          ))}
        </svg>
      </div>
      <p className="loom-scale" aria-hidden="true">
        {threads.length
          ? `One thread is about ${count(Math.max(1, Math.round(total / threads.length)))} characters.`
          : ""}
      </p>
    </div>
  );
}

const LedgerRow = memo(function LedgerRow({ row, index, lit, onHover, onRead }) {
  const kind = family(row.tool);
  const share = row.before ? Math.min(1, row.after / row.before) : 0;
  const folded = row.after < row.before;
  const [name, rest] = described(row);
  return (
    <tr
      className={(lit ? "lit " : "") + (folded ? "folded" : "")}
      onMouseEnter={() => onHover(row.id)}
      onMouseLeave={() => onHover(null)}
      data-row={row.id}
    >
      <td className="num dim">{index + 1}</td>
      <td className="loom-tool" title={row.label || name}>
        <i className={"swatch f-" + kind} aria-hidden="true" />
        <span className="loom-name">{name}</span>
        {rest && <span className="loom-rest">{rest}</span>}
      </td>
      <td className="num">{count(row.before)}</td>
      <td className="num">{count(row.after)}</td>
      <td className="loom-cell-bar" aria-hidden="true">
        <span className={"loom-bar f-" + kind}>
          <span className="kept" style={{ width: share * 100 + "%" }} />
          <span className="gone" style={{ width: (1 - share) * 100 + "%" }} />
        </span>
      </td>
      <td className="loom-action">
        {row.reference ? (
          <button
            type="button"
            className="link"
            aria-label={`Read original, row ${index + 1}, ${row.label || name}, ${count(row.before)} characters`}
            onClick={() => onRead(row)}
          >
            Read original
          </button>
        ) : (
          <span className="dim">in full</span>
        )}
      </td>
    </tr>
  );
});

export const Loom = memo(function Loom({ docked }) {
  const rows = useStudio((state) => state.loom);
  const [open, setOpen] = useState(() => {
    try {
      return localStorage.getItem("turnloom-loom") !== "closed";
    } catch {
      return true;
    }
  });
  const [onlyFolded, setOnlyFolded] = useState(false);
  const [hover, setHover] = useState(null);
  const [reading, setReading] = useState(null);
  const body = useRef(null);

  const stats = useMemo(() => {
    let total = 0;
    let kept = 0;
    let folded = 0;
    let archived = 0;
    const families = {};
    for (const row of rows) {
      total += row.before;
      kept += row.after;
      if (row.after < row.before) folded += 1;
      if (row.reference) archived += 1;
      families[family(row.tool)] = (families[family(row.tool)] || 0) + 1;
    }
    return { total, kept, out: Math.max(0, total - kept), folded, archived, families };
  }, [rows]);

  function toggle() {
    setOpen((old) => {
      try {
        localStorage.setItem("turnloom-loom", old ? "closed" : "open");
      } catch {
        // Remembering the choice is optional.
      }
      return !old;
    });
  }

  function pick(row) {
    if (row.reference) return setReading(row);
    const target = body.current?.querySelector(`[data-row="${CSS.escape(row.id)}"]`);
    target?.scrollIntoView({ block: "nearest" });
  }

  const shown = onlyFolded ? rows.filter((row) => row.after < row.before) : rows;
  const expanded = open || !docked;
  let summary;
  if (!rows.length) summary = "";
  else if (!stats.out)
    summary = `Nothing folded yet. ${plural(rows.length, "result")}, ${count(stats.total)} characters, all in the prompt.`;
  else
    summary =
      `${count(stats.out)} of ${count(stats.total)} characters folded out of the prompt ` +
      `(${Math.round((stats.out * 100) / stats.total)}%). ${count(stats.kept)} remain.`;

  return (
    <section className={"pane loom" + (expanded ? "" : " shut")} aria-label="Context Loom">
      <header className="bar">
        {docked ? (
          <button
            type="button"
            className="quiet bar-toggle"
            aria-expanded={open}
            title={open ? "Collapse the Context Loom" : "Expand the Context Loom"}
            onClick={toggle}
          >
            <Caret open={open} />
            <span className="label">Context Loom</span>
          </button>
        ) : (
          <h2 className="label">Context Loom</h2>
        )}
        <p className="loom-summary" role="status">
          {summary}
        </p>
      </header>
      {expanded &&
        (rows.length ? (
          <div className="loom-body">
            <div className="loom-left">
              <Figure rows={rows} total={stats.total} kept={stats.kept} hover={hover} onHover={setHover} onPick={pick} />
              <ul className="loom-legend" aria-label="Thread colours by tool">
                {FAMILY_ORDER.filter((name) => stats.families[name]).map((name) => (
                  <li key={name}>
                    <i className={"swatch f-" + name} aria-hidden="true" />
                    {FAMILY_LABEL[name]} <span className="num dim">{stats.families[name]}</span>
                  </li>
                ))}
              </ul>
            </div>
            <div className="loom-ledger" ref={body}>
              <table>
                <caption className="sr-only">
                  Every tool result in the conversation with its original size, the size still in the prompt, and a
                  control to read the archived original.
                </caption>
                <thead>
                  <tr>
                    <th scope="col" className="num">
                      #
                    </th>
                    <th scope="col">Tool</th>
                    <th scope="col" className="num">
                      Original
                    </th>
                    <th scope="col" className="num">
                      In prompt
                    </th>
                    <th scope="col">
                      <span className="loom-key" aria-hidden="true">
                        <span className="loom-bar">
                          <span className="kept" style={{ width: 14 }} />
                        </span>
                        kept
                        <span className="loom-bar">
                          <span className="gone" style={{ width: 14 }} />
                        </span>
                        folded
                      </span>
                    </th>
                    <th scope="col">
                      <label className="check">
                        <input
                          type="checkbox"
                          checked={onlyFolded}
                          onChange={(event) => setOnlyFolded(event.target.checked)}
                        />
                        Folded only
                      </label>
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {shown.map((row) => (
                    <LedgerRow
                      key={row.id}
                      row={row}
                      index={rows.indexOf(row)}
                      lit={hover === row.id}
                      onHover={setHover}
                      onRead={setReading}
                    />
                  ))}
                </tbody>
              </table>
              {!shown.length && <p className="note-line pad">No result has been folded yet.</p>}
            </div>
          </div>
        ) : (
          <div className="empty loom-empty">
            <p className="empty-title">No tool results yet</p>
            <p>
              Every file the agent reads, every search and every command adds a row of threads here, sized by how much
              text it returned. When older results are folded out of the prompt to save context, their threads pull
              tight, and the full original stays one click away in a local archive.
            </p>
          </div>
        ))}
      {reading && <Reader row={reading} onClose={() => setReading(null)} />}
    </section>
  );
});

// --- Reading an archived original ---------------------------------------------------------

function fromResult(value) {
  if (value == null || typeof value !== "object" || Array.isArray(value))
    return [{ text: typeof value === "string" ? value : JSON.stringify(value, null, 2) }];
  if (typeof value.preview === "string" && value.reference)
    return [
      { label: `Preview of an earlier archive (${value.reference})`, text: value.preview },
    ];
  if ("stdout" in value || "stderr" in value) {
    const parts = [{ label: value.error || "exit code " + value.exit_code, text: "" }];
    if (value.stdout) parts.push({ label: "stdout", text: value.stdout });
    if (value.stderr) parts.push({ label: "stderr", text: value.stderr });
    return parts;
  }
  if (typeof value.error === "string") return [{ label: "error", text: value.error }];
  if (typeof value.diff === "string") return [{ label: value.path, text: value.diff, diff: true }];
  if (typeof value.text === "string")
    return [{ label: value.path ? `${value.path}, from line ${value.line || 1}` : "", text: value.text }];
  for (const key of ["matches", "entries", "paths"])
    if (Array.isArray(value[key])) return [{ label: key, text: value[key].join("\n") || "No results." }];
  return [{ text: JSON.stringify(value, null, 2) }];
}

function parsed(text) {
  try {
    return JSON.parse(text);
  } catch {
    return text;
  }
}

/** Turn an archived record (one tool result, or a list of archived messages) into labelled sections. */
function sections(record) {
  if (!Array.isArray(record)) return fromResult(record);
  const out = [];
  for (const message of record) {
    if (!message || typeof message !== "object") continue;
    if (message.role === "tool") {
      for (const part of fromResult(parsed(message.content)))
        out.push({ ...part, label: "tool result" + (part.label ? ", " + part.label : "") });
      continue;
    }
    if (typeof message.content === "string" && message.content)
      out.push({ label: message.role === "user" ? "request" : "agent", text: message.content });
    for (const call of message.tool_calls || [])
      out.push({ label: "tool call", text: `${call.function?.name} ${call.function?.arguments}` });
  }
  return out;
}

function Reader({ row, onClose }) {
  const [text, setText] = useState("");
  const [total, setTotal] = useState(row.before);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(true);
  const [raw, setRaw] = useState(false);
  const alive = useRef(true);
  const held = useRef("");

  async function load(limit) {
    setBusy(true);
    setError("");
    try {
      let size = total;
      while (alive.current && held.current.length < Math.min(size, limit)) {
        const page = await api("recall", { reference: row.reference, offset: held.current.length, length: PAGE });
        if (!alive.current) return;
        size = page.total;
        if (!page.text) break;
        held.current += page.text;
        setText(held.current);
        setTotal(size);
      }
    } catch (failure) {
      if (alive.current) setError(failure.message);
    } finally {
      if (alive.current) setBusy(false);
    }
  }

  useEffect(() => {
    alive.current = true;
    load(EAGER);
    return () => {
      alive.current = false;
    };
  }, []);

  const complete = !busy && !error && text.length >= total;
  const parts = useMemo(() => {
    if (!complete || raw) return null;
    const record = parsed(text);
    return typeof record === "string" ? null : sections(record);
  }, [complete, raw, text]);

  return (
    <Dialog
      title={row.tool === "exchange" ? "Archived exchange" : "Archived result of " + (row.label || row.tool)}
      wide
      onClose={onClose}
      footer={
        <>
          <span className="reader-meta num">
            {count(Math.min(text.length, total))} of {count(total)} characters · reference {row.reference}
          </span>
          {!busy && text.length < total && !error && (
            <button type="button" onClick={() => load(Infinity)}>
              Load the remaining {count(total - text.length)} characters
            </button>
          )}
          <label className="check">
            <input type="checkbox" checked={raw} disabled={!complete} onChange={(event) => setRaw(event.target.checked)} />
            Show as stored
          </label>
        </>
      }
    >
      <p className="note-line">
        This is the full text from the local archive. The prompt now carries {count(row.after)} characters in its
        place. Reading it here does not use a model turn.
      </p>
      {error && (
        <p className="problem" role="alert">
          {error}
        </p>
      )}
      {parts ? (
        parts.map((part, index) => (
          <section className="reader-part" key={index}>
            {part.label && <h3 className="label">{part.label}</h3>}
            {part.diff ? <DiffView text={part.text} /> : part.text && <pre className="output">{part.text}</pre>}
          </section>
        ))
      ) : (
        <pre className="output" aria-busy={busy}>
          {text || (busy ? "Reading the archive" : "")}
        </pre>
      )}
    </Dialog>
  );
}
