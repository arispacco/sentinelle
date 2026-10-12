"""Module IA (Gemini) pour la PWA : classification d'intentions, résumés et réponses chat."""
from __future__ import annotations

import json
import logging
import os
import re
from typing import Dict, List, Optional
import requests

from bot.config import ENV_PATH
from models import strip_cdata

log = logging.getLogger(__name__)


def get_gemini_key() -> Optional[str]:
    """Récupère la clé API Gemini depuis l'environnement."""
    return os.getenv("GEMINI_API_KEY")


def call_gemini(prompt: str, json_mode: bool = False) -> Optional[str]:
    """Appelle l'API Gemini en direct via REST (sans dépendance lourde).

    Rotation automatique entre modèles : chaque modèle Gemini possède son propre
    quota (RPM/RPD) ; en cas de 429/404/503, on tente le modèle suivant.
    """
    key = get_gemini_key()
    if not key:
        return None

    primary = os.getenv("GEMINI_MODEL", "gemini-flash-latest")
    fallback_candidates = ["gemini-flash-latest", "gemini-flash-lite-latest"]
    seen = set()
    models = []
    for m in [primary] + fallback_candidates:
        if m and m not in seen:
            seen.add(m)
            models.append(m)

    payload: Dict = {
        "contents": [{"parts": [{"text": prompt}]}]
    }
    
    if json_mode:
        payload["generationConfig"] = {
            "responseMimeType": "application/json"
        }

    headers = {"Content-Type": "application/json"}
    for model in models:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}"
        try:
            resp = requests.post(url, headers=headers, json=payload, timeout=12)
            if resp.status_code in (401, 403):
                log.warning("[AI] Clé API Gemini non autorisée / invalide (HTTP %d). Appel ignoré.", resp.status_code)
                return None
            if resp.status_code in (429, 404, 503):
                log.warning("[AI] Gemini %s indisponible (HTTP %d) — modèle suivant", model, resp.status_code)
                continue
            resp.raise_for_status()
            res_data = resp.json()
            
            candidates = res_data.get("candidates", [])
            if candidates:
                parts = candidates[0].get("content", {}).get("parts", [])
                # Ignorer les parties "thought" des modèles de raisonnement (2.5+/3.x)
                texts = [p.get("text", "") for p in parts if p.get("text") and not p.get("thought")]
                if texts:
                    return "".join(texts).strip()
            return None
        except requests.Timeout:
            log.warning("[AI] Timeout Gemini (%s) dépassé (12s) — modèle suivant", model)
            continue
        except Exception as e:
            log.warning("[AI] Erreur appel Gemini (%s) : %s", model, e)
    
    return None


def call_ollama(prompt: str) -> Optional[str]:
    """Appelle l'API Ollama locale si elle est disponible."""
    host = os.getenv("OLLAMA_HOST", "http://localhost:11434")
    model = os.getenv("OLLAMA_MODEL", "mistral")
    url = f"{host}/api/generate"
    try:
        payload = {
            "model": model,
            "prompt": prompt,
            "stream": False
        }
        resp = requests.post(url, json=payload, timeout=8)
        resp.raise_for_status()
        return resp.json().get("response", "").strip()
    except Exception as e:
        log.debug("[AI] Ollama non disponible sur %s : %s", host, e)
        return None


def call_llm(prompt: str, json_mode: bool = False) -> Optional[str]:
    """Appelle le meilleur LLM disponible (Gemini par défaut, Ollama en repli ou préférence)."""
    prefer_ollama = os.getenv("PREFER_OLLAMA", "false").lower() == "true"
    if prefer_ollama:
        res = call_ollama(prompt)
        if res:
            return res
            
    res = call_gemini(prompt, json_mode)
    if res:
        return res
        
    # Repli final sur Ollama
    if not prefer_ollama:
        return call_ollama(prompt)
        
    return None


