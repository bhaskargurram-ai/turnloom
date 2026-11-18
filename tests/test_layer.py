import json
import subprocess
import tempfile
import threading
import unittest
import urllib.request
from importlib.util import find_spec
from pathlib import Path
from unittest.mock import patch

from turnloom import atif, gateway, meter, providers, proxy, roster
from turnloom.agent import Config, cache_marked, reply_allowance, run_agent
from turnloom.studio import Workbench


def reply(content=None, calls=None, prompt=100, completion=10, cached=0):
    message = {"role": "assistant", "content": content}
    if calls:
        message["tool_calls"] = [{"id": f"c{index}", "type": "function", "function": {"name": name, "arguments": json.dumps(arguments)}}
                                 for index, (name, arguments) in enumerate(calls)]
    return {"id": "x", "model": "fixture", "choices": [{"index": 0, "message": message, "finish_reason": "tool_calls" if calls else "stop"}],
            "usage": {"prompt_tokens": prompt, "completion_tokens": completion, "prompt_tokens_details": {"cached_tokens": cached}}}


class RosterTests(unittest.TestCase):
    def test_catalog_spans_divisions_and_resolves_short_names(self):
        rows = roster.catalog()
        self.assertGreater(len(rows), 250)
        self.assertEqual([row["id"] for row in rows[:2]], ["builder", "debugger"])
        self.assertEqual(roster.find("reviewer")["id"], "engineering-code-reviewer")
        self.assertIn("Code Reviewer", roster.body("reviewer"))
        self.assertIn("Role: builder", roster.body("builder"))
        self.assertIn("Role: debugger", roster.body("debugger"))
        with self.assertRaisesRegex(ValueError, "Unknown persona"):
            roster.find("nobody")

    def test_workspace_persona_is_selectable(self):
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder, ".turnloom", "personas")
            target.mkdir(parents=True)
            (target / "release-captain.md").write_text("---\nname: Release Captain\ndescription: Ships.\n---\nCut the release.", encoding="utf-8")
            self.assertEqual(roster.find("custom-release-captain", folder)["name"], "Release Captain")
            self.assertEqual(roster.body("custom-release-captain", folder), "Cut the release.")


class MeterTests(unittest.TestCase):
    def test_cached_input_bills_at_the_cache_rate(self):
        usage = {"prompt_tokens": 1_000_000, "completion_tokens": 0, "cached_tokens": 800_000}
        self.assertAlmostEqual(meter.price(usage, 3, 15, 0.3), 0.6 + 0.24)
        self.assertAlmostEqual(meter.price(usage, 3, 15), 3.0)

    def test_ledger_groups_by_model_and_persona(self):
        with tempfile.TemporaryDirectory() as folder:
            ledger = meter.Ledger(folder)
            ledger.add(model="a", persona="builder", prompt_tokens=100, completion_tokens=10, cost=0.5, saved_tokens=40)
            ledger.add(model="b", persona="builder", prompt_tokens=50, completion_tokens=5, cost=None)
            summary = ledger.summary()
            self.assertEqual(summary["totals"]["calls"], 2)
            self.assertEqual(summary["totals"]["unpriced_calls"], 1)
            self.assertEqual(summary["personas"][0]["tokens"], 165)
            self.assertEqual({row["model"] for row in summary["models"]}, {"a", "b"})
            self.assertEqual(len(summary["days"]), 1)

    def test_composition_separates_folded_results(self):
        parts = meter.composition([{"role": "system", "content": "s"}, {"role": "tool", "content": '{"reference": "ab"}'},
                                   {"role": "tool", "content": "plain output"}])
        self.assertGreater(parts["folded"], 0)
        self.assertGreater(parts["tools"], 0)


