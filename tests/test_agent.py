import io
import json
import os
import shutil
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from unittest.mock import patch

from turnloom import agent
from turnloom.agent import Config, api_request, fit_context, load_history, repair, run_agent, run_shell, shorten
from turnloom.compare import paired_difference
from turnloom.context import Observations
from turnloom.evaluate import bootstrap_interval, macro_score, score_command, validate_manifest


def reply(content=None, calls=(), usage=(10, 5), finish="stop"):
    message = {"role": "assistant", "content": content}
    if calls:
        message["tool_calls"] = [{"id": f"call-{index}", "type": "function",
                                  "function": {"name": name, "arguments": json.dumps(arguments)}}
                                 for index, (name, arguments) in enumerate(calls)]
    return {"choices": [{"message": message, "finish_reason": finish}],
            "usage": {"prompt_tokens": usage[0], "completion_tokens": usage[1]}}


class Scripted:
    """Stand in for the model endpoint and record what each request carried."""

    def __init__(self, *replies):
        self.replies = list(replies)
        self.sent = []

    def __call__(self, config, messages, deadline, allow_tools=True, on_text=None):
        self.sent.append({"messages": json.loads(json.dumps(messages)), "tools": allow_tools,
                          "max_tokens": config.max_reply_tokens})
        item = self.replies.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item(messages) if callable(item) else item


class LoopTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name).resolve()

    def config(self, **options):
        options.setdefault("auto_approve", True)
        return Config("https://example.test/v1", "test-model", "secret", self.root, **options)

    def run_with(self, model, task="Do the work", **options):
        history = options.pop("history", None)
        archive = options.pop("archive", None)
        config = options.pop("config", None) or self.config(**options)
        with patch("turnloom.agent.api_request", side_effect=model):
            return run_agent(task, config, history, archive)

    def test_agent_executes_tool_and_finishes(self):
        model = Scripted(reply(calls=[("shell", {"command": "echo ready"})], usage=(12, 7)), reply("Done", usage=(15, 3)))
        result = self.run_with(model)
        self.assertEqual((result.status, result.answer), ("completed", "Done"))
        self.assertEqual(result.usage["prompt_tokens"], 27)
        self.assertEqual(model.sent[1]["messages"][-1]["role"], "tool")
        self.assertIn("ready", model.sent[1]["messages"][-1]["content"])

    def test_commands_and_edits_are_declined_without_an_approver(self):
        model = Scripted(reply(calls=[("shell", {"command": "echo unsafe"}),
                                      ("write_file", {"path": "a.txt", "content": "x"})]), reply("Stopped"))
        self.run_with(model, auto_approve=False)
        results = [json.loads(item["content"]) for item in model.sent[1]["messages"] if item["role"] == "tool"]
        self.assertEqual([item["error"] for item in results], ["Operator declined the command", "Operator declined the edit"])
        self.assertFalse((self.root / "a.txt").exists())

    def test_file_tools_edit_record_and_report_changes(self):
        (self.root / "app.py").write_text("value = 1\n")
        trace = self.root / ".turnloom" / ("b" * 32 + ".jsonl")
        asked = []
        model = Scripted(reply(calls=[("read_file", {"path": "app.py"})]),
                         reply(calls=[("edit_file", {"path": "app.py", "old_string": "value = 1", "new_string": "value = 2"})]),
                         reply("Changed the value."))
        result = self.run_with(model, auto_approve=False, transcript=trace,
                               approve=lambda kind, detail: asked.append((kind, detail)) or True)
        self.assertEqual(result.files_changed, ["app.py"])
        self.assertEqual((self.root / "app.py").read_text(), "value = 2\n")
        self.assertEqual(asked[0][0], "edit")
        self.assertIn("+value = 2", asked[0][1])
        self.assertIn("1: value = 1", model.sent[1]["messages"][-1]["content"])
        kinds = [json.loads(line)["type"] for line in trace.read_text(encoding="utf-8").splitlines()]
        self.assertEqual(kinds, ["start", "assistant", "tool", "assistant", "edit", "tool", "assistant", "end"])
        ignored = (trace.parent / ".gitignore").read_text().splitlines()
        self.assertTrue({"*.jsonl", "*.db", ".gitignore"} <= set(ignored))
        self.assertNotIn("*", ignored)

    def test_read_only_sessions_offer_no_write_or_shell_tools(self):
        class Servers:
            def __init__(self):
                self.calls = []

            def schema(self):
                return [{"type": "function", "function": {"name": "mcp_call"}}]

            def dispatch(self, *arguments):
                self.calls.append(arguments)
                return {"ran": True}
        servers = Servers()
        names = [tool["function"]["name"] for tool in agent.tool_schemas(self.config(read_only=True, files=object(), mcp=servers))]
        self.assertEqual(names, ["list_files", "read_file", "search_files", "recall"])
        call = ("mcp_call", {"server": "s", "name": "t", "arguments": {}})
        model = Scripted(reply(calls=[call]), reply("Could not."))
        self.run_with(model, read_only=True, mcp=servers)
        self.assertIn("read-only", model.sent[1]["messages"][-1]["content"])
        self.assertEqual(servers.calls, [])
        refused = json.loads(run_shell('{"command": "echo hi"}', self.config(read_only=True), float("inf")))
        self.assertIn("read-only", refused["error"])

    def test_invalid_and_null_tool_arguments_are_returned_to_model(self):
        self.assertIn("error", json.loads(run_shell("{}", self.config(), float("inf"))))
        done = json.loads(run_shell('{"command": "echo ok", "timeout_seconds": null}', self.config(), float("inf")))
        self.assertEqual(done["exit_code"], 0)
        raw = reply(calls=[("shell", {})])
        raw["choices"][0]["message"]["tool_calls"][0]["function"]["arguments"] = {"command": "echo object"}
        del raw["choices"][0]["message"]["tool_calls"][0]["id"]
        model = Scripted(raw, reply("Done"))
        self.assertEqual(self.run_with(model).status, "completed")
        self.assertIn("object", model.sent[1]["messages"][-1]["content"])

    def test_timeout_ends_the_whole_process_tree(self):
        marker = self.root / "survivor.txt"
        script = self.root / "slow.py"
        script.write_text("import time, pathlib\ntime.sleep(4)\npathlib.Path('survivor.txt').write_text('alive')\n")
        started = time.monotonic()
        command = f'"{sys.executable}" slow.py'
        result = json.loads(run_shell(json.dumps({"command": command, "timeout_seconds": 1}), self.config(), float("inf")))
        self.assertIn("timed out", result["error"])
        self.assertLess(time.monotonic() - started, 3.5)
        time.sleep(4)
        self.assertFalse(marker.exists())
        self.assertFalse(score_command(command, self.root, 1))

    def test_stop_interrupts_a_running_command(self):
        flag = threading.Event()
        threading.Timer(0.5, flag.set).start()
        started = time.monotonic()
        command = f'"{sys.executable}" -c "import time; time.sleep(30)"'
        result = json.loads(run_shell(json.dumps({"command": command}), self.config(stop_requested=flag.is_set), float("inf")))
        self.assertEqual(result["error"], "Command stopped by the operator")
        self.assertLess(time.monotonic() - started, 10)

    @unittest.skipUnless(os.name == "nt", "cmd.exe behavior")
    def test_multiline_commands_are_refused_on_windows(self):
        result = json.loads(run_shell(json.dumps({"command": "echo one\necho two"}), self.config(), float("inf")))
        self.assertIn("one line", result["error"])

    def test_command_output_is_decoded_as_utf8_and_archived_in_full(self):
        (self.root / "emit.py").write_bytes(
            b"import sys\nsys.stdout.buffer.write(('caf\\u00e9 \\u2713 ' + 'x' * 30000 + ' END').encode('utf-8'))\n")
        archive = Observations()
        raw = run_shell(json.dumps({"command": f'"{sys.executable}" emit.py'}), self.config(output_chars=2000),
                        float("inf"), archive)
        result = json.loads(raw)
        self.assertTrue(result["stdout"].startswith("café ✓ "))
        self.assertIn("characters omitted", result["stdout"])
        full = json.loads(archive.recall(json.dumps({"reference": result["reference"], "offset": result["characters"] - 60, "length": 60})))
        self.assertIn(" END", full["text"])
        found = json.loads(archive.recall(json.dumps({"reference": result["reference"], "search": "café"})))
        self.assertEqual((found["count"], found["matches"][0]["offset"] > 0), (1, True))

    def test_child_processes_do_not_inherit_model_credentials(self):
        with patch.dict(os.environ, {"OPENROUTER_API_KEY": "sk-or-hidden", "UNRELATED": "sk-live-value", "KEEP_ME": "visible"}):
            config = Config("https://example.test/v1", "m", "sk-live-value", self.root, auto_approve=True)
            command = f'"{sys.executable}" -c "import os; print(sorted(k for k in os.environ if k in (\'OPENROUTER_API_KEY\', \'UNRELATED\', \'KEEP_ME\')))"'
            result = json.loads(run_shell(json.dumps({"command": command}), config, float("inf")))
        self.assertEqual(result["stdout"].strip(), "['KEEP_ME']")

    def test_three_identical_calls_in_a_row_end_tool_use_with_a_summary(self):
        same = ("shell", {"command": "echo again"})
        model = Scripted(reply(calls=[same]), reply(calls=[same]), reply(calls=[same]), reply("Summary of evidence."))
        result = self.run_with(model)
        self.assertEqual((result.status, result.answer), ("partial", "Summary of evidence."))
        self.assertFalse(model.sent[-1]["tools"])
        self.assertIn("three times", result.errors[-1])

    def test_repeating_a_check_between_edits_is_not_a_loop(self):
        test = ("shell", {"command": "echo test"})
        edits = [("write_file", {"path": "a.txt", "content": str(index)}) for index in range(3)]
        model = Scripted(reply(calls=[test]), reply(calls=[edits[0]]), reply(calls=[test]), reply(calls=[edits[1]]),
                         reply(calls=[test]), reply(calls=[edits[2]]), reply(calls=[test]), reply("All green."))
        self.assertEqual(self.run_with(model).status, "completed")

    def test_step_limit_requests_a_written_summary(self):
        calls = [reply(calls=[("shell", {"command": f"echo {index}"})]) for index in range(2)]
        model = Scripted(*calls, reply("Here is what I found."))
        result = self.run_with(model, max_steps=3)
        self.assertEqual((result.status, result.answer, result.steps), ("partial", "Here is what I found.", 3))
        self.assertFalse(model.sent[-1]["tools"])

    def test_limits_explain_themselves(self):
        result = self.run_with(Scripted(reply(calls=[("shell", {"command": "echo 1"})])), max_steps=1)
        self.assertEqual(result.status, "step_limit")
        self.assertIn("limit of 1", result.errors[-1])
        spent = self.run_with(Scripted(), budget_usd=0.0000001, input_rate=1, output_rate=4)
        self.assertEqual(spent.status, "budget_limit")
        self.assertIn("allowance", spent.errors[-1])

    def test_provider_errors_and_malformed_replies_are_explained(self):
        cases = [({"error": {"message": "Rate limit exceeded"}}, "Rate limit exceeded"),
                 ({"choices": []}, "no message"), ({"choices": [{"message": None}]}, "no message")]
        for body, expected in cases:
            with self.subTest(body=body):
                result = self.run_with(Scripted(body))
                self.assertEqual(result.status, "error")
                self.assertIn(expected, result.errors[-1])

    def test_empty_reply_is_retried_once_and_truncated_reply_is_kept(self):
        model = Scripted(reply(""), reply("Recovered."))
        self.assertEqual(self.run_with(model).answer, "Recovered.")
        self.assertEqual(self.run_with(Scripted(reply(""), reply(""))).status, "error")

    def test_empty_replies_never_enter_the_conversation_or_the_transcript(self):
        trace = self.root / ".turnloom" / ("e" * 32 + ".jsonl")
        history = []
        failed = self.run_with(Scripted(reply(None), reply(None)), task="first", history=history, transcript=trace)
        self.assertEqual(failed.status, "error")
        model = Scripted(reply(None), reply("recovered"))
        self.run_with(model, task="second", history=history, transcript=trace)
        sent = [(item["role"], item.get("content")) for item in model.sent[0]["messages"][1:]]
        self.assertEqual(sent, [("user", "first"), ("user", "second")])
        resumed = load_history(trace, "system")
        self.assertEqual([(item["role"], item.get("content")) for item in resumed[1:]],
                         [("user", "first"), ("user", "second"), ("assistant", "recovered")])
        self.assertTrue(all(item.get("content") or item.get("tool_calls") for item in resumed))
        cut = self.run_with(Scripted(reply("A long answer that stops", finish="length")))
        self.assertEqual((cut.status, cut.answer), ("partial", "A long answer that stops"))

    def test_missing_usage_is_estimated_instead_of_discarding_the_reply(self):
        body = reply("Paid answer")
        del body["usage"]
        result = self.run_with(Scripted(body), budget_usd=1, input_rate=1, output_rate=4)
        self.assertEqual((result.status, result.answer), ("completed", "Paid answer"))
        self.assertEqual(result.usage["estimated_calls"], 1)
        self.assertGreater(result.usage["prompt_tokens"], 0)

    def test_cached_tokens_are_reported(self):
        body = reply("ok")
        body["usage"]["prompt_tokens_details"] = {"cached_tokens": 6}
        self.assertEqual(self.run_with(Scripted(body)).usage["cached_tokens"], 6)

    def test_interrupted_turn_leaves_a_history_the_next_turn_can_use(self):
        history = []
        flag = threading.Event()
        calls = [("shell", {"command": "echo one"}), ("shell", {"command": "echo two"})]
        config = self.config(stop_requested=flag.is_set, on_tool_event=lambda *args: flag.set())
        first = self.run_with(Scripted(reply("Working on it", calls=calls)), config=config, history=history)
        self.assertEqual(first.status, "stopped")
        answered = {item["tool_call_id"] for item in history if item["role"] == "tool"}
        asked = {call["id"] for item in history for call in item.get("tool_calls") or []}
        self.assertEqual(asked, answered)
        flag.clear()
        model = Scripted(reply("Continuing."))
        self.assertEqual(self.run_with(model, config=self.config(), history=history).status, "completed")
        roles = [item["role"] for item in model.sent[0]["messages"]]
        self.assertEqual(roles, ["system", "user", "assistant", "tool", "user"])

    def test_keyboard_interrupt_ends_the_turn_and_closes_the_trace(self):
        trace = self.root / ".turnloom" / ("c" * 32 + ".jsonl")
        result = self.run_with(Scripted(KeyboardInterrupt()), transcript=trace)
        self.assertEqual(result.status, "stopped")
        self.assertEqual(json.loads(trace.read_text(encoding="utf-8").splitlines()[-1])["type"], "end")

    def test_repair_drops_unanswered_calls_only(self):
        messages = [{"role": "assistant", "content": None, "tool_calls": [{"id": "a"}, {"id": "b"}]},
                    {"role": "tool", "tool_call_id": "a", "content": "{}"},
                    {"role": "assistant", "content": None, "tool_calls": [{"id": "c"}]},
                    {"role": "assistant", "content": "note", "tool_calls": [{"id": "d"}]}]
        repair(messages)
        self.assertEqual([call["id"] for call in messages[0]["tool_calls"]], ["a"])
        self.assertEqual(messages[2], {"role": "assistant", "content": "note"})
        self.assertEqual(len(messages), 3)

    def test_repair_handles_reused_call_ids_and_empty_messages(self):
        messages = [{"role": "assistant", "content": None, "tool_calls": [{"id": "0"}]},
                    {"role": "tool", "tool_call_id": "0", "content": "{}"},
                    {"role": "assistant", "content": None},
                    {"role": "assistant", "content": None, "tool_calls": [{"id": "0"}]},
                    {"role": "user", "content": "next"}]
        repair(messages)
        self.assertEqual([item["role"] for item in messages], ["assistant", "tool", "user"])

    def test_saved_session_resumes_with_its_tool_results(self):
        trace = self.root / ".turnloom" / ("d" * 32 + ".jsonl")
        self.run_with(Scripted(reply(calls=[("shell", {"command": "echo first-run"})]), reply("First answer.")), transcript=trace)
        history = load_history(trace, "fresh system prompt")
        self.assertEqual([item["role"] for item in history], ["system", "user", "assistant", "tool", "assistant"])
        model = Scripted(reply("Second answer."))
        result = self.run_with(model, task="And then?", history=history, transcript=trace)
        self.assertEqual(result.status, "completed")
        sent = model.sent[0]["messages"]
        self.assertEqual(sent[0]["content"], "fresh system prompt")
        self.assertIn("first-run", sent[3]["content"])
        self.assertEqual(sent[-1], {"role": "user", "content": "And then?"})


