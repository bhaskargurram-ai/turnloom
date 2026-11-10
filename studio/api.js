import { useEffect, useState } from "react";

const token =
  location.hash.slice(1) || sessionStorage.getItem("studio-session");
if (token) sessionStorage.setItem("studio-session", token);
history.replaceState(null, "", location.pathname);

export async function api(action, data = {}) {
  const response = await fetch("/api/" + action, {
    method: "POST",
    headers: {
      Authorization: "Bearer " + token,
      "Content-Type": "application/json",
    },
    body: JSON.stringify(data),
  });
  if (!response.ok) {
    const failure = await response.json().catch(() => ({}));
    throw Error(
      failure.error || "Studio session unavailable. Reopen the launcher.",
    );
  }
  return response.json();
}

/** Load a resource when `key` changes. Returns [value, reload, error]. */
export function useLoad(action, data, key) {
  const [value, setValue] = useState(null),
    [error, setError] = useState(""),
    [tick, setTick] = useState(0);
  useEffect(() => {
    let live = true;
    api(action, data)
      .then((result) => {
        if (live) {
          setValue(result);
          setError("");
        }
      })
      .catch((e) => live && setError(e.message));
    return () => {
      live = false;
    };
  }, [key, tick]);
  return [value, () => setTick((n) => n + 1), error];
}

export const tokens = (n) =>
  n == null
    ? "–"
    : n >= 1e6
      ? (n / 1e6).toFixed(n >= 1e7 ? 1 : 2) + "M"
      : n >= 1e4
        ? (n / 1e3).toFixed(n >= 1e5 ? 0 : 1) + "k"
        : Math.round(n).toLocaleString();

export const usd = (n) =>
  n == null
    ? "–"
    : n === 0
      ? "$0"
      : n < 0.01
        ? "$" + n.toFixed(4)
        : n < 10
          ? "$" + n.toFixed(3)
          : "$" + n.toFixed(2);

export const percent = (part, whole) =>
  whole ? Math.round((part * 100) / whole) : 0;

export function ago(seconds) {
  const span = Date.now() / 1000 - seconds;
  if (span < 90) return "just now";
  if (span < 5400) return Math.round(span / 60) + " min ago";
  if (span < 129600) return Math.round(span / 3600) + " h ago";
  return new Date(seconds * 1000).toLocaleDateString();
}

export const PERSONA_COLORS = {
  mint: "#87dfc8",
  purple: "#afadf2",
  blue: "#7cb7ea",
  green: "#8fd694",
  orange: "#e9ac74",
  red: "#ef8f8f",
  cyan: "#79c8d8",
  yellow: "#e2cf7c",
  pink: "#e9a0c8",
  indigo: "#9da6f5",
  teal: "#7fd1c2",
  gold: "#e3c27a",
};
export function personaColor(persona) {
  const raw = (persona?.color || "").toLowerCase();
  if (/^#[0-9a-f]{6}$/.test(raw)) return raw;
  return (
    PERSONA_COLORS[raw] ||
    Object.values(PERSONA_COLORS)[
      [...(persona?.division || "x")].reduce((n, c) => n + c.charCodeAt(0), 0) %
        12
    ]
  );
}
