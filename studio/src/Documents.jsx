import React, { memo, useEffect, useRef, useState } from "react";
import { api } from "./api.js";
import { useLoad, useStudio } from "./store.js";
import { Block, Install, Page } from "./Page.jsx";
import { count } from "./util.js";

/** The source text with each grounded field marked where it was read. */
function Source({ text, fields, active, onSelect }) {
  const marks = useRef({});
  useEffect(() => {
    marks.current[active]?.scrollIntoView({ block: "nearest" });
  }, [active]);
  const spans = fields
    .filter((field) => field.grounding?.char_span && !field.grounding.page)
    .map((field) => ({ start: field.grounding.char_span[0], end: field.grounding.char_span[1], field }))
    .filter((span) => span.end > span.start && span.end <= text.length)
    .sort((a, b) => a.start - b.start);
  const parts = [];
  let cursor = 0;
  for (const span of spans) {
    if (span.start < cursor) continue;
    parts.push(text.slice(cursor, span.start));
    parts.push(
      <mark
        key={span.field.path}
        ref={(node) => {
          marks.current[span.field.path] = node;
        }}
        className={span.field.decision + (active === span.field.path ? " active" : "")}
        title={span.field.path}
        onClick={() => onSelect(span.field.path)}
      >
        {text.slice(span.start, span.end)}
      </mark>,
    );
    cursor = span.end;
  }
  parts.push(text.slice(cursor));
  return (
    <pre className="output source scroll-box" tabIndex={0} aria-label="Document text with the read values marked">
      {parts}
    </pre>
  );
}

