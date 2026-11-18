// One shared snapshot of server state, fed by a long-poll loop. Components subscribe to the
// fields they render, so a streaming draft does not re-render the file tree or the editor.

import { useEffect, useState, useSyncExternalStore } from "react";
import { api, token } from "./api.js";

function createStore(initial) {
  let state = initial;
  const listeners = new Set();
  return {
    get: () => state,
    set(patch) {
      state = { ...state, ...patch };
      for (const listener of listeners) listener();
    },
    subscribe(listener) {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
  };
}

const SETTINGS = {
  budget: 0,
  fold: true,
  keep: 3,
  reply: 8192,
  effort: "",
  mode: "solo",
  auto_approve: false,
  auto_edit: false,
  cache: true,
  servers: [],
  gate: "confirm",
  team: [],
};

const METER = {
  prompt_tokens: 0,
  completion_tokens: 0,
  cached_tokens: 0,
  cost: 0,
  saved_tokens: 0,
  steps: 0,
  priced: false,
  window: 0,
  prompt: 0,
  context: {},
  spark: [],
};

// Everything that belongs to one workspace and one server session. Replaced when the workspace changes.
const SESSION = {
  loaded: false,
  workspace: "",
  model: "",
  connection: null,
  version: "",
  busy: false,
  stopping: false,
  pending: null,
  messages: [],
  session: "",
  trace: "",
  draft: "",
  running: "",
  runningSince: 0,
  busySince: 0,
  loom: [],
  edits: [],
  persona: "builder",
  settings: SETTINGS,
  stages: [],
  companions: false,
  gate: { installed: false, policy: false, mode: "confirm", active: false },
  limits: null,
  meter: METER,
  // What the operator allowed for this session only: all edits, and a count of exact commands or tool calls.
  always: { edits: false, requests: 0 },
  // MCP servers the agent can use on the next turn: switched on, with an unchanged definition.
  servers_on: [],
  desktop: false,
  // The persona chosen for the next turn, when it differs from the one the server last ran.
  picked: null,
};

export const studio = createStore({
  // link: "starting" | "live" | "retrying" | "gone" | "expired" | "closed" | "none"
  link: token ? "starting" : "none",
  ...SESSION,
  // Counts events after which files on disk may differ: an edit, a command, an undo, a finished turn.
  disk: 0,
  // Counts workspace switches, so views know to drop what they loaded for the previous folder.
  epoch: 0,
});

export function useStudio(selector) {
  return useSyncExternalStore(studio.subscribe, () => selector(studio.get()));
}

// Reuse the previous object when nothing changed, so memoised components skip their render.
function same(previous, next) {
  return JSON.stringify(previous) === JSON.stringify(next) ? previous : next;
}

const EMPTY = { snapshot: 0, revision: null, session: null };
let held = EMPTY;
let controller = null;
let immediate = false;
let failures = 0;
let running = false;
let suspended = false;
let wake = null;

function apply(snap, asked) {
  if (snap.snapshot <= held.snapshot) return;
  // A reply to an explicit refresh is always applied: a few server facts, such as whether a policy
  // file exists, can change without the revision counter moving.
  const unchanged = !asked && snap.revision === held.revision && snap.session === held.session;
  held = { snapshot: snap.snapshot, revision: snap.revision, session: snap.session };
  const prior = studio.get();
  if (unchanged && prior.loaded) {
    if (prior.link !== "live") studio.set({ link: "live" });
    return;
  }
  const messages =
    snap.offset === 0 && snap.session !== prior.session
      ? snap.messages
      : snap.messages.length || snap.offset !== prior.messages.length
        ? [...prior.messages.slice(0, snap.offset), ...snap.messages]
        : prior.messages;
  const touched = snap.messages.some(
    (item) =>
      item.role === "note" ||
      (item.role === "tool" && (item.diff || item.name === "shell" || item.name === "mcp_call")),
  );
  const finished = prior.busy && !snap.busy;
  const now = Date.now();
  studio.set({
    link: "live",
    loaded: true,
    workspace: snap.workspace,
    model: snap.model,
    connection: same(prior.connection, snap.connection),
    version: snap.version,
    busy: snap.busy,
    stopping: snap.stopping,
    pending: same(prior.pending, snap.pending),
    messages,
    session: snap.session,
    trace: snap.trace,
    draft: snap.draft,
    running: snap.running,
    runningSince: snap.running ? (snap.running === prior.running ? prior.runningSince : now) : 0,
    busySince: snap.busy ? prior.busySince || now : 0,
    loom: same(prior.loom, snap.loom),
    edits: same(prior.edits, snap.edits),
    persona: snap.persona,
    settings: same(prior.settings, snap.settings),
    stages: same(prior.stages, snap.stages),
    companions: snap.companions,
    gate: same(prior.gate, snap.gate),
    limits: same(prior.limits, snap.limits),
    meter: same(prior.meter, snap.meter),
    always: same(prior.always, snap.always || { edits: false, requests: 0 }),
    servers_on: same(prior.servers_on, snap.servers_on || []),
    desktop: !!snap.desktop,
    disk: prior.disk + (prior.loaded && (touched || finished || snap.session !== prior.session) ? 1 : 0),
  });
}

function pause(milliseconds) {
  return new Promise((resolve) => {
    const timer = setTimeout(done, milliseconds);
    function done() {
      clearTimeout(timer);
      wake = null;
      resolve();
    }
    wake = done;
  });
}

async function loop() {
  while (running) {
    if (suspended) {
      await pause(200);
      continue;
    }
    controller = new AbortController();
    const wait = !immediate;
    immediate = false;
    // The server answers a waiting request within 25 seconds. Silence for much longer means the
    // connection is dead even though nothing reported an error.
    let silent = false;
    const current = controller;
    const timer = setTimeout(() => {
      silent = true;
      current.abort();
    }, 45000);
    try {
      const state = studio.get();
      const snap = await api(
        "state",
        { since: state.messages.length, session: held.session, revision: held.revision, wait },
        controller.signal,
      );
      failures = 0;
      if (!suspended) apply(snap, !wait);
    } catch (error) {
      if (!running) return;
      if (error.name === "AbortError" && !silent) continue;
      if (error.status === 403) {
        running = false;
        studio.set({ link: "expired" });
        return;
      }
      failures += 1;
      // About three misses in a row means the server is gone, not busy.
      studio.set({ link: failures >= 3 ? "gone" : "retrying" });
      await pause(Math.min(15000, 500 * 2 ** Math.min(failures, 5)));
      immediate = true;
    } finally {
      clearTimeout(timer);
    }
  }
}

export function startPolling() {
  if (running || !token) return;
  running = true;
  loop();
}

export function stopPolling(link) {
  running = false;
  controller?.abort();
  wake?.();
  if (link) studio.set({ link });
}

// Ask for the current state now instead of waiting for the long poll to return.
export function refresh() {
  immediate = true;
  controller?.abort();
  wake?.();
}

/** Say that files on disk may have changed, so the tree and the open file are read again. */
export function touchDisk() {
  studio.set({ disk: studio.get().disk + 1 });
}

/** Change a subset of the settings. Resolves once the server has applied it and the store shows it. */
export async function configure(patch) {
  const settings = await api("settings", patch);
  studio.set({ settings: same(studio.get().settings, settings) });
  return settings;
}

/**
 * Ask the server to open another folder. It shows a native folder dialog, so the request can
 * take as long as the operator takes to choose. Returns whether the workspace changed.
 */
export async function switchWorkspace() {
  suspended = true;
  controller?.abort();
  try {
    const result = await api("workspace", {});
    if (result.changed) {
      held = EMPTY;
      const prior = studio.get();
      studio.set({ ...SESSION, epoch: prior.epoch + 1, disk: prior.disk + 1 });
    }
    return result.changed;
  } finally {
    suspended = false;
    immediate = true;
    wake?.();
  }
}

/** Fetch one read-only resource and fetch it again whenever `key` changes. */
export function useLoad(action, data, key, enabled = true) {
  const [state, setState] = useState({ data: null, error: "", loading: enabled });
  const [tick, setTick] = useState(0);
  useEffect(() => {
    if (!enabled) return;
    let live = true;
    setState((old) => ({ ...old, loading: true }));
    api(action, data).then(
      (result) => live && setState({ data: result, error: "", loading: false }),
      (failure) => live && setState((old) => ({ data: old.data, error: failure.message, loading: false })),
    );
    return () => {
      live = false;
    };
  }, [key, tick, enabled]);
  return { ...state, reload: () => setTick((value) => value + 1) };
}

// The persona catalog is large and the same for every view, so it is fetched once per workspace.
const roster = createStore({ epoch: -1, data: null, error: "" });

export function usePersonas() {
  const epoch = useStudio((state) => state.epoch);
  const loaded = useStudio((state) => state.loaded);
  const value = useSyncExternalStore(roster.subscribe, roster.get);
  useEffect(() => {
    if (!loaded || roster.get().epoch === epoch) return;
    roster.set({ epoch, data: null, error: "" });
    api("personas").then(
      (data) => roster.get().epoch === epoch && roster.set({ data }),
      (failure) => roster.get().epoch === epoch && roster.set({ error: failure.message }),
    );
  }, [epoch, loaded]);
  return value.epoch === epoch ? value : { epoch, data: null, error: "" };
}

export const personaTitle = (text) => (text || "").replace(/-/g, " ").replace(/\b\w/g, (letter) => letter.toUpperCase());
