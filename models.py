"""Modèle de données pour une offre IA détectée."""
from __future__ import annotations

import hashlib
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import List
from urllib.parse import urlparse

MAX_IMAGE_URL_LEN = 600

CDATA_PATTERN = re.compile(r"<!\[CDATA\[(.*?)\]\]>", re.DOTALL)


def strip_cdata(value) -> str:
    """Retire les marqueurs CDATA d'un texte RSS et nettoie les espaces."""
    if not isinstance(value, str):
        return ""
    return CDATA_PATTERN.sub(r"\1", value).strip()


def normalize_image_url(value) -> str:
    """Normalise une URL d'image : http(s) uniquement, tronquée raisonnablement.

    Retourne "" pour toute valeur absente, non-string ou non http(s).
    """
    if not isinstance(value, str):
        return ""
    url = value.strip()
    try:
        scheme = (urlparse(url).scheme or "").lower()
    except ValueError:
        return ""
    if scheme not in ("http", "https"):
        return ""
    return url[:MAX_IMAGE_URL_LEN]


@dataclass
class Offer:
    title: str
    url: str
    source: str
    provider: str = "Inconnu"
    offer_type: str = "unknown"
    description: str = ""
    keywords_matched: List[str] = field(default_factory=list)
    found_at: str = ""
    image_url: str = ""

    def __post_init__(self):
        self.title = strip_cdata(self.title)
        self.description = strip_cdata(self.description)
        self.image_url = normalize_image_url(self.image_url)
        if not self.found_at:
            self.found_at = datetime.now(timezone.utc).isoformat()

    @property
    def uid(self) -> str:
        raw = f"{self.url}|{self.title.lower().strip()}"
        return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]

    def to_dict(self) -> dict:
        return asdict(self)
