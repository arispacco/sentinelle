# Sentinelle

Sentinelle est une plateforme de veille : recherche sourcée à partir de collectes web, portail clinique pour des professionnels de santé, et simulateur de marchés (paper trading, sans argent réel).

Le code est ouvert pour que chacun puisse le lire, le lancer, et proposer une amélioration. La première contribution demandée est volontairement petite : **un fichier nouveau**, pour apprendre le chemin Fork → branche → commit → pull request sans conflit de fusion.

- Guide pas à pas : [CONTRIBUTING.md](CONTRIBUTING.md)
- Présentation du projet à traduire : [docs/guide.md](docs/guide.md)
- Issues pour une première pull request : [good first issue](https://github.com/arispacco/sentinelle/issues?q=is%3Aissue+is%3Aopen+label%3A%22good+first+issue%22)
- Code de conduite : [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md)
- Sécurité : [SECURITY.md](SECURITY.md)

Les informations médicales et financières affichées par Sentinelle sont **informatives**. Elles ne constituent ni un diagnostic, ni un conseil en investissement.

## Première contribution

1. Choisis une issue `good first issue` dont personne n'a encore écrit « Je la prends ».
2. Suis les sept étapes de [CONTRIBUTING.md](CONTRIBUTING.md).
3. N'édite pas ce README pour une traduction : chaque langue a son propre fichier sous `docs/i18n/`.

En 2026, une pull request ne sert plus à gagner les lots Hacktoberfest. Elle reste la compétence de base pour collaborer sur un projet, open source ou non. Les issues de traduction sont prévues pour l'atelier. Les issues « Plus tard » attendent que Git soit devenu familier.

Si toutes les issues simples sont déjà prises, ajoute `participants/ton-prenom-ton-nom.md` à partir de [l'exemple](participants/EXEMPLE.md). Une personne, un fichier.

## Lancer en local

Python 3.12 est la version du conteneur de production. Python 3.11 suffit pour les tests.

```bash
git clone https://github.com/arispacco/sentinelle.git
cd sentinelle
python -m venv .venv
```

Windows (PowerShell) :

```powershell
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env
python -m uvicorn web.server:app --reload --port 8000
```

macOS et Linux :

```bash
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
python -m uvicorn web.server:app --reload --port 8000
```

Ouvre `http://localhost:8000`.

Renseigne les clés dans `.env` seulement pour les fonctions qui en ont besoin (Gemini, Firebase, SMTP, Postgres). Le fichier `.env` ne se commite pas. Le détail des variables est dans [.env.example](.env.example).

### Tests

```bash
python -m unittest discover -s tests -p "test_*.py"
```

### Application mobile

Le client Flutter du portail clinique est dans `medical_app/` :

```bash
cd medical_app
flutter pub get
flutter run
```

### Déploiement

Le service web est décrit dans [`render.yaml`](render.yaml) et [`Dockerfile`](Dockerfile). Les secrets se saisissent dans le tableau de bord de l'hébergeur, jamais dans Git. Notes d'exploitation : [docs/OPS.md](docs/OPS.md).

## Carte du dépôt

| Dossier | Rôle |
| --- | --- |
| `web/` | API FastAPI et interface web |
| `bot/` | Veille, synthèse, alertes, simulateur |
| `scrapers/` | Collectes (actualités, emplois, marchés, prédictions) |
| `medical_app/` | Application Flutter |
| `tests/` | Tests Python |
| `docs/` | Documentation à tenir à jour, y compris les traductions |

Deux modèles `Offer` coexistent : `models.py` pour les collectes, `bot/models.py` pour les offres enrichies (crédibilité, provenance). Une issue « Plus tard » demande de documenter cette frontière avant toute fusion.

## Traductions

La version de référence est le français, dans ce README et dans [docs/guide.md](docs/guide.md). Les autres langues vivent dans `docs/i18n/<code>/`, un fichier par langue. La liste et la règle anti-conflit sont dans [docs/i18n/LISEZMOI.md](docs/i18n/LISEZMOI.md).

- English: [docs/i18n/en/README.md](docs/i18n/en/README.md)

## Licence

[MIT](LICENSE) © 2026 Aris Pacco.
