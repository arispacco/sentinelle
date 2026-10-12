"""Scraper d'actualités via Google News RSS pour différentes thématiques."""
from __future__ import annotations

import logging
import html
import warnings
from typing import List
from bs4 import BeautifulSoup, XMLParsedAsHTMLWarning

warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)

from models import Offer, strip_cdata
from scrapers.base import BaseScraper

log = logging.getLogger(__name__)

# Timeouts courts : un flux qui traîne est abandonné, pas bloquant.
FEED_TIMEOUT = 5

# Parser XML (lxml) : lit correctement <link> (html.parser la traite comme
# balise void et renvoie un texte vide) et gère nativement les CDATA.
SOUP_FEATURES = "xml"

NEWS_SOURCES = {
    "top": {
        "name": "Top Actu",
        "urls": [
            ("https://www.francetvinfo.fr/titres.rss", "France Info"),
            ("https://www.lefigaro.fr/rss/figaro_flash-actu.xml", "Le Figaro"),
            ("https://www.lemonde.fr/rss/une.xml", "Le Monde"),
            ("https://feeds.bbci.co.uk/news/world/rss.xml", "BBC World"),
            ("https://www.aljazeera.com/xml/rss/all.xml", "Al Jazeera"),
            ("https://news.google.com/rss?hl=fr&gl=FR&ceid=FR:fr", None),
        ],
    },
    "tech": {
        "name": "Actu Tech",
        "urls": [
            ("https://www.clubic.com/feed/news.rss", "Clubic"),
            ("https://www.01net.com/actualites/feed/", "01Net"),
            ("https://techcrunch.com/feed/", "TechCrunch"),
            ("https://feeds.arstechnica.com/arstechnica/index/", "Ars Technica"),
            ("https://news.google.com/rss/headlines/section/topic/TECHNOLOGY?hl=fr&gl=FR&ceid=FR:fr", None),
        ],
    },
    "sport": {
        "name": "Actu Sport",
        "urls": [
            ("https://www.francetvinfo.fr/sports.rss", "France Info"),
            ("https://news.google.com/rss/headlines/section/topic/SPORTS?hl=fr&gl=FR&ceid=FR:fr", None),
        ],
    },
    "finance": {
        "name": "Actu Finance",
        "urls": [
            ("https://www.bfmtv.com/rss/economie/", "BFM Business"),
            ("https://www.challenges.fr/rss.xml", "Challenges"),
            ("https://www.theguardian.com/uk/business/rss", "The Guardian"),
            ("https://news.google.com/rss/headlines/section/topic/BUSINESS?hl=fr&gl=FR&ceid=FR:fr", None),
        ],
    },
    "politic": {
        "name": "Actu Politique",
        "urls": [
            ("https://www.francetvinfo.fr/politique.rss", "France Info"),
            ("https://www.lefigaro.fr/rss/figaro_politique.xml", "Le Figaro"),
            ("https://news.google.com/rss/search?q=politique&hl=fr&gl=FR&ceid=FR:fr", None),
        ],
    },
    "economie": {
        "name": "Actu Économie",
        "urls": [
            ("https://www.bfmtv.com/rss/economie/", "BFM Business"),
            ("https://www.lemonde.fr/economie/rss_full.xml", "Le Monde"),
            ("https://news.google.com/rss/search?q=economie&hl=fr&gl=FR&ceid=FR:fr", None),
        ],
    },
    "afrique": {
        "name": "Actu Afrique",
        "urls": [
            ("https://www.jeuneafrique.com/feed/", "Jeune Afrique"),
            ("https://www.rfi.fr/fr/afrique/rss", "RFI"),
            ("https://feeds.bbci.co.uk/afrique/rss.xml", "BBC Afrique"),
            ("https://www.france24.com/fr/afrique/rss", "France 24"),
            ("https://news.google.com/rss/search?q=Afrique+OR+Afrique+centrale+OR+Sahel&hl=fr&gl=FR&ceid=FR:fr", None),
        ],
    },
    "international": {
        "name": "Actu Internationale",
        "urls": [
            ("https://www.lemonde.fr/international/rss_full.xml", "Le Monde"),
            ("https://www.lefigaro.fr/rss/figaro_international.xml", "Le Figaro"),
            ("https://feeds.bbci.co.uk/news/world/rss.xml", "BBC World"),
            ("https://news.google.com/rss/search?q=%22Courrier%20International%22&hl=fr&gl=FR&ceid=FR:fr", "Courrier International"),
            ("https://www.aljazeera.com/xml/rss/all.xml", "Al Jazeera"),
        ],
    },
    "ia": {
        "name": "Actu IA",
        "urls": [
            ("https://www.clubic.com/feed/tag/intelligence-artificielle.rss", "Clubic"),
            ("https://techcrunch.com/category/artificial-intelligence/feed/", "TechCrunch"),
            ("https://news.google.com/rss/search?q=intelligence+artificielle+OR+AI+OR+LLM&hl=fr&gl=FR&ceid=FR:fr", None),
        ],
    },
}

