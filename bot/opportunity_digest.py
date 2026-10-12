"""Digest de bienvenue personnalisé pour le hub Opportunités."""
from __future__ import annotations

import json
import logging
from typing import Optional

from bot.ai import call_llm
from bot.opportunities import feed_for_profile
from bot.user_profile import get_profile_store
from models import strip_cdata

log = logging.getLogger(__name__)


def build_welcome_digest(force_refresh: bool = False) -> dict:
    profile = get_profile_store().get()
    name = profile.get("display_name") or "là"
    feed = feed_for_profile(limit=6)
    sources = [
        {
            "title": i.get("title"),
            "url": i.get("url"),
            "provider": i.get("provider"),
            "offer_type": i.get("offer_type"),
            "match_score": i.get("match_score"),
            "description": (i.get("description") or "")[:180],
        }
        for i in feed
    ]

    if not sources:
        message = (
            f"Bonjour {name} ! Je n'ai pas encore trouvé d'opportunités scrapées "
            "qui correspondent à ton profil. Complète tes compétences / ville, ou lance une recherche."
        )
        return {"message": message, "sources": [], "profile": profile}

    data_str = json.dumps(sources, ensure_ascii=False)
    skills = profile.get("skills") or []
    city = profile.get("city") or ""
    prompt = f"""
Tu es l'assistant Scout, ton conversationnel et utile.
Prénom utilisateur : {name}
Profil skills: {skills}
Intérêts: {profile.get('interests')}
Ville/Pays: {city or "?"} / {profile.get("country") or "?"}

OPPORTUNITÉS SCRAPÉES (seule vérité) :
{data_str}

Rédige un message d'accueil en français au format MARKDOWN strict :
- Commence par « Bonjour {{prénom}} ! » puis une phrase d'accroche personnalisée
  qui s'appuie sur le profil (ville et/ou compétences si présentes).
- Ensuite une liste markdown de 3 à 5 opportunités maximum ; chaque puce :
  **[Titre de l'offre](url)** — Organisation · score de correspondance X.X
  (le titre est un lien markdown cliquable, l'URL vient STRICTEMENT des données).
- Termine par une phrase invitant à affiner la recherche (ville, domaine…).
- N'invente AUCUNE offre ni URL absent des données.
- Pas de texte brut hors structure markdown décrite ci-dessus.
"""
    message = call_llm(prompt)
    if not message:
        # Repli déterministe : markdown propre et personnalisé, sans LLM.
        hooks = []
        if city:
            hooks.append(f"Basé à {city},")
        if skills:
            hooks.append("avec ton profil " + " / ".join(str(s) for s in skills[:3]) + ",")
        accroche = " ".join(hooks)
        lines = [
            f"Bonjour {name} ! {accroche} voici les opportunités scrapées qui collent le mieux à ton profil :"
        ]
        for s in sources[:5]:
            title = strip_cdata(s.get("title") or "")
            url = s.get("url") or ""
            org = strip_cdata(s.get("provider") or "")
            score = s.get("match_score")
            label = f"[{title}]({url})" if url else f"**{title}**"
            bits = [org]
            if isinstance(score, (int, float)):
                bits.append(f"score {score:.1f}")
            lines.append(f"- **{label}**" + ((" — " + " · ".join(bits)) if bits else ""))
        lines.append(
            "Précise ta ville ou un domaine qui t'intéresse et j'affine la sélection."
        )
        message = "\n".join(lines)

    return {"message": message, "sources": sources, "profile": profile}
