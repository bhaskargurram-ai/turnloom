import React, { useLayoutEffect, useRef, useState } from "react";
import { compact, count } from "./util.js";

// Charts are drawn by hand as SVG in the page's own tokens: hairline axes, square marks,
// values in ink. Series colour marks identity only; every chart has a legend or a table.

function useWidth() {
  const node = useRef(null);
  const [width, setWidth] = useState(0);
  useLayoutEffect(() => {
    const measure = () => {
      const next = Math.floor(node.current.getBoundingClientRect().width);
      if (next) setWidth((old) => (old === next ? old : next));
    };
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(node.current);
    return () => observer.disconnect();
  }, []);
  return [node, width];
}

function ceiling(value) {
  if (value <= 0) return 1;
  const power = 10 ** Math.floor(Math.log10(value));
  const lead = value / power;
  return (lead <= 1 ? 1 : lead <= 2 ? 2 : lead <= 5 ? 5 : 10) * power;
}

export function Legend({ series }) {
  return (
    <ul className="legend">
      {series.map((item) => (
        <li key={item.key}>
          <i className={"chip " + item.tone} aria-hidden="true" />
          {item.label}
          {item.value != null && <span className="num dim">{item.value}</span>}
        </li>
      ))}
    </ul>
  );
}

/**
 * Vertical bars, one per row, each a stack of the given series. `rows` are
 * {label, title, values: {key: number}}. A single series is simply a stack of one.
 */
export function Bars({ rows, series, format = compact, axis = format, height = 168, label }) {
  const [node, width] = useWidth();
  const [hover, setHover] = useState(-1);
  const bottom = 18;
  const top = 6;
  const plot = height - top - bottom;
  const totals = rows.map((row) => series.reduce((sum, item) => sum + (row.values[item.key] || 0), 0));
  const max = ceiling(Math.max(...totals, 0));
  // The axis column is as wide as its longest label needs, at 6.4px a character of 10.5px mono.
  const left = 12 + 6.4 * Math.max(...[0, 0.5, 1].map((share) => String(axis(max * share)).length));
  const inner = Math.max(0, width - left - 4);
  const slot = rows.length ? inner / rows.length : 0;
  const bar = Math.max(1, Math.min(22, slot - (slot > 6 ? 2 : 0)));
  const every = Math.max(1, Math.ceil(rows.length / Math.max(1, Math.floor(inner / 44))));
  const shown = hover >= 0 ? rows[hover] : null;
  return (
    <div className="chart" ref={node}>
      {width > 0 && (
        <svg width={width} height={height} role="img" aria-label={label}>
          {[0, 0.5, 1].map((share) => {
            const y = top + plot - plot * share;
            return (
              <g key={share}>
                <line className="grid-line" x1={left} x2={width} y1={y + 0.5} y2={y + 0.5} />
                <text className="tick" x={left - 6} y={y + 4} textAnchor="end">
                  {axis(max * share)}
                </text>
              </g>
            );
          })}
          {rows.map((row, index) => {
            const x = left + slot * index + (slot - bar) / 2;
            let y = top + plot;
            return (
              <g key={index} className={hover === index ? "lit" : ""}>
                {series.map((item) => {
                  const value = row.values[item.key] || 0;
                  const size = (value / max) * plot;
                  y -= size;
                  // A hairline of surface between segments keeps neighbours apart without a border.
                  return size > 0 ? (
                    <rect key={item.key} className={"mark " + item.tone} x={x} y={y + (size > 2 ? 1 : 0)} width={bar} height={Math.max(0.5, size - (size > 2 ? 1 : 0))} />
                  ) : null;
                })}
                {index % every === 0 && (
                  <text className="tick" x={x + bar / 2} y={height - 4} textAnchor="middle">
                    {row.label}
                  </text>
                )}
                <rect
                  className="hit"
                  x={left + slot * index}
                  y={top}
                  width={slot}
                  height={plot}
                  onMouseEnter={() => setHover(index)}
                  onMouseLeave={() => setHover(-1)}
                />
              </g>
            );
          })}
        </svg>
      )}
      <p className="chart-read" role="status">
        {shown
          ? shown.title + ": " + series.map((item) => `${item.label.toLowerCase()} ${format(shown.values[item.key] || 0)}`).join(", ")
          : rows.length
            ? "Point at a bar to read its values."
            : ""}
      </p>
    </div>
  );
}

/** One horizontal bar for the context window: what fills it, and how much is still free. */
export function Composition({ parts, used, window: size, label }) {
  const sum = parts.reduce((total, part) => total + part.value, 0) || 1;
  const scale = size ? used / size : 0;
  return (
    <div className="composition">
      <div className="composition-track" role="img" aria-label={label}>
        {parts.map((part) =>
          part.value ? (
            <i key={part.key} className={"chip " + part.tone} style={{ width: (part.value / sum) * scale * 100 + "%" }} title={part.label} />
          ) : null,
        )}
      </div>
      <div className="composition-scale num" aria-hidden="true">
        <span>0</span>
        <span>{compact(size)} tokens</span>
      </div>
    </div>
  );
}

/** The prompt size of each recent model call as a small line. */
export function Spark({ values, label }) {
  if (values.length < 2) return null;
  const width = 120;
  const height = 26;
  const max = Math.max(...values, 1);
  const points = values.map((value, index) => `${((index / (values.length - 1)) * (width - 2) + 1).toFixed(1)},${(height - 2 - (value / max) * (height - 4)).toFixed(1)}`);
  return (
    <svg className="spark" width={width} height={height} role="img" aria-label={label + ": " + values.map(count).join(", ")}>
      <polyline points={points.join(" ")} />
    </svg>
  );
}