class ContextTests(unittest.TestCase):
    def config(self, **options):
        return Config("https://example.test/v1", "m", "", Path(__file__).parent, **options)

    def exchange(self, ident, size):
        return [{"role": "assistant", "content": None, "tool_calls": [{"id": ident, "type": "function", "function": {
                    "name": "shell", "arguments": "{}"}}]},
                {"role": "tool", "tool_call_id": ident, "content": json.dumps({"stdout": ident * size})}]

    def test_overflow_archives_old_exchanges_and_lists_them_for_recall(self):
        archive = Observations()
        messages = [{"role": "system", "content": "Base prompt"}, {"role": "user", "content": "task"}]
        for ident in "abcde":
            messages += self.exchange(ident, 4000)
        fit_context(messages, self.config(context_tokens=5200, max_reply_tokens=1000), archive)
        kept = [item["tool_call_id"] for item in messages if item["role"] == "tool"]
        self.assertLess(len(kept), 5)
        self.assertEqual(kept[-1], "e")
        self.assertEqual(messages[1]["content"], "task")
        reference = archive.exchanges[0][0]
        self.assertIn(reference, messages[0]["content"])
        self.assertTrue(messages[0]["content"].startswith("Base prompt"))
        recalled = json.loads(archive.recall(json.dumps({"reference": reference, "length": 400})))
        self.assertIn("aaaa", recalled["text"])

    def test_oversized_newest_result_is_shrunk_instead_of_failing(self):
        archive = Observations()
        messages = [{"role": "system", "content": "s"}, {"role": "user", "content": "task"}] + self.exchange("z", 60000)
        fit_context(messages, self.config(context_tokens=6000, max_reply_tokens=1000), archive)
        folded = json.loads(messages[-1]["content"])
        self.assertEqual(folded["characters"], 60000 + len('{"stdout": ""}'))
        self.assertIn(folded["reference"], archive.records)
        with self.assertRaisesRegex(ValueError, "context window"):
            fit_context([{"role": "system", "content": "s"}, {"role": "user", "content": "x" * 40000}],
                        self.config(context_tokens=6000, max_reply_tokens=1000), archive)

    def test_results_of_the_latest_batch_are_never_folded_unread(self):
        archive = Observations()
        batch = {"role": "assistant", "content": None, "tool_calls": [{"id": str(index)} for index in range(5)]}
        results = [{"role": "tool", "tool_call_id": str(index), "content": "r" * 900} for index in range(5)]
        messages = [{"role": "user", "content": "go"}, batch, *results]
        self.assertEqual(archive.fold(messages), 0)
        messages.append({"role": "assistant", "content": None, "tool_calls": [{"id": "next"}]})
        messages.append({"role": "tool", "tool_call_id": "next", "content": "n" * 900})
        self.assertGreater(archive.fold(messages), 0)
        sizes = [len(item["content"]) for item in messages if item["role"] == "tool"]
        self.assertEqual(sizes[3:], [900, 900, 900])
        self.assertTrue(all(size < 650 for size in sizes[:3]))
        first = json.loads(next(item["content"] for item in messages if item["role"] == "tool"))
        self.assertEqual((first["ending"], first["characters"]), ("r" * 120, 900))
        self.assertIn("Call recall", first["note"])

    def test_refolding_keeps_the_reference_to_the_full_record(self):
        archive = Observations()
        full = "line\n" * 20000
        reference = archive.save(full)
        clipped = json.dumps({"reference": reference, "characters": len(full), "preview": full[:5000]})
        messages = [{"role": "tool", "tool_call_id": "big", "content": clipped}] + [
            {"role": "tool", "tool_call_id": str(index), "content": "recent"} for index in range(3)]
        archive.fold(messages)
        folded = json.loads(messages[0]["content"])
        self.assertEqual((folded["reference"], folded["characters"]), (reference, len(full)))

    def test_folding_non_ascii_output_shrinks_it_and_never_nests(self):
        archive = Observations()
        text = "变量 значение مقدار " * 60
        messages = [{"role": "tool", "tool_call_id": "u", "content": text}] + [
            {"role": "tool", "tool_call_id": str(index), "content": "recent"} for index in range(3)]
        removed = archive.fold(messages)
        folded = messages[0]["content"]
        self.assertGreater(removed, 0)
        self.assertLess(len(folded), len(text))
        self.assertIn("变量", folded)
        self.assertEqual(archive.fold(messages), 0)
        self.assertEqual(messages[0]["content"], folded)

    def test_archive_note_survives_a_new_system_prompt(self):
        archive = Observations()
        reference = archive.save("an archived exchange")
        archive.exchanges.append((reference, 20))
        prompt = agent.noted("New persona prompt", archive)
        self.assertIn(reference, prompt)
        self.assertEqual(agent.noted(prompt, archive), prompt)
        self.assertEqual(agent.noted(prompt, Observations()), "New persona prompt")

    def test_recall_reports_bad_arguments_precisely(self):
        archive = Observations()
        reference = archive.save("abcdef")
        self.assertEqual(json.loads(archive.recall(json.dumps({"reference": reference, "offset": None})))["text"], "abcdef")
        self.assertIn("integers", json.loads(archive.recall(json.dumps({"reference": reference, "offset": "x"})))["error"])
        self.assertIn("expired", json.loads(archive.recall('{"reference": "missing"}'))["error"])

    def test_archive_evicts_oldest_records_at_capacity(self):
        archive = Observations(capacity=1000)
        first = archive.save("a" * 600)
        second = archive.save("b" * 600)
        self.assertNotIn(first, archive.records)
        self.assertIn(second, archive.records)
        self.assertIsNone(archive.save("c" * 2000))


