"""API REST + SSE : offres, favoris, runs, validation, email/share, live."""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Dict, List, Optional
import time

from fastapi import APIRouter, Header, Request
from fastapi.responses import JSONResponse, StreamingResponse
from urllib.parse import urlparse

from bot.credibility.engine import score_offer
from bot.credibility.trust import backprop_validation
from bot.models import Favorite
from models import normalize_image_url
from bot.orchestrator import Orchestrator
from bot.store import Store
from web.email_gen import render_email
from bot.ai import parse_user_intent, synthesize_digest, strip_cdata
from bot.dynamic import run_dynamic_scrape
from bot.auth import get_auth_store, create_token, require_professional, AUTH_OPTIONAL
from bot.firebase_auth import require_auth_for_request, authenticate_request

log = logging.getLogger(__name__)

# Rate-limit thread-safe en mémoire : {ip: [timestamps]}
_RATE: Dict[str, list] = {}
_RATE_LOCK = asyncio.Lock()
_RATE_MAX = 120  # req / fenêtre
_RATE_WINDOW = 60.0


async def _rate_limit(request: Request, max_requests: int = _RATE_MAX, window: float = _RATE_WINDOW) -> Optional[JSONResponse]:
    """Rate limiter thread-safe avec asyncio.Lock (buckets par IP, fenêtre glissante)."""
    ip = request.client.host if request.client else "unknown"
    now = time.time()
    async with _RATE_LOCK:
        bucket = [t for t in _RATE.get(ip, []) if now - t < window]
        if len(bucket) >= max_requests:
            return JSONResponse({"error": "Rate limit dépassé"}, status_code=429)
        bucket.append(now)
        _RATE[ip] = bucket
    return None


def _chat_source(o, topic_id=None) -> dict:
    """Contrat front : titre sans CDATA, url http(s), source, image, topic_id."""
    url = strip_cdata(getattr(o, "url", "") or "")
    if urlparse(url).scheme not in ("http", "https"):
        parsed = urlparse(url)
        url = f"https://{url}" if not parsed.scheme and url else ""
    return {
        "title": strip_cdata(getattr(o, "title", "")),
        "url": url,
        "source": strip_cdata(getattr(o, "provider", "") or getattr(o, "source", "") or ""),
        "image": normalize_image_url(getattr(o, "image_url", "") or ""),
        "provider": strip_cdata(getattr(o, "provider", "")),
        "offer_type": getattr(o, "offer_type", ""),
        "description": strip_cdata(getattr(o, "description", "") or "")[:240],
        "topic_id": topic_id,
    }


def _auth_guard(authorization: Optional[str], path: str):
    """Legacy auth guard pour le portail médical B2B."""
    try:
        user = require_professional(authorization)
        get_auth_store().log_access((user or {}).get("email"), path)
        return user, None
    except ValueError as e:
        return None, JSONResponse({"error": str(e)}, status_code=401)


async def _unified_auth(request: Request, authorization: Optional[str] = None):
    """Auth unifiée : Firebase + legacy JWT. Lecture libre, écriture protégée."""
    user, err = require_auth_for_request(request, authorization)
    if err:
        return None, err
    # Logger l'accès si utilisateur identifié
    if user:
        try:
            get_auth_store().log_access(user.get("email"), request.url.path)
        except Exception:
            pass
    return user, None


