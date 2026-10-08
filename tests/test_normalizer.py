"""Mots-clés et classification des textes d'offres."""
from __future__ import annotations

import unittest

from normalizer import classify, matches_keywords


class TestMatchesKeywords(unittest.TestCase):
    def test_empty_text(self):
        self.assertEqual(matches_keywords(""), [])

    def test_finds_free_and_course(self):
        found = matches_keywords("A free course this week")
        self.assertIn("free", found)
        self.assertIn("course", found)


class TestClassify(unittest.TestCase):
    def test_empty_text(self):
        self.assertIsNone(classify(""))

    def test_free_tier_beats_training(self):
        self.assertEqual(classify("free tier for the API"), "free_tier")

    def test_course_is_training(self):
        self.assertEqual(classify("an online course"), "training")

    def test_certificate_is_certification(self):
        self.assertEqual(classify("a professional certificate"), "certification")


if __name__ == "__main__":
    unittest.main()
