import React from "react";
import {
  Area,
  AreaChart,
  Bar,
  BarChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import {
  ArrowDownRight,
  CircleDollarSign,
  Database,
  Gauge,
  Layers3,
  Sparkles,
} from "lucide-react";
import { api, percent, tokens, usd, useLoad } from "./api.js";

const SERIES = {
  fresh: { color: "#79bdcf", label: "Fresh input" },
  cached: { color: "#afadf2", label: "Cached input" },
  output: { color: "#e3b783", label: "Output" },
};
const PARTS = [
  ["system", "System prompt and persona", "#afadf2"],
  ["user", "Your tasks", "#79bdcf"],
  ["assistant", "Model replies and tool calls", "#e3b783"],
  ["tools", "Tool results in full", "#ef9f8f"],
  ["folded", "Folded previews", "#87dfc8"],
];
const tip = {
  contentStyle: {
    background: "#11171d",
    border: "1px solid #2c3640",
    borderRadius: 8,
    fontSize: 12,
  },
  cursor: { fill: "rgba(255,255,255,.04)" },
};

function Kpi({ icon, label, value, detail, tone }) {
  return (
    <div className={"kpi " + (tone || "")}>
      <span className="kpi-label">
        {icon}
        {label}
      </span>
      <strong>{value}</strong>
      <small>{detail}</small>
    </div>
  );
}

export function ContextBar({ meter }) {
  const parts = meter.context || {},
    used = meter.prompt || PARTS.reduce((n, [key]) => n + (parts[key] || 0), 0),
    sum = PARTS.reduce((n, [key]) => n + (parts[key] || 0), 0) || 1;
  return (
    <div className="contextbar">
      <div
        className="track"
        role="img"
        aria-label={`${percent(used, meter.window)}% of the context window in use`}
      >
        {PARTS.map(([key, label, color]) =>
          parts[key] ? (
            <i
              key={key}
              title={`${label}: about ${tokens((parts[key] * used) / sum)} tokens`}
              style={{
                width: ((parts[key] / sum) * used * 100) / meter.window + "%",
                background: color,
              }}
            />
          ) : null,
        )}
      </div>
      <div className="legend">
        {PARTS.map(([key, label, color]) => (
          <span key={key}>
            <i style={{ background: color }} />
            {label}
            <b>{tokens(((parts[key] || 0) * used) / sum)}</b>
          </span>
        ))}
      </div>
    </div>
  );
}

function Field({ label, hint, children }) {
  return (
    <label className="field">
      <span>
        {label}
        {hint && <small>{hint}</small>}
      </span>
      {children}
    </label>
  );
}

export function CostView({ state, report, openConnect }) {
  const [usage] = useLoad(
    "usage",
    {},
    state.meter.steps + ":" + state.busy + ":" + state.model,
  );
  const meter = state.meter,
    settings = state.settings,
    connection = state.connection;
  const set = (patch) => api("settings", patch).catch(report);
  const steps = (usage?.steps || []).map((s) => ({
    step: s.step,
    fresh: s.prompt_tokens - s.cached_tokens,
    cached: s.cached_tokens,
    output: s.completion_tokens,
  }));
  const totals = usage?.totals,
    priced = meter.priced,
    saving = connection
      ? (meter.saved_tokens * (connection.input || 0)) / 1e6
      : 0;
  return (
    <div className="page">
      <header className="pagehead">
        <div>
          <h1>Cost and tokens</h1>
          <p>
            Measured from provider-reported usage on every model call.{" "}
            {priced
              ? "Costs use the rates your provider lists for the connected model."
              : "The connected route lists no prices, so cost is not estimated."}
          </p>
        </div>
      </header>
      <div className="kpis">
        <Kpi
          icon={<CircleDollarSign size={14} />}
          label="This session"
          value={priced ? usd(meter.cost) : tokens(meter.prompt_tokens + meter.completion_tokens)}
          detail={
            priced
              ? `${tokens(meter.prompt_tokens)} in · ${tokens(meter.completion_tokens)} out`
              : "tokens · no listed price"
          }
        />
        <Kpi
          icon={<Gauge size={14} />}
          label="Context window"
          value={percent(meter.prompt, meter.window) + "%"}
          detail={`${tokens(meter.prompt)} of ${tokens(meter.window)} in the last request`}
        />
        <Kpi
          icon={<Database size={14} />}
          label="Served from cache"
          value={percent(meter.cached_tokens, meter.prompt_tokens) + "%"}
          detail={`${tokens(meter.cached_tokens)} input tokens at the cached rate`}
        />
        <Kpi
          tone="good"
          icon={<Layers3 size={14} />}
          label="Kept out by folding"
          value={tokens(meter.saved_tokens)}
          detail={
            priced && saving
              ? `about ${usd(saving)} of input not sent`
              : "tokens not re-sent across calls"
          }
        />
      </div>

      <section className="card">
        <h2>Prompt composition</h2>
        <p className="sub">
          What the next request carries. Folded results stay readable through
          the Context Loom.
        </p>
        {meter.prompt ? (
          <ContextBar meter={meter} />
        ) : (
          <p className="hint">Send a task to see where the prompt goes.</p>
        )}
      </section>

      <div className="grid2">
        <section className="card">
          <h2>Tokens per model call</h2>
          <p className="sub">This session, in call order.</p>
          <div className="keys">
            {Object.entries(SERIES).map(([key, s]) => (
              <span key={key}>
                <i style={{ background: s.color }} />
                {s.label}
              </span>
            ))}
          </div>
          {steps.length ? (
            <div className="plot">
              <ResponsiveContainer>
                <BarChart data={steps} barCategoryGap={steps.length > 24 ? 1 : 4}>
                  <CartesianGrid stroke="#222b33" vertical={false} />
                  <XAxis dataKey="step" tickLine={false} axisLine={false} fontSize={11} stroke="#7d8b96" />
                  <YAxis tickFormatter={tokens} tickLine={false} axisLine={false} fontSize={11} stroke="#7d8b96" width={44} />
                  <Tooltip {...tip} formatter={(v, name) => [v.toLocaleString(), SERIES[name].label]} labelFormatter={(l) => "Call " + l} />
                  {Object.entries(SERIES).map(([key, s], i) => (
                    <Bar key={key} dataKey={key} stackId="t" fill={s.color} radius={i === 2 ? [3, 3, 0, 0] : 0} isAnimationActive={false} />
                  ))}
                </BarChart>
              </ResponsiveContainer>
            </div>
          ) : (
            <p className="hint">No model calls yet in this session.</p>
          )}
        </section>
        <section className="card">
          <h2>Workspace spend, last 14 days</h2>
          <p className="sub">
            {totals
              ? `${totals.calls.toLocaleString()} calls · ${tokens(totals.prompt_tokens + totals.completion_tokens)} tokens · ${usd(totals.cost)} recorded`
              : "Loading the ledger…"}
            {totals?.unpriced_calls ? ` · ${totals.unpriced_calls} calls had no listed price` : ""}
          </p>
          {usage?.days.length ? (
            <div className="plot">
              <ResponsiveContainer>
                <AreaChart data={usage.days}>
                  <CartesianGrid stroke="#222b33" vertical={false} />
                  <XAxis dataKey="day" tickFormatter={(d) => d.slice(5)} tickLine={false} axisLine={false} fontSize={11} stroke="#7d8b96" />
                  <YAxis tickFormatter={totals.cost ? usd : tokens} tickLine={false} axisLine={false} fontSize={11} stroke="#7d8b96" width={52} />
                  <Tooltip {...tip} formatter={(v) => [totals.cost ? usd(v) : tokens(v), totals.cost ? "Cost" : "Tokens"]} />
                  <Area dataKey={totals.cost ? "cost" : "tokens"} stroke="#87dfc8" strokeWidth={2} fill="#87dfc8" fillOpacity={0.14} isAnimationActive={false} />
                </AreaChart>
              </ResponsiveContainer>
            </div>
          ) : (
            <p className="hint">The ledger at .turnloom/usage.jsonl fills as you work.</p>
          )}
        </section>
      </div>

      <div className="grid2">
        <section className="card">
          <h2>Spending controls</h2>
          <p className="sub">Applied to the next turn. Saved with the workspace.</p>
          <div className="fields">
            <Field label="Budget per turn" hint={priced ? "USD · 0 means no cap" : "needs a priced model"}>
              <input type="number" min="0" step="0.05" disabled={!priced || state.busy} defaultValue={settings.budget} key={"b" + settings.budget}
                onBlur={(e) => +e.target.value !== settings.budget && set({ budget: +e.target.value })} aria-label="Budget per turn in USD" />
            </Field>
            <Field label="Reply limit" hint="tokens per model call">
              <input type="number" min="256" step="256" disabled={state.busy} defaultValue={settings.reply} key={"r" + settings.reply}
                onBlur={(e) => +e.target.value !== settings.reply && set({ reply: +e.target.value })} aria-label="Reply token limit" />
            </Field>
            <Field label="Reasoning effort" hint="for models that accept it">
              <select value={settings.effort} disabled={state.busy} onChange={(e) => set({ effort: e.target.value })} aria-label="Reasoning effort">
                <option value="">Model default</option>
                {["minimal", "low", "medium", "high"].map((x) => <option key={x}>{x}</option>)}
              </select>
            </Field>
            <Field label="Keep unfolded" hint={`newest ${settings.keep} tool results`}>
              <input type="range" min="1" max="10" disabled={state.busy || !settings.fold} value={settings.keep}
                onChange={(e) => set({ keep: +e.target.value })} aria-label="Tool results kept unfolded" />
            </Field>
          </div>
          <label className="switch">
            <input type="checkbox" checked={settings.fold} disabled={state.busy} onChange={(e) => set({ fold: e.target.checked })} />
            <span>
              <b>Fold older tool output</b>
              <small>Long results leave the prompt as a preview with a recall reference.</small>
            </span>
          </label>
          <label className="switch">
            <input type="checkbox" checked={settings.cache} disabled={state.busy} onChange={(e) => set({ cache: e.target.checked })} />
            <span>
              <b>Prompt caching for Claude routes</b>
              <small>Marks the system prompt as a cache breakpoint. Other providers cache prefixes on their own.</small>
            </span>
          </label>
        </section>
        <section className="card">
          <h2>
            <Sparkles size={15} /> Lower-cost models on this provider
          </h2>
          {!connection ? (
            <p className="hint">Connect a model to compare routes.</p>
          ) : !usage?.alternatives?.catalog ? (
            <p className="hint">
              Browse this provider's models once to compare prices here.{" "}
              <button className="link" onClick={openConnect}>Open model connection</button>
            </p>
          ) : (
            <>
              <p className="sub">
                {usage.alternatives.basis.measured ? "This session's" : "A sample"} {tokens(usage.alternatives.basis.prompt_tokens)} input and{" "}
                {tokens(usage.alternatives.basis.completion_tokens)} output tokens come to {usd(usage.alternatives.basis.current)} on{" "}
                {connection.model} at list prices, before cache discounts. Prices only: capability differs between models.
              </p>
              <table className="table">
                <thead><tr><th>Model</th><th>In / out per M</th><th>Same usage</th><th></th></tr></thead>
                <tbody>
                  {usage.alternatives.models.map((m) => (
                    <tr key={m.model}>
                      <td className="mono">{m.model}</td>
                      <td>{usd(m.input)} / {usd(m.output)}</td>
                      <td>{usd(m.projected)}</td>
                      <td className="down">
                        {usage.alternatives.basis.current > 0 && (
                          <><ArrowDownRight size={13} />{100 - percent(m.projected, usage.alternatives.basis.current)}%</>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
              {!usage.alternatives.models.length && <p className="hint">No cheaper tool-capable model is listed for this provider.</p>}
            </>
          )}
        </section>
      </div>

      <div className="grid2">
        {[["models", "By model", "model"], ["personas", "By persona", "persona"]].map(([key, title, column]) => (
          <section className="card" key={key}>
            <h2>{title}</h2>
            <table className="table">
              <thead><tr><th>{column[0].toUpperCase() + column.slice(1)}</th><th>Calls</th><th>Tokens</th><th>Cost</th><th>Folded away</th></tr></thead>
              <tbody>
                {(usage?.[key] || []).map((row) => (
                  <tr key={row[column]}>
                    <td className="mono">{row[column]}</td>
                    <td>{row.calls}</td>
                    <td>{tokens(row.tokens)}</td>
                    <td>{usd(row.cost)}</td>
                    <td>{tokens(row.saved_tokens)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            {!usage?.[key]?.length && <p className="hint">Nothing recorded yet.</p>}
          </section>
        ))}
      </div>
      <p className="foot">
        Token counts for the composition bar use {usage?.exact_tokens ? "tiktoken's o200k vocabulary" : "four characters per token"}, an approximation for
        non-OpenAI models. Totals and costs come from provider-reported usage. Your invoice can differ.
      </p>
    </div>
  );
}
