"""Veille continue : collecte toutes les 15 min, regroupement en sujets,
synthèses IA multi-sources (type Perplexity), historique illimité.

Cycle : collect → cluster → filtrage des nouveaux sujets → synthèse des
10 premiers (plafond quota Gemini) → persistance ; les excédents passent en
file `pending` et sont traités en priorité au cycle suivant.
"""
from __future__ import annotations

import hashlib
import logging
import re
import unicodedata
from datetime import datetime, timezone
from typing import Dict, List

log = logging.getLogger(__name__)

MAX_TOPICS_PER_CYCLE = 10
MAX_ARTICLES_PER_TOPIC = 15
JACCARD_TOPIC_THRESHOLD = 0.35
JACCARD_DUP_THRESHOLD = 0.5
JACCARD_CHAT_MATCH_THRESHOLD = 0.28

_IA_KEYWORDS = {
    "ia", "ai", "llm", "openai", "anthropic", "claude", "chatgpt", "gpt",
    "gemini", "mistral", "huggingface", "hugging", "copilot", "midjourney",
    "stable", "diffusion", "neural", "transformer", "deepseek", "grok",
    "intelligence", "artificielle", "machine", "learning", "genai",
}

_CATEGORY_FROM_SOURCE = {
    "news_top": "top",
    "news_tech": "tech",
    "news_sport": "sport",
    "news_finance": "finance",
    "news_politic": "politic",
    "news_economie": "economie",
    "news_afrique": "afrique",
    "news_international": "international",
    "news_ia": "ia",
}

_STOPWORDS = {
    "le", "la", "les", "un", "une", "des", "de", "du", "et", "ou", "a", "à", "au",
    "aux", "en", "dans", "sur", "pour", "par", "avec", "sans", "sous", "ce", "cet",
    "cette", "ces", "son", "sa", "ses", "leur", "leurs", "est", "sont", "etre",
    "être", "ont", "avait", "plus", "moins", "que", "qui", "quoi", "dont", "apres",
    "après", "avant", "entre", "contre", "chez", "tout", "toute", "tous", "toutes",
    "the", "a", "an", "of", "in", "on", "for", "to", "with", "and", "or", "is",
    "are", "was", "were", "be", "been", "at", "by", "from", "as", "it", "its",
    "this", "that", "these", "those", "after", "before", "between", "his", "her",
    "new", "nouveau", "nouvelle", "vs", "pas", "ne", "plusieurs", "deux", "trois",
}


def normalize_tokens(text: str) -> set:
    """Minuscule, sans accents/ponctuation/stopwords → ensemble de tokens significatifs."""
    if not text:
        return set()
    txt = unicodedata.normalize("NFKD", str(text))
    txt = "".join(c for c in txt if not unicodedata.combining(c)).lower()
    words = re.findall(r"[a-z0-9]{3,}", txt)
    return {w for w in words if w not in _STOPWORDS}


def topic_signature(articles: List[dict]) -> str:
    """Hash stable du sujet : top 5 tokens triés de l'article le plus long."""
    best = max(articles, key=lambda a: len(a.get("title", "")), default={})
    tokens = sorted(normalize_tokens(best.get("title", "")))[:5]
    if not tokens:
        tokens = sorted(normalize_tokens(best.get("description", "")))[:5]
    raw = "|".join(tokens)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def jaccard(a: set, b: set) -> float:
    if not a or not b:
        return 0.0
    inter = len(a & b)
    if not inter:
        return 0.0
    return inter / len(a | b)


def cluster_topics(articles: List[dict]) -> List[dict]:
    """Regroupe les articles parlant de la même information.

    Deux articles sont liés si leurs titres partagent ≥ 35 % de tokens (Jaccard).
    Le média d'un article n'est jamais comparé à lui-même (pas d'auto-cluster
    sur le domaine). Cap : MAX_ARTICLES_PER_TOPIC par sujet.
    """
    clusters: List[dict] = []
    token_sets: List[set] = []

    for art in articles:
        title = str(art.get("title", "")).strip()
        if not title:
            continue
        tokens = normalize_tokens(title)
        if not tokens:
            continue

        placed = False
        for idx, cluster in enumerate(clusters):
            if len(cluster["articles"]) >= MAX_ARTICLES_PER_TOPIC:
                continue
            # Ne pas regrouper deux articles du même média sur simple similarité
            if art.get("source") and cluster["medias"].get(art.get("source")):
                continue
            if jaccard(tokens, token_sets[idx]) >= JACCARD_TOPIC_THRESHOLD:
                cluster["articles"].append(art)
                cluster["medias"][art.get("source") or "?"] = True
                token_sets[idx] = token_sets[idx] | tokens
                placed = True
                break
        if not placed:
            clusters.append({
                "articles": [art],
                "medias": {art.get("source") or "?": True},
            })
            token_sets.append(tokens)

    return clusters


def _article_to_source(a: dict) -> dict:
    return {
        "title": str(a.get("title", "")),
        "url": str(a.get("url", "")),
        "source": str(a.get("source") or a.get("provider") or ""),
        "image": str(a.get("image") or a.get("image_url") or ""),
    }


def infer_article_category(article: dict) -> str:
    """Catégorie d'un article : source scrapée, puis heuristique IA, sinon top."""
    scrape = str(article.get("scrape_source") or article.get("source") or "")
    if scrape in _CATEGORY_FROM_SOURCE:
        return _CATEGORY_FROM_SOURCE[scrape]
    # keywords_matched depuis NewsScraper
    for kw in article.get("keywords_matched") or []:
        k = str(kw).lower().strip()
        if k in (
            "top", "tech", "sport", "finance", "politic", "economie",
            "afrique", "international", "ia",
        ):
            return k
    blob = f"{article.get('title', '')} {article.get('description', '')}".lower()
    tokens = normalize_tokens(blob)
    if tokens & _IA_KEYWORDS or any(k in blob for k in (
        "intelligence artificielle", "openai", "anthropic", "hugging face",
        "large language", "chatgpt",
    )):
        return "ia"
    return "top"


def infer_topic_category(articles: List[dict]) -> str:
    """Catégorie majoritaire du cluster ; priorité à ia si présente."""
    cats = [infer_article_category(a) for a in articles]
    if "ia" in cats:
        return "ia"
    if not cats:
        return "top"
    # Majorité simple
    counts: Dict[str, int] = {}
    for c in cats:
        counts[c] = counts.get(c, 0) + 1
    return max(counts, key=counts.get)


def build_topic_record(cluster: dict) -> dict:
    """Construit un enregistrement de sujet à partir d'un cluster."""
    articles = cluster["articles"]
    best = max(articles, key=lambda a: len(a.get("title", "")))
    return {
        "signature": topic_signature(articles),
        "title": best.get("title", ""),
        "sources": [_article_to_source(a) for a in articles[:MAX_ARTICLES_PER_TOPIC]],
        "image": best.get("image") or best.get("image_url") or "",
        "source_count": len(articles),
        "first_seen": datetime.now(timezone.utc).isoformat(),
        "category": infer_topic_category(articles),
    }