class Broadcaster:
    """Pub/sub SSE : clients s'abonnent, l'orchestrateur publie la progression."""
    def __init__(self):
        self.subscribers: List[asyncio.Queue] = []

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue()
        self.subscribers.append(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        if q in self.subscribers:
            self.subscribers.remove(q)

    def publish(self, data: dict) -> None:
        for q in self.subscribers:
            q.put_nowait(data)


def create_router(store: Store) -> APIRouter:
    router = APIRouter(prefix="/api")
    bc = Broadcaster()
    orch = Orchestrator(store=Store(), on_progress=lambda **kw: bc.publish(kw))

    @router.get("/stats")
    async def stats():
        return store.get_stats()

    @router.get("/offers")
    async def offers(tier: Optional[str] = None, status: Optional[str] = None,
                     network: Optional[str] = None, offer_type: Optional[str] = None,
                     search: Optional[str] = None, limit: int = 500):
        return [o.to_dict() for o in store.get_offers(
            tier=tier, status=status, network=network, offer_type=offer_type,
            search=search, limit=limit)]

    @router.get("/offers/{oid}")
    async def offer(oid: str):
        o = store.get_offer(oid)
        return o.to_dict() if o else JSONResponse({"error": "not found"}, 404)

    @router.get("/offers/{oid}/comments")
    async def offer_comments(oid: str):
        o = store.get_offer(oid)
        if not o:
            return JSONResponse({"error": "not found"}, 404)
        return [c.to_dict() for c in store.get_comments(o.dna_hash)]

    @router.post("/offers/{oid}/validate")
    async def validate(oid: str, request: Request, authorization: Optional[str] = Header(None)):
        user, err = await _unified_auth(request, authorization)
        if err:
            return err
        o = store.get_offer(oid)
        if not o:
            return JSONResponse({"error": "not found"}, 404)
        store.set_offer_status(oid, "validated")
        fav = backprop_validation(store, o)
        score_offer(store, o)
        bc.publish({"event": "offer_validated", "offer_id": oid})
        return {"ok": True, "favorite": fav.to_dict()}

    @router.post("/offers/status")
    async def set_status(body: dict, request: Request, authorization: Optional[str] = Header(None)):
        user, err = await _unified_auth(request, authorization)
        if err:
            return err
        ids = body.get("ids", [])
        status = body.get("status", "new")
        for i in ids:
            store.set_offer_status(i, status)
        bc.publish({"event": "status_changed", "ids": ids, "status": status})
        return {"ok": True, "updated": len(ids)}
    # --- favoris ---
    @router.get("/favorites")
    async def favorites():
        return [f.to_dict() for f in store.get_favorites()]

    @router.post("/favorites")
    async def add_fav(body: dict, request: Request, authorization: Optional[str] = Header(None)):
        user, err = await _unified_auth(request, authorization)
        if err:
            return err
        f = Favorite(key=body.get("key", ""), kind=body.get("kind", "source"),
                     label=body.get("label", ""), trust_score=body.get("trust_score", 1.0),
                     validated_count=body.get("validated_count", 0))
        store.add_favorite(f)
        return {"ok": True}

    @router.delete("/favorites/{key}")
    async def del_fav(key: str, request: Request, authorization: Optional[str] = Header(None)):
        user, err = await _unified_auth(request, authorization)
        if err:
            return err
        store.conn.execute("DELETE FROM favorites WHERE key=?", (key,))
        store.conn.commit()
        return {"ok": True}

    # --- runs / agents ---
    @router.get("/agents")
    async def agents():
        return Orchestrator.available_agents()

    @router.get("/news/digest")
    async def news_digest(limit: int = 20, offset: int = 0, category: str = None):
        limit = max(1, min(int(limit), 50))
        offset = max(0, int(offset))
        items = store.get_news_topics(limit=limit, offset=offset, category=category)
        return {"items": items, "total": store.count_news_topics(category=category)}

    @router.get("/news/topics/{topic_id}")
    async def news_topic_detail(topic_id: int):
        topic = store.get_news_topic(topic_id)
        if not topic:
            return JSONResponse({"error": "Sujet introuvable"}, status_code=404)
        return topic

    @router.post("/news/refresh")
    async def news_refresh(request: Request, authorization: Optional[str] = Header(None)):
        user, err = await _unified_auth(request, authorization)
        if err:
            return err
        if not user:
            return JSONResponse({"error": "Authentification requise pour actualiser."}, status_code=401)

        async def _cycle():
            from bot.news_digest import run_news_cycle
            try:
                await asyncio.to_thread(run_news_cycle, store)
            except Exception as e:
                log.exception("[news] cycle manuel en échec : %s", e)

        asyncio.create_task(_cycle())
        return {"status": "started"}

    @router.get("/runs")
    async def runs():
        rows = store.conn.execute("SELECT * FROM runs ORDER BY id DESC LIMIT 20").fetchall()
        return [{"id": r["id"], "started_at": r["started_at"],
                 "finished_at": r["finished_at"], "status": r["status"],
                 "offers_count": r["offers_count"],
                 "agents": json.loads(r["agents_json"] or "[]")} for r in rows]

    @router.get("/run/status")
    async def run_status():
        return orch.status

    @router.post("/run")
    async def run(body: dict = None):
        body = body or {}
        only = body.get("only")

        async def _wrapped():
            try:
                await orch.run(only=only)
            except Exception as e:
                log.exception("run error: %s", e)
                bc.publish({"event": "run_error", "error": str(e)})

        asyncio.create_task(_wrapped())
        return {"ok": True, "agents": only or "default"}

    # --- SSE live ---
    @router.get("/events")
    async def events():
        q = bc.subscribe()

        async def stream():
            try:
                yield f"data: {json.dumps({'event': 'connected'})}\n\n"
                while True:
                    data = await q.get()
                    yield f"data: {json.dumps(data)}\n\n"
            except asyncio.CancelledError:
                pass
            finally:
                bc.unsubscribe(q)

        return StreamingResponse(stream(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache",
                                          "Connection": "keep-alive"})

    # --- email / partage ---
    @router.post("/email/single")
    async def email_single(body: dict):
        o = store.get_offer(body.get("id", ""))
        if not o:
            return JSONResponse({"error": "not found"}, 404)
        return render_email([o])

    @router.post("/email/bulk")
    async def email_bulk(body: dict):
        ids = body.get("ids", [])
        offers = [store.get_offer(i) for i in ids]
        offers = [o for o in offers if o]
        return render_email(offers)

    # --- chat conversationnel et scraping dynamique (ancré sources) ---
    @router.post("/chat")
    async def chat(body: dict, request: Request, authorization: Optional[str] = Header(None)):
        try:
            user, err = await _unified_auth(request, authorization)
            if err:
                return err
            message = (body.get("message") or "").strip()
            session_id = body.get("session_id") or "default"
            if not message:
                return JSONResponse({"error": "Empty message"}, 400)

            history = None
            try:
                from bot.chat_history import get_history_store
                history = get_history_store()
                history.add(session_id, "user", message)
            except Exception as e_hist:
                log.warning("[chat] Erreur enregistrement historique user : %s", e_hist)

            analysis = parse_user_intent(message)
            intent = analysis.get("intent", "chat")
            query = analysis.get("query", "")
            category = analysis.get("category")
            specificity = analysis.get("specificity")
            if intent == "search_news" and specificity not in ("specific", "generic"):
                from bot.ai import infer_specificity
                specificity = infer_specificity(message, intent, category, query)
            reply = analysis.get("reply", "Je traite votre demande...")

            offers = []
            watch_payload = None
            if intent == "create_watch" and query:
                try:
                    from bot.watch_store import get_watch_store, guess_kind_and_ticker
                    from bot.watch_engine import refresh_watch, generate_watch_insight
                    store_w = get_watch_store()
                    kind, ticker = guess_kind_and_ticker(query)
                    existing = None
                    for w in store_w.list():
                        if query.lower() in w["title"].lower() or query.lower() in w["query"].lower():
                            existing = w
                            break
                    if existing:
                        watch = existing
                    else:
                        watch = store_w.create(title=query, kind=kind, query=query, ticker=ticker)
                    await asyncio.to_thread(refresh_watch, watch["id"])
                    insight = await asyncio.to_thread(generate_watch_insight, watch["id"], "summary")
                    dash = store_w.dashboard(watch["id"])
                    content = (insight or {}).get("content") or reply
                    reply = (
                        f"Suivi **{watch['title']}** prêt (slug `{watch['slug']}`).\n\n"
                        f"{content}\n\n"
                        f"Ouvre le dashboard dans **Mes suivis** → {watch['title']}."
                    )
                    watch_payload = {"watch": watch, "dashboard": dash}
                except Exception as e_w:
                    log.warning("[chat] Erreur création suivi : %s", e_w)
                    reply = f"Création du suivi « {query} » initiée mais une étape a pris du retard."
            elif intent != "chat" and query:
                try:
                    offers = await asyncio.to_thread(
                        run_dynamic_scrape, store, intent, query, category, specificity
                    )
                except Exception as e_scr:
                    log.warning("[chat] Erreur scraping dynamique : %s", e_scr)
                    offers = []

                if offers:
                    try:
                        digest = synthesize_digest(
                            message, offers, specificity=specificity or "generic"
                        )
                        reply = digest or reply
                    except Exception as e_dig:
                        log.warning("[chat] Erreur synthèse digest : %s", e_dig)
                else:
                    reply = (
                        f"Aucune source scrapée trouvée pour « {query} ». "
                        "Affine ta requête (mots-clés plus précis, autre espace thématique) "
                        "— je ne complète pas avec des connaissances hors scraping."
                    )
            elif intent == "chat":
                # Pas de digression encyclopédique
                reply = analysis.get("reply") or (
                    "Pose une question concrète : je scraperai des sources et répondrai uniquement à partir d'elles. "
                    "Tu peux aussi dire « suis SpaceX » ou « suis la guerre en Iran » pour créer un dashboard."
                )

            sources = []
            match_fn = None
            if intent == "search_news" and specificity != "specific":
                try:
                    from bot.news_digest import match_topic_id
                    match_fn = match_topic_id
                except Exception:
                    match_fn = None
            for o in offers[:15]:
                tid = None
                if match_fn:
                    try:
                        tid = match_fn(store, getattr(o, "title", "") or "")
                    except Exception:
                        tid = None
                sources.append(_chat_source(o, topic_id=tid))
            if history:
                try:
                    history.add(session_id, "assistant", reply, sources)
                except Exception as e_h2:
                    log.warning("[chat] Erreur enregistrement historique assistant : %s", e_h2)

            return {
                "intent": intent,
                "query": query,
                "category": category,
                "specificity": specificity,
                "reply": reply,
                "session_id": session_id,
                "sources": sources,
                "offers": [o.to_dict() for o in offers],
                "watch": watch_payload,
            }
        except Exception as e_global:
            log.exception("[chat] Erreur non gérée dans /api/chat : %s", e_global)
            return JSONResponse({
                "intent": "chat",
                "query": "",
                "category": None,
                "reply": "Une erreur temporaire est survenue lors de la recherche. Réessaie avec d'autres mots-clés.",
                "session_id": body.get("session_id") or "default",
                "sources": [],
                "offers": [],
                "watch": None,
                "error": str(e_global)
            }, status_code=200)

    @router.get("/chat/history")
    async def chat_history_sessions(limit: int = 30):
        from bot.chat_history import get_history_store
        return get_history_store().list_sessions(limit=limit)

    @router.get("/chat/history/{session_id}")
    async def chat_history_session(session_id: str):
        from bot.chat_history import get_history_store
        return {"session_id": session_id, "turns": get_history_store().get_session(session_id)}

    @router.get("/spaces")
    async def spaces_catalog():
        from bot.spaces import list_spaces
        return list_spaces()

    @router.get("/spaces/{slug}")
    async def space_detail(slug: str, q: Optional[str] = None, limit: int = 24):
        from bot.spaces import load_space_items
        data = await asyncio.to_thread(load_space_items, slug, q, limit)
        if data.get("error") == "unknown_space":
            return JSONResponse({"error": "Espace inconnu"}, status_code=404)
        return data

    @router.get("/crypto/markets")
    async def crypto_markets():
        from scrapers.crypto import CryptoScraper
        offers = await asyncio.to_thread(lambda: CryptoScraper().markets_as_offers())
        return [o.to_dict() for o in offers]

    @router.get("/predictions")
    async def predictions(q: Optional[str] = None, limit: int = 30):
        """Liste legacy (Offer) — préfère /predictions/markets pour le live."""
        from scrapers.predictions import PredictionsScraper
        offers = await asyncio.to_thread(lambda: PredictionsScraper().run(q)[:limit])
        return [o.to_dict() for o in offers]

    @router.get("/predictions/markets")
    async def predictions_markets(request: Request, q: Optional[str] = None, limit: int = 30):
        """Marchés Polymarket live (Gamma), normalisés pour le paper trading."""
        limited = await _rate_limit(request, max_requests=60, window=60.0)
        if limited:
            return limited
        limit = max(1, min(int(limit or 30), 50))
        from scrapers.predictions import PredictionsScraper
        markets = await asyncio.to_thread(
            lambda: PredictionsScraper().markets_as_dicts(limit=limit, query=q, include_clob_price=False)
        )
        return {"markets": markets, "count": len(markets), "source": "gamma-api.polymarket.com"}

    @router.get("/predictions/market/{market_id}")
    async def predictions_market_detail(market_id: str, request: Request):
        """Détail marché + prix CLOB + historique + carnet (proxifié serveur)."""
        limited = await _rate_limit(request, max_requests=60, window=60.0)
        if limited:
            return limited
        from scrapers.polymarket_client import PolymarketClient
        try:
            PolymarketClient.validate_market_id(market_id)
        except ValueError as e:
            return JSONResponse({"error": str(e)}, 400)
        from scrapers.predictions import PredictionsScraper
        detail = await asyncio.to_thread(lambda: PredictionsScraper().market_detail(market_id))
        if not detail:
            return JSONResponse({"error": "marché introuvable"}, 404)
        return detail

    # --- Suivis (watches) ---
    @router.get("/watches")
    async def list_watches():
        from bot.watch_store import get_watch_store
        return get_watch_store().list()

    @router.post("/watches")
    async def create_watch(body: dict, request: Request, authorization: Optional[str] = Header(None)):
        user, err = await _unified_auth(request, authorization)
        if err:
            return err
        from bot.watch_store import get_watch_store, guess_kind_and_ticker
        from bot.watch_engine import refresh_watch
        title = (body.get("title") or "").strip()
        if not title:
            return JSONResponse({"error": "title requis"}, 400)
        kind_in = body.get("kind")
        ticker_in = body.get("ticker")
        kind, ticker = guess_kind_and_ticker(title, kind_in, ticker_in)
        query = (body.get("query") or title).strip()
        store = get_watch_store()
        # réutiliser si slug proche déjà existant
        existing = None
        for w in store.list():
            if w["title"].lower() == title.lower() or w["query"].lower() == query.lower():
                existing = w
                break
        if existing:
            watch = existing
        else:
            watch = store.create(
                title=title,
                kind=kind,
                query=query,
                ticker=ticker,
                description=body.get("description") or "",
            )
        if body.get("refresh", True):
            await asyncio.to_thread(refresh_watch, watch["id"])
            watch = store.get(watch["id"])
        return watch

    @router.get("/watches/{slug}")
    async def get_watch(slug: str):
        from bot.watch_store import get_watch_store
        store = get_watch_store()
        w = store.get_by_slug(slug)
        if not w:
            return JSONResponse({"error": "Suivi introuvable"}, 404)
        return store.dashboard(w["id"])

    @router.post("/watches/{slug}/refresh")
    async def refresh_watch_endpoint(slug: str):
        from bot.watch_store import get_watch_store
        from bot.watch_engine import refresh_watch
        store = get_watch_store()
        w = store.get_by_slug(slug)
        if not w:
            return JSONResponse({"error": "Suivi introuvable"}, 404)
        result = await asyncio.to_thread(refresh_watch, w["id"])
        return {"refresh": result, "dashboard": store.dashboard(w["id"])}

    @router.post("/watches/{slug}/analyze")
    async def analyze_watch_endpoint(slug: str, body: dict = None):
        from bot.watch_store import get_watch_store
        from bot.watch_engine import generate_watch_insight
        body = body or {}
        store = get_watch_store()
        w = store.get_by_slug(slug)
        if not w:
            return JSONResponse({"error": "Suivi introuvable"}, 404)
        kind = body.get("kind") or "summary"
        insight = await asyncio.to_thread(
            generate_watch_insight, w["id"], kind, body.get("prompt") or ""
        )
        return {"insight": insight, "dashboard": store.dashboard(w["id"])}

    @router.delete("/watches/{slug}")
    async def delete_watch(slug: str, request: Request, authorization: Optional[str] = Header(None)):
        user, err = await _unified_auth(request, authorization)
        if err:
            return err
        from bot.watch_store import get_watch_store
        store = get_watch_store()
        w = store.get_by_slug(slug)
        if not w:
            return JSONResponse({"error": "Suivi introuvable"}, 404)
        store.delete(w["id"])
        return {"status": "deleted", "slug": slug}

    # --- Marché multi-providers (gratuit / freemium) ---
    @router.get("/market/providers")
    async def market_providers():
        from bot.market_data import providers_status
        return providers_status()

    @router.get("/market/chart")
    async def market_chart(
        symbol: str,
        range: str = "3mo",  # noqa: A002 — query publique
        force: bool = True,
    ):
        from bot.market_data import clear_market_caches, get_chart
        if force:
            clear_market_caches()
        # kwargs explicites (évite confusion avec le builtin range)
        return await asyncio.to_thread(get_chart, symbol, range, True)

    @router.get("/market/quote")
    async def market_quote(symbol: str, force: bool = True):
        from bot.market_data import get_quote
        q = await asyncio.to_thread(get_quote, symbol, True)
        if not q:
            return JSONResponse({"error": "Quote indisponible", "symbol": symbol}, 404)
        return q

    @router.get("/market/crypto/tickers")
    async def market_crypto_tickers(symbols: str = "BTCUSDT,ETHUSDT,SOLUSDT,BNBUSDT"):
        from bot.market_data import get_quote
        out = []
        for s in [x.strip() for x in symbols.split(",") if x.strip()]:
            q = await asyncio.to_thread(get_quote, s)
            if q:
                out.append(q)
        return {"tickers": out, "count": len(out)}

    @router.get("/market/stream/{symbol}")
    async def market_stream(symbol: str, interval_sec: float = 3.0):
        """SSE near-realtime : poll Binance/Yahoo selon l'actif."""
        from bot.market_data import get_quote, is_crypto_symbol, normalize_symbol

        sym = normalize_symbol(symbol)
        delay = max(1.5, min(float(interval_sec or 3), 30.0))
        # crypto: refresh rapide ; actions: plus lent (respect quotas / retard)
        if not is_crypto_symbol(sym):
            delay = max(delay, 15.0)

        async def event_gen():
            while True:
                q = await asyncio.to_thread(get_quote, sym, True)
                payload = q or {"symbol": sym, "error": "unavailable"}
                yield f"data: {json.dumps(payload)}\n\n"
                await asyncio.sleep(delay)

        return StreamingResponse(event_gen(), media_type="text/event-stream")

    # --- Profil utilisateur ---
    @router.get("/profile")
    async def get_profile():
        from bot.user_profile import get_profile_store
        return get_profile_store().get()

    @router.put("/profile")
    async def put_profile(body: dict):
        from bot.user_profile import get_profile_store
        return get_profile_store().update(body or {})

    # --- Opportunités ---
    @router.get("/opportunities")
    async def list_opportunities(
        type: Optional[str] = None,
        q: Optional[str] = None,
        city: Optional[str] = None,
        country: Optional[str] = None,
        company: Optional[str] = None,
        limit: int = 40,
    ):
        from bot.opportunities import collect_opportunities, feed_for_profile
        if type == "feed":
            items = await asyncio.to_thread(feed_for_profile, min(limit, 20))
        else:
            items = await asyncio.to_thread(
                collect_opportunities, type, q, city, country, company, limit
            )
        return {"items": items, "count": len(items)}

    @router.get("/opportunities/feed")
    async def opportunities_feed(limit: int = 20):
        from bot.opportunities import feed_for_profile
        items = await asyncio.to_thread(feed_for_profile, limit)
        return {"items": items, "count": len(items)}

    @router.get("/opportunities/digest")
    async def opportunities_digest():
        from bot.opportunity_digest import build_welcome_digest
        return await asyncio.to_thread(build_welcome_digest)

    # --- Learn Trading (paper) ---
    @router.get("/learn/portfolio")
    async def learn_portfolio():
        from bot.paper_trading import get_paper_store
        return get_paper_store().portfolio()

    @router.post("/learn/orders")
    async def learn_orders(body: dict, request: Request, authorization: Optional[str] = Header(None)):
        user, err = await _unified_auth(request, authorization)
        if err:
            return err
        from bot.paper_trading import get_paper_store
        try:
            result = await asyncio.to_thread(
                get_paper_store().place_order,
                body.get("symbol") or "",
                body.get("side") or "buy",
                float(body.get("qty") or 0),
                body.get("order_type") or "market",
                body.get("limit_price"),
            )
            return result
        except ValueError as e:
            return JSONResponse({"error": str(e)}, 400)

    @router.post("/learn/explain")
    async def learn_explain(body: dict):
        from bot.paper_trading import explain_term
        q = (body.get("question") or body.get("q") or "").strip()
        if not q:
            return JSONResponse({"error": "question requise"}, 400)
        return await asyncio.to_thread(explain_term, q)

    @router.post("/learn/level")
    async def learn_level(body: dict):
        from bot.paper_trading import get_paper_store
        return get_paper_store().set_level(body.get("level") or "beginner")

    @router.post("/learn/reset")
    async def learn_reset(request: Request, authorization: Optional[str] = Header(None)):
        user, err = await _unified_auth(request, authorization)
        if err:
            return err
        from bot.paper_trading import get_paper_store
        return get_paper_store().reset()

    @router.post("/learn/journal")
    async def learn_journal(body: dict):
        from bot.paper_trading import get_paper_store
        note = (body.get("note") or "").strip()
        if not note:
            return JSONResponse({"error": "note requise"}, 400)
        return get_paper_store().add_journal(note)

    @router.post("/learn/sweep")
    async def learn_sweep():
        """Déclenche la réévaluation des ordres limites en attente (utile pour
        forcer le balayage sans attendre le prochain GET /portfolio)."""
        from bot.paper_trading import get_paper_store
        return get_paper_store().sweep_open_orders()

    # --- Marché de prédictions (Polymarket Paper Betting) ---
    @router.post("/predictions/bet")
    async def place_prediction_bet(request: Request, body: dict):
        """Paper bet : cash virtuel local, prix sourcé depuis CLOB (pas le client)."""
        limited = await _rate_limit(request, max_requests=30, window=60.0)
        if limited:
            return limited
        from bot.paper_trading import get_paper_store
        from scrapers.polymarket_client import get_polymarket_client

        market_ref = (
            (body.get("market_id") or body.get("market_slug") or body.get("slug") or "")
            .strip()
        )
        outcome = (body.get("outcome") or "YES").strip().upper()
        if outcome in ("OUI", "Y"):
            outcome = "YES"
        elif outcome in ("NON", "N"):
            outcome = "NO"
        try:
            amount = float(body.get("amount") or 10.0)
        except (TypeError, ValueError):
            return JSONResponse({"error": "montant invalide"}, 400)
        if amount <= 0 or amount > 1_000_000:
            return JSONResponse({"error": "montant hors bornes"}, 400)

        if not market_ref:
            return JSONResponse({"error": "marché requis"}, 400)

        client = get_polymarket_client()
        question = (body.get("question") or "Marché de prédiction").strip()[:500]
        market_slug = market_ref
        price: Optional[float] = None
        price_source = ""

        try:
            client.validate_market_id(market_ref)
        except ValueError:
            return JSONResponse({"error": "identifiant marché invalide"}, 400)

        def _resolve():
            raw = client.get_market(market_ref)
            if not raw:
                return None, None, None, None
            norm = client.normalize_market(raw, include_clob_price=False)
            px = client.quote_price_for_outcome(norm, outcome)
            return norm, px, norm.get("slug") or market_ref, norm.get("question") or question

        norm, clob_price, slug, qtitle = await asyncio.to_thread(_resolve)
        if norm:
            market_slug = slug or market_slug
            question = qtitle or question
        if clob_price is not None:
            price = float(clob_price)
            price_source = "clob"
        elif norm and outcome == "YES" and norm.get("gamma_yes_price") is not None:
            price = float(norm["gamma_yes_price"])
            price_source = "gamma"
        elif norm and outcome == "NO" and norm.get("gamma_no_price") is not None:
            price = float(norm["gamma_no_price"])
            price_source = "gamma"

        if price is None:
            return JSONResponse({"error": "cours serveur indisponible"}, 400)

        res = get_paper_store().buy_prediction(
            market_slug=market_slug,
            question=question,
            outcome=outcome,
            price=price,
            amount=amount,
        )
        if "error" in res:
            return JSONResponse(res, 400)
        res["price_source"] = price_source
        res["market_slug"] = market_slug
        return res

    @router.get("/predictions/portfolio")
    async def get_predictions_portfolio():
        from bot.paper_trading import get_paper_store
        p = get_paper_store()
        return {
            "predictions": p.list_predictions(50),
            "cash": p.account().get("cash", 0),
        }

    @router.get("/health")
    async def health():
        """Healthcheck pour Render — vérifie DB et services."""
        import platform
        checks = {"status": "ok", "python": platform.python_version()}
        try:
            from bot.medical_store import MedicalStore
            med = MedicalStore()
            try:
                stats = med.get_stats()
                checks["medical_backend"] = stats.get("backend")
                checks["medical_records"] = stats.get("total_records")
            finally:
                med.close()
        except Exception as e:
            checks["medical"] = f"error: {e}"
        checks["auth_optional"] = AUTH_OPTIONAL
        checks["store_ok"] = store.conn is not None
        return checks

    @router.post("/auth/firebase")
    async def auth_firebase(body: dict):
        """Échange un token Firebase contre les infos utilisateur (post-login frontend)."""
        token = body.get("token", "")
        if not token:
            return JSONResponse({"error": "Token manquant"}, 400)
        user, error = authenticate_request(f"Bearer {token}")
        if not user:
            return JSONResponse({"error": error or "Token invalide"}, 401)
        return {"user": user}

    @router.post("/auth/register")
    async def auth_register(body: dict, request: Request):
        limited = await _rate_limit(request)
        if limited:
            return limited
        try:
            user = get_auth_store().register(
                email=body.get("email", ""),
                license_number=body.get("license_number", ""),
                password=body.get("password", ""),
                full_name=body.get("full_name", ""),
            )
        except ValueError as e:
            return JSONResponse({"error": str(e)}, status_code=400)
        token = create_token({"sub": user["email"], "license": user["license_number"], "uid": user["id"]})
        return {"token": token, "user": user}

    @router.post("/auth/login")
    async def auth_login(body: dict, request: Request):
        limited = await _rate_limit(request)
        if limited:
            return limited
        user = get_auth_store().authenticate(
            email=body.get("email", ""),
            password=body.get("password", ""),
            license_number=body.get("license_number", ""),
        )
        if not user:
            return JSONResponse({"error": "Identifiants invalides"}, status_code=401)
        token = create_token({"sub": user["email"], "license": user["license_number"], "uid": user["id"]})
        return {"token": token, "user": user}

    @router.get("/medical")
    async def get_medical_records(
        request: Request,
        search: Optional[str] = None,
        source: Optional[str] = None,
        limit: int = 50,
        phase: Optional[str] = None,
        status: Optional[str] = None,
        country: Optional[str] = None,
        authorization: Optional[str] = Header(None),
    ):
        limited = await _rate_limit(request)
        if limited:
            return limited
        _, err = _auth_guard(authorization, "/api/medical")
        if err:
            return err
        from bot.medical_store import MedicalStore
        med_store = MedicalStore()
        return med_store.get_records(
            search=search, source=source, limit=limit,
            phase=phase, status=status, country=country,
        )

    @router.get("/medical/search")
    async def medical_search(
        request: Request,
        q: Optional[str] = None,
        source: Optional[str] = None,
        limit: int = 50,
        phase: Optional[str] = None,
        status: Optional[str] = None,
        country: Optional[str] = None,
        authorization: Optional[str] = Header(None),
    ):
        limited = await _rate_limit(request)
        if limited:
            return limited
        _, err = _auth_guard(authorization, "/api/medical/search")
        if err:
            return err
        from bot.medical_store import MedicalStore
        med_store = MedicalStore()
        return {
            "query": q,
            "results": med_store.get_records(
                search=q, source=source, limit=limit,
                phase=phase, status=status, country=country, semantic=True,
            ),
        }

    @router.get("/medical/stats")
    async def medical_stats(authorization: Optional[str] = Header(None)):
        _, err = _auth_guard(authorization, "/api/medical/stats")
        if err:
            return err
        from bot.medical_store import MedicalStore
        return MedicalStore().get_stats()

    @router.get("/medical/nearby")
    async def get_nearby_records(
        request: Request,
        lat: float,
        lon: float,
        max_km: float = 1000.0,
        authorization: Optional[str] = Header(None),
    ):
        limited = await _rate_limit(request)
        if limited:
            return limited
        _, err = _auth_guard(authorization, "/api/medical/nearby")
        if err:
            return err
        from bot.medical_store import MedicalStore
        return MedicalStore().get_nearby_studies(lat=lat, lon=lon, max_km=max_km)

    @router.get("/medical/plants")
    async def get_plants(
        request: Request,
        search: Optional[str] = None,
        enrich: bool = False,
        authorization: Optional[str] = Header(None),
    ):
        limited = await _rate_limit(request)
        if limited:
            return limited
        _, err = _auth_guard(authorization, "/api/medical/plants")
        if err:
            return err
        from bot.medical_store import MedicalStore
        from scrapers.medical import MedicalScraper
        med_store = MedicalStore()
        scraper = MedicalScraper()
        plants = med_store.get_plants(search=search)

        if enrich:
            for p in plants:
                sci_name = p.get("scientific_name")
                if sci_name:
                    try:
                        new_refs = scraper.scrape_pubmed_for_plant(sci_name)
                        if new_refs:
                            merged = list(set((p.get("references") or []) + new_refs))
                            p["references"] = merged
                            p_to_save = p.copy()
                            p_to_save["references"] = merged
                            med_store.upsert_plant(p_to_save)
                    except Exception as e:
                        log.warning("[api] Échec enrichissement %s : %s", sci_name, e)

        for p in plants:
            related = med_store.find_related_for_plant(p, limit=8)
            p["related_studies"] = related
            p["evidence_level"] = (
                "élevé" if len(related) >= 5 else
                "modéré" if len(related) >= 2 else
                "préliminaire"
            )
        return plants

    @router.get("/medical/plants/{name}")
    async def get_plant_detail(name: str, authorization: Optional[str] = Header(None)):
        _, err = _auth_guard(authorization, f"/api/medical/plants/{name}")
        if err:
            return err
        from bot.medical_store import MedicalStore
        med_store = MedicalStore()
        plants = med_store.get_plants(search=name)
        plant = next((p for p in plants if p.get("name", "").lower() == name.lower()
                      or (p.get("scientific_name") or "").lower() == name.lower()), None)
        if not plant and plants:
            plant = plants[0]
        if not plant:
            return JSONResponse({"error": "Plante introuvable"}, status_code=404)
        related = med_store.find_related_for_plant(plant, limit=15)
        plant["related_studies"] = related
        plant["evidence_level"] = (
            "élevé" if len(related) >= 5 else
            "modéré" if len(related) >= 2 else
            "préliminaire"
        )
        plant["cross_sheet"] = {
            "traditional_use": plant.get("indications"),
            "active_compounds": plant.get("active_compounds"),
            "indexed_evidence_count": len(related),
            "disclaimer": "Croisement exploratoire tradition ↔ littérature. Ne constitue pas une preuve d'efficacité clinique.",
        }
        return plant

    @router.get("/medical/{record_id}")
    async def get_medical_record(record_id: str, authorization: Optional[str] = Header(None)):
        _, err = _auth_guard(authorization, f"/api/medical/{record_id}")
        if err:
            return err
        from bot.medical_store import MedicalStore
        rec = MedicalStore().get_record(record_id)
        if not rec:
            return JSONResponse({"error": "Enregistrement introuvable"}, status_code=404)
        return rec

    @router.post("/medical/{record_id}/contact-email")
    async def medical_contact_email(
        record_id: str,
        body: dict = None,
        authorization: Optional[str] = Header(None),
    ):
        user, err = _auth_guard(authorization, f"/api/medical/{record_id}/contact-email")
        if err:
            return err
        from bot.medical_store import MedicalStore
        rec = MedicalStore().get_record(record_id)
        if not rec:
            return JSONResponse({"error": "Enregistrement introuvable"}, status_code=404)
        body = body or {}
        case_summary = body.get("case_summary", "Résumé de cas non fourni (anonymisé).")
        professional = (user or {}).get("sub") or body.get("email") or "professionnel@example.com"
        license_no = (user or {}).get("license") or body.get("license_number") or "N/A"
        nct = rec.get("nct_id") or rec.get("id")
        subject = f"[Mise en relation clinique] {nct} — {rec.get('title', '')[:60]}"
        text = (
            f"Bonjour,\n\n"
            f"Je suis un professionnel de santé ({professional}, licence {license_no}) "
            f"et souhaite échanger au sujet de l'étude suivante :\n\n"
            f"• Identifiant : {nct}\n"
            f"• Titre : {rec.get('title')}\n"
            f"• Sponsor : {rec.get('sponsor')}\n"
            f"• Lien : {rec.get('url')}\n\n"
            f"Résumé anonymisé du cas clinique :\n{case_summary}\n\n"
            f"Cordialement,\n{professional}\n"
        )
        mailto = f"mailto:info@clinicaltrials.gov?subject={subject}&body={text}"
        return {
            "subject": subject,
            "body": text,
            "mailto": mailto,
            "nct_id": nct,
            "record_id": record_id,
        }

    @router.get("/alerts")
    async def get_alerts():
        return store.get_alerts()

    @router.post("/alerts")
    async def create_alert(body: dict, request: Request, authorization: Optional[str] = Header(None)):
        user, err = await _unified_auth(request, authorization)
        if err:
            return err
        email = body.get("email", "")
        query = body.get("query", "")
        category = body.get("category", "")
        location = body.get("location")
        if not email or not query or not category:
            return JSONResponse({"error": "Champs obligatoires manquants"}, 400)
            
        alert_id = store.add_alert(email, query, category, location)
        
        # Envoyer immédiatement un mail de confirmation avec les correspondances existantes
        try:
            from bot.medical_store import MedicalStore
            from web.email_gen import send_smtp_email
            
            matches = []
            if category == "medical":
                med_store = MedicalStore()
                records = med_store.get_records(limit=10)
                for r in records:
                    txt = query.lower()
                    if txt in r.get("title", "").lower() or txt in r.get("summary", "").lower() or txt in r.get("conditions", "").lower():
                        matches.append(r["title"])
            else:
                offers = store.get_offers(limit=50)
                for o in offers:
                    o_type = o.offer_type
                    if category == "jobs" and o_type != "job":
                        continue
                    if category == "news" and o_type != "news":
                        continue
                    if category == "promotions" and o_type != "promotion":
                        continue
                    if query.lower() in o.title.lower() or query.lower() in o.description.lower():
                        matches.append(o.title)
            
            subject = f"✅ Confirmation de votre Alerte Veille : '{query}'"
            matches_text = ""
            if matches:
                matches_text = "\n\nVoici les premières correspondances trouvées dans notre base :\n" + "\n".join(f"• {m}" for m in matches[:5])
            else:
                matches_text = "\n\nAucune correspondance trouvée pour le moment. Nous surveillons le Web pour vous et vous tiendrons informé !"
                
            body_plain = (
                f"Bonjour,\n\n"
                f"Votre alerte de veille personnalisée a bien été enregistrée.\n"
                f"• Catégorie : {category}\n"
                f"• Requête : '{query}'\n"
                f"• Localisation : {location or 'Globale'}"
                f"{matches_text}\n\n"
                f"Cordialement,\n"
                f"L'équipe Scrapper IA"
            )
            
            sent = send_smtp_email(email, subject, body_plain)
            if sent:
                log.info("[ALERT] Mail de confirmation envoyé à %s", email)
            else:
                print(f"📧 [EMAIL DE CONFIRMATION SIMULÉ] Envoyé à {email} (SMTP non configuré dans .env) : Alerte enregistrée pour '{query}'")
        except Exception as e:
            log.warning("[ALERT] Échec de l'envoi du mail de confirmation : %s", e)
            
        return {"id": alert_id, "status": "success"}

    @router.delete("/alerts/{alert_id}")
    async def delete_alert(alert_id: int):
        store.delete_alert(alert_id)
        return {"status": "deleted"}

    return router
