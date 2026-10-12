"""Client HTTP Polymarket (Gamma + CLOB + Data API) — lecture seule, timeouts stricts.

Endpoints vérifiés (GET, timeout 8s) le 2026-09-09 :
- gamma-api.polymarket.com/markets?limit=1 → liste marchés
- gamma-api.polymarket.com/markets/{id} → détail
- gamma-api.polymarket.com/markets?slug=… → lookup slug
- gamma-api.polymarket.com/events?limit=1
- gamma-api.polymarket.com/events/keyset?limit=1
- data-api.polymarket.com/live-volume?id={event_id}
- clob.polymarket.com/time → epoch
- clob.polymarket.com/price?token_id=&side=buy → {"price":"0.038"}
- clob.polymarket.com/midpoint?token_id= → {"mid":"0.0385"}
- clob.polymarket.com/book?token_id= → bids/asks
- clob.polymarket.com/prices-history?market=&interval=&fidelity=
- clob.polymarket.com/clob-markets/{condition_id}
- clob.polymarket.com/spread?token_id= → {"spread":"0.001"}
- clob.polymarket.com/tick-size?token_id= → {"minimum_tick_size":0.001}

Pas de clés secrètes : APIs publiques en lecture.
"""
from __future__ import annotations

import json
import logging
import re
import threading
import time
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote

import requests

from config import DEFAULT_HEADERS

log = logging.getLogger(__name__)

GAMMA = "https://gamma-api.polymarket.com"
CLOB = "https://clob.polymarket.com"
DATA = "https://data-api.polymarket.com"

POLY_TIMEOUT = 8  # secondes — aligné sur les sondes live
_CACHE_TTL = 8.0
_MIN_INTERVAL = 0.05  # ~20 req/s max côté process (rate limit basique)

_slug_re = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9\-_]{0,180}$")
_hex_re = re.compile(r"^0x[a-fA-F0-9]{40,128}$")
_token_re = re.compile(r"^\d{10,80}$")
_id_re = re.compile(r"^\d{1,12}$")


