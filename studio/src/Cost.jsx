import React, { memo, useEffect, useState } from "react";
import { configure, useLoad, useStudio } from "./store.js";
import { Block, Figures, Page, Switch } from "./Page.jsx";
import { Bars, Composition, Legend, Spark } from "./Charts.jsx";
import { compact, count, money, percent, rate } from "./util.js";

const CALL_SERIES = [
  { key: "fresh", label: "Fresh input", tone: "s1" },
  { key: "cached", label: "Cached input", tone: "s2" },
  { key: "output", label: "Output", tone: "s3" },
];

const PARTS = [
  ["system", "System prompt and persona", "s1"],
  ["user", "Your tasks", "s2"],
  ["assistant", "Model replies and tool calls", "s3"],
  ["tools", "Tool results in full", "s4"],
  ["folded", "Folded previews", "s5"],
];

/** A number field that sends its value when the operator leaves it or presses Enter. */
function NumberField({ label, hint, value, min, max, step, disabled, onCommit }) {
  const [text, setText] = useState(String(value));
  useEffect(() => setText(String(value)), [value]);
  const commit = () => {
    const next = Number(text);
    if (text.trim() === "" || !Number.isFinite(next)) return setText(String(value));
    if (next !== value) onCommit(next);
  };
  return (
    <label className="field">
      <span>{label}</span>
      <input
        type="number"
        inputMode="decimal"
        value={text}
        min={min}
        max={max}
        step={step}
        disabled={disabled}
        onChange={(event) => setText(event.target.value)}
        onBlur={commit}
        onKeyDown={(event) => event.key === "Enter" && event.currentTarget.blur()}
      />
      <small>{hint}</small>
    </label>
  );
}