_GENERIC_NEWS_MARKERS = (
    "quoi de neuf", "quoi de nouveau", "les news", "les actu", "les actus",
    "actualités", "actualites", "aujourd'hui", "cette semaine", "panorama",
    "digest", "résumé du jour", "resume du jour", "dernières nouvelles",
    "dernieres nouvelles", "top actu", "à la une", "a la une",
)
_SPECIFIC_NEWS_MARKERS = (
    "parle-moi", "parle moi", "c'est quoi", "cest quoi", "explique",
    "modèle", "modele", "model", "qui est", "comment fonctionne",
    "détaille", "detaille", "fais-moi un résumé de", "resume de",
)


def infer_specificity(message: str, intent: str = "search_news", category: str = None, query: str = "") -> str:
    """Déduit specific|generic pour search_news (heuristique déterministe)."""
    if intent != "search_news":
        return "generic"
    low = (message or "").lower().strip()
    if any(m in low for m in _GENERIC_NEWS_MARKERS):
        return "generic"
    if any(m in low for m in _SPECIFIC_NEWS_MARKERS):
        return "specific"
    # Catégorie seule / requête très courte → panorama
    q = (query or "").strip().lower()
    cat_aliases = {
        "top", "tech", "sport", "finance", "politic", "politique", "economie",
        "économie", "afrique", "international", "ia", "ai", "actu", "news",
    }
    if category and (not q or q in cat_aliases or q == str(category).lower()):
        return "generic"
    if len(low.split()) <= 4 and category:
        return "generic"
    # Question avec sujet nommé distinct → spécifique
    if len(low.split()) >= 5 or (q and len(q.split()) >= 2 and q not in cat_aliases):
        return "specific"
    return "generic"


def _normalize_intent_result(data: dict, message: str) -> dict:
    """Garantit specificity cohérente sur la sortie LLM ou heuristique."""
    intent = data.get("intent") or "chat"
    category = data.get("category")
    query = data.get("query") or ""
    raw_spec = (data.get("specificity") or "").strip().lower()
    if intent == "search_news":
        if raw_spec in ("specific", "generic"):
            data["specificity"] = raw_spec
        else:
            data["specificity"] = infer_specificity(message, intent, category, query)
    else:
        data["specificity"] = None
    return data


