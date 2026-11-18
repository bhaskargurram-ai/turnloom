"""Drive `turnloom.acp` the way an editor does: a real agent process on stdio, the SDK's own client on the other end."""

import asyncio
import importlib.util
import json
import re
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from turnloom import __version__

SDK = importlib.util.find_spec("acp") is not None
if SDK:
    import acp
    from acp import schema

ROOT = Path(__file__).resolve().parent.parent
AGENT = (sys.executable, "-c", "from tests.test_acp import play; play()")
SLEEP = f'"{sys.executable}" -c "import time; time.sleep(60)"'
EDIT = ("edit_file", {"path": "app.py", "old_string": "value = 1", "new_string": "value = 2"})
PLANS = {
    "Hello": [("Hello there.", [])],
    "Rename": [("Reading the file. ", [("read_file", {"path": "app.py"})]), ("", [EDIT]), ("Changed the value.", [])],
    "Twice": [("", [EDIT]), ("", [("write_file", {"path": "new.txt", "content": "made\n"})]), ("Both done.", [])],
    "Wait": [("", [("shell", {"command": SLEEP})]), ("Finished waiting.", [])],
    "Again": [("", [("shell", {"command": "echo once"})]), ("", [("shell", {"command": "echo once"})]), ("Ran it twice.", [])],
    "Tools": [("", [("mcp_search", {"server": "receipts", "query": ""})]), ("Listed the tools.", [])],
    "Echo": [lambda messages: (messages[-1]["content"], [])],
    "Count": [lambda messages: (f"{sum(item['role'] == 'user' for item in messages)} user messages", [])],
}


def model(config, messages, deadline, allow_tools=True, on_text=None):
    """The scripted model inside the agent process: the task's first word names a plan, each tool result advances it."""
    asked = max(index for index, item in enumerate(messages) if item["role"] == "user")
    step = PLANS[messages[asked]["content"].split()[0]][sum(item["role"] == "tool" for item in messages[asked:])]
    content, calls = step(messages) if callable(step) else step
    for piece in re.findall(r"\S+\s*", content):
        on_text(piece)
    message = {"role": "assistant", "content": content or None}
    if calls:
        message["tool_calls"] = [{"id": f"call-{index}", "type": "function", "function": {
            "name": name, "arguments": json.dumps(arguments)}} for index, (name, arguments) in enumerate(calls)]
    return {"choices": [{"message": message, "finish_reason": "stop"}], "usage": {"prompt_tokens": 10, "completion_tokens": 5}}


def play():
    """Entry point of the agent process the tests launch: the real server, with only the model request replaced."""
    from turnloom.acp import serve
    with patch("turnloom.agent.api_request", side_effect=model):
        raise SystemExit(serve(Path(sys.argv[1])))


class Editor:
    """A small ACP client. It records what the agent sends, in order, and answers permission requests from a list."""

    def __init__(self, answers):
        self.answers = list(answers)
        self.log, self.frames = [], []
        self.changed = asyncio.Event()

    def note(self, kind, item):
        self.log.append((kind, item))
        self.changed.set()

    def frame(self, event):
        if event.direction.value == "incoming":
            self.frames.append(event.message)

    async def session_update(self, session_id, update, **_):
        self.note(update.session_update, update)

    async def request_permission(self, session_id, tool_call, options, **_):
        self.note("permission", (tool_call, options))
        answer = self.answers.pop(0)
        if answer == "never":
            await asyncio.Event().wait()
        outcome = schema.AllowedOutcome(option_id=answer) if answer else schema.DeniedOutcome()
        return schema.RequestPermissionResponse(outcome=outcome)

    async def saw(self, kind, status):
        """Wait until the agent has reported an update of this kind and status."""
        while not any(key == kind and item.status == status for key, item in self.log):
            self.changed.clear()
            await self.changed.wait()

    def of(self, kind):
        return [item for key, item in self.log if key == kind]

    def text(self, kind="agent_message_chunk"):
        return "".join(item.content.text for item in self.of(kind))

    def trace(self):
        """The order of events: runs of message chunks collapsed, tool reports named by kind and status."""
        rows = []
        for key, item in self.log:
            row = " ".join(filter(None, (key, getattr(item, "kind", None), getattr(item, "status", None))))
            if row != "agent_message_chunk" or rows[-1:] != [row]:
                rows.append(row)
        return rows


def words(text):
    return [schema.TextContentBlock(text=text)]


