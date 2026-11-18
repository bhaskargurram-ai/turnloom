// Unified diffs: parsing what the server sends, and making one in the browser when two
// texts have to be compared (an open buffer with disk, the working tree with the last commit).

/** Rows of {kind, a, b, text}. kind: file | hunk | add | del | ctx | meta. */
export function parseDiff(text) {
  const rows = [];
  let a = 0;
  let b = 0;
  let inHunk = false;
  const source = text.split("\n");
  if (source.at(-1) === "") source.pop();
  for (const line of source) {
    const hunk = /^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@/.exec(line);
    if (hunk) {
      a = Number(hunk[1]);
      b = Number(hunk[2]);
      inHunk = true;
      rows.push({ kind: "hunk", text: line });
    } else if (!inHunk) {
      rows.push({ kind: line.startsWith("---") || line.startsWith("+++") ? "file" : "meta", text: line });
    } else if (line.startsWith("...[") && line.endsWith("omitted]")) {
      rows.push({ kind: "meta", text: line });
    } else if (line[0] === "+") {
      rows.push({ kind: "add", b: b++, text: line.slice(1) });
    } else if (line[0] === "-") {
      rows.push({ kind: "del", a: a++, text: line.slice(1) });
    } else {
      rows.push({ kind: "ctx", a: a++, b: b++, text: line.slice(1) });
    }
  }
  return rows;
}

export function diffStat(text) {
  let added = 0;
  let removed = 0;
  for (const row of parseDiff(text)) {
    if (row.kind === "add") added += 1;
    else if (row.kind === "del") removed += 1;
  }
  return { added, removed };
}

/**
 * Pair the rows of a parsed diff for a two-column view: a removed line sits beside the line
 * that replaced it, and unmatched lines face an empty cell.
 */
export function splitRows(rows) {
  const out = [];
  let index = 0;
  while (index < rows.length) {
    const row = rows[index];
    if (row.kind === "ctx") {
      out.push({ left: row, right: row });
      index += 1;
    } else if (row.kind === "del" || row.kind === "add") {
      const removed = [];
      const added = [];
      while (index < rows.length && rows[index].kind === "del") removed.push(rows[index++]);
      while (index < rows.length && rows[index].kind === "add") added.push(rows[index++]);
      for (let pair = 0; pair < Math.max(removed.length, added.length); pair++)
        out.push({ left: removed[pair] || null, right: added[pair] || null });
    } else {
      out.push({ band: row });
      index += 1;
    }
  }
  return out;
}

// Myers' shortest edit script over lines. Returns null when the texts differ by more than
// `limit` edits, where a line diff stops being readable anyway. Round d keeps 2d+3 end points,
// stored at index k+d+1, so memory grows with the square of the difference and not with file size.
function editScript(a, b, limit) {
  const n = a.length;
  const m = b.length;
  const max = Math.min(n + m, limit);
  const trace = [];
  let prev = new Int32Array(2);
  for (let d = 0; d <= max; d++) {
    const cur = new Int32Array(2 * d + 3);
    for (let k = -d; k <= d; k += 2) {
      const down = k === -d || (k !== d && prev[k - 1 + d] < prev[k + 1 + d]);
      let x = down ? prev[k + 1 + d] : prev[k - 1 + d] + 1;
      let y = x - k;
      while (x < n && y < m && a[x] === b[y]) {
        x += 1;
        y += 1;
      }
      cur[k + d + 1] = x;
      if (x >= n && y >= m) {
        const steps = [];
        let cx = n;
        let cy = m;
        for (let depth = d; depth > 0; depth--) {
          const before = trace[depth - 1];
          const diagonal = cx - cy;
          const wentDown =
            diagonal === -depth ||
            (diagonal !== depth && before[diagonal - 1 + depth] < before[diagonal + 1 + depth]);
          const previous = wentDown ? diagonal + 1 : diagonal - 1;
          const px = before[previous + depth];
          const py = px - previous;
          while (cx > px && cy > py) steps.push(["ctx", --cx, --cy]);
          if (wentDown) steps.push(["add", cx, --cy]);
          else steps.push(["del", --cx, cy]);
        }
        while (cx > 0 && cy > 0) steps.push(["ctx", --cx, --cy]);
        return steps.reverse();
      }
    }
    trace.push(cur);
    prev = cur;
  }
  return null;
}

const split = (text) => (text === "" ? [] : text.replace(/\r\n?/g, "\n").replace(/\n$/, "").split("\n"));

/** A unified diff of two texts with three lines of context, or null when they are too different. */
export function unifiedDiff(before, after, name, limit = 2500) {
  const a = split(before);
  const b = split(after);
  let head = 0;
  while (head < a.length && head < b.length && a[head] === b[head]) head += 1;
  let tail = 0;
  while (tail < a.length - head && tail < b.length - head && a[a.length - 1 - tail] === b[b.length - 1 - tail]) tail += 1;
  const innerA = a.slice(head, a.length - tail);
  const innerB = b.slice(head, b.length - tail);
  let steps;
  if (!innerA.length || !innerB.length) {
    // One side is a pure insertion or deletion: no search needed, whatever its size.
    steps = [...innerA.map((_, index) => ["del", index, 0]), ...innerB.map((_, index) => ["add", innerA.length, index])];
  } else {
    steps = editScript(innerA, innerB, limit);
    if (!steps) return null;
  }
  const all = [];
  for (let index = 0; index < head; index++) all.push(["ctx", index, index]);
  for (const [kind, x, y] of steps) all.push([kind, x + head, y + head]);
  for (let index = 0; index < tail; index++) all.push(["ctx", a.length - tail + index, b.length - tail + index]);
  const out = ["--- a/" + name, "+++ b/" + name];
  const context = 3;
  let index = 0;
  while (index < all.length) {
    while (index < all.length && all[index][0] === "ctx") index += 1;
    if (index >= all.length) break;
    const start = Math.max(0, index - context);
    let end = index;
    let quiet = 0;
    let cursor = index;
    while (cursor < all.length && quiet <= context * 2) {
      if (all[cursor][0] === "ctx") quiet += 1;
      else {
        quiet = 0;
        end = cursor;
      }
      cursor += 1;
    }
    const stop = Math.min(all.length, end + 1 + context);
    const body = [];
    let oldCount = 0;
    let newCount = 0;
    for (let row = start; row < stop; row++) {
      const [kind, x, y] = all[row];
      if (kind === "ctx") {
        body.push(" " + a[x]);
        oldCount += 1;
        newCount += 1;
      } else if (kind === "del") {
        body.push("-" + a[x]);
        oldCount += 1;
      } else {
        body.push("+" + b[y]);
        newCount += 1;
      }
    }
    const [, firstOld, firstNew] = all[start];
    out.push(`@@ -${oldCount ? firstOld + 1 : firstOld},${oldCount} +${newCount ? firstNew + 1 : firstNew},${newCount} @@`, ...body);
    index = stop;
  }
  return out.length > 2 ? out.join("\n") : "";
}
