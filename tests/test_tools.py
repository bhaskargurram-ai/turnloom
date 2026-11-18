import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from turnloom.tools import PROTECTED, Workspace, tolerant


class WorkspaceToolTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name).resolve()
        (self.root / "src").mkdir()
        (self.root / ".github").mkdir()
        (self.root / ".git").mkdir()
        (self.root / ".git" / "config").write_text("[core]\n")
        (self.root / "src" / "app.py").write_bytes(b"def a():\r\n    return 1\r\n\r\ndef b():\r\n    return 1\r\n")
        (self.root / ".env").write_text("TOKEN=private\n")
        (self.root / ".github" / "ci.yml").write_text("name: checks\n")
        self.files = Workspace(self.root)

    def call(self, name, **arguments):
        return self.files.dispatch(name, json.dumps(arguments))

    def test_paths_stay_inside_the_workspace_and_skip_secrets(self):
        for path in ["../x", "src/../../x", "/etc/passwd", "C:/Windows/win.ini", "\\Windows\\win.ini"]:
            with self.subTest(path=path):
                self.assertIn(self.call("read_file", path=path)["error"],
                              ("Path outside the workspace.", "No such file or directory: " + path))
                self.assertNotIn("text", self.call("read_file", path=path))
        for path in [".env", ".git/config", "src/id_rsa", "deploy/server.pem", ".Env"]:
            with self.subTest(path=path):
                self.assertEqual(self.call("read_file", path=path), {"error": PROTECTED})
                self.assertEqual(self.call("write_file", path=path, content="x"), {"error": PROTECTED})
        self.assertEqual((self.root / ".env").read_text(), "TOKEN=private\n")
        self.assertEqual(self.call("list_files")["entries"], [".github/", "src/"])
        self.assertEqual(self.call("read_file", path=".github/ci.yml")["text"], "1: name: checks")

    @unittest.skipUnless(os.name == "nt", "Windows path aliases")
    def test_windows_aliases_of_protected_names_are_refused(self):
        for path in [".env.", ".env ", ".env::$DATA", "src/app.py:stream"]:
            with self.subTest(path=path):
                result = self.call("read_file", path=path)
                self.assertIn("error", result)
                self.assertNotIn("private", json.dumps(result))

    def test_symlink_escape_is_refused(self):
        with tempfile.TemporaryDirectory() as elsewhere:
            (Path(elsewhere) / "secret.txt").write_text("outside")
            link = self.root / "link"
            try:
                link.symlink_to(elsewhere, target_is_directory=True)
            except OSError:
                self.skipTest("symlinks unavailable")
            self.assertEqual(self.call("read_file", path="link/secret.txt"), {"error": "Path outside the workspace."})
            self.assertEqual(self.call("write_file", path="link/new.txt", content="x"),
                             {"error": "Path outside the workspace."})
            self.assertFalse((Path(elsewhere) / "new.txt").exists())
            self.assertNotIn("link/", self.call("list_files")["entries"])

    def test_read_pages_through_a_file(self):
        (self.root / "long.txt").write_text("\n".join(f"row {index}" for index in range(1, 601)))
        first = self.call("read_file", path="long.txt")
        self.assertEqual((first["line"], first["total_lines"], first["next_line"]), (1, 600, 251))
        tail = self.call("read_file", path="long.txt", line=599, limit=50)
        self.assertEqual(tail["text"], "599: row 599\n600: row 600")
        self.assertNotIn("next_line", tail)
        (self.root / "blob.bin").write_bytes(b"\x00\x01")
        self.assertIn("binary", self.call("read_file", path="blob.bin")["error"])

    def check_search(self, engine):
        found = self.call("search_files", query="return 1")
        if found["engine"] != engine:
            self.skipTest("ripgrep is not installed")
        self.assertEqual(found["matches"], ["src/app.py:2:    return 1", "src/app.py:5:    return 1"])
        self.assertEqual(self.call("search_files", query=r"def \w\(", regex=True, glob="*.py")["count"], 2)
        self.assertEqual(self.call("search_files", query="TOKEN")["matches"], [])
        self.assertEqual(self.call("search_files", query="TOKEN", glob=".env")["matches"], [])
        self.assertEqual(self.call("search_files", query="checks", path=".github/ci.yml")["matches"],
                         [".github/ci.yml:1:name: checks"])
        self.assertIn("Invalid regular expression", self.call("search_files", query="(", regex=True)["error"])

    def test_search_reports_lines_with_ripgrep(self):
        self.check_search("ripgrep")

    def test_search_falls_back_to_python_without_ripgrep(self):
        with patch("turnloom.tools.shutil.which", return_value=None):
            self.check_search("python")

    def test_glob_lists_nested_files(self):
        self.assertEqual(self.call("list_files", glob="**/*.py"), {"paths": ["src/app.py"], "truncated": False})
        self.assertEqual(self.call("list_files", glob="*.yml")["paths"], [".github/ci.yml"])

    def test_edit_requires_one_match_and_keeps_line_endings(self):
        ambiguous = self.call("edit_file", path="src/app.py", old_string="    return 1", new_string="    return 2")
        self.assertIn("matches 2 places", ambiguous["error"])
        done = self.call("edit_file", path="src/app.py", old_string="def b():\n    return 1",
                         new_string="def b():\n    return 2")
        self.assertEqual((done["added"], done["removed"]), (1, 1))
        self.assertIn("+    return 2", done["diff"])
        self.assertEqual((self.root / "src" / "app.py").read_bytes(),
                         b"def a():\r\n    return 1\r\n\r\ndef b():\r\n    return 2\r\n")
        every = self.call("edit_file", path="src/app.py", old_string="return", new_string="yield", replace_all=True)
        self.assertEqual(every["replacements"], 2)
        self.assertIn("not found", self.call("edit_file", path="src/app.py", old_string="absent", new_string="x")["error"])
        self.assertIn("write_file", self.call("edit_file", path="missing.py", old_string="a", new_string="b")["error"])

    def test_edit_tolerates_indentation_drift_only_when_unambiguous(self):
        source = "class A:\n    def run(self):\n        total = 1\n        return total\n"
        fixed = tolerant(source, "def run(self):\n    total = 1", "def run(self):\n    total = 2")
        self.assertEqual(fixed, "class A:\n    def run(self):\n        total = 2\n        return total\n")
        self.assertIsNone(tolerant("x = 1\nx = 1\n", "  x = 1", "  x = 2"))

    def test_declined_edit_leaves_the_file_and_shows_a_diff(self):
        seen = []
        result = self.files.dispatch("write_file", json.dumps({"path": "notes.md", "content": "hello\n"}),
                                     lambda kind, detail: seen.append((kind, detail)) or False)
        self.assertEqual(result, {"error": "Operator declined the edit"})
        self.assertFalse((self.root / "notes.md").exists())
        self.assertEqual(seen[0][0], "edit")
        self.assertIn("Create notes.md", seen[0][1])
        self.assertIn("+hello", seen[0][1])

    def test_read_only_sessions_refuse_writes(self):
        result = self.files.dispatch("write_file", '{"path": "a.txt", "content": "x"}', read_only=True)
        self.assertIn("read-only", result["error"])
        self.assertFalse((self.root / "a.txt").exists())

    def test_undo_restores_a_turn_and_reports_conflicts(self):
        original = (self.root / "src" / "app.py").read_bytes()
        self.files.turn = 1
        self.call("edit_file", path="src/app.py", old_string="def a():\n    return 1", new_string="def a():\n    return 7")
        self.call("write_file", path="docs/new.md", content="draft\n")
        self.call("write_file", path="keep.md", content="agent\n")
        (self.root / "keep.md").write_text("operator edit\n")
        turn, files = self.files.pending()
        self.assertEqual((turn, [item["path"] for item in files]), (1, ["src/app.py", "docs/new.md", "keep.md"]))
        outcome = self.files.undo()
        self.assertEqual(outcome, {"restored": ["src/app.py", "docs/new.md"], "conflicts": ["keep.md"]})
        self.assertEqual((self.root / "src" / "app.py").read_bytes(), original)
        self.assertFalse((self.root / "docs" / "new.md").exists())
        self.assertEqual((self.root / "keep.md").read_text(), "operator edit\n")

    def test_malformed_arguments_return_errors(self):
        self.assertIn("JSON object", self.files.dispatch("read_file", "[]")["error"])
        self.assertEqual(self.files.dispatch("read_file", "{}"), {"error": "Missing argument: path"})
        self.assertIn("error", self.files.dispatch("edit_file", '{"path": "src/app.py", "old_string": 1, "new_string": "x"}'))


if __name__ == "__main__":
    unittest.main()
