// Colour theme: follows the system unless the operator picks one, which is remembered.

const KEY = "turnloom-theme";
const ORDER = ["auto", "light", "dark"];

export function storedTheme() {
  try {
    const value = localStorage.getItem(KEY);
    return ORDER.includes(value) ? value : "auto";
  } catch {
    return "auto";
  }
}

export function applyTheme(theme) {
  if (theme === "auto") delete document.documentElement.dataset.theme;
  else document.documentElement.dataset.theme = theme;
  try {
    if (theme === "auto") localStorage.removeItem(KEY);
    else localStorage.setItem(KEY, theme);
  } catch {
    // The choice still applies to this page.
  }
}

export const nextTheme = (theme) => ORDER[(ORDER.indexOf(theme) + 1) % ORDER.length];

applyTheme(storedTheme());
