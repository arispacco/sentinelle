"""Catalogue des espaces thématiques + chargement des items scrapés."""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from bot.models import Offer

log = logging.getLogger(__name__)

SPACES: List[Dict[str, Any]] = [
    {
        "slug": "actualite",
        "title": "Actualité",
        "description": "À la une mondiale",
        "icon": "news",
        "news_category": "top",
        "disclaimer": None,
    },
    {
        "slug": "sport",
        "title": "Sport",
        "description": "Résultats et transferts",
        "icon": "sport",
        "news_category": "sport",
        "disclaimer": None,
    },
    {
        "slug": "finance",
        "title": "Finance",
        "description": "Marchés et économie",
        "icon": "finance",
        "news_category": "finance",
        "disclaimer": None,
    },
    {
        "slug": "crypto",
        "title": "Crypto",
        "description": "Prix et actus crypto",
        "icon": "crypto",
        "disclaimer": "Données marché indicatives (CoinGecko). Pas un conseil d'investissement.",
    },
    {
        "slug": "sante",
        "title": "Santé",
        "description": "Essais cliniques et publications",
        "icon": "health",
        "disclaimer": "Réservé aux professionnels. Ne remplace pas un avis médical.",
    },
    {
        "slug": "geopolitique",
        "title": "Géopolitique",
        "description": "Conflits et relations internationales",
        "icon": "geo",
        "disclaimer": "Synthèse multi-sources presse. Vérifiez toujours les sources primaires.",
    },
    {
        "slug": "predictions",
        "title": "Prédictions",
        "description": "Probabilités Polymarket",
        "icon": "predict",
        "disclaimer": "Marchés de prédiction publics. Pas un conseil financier ni d'incitation au pari.",
    },
]


def list_spaces() -> List[Dict[str, Any]]:
    return [{k: v for k, v in s.items() if k != "news_category"} for s in SPACES]


def get_space_meta(slug: str) -> Optional[Dict[str, Any]]:
    for s in SPACES:
        if s["slug"] == slug:
            return s
    return None


def _offer_dict(o: Offer) -> dict:
    d = o.to_dict() if hasattr(o, "to_dict") else {
        "title": o.title,
        "url": o.url,
        "source": o.source,
        "provider": o.provider,
        "offer_type": o.offer_type,
        "description": o.description,
        "image_url": getattr(o, "image_url", ""),
        "keywords_matched": getattr(o, "keywords_matched", []),
    }
    if "image_url" not in d:
        d["image_url"] = getattr(o, "image_url", "")
    return d


def load_space_items(slug: str, query: Optional[str] = None, limit: int = 24) -> Dict[str, Any]:
    meta = get_space_meta(slug)
    if not meta:
        return {"error": "unknown_space", "items": []}

    items: List[dict] = []
    try:
        if slug in ("actualite", "sport", "finance"):
            from scrapers.news import NewsScraper
            cat = meta.get("news_category") or "top"
            offers = NewsScraper().run_category(cat)
            if query:
                q = query.lower()
                offers = [o for o in offers if q in o.title.lower() or q in (o.description or "").lower()]
            items = [_offer_dict(o) for o in offers[:limit]]

        elif slug == "crypto":
            from scrapers.crypto import CryptoScraper
            items = [_offer_dict(o) for o in CryptoScraper().run(query)[:limit]]

        elif slug == "geopolitique":
            from scrapers.geopolitics import GeopoliticsScraper
            items = [_offer_dict(o) for o in GeopoliticsScraper().run(query)[:limit]]

        elif slug == "predictions":
            from scrapers.predictions import PredictionsScraper
            items = PredictionsScraper().markets_as_dicts(limit=limit, query=query)[:limit]

        elif slug == "sante":
            from bot.medical_store import MedicalStore
            store = MedicalStore()
            records = store.get_records(search=query, limit=limit)
            for r in records:
                items.append({
                    "id": r.get("id"),
                    "title": r.get("title"),
                    "url": r.get("url"),
                    "source": r.get("source"),
                    "provider": r.get("sponsor") or "Santé",
                    "offer_type": "medical",
                    "description": r.get("ai_cheat_sheet") or r.get("summary") or "",
                    "status": r.get("status"),
                    "phase": r.get("phase"),
                    "image_url": "https://images.unsplash.com/photo-1576091160399-112ba8d25d1d?w=700&auto=format&fit=crop&q=80",
                })
            # Si vide et query : scrape live
            if not items and query:
                from scrapers.medical import MedicalScraper
                scraper = MedicalScraper()
                for rec in (scraper.scrape_clinical_trials(query) + scraper.scrape_pubmed(query))[:limit]:
                    store.upsert_record(rec)
                    items.append({
                        "id": rec.get("id"),
                        "title": rec.get("title"),
                        "url": rec.get("url"),
                        "source": rec.get("source"),
                        "provider": rec.get("sponsor") or "Santé",
                        "offer_type": "medical",
                        "description": rec.get("summary") or "",
                        "image_url": "https://images.unsplash.com/photo-1576091160399-112ba8d25d1d?w=700&auto=format&fit=crop&q=80",
                    })
            store.close()
    except Exception as e:
        log.exception("[spaces] échec chargement %s : %s", slug, e)
        return {
            "slug": slug,
            "title": meta["title"],
            "description": meta["description"],
            "disclaimer": meta.get("disclaimer"),
            "items": [],
            "error": str(e),
        }

    return {
        "slug": slug,
        "title": meta["title"],
        "description": meta["description"],
        "disclaimer": meta.get("disclaimer"),
        "items": items,
    }
