import contextlib
import io
import json
import os
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from unittest.mock import patch

from turnloom import agent, responses
from turnloom.agent import Config, api_request, fit_context, load_history, reply_message, run_agent
from turnloom.cli import main
from turnloom.context import Observations
from turnloom.responses import ITEMS


def reasoning(ident):
    return {"type": "reasoning", "id": ident, "summary": [], "content": [], "encrypted_content": "sealed-" + ident}


def call(ident, name="recall", arguments='{"reference": "none"}'):
    return {"type": "function_call", "id": "fc_" + ident, "call_id": ident, "name": name, "arguments": arguments, "status": "completed"}


def said(text, **extra):
    return {"type": "message", "id": "msg_1", "role": "assistant", "status": "completed",
            "content": [{"type": "output_text", "text": text, "annotations": []}], **extra}


def response(*output, usage=(100, 20), **extra):
    return {"id": "resp_1", "object": "response", "status": "completed", "error": None, "output": list(output),
            "usage": {"input_tokens": usage[0], "output_tokens": usage[1], "total_tokens": sum(usage),
                      "input_tokens_details": {"cached_tokens": 64}, "output_tokens_details": {"reasoning_tokens": 12}}, **extra}


def exchange(ident, size=10):
    """One model call in the stored conversation: reasoning, a tool call and its result."""
    message, _ = reply_message(responses.reply(response(reasoning("rs_" + ident), call(ident))))
    return [message, {"role": "tool", "tool_call_id": ident, "content": json.dumps({"stdout": ident * size})}]


class Endpoint(BaseHTTPRequestHandler):
    script = []
    seen = []

    def log_message(self, *args):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        type(self).seen.append((self.path, body))
        status, headers, payload = type(self).script.pop(0)
        payload = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
        self.send_response(status)
        for key, value in headers.items():
            self.send_header(key, value)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


def sse(*events):
    return "".join(f"event: {event['type']}\ndata: {json.dumps(event)}\n\n" for event in events).encode()


