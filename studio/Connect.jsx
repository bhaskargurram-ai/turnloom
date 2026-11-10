import React, { useEffect, useMemo, useState } from "react";
import { Search, X } from "lucide-react";
import { api, tokens, usd } from "./api.js";

export function Connect({ close, finish, current }) {
  const [choices, setChoices] = useState([]),
    [provider, setProvider] = useState(current?.provider || "openrouter"),
    [rows, setRows] = useState([]),
    [key, setKey] = useState(""),
    [model, setModel] = useState(""),
    [filter, setFilter] = useState(""),
    [free, setFree] = useState(false),
    [working, setWorking] = useState(false),
    [error, setError] = useState("");
  useEffect(() => {
    api("providers")
      .then(setChoices)
      .catch((e) => setError(e.message));
  }, []);
  async function perform(fn) {
    setWorking(true);
    setError("");
    try {
      await fn();
    } catch (e) {
      setError(e.message);
    } finally {
      setWorking(false);
    }
  }
  const groups = useMemo(() => {
    const map = new Map();
    for (const p of choices)
      map.set(p.group, [...(map.get(p.group) || []), p]);
    return [...map];
  }, [choices]);
  const chosen = choices.find((p) => p.id === provider);
  const visible = rows.filter(
    (r) =>
      (!free || r.tier === "free") &&
      r.model.toLowerCase().includes(filter.toLowerCase()),
  );
  const selected = visible.some((r) => r.model === model)
    ? model
    : visible[0]?.model || "";
  const unset = chosen?.environment?.length > 1 ? chosen.environment : [];
  return (
    <div className="scrim">
      <section
        className="dialog connect"
        role="dialog"
        aria-modal="true"
        aria-label="Model connection"
      >
        <div className="row">
          <h2>Model connection</h2>
          <button
            aria-label="Close connection"
            disabled={working}
            onClick={close}
          >
            <X />
          </button>
        </div>
        <label>
          Provider
          <select
            disabled={working}
            value={provider}
            onChange={(e) => {
              setProvider(e.target.value);
              setRows([]);
              setKey("");
              setError("");
            }}
          >
            {groups.map(([group, list]) => (
              <optgroup key={group} label={`${group} (${list.length})`}>
                {list.map((p) => (
                  <option key={p.id} value={p.id}>
                    {p.name}
                  </option>
                ))}
              </optgroup>
            ))}
          </select>
        </label>
        {chosen?.group?.includes("LiteLLM") && (
          <small className="note">
            This provider's native API is reached through LiteLLM.
            {unset.length
              ? ` It reads ${unset.join(", ")} from your environment. Set them before starting Studio.`
              : ""}
          </small>
        )}
        {chosen?.requiresKey && (
          <label>
            API key
            <input
              type="password"
              autoComplete="off"
              placeholder={
                provider === "openrouter"
                  ? "Use browser sign-in or paste a key"
                  : "Use saved key or paste a new key"
              }
              value={key}
              onChange={(e) => setKey(e.target.value)}
            />
          </label>
        )}
        <button
          disabled={working}
          onClick={() =>
            perform(async () => {
              const list = await api("models", { provider, key });
              setRows(list);
              if (!list.length)
                setError("No models are available for this provider.");
            })
          }
        >
          Browse models
        </button>
        {!!rows.length && (
          <>
            <div className="row left">
              <label className="search grow">
                <Search size={15} />
                <input
                  value={filter}
                  onChange={(e) => setFilter(e.target.value)}
                  placeholder="Search model names"
                  aria-label="Find a model"
                />
              </label>
              <label className="check">
                <input
                  type="checkbox"
                  checked={free}
                  onChange={(e) => setFree(e.target.checked)}
                />
                Free routes only
              </label>
            </div>
            <label className="sr">
              Model
              <select
                value={selected}
                onChange={(e) => setModel(e.target.value)}
              >
                {visible.map((r) => (
                  <option key={r.model}>{r.model}</option>
                ))}
              </select>
            </label>
            <div className="models" role="listbox" aria-label="Models">
              {visible.slice(0, 300).map((r) => (
                <button
                  key={r.model}
                  role="option"
                  aria-selected={r.model === selected}
                  className={r.model === selected ? "on" : ""}
                  onClick={() => setModel(r.model)}
                >
                  <span className="mono">{r.model}</span>
                  <span className="price">
                    {r.tier === "free"
                      ? "Free"
                      : r.input == null
                        ? "Price not listed"
                        : `${usd(r.input)} in · ${usd(r.output)} out / M`}
                    {r.context ? " · " + tokens(r.context) + " ctx" : ""}
                  </span>
                </button>
              ))}
            </div>
            <small className="note">
              {visible.length} of {rows.length} tool-capable models ·{" "}
              {rows[0].source}
            </small>
          </>
        )}
        {error && (
          <p role="alert" className="error">
            {error}
          </p>
        )}
        <button
          className="primary"
          disabled={!selected || working}
          onClick={() =>
            perform(async () => {
              await api("connect", {
                provider,
                key,
                model: selected,
                browser: provider === "openrouter" && !key,
              });
              finish();
            })
          }
        >
          {working
            ? "Connecting…"
            : provider === "openrouter" && !key
              ? "Connect with OpenRouter"
              : "Use connection"}
        </button>
      </section>
    </div>
  );
}
