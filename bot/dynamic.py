"""Scraping dynamique en temps réel basé sur la requête de l'utilisateur."""
from __future__ import annotations

import logging
import urllib.parse
from typing import List, Optional
import requests
from bs4 import BeautifulSoup

from bot.models import Offer
from bot.store import Store
from bot.credibility.engine import score_offer
from bot.credibility.dna import offer_dna
from models import normalize_image_url, strip_cdata
from normalizer import classify, matches_keywords
from scrapers.base import BaseScraper
from config import DEFAULT_HEADERS, REQUEST_TIMEOUT, DYNAMIC_TIMEOUT
 
log = logging.getLogger(__name__)
 
 
class DynamicScraper(BaseScraper):
    name = "dynamic"

    def search_google_news(self, query: str, offer_type: str = "news") -> List[Offer]:
        """Recherche sur Google News RSS en temps réel pour une requête donnée."""
        time_filter = ""
        low_query = query.lower()
        if any(t in low_query for t in ["cette semaine", "derniers jours", "récent", "this week", "latest", "recent"]):
            time_filter = " when:7d"
        elif any(t in low_query for t in ["aujourd'hui", "ce jour", "today", "24h"]):
            time_filter = " when:1d"
            
        full_query = query
        if time_filter and time_filter not in low_query:
            full_query = f"{query}{time_filter}"

        escaped_query = urllib.parse.quote_plus(full_query)
        url = f"https://news.google.com/rss/search?q={escaped_query}&hl=fr&gl=FR&ceid=FR:fr"
        log.info("[dynamic] recherche Google News RSS: %s", url)
        
        xml_content = self.fetch_static(url, timeout=DYNAMIC_TIMEOUT)
        if not xml_content:
            return []

        soup = BeautifulSoup(xml_content, "xml")
        items = soup.find_all("item")
        offers: List[Offer] = []

        for item in items[:15]:  # Retourner max 15 résultats
            title = strip_cdata(item.find("title").get_text() if item.find("title") else "")
            link = item.find("link").get_text() if item.find("link") else ""
            desc_raw = item.find("description").get_text() if item.find("description") else ""
            desc_soup = BeautifulSoup(desc_raw, "html.parser")
            description = strip_cdata(desc_soup.get_text().replace("&nbsp;", " "))

            # Image : media:thumbnail / media:content / enclosure / itunes:image
            image_url = ""
            for tag_name in ("media:thumbnail", "media:content", "itunes:image"):
                media = item.find(tag_name)
                if media:
                    image_url = normalize_image_url(media.get("url", ""))
                    if image_url:
                        break

            provider = "Google News"
            if " - " in title:
                parts = title.rsplit(" - ", 1)
                title = parts[0]
                provider = parts[1]

            offers.append(Offer(
                title=title,
                url=link,
                source=f"dynamic_{offer_type}",
                provider=provider,
                offer_type=offer_type,
                description=description or "Aucun résumé disponible.",
                keywords_matched=[query],
                image_url=image_url,
            ))

        return offers

    def search_hacker_news(self, query: str) -> List[Offer]:
        """Recherche sur Hacker News (Algolia API) en temps réel."""
        escaped_query = urllib.parse.quote_plus(query)
        url = f"https://hn.algolia.com/api/v1/search?query={escaped_query}&tags=story&hitsPerPage=15"
        log.info("[dynamic] recherche HN: %s", url)
        
        try:
            resp = self.session.get(url, timeout=DYNAMIC_TIMEOUT)
            resp.raise_for_status()
            data = resp.json()
            out: List[Offer] = []
            for hit in data.get("hits", []):
                title = hit.get("title") or ""
                link = hit.get("url") or f"https://news.ycombinator.com/item?id={hit.get('objectID')}"
                blob = (title + " " + hit.get("story_text", "")).lower()
                kw = matches_keywords(blob) or [query]
                
                out.append(Offer(
                    title=title,
                    url=link,
                    source="hackernews",
                    provider="Hacker News",
                    offer_type=classify(blob) or "promotion",
                    description=f"HN • {hit.get('points', 0)} pts • {hit.get('num_comments', 0)} commentaires",
                    keywords_matched=kw,
                ))
            return out
        except Exception as e:
            log.warning("[dynamic] Échec recherche HN : %s", e)
            return []

    def search_reddit(self, query: str) -> List[Offer]:
        """Recherche sur Reddit en temps réel."""
        escaped_query = urllib.parse.quote_plus(query)
        url = f"https://www.reddit.com/search.json?q={escaped_query}&limit=15"
        log.info("[dynamic] recherche Reddit: %s", url)
        
        try:
            resp = self.session.get(url, timeout=DYNAMIC_TIMEOUT)
            resp.raise_for_status()
            data = resp.json()
            children = data.get("data", {}).get("children", [])
            out: List[Offer] = []
            for c in children:
                d = c.get("data", {})
                title = d.get("title", "")
                blob = (title + " " + d.get("selftext", "")).lower()
                kw = matches_keywords(blob) or [query]
                
                permalink = d.get("permalink")
                link = f"https://www.reddit.com{permalink}" if permalink else d.get("url", "")
                
                out.append(Offer(
                    title=title,
                    url=link,
                    source="reddit",
                    provider=f"r/{d.get('subreddit', 'all')}",
                    offer_type=classify(blob) or "promotion",
                    description=f"Reddit • score {d.get('score', 0)}",
                    keywords_matched=kw,
                ))
            return out
        except Exception as e:
            log.warning("[dynamic] Échec recherche Reddit : %s", e)
            return []


