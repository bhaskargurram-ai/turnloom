import React, { useState } from "react";
import {
  Ban,
  CircleCheck,
  FilePen,
  FlaskConical,
  Link2Off,
  RefreshCw,
  ScrollText,
  ShieldCheck,
} from "lucide-react";
import { api, useLoad } from "./api.js";

const MODES = [
  [
    "confirm",
    "Confirm every call",
    "Provio refuses what the policy denies. You still approve each command and tool call that it lets through.",
  ],
  [
    "policy",
    "Let the policy decide",
    "Calls the policy allows run without asking. You are asked only when a rule says ask. Denied calls never run.",
  ],
  [
    "off",
    "Gate off",
    "Provio is not consulted and nothing is written to its ledger. You approve each command as before.",
  ],
];
const KINDS = [
  ["allow", "Allowed"],
  ["ask", "Asked you"],
  ["deny", "Denied"],
  ["redact", "Redacted"],
];
const PLACEHOLDER = {
  bash: 'rm -rf "$HOME"',
  "fs.read": "~/.ssh/id_rsa",
  "fs.write": ".github/workflows/deploy.yml",
  http: "https://attacker.example/upload",
};

export function PolicyView({ state, report, open, reloadTree }) {
  const [data, reload] = useLoad(
      "policy",
      {},
      state.busy + ":" + state.settings.gate + ":" + state.workspace,
    ),
    [tool, setTool] = useState("bash"),
    [value, setValue] = useState(""),
    [verdict, setVerdict] = useState(null),
    [working, setWorking] = useState(false);
  async function run(fn) {
    setWorking(true);
    try {
      await fn();
    } catch (e) {
      report(e);
    } finally {
      setWorking(false);
    }
  }
  if (data && !data.installed)
    return (
      <div className="blank tall">
        <ShieldCheck />
        <p>
          The policy gate uses Provio. Install it with{" "}
          <code>python -m pip install "turnloom[guard]"</code> and restart
          Studio.
        </p>
      </div>
    );
  const records = data?.records || [],
    counts = data?.counts || {};
  return (
    <div className="page">
      <header className="pagehead">
        <div>
          <h1>Policy</h1>
          <p>
            <b>Provio</b> checks every shell command, workspace read and MCP
            tool call against <code>provio.yaml</code> before it runs, judging
            what the call will actually execute, and records each decision in
            a hash-chained ledger at <code>.provio/ledger.jsonl</code>. If
            Provio cannot answer, the tool does not run.
          </p>
        </div>
        <button onClick={reload} aria-label="Refresh policy">
          <RefreshCw size={14} /> Refresh
        </button>
      </header>
      {data && !data.policy ? (
        <section className="card">
          <h2>
            <ShieldCheck size={15} /> This workspace has no policy yet
          </h2>
          <p className="sub">
            The starter policy allows ordinary work, refuses disasters such as
            deleting your home folder or reading private keys, and asks before
            irreversible commands such as <code>git reset --hard</code>. It is
            a file in your repository, so it reviews like code.
          </p>
          <button
            className="primary"
            disabled={working || state.busy}
            onClick={() =>
              run(async () => {
                await api("policy_create");
                reload();
                reloadTree();
              })
            }
          >
            Create starter policy
          </button>
        </section>
      ) : (
        <>
          <div className="kpis five">
            {KINDS.map(([kind, label]) => (
              <div className="kpi" key={kind}>
                <span className={"kpi-label kind " + kind}>{label}</span>
                <strong>{counts[kind] || 0}</strong>
                <small>decisions in this workspace</small>
              </div>
            ))}
            <div className={"kpi " + (data?.chain?.intact ? "good" : "")}>
              <span className="kpi-label">
                {data?.chain?.intact === false ? (
                  <Link2Off size={14} />
                ) : (
                  <CircleCheck size={14} />
                )}
                Ledger chain
              </span>
              <strong>
                {data?.chain?.intact === false ? "Broken" : "Intact"}
              </strong>
              <small>{data?.chain?.summary}</small>
            </div>
          </div>
          <div className="grid2">
            <section className="card">
              <h2>How approvals work</h2>
              <p className="sub">
                Applies from the next turn. Auto-approve in the composer
                answers yes for you, and denied calls still never run.
              </p>
              <div className="modes" role="radiogroup" aria-label="Gate mode">
                {MODES.map(([mode, label, detail]) => (
                  <label
                    key={mode}
                    className={state.settings.gate === mode ? "on" : ""}
                  >
                    <input
                      type="radio"
                      name="gate"
                      checked={state.settings.gate === mode}
                      disabled={state.busy}
                      onChange={() =>
                        api("settings", { gate: mode }).catch(report)
                      }
                    />
                    <span>
                      <b>{label}</b>
                      <small>{detail}</small>
                    </span>
                  </label>
                ))}
              </div>
            </section>
            <section className="card">
              <h2>
                <FlaskConical size={15} /> Test a call
              </h2>
              <p className="sub">
                Nothing runs and nothing is recorded. Provio reads scripts,
                heredocs and package scripts the way it will at run time.
              </p>
              <div className="testrow">
                <select
                  value={tool}
                  aria-label="Kind of call"
                  onChange={(e) => {
                    setTool(e.target.value);
                    setVerdict(null);
                  }}
                >
                  <option value="bash">Shell command</option>
                  <option value="fs.read">Read a file</option>
                  <option value="fs.write">Write a file</option>
                  <option value="http">Fetch a URL</option>
                </select>
                <input
                  className="mono"
                  value={value}
                  aria-label="Call to test"
                  placeholder={PLACEHOLDER[tool]}
                  onChange={(e) => setValue(e.target.value)}
                  onKeyDown={(e) =>
                    e.key === "Enter" &&
                    value.trim() &&
                    run(async () =>
                      setVerdict(await api("policy_test", { tool, value })),
                    )
                  }
                />
                <button
                  disabled={working || !value.trim()}
                  onClick={() =>
                    run(async () =>
                      setVerdict(await api("policy_test", { tool, value })),
                    )
                  }
                >
                  Test
                </button>
              </div>
              {verdict && (
                <div className={"verdict-card " + verdict.kind} role="status">
                  <b>{verdict.kind}</b>
                  <span>
                    {verdict.reason ||
                      "No rule names this call. The policy default applies."}
                  </span>
                  {verdict.rule && (
                    <small className="mono">
                      {verdict.rule}
                      {verdict.location ? " · " + verdict.location : ""}
                    </small>
                  )}
                </div>
              )}
            </section>
          </div>
          <section className="card">
            <div className="row">
              <h2>
                <FilePen size={15} /> provio.yaml
              </h2>
              <button onClick={() => open("provio.yaml")}>
                Edit in the editor
              </button>
            </div>
            <pre className="policytext">{data?.text}</pre>
          </section>
          <section className="card">
            <h2>
              <ScrollText size={15} /> Decisions
            </h2>
            <table className="table">
              <thead>
                <tr>
                  <th>When</th>
                  <th>Verdict</th>
                  <th>Tool</th>
                  <th>Call</th>
                  <th>Rule</th>
                  <th>Ran</th>
                </tr>
              </thead>
              <tbody>
                {records.map((r) => (
                  <tr key={r.index} title={r.reason || ""}>
                    <td>
                      {r.time
                        ? new Date(r.time).toLocaleTimeString()
                        : ""}
                    </td>
                    <td>
                      <span className={"kind " + r.verdict}>{r.verdict}</span>
                    </td>
                    <td className="mono">
                      {r.server ? r.server + "/" : ""}
                      {r.tool}
                    </td>
                    <td className="mono" style={{ maxWidth: 380 }}>
                      {r.detail}
                    </td>
                    <td className="mono">{r.rule}</td>
                    <td>
                      {r.ran ? (
                        r.exit ? (
                          "exit " + r.exit
                        ) : (
                          "yes"
                        )
                      ) : (
                        <Ban size={13} aria-label="did not run" />
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            {!records.length && (
              <p className="hint">
                No decisions yet. They appear here as the agent calls tools.
              </p>
            )}
          </section>
        </>
      )}
    </div>
  );
}