def parse_user_intent(message: str) -> dict:
    """Analyse la requête pour en extraire l'intention et les mots-clés."""
    key = get_gemini_key()
    if key:
        prompt = f"""
        Tu es le routeur d'intention d'un hub de recherche ancré sur le scraping (style Perplexity).
        Message : "{message}"

        Réponds UNIQUEMENT en JSON valide :
        - "intent" parmi :
            * "search_jobs"
            * "search_scholarships"
            * "search_travel"
            * "search_events"
            * "search_news"
            * "search_offers"
            * "search_medical"
            * "search_crypto"
            * "search_geopolitics"
            * "search_predictions"
            * "create_watch" (si l'utilisateur veut suivre une entreprise, un conflit, un événement dans le temps / dashboard)
            * "chat" (salutations uniquement, sans recherche)
        - "query" : requête nettoyée (pour create_watch : nom de l'entité ou du sujet)
        - "category" : si search_news : top|tech|sport|finance|politic|economie|afrique|international|ia sinon null
        - "specificity" : "specific" si la question vise UN sujet précis (ex. un modèle, une entreprise, un événement nommé) ;
          "generic" si c'est un panorama / digeste large (ex. « les news sur l'IA », « quoi de neuf aujourd'hui ») ;
          null si intent ≠ search_news
        - "reply" : courte phrase de transition en français
        """
        res = call_llm(prompt, json_mode=True)
        if res:
            try:
                data = json.loads(res)
                if "intent" in data:
                    return _normalize_intent_result(data, message)
            except Exception:
                log.warning("[AI] Échec parsing JSON Gemini : %s", res)

    low = message.lower()

    if any(x in low for x in ["suis ", "suivre", "suivi", "watch", "dashboard", "tableau de bord", "monitorer", "surveille"]):
        query = message
        for word in ["je", "veux", "voudrais", "aimerais", "suis", "suivre", "le", "la", "les", "l'", "suivi", "de", "du", "des", "évolution", "evolution", "actu", "actualité"]:
            query = re.sub(rf"\b{word}\b", " ", query, flags=re.IGNORECASE)
        query = " ".join(query.split())
        return _normalize_intent_result({
            "intent": "create_watch",
            "query": query or message.strip(),
            "category": None,
            "reply": f"Je crée / mets à jour un tableau de suivi pour « {query or message.strip()} »...",
        }, message)

    if any(x in low for x in ["crypto", "bitcoin", "btc", "ethereum", "eth", "coingecko", "token", "blockchain"]):
        query = re.sub(r"\b(cherche|trouve|crypto|prix|cours|de|du|des|le|la|les)\b", " ", message, flags=re.I)
        query = " ".join(query.split())
        return _normalize_intent_result({
            "intent": "search_crypto",
            "query": query or "bitcoin",
            "category": None,
            "reply": f"Je consulte les marchés et actus crypto pour '{query or 'bitcoin'}'...",
        }, message)

    if any(x in low for x in ["polymarket", "prédiction", "prediction", "probabilité", "probabilite", "pari", "odds", "marché de prédiction"]):
        query = re.sub(r"\b(cherche|trouve|polymarket|prédiction|prediction|probabilité|sur|de|du|des)\b", " ", message, flags=re.I)
        query = " ".join(query.split())
        return _normalize_intent_result({
            "intent": "search_predictions",
            "query": query or "election",
            "category": None,
            "reply": f"Je récupère les probabilités Polymarket pour '{query or 'election'}'...",
        }, message)

    if any(x in low for x in ["guerre", "conflit", "géopolit", "geopolit", "sanctions", "ukraine", "gaza", "otan", "taiwan", "sahel"]):
        query = message
        for word in ["cherche", "trouve", "actu", "actualite", "sur", "le", "la", "les", "de"]:
            query = re.sub(rf"\b{word}\b", "", query, flags=re.IGNORECASE)
        query = " ".join(query.split())
        return _normalize_intent_result({
            "intent": "search_geopolitics",
            "query": query or "conflit",
            "category": None,
            "reply": f"Je rassemble des sources géopolitiques sur '{query or 'conflit'}'...",
        }, message)

    if any(x in low for x in ["medecin", "medical", "essai", "clinique", "pathologie", "traitement", "paludisme", "malaria", "drepanocytose", "sickle cell", "hiv", "vih", "tuberculose", "tuberculosis", "cancer", "sante", "santé", "pubmed"]):
        query = message
        for word in ["cherche", "trouve", "trouver", "les", "des", "essais", "cliniques", "clinique", "sur", "le", "la", "l'", "pour", "contre"]:
            query = re.sub(rf"\b{word}\b", "", query, flags=re.IGNORECASE)
        query = " ".join(query.split())
        return _normalize_intent_result({
            "intent": "search_medical",
            "query": query or "malaria",
            "category": None,
            "reply": f"Je recherche des études cliniques et publications sur '{query or 'malaria'}'..."
        }, message)

    if any(x in low for x in ["bourse", "bourses", "scholarship", "fellowship", "grant study"]):
        query = message
        for word in ["cherche", "trouve", "trouver", "des", "les", "bourses", "bourse", "scholarship", "scholarships", "de", "pour", "en"]:
            query = re.sub(rf"\b{word}\b", "", query, flags=re.IGNORECASE)
        query = " ".join(query.split())
        return _normalize_intent_result({
            "intent": "search_scholarships",
            "query": query or "scholarship",
            "category": None,
            "reply": f"Je cherche des bourses / scholarships pour « {query or 'études'} »...",
        }, message)

    if any(x in low for x in ["voyage", "travel grant", "mobilité", "mobilite", "visa opportunity", "échange", "echange"]):
        query = message
        for word in ["cherche", "trouve", "trouver", "des", "les", "voyages", "voyage", "opportunités", "opportunites", "de", "pour", "en"]:
            query = re.sub(rf"\b{word}\b", "", query, flags=re.IGNORECASE)
        query = " ".join(query.split())
        return _normalize_intent_result({
            "intent": "search_travel",
            "query": query or "mobilité",
            "category": None,
            "reply": f"Je cherche des opportunités de voyage / mobilité pour « {query or 'mobilité'} »...",
        }, message)

    if any(x in low for x in ["événement", "evenement", "events", "event", "conférence", "conference", "meetup", "hackathon"]):
        query = message
        for word in ["cherche", "trouve", "trouver", "des", "les", "événements", "evenements", "événement", "evenement", "events", "event", "de", "pour", "en"]:
            query = re.sub(rf"\b{word}\b", "", query, flags=re.IGNORECASE)
        query = " ".join(query.split())
        return _normalize_intent_result({
            "intent": "search_events",
            "query": query or "tech",
            "category": None,
            "reply": f"Je cherche des événements pour « {query or 'tech'} »...",
        }, message)

    if any(x in low for x in ["job", "emploi", "recrute", "travail", "stage", "alternance", "position", "career", "poste", "developer"]):
        query = message
        for word in ["cherche", "trouve", "trouver", "des", "jobs", "job", "offres", "offre", "d'emploi", "emploi", "de", "pour", "en"]:
            query = re.sub(rf"\b{word}\b", "", query, flags=re.IGNORECASE)
        query = " ".join(query.split())
        return _normalize_intent_result({
            "intent": "search_jobs",
            "query": query or "developer",
            "category": None,
            "reply": f"Je recherche des offres d'emploi pour '{query or 'developer'}'..."
        }, message)

    if any(x in low for x in ["actu", "news", "nouvelle", "actualite", "actualité", "sport", "tech", "finance", "politique", "economie", "économie", "ia"]):
        category = "top"
        # IA avant tech (sinon « news IA » tombe en tech)
        if "ia" in low or "intelligence artificielle" in low or re.search(r"\bai\b", low):
            category = "ia"
        elif "afrique" in low or "africain" in low or "sahel" in low:
            category = "afrique"
        elif "internation" in low or "monde" in low:
            category = "international"
        elif "tech" in low:
            category = "tech"
        elif "sport" in low:
            category = "sport"
        elif "finance" in low or "affaires" in low:
            category = "finance"
        elif "politi" in low:
            category = "politic"
        elif "econo" in low:
            category = "economie"
        query = message
        for word in ["actu", "actus", "news", "nouvelles", "actualites", "actualité", "sur", "l'", "la", "le", "les"]:
            query = re.sub(rf"\b{word}\b", "", query, flags=re.IGNORECASE)
        query = " ".join(query.split())
        return _normalize_intent_result({
            "intent": "search_news",
            "query": query or category,
            "category": category,
            "reply": f"Je consulte les actualités '{category}'..."
        }, message)

    if any(x in low for x in ["promo", "reduction", "free", "gratuit", "discount", "credits", "offre"]):
        query = message
        for word in ["cherche", "trouve", "promos", "promo", "gratuit", "free", "credits", "offres"]:
            query = re.sub(rf"\b{word}\b", "", query, flags=re.IGNORECASE)
        query = " ".join(query.split())
        return _normalize_intent_result({
            "intent": "search_offers",
            "query": query or "AI",
            "category": None,
            "reply": f"Je recherche des promotions liées à '{query or 'AI'}'..."
        }, message)

    # Recherche générique -> news scrape
    if len(message.strip()) > 12 and not any(x in low for x in ["bonjour", "salut", "hello", "merci", "hey"]):
        return _normalize_intent_result({
            "intent": "search_news",
            "query": message.strip(),
            "category": None,
            "reply": f"Je lance une recherche sourcée sur « {message.strip()[:60]} »...",
        }, message)

    return _normalize_intent_result({
        "intent": "chat",
        "query": "",
        "category": None,
        "reply": "Bonjour. Pose une question : j'irai scraper des sources et je répondrai uniquement à partir d'elles."
    }, message)


