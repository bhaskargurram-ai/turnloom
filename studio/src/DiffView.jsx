import React, { memo, useMemo, useState } from "react";
import { parseDiff, splitRows, unifiedDiff } from "./diff.js";
import { visible } from "./util.js";

const SIGN = { add: "+", del: "−", ctx: " " };
const SPOKEN = { add: "added", del: "removed" };

const digits = (rows) => String(rows.reduce((most, row) => Math.max(most, row.a || 0, row.b || 0), 0)).length;

/** A unified diff as numbered lines. Added and removed lines carry a sign as well as a colour. */
export const DiffView = memo(function DiffView({ text, label }) {
  const rows = useMemo(() => parseDiff(text), [text]);
  return (
    <div className="diff" role="group" aria-label={label || "File changes"} style={{ "--digits": digits(rows) }}>
      {rows.map((row, index) =>
        row.kind === "file" || row.kind === "hunk" || row.kind === "meta" ? (
          <div className={"dl " + row.kind} key={index}>
            {row.text}
          </div>
        ) : (
          <div className={"dl " + row.kind} key={index}>
            <span className="ln" aria-hidden="true">
              {row.a ?? ""}
            </span>
            <span className="ln" aria-hidden="true">
              {row.b ?? ""}
            </span>
            <span className="sg" aria-label={SPOKEN[row.kind]}>
              {SIGN[row.kind]}
            </span>
            <span className="tx">{visible(row.text)}</span>
          </div>
        ),
      )}
    </div>
  );
});

function Side({ row, side }) {
  if (!row) return <span className="half none" />;
  const kind = row.kind === "ctx" ? "ctx" : side === "left" ? "del" : "add";
  return (
    <span className={"half " + kind}>
      <span className="ln" aria-hidden="true">
        {side === "left" ? row.a : row.b}
      </span>
      <span className="sg" aria-label={SPOKEN[kind]}>
        {SIGN[kind]}
      </span>
      <span className="tx">{visible(row.text)}</span>
    </span>
  );
}

/** The same diff in two columns: the earlier text on the left, the later text on the right. */
export const SplitDiff = memo(function SplitDiff({ text, label, before = "Before", after = "After" }) {
  const rows = useMemo(() => parseDiff(text), [text]);
  const pairs = useMemo(() => splitRows(rows), [rows]);
  return (
    <div className="diff split" role="group" aria-label={label || "File changes"} style={{ "--digits": digits(rows) }}>
      <div className="dl sides" aria-hidden="true">
        <span>{before}</span>
        <span>{after}</span>
      </div>
      {pairs.map((pair, index) =>
        pair.band ? (
          pair.band.kind === "file" ? null : (
            <div className={"dl " + pair.band.kind} key={index}>
              {pair.band.text}
            </div>
          )
        ) : (
          <div className="dl pair" key={index}>
            <Side row={pair.left} side="left" />
            <Side row={pair.right} side="right" />
          </div>
        ),
      )}
    </div>
  );
});

function remembered() {
  try {
    return localStorage.getItem("turnloom-diff") === "split" ? "split" : "unified";
  } catch {
    return "unified";
  }
}

/**
 * Two versions of one file with a switch between the unified and the side-by-side layout.
 * Pass `diff` when the server already made one; otherwise it is computed here.
 */
export function DiffPanel({ original, modified, diff, name, before, after, tools }) {
  const [mode, setMode] = useState(remembered);
  const text = useMemo(() => (diff != null ? diff : unifiedDiff(original, modified, name)), [original, modified, diff, name]);
  function choose(next) {
    setMode(next);
    try {
      localStorage.setItem("turnloom-diff", next);
    } catch {
      // Remembering the layout is optional.
    }
  }
  return (
    <div className="diff-panel">
      <div className="diff-tools">
        <div className="segmented" role="group" aria-label="Diff layout">
          <button type="button" aria-pressed={mode === "unified"} onClick={() => choose("unified")}>
            Unified
          </button>
          <button type="button" aria-pressed={mode === "split"} onClick={() => choose("split")}>
            Side by side
          </button>
        </div>
        {tools}
      </div>
      <div className="diff-scroll" tabIndex={0} aria-label={"Changes to " + name}>
        {text === null ? (
          <p className="note-line pad">The two versions differ in too many lines to show line by line.</p>
        ) : !text ? (
          <p className="note-line pad">The two versions have the same lines.</p>
        ) : mode === "split" ? (
          <SplitDiff text={text} label={"Changes to " + name} before={before} after={after} />
        ) : (
          <DiffView text={text} label={"Changes to " + name} />
        )}
      </div>
    </div>
  );
}
