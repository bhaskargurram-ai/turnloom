import base64
import hashlib
import io
import json
import os
import tempfile
import threading
import unittest
import urllib.parse
import urllib.request
from contextlib import redirect_stdout
from http.server import BaseHTTPRequestHandler, HTTPServer
from unittest.mock import patch

from turnloom import openrouter, providers
from turnloom.cli import main


class ConnectionTests(unittest.TestCase):
    def test_connect_then_run_uses_selected_server(self):
        sent = []
        class Host(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b'{"data":[{"id":"local-fixture"}]}')

            def do_POST(self):
                sent.append((self.path, json.loads(self.rfile.read(int(self.headers["Content-Length"])))))
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b'{"choices":[{"message":{"content":"ready"}}]}')

        with HTTPServer(("127.0.0.1", 0), Host) as host, tempfile.TemporaryDirectory() as directory:
            thread = threading.Thread(target=host.serve_forever, daemon=True)
            thread.start()
            try:
                env = {"TURNLOOM_CONFIG_DIR": directory, "HARNESS_ENDPOINT": "", "HARNESS_MODEL": ""}
                endpoint = f"http://127.0.0.1:{host.server_port}/v1"
                with patch.dict(os.environ, env), patch.dict(providers.PROVIDERS, {"ollama": ["Fixture", endpoint, ""]}), redirect_stdout(io.StringIO()):
                    self.assertEqual(main(["connect", "ollama", "--model", "local-fixture"]), 0)
                    self.assertEqual(main(["run", "hello", "--workspace", directory, "--yes"]), 0)
                    self.assertEqual(sent[0][0], "/v1/chat/completions")
                    self.assertEqual(sent[0][1]["model"], "local-fixture")
                    self.assertEqual(main(["disconnect"]), 0)
                    self.assertFalse(providers.profile_path().exists())
            finally:
                host.shutdown()
                thread.join()

    def test_free_filter_and_secret_isolation(self):
        rows = {"data": [{"id": name, "supported_parameters": ["tools"], "pricing": price} for name, price in [
            ("free-one", {"prompt": "0", "completion": "0"}), ("unknown", {}),
            ("paid", {"prompt": "0", "completion": "0.000002"})]]}
        rows["data"].append({"id": "no-tools", "supported_parameters": []})
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {"TURNLOOM_CONFIG_DIR": folder, "OPENROUTER_API_KEY": "fixture-key"}):
            with patch.object(providers, "fetch", side_effect=[rows, {"data": {}}]) as get, redirect_stdout(io.StringIO()):
                main(["connect", "openrouter", "--tier", "free", "--model", "free-one"])
                self.assertEqual(get.call_args.args, ("https://openrouter.ai/api/v1/key", "fixture-key"))
            self.assertNotIn("fixture-key", providers.profile_path().read_text())
            self.assertEqual(providers.profile()["tier"], "free")
            with patch.dict(os.environ, {"HARNESS_MODEL": "", "HARNESS_ENDPOINT": ""}):
                self.assertTrue(providers.resolve().free)
            with patch.object(providers, "fetch", return_value=rows):
                self.assertEqual({row["model"]: row["tier"] for row in providers.models("openrouter")},
                                 {"free-one": "free", "paid": "paid", "unknown": "unknown"})

    def test_browser_loopback_exchanges_matching_pkce_verifier(self):
        challenge = []
        threads = []
        actual_open = urllib.request.urlopen
        def browser(url):
            params = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
            challenge.extend(params["code_challenge"])
            callback = params["callback_url"][0] + "?" + urllib.parse.urlencode({"state": params["state"][0], "code": "one-time"})
            def send():
                with actual_open(callback, timeout=5) as reply:
                    reply.read()
            threads.append(threading.Thread(target=send))
            threads[-1].start()
            return True
        def exchange(request, **kwargs):
            payload = json.loads(request.data)
            digest = hashlib.sha256(payload["code_verifier"].encode()).digest()
            self.assertEqual(challenge[0], base64.urlsafe_b64encode(digest).decode().rstrip("="))
            self.assertEqual(payload["code"], "one-time")
            return io.BytesIO(b'{"key":"fake-key"}')
        with patch.object(openrouter.webbrowser, "open", side_effect=browser), patch.object(urllib.request, "urlopen", side_effect=exchange):
            self.assertEqual(openrouter.login(), "fake-key")
        threads[0].join()
