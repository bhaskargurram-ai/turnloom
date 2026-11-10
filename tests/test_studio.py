import json
import tempfile
import threading
import unittest
from http.client import HTTPConnection
from pathlib import Path
from unittest.mock import patch

from turnloom import providers
from turnloom.studio import Workbench, make_server


class StudioTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        resolution = patch.object(providers, "resolve", return_value=None)
        resolution.start()
        self.addCleanup(resolution.stop)
        self.bench = Workbench(self.root)

    def test_edited_file_checks_version_and_workspace(self):
        path = self.root / "hello.py"
        path.write_bytes(b"first\r\n")
        initial = self.bench.dispatch("read", {"path": path.name})
        changes = {"path": path.name, "text": "second\n", "version": initial["version"]}
        self.bench.dispatch("save", changes)
        self.assertEqual(path.read_bytes(), b"second\r\n")
        with self.assertRaisesRegex(ValueError, "changed on disk"):
            self.bench.dispatch("save", changes)
        with self.assertRaisesRegex(ValueError, "outside the workspace"):
            self.bench.dispatch("tree", {"path": ".."})

    def test_sample_reference_reads_from_its_own_archive(self):
        state = self.bench.dispatch("state", {})
        self.assertTrue(state["sample"])
        row = next(row for row in state["loom"] if row["reference"])
        self.assertLess(row["after"], row["before"])
        query = {"reference": row["reference"], "offset": 700, "length": 25}
        sample = self.bench.dispatch("recall", {**query, "sample": True})
        self.assertEqual(sample["total"], row["before"])
        self.assertEqual(len(sample["text"]), 25)
        self.assertIn("error", self.bench.dispatch("recall", query))

    def test_connect_uses_selected_catalog_entry(self):
        data = {"provider": "ollama", "model": "local-fixture"}
        row = {"model": data["model"], "input": None, "output": None, "tier": "free"}
        with patch.object(providers, "models", return_value=[row]), patch.object(providers, "store_profile") as save:
            with self.assertRaisesRegex(ValueError, "Refresh"):
                self.bench.dispatch("connect", data)
            self.bench.dispatch("models", data)
            self.bench.dispatch("connect", data)
            save.assert_called_once_with({"provider": "ollama", **row})
        self.assertEqual(self.bench.dispatch("state", {})["model"], "local-fixture")

    def test_api_requires_token_and_checks_origin(self):
        server, url = make_server(self.root)
        self.addCleanup(server.server_close)
        thread = threading.Thread(target=server.serve_forever)
        thread.start()
        self.addCleanup(thread.join)
        self.addCleanup(server.shutdown)
        headers = {"Authorization": "Bearer " + url.split("#")[1]}
        for request_headers, expected in [({}, 403), ({**headers, "Origin": "https://example.org"}, 403), (headers, 200)]:
            with self.subTest(expected=expected):
                client = HTTPConnection("127.0.0.1", server.server_port, timeout=5)
                try:
                    client.request("POST", "/api/state", "{}", request_headers)
                    response = client.getresponse()
                    self.assertEqual(response.status, expected)
                    if expected == 200:
                        self.assertTrue(json.loads(response.read())["sample"])
                finally:
                    client.close()