@unittest.skipUnless(SDK, "Install the acp extra")
class AcpTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name).resolve()
        (self.root / "app.py").write_bytes(b"value = 1\n")
        self.settings = {"TURNLOOM_ENDPOINT": "http://127.0.0.1:9/v1", "TURNLOOM_MODEL": "scripted",
                         "TURNLOOM_CONFIG_DIR": str(self.root / "config")}

    async def connect(self, *answers):
        """Launch the agent process and return an initialized connection to it with the client that listens."""
        editor = Editor(answers)
        self.conn, self.process = await self.enterAsyncContext(acp.spawn_agent_process(
            editor, *AGENT, str(self.root), env=self.settings, cwd=ROOT, observers=[editor.frame],
            transport_kwargs={"stderr": None, "shutdown_timeout": 10}))
        self.hello = await self.soon(self.conn.initialize(protocol_version=acp.PROTOCOL_VERSION))
        return self.conn, editor

    async def soon(self, awaitable, seconds=40):
        return await asyncio.wait_for(awaitable, seconds)

    async def session(self, conn, servers=()):
        return (await self.soon(conn.new_session(cwd=str(self.root), mcp_servers=list(servers)))).session_id

    async def ask(self, conn, session_id, task):
        return (await self.soon(conn.prompt(session_id=session_id, prompt=words(task)))).stop_reason

    async def test_initialize_reports_only_what_is_implemented(self):
        await self.connect()
        self.assertEqual(self.hello.protocol_version, 1)
        self.assertEqual((self.hello.agent_info.name, self.hello.agent_info.version), ("Turnloom", __version__))
        offered = self.hello.model_dump(mode="json", by_alias=True, exclude_unset=True)
        self.assertEqual(offered["agentCapabilities"], {
            "loadSession": True, "mcpCapabilities": {"http": False, "sse": False},
            "promptCapabilities": {"image": False, "audio": False, "embeddedContext": True}})
        self.assertEqual(offered["authMethods"], [])

    async def test_new_session_starts_in_ask_mode_and_checks_the_directory(self):
        conn, _ = await self.connect()
        created = await self.soon(conn.new_session(cwd=str(self.root), mcp_servers=[]))
        self.assertRegex(created.session_id, "^[a-f0-9]{32}$")
        self.assertEqual(created.modes.current_mode_id, "ask")
        self.assertEqual([mode.id for mode in created.modes.available_modes], ["ask", "auto-edit", "read-only"])
        with self.assertRaises(acp.RequestError) as refused:
            await self.soon(conn.new_session(cwd=str(self.root / "absent"), mcp_servers=[]))
        self.assertEqual(refused.exception.code, -32602)

    async def test_turn_streams_text_reads_a_file_and_applies_an_approved_edit(self):
        conn, editor = await self.connect("allow")
        session_id = await self.session(conn)
        self.assertEqual(await self.ask(conn, session_id, "Rename the value"), "end_turn")
        self.assertEqual(editor.trace(), [
            "agent_message_chunk", "tool_call read in_progress", "tool_call_update completed", "tool_call edit pending",
            "permission", "tool_call_update in_progress", "tool_call_update completed", "agent_message_chunk"])
        self.assertEqual(editor.text(), "Reading the file. Changed the value.")
        self.assertGreater(len(editor.of("agent_message_chunk")), 4)
        read, proposed = editor.of("tool_call")
        self.assertEqual((read.title, read.name, Path(read.locations[0].path)), ("Read app.py", "read_file", self.root / "app.py"))
        self.assertIn("1: value = 1", editor.of("tool_call_update")[0].content[0].content.text)
        (request, options), = editor.of("permission")
        self.assertEqual((request.tool_call_id, request.title, request.kind), (proposed.tool_call_id, "Edit app.py", "edit"))
        self.assertEqual([(option.option_id, option.kind) for option in options],
                         [("allow", "allow_once"), ("always", "allow_always"), ("reject", "reject_once")])
        applied = editor.of("tool_call_update")[-1]
        for change in (proposed.content[0], request.content[0], applied.content[0]):
            self.assertEqual((change.type, Path(change.path), change.old_text, change.new_text),
                             ("diff", self.root / "app.py", "value = 1\n", "value = 2\n"))
        self.assertEqual((self.root / "app.py").read_bytes(), b"value = 2\n")
        self.assertTrue((self.root / ".turnloom" / (session_id + ".jsonl")).is_file())

    async def test_rejected_edit_leaves_the_file_and_the_turn_continues(self):
        conn, editor = await self.connect("reject")
        self.assertEqual(await self.ask(conn, await self.session(conn), "Rename the value"), "end_turn")
        refused = editor.of("tool_call_update")[-1]
        self.assertEqual(refused.status, "failed")
        self.assertIn("declined the edit", refused.content[0].content.text)
        self.assertEqual((self.root / "app.py").read_bytes(), b"value = 1\n")
        self.assertTrue(editor.text().endswith("Changed the value."))

    async def test_cancelled_permission_outcome_counts_as_a_rejection(self):
        conn, editor = await self.connect(None)
        self.assertEqual(await self.ask(conn, await self.session(conn), "Rename the value"), "end_turn")
        self.assertEqual(editor.of("tool_call_update")[-1].status, "failed")
        self.assertEqual((self.root / "app.py").read_bytes(), b"value = 1\n")

    async def test_always_allowing_edits_switches_the_session_to_auto_edit(self):
        conn, editor = await self.connect("always")
        session_id = await self.session(conn)
        self.assertEqual(await self.ask(conn, session_id, "Twice edit"), "end_turn")
        self.assertEqual(len(editor.of("permission")), 1)
        self.assertEqual([update.current_mode_id for update in editor.of("current_mode_update")], ["auto-edit"])
        self.assertEqual((self.root / "new.txt").read_bytes(), b"made\n")
        created = editor.of("tool_call_update")[-1].content[0]
        self.assertEqual((created.old_text, created.new_text), (None, "made\n"))

    async def test_always_allowing_a_command_covers_that_exact_command_only(self):
        conn, editor = await self.connect("always", "reject")
        session_id = await self.session(conn)
        self.assertEqual(await self.ask(conn, session_id, "Again please"), "end_turn")
        self.assertEqual(len(editor.of("permission")), 1)
        self.assertEqual([update.status for update in editor.of("tool_call_update")], ["in_progress", "completed"] * 2)
        self.assertIn("once\nexit code 0", editor.of("tool_call_update")[-1].content[0].content.text)
        self.assertEqual(await self.ask(conn, session_id, "Wait for it"), "end_turn")
        self.assertEqual((len(editor.of("permission")), editor.of("tool_call_update")[-1].status), (2, "failed"))

    async def test_read_only_mode_refuses_edits_without_asking(self):
        conn, editor = await self.connect()
        session_id = await self.session(conn)
        await self.soon(conn.set_session_mode(session_id=session_id, mode_id="read-only"))
        self.assertEqual(await self.ask(conn, session_id, "Rename the value"), "end_turn")
        self.assertEqual(editor.of("permission"), [])
        self.assertIn("read-only", editor.of("tool_call_update")[-1].content[0].content.text)
        self.assertEqual((self.root / "app.py").read_bytes(), b"value = 1\n")
        with self.assertRaises(acp.RequestError) as refused:
            await self.soon(conn.set_session_mode(session_id=session_id, mode_id="yolo"))
        self.assertEqual(refused.exception.code, -32602)

    async def test_cancel_stops_a_running_command_and_ends_the_turn_as_cancelled(self):
        conn, editor = await self.connect("allow")
        session_id = await self.session(conn)
        turn = asyncio.ensure_future(conn.prompt(session_id=session_id, prompt=words("Wait for it")))
        await self.soon(editor.saw("tool_call_update", "in_progress"))
        await conn.cancel(session_id=session_id)
        self.assertEqual((await self.soon(turn, 20)).stop_reason, "cancelled")
        stopped = editor.of("tool_call_update")[-1]
        self.assertEqual(stopped.status, "failed")
        self.assertIn("stopped by the operator", stopped.content[0].content.text)
        self.assertNotIn("Finished waiting.", editor.text())
        self.assertEqual(await self.ask(conn, session_id, "Hello again"), "end_turn")

    async def test_withdrawn_prompt_request_is_still_answered_as_a_cancelled_turn(self):
        conn, editor = await self.connect("allow")
        session_id = await self.session(conn)
        turn = asyncio.ensure_future(conn.prompt(session_id=session_id, prompt=words("Wait for it")))
        await self.soon(editor.saw("tool_call_update", "in_progress"))
        turn.cancel()  # The SDK client withdraws the request with $/cancel_request.
        await self.soon(editor.saw("tool_call_update", "failed"))
        self.assertEqual(await self.ask(conn, session_id, "Hello again"), "end_turn")
        self.assertIn({"stopReason": "cancelled"}, [frame.get("result") for frame in editor.frames])

    async def test_cancel_withdraws_an_unanswered_permission_request(self):
        conn, editor = await self.connect("never")
        session_id = await self.session(conn)
        turn = asyncio.ensure_future(conn.prompt(session_id=session_id, prompt=words("Rename the value")))
        await self.soon(editor.saw("tool_call", "pending"))
        while not editor.of("permission"):
            await asyncio.sleep(0.01)
        await conn.cancel(session_id=session_id)
        self.assertEqual((await self.soon(turn, 20)).stop_reason, "cancelled")
        self.assertEqual((self.root / "app.py").read_bytes(), b"value = 1\n")

    async def test_disconnect_during_a_command_ends_the_agent_process(self):
        conn, editor = await self.connect("allow")
        turn = asyncio.ensure_future(conn.prompt(session_id=await self.session(conn), prompt=words("Wait for it")))
        await self.soon(editor.saw("tool_call_update", "in_progress"))
        self.process.stdin.close()
        self.assertEqual(await self.soon(self.process.wait(), 8), 0)
        with self.assertRaises(ConnectionError):
            await self.soon(turn)

    async def test_unknown_session_is_reported_as_a_missing_resource(self):
        conn, _ = await self.connect()
        missing = "0" * 32
        for request in (conn.prompt(session_id=missing, prompt=words("Hello")), conn.set_session_mode(session_id=missing, mode_id="ask"),
                        conn.load_session(cwd=str(self.root), session_id=missing, mcp_servers=[])):
            with self.assertRaises(acp.RequestError) as refused:
                await self.soon(request)
            self.assertEqual((refused.exception.code, refused.exception.data), (-32002, {"sessionId": missing}))
        await conn.cancel(session_id=missing)
        self.assertEqual(await self.ask(conn, await self.session(conn), "Hello"), "end_turn")

    async def test_prompt_without_a_connected_model_tells_the_user_to_connect(self):
        del self.settings["TURNLOOM_ENDPOINT"], self.settings["TURNLOOM_MODEL"]
        conn, editor = await self.connect()
        self.assertEqual(await self.ask(conn, await self.session(conn), "Hello"), "end_turn")
        self.assertIn("Run `turnloom connect` in a terminal", editor.text())
        self.assertNotIn("Hello there.", editor.text())

    async def test_prompt_resources_reach_the_model_as_delimited_text(self):
        conn, editor = await self.connect()
        session_id = await self.session(conn)
        uri = (self.root / "app.py").as_uri()
        prompt = [*words("Echo this"), schema.ResourceContentBlock(name="app.py", uri=uri),
                  schema.EmbeddedResourceContentBlock(resource=schema.TextResourceContents(uri="untitled:notes", text="remember"))]
        self.assertEqual((await self.soon(conn.prompt(session_id=session_id, prompt=prompt))).stop_reason, "end_turn")
        self.assertEqual(editor.text(), 'Echo this [app.py](app.py) untitled:notes\n\n'
                                        '<attachment source="untitled:notes">\nremember\n</attachment>')
        image = schema.ImageContentBlock(data="AAAA", mime_type="image/png")
        with self.assertRaises(acp.RequestError) as refused:
            await self.soon(conn.prompt(session_id=session_id, prompt=[image]))
        self.assertEqual(refused.exception.code, -32602)

    async def test_saved_session_loads_with_its_history_replayed_and_continues(self):
        conn, _ = await self.connect("allow")
        session_id = await self.session(conn)
        await self.ask(conn, session_id, "Rename the value")
        await self.conn.close()
        conn, editor = await self.connect()
        loaded = await self.soon(conn.load_session(cwd=str(self.root), session_id=session_id, mcp_servers=[]))
        self.assertEqual(loaded.modes.current_mode_id, "ask")
        self.assertEqual(editor.text("user_message_chunk"), "Rename the value")
        self.assertEqual(editor.text(), "Reading the file. Changed the value.")
        read, edit = editor.of("tool_call")
        self.assertEqual((read.kind, read.status, edit.kind, edit.status), ("read", "completed", "edit", "completed"))
        self.assertEqual((edit.content[0].old_text, edit.content[0].new_text), ("value = 1\n", "value = 2\n"))
        self.assertEqual(await self.ask(conn, session_id, "Count the turns"), "end_turn")
        self.assertTrue(editor.text().endswith("2 user messages"))

    async def test_editor_mcp_servers_are_connected_or_reported_never_dropped(self):
        launch = f"import sys; sys.path.insert(0, {str(ROOT)!r}); from turnloom.cli import main; main(['serve', '--workspace', {str(self.root)!r}])"
        servers = [schema.McpServerStdio(name="receipts", command=sys.executable, args=["-c", launch],
                                         env=[schema.EnvVariable(name="TOKEN", value="not-shown")]),
                   schema.HttpMcpServer(name="keyed", url="https://mcp.example.test/mcp", headers=[])]
        conn, editor = await self.connect()
        session_id = await self.session(conn, servers=servers)
        self.assertEqual(await self.ask(conn, session_id, "Tools please"), "end_turn")
        self.assertRegex(editor.text(), "did not connect these MCP servers.*\n- keyed: ")
        self.assertNotIn("not-shown", json.dumps([str(item) for _, item in editor.log]))
        if importlib.util.find_spec("mcp"):
            listed = editor.of("tool_call_update")[-1]
            self.assertEqual(listed.status, "completed", listed.content)
            self.assertIn("receipt", listed.content[0].content.text)
        self.assertEqual(await self.ask(conn, await self.session(conn, servers=servers[1:]), "Hello"), "end_turn")
        self.assertEqual(editor.text().count("did not connect"), 1)