class Endpoint(BaseHTTPRequestHandler):
    script = []
    seen = []

    def log_message(self, *args):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        type(self).seen.append(body)
        status, headers, payload = type(self).script.pop(0)
        self.send_response(status)
        for key, value in headers.items():
            self.send_header(key, value)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


def sse(*chunks):
    return "".join(f"data: {json.dumps(chunk) if not isinstance(chunk, str) else chunk}\n\n" for chunk in chunks).encode()


class TransportTests(unittest.TestCase):
    def setUp(self):
        Endpoint.script, Endpoint.seen = [], []
        agent.STREAM_UNSUPPORTED.clear()
        self.addCleanup(agent.COMPLETION_TOKENS.intersection_update, set(agent.COMPLETION_TOKENS))
        self.server = HTTPServer(("127.0.0.1", 0), Endpoint)
        thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(thread.join)
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.config = Config(f"http://127.0.0.1:{self.server.server_port}/v1", "m", "secret", Path(__file__).parent)
        sleeper = patch("turnloom.agent.time.sleep")
        sleeper.start()
        self.addCleanup(sleeper.stop)

    def ask(self, **options):
        return api_request(self.config, [{"role": "user", "content": "hello"}], time.monotonic() + 30, **options)

    def test_request_shape(self):
        response = io.BytesIO(b'{"choices": [{"message": {"role": "assistant", "content": "ok"}}]}')
        response.headers = {}
        config = Config("https://example.test/v1", "test-model", "secret", Path(__file__).parent)
        with patch("urllib.request.urlopen", return_value=response) as urlopen:
            api_request(config, [{"role": "user", "content": "hello"}], time.monotonic() + 5)
        request = urlopen.call_args.args[0]
        payload = json.loads(request.data)
        self.assertEqual(request.full_url, "https://example.test/v1/chat/completions")
        self.assertEqual([tool["function"]["name"] for tool in payload["tools"]], ["shell", "recall"])
        self.assertEqual(request.get_header("Authorization"), "Bearer secret")
        self.assertNotIn("stream", payload)
        self.assertRegex(request.get_header("User-agent"), r"^Turnloom/\d+\.\d+")

    def test_stream_assembles_text_tool_calls_and_usage(self):
        call = {"index": 0, "id": "c1", "type": "function", "function": {"name": "shell", "arguments": ""}}
        Endpoint.script.append((200, {"Content-Type": "text/event-stream"}, sse(
            {"choices": [{"delta": {"role": "assistant", "content": "Look"}}]},
            {"choices": [{"delta": {"content": "ing."}}]},
            {"choices": [{"delta": {"tool_calls": [call]}}]},
            {"choices": [{"delta": {"tool_calls": [{"index": 0, "function": {"arguments": '{"command":'}}]}}]},
            {"choices": [{"delta": {"tool_calls": [{"index": 0, "function": {"arguments": ' "ls"}'}}]}}]},
            {"choices": [{"delta": {"tool_calls": [{"index": 1, "id": "c2", "function": {"name": "recall", "arguments": "{}"}}]}}]},
            {"choices": [{"delta": {}, "finish_reason": "tool_calls"}]},
            {"choices": [], "usage": {"prompt_tokens": 9, "completion_tokens": 4}}, "[DONE]")))
        pieces = []
        response = self.ask(on_text=pieces.append)
        message = response["choices"][0]["message"]
        self.assertEqual(pieces, ["Look", "ing."])
        self.assertEqual(message["content"], "Looking.")
        self.assertEqual([(c["id"], c["function"]["name"], c["function"]["arguments"]) for c in message["tool_calls"]],
                         [("c1", "shell", '{"command": "ls"}'), ("c2", "recall", "{}")])
        self.assertEqual(response["usage"], {"prompt_tokens": 9, "completion_tokens": 4})
        self.assertEqual(Endpoint.seen[0]["stream_options"], {"include_usage": True})

    def test_endpoint_without_streaming_falls_back_and_is_remembered(self):
        whole = json.dumps({"choices": [{"message": {"content": "plain"}}]}).encode()
        Endpoint.script += [(400, {}, b'{"error": "stream_options unsupported"}'), (200, {"Content-Type": "application/json"}, whole),
                            (200, {"Content-Type": "application/json"}, whole)]
        self.assertEqual(self.ask(on_text=lambda text: None)["choices"][0]["message"]["content"], "plain")
        self.ask(on_text=lambda text: None)
        self.assertEqual(["stream" in body for body in Endpoint.seen], [True, False, False])

    def test_a_request_that_fails_either_way_does_not_disable_streaming(self):
        Endpoint.script += [(400, {}, b'{"error": "context length exceeded"}'), (400, {}, b'{"error": "context length exceeded"}')]
        with self.assertRaisesRegex(RuntimeError, "context length exceeded"):
            self.ask(on_text=lambda text: None)
        self.assertEqual(agent.STREAM_UNSUPPORTED, set())

    def test_retries_honor_retry_after_and_give_up_on_client_errors(self):
        ok = json.dumps({"choices": [{"message": {"content": "ok"}}]}).encode()
        Endpoint.script += [(429, {"Retry-After": "7"}, b"slow down"), (503, {}, b"busy"), (200, {}, ok)]
        with patch("turnloom.agent.time.monotonic", side_effect=time.monotonic) as clock:
            self.assertEqual(self.ask()["choices"][0]["message"]["content"], "ok")
            self.assertTrue(clock.called)
        self.assertEqual(len(Endpoint.seen), 3)
        Endpoint.script.append((401, {}, b'{"error": "bad key"}'))
        with self.assertRaisesRegex(RuntimeError, "Model HTTP 401.*bad key"):
            self.ask()
        self.assertEqual(len(Endpoint.seen), 4)

    def test_token_parameter_is_swapped_when_the_endpoint_asks(self):
        ok = json.dumps({"choices": [{"message": {"content": "ok"}}]}).encode()
        Endpoint.script += [(400, {}, b"Unsupported parameter: 'max_tokens'. Use 'max_completion_tokens' instead."), (200, {}, ok)]
        self.ask()
        self.assertIn("max_completion_tokens", Endpoint.seen[1])
        self.assertNotIn("max_tokens", Endpoint.seen[1])
        # The endpoint's answer is remembered: the next request asks the right way the first time.
        Endpoint.script.append((200, {}, ok))
        self.ask()
        self.assertEqual(len(Endpoint.seen), 3)
        self.assertIn("max_completion_tokens", Endpoint.seen[2])
        self.assertNotIn("max_tokens", Endpoint.seen[2])

    def test_non_json_reply_is_reported_plainly(self):
        Endpoint.script.append((200, {"Content-Type": "text/html"}, b"<html>login</html>"))
        with self.assertRaisesRegex(RuntimeError, "not JSON"):
            self.ask()

    def test_dropped_connection_is_retried_and_slow_reply_only_once(self):
        ok = io.BytesIO(json.dumps({"choices": [{"message": {"content": "ok"}}]}).encode())
        ok.headers = {}
        with patch("urllib.request.urlopen", side_effect=[ConnectionResetError("reset"), ok]) as urlopen:
            self.ask()
            self.assertEqual(urlopen.call_count, 2)
        with patch("urllib.request.urlopen", side_effect=TimeoutError("timed out")) as urlopen:
            with self.assertRaisesRegex(RuntimeError, "transport failure"):
                self.ask()
            self.assertEqual(urlopen.call_count, 2)

    def test_interrupted_stream_is_not_replayed_after_text_was_shown(self):
        partial = sse({"choices": [{"delta": {"content": "Half an ans"}}]})
        Endpoint.script.append((200, {"Content-Type": "text/event-stream", "X-Truncate": "1"}, partial))

        def broken(request, timeout=None):
            class Stream(io.BytesIO):
                headers = {"Content-Type": "text/event-stream"}

                def __iter__(self):
                    yield from partial.splitlines(keepends=True)
                    raise ConnectionResetError("connection lost")
            return Stream()
        shown = []
        with patch("urllib.request.urlopen", side_effect=broken) as urlopen:
            with self.assertRaisesRegex(RuntimeError, "transport failure"):
                self.ask(on_text=shown.append)
            self.assertEqual(urlopen.call_count, 1)
        self.assertEqual(shown, ["Half an ans"])