CATEGORY_FALLBACK_IMAGES = {
    "top": "https://images.unsplash.com/photo-1585829365295-ab7cd400c167?w=700&auto=format&fit=crop&q=80",
    "tech": "https://images.unsplash.com/photo-1518770660439-4636190af475?w=700&auto=format&fit=crop&q=80",
    "sport": "https://images.unsplash.com/photo-1461896836934-ffe607ba8211?w=700&auto=format&fit=crop&q=80",
    "finance": "https://images.unsplash.com/photo-1611974789855-9c2a0a7236a3?w=700&auto=format&fit=crop&q=80",
    "politic": "https://images.unsplash.com/photo-1541872703-74c5e44368f9?w=700&auto=format&fit=crop&q=80",
    "economie": "https://images.unsplash.com/photo-1590283603385-17ffb3a7f29f?w=700&auto=format&fit=crop&q=80",
    "afrique": "https://images.unsplash.com/photo-1516026672322-bc52d61a55d5?w=700&auto=format&fit=crop&q=80",
    "international": "https://images.unsplash.com/photo-1526778548025-fa2f459cd5c1?w=700&auto=format&fit=crop&q=80",
    "ia": "https://images.unsplash.com/photo-1677442136019-21780efad99a?w=700&auto=format&fit=crop&q=80",
}


class NewsScraper(BaseScraper):
    name = "news"

    def _extract_image_from_item(self, item, desc_soup, category: str) -> str:
        """Extrait l'image réelle de l'article depuis les balises RSS standard."""
        # 1. Balise enclosure standard RSS (France Info, Le Figaro, Les Echos...)
        enc = item.find("enclosure")
        if enc and enc.get("url"):
            url = enc["url"]
            if any(ext in url.lower() for ext in [".jpg", ".jpeg", ".png", ".webp", "image"]):
                return url

        # 2. Balise media:content ou media:thumbnail (Clubic, 01Net, TechCrunch...)
        for tag_name in ["media:content", "media:thumbnail"]:
            media = item.find(tag_name)
            if media and media.get("url"):
                return media["url"]

        # 3. Balise <img> dans la description HTML
        img = desc_soup.find("img")
        if img and img.get("src"):
            src = img["src"]
            if src.startswith("http"):
                return src

        # 4. Balise content:encoded
        content_enc = item.find("content:encoded")
        if content_enc:
            c_soup = BeautifulSoup(content_enc.get_text(), "html.parser")
            c_img = c_soup.find("img")
            if c_img and c_img.get("src") and c_img["src"].startswith("http"):
                return c_img["src"]

        return CATEGORY_FALLBACK_IMAGES.get(category, CATEGORY_FALLBACK_IMAGES["top"])

    def run_category(self, category: str) -> List[Offer]:
        """Scrape une catégorie d'actualités avec extraction des vraies images d'articles."""
        cfg = NEWS_SOURCES.get(category)
        if not cfg:
            log.warning("[news] Catégorie inconnue : %s", category)
            return []

        urls = cfg.get("urls") or []
        offers: List[Offer] = []
        seen_titles = set()

        # Rotation : au plus 2 items consécutifs du même flux, pour garantir
        # la diversité des médias quand un flux publie en rafale.
        per_feed_quota: dict = {}
        max_per_feed = 3

        for entry in urls:
            if len(offers) >= 20:
                break
            url, default_provider = (entry if isinstance(entry, tuple) else (entry, None))
            try:
                xml_content = self.fetch_static(url, timeout=FEED_TIMEOUT)
                if not xml_content:
                    continue

                soup = BeautifulSoup(xml_content, SOUP_FEATURES)
                items = soup.find_all("item")

                for item in items:
                    if len(offers) >= 20:
                        break
                    if per_feed_quota.get(url, 0) >= max_per_feed:
                        break

                    raw_title = item.find("title").get_text() if item.find("title") else ""
                    title = strip_cdata(raw_title)
                    if not title or title.lower() in seen_titles:
                        continue
                    seen_titles.add(title.lower())

                    link = item.find("link").get_text() if item.find("link") else ""

                    desc_raw = item.find("description").get_text() if item.find("description") else ""
                    desc_soup = BeautifulSoup(desc_raw, "html.parser")
                    description = strip_cdata(desc_soup.get_text().replace("&nbsp;", " "))

                    # Déterminer le média
                    provider = default_provider or cfg["name"]
                    if " - " in title and not default_provider:
                        parts = title.rsplit(" - ", 1)
                        title, provider = parts[0].strip(), parts[1].strip()
                    elif "francetvinfo" in url:
                        provider = "France Info"
                    elif "lefigaro" in url:
                        provider = "Le Figaro"
                    elif "lemonde" in url:
                        provider = "Le Monde"
                    elif "clubic" in url:
                        provider = "Clubic"
                    elif "01net" in url:
                        provider = "01Net"
                    elif "bfmtv" in url:
                        provider = "BFM Business"
                    elif "challenges.fr" in url:
                        provider = "Challenges"

                    # Extraire l'image réelle
                    img_url = self._extract_image_from_item(item, desc_soup, category)

                    offers.append(Offer(
                        title=title,
                        url=link,
                        source=f"news_{category}",
                        provider=provider,
                        offer_type="news",
                        description=description or "Consultez l'article complet pour tous les détails de cette actualité.",
                        keywords_matched=[category],
                        image_url=img_url,
                    ))
                    per_feed_quota[url] = per_feed_quota.get(url, 0) + 1
            except Exception as e:
                # Flux en échec : on skippe silencieusement (log) et on continue.
                log.warning("[news] Erreur sur flux %s: %s", url, e)

        return offers

    def run(self) -> List[Offer]:
        """Parcourt toutes les thématiques configurées."""
        offers: List[Offer] = []
        for cat in NEWS_SOURCES.keys():
            try:
                offers.extend(self.run_category(cat))
            except Exception as e:
                log.exception("[news] Erreur sur la catégorie %s: %s", cat, e)
        log.info("[news] %d actualités trouvées au total", len(offers))
        return offers