class AgentTests(unittest.TestCase):
    def test_claude_routes_mark_the_system_prompt_for_caching(self):
        messages = [{"role": "system", "content": "rules"}, {"role": "user", "content": "hi"}]
        routed = Config("https://openrouter.ai/api/v1", "anthropic/claude-sonnet-5.5", "", Path("."))
        self.assertEqual(cache_marked(messages, routed)[0]["content"][0]["cache_control"], {"type": "ephemeral"})
        self.assertIs(cache_marked(messages, Config("https://openrouter.ai/api/v1", "qwen/qwen3", "", Path("."))), messages)
        self.assertIs(cache_marked(messages, Config("https://api.example/v1", "claude", "", Path("."))), messages)

    def test_reply_allowance_respects_a_strict_window(self):
        config = Config("x", "m", "", Path("."), context_tokens=4000, max_reply_tokens=3000)
        messages = [{"role": "user", "content": "a" * 8000}]
        self.assertLess(reply_allowance(messages, config), 1300)
        self.assertLess(reply_allowance(messages, config, scale=1.5), reply_allowance(messages, config))
        self.assertEqual(reply_allowance([{"role": "user", "content": "a"}], config), 3000)

    def test_steps_report_cached_tokens_and_cost(self):
        with tempfile.TemporaryDirectory() as folder:
            steps = []
            config = Config("local", "fixture", "", Path(folder), input_rate=2, output_rate=10, cache_rate=0.2, on_step=steps.append)
            with patch("turnloom.agent.api_request", return_value=reply("Done.", prompt=1000, completion=100, cached=600)):
                result = run_agent("Say done", config)
            self.assertEqual(result.usage["cached_tokens"], 600)
            self.assertAlmostEqual(steps[0]["cost"], (400 * 2 + 600 * 0.2 + 100 * 10) / 1e6)
            self.assertAlmostEqual(result.estimated_usd, steps[0]["cost"])


class AtifTests(unittest.TestCase):
    def test_transcript_becomes_paired_steps_with_totals(self):
        with tempfile.TemporaryDirectory() as folder:
            trace = Path(folder, "run.jsonl")
            config = Config("local", "fixture", "", Path(folder), transcript=trace, auto_approve=True)
            with patch("turnloom.agent.api_request", side_effect=[
                    reply(None, [("shell", {"command": "echo hi"})], prompt=50, completion=5),
                    reply("Printed hi.", prompt=80, completion=7, cached=20)]):
                run_agent("Print hi", config)
            trajectory = atif.export(trace, Path(folder, "trajectory.json"))
        self.assertEqual(trajectory["schema_version"], "ATIF-v1.8")
        self.assertEqual([step["source"] for step in trajectory["steps"]], ["system", "user", "agent", "agent"])
        self.assertEqual([step["step_id"] for step in trajectory["steps"]], [1, 2, 3, 4])
        call = trajectory["steps"][2]
        self.assertEqual(call["tool_calls"][0]["arguments"], {"command": "echo hi"})
        self.assertEqual(call["observation"]["results"][0]["source_call_id"], call["tool_calls"][0]["tool_call_id"])
        self.assertIn("hi", call["observation"]["results"][0]["content"])
        self.assertEqual(trajectory["final_metrics"]["total_prompt_tokens"], 130)
        self.assertEqual(trajectory["final_metrics"]["total_cached_tokens"], 20)
        self.assertTrue(all(step["timestamp"] > "2026" for step in trajectory["steps"]))


