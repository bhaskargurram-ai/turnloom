import React, { memo, useEffect, useMemo, useState } from "react";
import { api } from "./api.js";
import { configure, personaTitle, studio, usePersonas, useStudio } from "./store.js";
import { Dialog } from "./Dialog.jsx";
import { Block, Page } from "./Page.jsx";
import { Prose } from "./Prose.jsx";
import { count } from "./util.js";

/** A text field that saves when the operator leaves it, so half-typed text never reaches the server. */
function Held({ value, onCommit, multiline, ...rest }) {
  const [text, setText] = useState(value);
  useEffect(() => setText(value), [value]);
  const Tag = multiline ? "textarea" : "input";
  return (
    <Tag
      {...rest}
      value={text}
      onChange={(event) => setText(event.target.value)}
      onBlur={() => (text.trim() && text !== value ? onCommit(text.trim()) : setText(value))}
    />
  );
}

function Team({ catalog, report }) {
  const settings = useStudio((state) => state.settings);
  const busy = useStudio((state) => state.busy);
  const team = settings.team;
  const save = (next) => configure({ team: next }).catch(report);
  const patch = (index, change) => save(team.map((stage, at) => (at === index ? { ...stage, ...change } : stage)));
  function move(index, by) {
    const next = [...team];
    [next[index], next[index + by]] = [next[index + by], next[index]];
    save(next);
  }
  function add() {
    let number = team.length + 1;
    while (team.some((stage) => stage.name === "stage-" + number)) number += 1;
    save([...team, { name: "stage-" + number, persona: "builder", read_only: false, instruction: "Describe what this stage should do." }]);
  }
  const options = useMemo(() => {
    const groups = {};
    for (const row of catalog) (groups[row.division] ||= []).push(row);
    return Object.entries(groups).sort((a, b) => a[0].localeCompare(b[0]));
  }, [catalog]);

  return (
    <Block
      title="How a task is run"
      note={
        busy
          ? "A turn is running. The setup can change when it ends."
          : "One agent answers with the persona chosen in the composer. A team runs its stages in order on the same workspace: each stage gets the task and the earlier reports, and a failed stage stops the run. Every stage is a full turn, so a three-stage team costs about three turns."
      }
    >
      <div className="segmented" role="group" aria-label="Run mode">
        <button type="button" aria-pressed={settings.mode === "solo"} disabled={busy} onClick={() => configure({ mode: "solo" }).catch(report)}>
          One agent
        </button>
        <button type="button" aria-pressed={settings.mode === "team"} disabled={busy} onClick={() => configure({ mode: "team" }).catch(report)}>
          Team of {team.length}
        </button>
      </div>
      <ol className={"team" + (settings.mode === "team" ? "" : " idle")} aria-label="Team stages">
        {team.map((stage, index) => (
          <li key={stage.name}>
            <span className="num team-order">{index + 1}</span>
            <label className="field team-name">
              <span>Stage</span>
              <Held value={stage.name} maxLength={24} disabled={busy} spellCheck={false} onCommit={(name) => patch(index, { name })} />
            </label>
            <label className="field team-persona">
              <span>Persona</span>
              <select value={stage.persona} disabled={busy} onChange={(event) => patch(index, { persona: event.target.value })}>
                {options.map(([division, rows]) => (
                  <optgroup key={division} label={personaTitle(division)}>
                    {rows.map((row) => (
                      <option key={row.id} value={row.id}>
                        {row.name}
                      </option>
                    ))}
                  </optgroup>
                ))}
              </select>
            </label>
            <label className="field team-instruction">
              <span>Instruction for {stage.name}</span>
              <Held multiline rows={2} value={stage.instruction} maxLength={2000} disabled={busy} onCommit={(instruction) => patch(index, { instruction })} />
            </label>
            <span className="team-tools">
              <label className="check" title="This stage can read and search only: no edits, commands or MCP tools">
                <input type="checkbox" checked={!!stage.read_only} disabled={busy} onChange={(event) => patch(index, { read_only: event.target.checked })} />
                Read-only
                <span className="sr-only"> stage {stage.name}</span>
              </label>
              <button type="button" className="small quiet" disabled={!index || busy} aria-label={"Move stage " + stage.name + " up"} onClick={() => move(index, -1)}>
                Up
              </button>
              <button
                type="button"
                className="small quiet"
                disabled={index === team.length - 1 || busy}
                aria-label={"Move stage " + stage.name + " down"}
                onClick={() => move(index, 1)}
              >
                Down
              </button>
              <button
                type="button"
                className="small quiet"
                disabled={team.length < 2 || busy}
                aria-label={"Remove stage " + stage.name}
                onClick={() => save(team.filter((_, at) => at !== index))}
              >
                Remove
              </button>
            </span>
          </li>
        ))}
      </ol>
      <p>
        <button type="button" className="small" disabled={team.length >= 8 || busy} onClick={add}>
          Add a stage
        </button>
      </p>
    </Block>
  );
}

