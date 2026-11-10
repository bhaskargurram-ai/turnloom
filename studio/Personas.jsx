import React, { useMemo, useState } from "react";
import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";
import {
  ArrowDown,
  ArrowUp,
  Check,
  Plus,
  Search,
  Trash2,
  Users,
  X,
} from "lucide-react";
import { api, personaColor } from "./api.js";

const title = (text) =>
  text.replace(/-/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());

export function Avatar({ persona, size = 34 }) {
  const color = personaColor(persona);
  return (
    <span
      className="avatar"
      style={{
        width: size,
        height: size,
        fontSize: size * 0.5,
        background: color + "22",
        borderColor: color + "66",
      }}
      aria-hidden="true"
    >
      {persona?.emoji || persona?.name?.[0] || "?"}
    </span>
  );
}

function TeamBuilder({ state, catalog, report }) {
  const settings = state.settings,
    team = settings.team;
  const save = (next) => api("settings", { team: next }).catch(report);
  const patch = (index, change) =>
    save(team.map((s, i) => (i === index ? { ...s, ...change } : s)));
  function move(index, by) {
    const next = [...team];
    [next[index], next[index + by]] = [next[index + by], next[index]];
    save(next);
  }
  return (
    <section className="card team">
      <div className="row">
        <h2>
          <Users size={16} /> Team run
        </h2>
        <div className="segmented" role="group" aria-label="Run mode">
          {[
            ["solo", "One agent"],
            ["team", "Team pipeline"],
          ].map(([mode, label]) => (
            <button
              key={mode}
              className={settings.mode === mode ? "on" : ""}
              aria-pressed={settings.mode === mode}
              disabled={state.busy}
              onClick={() => api("settings", { mode }).catch(report)}
            >
              {label}
            </button>
          ))}
        </div>
      </div>
      <p className="sub">
        A team runs these stages in order on one workspace with LangGraph. Each
        stage gets the task plus the earlier reports, and a failed stage stops
        the run. Every stage is a full agent turn, so a three-stage team costs
        roughly three turns.
      </p>
      <ol className="stages">
        {team.map((stage, index) => {
          const persona = catalog.find((p) => p.id === stage.persona);
          return (
            <li key={stage.name}>
              <Avatar persona={persona} size={30} />
              <input
                className="stagename"
                defaultValue={stage.name}
                key={stage.name}
                aria-label="Stage name"
                disabled={state.busy}
                onBlur={(e) =>
                  e.target.value.trim() &&
                  e.target.value !== stage.name &&
                  patch(index, { name: e.target.value })
                }
              />
              <select
                value={stage.persona}
                aria-label={"Persona for " + stage.name}
                disabled={state.busy}
                onChange={(e) => patch(index, { persona: e.target.value })}
              >
                {catalog.map((p) => (
                  <option key={p.id} value={p.id}>
                    {p.name} · {title(p.division)}
                  </option>
                ))}
              </select>
              <input
                className="instruction"
                defaultValue={stage.instruction}
                key={stage.instruction}
                aria-label={"Instruction for " + stage.name}
                disabled={state.busy}
                onBlur={(e) =>
                  e.target.value !== stage.instruction &&
                  patch(index, { instruction: e.target.value })
                }
              />
              <span className="stagetools">
                <button aria-label="Move stage up" disabled={!index || state.busy} onClick={() => move(index, -1)}>
                  <ArrowUp size={14} />
                </button>
                <button aria-label="Move stage down" disabled={index === team.length - 1 || state.busy} onClick={() => move(index, 1)}>
                  <ArrowDown size={14} />
                </button>
                <button aria-label="Remove stage" disabled={team.length < 2 || state.busy} onClick={() => save(team.filter((_, i) => i !== index))}>
                  <Trash2 size={14} />
                </button>
              </span>
            </li>
          );
        })}
      </ol>
      <button
        className="ghost"
        disabled={team.length >= 8 || state.busy}
        onClick={() =>
          save([
            ...team,
            {
              name: "stage-" + (team.length + 1),
              persona: "builder",
              instruction: "Describe what this stage should do.",
            },
          ])
        }
      >
        <Plus size={14} /> Add stage
      </button>
    </section>
  );
}