class ConversionTests(unittest.TestCase):
    def test_conversation_becomes_input_items_in_the_order_the_model_produced_them(self):
        first, _ = reply_message(responses.reply(response(reasoning("rs_1"), said("Looking.", phase="commentary"), call("a"), call("b"))))
        messages = [{"role": "system", "content": "Rules"}, {"role": "user", "content": "Fix it"}, first,
                    {"role": "tool", "tool_call_id": "a", "content": '{"stdout": "one"}'},
                    {"role": "tool", "tool_call_id": "b", "content": '{"stdout": "two"}'},
                    {"role": "assistant", "content": "Done."}]
        self.assertEqual(responses.items(messages), [
            {"role": "developer", "content": "Rules"},
            {"role": "user", "content": "Fix it"},
            reasoning("rs_1"),
            {"role": "assistant", "content": "Looking.", "phase": "commentary"},
            {"type": "function_call", "call_id": "a", "name": "recall", "arguments": '{"reference": "none"}'},
            {"type": "function_call", "call_id": "b", "name": "recall", "arguments": '{"reference": "none"}'},
            {"type": "function_call_output", "call_id": "a", "output": '{"stdout": "one"}'},
            {"type": "function_call_output", "call_id": "b", "output": '{"stdout": "two"}'},
            {"role": "assistant", "content": "Done."}])

    def test_a_message_from_the_chat_transport_is_sent_as_text_then_calls(self):
        messages = [{"role": "user", "content": "go"},
                    {"role": "assistant", "content": "Checking.", "tool_calls": [
                        {"id": "c1", "type": "function", "function": {"name": "shell", "arguments": '{"command": "ls"}'}}]},
                    {"role": "tool", "tool_call_id": "c1", "content": "files"}]
        self.assertEqual(responses.items(messages)[1:], [
            {"role": "assistant", "content": "Checking."},
            {"type": "function_call", "call_id": "c1", "name": "shell", "arguments": '{"command": "ls"}'},
            {"type": "function_call_output", "call_id": "c1", "output": "files"}])

    def test_request_carries_tools_limits_and_the_stateless_settings(self):
        body = responses.request("m", [{"role": "user", "content": "hi"}], [agent.SHELL], 4096, "medium")
        self.assertEqual((body["store"], body["include"], body["max_output_tokens"], body["reasoning"]),
                         (False, ["reasoning.encrypted_content"], 4096, {"effort": "medium"}))
        tool = body["tools"][0]
        self.assertEqual((tool["type"], tool["name"], tool["strict"]), ("function", "shell", False))
        self.assertEqual(tool["parameters"], agent.SHELL["function"]["parameters"])
        self.assertNotIn("function", tool)
        bare = responses.request("m", [{"role": "user", "content": "hi"}], [], 4096, None)
        self.assertFalse({"tools", "tool_choice", "reasoning"} & set(bare))

    def test_reply_becomes_a_chat_message_that_keeps_its_reasoning(self):
        reply = responses.reply(response(reasoning("rs_1"), said("Looking."), call("a"), call("b", "shell", '{"command": "ls"}')))
        message, finish = reply_message(reply)
        self.assertEqual((message["content"], finish), ("Looking.", "tool_calls"))
        self.assertEqual([(item["id"], item["function"]["name"], item["function"]["arguments"]) for item in message["tool_calls"]],
                         [("a", "recall", '{"reference": "none"}'), ("b", "shell", '{"command": "ls"}')])
        # Reasoning is kept whole; text and calls are markers, since the message already holds them.
        self.assertEqual(message[ITEMS], [reasoning("rs_1"), {"type": "message"}, {"type": "function_call", "call_id": "a"},
                                          {"type": "function_call", "call_id": "b"}])

    def test_usage_is_reported_under_the_chat_names_with_cached_and_reasoning_counts(self):
        usage = responses.reply(response(said("ok"), usage=(900, 40)))["usage"]
        self.assertEqual(usage, {"prompt_tokens": 900, "completion_tokens": 40, "total_tokens": 940,
                                 "prompt_tokens_details": {"cached_tokens": 64},
                                 "completion_tokens_details": {"reasoning_tokens": 12}})
        self.assertEqual(agent.cached_tokens(usage), 64)
        self.assertIsNone(responses.reply({"output": [said("ok")]})["usage"])

    def test_plain_answers_cut_off_replies_and_failures(self):
        message, finish = reply_message(responses.reply(response(said("All good."))))
        self.assertEqual((message, finish), ({"role": "assistant", "content": "All good."}, "stop"))
        cut = responses.reply(response(said("Half"), status="incomplete", incomplete_details={"reason": "max_output_tokens"}))
        self.assertEqual(cut["choices"][0]["finish_reason"], "length")
        refusal = {"type": "message", "role": "assistant", "content": [{"type": "refusal", "refusal": "I cannot help with that."}]}
        self.assertEqual(responses.reply(response(refusal))["choices"][0]["message"]["content"], "I cannot help with that.")
        with self.assertRaisesRegex(RuntimeError, "Model error.*server_error"):
            reply_message(responses.reply({"status": "failed", "error": {"code": "server_error", "message": "boom"}, "output": []}))

    def test_reasoning_that_cannot_be_sent_back_is_not_kept(self):
        unreadable = {"type": "reasoning", "id": "rs_9", "summary": []}
        message, _ = reply_message(responses.reply(response(unreadable, said("ok"))))
        self.assertNotIn(ITEMS, message)


