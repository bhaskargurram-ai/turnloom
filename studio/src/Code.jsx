import React, { forwardRef, memo, useCallback, useEffect, useImperativeHandle, useLayoutEffect, useRef, useState } from "react";
import { api } from "./api.js";
import { useStudio } from "./store.js";
import { Files } from "./Files.jsx";
import { Editor } from "./Editor.jsx";
import { Loom } from "./Loom.jsx";

const PARTS = [
  ["files", "Files"],
  ["editor", "Editor"],
  ["loom", "Context Loom"],
];

/**
 * The Code view: file tree, editor and Context Loom. With room they sit side by side; in a
 * narrow pane a small switch shows one at a time. The editor stays mounted either way, so an
 * unsaved buffer survives a visit to another view.
 */
export const Code = memo(
  forwardRef(function Code({ hidden, report, onPath, onDirty }, handle) {
    const epoch = useStudio((state) => state.epoch);
    const loaded = useStudio((state) => state.loaded);
    const node = useRef(null);
    const editor = useRef(null);
    const [wide, setWide] = useState(true);
    const [part, setPart] = useState("editor");
    const [path, setPath] = useState(null);

    useLayoutEffect(() => {
      const measure = () => {
        const width = node.current.getBoundingClientRect().width;
        if (width) setWide(width >= 640);
      };
      measure();
      const observer = new ResizeObserver(measure);
      observer.observe(node.current);
      return () => observer.disconnect();
    }, []);

    const track = useCallback(
      (next) => {
        setPath(next);
        onPath(next);
      },
      [onPath],
    );
    const open = useCallback((name) => {
      editor.current.open(name);
      setPart("editor");
    }, []);
    const renamed = useCallback((from, to) => editor.current.renamed(from, to), []);
    const deleted = useCallback((name) => editor.current.deleted(name), []);

    useImperativeHandle(handle, () => ({
      open,
      text: () => editor.current?.text() || "",
    }));

    // A new workspace starts with nothing open; then the README is opened when there is one.
    useEffect(() => {
      if (!loaded) return;
      let live = true;
      track(null);
      onDirty(false);
      api("tree", { path: "." })
        .then((list) => {
          const first = list.find((item) => !item.dir && /^readme(\.|$)/i.test(item.name));
          if (live && first) editor.current?.open(first.path);
        })
        .catch(() => {});
      return () => {
        live = false;
      };
    }, [epoch, loaded]);

    return (
      <div className={"code" + (wide ? " wide" : "")} data-part={part} hidden={hidden} ref={node}>
        <nav className="part-tabs" aria-label="Parts of the Code view">
          {PARTS.map(([name, label]) => (
            <button type="button" key={name} aria-current={part === name ? "true" : undefined} onClick={() => setPart(name)}>
              {label}
            </button>
          ))}
        </nav>
        <Files key={"files" + epoch} active={path} onOpen={open} onRenamed={renamed} onDeleted={deleted} report={report} />
        <Editor key={"editor" + epoch} ref={editor} report={report} onPath={track} onDirty={onDirty} />
        <Loom docked={wide} />
      </div>
    );
  }),
);
