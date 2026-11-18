import React from "react";
import { Reload } from "./icons.jsx";

/** The frame every data view shares: a heading with one sentence of purpose, then ruled sections. */
export function Page({ title, lead, tools, active, onReload, error, children }) {
  return (
    <section className="page" hidden={!active} aria-label={title}>
      <header className="page-head">
        <div>
          <h2>{title}</h2>
          {lead && <p>{lead}</p>}
        </div>
        <span className="page-tools">
          {tools}
          {onReload && (
            <button type="button" className="small" onClick={onReload} title={"Read " + title.toLowerCase() + " again"}>
              <Reload /> Refresh
            </button>
          )}
        </span>
      </header>
      {error && (
        <p className="problem" role="alert">
          {error}
        </p>
      )}
      {children}
    </section>
  );
}

export function Block({ title, note, tools, className = "", children }) {
  return (
    <section className={"block " + className}>
      <header className="block-head">
        <h3 className="label">{title}</h3>
        {tools && <span className="block-tools">{tools}</span>}
      </header>
      {note && <p className="block-note">{note}</p>}
      {children}
    </section>
  );
}

/** A row of headline numbers: label above, value in mono, one line of context below. */
export function Figures({ items }) {
  return (
    <dl className="figures">
      {items.map((item) => (
        <div key={item.label} className={item.tone || ""}>
          <dt className="label">{item.label}</dt>
          <dd className="figure-value num">{item.value}</dd>
          <dd className="figure-detail">{item.detail}</dd>
        </div>
      ))}
    </dl>
  );
}

/** What to show in place of a view whose optional package is not installed. */
export function Install({ title, active, what, extra, after, children }) {
  return (
    <section className="page" hidden={!active} aria-label={title}>
      <header className="page-head">
        <div>
          <h2>{title}</h2>
        </div>
      </header>
      <div className="empty wide">
        <p className="empty-title">{what} is not installed</p>
        <p>{children}</p>
        <p>Install it into the Python environment that runs Turnloom, then start Studio again:</p>
        <pre className="output">python -m pip install "turnloom[{extra}]"</pre>
      </div>
      {after}
    </section>
  );
}

/** A labelled on/off control with a sentence saying what it does. */
export function Switch({ label, detail, checked, disabled, onChange, tone }) {
  return (
    <label className={"switch" + (tone ? " " + tone : "")}>
      <input type="checkbox" checked={checked} disabled={disabled} onChange={(event) => onChange(event.target.checked)} />
      <span>
        <b>{label}</b>
        <small>{detail}</small>
      </span>
    </label>
  );
}
