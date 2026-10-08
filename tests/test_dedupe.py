"""Déduplication des offres par URL normalisée."""
from __future__ import annotations

import unittest

from dedupe import dedupe
from models import Offer


def _offer(url: str, title: str = "Offre") -> Offer:
    return Offer(title=title, url=url, source="test")


class TestDedupe(unittest.TestCase):
    def test_query_string_is_ignored(self):
        kept = dedupe([
            _offer("https://exemple.org/a?x=1", "Première"),
            _offer("https://exemple.org/a?x=2", "Deuxième"),
        ])
        self.assertEqual(len(kept), 1)
        self.assertEqual(kept[0].title, "Première")

    def test_trailing_slash_is_ignored(self):
        kept = dedupe([
            _offer("https://exemple.org/a"),
            _offer("https://exemple.org/a/"),
        ])
        self.assertEqual(len(kept), 1)

    def test_different_urls_are_kept(self):
        kept = dedupe([
            _offer("https://exemple.org/a", "A"),
            _offer("https://exemple.org/b", "B"),
        ])
        self.assertEqual([o.title for o in kept], ["A", "B"])

    def test_empty_url_falls_back_to_uid(self):
        kept = dedupe([
            _offer("", "Alpha"),
            _offer("", "Beta"),
        ])
        self.assertEqual([o.title for o in kept], ["Alpha", "Beta"])

    def test_host_case_and_fragment_collapse(self):
        kept = dedupe([
            _offer("https://Exemple.org/a#part", "Une"),
            _offer("https://exemple.org/a", "Deux"),
        ])
        self.assertEqual(len(kept), 1)
        self.assertEqual(kept[0].title, "Une")


if __name__ == "__main__":
    unittest.main()
