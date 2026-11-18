import React, { useEffect, useMemo, useRef, useState } from "react";
import { api } from "./api.js";
import { refresh, useStudio } from "./store.js";
import { Dialog } from "./Dialog.jsx";
import { clock, count, rate, tokens } from "./util.js";

function remembered() {
  try {
    return localStorage.getItem("turnloom-provider") || "";
  } catch {
    return "";
  }
}

function Waiting({ since }) {
  const [, tick] = useState(0);
  useEffect(() => {
    const timer = setInterval(() => tick((value) => value + 1), 1000);
    return () => clearInterval(timer);
  }, []);
  return <span className="num">{clock(Date.now() - since)}</span>;
}

function price(row, side) {
  if (row[side] != null) return rate(row[side]);
  return row.tier === "free" ? "free" : "";
}

export function Connection({ onClose }) {
  const model = useStudio((state) => state.model);
  const busy = useStudio((state) => state.busy);
  const sessionKey = useStudio((state) => !!state.connection?.sessionKey);
  const [providers, setProviders] = useState(null);
  const [provider, setProvider] = useState("");
  const [key, setKey] = useState("");
  const [rows, setRows] = useState(null);
  const [loading, setLoading] = useState(false);
  const [filter, setFilter] = useState("");
  const [freeOnly, setFreeOnly] = useState(false);
  const [chosen, setChosen] = useState("");
  const [working, setWorking] = useState(null); // {kind: "connect" | "signin", since}
  const [error, setError] = useState("");
  const request = useRef(0);

  const entry = providers?.find((item) => item.id === provider);
  const needsKey = !!entry?.requiresKey;
  const missingKey = needsKey && !entry.saved && !key.trim();
  const signIn = provider === "openrouter" && missingKey;
  const library = !!entry?.group?.includes("LiteLLM");
  const unset = library && !needsKey ? entry.environment || [] : [];

  async function load(id, secret) {
    const mine = ++request.current;
    setLoading(true);
    setError("");
    try {
      const list = await api("models", secret ? { provider: id, key: secret } : { provider: id });
      if (mine !== request.current) return;
      setRows(list);
      setChosen((old) => (list.some((row) => row.model === old) ? old : list.length === 1 ? list[0].model : ""));
    } catch (failure) {
      if (mine !== request.current) return;
      setRows(null);
      setError(failure.message);
    } finally {
      if (mine === request.current) setLoading(false);
    }
  }

  function choose(id, list = providers) {
    const next = list.find((item) => item.id === id);
    request.current += 1;
    setProvider(id);
    setKey("");
    setRows(null);
    setChosen("");
    setFilter("");
    setFreeOnly(false);
    setError("");
    setLoading(false);
    // The list can be read straight away when no key is needed, one is saved, or the catalog is public.
    if (next && (!next.requiresKey || next.saved || id === "openrouter" || next.group?.includes("LiteLLM"))) load(id, "");
  }

  useEffect(() => {
    api("providers").then(
      (list) => {
        setProviders(list);
        const last = remembered();
        choose(list.some((item) => item.id === last) ? last : list[0]?.id || "", list);
      },
      (failure) => setError(failure.message),
    );
  }, []);

  const mixed = useMemo(
    () => !!rows && rows.some((row) => row.tier === "free") && rows.some((row) => row.tier !== "free"),
    [rows],
  );
  const shown = useMemo(() => {
    if (!rows) return [];
    const needle = filter.trim().toLowerCase();
    return rows.filter(
      (row) => (!freeOnly || !mixed || row.tier === "free") && (!needle || row.model.toLowerCase().includes(needle)),
    );
  }, [rows, filter, freeOnly, mixed]);
  const selected = rows?.find((row) => row.model === chosen);
  const groups = useMemo(() => {
    const found = new Map();
    for (const item of providers || []) found.set(item.group || "Providers", [...(found.get(item.group || "Providers") || []), item]);
    return [...found];
  }, [providers]);

  async function connect() {
    setError("");
    setWorking({ kind: signIn ? "signin" : "connect", since: Date.now() });
    try {
      await api("connect", {
        provider,
        model: chosen,
        ...(key.trim() ? { key: key.trim() } : {}),
        ...(signIn ? { browser: true } : {}),
      });
      try {
        localStorage.setItem("turnloom-provider", provider);
      } catch {
        // Remembering the provider is optional.
      }
      refresh();
      onClose();
    } catch (failure) {
      setError(failure.message);
      setWorking(null);
    }
  }

  let reason = "";
  if (busy) reason = "A turn is running. Stop it or let it finish before changing the model.";
  else if (loading) reason = "Reading the model list.";
  else if (!rows && needsKey && missingKey && !signIn) reason = "Enter the API key, then load the models.";
  else if (!rows) reason = "Load the model list first.";
  else if (!selected) reason = "Choose a model from the list.";
  else if (missingKey && !signIn) reason = "Enter the API key for this provider.";
  const ready = !reason && !working;

  return (
    <Dialog
      title="Model connection"
      wide
      className="connection"
      locked={!!working}
      onClose={onClose}
      footer={
        <>
          <span className="reader-meta" role="status">
            {working?.kind === "signin" ? (
              <>
                A browser tab opened at openrouter.ai. Approve access there, then return here. Waiting{" "}
                <Waiting since={working.since} /> of up to 3:00.
              </>
            ) : working ? (
              "Connecting"
            ) : (
              reason || `Ready to use ${selected.model}.`
            )}
          </span>
          <button type="button" className="primary" disabled={!ready} onClick={connect}>
            {signIn ? "Sign in with OpenRouter and connect" : "Connect"}
          </button>
          <button type="button" disabled={!!working} onClick={onClose}>
            Cancel
          </button>
        </>
      }
    >
      <p className="note-line">
        {model ? (
          <>
            Connected to <code>{model}</code>.
          </>
        ) : (
          "No model is connected yet."
        )}{" "}
        Tasks, the files the agent reads and command output are sent to the provider you choose.
        {sessionKey && " The current key is held in memory only and is gone when Studio closes."}
      </p>

      <div className="form-row">
        <label className="field">
          <span>Provider</span>
          <select
            value={provider}
            disabled={!providers || !!working}
            data-autofocus
            onChange={(event) => choose(event.target.value)}
          >
            {groups.map(([group, list]) => (
              <optgroup key={group} label={`${group} (${list.length})`}>
                {list.map((item) => (
                  <option key={item.id} value={item.id}>
                    {item.name}
                    {item.requiresKey ? (item.saved ? " (key saved)" : "") : item.group?.includes("LiteLLM") ? "" : " (no key needed)"}
                  </option>
                ))}
              </optgroup>
            ))}
          </select>
        </label>
        {needsKey && (
          <label className="field grow">
            <span>API key</span>
            <input
              type="password"
              autoComplete="off"
              spellCheck={false}
              value={key}
              disabled={!!working}
              placeholder={
                entry.saved
                  ? "A key is saved. Leave empty to keep using it"
                  : signIn
                    ? "Paste a key, or sign in below"
                    : "Paste the key"
              }
              onChange={(event) => setKey(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === "Enter" && key.trim()) {
                  event.preventDefault();
                  load(provider, key.trim());
                }
              }}
            />
          </label>
        )}
      </div>
      {entry && (
        <p className="endpoint">
          {library ? (
            <>
              Requests go to {entry.name}'s own API through the LiteLLM library on this computer
              {needsKey ? ", and the key is sent only there." : "."}
            </>
          ) : (
            <>
              {needsKey ? "The key is sent only to " : "No key is needed. Requests go to "}
              <code>{entry.endpoint}</code>.
            </>
          )}
          {unset.length > 0 && (
            <>
              {" "}
              This provider has no single key to paste. It reads{" "}
              {unset.map((name, index) => (
                <React.Fragment key={name}>
                  {index ? ", " : ""}
                  <code>{name}</code>
                </React.Fragment>
              ))}{" "}
              from the environment, and {unset.length === 1 ? "it is" : "they are"} not set. Set{" "}
              {unset.length === 1 ? "it" : "them"} before starting Studio.
            </>
          )}
          {needsKey && entry.saved && " A key for this provider is already stored on this computer."}
          {needsKey && !entry.saved && entry.variable && (
            <>
              {" "}
              A key in the <code>{entry.variable}</code> environment variable is used too.
            </>
          )}
          {signIn &&
            " Without a key, the Connect button opens OpenRouter in a new browser tab to create one for Turnloom."}
        </p>
      )}

      <div className="model-head">
        <h3 className="label">Models</h3>
        {rows && (
          <>
            <label className="field-inline grow">
              <span className="sr-only">Filter models</span>
              <input
                type="search"
                value={filter}
                placeholder="Filter by name"
                onChange={(event) => setFilter(event.target.value)}
              />
            </label>
            {mixed && (
              <label className="check">
                <input type="checkbox" checked={freeOnly} onChange={(event) => setFreeOnly(event.target.checked)} />
                Free only
              </label>
            )}
            <span className="num dim">
              {shown.length === rows.length ? count(rows.length) : `${count(shown.length)} of ${count(rows.length)}`}
            </span>
          </>
        )}
        <button
          type="button"
          className="small"
          disabled={!provider || loading || !!working || (needsKey && missingKey && !signIn)}
          onClick={() => load(provider, key.trim())}
        >
          {rows ? "Refresh list" : "Load models"}
        </button>
      </div>

      {error && (
        <p className="problem" role="alert">
          {error}
        </p>
      )}

      <div className="model-list" aria-busy={loading}>
        {loading ? (
          <p className="note-line pad">Reading the model list from {entry?.name}</p>
        ) : !rows ? (
          <p className="note-line pad">
            {!providers
              ? "Reading providers"
              : needsKey && missingKey && !signIn
                ? `${entry.name} lists its models only with a key. Paste the key above and choose Load models.`
                : "No model list loaded."}
          </p>
        ) : !rows.length ? (
          <p className="note-line pad">
            {needsKey
              ? `${entry.name} lists no models that support tool calls for this key.`
              : `${entry.name} reports no models. Load a tool-capable model in the local server, then refresh.`}
          </p>
        ) : !shown.length ? (
          <p className="note-line pad">No model matches the filter.</p>
        ) : (
          <table className="grid models">
            <thead>
              <tr>
                <th scope="col">Model</th>
                <th scope="col" className="num" title="Context window in tokens">
                  Context
                </th>
                <th scope="col" className="num" title="US dollars per million input tokens">
                  Input / 1M
                </th>
                <th scope="col" className="num" title="US dollars per million output tokens">
                  Output / 1M
                </th>
                <th scope="col" title="Whether the provider reports tool calling for this model">
                  Tools
                </th>
              </tr>
            </thead>
            <tbody>
              {shown.map((row) => (
                <tr key={row.model} className={row.model === chosen ? "chosen" : ""} onClick={() => setChosen(row.model)}>
                  <td>
                    <label className="model-pick">
                      <input
                        type="radio"
                        name="model"
                        checked={row.model === chosen}
                        disabled={!!working}
                        onChange={() => setChosen(row.model)}
                      />
                      <span>{row.model}</span>
                    </label>
                  </td>
                  <td className="num">{tokens(row.context) || <span className="dim">not listed</span>}</td>
                  <td className="num">{price(row, "input") || <span className="dim">not listed</span>}</td>
                  <td className="num">{price(row, "output") || <span className="dim">not listed</span>}</td>
                  <td>{row.tools ? "yes" : <span className="dim">not reported</span>}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </Dialog>
  );
}