export function PersonasView({ state, gallery, persona, choose, report }) {
  const [query, setQuery] = useState(""),
    [division, setDivision] = useState("featured"),
    [preview, setPreview] = useState(null);
  const catalog = gallery?.personas || [];
  const divisions = useMemo(() => {
    const counts = {};
    for (const p of catalog) counts[p.division] = (counts[p.division] || 0) + 1;
    return Object.entries(counts).sort((a, b) => b[1] - a[1]);
  }, [catalog]);
  const needle = query.trim().toLowerCase();
  const visible = catalog.filter((p) =>
    needle
      ? (p.name + " " + p.description + " " + p.division + " " + p.vibe)
          .toLowerCase()
          .includes(needle)
      : division === "featured"
        ? gallery.featured.includes(p.id) || p.division === "custom"
        : division === "all" || p.division === division,
  );
  async function show(p) {
    setPreview({ ...p, text: null });
    try {
      const body = await api("persona", { id: p.id });
      setPreview((old) => (old?.id === p.id ? { ...p, text: body.text } : old));
    } catch (e) {
      report(e);
    }
  }
  return (
    <div className="page">
      <header className="pagehead">
        <div>
          <h1>Agents</h1>
          <p>
            {catalog.length} personas. Two are Turnloom's own; the rest come
            from{" "}
            <b>Agency Agents</b>
            {gallery && " at commit " + gallery.commit.slice(0, 7)}. Add your
            own as Markdown in <code>.turnloom/personas/</code>.
          </p>
        </div>
      </header>
      <TeamBuilder state={state} catalog={catalog} report={report} />
      <div className="filterbar">
        <label className="search">
          <Search size={15} />
          <input
            placeholder="Search personas by name, skill or division"
            aria-label="Search personas"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
        </label>
        <div className="chips" role="group" aria-label="Division">
          {[["featured", "Featured"], ["all", "All"], ...divisions.map(([d, n]) => [d, `${title(d)} ${n}`])].map(([key, label]) => (
            <button
              key={key}
              className={!needle && division === key ? "on" : ""}
              onClick={() => {
                setDivision(key);
                setQuery("");
              }}
            >
              {label}
            </button>
          ))}
        </div>
      </div>
      <div className="gallery">
        {visible.map((p) => (
          <button
            key={p.id}
            className={"persona " + (p.id === persona ? "active" : "")}
            style={{ "--tone": personaColor(p) }}
            onClick={() => show(p)}
          >
            <span className="personahead">
              <Avatar persona={p} />
              <span>
                <b>{p.name}</b>
                <small>{title(p.division)}</small>
              </span>
              {p.id === persona && (
                <i className="using">
                  <Check size={12} /> In use
                </i>
              )}
            </span>
            <span className="desc">{p.description}</span>
          </button>
        ))}
        {!visible.length && <p className="hint">No persona matches "{query}".</p>}
      </div>
      {preview && (
        <div className="scrim" onClick={(e) => e.target === e.currentTarget && setPreview(null)}>
          <section className="dialog drawer" role="dialog" aria-label={preview.name + " persona"}>
            <div className="row">
              <span className="personahead">
                <Avatar persona={preview} size={42} />
                <span>
                  <h2>{preview.name}</h2>
                  <small>{title(preview.division)}{preview.vibe ? " · " + preview.vibe : ""}</small>
                </span>
              </span>
              <button aria-label="Close persona" onClick={() => setPreview(null)}>
                <X />
              </button>
            </div>
            <div className="prose">
              {preview.text == null ? <p className="hint">Loading instructions…</p> : (
                <Markdown skipHtml remarkPlugins={[remarkGfm]} components={{ img: () => null, a: ({ children }) => <span>{children}</span> }}>
                  {preview.text}
                </Markdown>
              )}
            </div>
            <div className="row end">
              <small>Only the selected persona's instructions enter the prompt.</small>
              <button
                className="primary"
                disabled={state.busy}
                onClick={() => {
                  choose(preview.id);
                  setPreview(null);
                }}
              >
                {preview.id === persona ? "In use" : "Use " + preview.name}
              </button>
            </div>
          </section>
        </div>
      )}
    </div>
  );
}