class PairingTests(unittest.TestCase):
    def config(self, **options):
        return Config("https://example.test/v1", "m", "", Path(__file__).parent, transport="responses", **options)

    def assert_valid(self, items):
        calls = [item["call_id"] for item in items if item.get("type") == "function_call"]
        outputs = [item["call_id"] for item in items if item.get("type") == "function_call_output"]
        self.assertEqual(calls, outputs)
        for index, item in enumerate(items):
            if item.get("type") == "reasoning":
                # Reasoning is only ever sent ahead of the output it led to.
                self.assertIn(items[index + 1].get("type") or items[index + 1].get("role"), ("function_call", "assistant", "reasoning"))

    def test_archived_exchanges_take_their_reasoning_and_leave_valid_pairs(self):
        archive = Observations()
        messages = [{"role": "system", "content": "Base prompt"}, {"role": "user", "content": "task"}]
        for ident in "abcde":
            messages += exchange(ident, 4000)
        dropped = fit_context(messages, self.config(context_tokens=5200, max_reply_tokens=1000), archive)
        self.assertGreater(dropped, 0)
        items = responses.items(messages)
        self.assert_valid(items)
        kept = [item["call_id"] for item in items if item.get("type") == "function_call"]
        self.assertEqual([item["id"] for item in items if item.get("type") == "reasoning"], ["rs_" + ident for ident in kept])
        self.assertEqual(kept[-1], "e")
        self.assertNotIn("a", kept)
        # The archive is for recall, so it holds the exchange without the encrypted reasoning.
        self.assertTrue(archive.exchanges)
        self.assertFalse(any("sealed-" in record for record in archive.records.values()))

    def test_folded_results_stay_paired_with_their_calls(self):
        archive = Observations()
        messages = [{"role": "user", "content": "task"}]
        for ident in "abcdef":
            messages += exchange(ident, 6000)
        self.assertGreater(archive.fold(messages, 3), 0)
        items = responses.items(messages)
        self.assert_valid(items)
        outputs = [item["output"] for item in items if item.get("type") == "function_call_output"]
        self.assertIn("Folded", outputs[0])
        self.assertEqual(len([item for item in items if item.get("type") == "reasoning"]), 6)

    def test_an_unanswered_call_is_left_out_together_with_its_reasoning(self):
        interrupted, _ = reply_message(responses.reply(response(reasoning("rs_x"), call("x"), call("y"))))
        messages = [{"role": "user", "content": "task"}, *exchange("a"), interrupted,
                    {"role": "tool", "tool_call_id": "x", "content": "done"},
                    {"role": "tool", "tool_call_id": "stray", "content": "nobody asked"}]
        items = responses.items(messages)
        self.assert_valid(items)
        self.assertEqual([item["call_id"] for item in items if item.get("type") == "function_call"], ["a", "x"])
        alone, _ = reply_message(responses.reply(response(reasoning("rs_z"), call("z"))))
        items = responses.items([{"role": "user", "content": "task"}, alone, {"role": "user", "content": "never mind"}])
        self.assertEqual(items, [{"role": "user", "content": "task"}, {"role": "user", "content": "never mind"}])


