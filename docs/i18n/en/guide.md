# Sentinelle guide

This document explains what the project does, how to run it, and where to contribute. The French version ([`docs/guide.md`](../../guide.md)) is the reference: translations go in `docs/i18n/<code>/guide.md`, not in that file.

## What Sentinelle is for

Sentinelle brings three uses together behind a single web application.

1. **Sourced search.** A question gets a summary based on collected pages, with links to the originals. The language model (Google Gemini, or Ollama locally) does not replace the sources: it summarizes them.
2. **Clinical portal.** Trials and records for healthcare professionals, with authentication for writes. It is not a diagnostic device.
3. **Market simulator.** Paper trading of prices and prediction bets: the portfolio is virtual. It is not investment advice.

## What the project is not

- An emergency medical service.
- A broker or a wallet holding real money.
- A place to store API keys. The empty template is `.env.example`. The real `.env` file stays on your machine.

## Code map

| Path | Contents |
| --- | --- |
| `web/server.py` | FastAPI application, static files, service health (`GET /health`) |
| `web/api.py` | HTTP routes (chat, alerts, medical, markets, predictions) |
| `web/static/` | Web interface (HTML, CSS, JavaScript) |
| `bot/` | Monitoring orchestration, storage, profile, simulator |
| `scrapers/` | Collectors by topic |
| `models.py` | Offer as returned by a scraper |
| `bot/models.py` | Enriched offer (score, provenance, expiry) |
| `tests/` | Automated tests, run without network access as much as possible |
| `medical_app/` | Flutter mobile client |
| `docs/OPS.md` | Backups and operations, not a first contribution |

## Running the server

Prerequisites: Python 3.11 or 3.12, and Git.

```bash
git clone https://github.com/arispacco/sentinelle.git
cd sentinelle
python -m venv .venv
```

Windows:

```powershell
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env
python -m uvicorn web.server:app --reload --port 8000
```

macOS and Linux:

```bash
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
python -m uvicorn web.server:app --reload --port 8000
```

Then open `http://localhost:8000`.

Without `GEMINI_API_KEY`, AI summaries are unavailable. The page and some of the collectors can still start. Never paste a key into a file tracked by Git.

## Checking that it responds

- `GET /health`: the web process is alive (this is the check used at deployment).
- `GET /api/health`: status of the medical storage and the main store.

## Running the tests

From the repository root, with the virtual environment activated:

```bash
python -m unittest discover -s tests -p "test_*.py"
```

The tests set `MEDICAL_AUTH_OPTIONAL=1` when they mount the API. They must not depend on a real secret key.

## Contributing without stepping on each other's toes

Read [CONTRIBUTING.md](../../../CONTRIBUTING.md). During a workshop, each person creates a different file:

- a translation of this guide or of the README;
- a glossary, an FAQ, or an installation guide for a single operating system.

Code fixes (container, tests, simulator) are described in separate issues. They are not for the day you discover GitHub.

## License

The project is under the [MIT](../../../LICENSE) license. You can use it, modify it and redistribute it, as long as you keep the copyright notice.