def generate_summary(title: str, text: str) -> str:
    """Génère un résumé en français d'un texte d'actualité/offre."""
    key = get_gemini_key()
    if key or os.getenv("OLLAMA_HOST") or os.getenv("PREFER_OLLAMA") == "true":
        prompt = f"""
        Résume le texte suivant en une seule phrase courte et percutante en français (maximum 120 caractères).
        Titre : {title}
        Texte : {text[:1000]}
        """
        summary = call_llm(prompt)
        if summary:
            return summary

    if len(text) > 150:
        return text[:147] + "..."
    return text


def _build_numbered_sources(offers: list, max_items: int = 8) -> tuple:
    """Numérote les items [1..N] pour les citations inline du digest."""
    numbered = []
    for i, o in enumerate(offers[:max_items], start=1):
        numbered.append({
            "n": i,
            "title": strip_cdata(getattr(o, "title", "")),
            "description": getattr(o, "description", ""),
            "provider": getattr(o, "provider", ""),
            "url": getattr(o, "url", ""),
            "type": getattr(o, "offer_type", ""),
        })
    return numbered


def synthesize_digest(user_message: str, offers: list, specificity: str = "generic") -> str:
    """Synthèse ancrée STRICTEMENT sur les items scrapés (pas de connaissances hors sources).

    Structure type Perplexity : phrase d'accroche directe, points clés avec
    citations inline [n], puis contexte final. Markdown en sortie.

    specificity=specific → réponse strictement centrée sur la question (pas de panorama).
    specificity=generic → multi-sujets autorisé.
    """
    if not offers:
        return ""

    is_specific = (specificity or "generic").lower() == "specific"
    data_str = json.dumps(_build_numbered_sources(offers), ensure_ascii=False)

    if is_specific:
        mode_rules = """5. MODE SPÉCIFIQUE — OBLIGATOIRE :
   - Réponds UNIQUEMENT à la question posée. Refuse toute digression.
   - Interdiction de dérouler un panorama ou une liste d'actus connexes (autres entreprises, autres modèles, « aussi dans l'actu »…).
   - Un vrai résumé complet et ciblé sur le sujet demandé, même si plusieurs sources traitent d'angles proches du MÊME sujet.
   - Si une source est hors-sujet, ignore-la silencieusement."""
        structure_b = (
            "b) 3 à 5 points clés en liste markdown, TOUS centrés sur le sujet de la question ; "
            "chaque point cite ses sources entre crochets : [1], [2]…"
        )
    else:
        mode_rules = """5. MODE GÉNÉRIQUE (panorama) :
   - Plusieurs sujets distincts sont bienvenus (style digeste / panorama).
   - Chaque point clé couvre idéalement un sujet différent tiré des sources."""
        structure_b = (
            "b) 3 à 5 points clés en liste markdown ; chaque point cite ses sources "
            "entre crochets : [1], [2]… correspondant aux numéros de la liste."
        )

    key = get_gemini_key()
    if key or os.getenv("OLLAMA_HOST") or os.getenv("PREFER_OLLAMA", "").lower() == "true":
        prompt = f"""Tu es un moteur de synthèse d'actualités ancré sur des sources scrapées (style Perplexity).
Question utilisateur : "{user_message}"

SOURCES SCRAPÉES NUMÉROTÉES (seule vérité autorisée) :
{data_str}

Règles ABSOLUES :
1. Réponds UNIQUEMENT à partir des sources numérotées ci-dessus. Aucune connaissance externe.
2. Si les sources sont insuffisantes ou hors-sujet, dis-le clairement sans inventer.
3. STRUCTURE OBLIGATOIRE en français (markdown) :
   a) Une première phrase directe qui répond à la question — interdiction de commencer par une formule générique (« Voici ce que les sources indiquent », « D'après ma recherche », etc.).
   {structure_b}
   c) Une dernière phrase courte de contexte ou de mise en perspective, uniquement si les sources le permettent.
4. N'ajoute aucun conseil financier, médical ou militaire au-delà de ce que disent les sources.
{mode_rules}
"""
        res = call_llm(prompt)
        if res:
            return res

    # Fallback déterministe sans LLM : même structure Perplexity.
    srcs = _build_numbered_sources(offers)
    if is_specific:
        lead = f"Voici ce que les sources disent sur « {user_message} »."
    else:
        lead = f"Voici l'essentiel des sources scrapées sur « {user_message} »."
    lines = [lead, ""]
    for s in srcs:
        title = s["title"] or s["url"]
        provider = s["provider"] or "Source"
        desc = (s["description"] or "").strip()
        line = f"- **[{s['n']}] {title}** — {provider}"
        if desc:
            clean = " ".join(desc.split())
            line += f" : {clean[:160]}"
        if s["url"]:
            line += f" ([source]({s['url']}))"
        lines.append(line)
    lines.append("")
    lines.append(
        "Contexte : ces résultats proviennent directement des flux scrapés à "
        "l'instant ; consulte les articles liés pour aller plus loin."
    )
    return "\n".join(lines)

