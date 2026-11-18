import React, { memo, useState } from "react";
import { api } from "./api.js";
import { configure, refresh, touchDisk, useLoad, useStudio } from "./store.js";
import { Block, Figures, Install, Page } from "./Page.jsx";
import { count } from "./util.js";

const MODES = [
  ["confirm", "Confirm every call", "Provio refuses what the policy denies. You still approve each command, edit and tool call it lets through."],
  ["policy", "Let the policy decide", "Calls the policy allows run without asking. You are asked only where a rule says ask. Denied calls never run."],
  ["off", "Gate off", "Provio is not consulted and nothing is written to its ledger. You approve each command as usual."],
];

const KINDS = [
  ["allow", "Allowed"],
  ["ask", "Asked you"],
  ["deny", "Denied"],
  ["redact", "Redacted"],
];

const EXAMPLE = {
  bash: 'rm -rf "$HOME"',
  "fs.read": "~/.ssh/id_rsa",
  "fs.write": ".github/workflows/deploy.yml",
  http: "https://example.com/upload",
};

function time(value) {
  if (!value) return "";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? String(value) : date.toLocaleString(undefined, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

export const Policy = memo(function Policy({ active, report, onOpen }) {
  const busy = useStudio((state) => state.busy);
  const mode = useStudio((state) => state.settings.gate);
  const { data, error, reload } = useLoad("policy", {}, `${busy}:${mode}`, active);
  const [tool, setTool] = useState("bash");
  const [value, setValue] = useState("");
  const [verdict, setVerdict] = useState(null);
  const [working, setWorking] = useState(false);

  if (data && !data.installed)
    return (
      <Install
        title="Policy"
        active={active}
        what="Provio, the policy gate,"
        extra="guard"
        after={
          data.policy && (
          <Block
            title="This workspace has a provio.yaml"
            note={
              mode === "off"
                ? "The gate is off, so turns run without the policy and you approve each command yourself."
                : "Its rules cannot be checked without Provio, so turns will not start while the gate is on. Install Provio, or turn the gate off to work without the policy."
            }
          >
            <div className="segmented" role="group" aria-label="Gate mode">
              <button type="button" aria-pressed={mode !== "off"} disabled={busy} onClick={() => configure({ gate: "confirm" }).catch(report)}>
                Gate on
              </button>
              <button type="button" aria-pressed={mode === "off"} disabled={busy} onClick={() => configure({ gate: "off" }).catch(report)}>
                Gate off
              </button>
            </div>
          </Block>
          )
        }
      >
        With Provio, every shell command, file read and tool call is checked against a policy file in the workspace
        before it runs, and each decision is written to a tamper-evident ledger.
      </Install>
    );

  async function run(work) {
    setWorking(true);
    try {
      await work();
    } catch (failure) {
      report(failure);
    } finally {
      setWorking(false);
    }
  }
  const test = () =>
    value.trim() && run(async () => setVerdict({ ...(await api("policy_test", { tool, value })), tool, value }));

  const records = data?.records || [];
  const counts = data?.counts || {};
  return (
    <Page
      title="Policy"
      active={active}
      onReload={reload}
      error={error}
      lead={
        <>
          Provio checks every shell command, file read and MCP tool call against <code>provio.yaml</code> before it
          runs, and records each decision in a hash-chained ledger at <code>.provio/ledger.jsonl</code>. When Provio
          cannot answer, the call does not run.
        </>
      }
    >
      {data && !data.policy ? (
        <Block title="This workspace has no policy yet">
          <p className="block-note">
            The starter policy allows ordinary work, refuses disasters such as deleting your home folder or reading
            private keys, and asks before irreversible commands such as <code>git reset --hard</code>. It is a file in
            the workspace, so it is reviewed like code.
          </p>
          <p>
            <button
              type="button"
              className="primary"
              disabled={working || busy}
              onClick={() =>
                run(async () => {
                  await api("policy_create");
                  touchDisk();
                  refresh();
                  reload();
                })
              }
            >
              Create starter policy
            </button>
          </p>
        </Block>
      ) : (
        data && (
          <>
            <Figures
              items={[
                ...KINDS.map(([kind, label]) => ({ label, value: count(counts[kind] || 0), detail: "decisions recorded" })),
                {
                  label: "Ledger chain",
                  value: data.chain?.intact === false ? "Broken" : "Intact",
                  detail: data.chain?.summary || "",
                  tone: data.chain?.intact === false ? "bad" : "",
                },
              ]}
            />
            <div className="cols">
              <Block
                title="How approvals work"
                note={busy ? "A turn is running. The mode can change when it ends." : "Applies from the next turn. A denied call never runs, in any mode."}
              >
                <div className="choices" role="radiogroup" aria-label="Gate mode">
                  {MODES.map(([name, label, detail]) => (
                    <label key={name} className={"switch" + (mode === name ? " on" : "")}>
                      <input
                        type="radio"
                        name="gate"
                        checked={mode === name}
                        disabled={busy}
                        onChange={() => configure({ gate: name }).catch(report)}
                      />
                      <span>
                        <b>{label}</b>
                        <small>{detail}</small>
                      </span>
                    </label>
                  ))}
                </div>
              </Block>
              <Block title="Test a call" note="Nothing runs and nothing is recorded. Provio reads scripts and package scripts the way it will at run time.">
                <form
                  className="inline-form"
                  onSubmit={(event) => {
                    event.preventDefault();
                    test();
                  }}
                >
                  <select
                    value={tool}
                    aria-label="Kind of call"
                    onChange={(event) => {
                      setTool(event.target.value);
                      setVerdict(null);
                    }}
                  >
                    <option value="bash">Shell command</option>
                    <option value="fs.read">Read a file</option>
                    <option value="fs.write">Write a file</option>
                    <option value="http">Fetch a URL</option>
                  </select>
                  <input
                    className="mono grow"
                    value={value}
                    aria-label="Command, path or URL to test"
                    placeholder={EXAMPLE[tool]}
                    spellCheck={false}
                    onChange={(event) => setValue(event.target.value)}
                  />
                  <button type="submit" disabled={working || !value.trim()}>
                    Test
                  </button>
                </form>
                {verdict && (
                  <div className={"verdict-box " + verdict.kind} role="status">
                    <p>
                      <span className={"verdict " + verdict.kind}>{verdict.kind}</span>{" "}
                      <code className="wrap">{verdict.value}</code>
                    </p>
                    <p>{verdict.reason || "No rule names this call. The policy's default applies."}</p>
                    {verdict.rule && (
                      <p className="dim mono">
                        rule {verdict.rule}
                        {verdict.location ? ", " + verdict.location : ""}
                        {verdict.irreversible ? ", marked irreversible" : ""}
                      </p>
                    )}
                  </div>
                )}
              </Block>
            </div>
            <Block
              title="provio.yaml"
              tools={
                <button type="button" className="small" onClick={() => onOpen("provio.yaml")}>
                  Open in editor
                </button>
              }
            >
              <pre className="output scroll-box" tabIndex={0} aria-label="Policy text">
                {data.text}
              </pre>
            </Block>
            <Block title={`Decisions (${count(records.length)}${data.total > records.length ? " most recent" : ""})`}>
              {records.length ? (
                <div className="table-wrap">
                  <table className="grid">
                    <thead>
                      <tr>
                        <th scope="col">When</th>
                        <th scope="col">Verdict</th>
                        <th scope="col">Tool</th>
                        <th scope="col">Call</th>
                        <th scope="col">Rule</th>
                        <th scope="col">Ran</th>
                      </tr>
                    </thead>
                    <tbody>
                      {records.map((row) => (
                        <tr key={row.index} title={row.reason || undefined}>
                          <td className="num nowrap">{time(row.time)}</td>
                          <td>
                            <span className={"verdict " + row.verdict}>{row.verdict}</span>
                          </td>
                          <td className="mono">
                            {row.server ? row.server + "/" : ""}
                            {row.tool}
                          </td>
                          <td className="mono wrap">{row.detail}</td>
                          <td className="mono">{row.rule || <span className="dim">default</span>}</td>
                          <td>{row.ran ? (row.exit ? "exit " + row.exit : "yes") : <span className="dim">no</span>}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              ) : (
                <p className="block-note">No decisions yet. One row appears here for each call the agent makes while the gate is on.</p>
              )}
            </Block>
          </>
        )
      )}
    </Page>
  );
});