// Where the catalog was taken from: a link when it is a web address, plain text otherwise.
function Source({ url }) {
  if (!/^https?:[/][/]/i.test(url || "")) return <>{url || "Agency Agents"}</>;
  return (
    <a href={url} target="_blank" rel="noopener noreferrer">
      {url.replace(/^https?:[/][/](www[.])?(github[.]com[/])?/i, "")}
    </a>
  );
}

function Preview({ persona, inUse, onClose, onUse }) {
  const [text, setText] = useState(null);
  const [error, setError] = useState("");
  useEffect(() => {
    let live = true;
    api("persona", { id: persona.id }).then(
      (body) => live && setText(body.text),
      (failure) => live && setError(failure.message),
    );
    return () => {
      live = false;
    };
  }, [persona.id]);
  return (
    <Dialog
      title={persona.name}
      wide
      onClose={onClose}
      footer={
        <>
          <span className="reader-meta">Only the chosen persona's instructions are added to the prompt.</span>
          <button type="button" className="primary" disabled={inUse} onClick={onUse} data-autofocus>
            {inUse ? "In use" : "Use " + persona.name}
          </button>
        </>
      }
    >
      <p className="note-line">
        <span className="tag">{personaTitle(persona.division)}</span> {persona.vibe || persona.description}
      </p>
      {error ? (
        <p className="problem" role="alert">
          {error}
        </p>
      ) : text === null ? (
        <p className="note-line">Reading the instructions</p>
      ) : (
        <Prose text={text} />
      )}
    </Dialog>
  );
}

export const Agents = memo(function Agents({ active, report }) {
  const { data: gallery, error } = usePersonas();
  const persona = useStudio((state) => state.picked || state.persona);
  const mode = useStudio((state) => state.settings.mode);
  const [query, setQuery] = useState("");
  const [division, setDivision] = useState("featured");
  const [preview, setPreview] = useState(null);
  const catalog = gallery?.personas || [];

  const divisions = useMemo(() => {
    const counts = {};
    for (const row of catalog) counts[row.division] = (counts[row.division] || 0) + 1;
    return Object.entries(counts).sort((a, b) => b[1] - a[1]);
  }, [catalog]);
  const needle = query.trim().toLowerCase();
  const shown = useMemo(() => {
    const featured = new Set(gallery?.featured || []);
    return catalog.filter((row) =>
      needle
        ? `${row.name} ${row.description} ${row.division} ${row.vibe || ""}`.toLowerCase().includes(needle)
        : division === "featured"
          ? featured.has(row.id) || row.division === "custom"
          : division === "all" || row.division === division,
    );
  }, [catalog, gallery, needle, division]);

  return (
    <Page
      title="Agents"
      active={active}
      error={error}
      lead={
        gallery ? (
          <>
            {count(catalog.length)} personas. A few are Turnloom's own; the rest come from <Source url={gallery.source} />
            {gallery.commit ? ", commit " + gallery.commit.slice(0, 7) : ""}. Add your own as Markdown files in{" "}
            <code>.turnloom/personas/</code>.
          </>
        ) : (
          "Reading the persona catalog"
        )
      }
    >
      <Team catalog={catalog} report={report} />
      <Block
        title="Personas"
        note={mode === "team" ? "In team mode each stage names its own persona above. The choice here applies when one agent runs." : undefined}
        tools={
          <span className="num dim" role="status">
            {count(shown.length)} shown
          </span>
        }
      >
        <div className="filter-row">
          <label className="field-inline grow">
            <span className="sr-only">Search personas</span>
            <input type="search" value={query} placeholder="Search by name, skill or division" onChange={(event) => setQuery(event.target.value)} />
          </label>
          <label className="field-inline">
            <span>Division</span>
            <select
              value={needle ? "" : division}
              onChange={(event) => {
                setDivision(event.target.value);
                setQuery("");
              }}
            >
              {needle && <option value="">Search results</option>}
              <option value="featured">Featured</option>
              <option value="all">All ({catalog.length})</option>
              {divisions.map(([name, total]) => (
                <option key={name} value={name}>
                  {personaTitle(name)} ({total})
                </option>
              ))}
            </select>
          </label>
        </div>
        {shown.length ? (
          <ul className="gallery">
            {shown.map((row) => (
              <li key={row.id} className={row.id === persona ? "current" : ""}>
                <button type="button" className="gallery-item" onClick={() => setPreview(row)}>
                  <span className="gallery-name">
                    {row.name}
                    {row.id === persona && <span className="tag">in use</span>}
                  </span>
                  <span className="gallery-division">{personaTitle(row.division)}</span>
                  <span className="gallery-text">{row.description}</span>
                </button>
              </li>
            ))}
          </ul>
        ) : (
          gallery && <p className="block-note">No persona matches "{query}".</p>
        )}
      </Block>
      {preview && (
        <Preview
          persona={preview}
          inUse={preview.id === persona}
          onClose={() => setPreview(null)}
          onUse={() => {
            studio.set({ picked: preview.id });
            setPreview(null);
          }}
        />
      )}
    </Page>
  );
});
