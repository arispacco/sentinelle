"""Modèles de données v2 : Offer+, Comment, Favorite, Run, CredibilityScore."""
from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import List, Optional

from models import normalize_image_url, strip_cdata


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class Offer:
    """Offre IA enrichie (crédibilité, provenance, ADN sémantique)."""
    id: str = ""
    dna_hash: str = ""          # empreinte sémantique (dedup + corroboration)
    title: str = ""
    url: str = ""
    source: str = ""            # nom de l'agent (official, forum, telegram...)
    agent: str = ""
    provider: str = "Inconnu"
    offer_type: str = "unknown"
    network: str = "web"        # web/hn/reddit/telegram/youtube/linkedin/x
    description: str = ""
    keywords_matched: List[str] = field(default_factory=list)
    found_at: str = field(default_factory=_now)
    first_seen: str = field(default_factory=_now)
    last_seen: str = field(default_factory=_now)
    expires_at: Optional[str] = None   # inféré depuis le texte
    credibility_tier: str = "pending"  # pending/verified/pas_sur/rejected
    credibility_score: int = 0         # 0-100
    provenance: List[dict] = field(default_factory=list)  # [{source,url,found_at,alive}]
    status: str = "new"                # new/validated/shared/expired/rejected
    vanished: int = 0
    image_url: str = ""                # logo/miniature (http(s) uniquement, normalisée)

    def __post_init__(self):
        self.title = strip_cdata(self.title)
        self.description = strip_cdata(self.description)
        self.image_url = normalize_image_url(self.image_url)
        if not self.id:
            self.id = hashlib.sha1(
                f"{self.url}|{self.title.lower().strip()}".encode()
            ).hexdigest()[:12]
        if not self.agent:
            self.agent = self.source

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Comment:
    """Commentaire rattaché à une offre (pour la polarité)."""
    id: Optional[int] = None
    offer_dna: str = ""
    source: str = ""        # youtube/reddit/hn/telegram
    author: str = ""
    text: str = ""
    polarity: str = "neutral"  # fake_call/neutral/endorsement
    found_at: str = field(default_factory=_now)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Favorite:
    """Source/profil/compte favori (validé par l'utilisateur)."""
    key: str = ""            # url ou handle
    kind: str = "source"     # source/profile/account/channel
    label: str = ""
    trust_score: float = 1.0
    added_at: str = field(default_factory=_now)
    last_verified: str = field(default_factory=_now)
    validated_count: int = 0

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Run:
    """Exécution du bot (historique)."""
    id: int = 0
    started_at: str = field(default_factory=_now)
    finished_at: Optional[str] = None
    agents: List[str] = field(default_factory=list)
    status: str = "running"   # running/done/error
    offers_count: int = 0

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class CredibilityScore:
    """Score de crédibilité décomposé (chaîne de preuves)."""
    offer_id: str = ""
    tier: str = "pending"
    score: int = 0
    polarity_ratio: float = 0.0
    fake_calls: int = 0
    total_comments: int = 0
    corroborating_sources: int = 1
    vanished: bool = False
    expired: bool = False
    reasons: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)