def convert_to_bot_offer(raw) -> Offer:
    """Convertit une offre racine (scrapers) en offre bot v2."""
    if hasattr(raw, "first_seen"):
        return raw  # Déjà une offre bot v2
    
    o = Offer(
        title=getattr(raw, "title", ""),
        url=getattr(raw, "url", ""),
        source=getattr(raw, "source", "") or "dynamic",
        agent="dynamic",
        provider=getattr(raw, "provider", "Inconnu"),
        offer_type=getattr(raw, "offer_type", "unknown"),
        network="web",
        description=getattr(raw, "description", ""),
        keywords_matched=getattr(raw, "keywords_matched", []),
        image_url=getattr(raw, "image_url", ""),
    )
    return o


def _filter_news_by_query(offers: List[Offer], query: str) -> List[Offer]:
    """Conserve les articles dont le titre/description overlapent les tokens de la query."""
    from bot.news_digest import normalize_tokens, jaccard

    q_tokens = normalize_tokens(query or "")
    if not q_tokens or not offers:
        return offers
    scored = []
    for o in offers:
        blob = f"{getattr(o, 'title', '')} {getattr(o, 'description', '')}"
        t = normalize_tokens(blob)
        score = jaccard(q_tokens, t)
        if score <= 0 and any(tok in blob.lower() for tok in q_tokens):
            score = 0.15
        if score > 0:
            scored.append((score, o))
    if not scored:
        return offers[:8]
    scored.sort(key=lambda x: x[0], reverse=True)
    return [o for _, o in scored]


