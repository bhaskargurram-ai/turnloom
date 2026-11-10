import React, { useState } from "react";
import { ChevronDown, ChevronRight, Layers3, Link2, X } from "lucide-react";
import { api } from "./api.js";

export function ContextLoom({ state, report }) {
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
