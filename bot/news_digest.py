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


def match_topic_id(store, title: str, limit: int = 80):
    """Trouve un news_topic existant par similarité Jaccard titre (ou None)."""
    tokens = normalize_tokens(title or "")
    if not tokens:
        return None
    try:
        topics = store.get_news_topics(limit=limit, offset=0)
    except Exception:
        return None
    best_id = None
    best_score = 0.0
    for t in topics:
        score = jaccard(tokens, normalize_tokens(t.get("title") or ""))
        if score > best_score:
            best_score = score
            best_id = t.get("id")
    if best_score >= JACCARD_CHAT_MATCH_THRESHOLD and best_id is not None:
        return int(best_id)
    return None


def summarize_prompt(topic_title: str, sources: List[dict]) -> str:
    numbered = "\n".join(
        f"[{i}] ({s.get('source') or 'source'}) {s.get('title','')} — {(s.get('description') or '')[:220]}"
        for i, s in enumerate(sources[:10], start=1)
    )
    return f"""Tu es un rédacteur d'actualités de Sentinelle. À partir des sources numérotées ci-dessous, rédige un article de synthèse original en français (style Perplexity).

SUJET : {topic_title}

SOURCES (seule vérité autorisée) :
{numbered}

Règles ABSOLUES :
1. Utilise UNIQUEMENT les faits présents dans les sources. N'invente rien.
2. Structure markdown : une phrase d'accroche directe (sans formule générique), puis 3 à 5 points clés en liste avec citations [n], puis une phrase de contexte.
3. Ne cite pas les noms des médias dans le corps du texte ; garde les [n] pour les sources.
4. Maximum 220 mots."""


def fallback_summary(topic_title: str, sources: List[dict]) -> str:
    lines = [f"{topic_title}", ""]
    for i, s in enumerate(sources[:8], start=1):
        desc = " ".join((s.get("description") or "").split())[:160]
        line = f"- **[{i}] {s.get('title','')}**"
        if desc:
            line += f" : {desc}"
        lines.append(line)
    lines.append("")
    lines.append("Synthèse générée à partir des sources listées ci-dessous.")
    return "\n".join(lines)


def collect_news_articles() -> List[dict]:
    """Collecte brute multi-catégories (une passe sur les flux d'actualité)."""
    from scrapers.news import NewsScraper

    scraper = NewsScraper()
    articles: List[dict] = []
    seen_urls = set()
    for offer in scraper.run():
        url = str(getattr(offer, "url", "") or "").strip()
        if not url or url in seen_urls:
            continue
        seen_urls.add(url)
        articles.append({
            "title": getattr(offer, "title", ""),
            "url": url,
            "source": getattr(offer, "provider", "") or "",
            "scrape_source": getattr(offer, "source", "") or "",
            "image": getattr(offer, "image_url", "") or "",
            "description": getattr(offer, "description", "") or "",
            "keywords_matched": list(getattr(offer, "keywords_matched", None) or []),
        })
    return articles


def run_news_cycle(store=None) -> dict:
    """Un cycle complet : collect → cluster → nouveaux sujets → synthèses.

    Plafond MAX_TOPICS_PER_CYCLE synthèses par cycle (tous statuts confondus) ;
    les sujets non synthétisés restent `pending` et sont traités en priorité au
    cycle suivant. Ne lève JAMAIS d'exception (log seulement).
    """
    if store is None:
        from bot.store import Store
        store = Store()

    stats = {"collected": 0, "new_topics": 0, "summarized": 0, "pending": 0}
    try:
        articles = collect_news_articles()
        stats["collected"] = len(articles)
        if not articles:
            return stats

        # 1. Traiter d'abord la file des sujets en attente de synthèse
        pending_budget = MAX_TOPICS_PER_CYCLE
        try:
            for topic in store.get_pending_summary_topics(limit=pending_budget):
                _generate_and_store_summary(store, topic)
                stats["summarized"] += 1
                pending_budget -= 1
                if pending_budget <= 0:
                    break
        except Exception as e:
            log.warning("[news_digest] file pending : %s", e)

        # 2. Clusters et nouveaux sujets
        clusters = cluster_topics(articles)
        for cluster in clusters:
            rec = build_topic_record(cluster)
            try:
                is_new = store.upsert_news_topic(rec)
            except Exception as e:
                log.warning("[news_digest] upsert sujet : %s", e)
                continue
            if is_new:
                stats["new_topics"] += 1

        # 3. Synthèse des nouveaux (plafonnée), les autres restent pending
        if pending_budget > 0:
            try:
                for topic in store.get_pending_summary_topics(limit=pending_budget):
                    _generate_and_store_summary(store, topic)
                    stats["summarized"] += 1
                    pending_budget -= 1
                    if pending_budget <= 0:
                        break
            except Exception as e:
                log.warning("[news_digest] synthèse nouveaux : %s", e)

        try:
            stats["pending"] = store.count_pending_summary_topics()
        except Exception:
            pass
    except Exception as e:
        log.exception("[news_digest] cycle en échec : %s", e)
    return stats


def _generate_and_store_summary(store, topic: dict) -> None:
    """Génère la synthèse d'un sujet et la persiste ; retry au cycle suivant si échec."""
    from bot.ai import get_gemini_key, call_llm

    sources = topic.get("sources") or []
    title = topic.get("title") or ""
    summary = None
    try:
        if get_gemini_key():
            summary = call_llm(summarize_prompt(title, sources))
    except Exception as e:
        log.warning("[news_digest] LLM indisponible pour « %s » : %s", title[:60], e)
    if not summary:
        summary = fallback_summary(title, sources)
        status = "pending_summary" if not get_gemini_key() else "ready"
    else:
        status = "ready"
    store.update_news_topic_summary(topic["id"], summary, status)
