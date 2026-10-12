"""Normalisation : détection de mots-clés, classification du type d'offre,
et nettoyage des descriptions scrapées (boilerplate de sections dupliquées)."""
from __future__ import annotations

import re
from typing import List, Optional

from config import OFFER_KEYWORDS, OFFER_TYPE_PATTERNS

# Titres de sections boilerplate qui polluent les descriptions d'offres
# (« Job Description », « Work Experience », ...) souvent répétés en bloc.
_SECTION_HEADING_RE = re.compile(
    r"^(job\s*description|about\s*(the\s*)?(job|role|company|opportunity)|"
    r"work\s*experience|experience|education(al)?(\s*qualifications?)?|"
    r"competencies|skills(\s*(&|and)\s*(abilities|requirements))?|"
    r"qualifications|required\s*(skills|experience)?|requirements?|"
    r"responsibilities|duties|benefits|what\s*you.?ll\s*do|"
    r"who\s*we\s*are|apply\s*(now|here)|how\s*to\s*apply|"
    r"equal\s*opportunity\s*(employer|statement)|eoe)[:\s.\-–—]*$",
    re.IGNORECASE,
)

_MAX_DESC_SEGMENTS = 12

# Runs de titres de sections collés (« Job DescriptionWork Experience… » ×N)
# produits par get_text() sur du HTML de fiches emploi → supprimés en bloc.
# Le séparateur \s* (et non \b) tolère l'absence totale d'espace entre titres.
_GLUED_HEADINGS_RE = re.compile(
    r"(?:(?:job\s*description|about\s*(?:the\s*)?(?:job|role|company|opportunity)|"
    r"work\s*experience|education(?:al)?(?:\s*qualifications?)?|"
    r"competencies|qualifications|required\s*(?:skills|experience)?|requirements?|"
    r"responsibilities|duties|benefits|who\s*we\s*are|what\s*you.?ll\s*do|"
    r"experience)\s*){2,}",
    re.IGNORECASE,
)


def matches_keywords(text: str) -> List[str]:
    """Retourne la liste (dédupliquée) des mots-clés d'offre présents dans le texte."""
    if not text:
        return []
    low = text.lower()
    return list({kw for kw in OFFER_KEYWORDS if kw in low})


def classify(text: str) -> Optional[str]:
    """Devine le type d'offre à partir du texte (celui avec le plus de correspondances)."""
    if not text:
        return None
    low = text.lower()
    best, best_score = None, 0
    for otype, patterns in OFFER_TYPE_PATTERNS.items():
        score = sum(1 for p in patterns if p in low)
        if score > best_score:
            best, best_score = otype, score
    return best


def clean_description(text: str, max_len: int = 400) -> str:
    """Nettoie une description HTML→texte pour affichage carte :
    déduplique les segments répétés consécutifs et supprime les titres de
    sections boilerplate. Le contenu utile reste intact."""
    if not text:
        return ""
    raw = str(text)
    # 1) Blocs de titres de sections collés (≥2 enchaînés) → supprimés d'un coup
    raw = _GLUED_HEADINGS_RE.sub(" ", raw)
    raw = re.sub(r"\s*\n+\s*", "\n", raw)
    raw = re.sub(r"[•·▪]\s*", "\n", raw)
    segments = [seg.strip() for seg in re.split(r"[\n;]+", raw)]

    cleaned: List[str] = []
    last_kept: Optional[str] = None
    for seg in segments:
        compact = re.sub(r"\s+", " ", seg).strip()
        if not compact:
            continue
        if _SECTION_HEADING_RE.match(compact):
            continue
        if last_kept is not None and compact.lower() == last_kept.lower():
            continue
        # Résidus « . » ou « · » après suppression d'un bloc collé
        compact = re.sub(r"^[\s.·]+|[\s.·]+$", "", compact)
        if not compact:
            continue
        cleaned.append(compact)
        last_kept = compact

    out = " · ".join(cleaned[:_MAX_DESC_SEGMENTS]) if cleaned else text.strip()
    return re.sub(r"\s{2,}", " ", out)[:max_len].strip()
