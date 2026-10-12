"""Matching et agrégation d'opportunités (jobs, bourses, voyages, events)."""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from bot.user_profile import get_profile_store

log = logging.getLogger(__name__)


def _offer_blob(o) -> str:
    return " ".join([
        getattr(o, "title", "") or "",
        getattr(o, "description", "") or "",
        getattr(o, "provider", "") or "",
        " ".join(getattr(o, "keywords_matched", None) or []),
    ]).lower()


def _clean(o) -> str:
    """Description nettoyée (boilerplate dédupliqué) prête pour la carte."""
    from normalizer import clean_description
    return clean_description(getattr(o, "description", "") or "", max_len=320)


def score_offer(o, profile: dict, filters: Optional[dict] = None) -> float:
    filters = filters or {}
    blob = _offer_blob(o)
    score = 1.0
    skills = [s.lower() for s in (profile.get("skills") or []) if s]
    interests = [s.lower() for s in (profile.get("interests") or []) if s]
    companies = [s.lower() for s in (profile.get("preferred_companies") or []) if s]
    city = (filters.get("city") or profile.get("city") or "").lower()
    country = (filters.get("country") or profile.get("country") or "").lower()
    company = (filters.get("company") or "").lower()
    q = (filters.get("q") or "").lower()

    for s in skills:
        if s and s in blob:
            score += 3.0
    for i in interests:
        if i and i in blob:
            score += 2.0
    for c in companies:
        if c and c in blob:
            score += 4.0
    if city and city in blob:
        score += 2.5
    if country and country in blob:
        score += 2.0
    if company and company in blob:
        score += 4.0
    if q and q in blob:
        score += 3.0
    return score


def offer_to_item(o, score: float = 0.0) -> dict:
    if hasattr(o, "to_dict"):
        raw = o.to_dict()
    else:
        raw = {
            "title": getattr(o, "title", ""),
            "url": getattr(o, "url", ""),
            "source": getattr(o, "source", ""),
            "provider": getattr(o, "provider", ""),
            "offer_type": getattr(o, "offer_type", ""),
            "description": getattr(o, "description", ""),
            "keywords_matched": getattr(o, "keywords_matched", []) or [],
            "image_url": getattr(o, "image_url", ""),
        }
    return {
        "title": raw.get("title"),
        "url": raw.get("url"),
        "source": raw.get("source"),
        "provider": raw.get("provider"),
        "offer_type": raw.get("offer_type"),
        "description": _clean(o),
        "keywords_matched": raw.get("keywords_matched") or [],
        "image_url": raw.get("image_url") or getattr(o, "image_url", "") or "",
        "match_score": round(score, 2),
    }


def collect_opportunities(
    opp_type: Optional[str] = None,
    q: Optional[str] = None,
    city: Optional[str] = None,
    country: Optional[str] = None,
    company: Optional[str] = None,
    limit: int = 40,
    profile: Optional[dict] = None,
) -> List[dict]:
    profile = profile or get_profile_store().get()
    filters = {"q": q, "city": city, "country": country, "company": company}
    raw: List[Any] = []

    types = [opp_type] if opp_type else ["job", "scholarship", "travel", "event"]
    for t in types:
        try:
            if t == "job":
                from scrapers.jobs import JobsScraper
                jobs = JobsScraper().run(
                    query=q,
                    city=city or profile.get("city"),
                    country=country or profile.get("country"),
                    company=company,
                    skills=profile.get("skills") or [],
                )
                if q or city or company:
                    from bot.dynamic import DynamicScraper
                    query_bits = [q or "emploi", "recrutement"]
                    if city:
                        query_bits.append(city)
                    if company:
                        query_bits.append(company)
                    raw.extend(DynamicScraper().search_google_news(" ".join(query_bits), "job"))
                raw.extend(jobs)
            elif t == "scholarship":
                from scrapers.opportunities_extra import ScholarshipScraper
                raw.extend(ScholarshipScraper().run(query=q, country=country or profile.get("country")))
            elif t == "travel":
                from scrapers.opportunities_extra import TravelOppScraper
                raw.extend(TravelOppScraper().run(query=q, country=country or profile.get("country")))
            elif t == "event":
                from scrapers.opportunities_extra import EventsScraper
                interest = (profile.get("interests") or ["tech"])[0] if profile.get("interests") else "tech"
                raw.extend(EventsScraper().run(query=q, interest=interest, city=city or profile.get("city")))
        except Exception as e:
            log.warning("[opportunities] échec type %s : %s", t, e)

    seen = set()
    scored = []
    for o in raw:
        key = (getattr(o, "url", None) or getattr(o, "title", None) or "").strip()
        if not key or key in seen:
            continue
        seen.add(key)
        ot = getattr(o, "offer_type", "") or ""
        if opp_type == "job" and ot and ot not in ("job", "news"):
            continue
        if opp_type and opp_type != "job" and ot and ot != opp_type:
            continue
        sc = score_offer(o, profile, filters)
        if q and q.lower() not in _offer_blob(o) and sc < 4:
            continue
        scored.append((sc, o))

    scored.sort(key=lambda x: x[0], reverse=True)
    items = [offer_to_item(o, sc) for sc, o in scored[:limit]]
    # Enrichissement og:image des items sans vignette (cache en base via Store).
    try:
        from bot.og_image import enrich_with_og_images
        from bot.store import Store
        enrich_with_og_images(items, store=Store())
    except Exception as e_img:
        log.debug("[opportunities] enrichissement og:image échoué : %s", e_img)
    return items


def feed_for_profile(limit: int = 20) -> List[dict]:
    profile = get_profile_store().get()
    items: List[dict] = []
    for t in ("job", "scholarship", "travel", "event"):
        items.extend(collect_opportunities(opp_type=t, limit=8, profile=profile))
    items.sort(key=lambda x: x.get("match_score", 0), reverse=True)
    seen = set()
    out = []
    for it in items:
        k = it.get("url") or it.get("title")
        if k in seen:
            continue
        seen.add(k)
        out.append(it)
        if len(out) >= limit:
            break
    return out