class ScoringTests(unittest.TestCase):
    def test_shipped_example_task_fails_until_fixed(self):
        examples = Path(__file__).parent.parent / "examples"
        cases = json.loads((examples / "manifest.json").read_text(encoding="utf-8"))
        validate_manifest(cases, examples)
        with tempfile.TemporaryDirectory() as directory:
            copy = Path(directory) / "task"
            shutil.copytree(cases[0]["workspace"], copy)
            self.assertFalse(score_command(cases[0]["check"], copy, 60))
            source = (copy / "slugify.py").read_text(encoding="utf-8")
            (copy / "slugify.py").write_text(source.replace('", title)', '", title.lower())'), encoding="utf-8")
            self.assertTrue(score_command(cases[0]["check"], copy, 60))

    def test_output_keeps_head_and_tail(self):
        output = shorten("a" * 50 + "z" * 50, 20)
        self.assertTrue(output.startswith("a" * 10))
        self.assertTrue(output.endswith("z" * 10))
        self.assertIn("omitted", output)

    def test_macro_weights_benchmarks_equally(self):
        rows = [
            {"benchmark": "one", "task_id": "a", "passed": True},
            {"benchmark": "two", "task_id": "b", "passed": False},
            {"benchmark": "two", "task_id": "c", "passed": False},
        ]
        self.assertEqual(macro_score(rows), 0.5)
        self.assertEqual(bootstrap_interval(rows, samples=100), (0.5, 0.5))

    def test_interval_reflects_variation_between_attempts(self):
        rows = [{"benchmark": "one", "task_id": "a", "passed": passed} for passed in (True, False, False)]
        lower, upper = bootstrap_interval(rows, samples=400)
        self.assertLess(lower, upper)
        self.assertLessEqual(lower, macro_score(rows))
        self.assertGreaterEqual(upper, macro_score(rows))

    def test_paired_difference_keeps_benchmark_weights(self):
        baseline = {"one": {"a": [0, 0, 0]}, "two": {"b": [1, 1, 1], "c": [1, 1, 1]}}
        candidate = {"one": {"a": [1, 1, 1]}, "two": {"b": [1, 1, 1], "c": [1, 1, 1]}}
        report = paired_difference(baseline, candidate, samples=100)
        self.assertEqual(report["macro_difference"], 0.5)
        self.assertEqual(report["ci_95"], [0.5, 0.5])
        self.assertEqual(report["tasks"], 3)
        with self.assertRaisesRegex(ValueError, "no trials"):
            paired_difference({}, {})


if __name__ == "__main__":
    unittest.main()