class ProxyTests(unittest.TestCase):
    def conversation(self, results):
        messages = [{"role": "system", "content": "s"}, {"role": "user", "content": "task"}]
        for index, text in enumerate(results):
            messages.append({"role": "assistant", "content": None, "tool_calls": [
                {"id": f"t{index}", "type": "function", "function": {"name": "bash", "arguments": "{}"}}]})
            messages.append({"role": "tool", "tool_call_id": f"t{index}", "content": text})
        return messages

    def test_old_results_fold_and_recall_is_answered_inside_the_proxy(self):
        seen = []
        long = "line of build output\n" * 200
        def send(body):
            seen.append(json.loads(json.dumps(body)))
            if len(seen) == 1:
                reference = json.loads(body["messages"][3]["content"])["reference"]
                return reply(None, [(proxy.RECALL, {"reference": reference, "length": 20}), ("bash", {"command": "ls"})])
            return reply("The log starts with: " + json.loads(body["messages"][-1]["content"])["text"])
        connection = providers.Connection("http://upstream/v1", "fixture", "", 1, 2)
        with tempfile.TemporaryDirectory() as folder:
            layer = proxy.Layer(connection, folder, keep=1, send=send)
            body = {"model": "default", "messages": self.conversation([long, "short", "recent"]),
                    "tools": [{"type": "function", "function": {"name": "bash", "parameters": {"type": "object"}}}]}
            response = layer.complete(body)
            ledger = meter.Ledger(folder).rows()
        self.assertEqual(seen[0]["model"], "fixture")
        self.assertLess(len(seen[0]["messages"][3]["content"]), 700)
        self.assertEqual(seen[0]["tools"][-1]["function"]["name"], proxy.RECALL)
        self.assertEqual(len(body["messages"][3]["content"]), len(long), "the caller's messages are untouched")
        self.assertEqual(response["choices"][0]["message"]["content"], "The log starts with: line of build output")
        self.assertEqual(response["usage"]["prompt_tokens"], 200)
        self.assertEqual(layer.totals["recalls"], 1)
        self.assertGreater(layer.totals["saved_tokens"], 500)
        self.assertEqual(ledger[0]["persona"], "proxy")
        self.assertAlmostEqual(ledger[0]["cost"], (200 * 1 + 20 * 2) / 1e6)

    def test_search_recall_and_unfolded_fallback(self):
        long = "\n".join(f"line {index}: ok" if index != 143 else "line 143: WARNING cache rebuilt" for index in range(400))
        archive = proxy.Observations()
        reference = archive.save(long)
        found = json.loads(archive.recall(json.dumps({"reference": reference, "search": "warning"})))
        self.assertEqual(found["count"], 1)
        self.assertIn("line 143: WARNING cache rebuilt", found["matches"][0]["text"])
        self.assertLess(len(found["matches"][0]["text"]), 800)
        seen = []
        def send(body):
            seen.append(body["messages"])
            if any(tool["function"]["name"] == proxy.RECALL for tool in body.get("tools", [])):
                folded = json.loads(body["messages"][3]["content"])
                return reply(None, [(proxy.RECALL, {"reference": folded["reference"], "offset": 0, "length": 10})])
            return reply("answered from the full history")
        layer = proxy.Layer(providers.Connection("http://upstream/v1", "fixture", ""), keep=1, send=send)
        body = {"model": "x", "messages": self.conversation([long, "recent"]),
                "tools": [{"type": "function", "function": {"name": "bash", "parameters": {"type": "object"}}}]}
        response = layer.complete(body)
        self.assertEqual(response["choices"][0]["message"]["content"], "answered from the full history")
        self.assertEqual(seen[-1][3]["content"], long)
        self.assertEqual(len(seen), 9)
        self.assertEqual(layer.totals["saved_tokens"], 0)

    def test_http_endpoint_serves_json_and_streams(self):
        layer = proxy.Layer(providers.Connection("http://upstream/v1", "fixture", ""),
                            send=lambda body: reply("hello", [("bash", {"command": "ls"})]))
        server = proxy.make_server(layer, 0, token="secret")
        thread = threading.Thread(target=server.serve_forever)
        thread.start()
        self.addCleanup(thread.join)
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        base = f"http://127.0.0.1:{server.server_port}/v1"
        def post(body, token="secret"):
            request = urllib.request.Request(base + "/chat/completions", json.dumps(body).encode(),
                                             {"Content-Type": "application/json", "Authorization": "Bearer " + token})
            return urllib.request.urlopen(request, timeout=5).read().decode()
        body = {"model": "x", "messages": [{"role": "user", "content": "hi"}]}
        self.assertEqual(json.loads(post(body))["choices"][0]["message"]["content"], "hello")
        events = [json.loads(line[6:]) for line in post({**body, "stream": True, "stream_options": {"include_usage": True}}).split("\n\n")
                  if line.startswith("data: {")]
        self.assertEqual(events[0]["choices"][0]["delta"]["tool_calls"][0]["function"]["name"], "bash")
        self.assertEqual(events[1]["choices"][0]["finish_reason"], "tool_calls")
        self.assertEqual(events[2]["usage"]["prompt_tokens"], 100)
        with self.assertRaises(urllib.error.HTTPError) as wrong:
            post(body, token="wrong")
        wrong.exception.close()
        for headers in ({"Origin": "https://example.org"}, {"Host": "attacker.example"}):
            request = urllib.request.Request(base + "/chat/completions", json.dumps(body).encode(),
                                             {"Content-Type": "text/plain", "Authorization": "Bearer secret", **headers})
            with self.assertRaises(urllib.error.HTTPError) as refused:
                urllib.request.urlopen(request, timeout=5)
            self.assertEqual(refused.exception.code, 403)
            refused.exception.close()

    @unittest.skipUnless(find_spec("openai"), "requires the openai package")
    def test_openai_sdk_talks_to_the_proxy(self):
        from openai import OpenAI
        layer = proxy.Layer(providers.Connection("http://upstream/v1", "fixture", ""), send=lambda body: reply("pong"))
        server = proxy.make_server(layer, 0)
        thread = threading.Thread(target=server.serve_forever)
        thread.start()
        self.addCleanup(thread.join)
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        client = OpenAI(base_url=f"http://127.0.0.1:{server.server_port}/v1", api_key="unused")
        self.addCleanup(client.close)
        answer = client.chat.completions.create(model="turnloom", messages=[{"role": "user", "content": "ping"}])
        self.assertEqual(answer.choices[0].message.content, "pong")
        chunks = list(client.chat.completions.create(model="turnloom", messages=[{"role": "user", "content": "ping"}], stream=True))
        self.assertEqual("".join(chunk.choices[0].delta.content or "" for chunk in chunks if chunk.choices), "pong")
        self.assertEqual(client.models.list().data[0].id, "fixture")


