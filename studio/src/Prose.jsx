import React, { memo, useMemo } from "react";
import Markdown from "react-markdown";

// Only web links become links. Anything else (javascript:, file:, data:) stays as its text.
function Link({ href, children }) {
  if (typeof href === "string" && /^https?:\/\//i.test(href)) {
    return (
      <a href={href} target="_blank" rel="noopener noreferrer">
        {children}
      </a>
    );
  }
  return <span>{children}</span>;
}

// The page loads nothing from the network, so an image is shown as its description.
function Picture({ alt }) {
  return alt ? <span className="img-alt">[image: {alt}]</span> : null;
}

const components = { a: Link, img: Picture };
const inline = { ...components, p: ({ children }) => <>{children}</> };

const DIVIDER = /^\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)*\|?\s*$/;
const FENCE = /^\s{0,3}(```|~~~)/;

function cells(line) {
  const trimmed = line.trim().replace(/^\|/, "").replace(/\|$/, "");
  return trimmed.split(/(?<!\\)\|/).map((cell) => cell.trim().replace(/\\\|/g, "|"));
}

/**
 * Split text into runs of ordinary Markdown and pipe tables. The Markdown parser bundled here
 * follows CommonMark, which has no tables; models write them often, so they are read here
 * rather than by shipping a second parser. Lines inside a code fence are never a table.
 */
function blocks(text) {
  const lines = text.split("\n");
  const out = [];
  let plain = [];
  let fenced = false;
  for (let index = 0; index < lines.length; index++) {
    const line = lines[index];
    if (FENCE.test(line)) fenced = !fenced;
    if (!fenced && line.includes("|") && DIVIDER.test(lines[index + 1] || "") && lines[index + 1].includes("-")) {
      const head = cells(line);
      const align = cells(lines[index + 1]).map((mark) =>
        mark.startsWith(":") && mark.endsWith(":") ? "center" : mark.endsWith(":") ? "right" : undefined,
      );
      const rows = [];
      index += 2;
      while (index < lines.length && lines[index].trim() && lines[index].includes("|")) rows.push(cells(lines[index++]));
      index -= 1;
      if (plain.length) out.push({ text: plain.join("\n") });
      plain = [];
      out.push({ head, align, rows });
    } else {
      plain.push(line);
    }
  }
  if (plain.length) out.push({ text: plain.join("\n") });
  return out;
}

function Table({ head, align, rows }) {
  return (
    <div className="table-wrap">
      <table className="grid">
        <thead>
          <tr>
            {head.map((cell, at) => (
              <th scope="col" key={at} style={{ textAlign: align[at] }}>
                <Markdown skipHtml components={inline}>
                  {cell}
                </Markdown>
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, index) => (
            <tr key={index}>
              {head.map((_, at) => (
                <td key={at} style={{ textAlign: align[at] }}>
                  <Markdown skipHtml components={inline}>
                    {row[at] || ""}
                  </Markdown>
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** Model-written text, rendered as Markdown. Operator text and errors never pass through here. */
export const Prose = memo(function Prose({ text }) {
  const parts = useMemo(() => (text.includes("|") ? blocks(text) : [{ text }]), [text]);
  return (
    <div className="prose">
      {parts.map((part, index) =>
        part.head ? (
          <Table key={index} {...part} />
        ) : (
          <Markdown key={index} skipHtml components={components}>
            {part.text}
          </Markdown>
        ),
      )}
    </div>
  );
});
