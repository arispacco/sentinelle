"""Génération d'emails / partages pour une ou plusieurs offres validées.

- render_email(offers) -> dict {subject, body_plain, body_html, share_text, mailto}
- 1 offre : email ciblé. Plusieurs : email combiné (tableau récapitulatif).
Utilisé par les boutons « Partager » (Web Share API / mailto) de la PWA.
"""
from __future__ import annotations

import html as _html
from typing import List
from urllib.parse import quote

from bot.models import Offer

_TYPE_LABEL = {
    "free_tier": "Free tier", "promotion": "Promotion", "pass": "Pass / crédits",
    "trial": "Essai", "training": "Formation", "certification": "Certification",
    "unknown": "Offre",
}
_TIER_EMOJI = {"verified": "🟢", "pas_sur": "🟠", "rejected": "🔴", "pending": "⚪"}


def _badge(tier: str) -> str:
    return _TIER_EMOJI.get(tier or "pending", "⚪")


def render_email(offers: List[Offer]) -> dict:
    if not offers:
        return {"subject": "", "body_plain": "", "body_html": "",
                "share_text": "", "mailto": ""}

    single = len(offers) == 1

    def line_plain(o: Offer) -> str:
        return (f"• {_badge(o.credibility_tier)} {_TYPE_LABEL.get(o.offer_type, 'Offre')}: "
                f"{o.title}\n  Fournisseur: {o.provider} | Réseau: {o.network}\n  Lien: {o.url}\n"
                f"  Score: {o.credibility_score}/100 ({o.credibility_tier})")

    def line_html(o: Offer) -> str:
        return (f"<tr><td>{_badge(o.credibility_tier)}</td>"
                f"<td><a href=\"{_html.escape(o.url)}\">{_html.escape(o.title)}</a></td>"
                f"<td>{_html.escape(o.provider)}</td>"
                f"<td>{_html.escape(_TYPE_LABEL.get(o.offer_type, 'Offre'))}</td>"
                f"<td>{o.credibility_score}/100</td></tr>")

    if single:
        o = offers[0]
        subject = f"🎁 Offre IA : {o.title[:80]}"
        body_plain = (f"{o.title}\n\nFournisseur : {o.provider}\n"
                      f"Type : {_TYPE_LABEL.get(o.offer_type, 'Offre')}\n"
                      f"Réseau : {o.network}\nScore crédibilité : "
                      f"{o.credibility_score}/100 ({o.credibility_tier})\n\n"
                      f"Lien : {o.url}\n\n{o.description}\n\n"
                      f"— partagé via le bot Scrapper IA")
        body_html = (f"<h2>🎁 { _html.escape(o.title)}</h2>"
                     f"<p><b>Fournisseur :</b> {_html.escape(o.provider)} · "
                     f"<b>Type :</b> {_html.escape(_TYPE_LABEL.get(o.offer_type, 'Offre'))} · "
                     f"<b>Réseau :</b> {_html.escape(o.network)}<br>"
                     f"<b>Crédibilité :</b> {_badge(o.credibility_tier)} "
                     f"{o.credibility_score}/100 ({o.credibility_tier})</p>"
                     f"<p><a href=\"{_html.escape(o.url)}\">Ouvrir l'offre →</a></p>"
                     f"<p>{_html.escape(o.description)}</p>"
                     f"<hr><small>— partagé via le bot Scrapper IA</small>")
    else:
        subject = f"🎁 {len(offers)} offres IA à partager"
        body_plain = ("Voici une sélection d'offres IA (gratuites / promotions / "
                      "formations) vérifiées par le bot Scrapper IA :\n\n"
                      + "\n\n".join(line_plain(o) for o in offers)
                      + "\n\n— partagé via le bot Scrapper IA")
        rows = "".join(line_html(o) for o in offers)
        body_html = (f"<h2>🎁 {len(offers)} offres IA à partager</h2>"
                     f"<table border='1' cellpadding='6' cellspacing='0' "
                     f"style='border-collapse:collapse'>"
                     f"<tr><th></th><th>Offre</th><th>Fournisseur</th>"
                     f"<th>Type</th><th>Score</th></tr>{rows}</table>"
                     f"<hr><small>— partagé via le bot Scrapper IA</small>")

    share_text = body_plain
    mailto = f"mailto:?subject={quote(subject)}&body={quote(body_plain)}"
    return {"subject": subject, "body_plain": body_plain,
            "body_html": body_html, "share_text": share_text, "mailto": mailto}


def send_smtp_email(to_email: str, subject: str, body_text: str, body_html: str = None) -> bool:
    """Envoie un email via SMTP si les variables d'environnement SMTP sont configurées.
    
    Variables attendues dans le .env :
    - SMTP_HOST ou SMTP_SERVER (ex: smtp.gmail.com)
    - SMTP_PORT (ex: 587)
    - SMTP_USER ou SMTP_USERNAME
    - SMTP_PASS ou SMTP_PASSWORD
    - SMTP_FROM
    """
    import os
    import smtplib
    from email.mime.multipart import MIMEMultipart
    from email.mime.text import MIMEText
    import logging

    server = os.getenv("SMTP_HOST") or os.getenv("SMTP_SERVER")
    port = os.getenv("SMTP_PORT")
    username = os.getenv("SMTP_USER") or os.getenv("SMTP_USERNAME")
    password = os.getenv("SMTP_PASS") or os.getenv("SMTP_PASSWORD")
    sender = os.getenv("SMTP_FROM", username)
    
    if not (server and port and username and password):
        return False
        
    try:
        msg = MIMEMultipart('alternative')
        msg['Subject'] = subject
        msg['From'] = sender
        msg['To'] = to_email
        
        msg.attach(MIMEText(body_text, 'plain'))
        if body_html:
            msg.attach(MIMEText(body_html, 'html'))
            
        with smtplib.SMTP(server, int(port)) as s:
            s.starttls()
            s.login(username, password)
            s.sendmail(sender, to_email, msg.as_string())
        return True
    except Exception as e:
        logging.getLogger(__name__).warning("[smtp] Échec de l'envoi d'email à %s : %s", to_email, e)
        return False