function Totals({ title, column, rows }) {
  return (
    <Block title={title}>
      {rows?.length ? (
        <div className="table-wrap">
          <table className="grid">
            <thead>
              <tr>
                <th scope="col">{column}</th>
                <th scope="col" className="num">
                  Calls
                </th>
                <th scope="col" className="num">
                  Tokens
                </th>
                <th scope="col" className="num">
                  Cost
                </th>
                <th scope="col" className="num">
                  Folded away
                </th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => (
                <tr key={row[column.toLowerCase()]}>
                  <td className="mono wrap">{row[column.toLowerCase()]}</td>
                  <td className="num">{count(row.calls)}</td>
                  <td className="num">{compact(row.tokens)}</td>
                  <td className="num">{row.cost ? money(row.cost) : <span className="dim">none recorded</span>}</td>
                  <td className="num">{compact(row.saved_tokens)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <p className="block-note">Nothing recorded yet.</p>
      )}
    </Block>
  );
}

export const Cost = memo(function Cost({ active, report, onConnect }) {
  const meter = useStudio((state) => state.meter);
  const settings = useStudio((state) => state.settings);
  const connection = useStudio((state) => state.connection);
  const busy = useStudio((state) => state.busy);
  const { data: usage, error, reload } = useLoad("usage", {}, `${meter.steps}:${busy}:${connection?.model}`, active);
  const [table, setTable] = useState(false);
  const set = (patch) => configure(patch).catch(report);

  const priced = meter.priced;
  const steps = usage?.steps || [];
  const calls = steps.map((step) => ({
    label: String(step.step),
    title: "Call " + step.step + (step.persona ? " (" + step.persona + ")" : ""),
    values: {
      fresh: Math.max(0, step.prompt_tokens - step.cached_tokens),
      cached: step.cached_tokens,
      output: step.completion_tokens,
    },
  }));
  const totals = usage?.totals;
  const byCost = !!totals?.cost;
  const days = (usage?.days || []).map((day) => ({
    label: day.day.slice(5),
    title: day.day,
    values: { value: byCost ? day.cost : day.tokens },
  }));
  const parts = meter.context || {};
  const sum = PARTS.reduce((total, [key]) => total + (parts[key] || 0), 0) || 1;
  const used = meter.prompt || 0;
  const composition = PARTS.map(([key, label, tone]) => ({ key, label, tone, value: ((parts[key] || 0) * used) / sum }));
  const saving = connection ? (meter.saved_tokens * (connection.input || 0)) / 1e6 : 0;
  const alternatives = usage?.alternatives;

  return (
    <Page
      title="Cost and tokens"
      active={active}
      onReload={reload}
      error={error}
      lead={
        "Measured from the usage the provider reports on every model call. " +
        (priced
          ? "Cost uses the rates listed for the connected model."
          : connection
            ? "The connected model lists no prices, so cost is not estimated."
            : "Connect a model to start measuring.")
      }
    >
      <Figures
        items={[
          {
            label: "This session",
            value: priced ? money(meter.cost) : compact(meter.prompt_tokens + meter.completion_tokens),
            detail: priced
              ? `${compact(meter.prompt_tokens)} tokens in, ${compact(meter.completion_tokens)} out, ${count(meter.steps)} calls`
              : `tokens over ${count(meter.steps)} model calls`,
          },
          {
            label: "Context window",
            value: meter.prompt ? percent(meter.prompt, meter.window) + "%" : "0%",
            detail: `${compact(meter.prompt)} of ${compact(meter.window)} tokens in the last request`,
          },
          {
            label: "Read from cache",
            value: percent(meter.cached_tokens, meter.prompt_tokens) + "%",
            detail: `${compact(meter.cached_tokens)} input tokens at the cached rate`,
          },
          {
            label: "Kept out by folding",
            value: compact(meter.saved_tokens),
            detail: priced && saving ? `tokens, about ${money(saving)} of input not sent` : "tokens not sent again across calls",
          },
        ]}
      />

      <Block
        title="What the next request carries"
        note="The bar is the model's context window. Folded results stay readable in the Context Loom."
        tools={
          (meter.spark || []).length > 1 && (
            <>
              <span className="dim">Prompt size over the last {meter.spark.length} calls</span>
              <Spark values={meter.spark} label="Prompt tokens per recent call" />
            </>
          )
        }
      >
        {meter.prompt ? (
          <>
            <Composition
              parts={composition}
              used={used}
              window={meter.window}
              label={`${percent(used, meter.window)}% of the context window is in use`}
            />
            <Legend series={composition.map((part) => ({ ...part, value: compact(part.value) }))} />
          </>
        ) : (
          <p className="block-note">Send a task to see what fills the prompt.</p>
        )}
      </Block>

      <div className="cols">
        <Block
          title="Tokens per model call"
          note="This session, in call order."
          tools={
            steps.length > 0 && (
              <button type="button" className="small quiet" aria-pressed={table} onClick={() => setTable(!table)}>
                {table ? "Show chart" : "Show table"}
              </button>
            )
          }
        >
          {!steps.length ? (
            <p className="block-note">No model calls yet in this session.</p>
          ) : table ? (
            <div className="table-wrap short">
              <table className="grid">
                <thead>
                  <tr>
                    <th scope="col" className="num">
                      Call
                    </th>
                    <th scope="col">Persona</th>
                    <th scope="col" className="num">
                      Input
                    </th>
                    <th scope="col" className="num">
                      Cached
                    </th>
                    <th scope="col" className="num">
                      Output
                    </th>
                    <th scope="col" className="num">
                      Cost
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {steps.map((step) => (
                    <tr key={step.step}>
                      <td className="num">{step.step}</td>
                      <td>{step.persona}</td>
                      <td className="num">{count(step.prompt_tokens)}</td>
                      <td className="num">{count(step.cached_tokens)}</td>
                      <td className="num">{count(step.completion_tokens)}</td>
                      <td className="num">{step.cost == null ? <span className="dim">no price</span> : money(step.cost)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <>
              <Legend series={CALL_SERIES} />
              <Bars rows={calls} series={CALL_SERIES} label="Tokens per model call, stacked by fresh input, cached input and output" />
            </>
          )}
        </Block>
        <Block
          title="This workspace, last 14 days"
          note={
            totals
              ? `${count(totals.calls)} calls, ${compact(totals.prompt_tokens + totals.completion_tokens)} tokens, ${money(totals.cost) || "$0"} recorded in .turnloom/usage.jsonl.` +
                (totals.unpriced_calls ? ` ${count(totals.unpriced_calls)} calls had no listed price.` : "")
              : "Reading the ledger"
          }
        >
          {days.length ? (
            <Bars
              rows={days}
              series={[{ key: "value", label: byCost ? "Cost" : "Tokens", tone: "s1" }]}
              format={byCost ? money : compact}
              axis={byCost ? (value) => "$" + Number(value.toPrecision(2)) : compact}
              label={byCost ? "Cost per day" : "Tokens per day"}
            />
          ) : (
            <p className="block-note">The ledger fills as you work.</p>
          )}
        </Block>
      </div>

      <div className="cols">
        <Block title="Spending controls" note={busy ? "A turn is running. Settings can change when it ends." : "Applied from the next turn and saved with the workspace."}>
          <div className="fields">
            <NumberField
              label="Budget per turn, USD"
              hint={priced ? "0 means no cap" : "Needs a model with listed prices"}
              value={settings.budget}
              min={0}
              step={0.05}
              disabled={!priced || busy}
              onCommit={(budget) => set({ budget })}
            />
            <NumberField
              label="Reply limit, tokens"
              hint="Per model call"
              value={settings.reply}
              min={256}
              step={256}
              disabled={busy}
              onCommit={(reply) => set({ reply })}
            />
            <label className="field">
              <span>Reasoning effort</span>
              <select value={settings.effort} disabled={busy} onChange={(event) => set({ effort: event.target.value })}>
                <option value="">Model default</option>
                {["minimal", "low", "medium", "high"].map((level) => (
                  <option key={level}>{level}</option>
                ))}
              </select>
              <small>For models that accept it</small>
            </label>
            <NumberField
              label="Results kept unfolded"
              hint="Newest tool results left whole"
              value={settings.keep}
              min={1}
              max={20}
              step={1}
              disabled={busy || !settings.fold}
              onCommit={(keep) => set({ keep })}
            />
          </div>
          <Switch
            label="Fold older tool output"
            detail="Long results leave the prompt as a short preview with a reference the agent can recall."
            checked={settings.fold}
            disabled={busy}
            onChange={(fold) => set({ fold })}
          />
          <Switch
            label="Prompt caching for Claude models"
            detail="Marks the system prompt as a cache point. Other providers cache prefixes on their own."
            checked={settings.cache}
            disabled={busy}
            onChange={(cache) => set({ cache })}
          />
        </Block>
        <Block title="Lower-cost models from this provider">
          {!connection ? (
            <p className="block-note">
              Connect a model to compare prices.{" "}
              <button type="button" className="link" onClick={onConnect}>
                Open model connection
              </button>
            </p>
          ) : !alternatives?.catalog ? (
            <p className="block-note">
              The model list for this provider has not been read in this session.{" "}
              <button type="button" className="link" onClick={onConnect}>
                Open model connection
              </button>{" "}
              and load it once to compare prices here.
            </p>
          ) : (
            <>
              <p className="block-note">
                {alternatives.basis.measured ? "This session's" : "A sample of"} {compact(alternatives.basis.prompt_tokens)}{" "}
                input and {compact(alternatives.basis.completion_tokens)} output tokens cost{" "}
                {money(alternatives.basis.current) || "$0"} on <code>{connection.model}</code> at list prices, before cache
                discounts. Price is the only thing compared: the models differ in ability.
              </p>
              {alternatives.models.length ? (
                <div className="table-wrap">
                  <table className="grid">
                    <thead>
                      <tr>
                        <th scope="col">Model</th>
                        <th scope="col" className="num">
                          In / out per 1M
                        </th>
                        <th scope="col" className="num">
                          Same usage
                        </th>
                        <th scope="col" className="num">
                          Saving
                        </th>
                      </tr>
                    </thead>
                    <tbody>
                      {alternatives.models.map((row) => (
                        <tr key={row.model}>
                          <td className="mono wrap">{row.model}</td>
                          <td className="num">
                            {rate(row.input)} / {rate(row.output)}
                          </td>
                          <td className="num">{money(row.projected) || "$0"}</td>
                          <td className="num">
                            {alternatives.basis.current > 0 ? 100 - percent(row.projected, alternatives.basis.current) + "%" : ""}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              ) : (
                <p className="block-note">No cheaper model with tool calling is listed for this provider.</p>
              )}
            </>
          )}
        </Block>
      </div>

      <div className="cols">
        <Totals title="By model" column="Model" rows={usage?.models} />
        <Totals title="By persona" column="Persona" rows={usage?.personas} />
      </div>
      <p className="page-foot">
        The composition bar counts tokens with {usage?.exact_tokens ? "tiktoken's o200k vocabulary" : "four characters per token"},
        an approximation for models from other vendors. Totals and costs come from the provider's own usage numbers. An
        invoice can still differ.
      </p>
    </Page>
  );
});
