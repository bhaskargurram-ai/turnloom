import React, { useState } from "react";
import { Mark } from "./icons.jsx";
import { refresh } from "./store.js";

function Screen({ title, children, overlay = false }) {
  return (
    <main className={"screen" + (overlay ? " overlay" : "")} role={overlay ? "alertdialog" : undefined} aria-label={overlay ? title : undefined}>
      <div className="screen-box">
        <p className="brand">
          <Mark />
          <span>Turnloom Studio</span>
        </p>
        <h1>{title}</h1>
        {children}
      </div>
    </main>
  );
}

const Start = () => (
  <p>
    To start it, run <code>turnloom studio</code> in the project folder, or use the launcher, and work in the tab it
    opens.
  </p>
);

/** Opened without the one-time key from the launcher: nothing here can work, so nothing is polled. */
export const NoSession = () => (
  <Screen title="This tab has no Studio session">
    <p>
      Studio only answers a tab that was opened by its launcher, which puts a one-time key in the address. This
      address was opened without one, or the tab was restored after its session ended.
    </p>
    <Start />
  </Screen>
);

export const Expired = () => (
  <Screen title="This tab's session has ended">
    <p>
      A Studio server answered at this address, but it does not accept this tab's key. Studio was restarted since the
      tab was opened, and each start issues a new key.
    </p>
    <Start />
  </Screen>
);

export const Closed = () => (
  <Screen title="Studio is closed">
    <p>The local server has stopped. Nothing is running in the background, and this tab can be closed.</p>
    <Start />
  </Screen>
);

/**
 * Catches an error thrown while drawing a view. The page says so in words, keeps the session
 * running on the server, and offers a reload: a blank page would leave the operator guessing.
 */
export class Guard extends React.Component {
  state = { error: null };

  static getDerivedStateFromError(error) {
    return { error };
  }

  render() {
    if (!this.state.error) return this.props.children;
    return (
      <Screen title="Studio could not draw this page">
        <p>
          Something went wrong in the page itself. The local server and any running turn are not affected, and
          reloading brings the session back as it was.
        </p>
        <p className="banner warn">
          <code>{String(this.state.error?.message || this.state.error)}</code>
        </p>
        <p>
          <button type="button" className="primary" onClick={() => location.reload()}>
            Reload the page
          </button>
        </p>
      </Screen>
    );
  }
}

/** Shown over the workbench after repeated failed requests, so unsaved text is still there to copy. */
export function Gone({ unsaved, text }) {
  const [copied, setCopied] = useState("");
  return (
    <Screen title="Studio is not running" overlay>
      <p>
        This tab can no longer reach the local server. It stops when you choose Close Studio, when its terminal
        window is closed, or by itself after 15 minutes with no Studio tab open and no turn running.
      </p>
      <p>
        Run <code>turnloom studio</code> again and continue in the tab it opens. Past sessions can be resumed from the
        Sessions list. This tab keeps checking and comes back if the same server returns.
      </p>
      {unsaved && (
        <p className="banner warn">
          <span>
            <strong>{unsaved} has unsaved edits.</strong> They exist only in this tab.
          </span>
          <button
            type="button"
            className="small"
            onClick={() =>
              navigator.clipboard.writeText(text()).then(
                () => setCopied("Copied to the clipboard."),
                () => setCopied("The browser refused clipboard access."),
              )
            }
          >
            Copy the unsaved text
          </button>
          <span role="status">{copied}</span>
        </p>
      )}
      <p>
        <button type="button" onClick={refresh} data-autofocus>
          Check again now
        </button>
      </p>
    </Screen>
  );
}
