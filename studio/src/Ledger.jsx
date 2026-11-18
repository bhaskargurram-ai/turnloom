import React, { memo, useState } from "react";
import { api } from "./api.js";
import { useLoad, useStudio } from "./store.js";
import { Block, Figures, Install, Page } from "./Page.jsx";
import { RISK } from "./Agent.jsx";
import { ago, count, plural } from "./util.js";

const seconds = (value) => (typeof value === "number" ? (value > 1e12 ? value / 1000 : value) : Date.parse(value) / 1000);

export const Ledger = memo(function Ledger({ active, report }) {
  const busy = useStudio((state) => state.busy);
  const companions = useStudio((state) => state.companions);
  const { data, error, reload } = useLoad("ledger", {}, String(busy), active && companions);
  const [checkpoints, setCheckpoints] = useState([]);
  const [label, setLabel] = useState("");
  const [amount, setAmount] = useState(1);
  const [working, setWorking] = useState("");
  const [outcome, setOutcome] = useState(null);

  if (!companions)
    return (
      <Install title="Undo ledger" active={active} what="Unwind, the reversibility proxy," extra="documents">
        Unwind sits between the agent and an MCP server. It classifies each tool call by how reversible it is, records
        it, and can run the inverse of the calls a server knows how to take back.
      </Install>
    );

  async function act(name, action, payload) {
    setWorking(name);
    try {
      const result = await api(action, payload);
      if (action === "checkpoint") {
        setCheckpoints((old) => [result, ...old]);
        setLabel("");
      } else {
        setOutcome(result?.undone || []);
      }
      reload();
    } catch (failure) {
      report(failure);
    } finally {
      setWorking("");
    }
  }

  const entries = data?.entries || [];
  const undoable = entries.filter((entry) => entry.status === "active" && entry.class !== "R0").length;
  return (
    <Page
      title="Undo ledger"
      active={active}
      onReload={reload}
      error={error}
      lead={
        <>
          Unwind records every tool call that passes through it with a reversibility class, and can take actions back,
          newest first. The log is <code>.turnloom/unwind.db</code> in the workspace and survives restarts.
        </>
      }
    >
      <Figures
        items={Object.entries(RISK).map(([level, detail]) => ({
          label: level,
          value: count(entries.filter((entry) => entry.class === level).length),
          detail,
        }))}
      />
      <div className="cols">
        <Block title={`Recorded actions (${count(entries.length)})`}>
          {entries.length ? (
            <div className="table-wrap">
              <table className="grid">
                <thead>
                  <tr>
                    <th scope="col">When</th>
                    <th scope="col">Tool</th>
                    <th scope="col">Class</th>
                    <th scope="col">Status</th>
                    <th scope="col">Undone with</th>
                  </tr>
                </thead>
                <tbody>
                  {entries.map((entry) => (
                    <tr key={entry.id} title={entry.arguments}>
                      <td className="nowrap">{entry.time ? ago(seconds(entry.time)) : ""}</td>
                      <td className="mono wrap">
                        {entry.server}/{entry.tool}
                      </td>
                      <td>
                        <span className={"risk " + entry.class}>{entry.class}</span>
                      </td>
                      <td>{String(entry.status).replace(/_/g, " ")}</td>
                      <td className="mono">{entry.inverse || <span className="dim">nothing</span>}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <p className="block-note">
              Nothing recorded yet. Read-only calls such as document verification leave no entry, because there is
              nothing to undo. Calls that change something appear here.
            </p>
          )}
        </Block>
        <Block
          title="Take actions back"
          note={`Undo runs each action's inverse through the same server. ${plural(undoable, "recorded action")} can still be undone.`}
        >
          <form
            className="inline-form"
            onSubmit={(event) => {
              event.preventDefault();
              act("undo", "undo", { n: amount });
            }}
          >
            <label className="field-inline">
              <span>Undo the last</span>
              <input
                type="number"
                min="1"
                max={Math.max(1, undoable)}
                value={amount}
                className="narrow-input"
                onChange={(event) => setAmount(Math.max(1, Number(event.target.value) || 1))}
              />
              <span>{amount === 1 ? "action" : "actions"}</span>
            </label>
            <button type="submit" className="primary" disabled={!!working || busy || !undoable}>
              {working === "undo" ? "Undoing" : "Undo"}
            </button>
          </form>
          <h4 className="label sub-head">Checkpoints</h4>
          <p className="block-note">Mark this point, then return to it later. Checkpoints last until Studio closes.</p>
          <form
            className="inline-form"
            onSubmit={(event) => {
              event.preventDefault();
              act("checkpoint", "checkpoint", { label });
            }}
          >
            <input
              className="grow"
              value={label}
              placeholder="Label, for example before the refactor"
              aria-label="Checkpoint label"
              onChange={(event) => setLabel(event.target.value)}
            />
            <button type="submit" disabled={!!working || busy}>
              {working === "checkpoint" ? "Saving" : "Create checkpoint"}
            </button>
          </form>
          {checkpoints.length > 0 && (
            <ul className="records compact">
              {checkpoints.map((point) => (
                <li key={point.checkpoint_id}>
                  <div className="record-head">
                    <h4>
                      {point.label || "Unnamed checkpoint"}{" "}
                      <span className="dim plain-weight">{point.ts ? ago(seconds(point.ts)) : ""}</span>
                    </h4>
                    <button
                      type="button"
                      className="small"
                      disabled={!!working || busy}
                      onClick={() => act(point.checkpoint_id, "undo", { checkpoint: point.checkpoint_id })}
                    >
                      {working === point.checkpoint_id ? "Undoing" : "Undo back to here"}
                    </button>
                  </div>
                </li>
              ))}
            </ul>
          )}
          {outcome && (
            <div className="verdict-box" role="status">
              {outcome.length ? (
                outcome.map((item, index) => (
                  <p key={index}>
                    <code>{item.tool}</code> {String(item.outcome || "").replace(/_/g, " ")}
                    {item.reason ? ": " + item.reason : ""}
                  </p>
                ))
              ) : (
                <p>Nothing to undo since that point.</p>
              )}
            </div>
          )}
        </Block>
      </div>
    </Page>
  );
});
