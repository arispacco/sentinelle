"""Base d'agent : convertit les offres du scraper v1 (root) en offres v2 (bot).

Les agents ont une API synchrone `run() -> List[Offer]` (+ `collect_comments`).
L'orchestrateur les exécute dans des threads (asyncio.to_thread) pour paralléliser.
"""
from __future__ import annotations

import logging
from typing import List

from bot.credibility.dna import offer_dna
from bot.credibility.expiry import infer_expiry
from bot.models import Comment, Offer

log = logging.getLogger(__name__)


class BaseAgent:
    name = "base"
    network = "web"

    def convert(self, raw) -> Offer:
        """Convertit une offre root (scrapers) en offre bot v2."""
        src = getattr(raw, "source", self.name) or self.name
        net = self.network
        if src in ("hackernews", "reddit"):
            net = src
        o = Offer(
            title=getattr(raw, "title", ""),
            url=getattr(raw, "url", ""),
            source=src, agent=self.name,
            provider=getattr(raw, "provider", "Inconnu"),
            offer_type=getattr(raw, "offer_type", "unknown"),
            network=net,
            description=getattr(raw, "description", ""),
            keywords_matched=getattr(raw, "keywords_matched", []),
            found_at=getattr(raw, "found_at", ""),
            image_url=getattr(raw, "image_url", ""),
        )
        o.dna_hash = offer_dna(o)
        o.expires_at = infer_expiry(f"{o.title} {o.description}", o.found_at)
        o.provenance = [{"source": self.name, "url": o.url,
                         "found_at": o.found_at, "alive": True}]
        return o

    def run(self) -> List[Offer]:
        raise NotImplementedError

    def collect_comments(self, offers: List[Offer]) -> List[Comment]:
        return []