@unittest.skipUnless(gateway.available(), "requires the llm extra")
class GatewayTests(unittest.TestCase):
    def test_native_providers_list_priced_tool_models(self):
        ids = [key for key, _, _ in gateway.providers()]
        self.assertEqual(ids[0], "litellm:anthropic")
        self.assertIn("litellm:bedrock", ids)
        rows = providers.models("litellm:anthropic")
        self.assertTrue(all(row["model"].startswith("anthropic/") and row["tools"] for row in rows))
        self.assertTrue(any(row["cache_read"] and row["context"] for row in rows))
        self.assertEqual(providers.info("litellm:anthropic")[1:], ("litellm://anthropic", "ANTHROPIC_API_KEY"))
        self.assertEqual(providers.info("litellm:bedrock")[2], "")

    def test_lookup_prices_only_from_the_same_provider(self):
        own = gateway.lookup("gpt-4o", "openai")
        self.assertTrue(own["same_provider"])
        self.assertGreater(own["input"], 0)
        self.assertGreater(own["context"], 100000)
        self.assertFalse((gateway.lookup("gpt-4o", "groq") or {}).get("same_provider"))
        self.assertIsNone(gateway.lookup("no-such-model-xyz", "openai"))

    def test_gateway_request_reaches_litellm_in_openai_format(self):
        import litellm
        config = Config("litellm://anthropic", "anthropic/claude-haiku-4-5", "k", Path("."))
        with tempfile.TemporaryDirectory() as folder, patch.object(litellm, "completion") as completion:
            completion.return_value.model_dump.return_value = reply("ok", prompt=9, completion=1)
            result = run_agent("hi", Config(**{**config.__dict__, "workspace": Path(folder)}))
        self.assertEqual(result.answer, "ok")
        sent = completion.call_args.kwargs
        self.assertEqual((sent["model"], sent["api_key"]), ("anthropic/claude-haiku-4-5", "k"))
        self.assertEqual(sent["messages"][0]["content"][0]["cache_control"], {"type": "ephemeral"})
        self.assertEqual([tool["function"]["name"] for tool in sent["tools"]],
                         ["list_files", "read_file", "search_files", "edit_file", "write_file", "shell", "recall"])


class StudioLayerTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        resolution = patch.object(providers, "resolve", return_value=providers.Connection(
            "local", "fixture", "", 1, 4, False, 0.1, 200000, "ollama"))
        resolution.start()
        self.addCleanup(resolution.stop)
        private = patch.dict("os.environ", {"TURNLOOM_CONFIG_DIR": str(self.root / "operator-config")})
        private.start()
        self.addCleanup(private.stop)
        self.bench = Workbench(self.root)
        self.addCleanup(self.bench.close)

    def wait(self):
        for _ in range(400):
            if not self.bench.dispatch("state", {})["busy"]:
                return
            threading.Event().wait(0.02)
        self.fail("turn did not finish")

    def test_turn_feeds_the_meter_ledger_and_usage_view(self):
        self.bench.dispatch("settings", {"budget": 1.5, "keep": 2, "servers": []})
        with patch("turnloom.agent.api_request", side_effect=[
                reply(None, [("list_files", {})], prompt=1000, completion=50, cached=400),
                reply("Empty workspace.", prompt=1200, completion=60)]) as request:
            self.bench.dispatch("run", {"task": "Look around", "persona": "reviewer"})
            self.wait()
        self.assertEqual(request.call_args_list[0].args[0].budget_usd, 1.5)
        self.assertEqual(request.call_args_list[0].args[0].fold_keep, 2)
        self.assertIn("Code Reviewer", request.call_args_list[0].args[1][0]["content"])
        state = self.bench.dispatch("state", {})
        self.assertEqual(state["persona"], "engineering-code-reviewer")
        self.assertEqual(state["meter"]["prompt_tokens"], 2200)
        self.assertEqual(state["meter"]["cached_tokens"], 400)
        self.assertEqual(state["meter"]["window"], 200000)
        self.assertGreater(state["meter"]["context"]["system"], 0)
        usage = self.bench.dispatch("usage", {})
        self.assertEqual(usage["totals"]["calls"], 2)
        self.assertEqual(usage["personas"][0]["persona"], "engineering-code-reviewer")
        self.assertAlmostEqual(usage["session"]["cost"], (600 * 1 + 400 * 0.1 + 50 * 4 + 1200 * 1 + 60 * 4) / 1e6)
        saved, = (self.root / "operator-config" / "workspaces").glob("*.json")
        self.assertEqual(json.loads(saved.read_text())["settings"]["budget"], 1.5)
        self.assertFalse((self.root / ".turnloom" / "studio.json").exists())
        self.assertEqual(Workbench(self.root).settings["budget"], 1.5)

    def test_a_repository_cannot_preapprove_actions_or_enable_servers(self):
        folder = self.root / ".turnloom"
        folder.mkdir(exist_ok=True)
        (folder / "studio.json").write_text(json.dumps({"auto_approve": True, "auto_edit": True, "gate": "off",
                                                        "servers": ["evil"]}), encoding="utf-8")
        (folder / "mcp.json").write_text(json.dumps({"mcpServers": {"evil": {"command": "evil-binary"}}}), encoding="utf-8")
        settings = Workbench(self.root).settings
        self.assertEqual((settings["auto_approve"], settings["auto_edit"], settings["gate"], settings["servers"]),
                         (False, False, "confirm", []))
        listed = self.bench.dispatch("servers", {})["servers"]
        self.assertEqual([(row["name"], row["enabled"], row["running"]) for row in listed if row["name"] == "evil"],
                         [("evil", False, False)])
        self.bench.dispatch("reset", {})
        fresh = self.bench.dispatch("state", {})
        self.assertEqual((fresh["messages"], fresh["loom"], fresh["meter"]["steps"]), ([], [], 0))

    def check_team_mode(self):
        self.bench.dispatch("settings", {"mode": "team", "servers": []})
        prompts = []
        modes = []
        def answer(config, messages, deadline, allow_tools=True, on_text=None):
            prompts.append(messages[0]["content"])
            modes.append(config.read_only)
            return reply(f"stage {len(prompts)} report")
        with patch("turnloom.agent.api_request", side_effect=answer):
            self.bench.dispatch("run", {"task": "Ship it", "persona": "builder"})
            self.wait()
        state = self.bench.dispatch("state", {})
        self.assertEqual([stage["status"] for stage in state["stages"]], ["completed"] * 3)
        self.assertIn("Software Architect", prompts[0])
        self.assertIn("Code Reviewer", prompts[2])
        stages = [message.get("stage") for message in state["messages"] if message["role"] == "assistant"]
        self.assertEqual(stages, ["plan", "build", "review", None])
        self.assertEqual(modes, [True, False, True])
        self.assertEqual(self.bench.dispatch("usage", {})["totals"]["calls"], 3)

    @unittest.skipUnless(find_spec("langgraph"), "requires the team extra")
    def test_team_mode_runs_each_stage_with_its_persona(self):
        self.check_team_mode()

    def test_team_mode_runs_without_langgraph(self):
        with patch.dict("sys.modules", {"langgraph": None, "langgraph.graph": None}):
            self.check_team_mode()

    def test_changes_compare_the_working_tree_with_the_last_commit(self):
        def git(*arguments):
            subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.org", "-c", "commit.gpgsign=false", *arguments],
                           cwd=self.root, check=True, capture_output=True)
        (self.root / "app.py").write_text("one\n")
        git("init", "-q")
        git("add", ".")
        git("commit", "-q", "-m", "first")
        (self.root / "app.py").write_text("two\n")
        (self.root / "new file.txt").write_text("fresh\n")
        files = {row["path"]: row["status"] for row in self.bench.dispatch("changes", {})["files"]}
        self.assertEqual(files, {"app.py": "modified", "new file.txt": "added"})
        self.assertEqual(self.bench.dispatch("change", {"path": "app.py"}), {"path": "app.py", "original": "one\n", "modified": "two\n"})
        self.assertEqual(self.bench.dispatch("change", {"path": "new file.txt"})["original"], "")

    def test_persona_gallery_and_preview(self):
        gallery = self.bench.dispatch("personas", {})
        self.assertGreater(len(gallery["personas"]), 250)
        self.assertEqual(len(gallery["commit"]), 40)
        self.assertIn("Security", self.bench.dispatch("persona", {"id": "security-appsec-engineer"})["text"])

    @unittest.skipUnless(find_spec("verifydoc") and find_spec("unwind"), "requires the studio extra")
    def test_documents_panel_verifies_through_unwind_and_reads_the_ledger(self):
        (self.root / "receipt.txt").write_text("Merchant: Corner Cafe\nDate: 2026-11-02\nTotal: 42.50\n")
        schema = {"type": "object", "properties": {"total": {"type": "number"}, "merchant": {"type": "string"}}}
        result = self.bench.dispatch("verify", {"path": "receipt.txt", "schema": schema, "threshold": 0.5})
        self.assertEqual({field["path"] for field in result["fields"]}, {"total", "merchant"})
        self.assertIn("Corner Cafe", result["text"])
        self.assertEqual(result["n_accepted"] + result["n_review"], 2)
        servers = self.bench.dispatch("servers", {})["servers"]
        self.assertEqual([(row["name"], row["unwind"], row["running"]) for row in servers], [("verifydoc", True, True)])
        names = [tool["name"] for tool in self.bench.dispatch("server_tools", {"name": "verifydoc"})]
        self.assertIn("unwind.undo", names)
        self.assertIn("checkpoint_id", self.bench.dispatch("checkpoint", {"label": "before edits"}))
        self.assertIsInstance(self.bench.dispatch("ledger", {})["entries"], list)
        self.bench.assess("verifydoc", "verify_extraction", {"document": "x", "schema": "{}"})
        self.assertTrue(self.bench.risk["reversibility_class"].startswith("R"))


if __name__ == "__main__":
    unittest.main()
