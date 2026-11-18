import unittest

from slugify import slugify


class SlugifyTests(unittest.TestCase):
    def test_lowercases_and_joins_words(self):
        self.assertEqual(slugify("Hello World"), "hello-world")

    def test_collapses_punctuation_and_spaces(self):
        self.assertEqual(slugify("  Release Notes: v2.0 (Final)  "), "release-notes-v2-0-final")

    def test_keeps_existing_slugs(self):
        self.assertEqual(slugify("already-a-slug"), "already-a-slug")

    def test_empty_title(self):
        self.assertEqual(slugify(""), "")


if __name__ == "__main__":
    unittest.main()
