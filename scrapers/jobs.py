"""Scraper d'offres d'emploi (RemoteOK, Arbeitnow, Google News)."""
from __future__ import annotations

import logging
import warnings
from typing import List
import urllib.parse

from bs4 import BeautifulSoup, XMLParsedAsHTMLWarning
warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)

from models import Offer, normalize_image_url
from normalizer import clean_description
from scrapers.base import BaseScraper
from config import REQUEST_TIMEOUT

log = logging.getLogger(__name__)


class JobsScraper(BaseScraper):
    name = "jobs"

    def run(
        self,
        query: str | None = None,
        city: str | None = None,
        country: str | None = None,
        company: str | None = None,
        skills: list | None = None,
    ) -> List[Offer]:
        offers: List[Offer] = []
        offers.extend(self._scrape_remoteok())
        offers.extend(self._scrape_arbeitnow())
        offers.extend(self._scrape_google_news_jobs(query=query, city=city, country=country, company=company))
        needles = [x.lower() for x in ([query, city, country, company] + list(skills or [])) if x]
        if needles:
            filtered = []
            for o in offers:
                blob = f"{o.title} {o.description} {o.provider} {' '.join(o.keywords_matched or [])}".lower()
                if any(n in blob for n in needles):
                    filtered.append(o)
            # si trop strict, garder le lot brut (le scoring profil fera le tri)
            if filtered:
                offers = filtered
        log.info("[jobs] %d offres d'emploi trouvées au total", len(offers))
        return offers

    def _scrape_remoteok(self) -> List[Offer]:
        out: List[Offer] = []
        url = "https://remoteok.com/api"
        log.info("[jobs] scraping RemoteOK API")
        try:
            resp = self.session.get(url, timeout=REQUEST_TIMEOUT)
            resp.raise_for_status()
            data = resp.json()
            # Le premier élément est un objet légal/info, les autres sont des offres
            if isinstance(data, list) and len(data) > 1:
                for item in data[1:30]:  # Limiter à 30 offres
                    title = item.get("position", "")
                    company = item.get("company", "Inconnu")
                    apply_url = item.get("url", "")
                    description = item.get("description", "")
                    logo = normalize_image_url(item.get("logo", ""))

                    # Nettoyer l'HTML de la description de RemoteOK
                    if description:
                        description = BeautifulSoup(description, "html.parser").get_text()
                        description = description.replace("&nbsp;", " ").strip()

                    tags = item.get("tags", [])
                    logo = normalize_image_url(
                        item.get("logo") or item.get("company_logo") or ""
                    )

                    out.append(Offer(
                        title=f"{title} @ {company}",
                        url=apply_url,
                        source="remoteok",
                        provider=company,
                        offer_type="job",
                        description=clean_description(description) or "Offre d'emploi RemoteOK",
                        keywords_matched=tags[:5],
                        image_url=logo,
                    ))
        except Exception as e:
            log.warning("[jobs] Échec de la récupération de RemoteOK : %s", e)
        return out

    def _scrape_arbeitnow(self) -> List[Offer]:
        out: List[Offer] = []
        url = "https://www.arbeitnow.com/api/job-board-api"
        log.info("[jobs] scraping Arbeitnow API")
        try:
            resp = self.session.get(url, timeout=REQUEST_TIMEOUT)
            resp.raise_for_status()
            data = resp.json()
            jobs = data.get("data", [])
            for item in jobs[:30]:  # Limiter à 30 offres
                title = item.get("title", "")
                company = item.get("company_name", "Inconnu")
                apply_url = item.get("url", "")
                description = item.get("description", "")
                employer_logo = normalize_image_url(item.get("employer_logo", ""))

                # Nettoyer la description
                if description:
                    description = BeautifulSoup(description, "html.parser").get_text()
                    description = description.replace("&nbsp;", " ").strip()

                tags = item.get("tags", [])

                out.append(Offer(
                    title=f"{title} @ {company}",
                    url=apply_url,
                    source="arbeitnow",
                    provider=company,
                    offer_type="job",
                    description=clean_description(description) or "Offre d'emploi Arbeitnow",
                    keywords_matched=tags[:5],
                    image_url=employer_logo,
                ))
        except Exception as e:
            log.warning("[jobs] Échec de la récupération de Arbeitnow : %s", e)
        return out

    def _extract_news_image(self, item) -> str:
        """media:thumbnail / media:content / enclosure depuis un flux RSS."""
        for tag_name in ("media:thumbnail", "media:content"):
            media = item.find(tag_name)
            if media and media.get("url"):
                found = normalize_image_url(media["url"])
                if found:
                    return found
        enc = item.find("enclosure")
        if enc and enc.get("url") and (enc.get("type") or "").startswith("image"):
            return normalize_image_url(enc["url"])
        return ""

    def _scrape_google_news_jobs(
        self,
        query: str | None = None,
        city: str | None = None,
        country: str | None = None,
        company: str | None = None,
    ) -> List[Offer]:
        out: List[Offer] = []
        bits = ['("offre d\'emploi" OR recrutement OR hiring OR stage)']
        if query:
            bits.append(query)
        else:
            bits.append("(developpeur OR ingenieur OR tech OR python)")
        if city:
            bits.append(city)
        if country:
            bits.append(country)
        if company:
            bits.append(company)
        q = urllib.parse.quote_plus(" ".join(bits))
        url = f"https://news.google.com/rss/search?q={q}&hl=fr&gl=FR&ceid=FR:fr"
        log.info("[jobs] scraping Google News FR jobs")
        try:
            xml_content = self.fetch_static(url)
            if xml_content:
                soup = BeautifulSoup(xml_content, "html.parser")
                items = soup.find_all("item")
                for item in items[:20]:
                    title = item.find("title").get_text() if item.find("title") else ""
                    link = item.find("link").get_text() if item.find("link") else ""
                    desc_raw = item.find("description").get_text() if item.find("description") else ""
                    desc_soup = BeautifulSoup(desc_raw, "html.parser")
                    description = desc_soup.get_text()

                    provider = "Google News Jobs"
                    if " - " in title:
                        parts = title.rsplit(" - ", 1)
                        title = parts[0]
                        provider = parts[1]

                    out.append(Offer(
                        title=title,
                        url=link,
                        source="google_news_jobs",
                        provider=provider,
                        offer_type="job",
                        description=clean_description(description) or "Annonce de recrutement trouvée sur le web.",
                        keywords_matched=["recrutement", "emploi"],
                        image_url=self._extract_news_image(item),
                    ))
        except Exception as e:
            log.warning("[jobs] Échec de la récupération Google News Jobs : %s", e)
        return out
