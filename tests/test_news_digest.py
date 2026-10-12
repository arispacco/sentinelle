"""Tests unitaires du regroupement de sujets d'actualité (sans réseau)."""
import unittest
from unittest import mock

from bot.news_digest import (
    cluster_topics,
    jaccard,
    normalize_tokens,
    topic_signature,
)


def art(title, source="Le Monde", url=None, image="", description=""):
    return {
        "title": title,
        "source": source,
        "url": url or f"https://example.com/{abs(hash(title))}",
        "image": image,
        "description": description,
    }


class TestNormalizeTokens(unittest.TestCase):
    def test_strip_accents_stopwords(self):
        toks = normalize_tokens("Les Élections américaines de 2026")
        self.assertIn("elections", toks)
        self.assertNotIn("les", toks)
        self.assertNotIn("de", toks)
        self.assertIn("2026", toks)

    def test_empty(self):
        self.assertEqual(normalize_tokens(""), set())
        self.assertEqual(normalize_tokens(None), set())


class TestJaccard(unittest.TestCase):
    def test_identical(self):
        self.assertEqual(jaccard({"a", "b"}, {"a", "b"}), 1.0)

    def test_disjoint(self):
        self.assertEqual(jaccard({"a"}, {"b"}), 0.0)


class TestClusterTopics(unittest.TestCase):
    def test_groups_same_story_different_medias(self):
        articles = [
            art("Anthropic entre en bourse à New York", "Le Monde"),
            art("Anthropic s'introduit en bourse à New York", "RFI"),
            art("Anthropic bourse New York introduction", "BBC"),
            art("Recette du pain au chocolat", "Le Figaro"),
        ]
        clusters = cluster_topics(articles)
        self.assertEqual(len(clusters), 2)
        sizes = sorted(len(c["articles"]) for c in clusters)
        self.assertEqual(sizes, [1, 3])

    def test_same_media_not_merged_on_similarity_alone(self):
        articles = [
            art("Tesla rappelle 2 millions de véhicules", "Clubic"),
            art("Tesla rappelle des millions de véhicules électriques", "Clubic"),
        ]
        clusters = cluster_topics(articles)
        # Même média : pas de fusion sur similarité seule
        self.assertEqual(len(clusters), 2)

    def test_cap_per_topic(self):
        from bot.news_digest import MAX_ARTICLES_PER_TOPIC
        articles = [
            art(f"Anthropic bourse introduction Wall Street variante {i}", f"Media{i}")
            for i in range(MAX_ARTICLES_PER_TOPIC + 5)
        ]
        clusters = cluster_topics(articles)
        self.assertLessEqual(len(clusters[0]["articles"]), MAX_ARTICLES_PER_TOPIC)


class TestTopicSignature(unittest.TestCase):
    def test_stable_regardless_of_order(self):
        a1 = [art("Tesla Semi camion électrique lancé", "A"), art("Tesla lance son camion", "B")]
        a2 = list(reversed(a1))
        self.assertEqual(topic_signature(a1), topic_signature(a2))

    def test_different_topics_different_signatures(self):
        a1 = [art("Anthropic entre en bourse", "A")]
        a2 = [art("Recette du pain au chocolat", "B")]
        self.assertNotEqual(topic_signature(a1), topic_signature(a2))


class TestCategoryIa(unittest.TestCase):
    def test_infer_ia_from_keywords(self):
        from bot.news_digest import infer_article_category, infer_topic_category, build_topic_record
        a = art("OpenAI lance un nouveau modèle GPT", "TechCrunch", description="LLM Anthropic vs OpenAI")
        a["keywords_matched"] = []
        self.assertEqual(infer_article_category(a), "ia")
        a2 = art("Match PSG-OM ce soir", "L'Équipe")
        a2["scrape_source"] = "news_sport"
        a2["keywords_matched"] = ["sport"]
        self.assertEqual(infer_article_category(a2), "sport")
        rec = build_topic_record({"articles": [a], "medias": {}})
        self.assertEqual(rec["category"], "ia")
        self.assertEqual(infer_topic_category([a, a2]), "ia")

    def test_scrape_source_ia(self):
        from bot.news_digest import infer_article_category
        a = art("Quelque chose de vague", "Clubic")
        a["scrape_source"] = "news_ia"
        self.assertEqual(infer_article_category(a), "ia")


class TestSpecificity(unittest.TestCase):
    def test_generic_vs_specific(self):
        from bot.ai import infer_specificity, parse_user_intent
        self.assertEqual(
            infer_specificity("Les news sur l'IA aujourd'hui", "search_news", "ia", "IA"),
            "generic",
        )
        self.assertEqual(
            infer_specificity("Parle-moi du modèle CIA qui fait sensation", "search_news", None, "modèle CIA"),
            "specific",
        )
        with mock.patch("bot.ai.get_gemini_key", return_value=None):
            r = parse_user_intent("Quoi de neuf aujourd'hui dans la tech")
            self.assertEqual(r["intent"], "search_news")
            self.assertEqual(r["specificity"], "generic")
            r2 = parse_user_intent("Parle-moi du modèle qui fait sensation en tech")
            self.assertEqual(r2["intent"], "search_news")
            self.assertEqual(r2["specificity"], "specific")

    def test_synthesize_specific_prompt_path(self):
        from bot.ai import synthesize_digest
        from bot.models import Offer
        import os
        o = Offer(
            title="Modèle CIA annoncé",
            url="https://example.com/cia",
            source="news_ia",
            provider="TechCrunch",
            offer_type="news",
            description="Le modèle CIA impressionne les labos.",
        )
        with mock.patch("bot.ai.get_gemini_key", return_value=None):
            with mock.patch.dict(os.environ, {"PREFER_OLLAMA": "false", "OLLAMA_HOST": ""}):
                text = synthesize_digest("Parle-moi du modèle CIA", [o], specificity="specific")
                self.assertIn("Modèle CIA", text)


if __name__ == "__main__":
    unittest.main()