def generate_medical_cheat_sheet(
    title: str,
    summary: str,
    criteria: str,
    nct_id: Optional[str] = None,
    phase: Optional[str] = None,
    status: Optional[str] = None,
    sponsor: Optional[str] = None,
) -> Optional[str]:
    """Prend un protocole clinique brut et le résume en fiche 30s, avec garde-fous."""
    summary = (summary or "").strip()
    criteria = (criteria or "").strip()
    title = (title or "").strip()

    # Garde-fou : refuser si la source est trop pauvre pour éviter les hallucinations
    if len(summary) < 40 and len(criteria) < 40:
        log.info("[AI] Cheat sheet refusée (source trop pauvre) pour: %s", title[:80])
        return None

    key = get_gemini_key()
    if not (key or os.getenv("OLLAMA_HOST") or os.getenv("PREFER_OLLAMA", "").lower() == "true"):
        return None

    cite = nct_id or "source non fournie"
    prompt = f"""
Tu es un assistant d'aide à la décision clinique B2B pour professionnels de santé uniquement.
Synthétise STRICTEMENT à partir des données fournies. N'invente AUCUN effet secondaire, critère ou contact absent du texte.
Si une information manque, écris « Non précisé dans la source ».

Identifiant officiel à citer : {cite}
Titre : {title}
Phase déclarée : {phase or "Non précisé"}
Statut déclaré : {status or "Non précisé"}
Sponsor déclaré : {sponsor or "Non précisé"}
Résumé source : {summary[:1500]}
Critères source : {criteria[:1500]}

Structure STRICTE (Markdown) :
### MÉMO CLINIQUE
* **Référence** : {cite}
* **Objectif** : ...
* **Critères d'inclusion clés** : ...
* **Critères d'exclusion clés** : ...
* **Phase & Statut** : ...
* **Effets secondaires majeurs** : ... (ou Non précisé dans la source)
* **Sponsor / Contact** : ...
* **Disclaimer** : Fiche d'aide à la décision pour professionnels ; ne remplace pas le jugement clinique ni le protocole officiel.
"""

    res = call_llm(prompt)
    if not res:
        return None
    # Garde-fou post-génération : la fiche doit citer l'identifiant si disponible
    if nct_id and nct_id not in res:
        res = f"* **Référence** : {nct_id}\n\n" + res
    return res


def get_embedding(text: str) -> Optional[List[float]]:
    """Génère l'embedding vectoriel d'un texte (gemini-embedding-001, 768 dimensions).

    Les modèles text-embedding-00x ont été retirés de l'API ; gemini-embedding-001
    accepte outputDimensionality=768 (Matryoshka), ce qui reste compatible avec le
    schéma Postgres vector(768) et réduit le stockage (~3 Ko / dossier).
    """
    key = get_gemini_key()
    if not key or not text:
        return None
    model = os.getenv("GEMINI_EMBED_MODEL", "gemini-embedding-001")
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:embedContent?key={key}"
    headers = {"Content-Type": "application/json"}
    payload = {
        "model": f"models/{model}",
        "content": {
            "parts": [{"text": text}]
        },
        "outputDimensionality": 768
    }
    try:
        resp = requests.post(url, headers=headers, json=payload, timeout=8)
        resp.raise_for_status()
        data = resp.json()
        return data.get("embedding", {}).get("values")
    except Exception as e:
        log.warning("[AI] Échec de la génération d'embeddings : %s", e)
        return None