class StdioTests(unittest.TestCase):
    def test_missing_sdk_is_reported_with_the_install_command(self):
        from turnloom.acp import serve
        with patch.dict(sys.modules, {"acp": None}), self.assertRaisesRegex(ValueError, r"pip install 'turnloom\[acp\]'"):
            serve(ROOT)

    def exchange(self, command, root):
        """Speak raw JSON-RPC to an agent process: one line out, lines back until the answer. Returns every line read."""
        settings = {"TURNLOOM_CONFIG_DIR": str(root / "config"), "TURNLOOM_ENDPOINT": "", "TURNLOOM_MODEL": "",
                    "HARNESS_ENDPOINT": "", "HARNESS_MODEL": ""}
        process = subprocess.Popen(command, cwd=ROOT, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                   env={**acp.default_environment(), **settings})
        watchdog = threading.Timer(60, process.kill)
        watchdog.start()
        self.addCleanup(watchdog.cancel)
        self.addCleanup(process.kill)
        lines, answers = [], {}
        requests = [("initialize", {"protocolVersion": 1}), ("session/new", {"cwd": str(root), "mcpServers": []}),
                    ("session/prompt", lambda: {"sessionId": answers[1]["sessionId"], "prompt": [{"type": "text", "text": "Hello"}]})]
        for number, (method, params) in enumerate(requests):
            params = params() if callable(params) else params
            process.stdin.write(json.dumps({"jsonrpc": "2.0", "id": number, "method": method, "params": params}).encode() + b"\n")
            process.stdin.flush()
            while number not in answers:
                line = process.stdout.readline()
                self.assertTrue(line, "the agent closed stdout before answering")
                lines.append(line)
                message = json.loads(line)
                if "result" in message or "error" in message:
                    answers[message["id"]] = message.get("result")
        process.stdin.close()
        lines += process.stdout.readlines()
        self.assertEqual(process.wait(timeout=15), 0)
        process.stdout.close()
        return lines, answers

    def check(self, command):
        with tempfile.TemporaryDirectory() as folder:
            lines, answers = self.exchange(command, Path(folder).resolve())
        self.assertEqual(answers[0]["agentInfo"], {"name": "Turnloom", "version": __version__})
        self.assertEqual(answers[2], {"stopReason": "end_turn"})
        messages = [json.loads(line) for line in lines]
        self.assertTrue(all(line.endswith(b"\n") and b"\r" not in line and message["jsonrpc"] == "2.0"
                            for line, message in zip(lines, messages, strict=True)))
        shown = "".join(message["params"]["update"]["content"]["text"] for message in messages if message.get("method") == "session/update")
        self.assertIn("turnloom connect", shown)

    @unittest.skipUnless(SDK, "Install the acp extra")
    def test_stdout_carries_only_protocol_frames_and_closing_stdin_ends_the_process(self):
        with tempfile.TemporaryDirectory() as folder:
            self.check([*AGENT, folder])

    @unittest.skipUnless(SDK, "Install the acp extra")
    def test_cli_entry_point_serves_the_protocol(self):
        with tempfile.TemporaryDirectory() as folder:
            command = [sys.executable, "-m", "turnloom", "acp", "--workspace", folder, "--persona", "reviewer"]
            if subprocess.run([*command, "--help"], cwd=ROOT, capture_output=True).returncode:
                self.skipTest("`turnloom acp` is not wired into the command line yet")
            self.check(command)


if __name__ == "__main__":
    unittest.main()
