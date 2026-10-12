"""Marchés de prédiction via Polymarket (Gamma + CLOB)."""
from __future__ import annotations

import logging
from typing import List, Optional

from bot.models import Offer
from scrapers.base import BaseScraper
from scrapers.polymarket_client import PolymarketClient, get_polymarket_client

log = logging.getLogger(__name__)


class PredictionsScraper(BaseScraper):
    name = "predictions"

    def __init__(self, client: Optional[PolymarketClient] = None, session=None):
        super().__init__(session=session)
        self.client = client or get_polymarket_client()

    def fetch_markets(self, limit: int = 30, query: Optional[str] = None) -> List[dict]:
        return self.client.list_markets(limit=limit, query=query)

    def markets_as_dicts(
        self,
        limit: int = 30,
        query: Optional[str] = None,
        include_clob_price: bool = False,
    ) -> List[dict]:
        raw = self.fetch_markets(limit=limit, query=query)
        return [self.client.normalize_market(m, include_clob_price=include_clob_price) for m in raw]

    def market_detail(self, market_id: str, with_history: bool = True, with_book: bool = True) -> Optional[dict]:
        raw = self.client.get_market(market_id)
        if not raw:
            return None
        detail = self.client.normalize_market(raw, include_clob_price=True)
        yes_tok = detail.get("yes_token_id")
        if with_history and yes_tok:
            detail["price_history"] = self.client.get_prices_history(yes_tok, interval="1d", fidelity=60)
        else:
            detail["price_history"] = []
        if with_book and yes_tok:
            detail["order_book"] = self.client.get_book(yes_tok, depth=8)
            detail["spread"] = self.client.get_spread(yes_tok)
        else:
            detail["order_book"] = {"bids": [], "asks": []}
            detail["spread"] = None
        if detail.get("condition_id"):
            detail["clob_market"] = self.client.get_clob_market(detail["condition_id"])
        return detail

    def run(self, query: Optional[str] = None) -> List[Offer]:
        markets = self.markets_as_dicts(limit=40, query=query, include_clob_price=False)
        out: List[Offer] = []
        for m in markets:
            yes = m.get("yes_price")
            keywords = [
                "prediction",
                "polymarket",
                f"prob:{(yes or 0.5) * 100:.1f}",
                m.get("slug") or "",
                f"market_id:{m.get('id') or ''}",
                f"yes_price:{yes if yes is not None else ''}",
                f"no_price:{m.get('no_price') if m.get('no_price') is not None else ''}",
            ]
            if m.get("yes_token_id"):
                keywords.append(f"yes_token:{m['yes_token_id']}")
            if m.get("no_token_id"):
                keywords.append(f"no_token:{m['no_token_id']}")
            out.append(Offer(
                title=m["title"],
                url=m["url"],
                source="polymarket",
                provider="Polymarket",
                offer_type="prediction",
                description=m["description"],
                keywords_matched=[k for k in keywords if k],
                image_url=m.get("image_url") or "",
            ))
        return out[:40]
