"""Extraction Open Graph (og:image) pour illustrer les résultats d'actualité.

Les flux RSS (RemoteOK, Arbeitnow, Google News) n'embarquent pas d'image :
on fetch la page de l'article (timeout court, en parallèle) et on lit
<meta property="og:image"> avec repli twitter:image. Le résultat est mis en
cache en base via la colonne image_url existante (upsert_offer).
"""
from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

from models import normalize_image_url

log = logging.getLogger(__name__)

OG_TIMEOUT = 3          # secondes max par page article
OG_MAX_ITEMS = 12       # top N items enrichis par requête
OG_MAX_WORKERS = 6      # threads parallèles
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}


def _is_http_url(url: str) -> bool:
    try:
        return urlparse(url).scheme in ("http", "https")
    except ValueError:
        return False


def _stable_id(o) -> str:
    """Clé de cache : id bot v2 si présent, sinon empreinte racine équivalente."""
    oid = getattr(o, "id", "") or ""
    return oid or getattr(o, "uid", "")


def _cached_image(store, url: str) -> str:
    """Cherche en base une image déjà connue pour cette URL d'article.

    Les offres bot v2 indexent par id (sha1(url|title)) : on interroge donc
    par recherche sur la colonne url plutôt que par id direct.
    """
    try:
        row = store._query_one("SELECT image_url FROM offers WHERE url=?", (url,))
    except Exception:
        return ""
    if not row:
        return ""
    val = row.get("image_url") if isinstance(row, dict) else row[0]
    return normalize_image_url(val or "")


def extract_og_image(url: str) -> str:
    """Récupère l'og:image (repli twitter:image) d'une page article.

    Échec (réseau, parsing, URL invalide) -> chaîne vide, jamais d'exception.
    """
    if not url or not _is_http_url(url):
        return ""
    try:
        resp = requests.get(url, timeout=OG_TIMEOUT, headers=_HEADERS,
                            allow_redirects=True, stream=True)
        resp.raise_for_status()
        # On lit au plus ~64 Ko : les meta tags sont dans le <head>.
        chunk = next(resp.iter_content(chunk_size=65536), b"") or b""
        resp.close()
        html = chunk.decode(resp.encoding or "utf-8", errors="ignore")
    except Exception as e:
        log.debug("[ogimage] fetch échec %s : %s", url, e)
        return ""

    try:
        soup = BeautifulSoup(html, "html.parser")
        for prop in ("og:image", "og:image:secure_url"):
            tag = soup.find("meta", attrs={"property": prop})
            if tag and tag.get("content") and _is_http_url(tag["content"].strip()):
                return tag["content"].strip()
        for name in ("twitter:image", "twitter:image:src"):
            tag = soup.find("meta", attrs={"name": name})
            if tag and tag.get("content") and _is_http_url(tag["content"].strip()):
                return tag["content"].strip()
    except Exception as e:
        log.debug("[ogimage] parse échec %s : %s", url, e)
    return ""


def enrich_with_og_images(offers: list, store=None, limit: int = OG_MAX_ITEMS) -> list:
    """Complète image_url des premières offres sans image, en parallèle.

    Accepte des objets (attribut image_url) ou des dicts (clé image_url).
    store : instance optionnelle de bot.store.Store — sert de cache :
      lecture (get_offer) avant fetch, écriture (upsert_offer) après succès.
    Retourne la liste d'entrée (mutée en place). Jamais d'exception remontée.
    """
    targets = []

    def _get_img(o):
        return o.get("image_url", "") if isinstance(o, dict) else getattr(o, "image_url", "")

    def _set_img(o, image):
        if isinstance(o, dict):
            o["image_url"] = image
        else:
            o.image_url = image

    def _get_url(o):
        return o.get("url", "") if isinstance(o, dict) else getattr(o, "url", "")

    for o in offers:
        if len(targets) >= limit:
            break
        url = _get_url(o)
        if _get_img(o) or not _is_http_url(url):
            continue
        if store is not None:
            cached = _cached_image(store, url)
            if cached:
                _set_img(o, cached)
                continue
        targets.append(o)
    if not targets:
        return offers

    with ThreadPoolExecutor(max_workers=OG_MAX_WORKERS) as pool:
        futures = {pool.submit(extract_og_image, _get_url(o)): o for o in targets}
        for fut, target in futures.items():
            try:
                image = fut.result()
            except Exception as e:
                log.debug("[ogimage] thread échec : %s", e)
                image = ""
            if image:
                _set_img(target, image)
    return offers


def get_store_safely():
    """Store partagé pour le cache image ; None si l'init échoue (jamais bloquant)."""
    try:
        from bot.store import Store
        return Store()
    except Exception as e:
        log.debug("[ogimage] store indisponible : %s", e)
        return None
