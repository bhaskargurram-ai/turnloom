import React, { memo, useState } from "react";
import { api } from "./api.js";
import { configure, useLoad, useStudio } from "./store.js";
import { Block, Page } from "./Page.jsx";
import { plural } from "./util.js";

const SAMPLE = `{
  "mcpServers": {
    "git": { "command": "mcp-server-git", "args": ["--repository", "."], "unwind": true },
    "time": { "command": "mcp-server-time" }
  }
}`;

export const Tools = memo(function Tools({ active, report, onView }) {
  const busy = useStudio((state) => state.busy);
  const enabled = useStudio((state) => state.settings.servers);
  const { data, error, reload } = useLoad("servers", {}, enabled.join(","), active);
  const [tools, setTools] = useState({});
  const [starting, setStarting] = useState("");

  async function inspect(name) {
    setStarting(name);
    try {
      const list = await api("server_tools", { name });
      setTools((old) => ({ ...old, [name]: list }));
      reload();
    } catch (failure) {
      report(failure);
    } finally {
      setStarting("");
    }
  }
  // Switching a server on records its definition as it is now, so the list is read again either way.
  const toggle = (name, on) =>
    configure({ servers: on ? [...enabled.filter((item) => item !== name), name] : enabled.filter((item) => item !== name) })
      .then(reload)
      .catch(report);

  const servers = data?.servers || [];
  return (
    <Page
      title="Tools"
      active={active}
      onReload={reload}
      error={error || data?.error}
      lead="Model Context Protocol servers the agent can search and call. Each runs as a local process, and every call asks for your approval. Servers marked Unwind go through the reversibility proxy, which classifies and logs their actions and can undo those the server allows."
    >
      <Block title={`Servers (${servers.length})`} note={busy ? "A turn is running. Servers can be switched when it ends." : undefined}>
        {servers.length ? (
          <ul className="records">
            {servers.map((server) => (
              <li key={server.name}>
                <div className="record-head">
                  <h4>
                    {server.name}
                    {server.bundled && <span className="tag">bundled</span>}
                    {server.unwind && <span className="tag">Unwind</span>}
                    {server.running && <span className="tag live">running</span>}
                    {server.changed && <span className="tag warn">changed, switched off</span>}
                  </h4>
                  <label className="check">
                    <input
                      type="checkbox"
                      checked={server.enabled}
                      disabled={busy}
                      onChange={(event) => toggle(server.name, event.target.checked)}
                    />
                    Available to the agent
                    <span className="sr-only">: {server.name}</span>
                  </label>
                </div>
                {server.changed && (
                  <p className="banner warn slim" role="status">
                    The definition of {server.name} in .turnloom/mcp.json changed after you switched it on, so the agent
                    no longer gets it. Read the command below, then switch it on again if it is what you expect.
                  </p>
                )}
                <code className="command">{server.command}</code>
                {tools[server.name] ? (
                  tools[server.name].length ? (
                    <table className="grid tool-list">
                      <caption className="sr-only">Tools offered by {server.name}</caption>
                      <tbody>
                        {tools[server.name].map((tool) => (
                          <tr key={tool.name}>
                            <th scope="row" className="mono">
                              {tool.name}
                            </th>
                            <td>{(tool.description || "").split("\n")[0]}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  ) : (
                    <p className="block-note">This server offers no tools.</p>
                  )
                ) : (
                  <button type="button" className="small" disabled={!!starting} onClick={() => inspect(server.name)}>
                    {starting === server.name ? "Starting the server" : "Start and list tools"}
                  </button>
                )}
                {tools[server.name]?.length > 0 && <p className="dim small-text">{plural(tools[server.name].length, "tool")}</p>}
              </li>
            ))}
          </ul>
        ) : (
          data && (
            <div className="empty">
              <p className="empty-title">No servers</p>
              <p>
                {data.companions
                  ? "Add servers to .turnloom/mcp.json as shown below."
                  : 'The bundled document server needs the documents extra (pip install "turnloom[documents]"). You can also add your own servers as shown below.'}
              </p>
            </div>
          )
        )}
      </Block>
      <Block
        title="Add a server"
        note={
          <>
            Create <code>.turnloom/mcp.json</code> in this workspace. Add <code>"unwind": true</code> to route a server
            through Unwind. Treat the file as executable configuration: Studio starts each command in it.
          </>
        }
      >
        <pre className="output">{SAMPLE}</pre>
        <p className="block-note">
          What each call did is recorded in the{" "}
          <button type="button" className="link" onClick={() => onView("ledger")}>
            undo ledger
          </button>
          .
        </p>
      </Block>
    </Page>
  );
});