class TransportTests(unittest.TestCase):
    def setUp(self):
        Endpoint.script, Endpoint.seen = [], []
        agent.STREAM_UNSUPPORTED.clear()
        self.server = HTTPServer(("127.0.0.1", 0), Endpoint)
        thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(thread.join)
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name).resolve()
        self.endpoint = f"http://127.0.0.1:{self.server.server_port}/v1"
        sleeper = patch("turnloom.agent.time.sleep")
        sleeper.start()
        self.addCleanup(sleeper.stop)

    def config(self, **options):
        options.setdefault("transport", "responses")
        return Config(self.endpoint, "m", "secret", self.root, auto_approve=True, **options)

    def ask(self, messages=None, **options):
        config = options.pop("config", None) or self.config()
        return api_request(config, messages or [{"role": "user", "content": "hello"}], time.monotonic() + 30, **options)

    def test_loop_replays_reasoning_on_the_next_call_and_after_a_resume(self):
        trace = self.root / ".turnloom" / ("a" * 32 + ".jsonl")
        Endpoint.script += [(200, {}, response(reasoning("rs_1"), call("call_1"), usage=(100, 30))),
                            (200, {}, response(reasoning("rs_2"), said("Nothing is archived."), usage=(160, 20)))]
        result = run_agent("Check the archive", self.config(transcript=trace, reasoning_effort="medium"))
        self.assertEqual((result.status, result.answer, result.steps), ("completed", "Nothing is archived.", 2))
        self.assertEqual(result.usage, {"prompt_tokens": 260, "completion_tokens": 50, "cached_tokens": 128})
        (path, first), (_, second) = Endpoint.seen
        self.assertEqual(path, "/v1/responses")
        self.assertNotIn("messages", first)
        self.assertEqual((first["store"], first["reasoning"], first["tool_choice"]), (False, {"effort": "medium"}, "auto"))
        self.assertEqual([item.get("role") for item in first["input"]], ["developer", "user"])
        self.assertEqual([item.get("type") for item in second["input"][2:]], ["reasoning", "function_call", "function_call_output"])
        self.assertEqual(second["input"][2], reasoning("rs_1"))
        self.assertEqual(second["input"][4]["call_id"], "call_1")
        self.assertIn("expired", second["input"][4]["output"])
        # A later process continues the saved session with the same reasoning in place.
        history = load_history(trace, "fresh system prompt")
        Endpoint.script.append((200, {}, response(said("Still nothing."))))
        run_agent("And now?", self.config(transcript=trace), history)
        resumed = Endpoint.seen[2][1]["input"]
        self.assertEqual([item["id"] for item in resumed if item.get("type") == "reasoning"], ["rs_1", "rs_2"])
        self.assertEqual(resumed[-1], {"role": "user", "content": "And now?"})

    def test_stream_reports_text_and_assembles_calls_reasoning_and_usage(self):
        final = response(reasoning("rs_1"), said("Looking."), call("c1", "shell", '{"command": "ls"}'), usage=(9, 4))
        Endpoint.script.append((200, {"Content-Type": "text/event-stream"}, sse(
            {"type": "response.created", "response": {"status": "in_progress", "output": []}},
            {"type": "response.output_item.done", "item": reasoning("rs_1")},
            {"type": "response.output_text.delta", "delta": "Look"},
            {"type": "response.output_text.delta", "delta": "ing."},
            {"type": "response.function_call_arguments.delta", "delta": '{"command":'},
            {"type": "response.completed", "response": final})))
        pieces = []
        reply = self.ask(on_text=pieces.append)
        message = reply["choices"][0]["message"]
        self.assertEqual(pieces, ["Look", "ing."])
        self.assertEqual((message["content"], message["tool_calls"][0]["function"]["arguments"]), ("Looking.", '{"command": "ls"}'))
        self.assertEqual(message[ITEMS][0], reasoning("rs_1"))
        self.assertEqual((reply["usage"]["prompt_tokens"], reply["usage"]["completion_tokens"]), (9, 4))
        body = Endpoint.seen[0][1]
        self.assertTrue(body["stream"])
        self.assertNotIn("stream_options", body)

    def test_stream_without_a_closing_event_keeps_the_items_it_delivered(self):
        Endpoint.script.append((200, {"Content-Type": "text/event-stream"}, sse(
            {"type": "response.output_text.delta", "delta": "Partial"},
            {"type": "response.output_item.done", "item": said("Partial")})))
        reply = self.ask(on_text=lambda text: None)
        self.assertEqual(reply["choices"][0]["message"]["content"], "Partial")
        self.assertIsNone(reply["usage"])

    def test_stream_errors_are_reported(self):
        Endpoint.script.append((200, {"Content-Type": "text/event-stream"}, sse(
            {"type": "error", "code": "server_error", "message": "The model is overloaded."})))
        with self.assertRaisesRegex(RuntimeError, "Model stream error.*overloaded"):
            self.ask(on_text=lambda text: None)
        Endpoint.script.append((200, {"Content-Type": "text/event-stream"}, sse(
            {"type": "response.failed", "response": {"status": "failed", "output": [],
                                                     "error": {"code": "rate_limit_exceeded", "message": "Slow down."}}})))
        with self.assertRaisesRegex(RuntimeError, "Model error.*Slow down"):
            reply_message(self.ask(on_text=lambda text: None))

    def test_http_errors_are_explained_and_busy_endpoints_retried(self):
        Endpoint.script.append((401, {}, b'{"error": {"message": "Incorrect API key provided"}}'))
        with self.assertRaisesRegex(RuntimeError, "Model HTTP 401.*Incorrect API key"):
            self.ask()
        Endpoint.script.append((404, {}, b'{"error": "no such route"}'))
        with self.assertRaisesRegex(RuntimeError, "Model HTTP 404.*use the chat transport"):
            self.ask()
        Endpoint.script += [(429, {"Retry-After": "1"}, b"slow down"), (503, {}, b"busy"), (200, {}, response(said("ok")))]
        self.assertEqual(self.ask()["choices"][0]["message"]["content"], "ok")
        self.assertEqual(len(Endpoint.seen), 5)
        Endpoint.script.append((400, {}, b'{"error": {"message": "Unsupported parameter: reasoning.effort"}}'))
        failed = run_agent("hello", self.config(), [])
        self.assertEqual(failed.status, "error")
        self.assertRegex(failed.errors[0], "^Model HTTP 400.*Unsupported parameter")

    def test_endpoint_without_streaming_falls_back_for_this_transport_only(self):
        Endpoint.script += [(400, {}, b'{"error": "stream is not supported"}'), (200, {}, response(said("plain")))]
        self.assertEqual(self.ask(on_text=lambda text: None)["choices"][0]["message"]["content"], "plain")
        self.assertEqual(["stream" in body for _, body in Endpoint.seen], [True, False])
        self.assertEqual(agent.STREAM_UNSUPPORTED, {self.endpoint + "/responses"})

    def test_reasoning_from_another_key_is_dropped_and_the_request_repeated(self):
        messages = [{"role": "user", "content": "task"}, *exchange("a")]
        refusal = {"error": {"code": "invalid_encrypted_content", "message": "The encrypted content for item rs_a could not be verified."}}
        Endpoint.script += [(400, {}, refusal), (200, {}, response(said("Carried on.")))]
        self.assertEqual(self.ask(messages)["choices"][0]["message"]["content"], "Carried on.")
        (_, first), (_, second) = Endpoint.seen
        self.assertEqual([item.get("type") for item in first["input"]], [None, "reasoning", "function_call", "function_call_output"])
        self.assertEqual([item.get("type") for item in second["input"]], [None, "function_call", "function_call_output"])
        self.assertNotIn(ITEMS, messages[1])

    def test_chat_transport_does_not_send_stored_reasoning(self):
        messages = [{"role": "user", "content": "task"}, *exchange("a")]
        Endpoint.script.append((200, {}, {"choices": [{"message": {"role": "assistant", "content": "ok"}}]}))
        self.ask(messages, config=self.config(transport="chat"))
        path, body = Endpoint.seen[0]
        self.assertEqual(path, "/v1/chat/completions")
        self.assertEqual([sorted(message) for message in body["messages"]],
                         [["content", "role"], ["content", "role", "tool_calls"], ["content", "role", "tool_call_id"]])
        self.assertIn(ITEMS, messages[1])

    def test_transport_comes_from_the_flag_then_the_environment_and_is_checked(self):
        base = ("https://example.test/v1", "m", "", self.root)
        with patch.dict(os.environ, {"TURNLOOM_TRANSPORT": ""}):
            self.assertEqual(Config(*base).transport, "chat")
        with patch.dict(os.environ, {"TURNLOOM_TRANSPORT": "responses"}):
            self.assertEqual(Config(*base).transport, "responses")
            self.assertEqual(Config(*base, transport="chat").transport, "chat")
        with self.assertRaisesRegex(ValueError, "chat or responses"):
            Config(*base, transport="websocket")
        with self.assertRaisesRegex(ValueError, "Chat Completions"):
            api_request(Config("litellm://openai/m", "m", "", self.root, transport="responses"), [], time.monotonic() + 5)

    def test_run_takes_the_transport_flag(self):
        settings = {"TURNLOOM_ENDPOINT": self.endpoint, "TURNLOOM_MODEL": "fixture", "TURNLOOM_API_KEY": "test-only",
                    "HARNESS_ENDPOINT": "", "HARNESS_MODEL": "", "TURNLOOM_TRANSPORT": "",
                    "TURNLOOM_CONFIG_DIR": str(self.root / "config")}
        Endpoint.script.append((200, {}, response(said("Verified."))))
        output = io.StringIO()
        with patch.dict(os.environ, settings), contextlib.redirect_stdout(output), contextlib.redirect_stderr(io.StringIO()), \
                patch("sys.stdin", io.StringIO()):
            code = main(["run", "Say it", "--workspace", str(self.root), "--read-only", "--json", "--transport", "responses"])
        self.assertEqual((code, json.loads(output.getvalue())["answer"]), (0, "Verified."))
        self.assertEqual(Endpoint.seen[0][0], "/v1/responses")


if __name__ == "__main__":
    unittest.main()