class PolymarketClient:
    """Client synchrone thread-safe avec cache TTL court et throttle."""

    def __init__(
        self,
        session: Optional[requests.Session] = None,
        timeout: float = POLY_TIMEOUT,
        cache_ttl: float = _CACHE_TTL,
    ):
        self.session = session or requests.Session()
        self.timeout = timeout
        self.cache_ttl = cache_ttl
        self._lock = threading.Lock()
        self._last_req = 0.0
        self._cache: Dict[str, Tuple[float, Any]] = {}

    def _throttle(self) -> None:
        with self._lock:
            now = time.monotonic()
            wait = _MIN_INTERVAL - (now - self._last_req)
            if wait > 0:
                time.sleep(wait)
            self._last_req = time.monotonic()

    def _cache_get(self, key: str) -> Any:
        row = self._cache.get(key)
        if not row:
            return None
        ts, val = row
        if time.monotonic() - ts > self.cache_ttl:
            self._cache.pop(key, None)
            return None
        return val

    def _cache_set(self, key: str, val: Any) -> Any:
        self._cache[key] = (time.monotonic(), val)
        return val

    def _get_json(self, url: str, params: Optional[dict] = None, cache_key: Optional[str] = None) -> Any:
        ck = cache_key or f"{url}?{params}"
        hit = self._cache_get(ck)
        if hit is not None:
            return hit
        self._throttle()
        headers = {**DEFAULT_HEADERS, "Accept": "application/json"}
        resp = self.session.get(url, params=params, headers=headers, timeout=self.timeout)
        resp.raise_for_status()
        data = resp.json()
        return self._cache_set(ck, data)

    # --- Validation IDs ---
    @staticmethod
    def validate_market_id(value: str) -> str:
        v = (value or "").strip()
        if _id_re.match(v) or _slug_re.match(v) or _hex_re.match(v):
            return v
        raise ValueError("identifiant marché invalide")

    @staticmethod
    def validate_token_id(value: str) -> str:
        v = (value or "").strip()
        if not _token_re.match(v):
            raise ValueError("token_id invalide")
        return v

    @staticmethod
    def validate_condition_id(value: str) -> str:
        v = (value or "").strip()
        if not _hex_re.match(v):
            raise ValueError("condition_id invalide")
        return v

    @staticmethod
    def parse_json_list(raw: Any) -> List[Any]:
        if raw is None:
            return []
        if isinstance(raw, list):
            return raw
        if isinstance(raw, str):
            try:
                parsed = json.loads(raw)
                return parsed if isinstance(parsed, list) else []
            except Exception:
                return []
        return []

    # --- Gamma ---
    def list_markets(
        self,
        limit: int = 30,
        query: Optional[str] = None,
        active: bool = True,
        closed: bool = False,
    ) -> List[dict]:
        limit = max(1, min(int(limit or 30), 50))
        params: Dict[str, Any] = {
            "limit": limit,
            "active": "true" if active else "false",
            "closed": "true" if closed else "false",
            "order": "volume24hr",
            "ascending": "false",
        }
        # Gamma ne filtre pas toujours sur `query` ; on filtre côté client si fourni.
        try:
            data = self._get_json(f"{GAMMA}/markets", params=params, cache_key=f"mkts:{limit}:{active}:{closed}")
        except Exception as e:
            log.warning("[polymarket] list_markets: %s", e)
            return []
        markets = data if isinstance(data, list) else data.get("markets", data.get("data", []))
        if not isinstance(markets, list):
            return []
        if query:
            q = query.lower().strip()
            markets = [
                m for m in markets
                if q in (m.get("question") or "").lower()
                or q in (m.get("slug") or "").lower()
            ]
        return markets[:limit]

    def get_market(self, market_id: str) -> Optional[dict]:
        mid = self.validate_market_id(market_id)
        try:
            if _id_re.match(mid):
                data = self._get_json(f"{GAMMA}/markets/{mid}", cache_key=f"mkt:{mid}")
                if isinstance(data, dict) and data.get("id"):
                    return data
            if _slug_re.match(mid) and not mid.startswith("0x"):
                data = self._get_json(f"{GAMMA}/markets", params={"slug": mid}, cache_key=f"slug:{mid}")
                if isinstance(data, list) and data:
                    return data[0]
            if _hex_re.match(mid):
                data = self._get_json(
                    f"{GAMMA}/markets",
                    params={"condition_ids": mid},
                    cache_key=f"cid:{mid}",
                )
                if isinstance(data, list) and data:
                    return data[0]
        except Exception as e:
            log.warning("[polymarket] get_market(%s): %s", mid, e)
        return None

    # --- CLOB ---
    def get_price(self, token_id: str, side: str = "buy") -> Optional[float]:
        tid = self.validate_token_id(token_id)
        side = "buy" if (side or "buy").lower() != "sell" else "sell"
        try:
            data = self._get_json(
                f"{CLOB}/price",
                params={"token_id": tid, "side": side},
                cache_key=f"price:{tid}:{side}",
            )
            if isinstance(data, dict) and data.get("price") is not None:
                return float(data["price"])
        except Exception as e:
            log.warning("[polymarket] price: %s", e)
        return None

    def get_midpoint(self, token_id: str) -> Optional[float]:
        tid = self.validate_token_id(token_id)
        try:
            data = self._get_json(f"{CLOB}/midpoint", params={"token_id": tid}, cache_key=f"mid:{tid}")
            if isinstance(data, dict) and data.get("mid") is not None:
                return float(data["mid"])
        except Exception as e:
            log.warning("[polymarket] midpoint: %s", e)
        return None

    def get_book(self, token_id: str, depth: int = 8) -> dict:
        tid = self.validate_token_id(token_id)
        depth = max(1, min(int(depth or 8), 25))
        try:
            data = self._get_json(f"{CLOB}/book", params={"token_id": tid}, cache_key=f"book:{tid}")
            if not isinstance(data, dict):
                return {"bids": [], "asks": [], "token_id": tid}
            bids = (data.get("bids") or [])[-depth:][::-1]  # meilleurs bids en tête
            asks = (data.get("asks") or [])[:depth]
            # CLOB renvoie souvent bids croissant ; on prend les plus hauts.
            if data.get("bids"):
                sorted_bids = sorted(
                    data["bids"],
                    key=lambda x: float(x.get("price") or 0),
                    reverse=True,
                )[:depth]
                bids = sorted_bids
            return {
                "market": data.get("market"),
                "asset_id": data.get("asset_id") or tid,
                "timestamp": data.get("timestamp"),
                "bids": bids,
                "asks": asks,
            }
        except Exception as e:
            log.warning("[polymarket] book: %s", e)
            return {"bids": [], "asks": [], "token_id": tid, "error": str(e)}

    def get_prices_history(
        self,
        token_id: str,
        interval: str = "1d",
        fidelity: int = 60,
    ) -> List[dict]:
        tid = self.validate_token_id(token_id)
        interval = interval if interval in ("1m", "1h", "6h", "1d", "1w", "max") else "1d"
        fidelity = max(1, min(int(fidelity or 60), 1440))
        try:
            data = self._get_json(
                f"{CLOB}/prices-history",
                params={"market": tid, "interval": interval, "fidelity": fidelity},
                cache_key=f"hist:{tid}:{interval}:{fidelity}",
            )
            hist = data.get("history") if isinstance(data, dict) else None
            return hist if isinstance(hist, list) else []
        except Exception as e:
            log.warning("[polymarket] prices-history: %s", e)
            return []

    def get_clob_market(self, condition_id: str) -> Optional[dict]:
        cid = self.validate_condition_id(condition_id)
        try:
            return self._get_json(f"{CLOB}/clob-markets/{quote(cid, safe='')}", cache_key=f"clobm:{cid}")
        except Exception as e:
            log.warning("[polymarket] clob-markets: %s", e)
            return None

    def get_spread(self, token_id: str) -> Optional[float]:
        tid = self.validate_token_id(token_id)
        try:
            data = self._get_json(f"{CLOB}/spread", params={"token_id": tid}, cache_key=f"spr:{tid}")
            if isinstance(data, dict) and data.get("spread") is not None:
                return float(data["spread"])
        except Exception as e:
            log.warning("[polymarket] spread: %s", e)
        return None

    def get_time(self) -> Optional[int]:
        try:
            data = self._get_json(f"{CLOB}/time", cache_key="clob:time")
            return int(data) if data is not None else None
        except Exception as e:
            log.warning("[polymarket] time: %s", e)
            return None

    def get_live_volume(self, event_id: str) -> Optional[list]:
        eid = (event_id or "").strip()
        if not _id_re.match(eid):
            raise ValueError("event id invalide")
        try:
            data = self._get_json(f"{DATA}/live-volume", params={"id": eid}, cache_key=f"vol:{eid}")
            return data if isinstance(data, list) else []
        except Exception as e:
            log.warning("[polymarket] live-volume: %s", e)
            return None

    # --- Normalisation ---
    def normalize_market(self, m: dict, include_clob_price: bool = False) -> dict:
        """Transforme un marché Gamma en dict API stable."""
        slug = m.get("slug") or ""
        mid = str(m.get("id") or "")
        condition_id = m.get("conditionId") or m.get("condition_id") or ""
        outcomes = [str(x) for x in self.parse_json_list(m.get("outcomes"))]
        prices_raw = self.parse_json_list(m.get("outcomePrices") or m.get("outcome_prices"))
        prices: List[float] = []
        for p in prices_raw:
            try:
                prices.append(float(p))
            except (TypeError, ValueError):
                prices.append(0.0)
        token_ids = [str(x) for x in self.parse_json_list(m.get("clobTokenIds") or m.get("clob_token_ids"))]
        tokens: Dict[str, str] = {}
        for i, name in enumerate(outcomes):
            if i < len(token_ids):
                tokens[name] = token_ids[i]
        yes_price = prices[0] if prices else None
        no_price = prices[1] if len(prices) > 1 else (1.0 - yes_price if yes_price is not None else None)

        yes_token = tokens.get("Yes") or tokens.get("YES") or (token_ids[0] if token_ids else None)
        no_token = tokens.get("No") or tokens.get("NO") or (token_ids[1] if len(token_ids) > 1 else None)

        clob_yes = clob_no = None
        if include_clob_price and yes_token:
            clob_yes = self.get_midpoint(yes_token) or self.get_price(yes_token, "buy")
        if include_clob_price and no_token:
            clob_no = self.get_midpoint(no_token) or self.get_price(no_token, "buy")

        volume = m.get("volume24hr") or m.get("volume") or 0
        try:
            volume_f = float(volume)
        except (TypeError, ValueError):
            volume_f = 0.0

        effective_yes = clob_yes if clob_yes is not None else yes_price
        effective_no = clob_no if clob_no is not None else no_price
        prob_pct = round((effective_yes or 0.5) * 100, 1)

        return {
            "id": mid,
            "slug": slug,
            "title": m.get("question") or m.get("title") or "Marché",
            "question": m.get("question") or m.get("title") or "Marché",
            "url": f"https://polymarket.com/event/{slug}" if slug else "https://polymarket.com",
            "source": "polymarket",
            "provider": "Polymarket",
            "offer_type": "prediction",
            "description": f"Probabilité : {prob_pct}% Oui · Volume : ${volume_f:,.0f} · Marché Polymarket",
            "image_url": m.get("image") or m.get("icon") or "",
            "condition_id": condition_id,
            "outcomes": outcomes or ["Yes", "No"],
            "outcome_prices": prices,
            "yes_price": effective_yes,
            "no_price": effective_no,
            "gamma_yes_price": yes_price,
            "gamma_no_price": no_price,
            "clob_yes_price": clob_yes,
            "clob_no_price": clob_no,
            "token_ids": tokens,
            "yes_token_id": yes_token,
            "no_token_id": no_token,
            "volume24hr": volume_f,
            "liquidity": float(m.get("liquidityNum") or m.get("liquidity") or 0) if m.get("liquidity") or m.get("liquidityNum") else 0,
            "best_bid": m.get("bestBid"),
            "best_ask": m.get("bestAsk"),
            "last_trade_price": m.get("lastTradePrice"),
            "end_date": m.get("endDate") or m.get("endDateIso"),
            "active": bool(m.get("active")),
            "closed": bool(m.get("closed")),
            "accepting_orders": bool(m.get("acceptingOrders")),
        }

    def resolve_outcome_token(self, market: dict, outcome: str) -> Optional[str]:
        o = (outcome or "YES").strip().upper()
        if o in ("YES", "Y", "OUI"):
            return market.get("yes_token_id") or (market.get("token_ids") or {}).get("Yes")
        if o in ("NO", "N", "NON"):
            return market.get("no_token_id") or (market.get("token_ids") or {}).get("No")
        tokens = market.get("token_ids") or {}
        for k, v in tokens.items():
            if str(k).upper() == o:
                return v
        return None

    def quote_price_for_outcome(self, market: dict, outcome: str) -> Optional[float]:
        """Prix CLOB (mid sinon buy) pour un outcome ; fallback Gamma."""
        token = self.resolve_outcome_token(market, outcome)
        if token:
            mid = self.get_midpoint(token)
            if mid is not None:
                return mid
            px = self.get_price(token, "buy")
            if px is not None:
                return px
        o = (outcome or "YES").strip().upper()
        if o in ("YES", "Y", "OUI"):
            return market.get("yes_price") or market.get("gamma_yes_price")
        return market.get("no_price") or market.get("gamma_no_price")


_client: Optional[PolymarketClient] = None
_client_lock = threading.Lock()


def get_polymarket_client() -> PolymarketClient:
    global _client
    with _client_lock:
        if _client is None:
            _client = PolymarketClient()
        return _client