def run_dynamic_scrape(
    store: Store,
    intent: str,
    query: str,
    category: Optional[str] = None,
    specificity: Optional[str] = None,
) -> List[Offer]:
    """Exécute un scraping en temps réel selon l'intention et la requête de l'utilisateur."""
    scraper = DynamicScraper()
    raw_offers: List[Offer] = []
    
    if intent == "search_jobs":
        from scrapers.jobs import JobsScraper
        from bot.user_profile import get_profile_store
        from bot.opportunities import score_offer as score_opp_offer
        profile = get_profile_store().get()
        # 1. Google News avec thématique emploi (+ profil)
        loc_bits = []
        if profile.get("city"):
            loc_bits.append(profile["city"])
        if profile.get("country"):
            loc_bits.append(profile["country"])
        loc = " ".join(loc_bits)
        news_q = f'"{query}" + ("recrutement" OR "offre d\'emploi" OR "recrute" OR "job" OR "hiring" OR stage)'
        if loc:
            news_q += f" {loc}"
        raw_offers.extend(scraper.search_google_news(news_q, "job"))
        # 2. APIs jobs + matching profil
        general_jobs = JobsScraper().run(
            query=query,
            city=profile.get("city"),
            country=profile.get("country"),
            skills=profile.get("skills") or [],
        )
        low_query = query.lower()
        filtered_jobs = []
        for j in general_jobs:
            blob = f"{j.title} {j.description} {' '.join(j.keywords_matched or [])}".lower()
            if low_query in blob or score_opp_offer(j, profile, {"q": query}) >= 4:
                filtered_jobs.append(j)
        raw_offers.extend(filtered_jobs or general_jobs[:15])

    elif intent == "search_scholarships":
        from scrapers.opportunities_extra import ScholarshipScraper
        from bot.user_profile import get_profile_store
        profile = get_profile_store().get()
        raw_offers.extend(ScholarshipScraper().run(query=query, country=profile.get("country")))

    elif intent == "search_travel":
        from scrapers.opportunities_extra import TravelOppScraper
        from bot.user_profile import get_profile_store
        profile = get_profile_store().get()
        raw_offers.extend(TravelOppScraper().run(query=query, country=profile.get("country")))

    elif intent == "search_events":
        from scrapers.opportunities_extra import EventsScraper
        from bot.user_profile import get_profile_store
        profile = get_profile_store().get()
        interest = (profile.get("interests") or ["tech"])[0] if profile.get("interests") else "tech"
        raw_offers.extend(EventsScraper().run(
            query=query,
            interest=interest,
            city=profile.get("city"),
        ))
        
    elif intent == "search_news":
        from scrapers.news import NEWS_SOURCES, NewsScraper
        is_specific = (specificity or "").lower() == "specific"
        if is_specific and query:
            # Question ciblée : Google News sur les mots-clés + filtrage Jaccard
            raw_offers.extend(scraper.search_google_news(query, "news"))
            if category in NEWS_SOURCES:
                raw_offers.extend(NewsScraper().run_category(category))
            raw_offers = _filter_news_by_query(raw_offers, query)
        elif category in NEWS_SOURCES:
            raw_offers.extend(NewsScraper().run_category(category))
        else:
            raw_offers.extend(scraper.search_google_news(query, "news"))
        try:
            from bot.og_image import enrich_with_og_images
            enrich_with_og_images(raw_offers)
        except Exception as e_img:
            log.debug("[dynamic] enrichissement og:image échoué : %s", e_img)
            
    elif intent == "search_offers":
        # Recherche d'offres promotionnelles sur HN, Reddit et Google News
        raw_offers.extend(scraper.search_hacker_news(query))
        raw_offers.extend(scraper.search_reddit(query))
        raw_offers.extend(scraper.search_google_news(f'"{query}" + ("promo" OR "gratuit" OR "free" OR "code" OR "réduction")', "promotion"))
        
    elif intent == "search_medical":
        # Scraping médical en temps réel : ClinicalTrials + PubMed
        from scrapers.medical import MedicalScraper
        from bot.medical_store import MedicalStore
        from bot.ai import generate_medical_cheat_sheet
        
        med_scraper = MedicalScraper()
        med_store = MedicalStore()
        
        trials = med_scraper.scrape_clinical_trials(query)
        papers = med_scraper.scrape_pubmed(query)
        
        for record in trials + papers:
            if record["source"] == "clinicaltrials":
                cheat = generate_medical_cheat_sheet(
                    record["title"],
                    record["summary"],
                    record["eligibility_criteria"] or "",
                    nct_id=record.get("nct_id"),
                    phase=record.get("phase"),
                    status=record.get("status"),
                    sponsor=record.get("sponsor"),
                )
                if cheat:
                    record["ai_cheat_sheet"] = cheat
            med_store.upsert_record(record)
            
            # Conversion pour l'affichage de l'assistant
            raw_offers.append(Offer(
                title=record["title"],
                url=record["url"],
                source=record["source"],
                provider=record["sponsor"],
                offer_type="medical",
                description=record["ai_cheat_sheet"] or record["summary"] or "",
                keywords_matched=[query, record["source"]]
            ))

    elif intent == "search_crypto":
        from scrapers.crypto import CryptoScraper
        raw_offers.extend(CryptoScraper().run(query))

    elif intent == "search_geopolitics":
        from scrapers.geopolitics import GeopoliticsScraper
        raw_offers.extend(GeopoliticsScraper().run(query))

    elif intent == "search_predictions":
        from scrapers.predictions import PredictionsScraper
        raw_offers.extend(PredictionsScraper().run(query))
            
    # Conversion bot v2, insertion DB et score
    converted: List[Offer] = []
    for raw in raw_offers:
        o = convert_to_bot_offer(raw)
        o.id = ""  # Forcer le recalcul de l'ID unique
        o.found_at = ""  # Initialiser found_at
        o.__post_init__()
        o.dna_hash = offer_dna(o)
        o.provenance = [{"source": o.source, "url": o.url, "alive": True}]
        
        # Enregistrer en base
        store.upsert_offer(o)
        store.add_provenance(o.dna_hash, o.source, o.url)
        
        # Calculer le score et tier
        score_offer(store, o)
        
        # Récupérer l'objet mis à jour depuis le store pour renvoyer des données cohérentes
        db_offer = store.get_offer(o.id)
        if db_offer:
            converted.append(db_offer)
            
    return converted
