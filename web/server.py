"""Serveur FastAPI : API REST + SSE + sert la PWA (statique)."""
from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.cors import CORSMiddleware

from bot.orchestrator import Orchestrator
from bot.store import Store
from web.api import create_router

STATIC_DIR = Path(__file__).resolve().parent / "static"
log = logging.getLogger("server")


def check_and_notify_alerts(store: Store):
    """Vérifie si de NOUVELLES offres correspondent aux alertes configurées.
    
    Ne notifie que les offres qui n'ont pas encore été signalées pour chaque alerte,
    grâce à la table alert_notifications.
    """
    try:
        alerts = store.get_alerts(active_only=True)
        if not alerts:
            return
            
        from bot.medical_store import MedicalStore
        
        # Récupérer les offres récentes
        offers = store.get_offers(limit=100)
        
        # Récupérer les études médicales récentes
        med_store = MedicalStore()
        med_records = med_store.get_records(limit=50)
        
        for alert in alerts:
            alert_id = alert["id"]
            email = alert["email"]
            query = alert["query"].lower()
            cat = alert["category"]
            loc = (alert["location"] or "").lower()
            
            # Récupérer les offres déjà notifiées pour cette alerte
            already_notified = store.get_notified_offer_ids(alert_id)
            
            new_matches = []  # (offer_id, title)
            
            if cat == "medical":
                for r in med_records:
                    rid = r.get("id", r.get("title", ""))
                    if rid in already_notified:
                        continue
                    text_match = (query in r.get("title", "").lower() 
                                  or query in r.get("summary", "").lower() 
                                  or query in r.get("conditions", "").lower())
                    loc_match = (not loc 
                                or loc in r.get("city", "").lower() 
                                or loc in r.get("country", "").lower())
                    if text_match and loc_match:
                        new_matches.append((str(rid), r["title"]))
            else:
                for o in offers:
                    if o.id in already_notified:
                        continue
                    o_type = o.offer_type
                    if cat == "jobs" and o_type != "job":
                        continue
                    if cat == "news" and o_type != "news":
                        continue
                    if cat == "promotions" and o_type not in ["promotion", "free_tier", "pass", "trial", "unknown"]:
                        continue
                        
                    text_match = (query in o.title.lower() 
                                  or query in o.description.lower())
                    if text_match:
                        new_matches.append((o.id, o.title))
            
            if not new_matches:
                continue
                
            log.info("[ALERT] %d nouvelle(s) correspondance(s) pour alerte #%d (%s, '%s').",
                     len(new_matches), alert_id, cat, query)
            
            subject = f"🔔 Alerte Veille : {len(new_matches)} nouvelle(s) correspondance(s) pour '{alert['query']}'"
            body_plain = (
                f"Bonjour,\n\n"
                f"De nouvelles correspondances ont été trouvées pour votre recherche "
                f"'{alert['query']}' dans la catégorie '{cat}' :\n\n"
                + "\n".join(f"• {title}" for _, title in new_matches[:10])
                + "\n\nConsultez votre tableau de bord Scrapper IA pour plus de détails.\n\n"
                f"Cordialement,\nL'équipe Scrapper IA"
            )
            
            from web.email_gen import send_smtp_email
            sent = send_smtp_email(email, subject, body_plain)
            
            # Enregistrer les notifications envoyées (même si SMTP simulé)
            for offer_id, _ in new_matches:
                store.record_alert_notification(alert_id, offer_id)
            store.update_alert_last_notified(alert_id)
            
            if sent:
                log.info("[ALERT] Email de notification envoyé avec succès à %s (%d offres)", email, len(new_matches))
            else:
                print(f"📧 [EMAIL DE NOTIFICATION SIMULÉ] Envoyé à {email} (SMTP non configuré dans .env) : "
                      f"{len(new_matches)} nouvelles correspondances pour '{alert['query']}'")
                      
    except Exception as e:
        log.warning("[ALERT] Échec de la vérification des alertes : %s", e, exc_info=True)


async def periodic_scraping_task(store: Store):
    """Bouclette en tâche de fond qui rafraîchit l'actu et les jobs toutes les 30 min."""
    orch = Orchestrator(store=store)
    # Attendre 10 secondes au démarrage pour laisser le serveur s'initialiser
    await asyncio.sleep(10)
    while True:
        log.info("[background] Lancement du scraping périodique (news + jobs)...")
        try:
            res = await orch.run(only=["news", "jobs"], score=True)
            log.info("[background] Scraping périodique terminé: %d offres insérées.", res.get("offers", 0))
            
            # Vérifier les alertes utilisateurs
            check_and_notify_alerts(store)
        except Exception as e:
            log.exception("[background] Échec du scraping périodique : %s", e)
        # Attendre 30 minutes (1800 secondes)
        await asyncio.sleep(1800)


def create_app(store: Store = None) -> FastAPI:
    app = FastAPI(
        title="Sentinelle — Veille & Recherche Intelligente",
        description="Plateforme de veille en temps réel : chat ancré sur scraping, médical B2B & trading",
        version="3.0.0",
    )
    is_prod = os.getenv("PYTHON_ENV", "development").strip().lower() == "production"
    # Le web a sa propre connexion (WAL) ; l'orchestrateur en aura une autre.
    app.state.store = store or Store()
    app.include_router(create_router(app.state.store))
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    # CORS — support de tous les sous-domaines Render, localhost et origines personnalisées
    cors_origin_env = os.getenv("CORS_ORIGIN", "")
    explicit_origins = [o.strip() for o in cors_origin_env.split(",") if o.strip()]
    if not explicit_origins:
        explicit_origins = [
            "https://sentinelle.onrender.com",
            "http://localhost:8000",
            "http://127.0.0.1:8000",
            "http://localhost:3000",
            "http://localhost:5173",
        ]

    app.add_middleware(
        CORSMiddleware,
        allow_origins=explicit_origins,
        allow_origin_regex=r"^https?://([a-zA-Z0-9_\-]+\.)*(onrender\.com|localhost|127\.0\.0\.1)(:\d+)?$",
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.on_event("startup")
    async def startup_event():
        mode = "PRODUCTION" if is_prod else "DEVELOPMENT"
        log.info("Sentinelle v3.0 démarré en mode %s", mode)
        asyncio.create_task(periodic_scraping_task(app.state.store))

    @app.get("/health")
    @app.head("/health")
    async def top_health():
        stats = {}
        try:
            stats = app.state.store.get_stats()
        except Exception as e:
            stats = {"error": str(e)}
        return {
            "status": "ok",
            "service": "sentinelle",
            "version": "3.0.0",
            "environment": "production" if is_prod else "development",
            "database": stats.get("backend", "unknown"),
            "total_offers": stats.get("total", 0),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    @app.get("/")
    @app.head("/")
    async def index():
        return FileResponse(str(STATIC_DIR / "index.html"))

    @app.get("/manifest.webmanifest")
    async def manifest():
        return FileResponse(str(STATIC_DIR / "manifest.webmanifest"),
                            media_type="application/manifest+json")

    @app.get("/sw.js")
    async def sw():
        return FileResponse(
            str(STATIC_DIR / "sw.js"),
            media_type="application/javascript",
            headers={
                "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
                "Pragma": "no-cache",
                "Expires": "0"
            }
        )

    return app


app = create_app()
