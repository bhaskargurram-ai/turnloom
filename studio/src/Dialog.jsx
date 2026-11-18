import React, { useId, useLayoutEffect, useRef } from "react";
import { Cross } from "./icons.jsx";

/**
 * A modal built on the native <dialog>: the browser traps focus inside it, closes it on
 * Escape and marks the rest of the page inert. Focus returns to the control that opened it.
 */
export function Dialog({ title, onClose, locked = false, wide = false, className = "", children, footer }) {
  const node = useRef(null);
  const heading = useId();
  const close = useRef(onClose);
  close.current = onClose;
  const lock = useRef(locked);
  lock.current = locked;

  useLayoutEffect(() => {
    const dialog = node.current;
    const opener = document.activeElement;
    dialog.showModal();
    // The browser focuses the first control; a dialog can name a better starting point.
    dialog.querySelector("[data-autofocus]")?.focus();
    return () => {
      dialog.close();
      if (opener instanceof HTMLElement && opener.isConnected) opener.focus();
    };
  }, []);

  return (
    <dialog
      ref={node}
      className={"dialog " + (wide ? "wide " : "") + className}
      aria-labelledby={heading}
      aria-modal="true"
      onCancel={(event) => {
        event.preventDefault();
        if (!lock.current) close.current();
      }}
      onMouseDown={(event) => {
        if (event.target === node.current && !lock.current) close.current();
      }}
    >
      <div className="dialog-box">
        <header className="dialog-head">
          <h2 id={heading}>{title}</h2>
          <button
            type="button"
            className="quiet square"
            aria-label="Close"
            title="Close (Esc)"
            disabled={locked}
            onClick={() => close.current()}
          >
            <Cross />
          </button>
        </header>
        <div className="dialog-body">{children}</div>
        {footer && <footer className="dialog-foot">{footer}</footer>}
      </div>
    </dialog>
  );
}

export function Confirm({ title, children, action, danger = false, onConfirm, onClose }) {
  return (
    <Dialog
      title={title}
      onClose={onClose}
      className="confirm"
      footer={
        <>
          <button type="button" className={danger ? "danger" : "primary"} onClick={onConfirm} data-autofocus>
            {action}
          </button>
          <button type="button" onClick={onClose}>
            Cancel
          </button>
        </>
      }
    >
      {children}
    </Dialog>
  );
}
