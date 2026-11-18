import React from "react";

export const count = (value) => Number(value || 0).toLocaleString("en-US");

export const plural = (value, noun, many = noun + "s") =>
  `${count(value)} ${value === 1 ? noun : many}`;

export function clock(milliseconds) {
  const seconds = Math.max(0, Math.floor(milliseconds / 1000));
  const minutes = Math.floor(seconds / 60);
  const rest = String(seconds % 60).padStart(2, "0");
  return minutes >= 60
    ? `${Math.floor(minutes / 60)}:${String(minutes % 60).padStart(2, "0")}:${rest}`
    : `${minutes}:${rest}`;
}

export function money(value) {
  if (value == null) return "";
  if (value === 0) return "$0";
  return "$" + (value < 0.1 ? value.toFixed(5) : value.toFixed(3));
}

export function rate(value) {
  if (value == null) return "";
  if (value === 0) return "free";
  return "$" + (value < 1 ? value.toFixed(3).replace(/0+$/, "").replace(/\.$/, "") : value.toFixed(2));
}

export function tokens(value) {
  if (!value) return "";
  if (value >= 1_000_000) return (value / 1_000_000).toFixed(value % 1_000_000 ? 1 : 0) + "M";
  return Math.round(value / 1000) + "K";
}

export function ago(seconds) {
  const elapsed = Date.now() / 1000 - seconds;
  if (elapsed < 60) return "just now";
  if (elapsed < 3600) return plural(Math.floor(elapsed / 60), "minute") + " ago";
  if (elapsed < 86400) return plural(Math.floor(elapsed / 3600), "hour") + " ago";
  const date = new Date(seconds * 1000);
  return date.toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" });
}

// Characters that change what a reader sees without being visible themselves: C0 and C1
// controls, DEL, zero-width marks and the bidirectional overrides used to disguise text.
// Tab and line feed are left alone.
const HIDDEN = [
  [0x00, 0x08],
  [0x0b, 0x1f],
  [0x7f, 0x9f],
  [0x200b, 0x200f],
  [0x2028, 0x202e],
  [0x2060, 0x2069],
  [0xfeff, 0xfeff],
];

const hidden = (code) => HIDDEN.some(([low, high]) => code >= low && code <= high);

function named(code) {
  if (code < 0x20) return String.fromCodePoint(0x2400 + code);
  if (code === 0x7f) return String.fromCodePoint(0x2421);
  return "U+" + code.toString(16).toUpperCase().padStart(4, "0");
}

/** Text as React nodes with every hidden character replaced by a marked, visible name. */
export function visible(text) {
  let index = 0;
  while (index < text.length && !hidden(text.charCodeAt(index))) index += 1;
  if (index === text.length) return text;
  const parts = [];
  let last = 0;
  for (; index < text.length; index++) {
    const code = text.charCodeAt(index);
    if (!hidden(code)) continue;
    if (index > last) parts.push(text.slice(last, index));
    parts.push(
      React.createElement(
        "span",
        { className: "ctl", key: index, title: "Hidden character, shown by name" },
        named(code),
      ),
    );
    last = index + 1;
  }
  if (last < text.length) parts.push(text.slice(last));
  return parts;
}

export function lines(text) {
  return text ? text.split("\n").length : 0;
}

// Tool families share one thread colour in the log and in the Context Loom.
const FAMILIES = {
  read_file: "read",
  list_files: "list",
  search_files: "search",
  edit_file: "edit",
  write_file: "edit",
  shell: "shell",
  recall: "recall",
  mcp_search: "mcp",
  mcp_call: "mcp",
  exchange: "exchange",
};

export const family = (tool) => FAMILIES[tool] || "other";

export const FAMILY_ORDER = ["read", "list", "search", "edit", "shell", "recall", "mcp", "exchange", "other"];

export const FAMILY_LABEL = {
  read: "read",
  list: "list",
  search: "search",
  edit: "edit",
  shell: "shell",
  recall: "recall",
  mcp: "MCP",
  exchange: "archived exchange",
  other: "other",
};

/** A token count short enough for a status line: 9,420 then 12.4K then 1.25M. */
export function compact(value) {
  if (value == null) return "";
  if (value >= 1e6) return (value / 1e6).toFixed(value >= 1e7 ? 1 : 2) + "M";
  if (value >= 1e4) return (value / 1e3).toFixed(value >= 1e5 ? 0 : 1) + "K";
  return count(Math.round(value));
}

export const percent = (part, whole) => (whole ? Math.round((part * 100) / whole) : 0);

export function useMedia(query) {
  const [matches, setMatches] = React.useState(() => window.matchMedia(query).matches);
  React.useEffect(() => {
    const list = window.matchMedia(query);
    const update = () => setMatches(list.matches);
    update();
    list.addEventListener("change", update);
    return () => list.removeEventListener("change", update);
  }, [query]);
  return matches;
}