export const Documents = memo(function Documents({ active, openPath }) {
  const busy = useStudio((state) => state.busy);
  const { data: meta, error: metaError } = useLoad("schemas", {}, "once", active);
  const [source, setSource] = useState("file");
  const [path, setPath] = useState(openPath || "");
  const [text, setText] = useState("");
  const [schema, setSchema] = useState("");
  const [template, setTemplate] = useState("");
  const [threshold, setThreshold] = useState(0.8);
  const [running, setRunning] = useState(false);
  const [result, setResult] = useState(null);
  const [selected, setSelected] = useState("");
  const [error, setError] = useState("");

  useEffect(() => {
    if (meta && !schema) pick(Object.keys(meta.schemas)[0]);
  }, [meta]);

  function pick(name) {
    setTemplate(name);
    if (meta?.schemas[name]) setSchema(JSON.stringify(meta.schemas[name], null, 2));
  }

  async function verify() {
    setRunning(true);
    setError("");
    try {
      JSON.parse(schema);
      const data = await api("verify", { schema, threshold, ...(source === "file" ? { path: path.trim() } : { text }) });
      setResult(data);
      setSelected("");
    } catch (failure) {
      setError(failure instanceof SyntaxError ? "The schema is not valid JSON: " + failure.message : failure.message);
    } finally {
      setRunning(false);
    }
  }

  if (meta && !meta.installed)
    return (
      <Install title="Documents" active={active} what="VerifyDoc, the document extractor," extra="documents">
        VerifyDoc reads fields out of a document against a schema and reports, for each one, how confident it is and
        where in the source it read the value. It runs on this computer with no model call.
      </Install>
    );

  const fields = result?.fields || [];
  const ready = source === "file" ? path.trim() : text.trim();
  return (
    <Page
      title="Documents"
      active={active}
      error={metaError}
      lead="VerifyDoc extracts fields against a schema and scores each one: a calibrated confidence, where in the source it was read, and whether to accept it or send it to review. It runs locally through Unwind, with no model call."
    >
      <div className="cols">
        <Block title="Document">
          <div className="segmented" role="group" aria-label="Document source">
            <button type="button" aria-pressed={source === "file"} onClick={() => setSource("file")}>
              Workspace file
            </button>
            <button type="button" aria-pressed={source === "text"} onClick={() => setSource("text")}>
              Pasted text
            </button>
          </div>
          {source === "file" ? (
            <label className="field">
              <span>Path in the workspace</span>
              <input className="mono" value={path} spellCheck={false} placeholder="docs/invoice.txt" onChange={(event) => setPath(event.target.value)} />
              <small>
                Text, Markdown or PDF.
                {openPath && openPath !== path && (
                  <>
                    {" "}
                    <button type="button" className="link" onClick={() => setPath(openPath)}>
                      Use the open file, {openPath}
                    </button>
                  </>
                )}
              </small>
            </label>
          ) : (
            <label className="field">
              <span>Document text</span>
              <textarea className="tall-input" value={text} placeholder="Paste the document text here" onChange={(event) => setText(event.target.value)} />
            </label>
          )}
          <label className="field">
            <span>Accept automatically at {Math.round(threshold * 100)}% confidence and above</span>
            <input type="range" min="0.5" max="0.99" step="0.01" value={threshold} onChange={(event) => setThreshold(Number(event.target.value))} />
          </label>
        </Block>
        <Block
          title="Schema"
          tools={
            <label className="field-inline">
              <span>Start from</span>
              <select value={template} onChange={(event) => pick(event.target.value)}>
                {!template && <option value="">Edited</option>}
                {Object.keys(meta?.schemas || {}).map((name) => (
                  <option key={name}>{name}</option>
                ))}
              </select>
            </label>
          }
        >
          <textarea
            className="mono tall-input"
            spellCheck={false}
            value={schema}
            aria-label="JSON Schema of the fields to extract"
            onChange={(event) => {
              setSchema(event.target.value);
              setTemplate("");
            }}
          />
        </Block>
      </div>
      <div className="page-actions">
        {error && (
          <p className="problem" role="alert">
            {error}
          </p>
        )}
        <span className="hint" role="status">
          {running ? "Verifying. The first run starts the document server, which can take a minute." : busy ? "Wait for the current turn to finish." : ""}
        </span>
        <button type="button" className="primary" disabled={running || busy || !ready} onClick={verify}>
          {running ? "Verifying" : "Verify document"}
        </button>
      </div>
      {result && (
        <div className="cols">
          <Block
            title="Fields"
            tools={
              <span className="num">
                {count(result.n_accepted ?? fields.filter((field) => field.decision === "accept").length)} accepted,{" "}
                {count(result.n_review ?? fields.filter((field) => field.decision !== "accept").length)} to review
              </span>
            }
          >
            <div className="table-wrap">
              <table className="grid fields-table">
                <thead>
                  <tr>
                    <th scope="col">Field</th>
                    <th scope="col">Value</th>
                    <th scope="col">Confidence</th>
                    <th scope="col">Decision</th>
                  </tr>
                </thead>
                <tbody>
                  {fields.map((field) => (
                    <tr key={field.path} className={selected === field.path ? "chosen" : ""} onClick={() => setSelected(field.path)}>
                      <th scope="row" className="mono">
                        <button type="button" className="link" onClick={() => setSelected(field.path)}>
                          {field.path}
                        </button>
                      </th>
                      <td className="wrap">{field.value == null || field.value === "" ? <span className="dim">not found</span> : String(field.value)}</td>
                      <td className="num nowrap">
                        <span className="confidence" aria-hidden="true">
                          <i className={field.decision} style={{ width: Math.round((field.confidence || 0) * 100) + "%" }} />
                        </span>
                        {Math.round((field.confidence || 0) * 100)}%
                      </td>
                      <td>
                        <span className={"verdict " + (field.decision === "accept" ? "allow" : "ask")}>
                          {field.decision === "accept" ? "accept" : "review"}
                        </span>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            {result.summary && <p className="block-note">{result.summary}</p>}
          </Block>
          <Block title="Where each value was read" note="Select a field to find it in the text.">
            {result.text ? (
              <Source text={result.text} fields={fields} active={selected} onSelect={setSelected} />
            ) : (
              <p className="block-note">
                This document has no text preview. Positions are reported by page:{" "}
                {fields
                  .filter((field) => field.grounding)
                  .map((field) => `${field.path} on page ${(field.grounding.page || 0) + 1}`)
                  .join(", ") || "none found"}
                .
              </p>
            )}
          </Block>
        </div>
      )}
    </Page>
  );
});
