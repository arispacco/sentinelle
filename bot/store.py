"""Persistance dual SQLite & PostgreSQL : offres, commentaires, favoris, runs, alertes.

Thread-safe et résilient pour Render (PostgreSQL managé) et développement local (SQLite).
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

from bot.config import DB_PATH
from bot.models import Comment, Favorite, Offer, Run

log = logging.getLogger(__name__)

try:
    import psycopg2
    import psycopg2.extras
except ImportError:
    psycopg2 = None

SQLITE_SCHEMA = """
CREATE TABLE IF NOT EXISTS offers (
    id TEXT PRIMARY KEY,
    dna_hash TEXT NOT NULL,
    title TEXT, url TEXT, source TEXT, agent TEXT, provider TEXT,
    offer_type TEXT, network TEXT, description TEXT,
    keywords_json TEXT, found_at TEXT, first_seen TEXT, last_seen TEXT,
    expires_at TEXT, credibility_tier TEXT DEFAULT 'pending',
    credibility_score INTEGER DEFAULT 0, provenance_json TEXT DEFAULT '[]',
    status TEXT DEFAULT 'new', vanished INTEGER DEFAULT 0,
    image_url TEXT DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_offers_dna ON offers(dna_hash);
CREATE INDEX IF NOT EXISTS idx_offers_status ON offers(status);
CREATE INDEX IF NOT EXISTS idx_offers_tier ON offers(credibility_tier);

CREATE TABLE IF NOT EXISTS comments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    offer_dna TEXT NOT NULL, source TEXT, author TEXT, text TEXT,
    polarity TEXT DEFAULT 'neutral', found_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_comments_dna ON comments(offer_dna);

CREATE TABLE IF NOT EXISTS favorites (
    key TEXT PRIMARY KEY, kind TEXT, label TEXT, trust_score REAL,
    added_at TEXT, last_verified TEXT, validated_count INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT, started_at TEXT, finished_at TEXT,
    agents_json TEXT, status TEXT, offers_count INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS offer_sources (
    offer_dna TEXT, source TEXT, url TEXT,
    first_seen TEXT, last_seen TEXT, alive INTEGER DEFAULT 1,
    PRIMARY KEY (offer_dna, source)
);

CREATE TABLE IF NOT EXISTS alerts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    email TEXT NOT NULL,
    query TEXT NOT NULL,
    category TEXT NOT NULL,
    location TEXT,
    active INTEGER DEFAULT 1,
    created_at TEXT DEFAULT (datetime('now', 'utc')),
    last_notified_at TEXT
);

CREATE TABLE IF NOT EXISTS alert_notifications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    alert_id INTEGER NOT NULL,
    offer_id TEXT NOT NULL,
    notified_at TEXT DEFAULT (datetime('now', 'utc')),
    UNIQUE(alert_id, offer_id)
);
CREATE INDEX IF NOT EXISTS idx_alert_notif_alert ON alert_notifications(alert_id);
"""

POSTGRES_SCHEMA = """
CREATE TABLE IF NOT EXISTS offers (
    id VARCHAR(100) PRIMARY KEY,
    dna_hash VARCHAR(100) NOT NULL,
    title TEXT, url TEXT, source VARCHAR(100), agent VARCHAR(100), provider VARCHAR(100),
    offer_type VARCHAR(50), network VARCHAR(100), description TEXT,
    keywords_json TEXT, found_at TEXT, first_seen TEXT, last_seen TEXT,
    expires_at TEXT, credibility_tier VARCHAR(50) DEFAULT 'pending',
    credibility_score INTEGER DEFAULT 0, provenance_json TEXT DEFAULT '[]',
    status VARCHAR(50) DEFAULT 'new', vanished INTEGER DEFAULT 0,
    image_url TEXT DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_pg_offers_dna ON offers(dna_hash);
CREATE INDEX IF NOT EXISTS idx_pg_offers_status ON offers(status);
CREATE INDEX IF NOT EXISTS idx_pg_offers_tier ON offers(credibility_tier);

CREATE TABLE IF NOT EXISTS comments (
    id SERIAL PRIMARY KEY,
    offer_dna VARCHAR(100) NOT NULL, source VARCHAR(100), author TEXT, text TEXT,
    polarity VARCHAR(50) DEFAULT 'neutral', found_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_pg_comments_dna ON comments(offer_dna);

CREATE TABLE IF NOT EXISTS favorites (
    key VARCHAR(200) PRIMARY KEY, kind VARCHAR(50), label TEXT, trust_score REAL,
    added_at TEXT, last_verified TEXT, validated_count INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS runs (
    id SERIAL PRIMARY KEY, started_at TEXT, finished_at TEXT,
    agents_json TEXT, status VARCHAR(50), offers_count INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS offer_sources (
    offer_dna VARCHAR(100), source VARCHAR(100), url TEXT,
    first_seen TEXT, last_seen TEXT, alive INTEGER DEFAULT 1,
    PRIMARY KEY (offer_dna, source)
);

CREATE TABLE IF NOT EXISTS alerts (
    id SERIAL PRIMARY KEY,
    email VARCHAR(200) NOT NULL,
    query TEXT NOT NULL,
    category VARCHAR(50) NOT NULL,
    location VARCHAR(100),
    active INTEGER DEFAULT 1,
    last_notified_at TEXT,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS alert_notifications (
    id SERIAL PRIMARY KEY,
    alert_id INTEGER NOT NULL REFERENCES alerts(id) ON DELETE CASCADE,
    offer_id VARCHAR(100) NOT NULL,
    notified_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(alert_id, offer_id)
);
"""


class Store:
    def __init__(self, path=DB_PATH):
        self.db_url = os.getenv("DATABASE_URL")
        self.use_pg = False
        self.conn = None

        if self.db_url:
            try:
                if psycopg2 is None:
                    raise ImportError("psycopg2 non installé")
                self.conn = psycopg2.connect(self.db_url)
                self.conn.autocommit = True
                with self.conn.cursor() as cur:
                    cur.execute(POSTGRES_SCHEMA)
                self.use_pg = True
                log.info("[store] Connecté avec succès à PostgreSQL")
            except Exception as e:
                log.warning("[store] Échec connexion PostgreSQL (%s). Repli sur SQLite.", e)
                self.conn = None

        if not self.use_pg:
            p = Path(path)
            p.parent.mkdir(parents=True, exist_ok=True)
            try:
                self.conn = sqlite3.connect(str(p), check_same_thread=False, isolation_level=None)
                self.conn.row_factory = sqlite3.Row
                self.conn.execute("PRAGMA busy_timeout=10000;")
                try:
                    self.conn.execute("PRAGMA journal_mode=WAL;")
                except sqlite3.OperationalError:
                    pass
                self.conn.executescript(SQLITE_SCHEMA)
                self.conn.commit()
                self._migrate()
            except sqlite3.OperationalError as e:
                log.error("[store] SQLite %s indisponible (%s) — fallback", p, e)
                try:
                    self.conn.close()
                except Exception:
                    pass
                fb = p.with_name(p.stem + ".fallback.db")
                self.conn = sqlite3.connect(str(fb), check_same_thread=False, isolation_level=None)
                self.conn.row_factory = sqlite3.Row
                self.conn.execute("PRAGMA busy_timeout=10000;")
                self.conn.executescript(SQLITE_SCHEMA)
                self.conn.commit()
                self._migrate()

    def _migrate(self):
        """Migrations incrémentales pour SQLite."""
        if not self.use_pg:
            try:
                self.conn.execute("ALTER TABLE alerts ADD COLUMN last_notified_at TEXT")
            except sqlite3.OperationalError:
                pass
            try:
                self.conn.execute("ALTER TABLE offers ADD COLUMN image_url TEXT DEFAULT ''")
            except sqlite3.OperationalError:
                pass
        else:
            with self.conn.cursor() as cur:
                cur.execute(
                    "SELECT COUNT(*) FROM information_schema.columns "
                    "WHERE table_name='offers' AND column_name='image_url'"
                )
                if (cur.fetchone() or [0])[0] == 0:
                    self.conn.execute("ALTER TABLE offers ADD COLUMN image_url TEXT DEFAULT ''")

    def _ensure_pg_conn(self):
        """Vérifie la liveness de la connexion PostgreSQL et reconnecte si nécessaire."""
        if not self.use_pg:
            return
        try:
            if self.conn is None or getattr(self.conn, "closed", 0) != 0:
                self.conn = psycopg2.connect(self.db_url)
                self.conn.autocommit = True
            else:
                with self.conn.cursor() as cur:
                    cur.execute("SELECT 1")
        except Exception:
            try:
                if self.conn:
                    self.conn.close()
            except Exception:
                pass
            self.conn = psycopg2.connect(self.db_url)
            self.conn.autocommit = True

    def _query(self, sql: str, params: tuple = ()) -> list:
        if self.use_pg:
            self._ensure_pg_conn()
            pg_sql = sql.replace("?", "%s")
            with self.conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(pg_sql, params)
                return cur.fetchall()
        else:
            return self.conn.execute(sql, params).fetchall()

    def _query_one(self, sql: str, params: tuple = ()) -> Optional[dict]:
        if self.use_pg:
            self._ensure_pg_conn()
            pg_sql = sql.replace("?", "%s")
            with self.conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(pg_sql, params)
                return cur.fetchone()
        else:
            return self.conn.execute(sql, params).fetchone()

    def _execute(self, sql: str, params: tuple = ()) -> Optional[int]:
        if self.use_pg:
            self._ensure_pg_conn()
            pg_sql = sql.replace("?", "%s")
            if "INSERT OR IGNORE INTO alert_notifications" in pg_sql:
                pg_sql = pg_sql.replace("INSERT OR IGNORE INTO", "INSERT INTO")
                pg_sql += " ON CONFLICT (alert_id, offer_id) DO NOTHING"
            with self.conn.cursor() as cur:
                cur.execute(pg_sql, params)
                return cur.rowcount
        else:
            cur = self.conn.execute(sql, params)
            return cur.rowcount

    # --- offres ---
    def upsert_offer(self, o: Offer) -> None:
        now = datetime.now(timezone.utc).isoformat()
        o.last_seen = now
        if not o.first_seen:
            o.first_seen = now
        self._execute(
            """INSERT INTO offers (id,dna_hash,title,url,source,agent,provider,
               offer_type,network,description,keywords_json,found_at,first_seen,
               last_seen,expires_at,credibility_tier,credibility_score,
               provenance_json,status,vanished,image_url)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(id) DO UPDATE SET
                 last_seen=excluded.last_seen,
                 provenance_json=excluded.provenance_json,
                 description=excluded.description,
                 image_url=COALESCE(NULLIF(excluded.image_url,''), offers.image_url)""",
            (o.id, o.dna_hash, o.title, o.url, o.source, o.agent, o.provider,
             o.offer_type, o.network, o.description, json.dumps(o.keywords_matched),
             o.found_at, o.first_seen, o.last_seen, o.expires_at,
             o.credibility_tier, o.credibility_score,
             json.dumps(o.provenance), o.status, o.vanished,
             getattr(o, "image_url", "") or ""),
        )

    def add_provenance(self, dna: str, source: str, url: str) -> None:
        now = datetime.now(timezone.utc).isoformat()
        self._execute(
            """INSERT INTO offer_sources (offer_dna,source,url,first_seen,last_seen,alive)
               VALUES (?,?,?,?,?,1)
               ON CONFLICT(offer_dna,source) DO UPDATE SET last_seen=excluded.last_seen, alive=1""",
            (dna, source, url, now, now),
        )

    def mark_vanished(self, dna: str) -> int:
        return self._execute(
            "UPDATE offer_sources SET alive=0 WHERE offer_dna=?", (dna,)
        ) or 0

    def set_credibility(self, offer_id: str, tier: str, score: int,
                        provenance: list) -> None:
        self._execute(
            "UPDATE offers SET credibility_tier=?, credibility_score=?, provenance_json=? WHERE id=?",
            (tier, score, json.dumps(provenance), offer_id),
        )

    def set_offer_status(self, offer_id: str, status: str) -> None:
        self._execute("UPDATE offers SET status=? WHERE id=?", (status, offer_id))

    def get_offer(self, offer_id: str) -> Optional[Offer]:
        row = self._query_one("SELECT * FROM offers WHERE id=?", (offer_id,))
        return self._row_to_offer(row) if row else None

    def get_offers(self, tier=None, status=None, network=None,
                   offer_type=None, search=None, limit=500) -> List[Offer]:
        q = "SELECT * FROM offers WHERE 1=1"
        args = []
        if tier:
            q += " AND credibility_tier=?"; args.append(tier)
        if status:
            q += " AND status=?"; args.append(status)
        if network:
            q += " AND network=?"; args.append(network)
        if offer_type:
            q += " AND offer_type=?"; args.append(offer_type)
        if search:
            search_op = "ILIKE" if self.use_pg else "LIKE"
            q += f" AND (title {search_op} ? OR description {search_op} ? OR provider {search_op} ?)"
            args += [f"%{search}%"] * 3
        q += " ORDER BY credibility_score DESC, last_seen DESC LIMIT ?"
        args.append(limit)
        return [self._row_to_offer(r) for r in self._query(q, tuple(args))]

    def get_all_dna(self) -> List[str]:
        rows = self._query("SELECT DISTINCT dna_hash FROM offers")
        return [r["dna_hash"] if isinstance(r, dict) else r[0] for r in rows]

    # --- commentaires ---
    def upsert_comment(self, c: Comment) -> None:
        self._execute(
            """INSERT INTO comments (offer_dna,source,author,text,polarity,found_at)
               VALUES (?,?,?,?,?,?)""",
            (c.offer_dna, c.source, c.author, c.text, c.polarity, c.found_at),
        )

    def get_comments(self, offer_dna: str) -> List[Comment]:
        rows = self._query("SELECT * FROM comments WHERE offer_dna=?", (offer_dna,))
        return [Comment(id=r["id"], offer_dna=r["offer_dna"], source=r["source"],
                        author=r["author"], text=r["text"], polarity=r["polarity"],
                        found_at=r["found_at"]) for r in rows]

    # --- favoris ---
    def add_favorite(self, f: Favorite) -> None:
        val_count_ref = "favorites.validated_count" if self.use_pg else "validated_count"
        self._execute(
            f"""INSERT INTO favorites (key,kind,label,trust_score,added_at,last_verified,validated_count)
               VALUES (?,?,?,?,?,?,?)
               ON CONFLICT(key) DO UPDATE SET validated_count={val_count_ref}+1,
                 trust_score=excluded.trust_score, last_verified=excluded.last_verified""",
            (f.key, f.kind, f.label, f.trust_score, f.added_at, f.last_verified, f.validated_count),
        )

    def get_favorites(self) -> List[Favorite]:
        rows = self._query("SELECT * FROM favorites ORDER BY trust_score DESC")
        return [Favorite(key=r["key"], kind=r["kind"], label=r["label"],
                         trust_score=r["trust_score"], added_at=r["added_at"],
                         last_verified=r["last_verified"],
                         validated_count=r["validated_count"]) for r in rows]

    def get_favorite_keys(self) -> List[str]:
        rows = self._query("SELECT key FROM favorites")
        return [r["key"] if isinstance(r, dict) else r[0] for r in rows]

    # --- runs ---
    def save_run(self, run: Run) -> int:
        if self.use_pg:
            with self.conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO runs (started_at,finished_at,agents_json,status,offers_count) VALUES (%s,%s,%s,%s,%s) RETURNING id",
                    (run.started_at, run.finished_at, json.dumps(run.agents), run.status, run.offers_count)
                )
                return cur.fetchone()[0]
        else:
            cur = self.conn.execute(
                "INSERT INTO runs (started_at,finished_at,agents_json,status,offers_count) VALUES (?,?,?,?,?)",
                (run.started_at, run.finished_at, json.dumps(run.agents),
                 run.status, run.offers_count),
            )
            self.conn.commit()
            return cur.lastrowid

    def update_run(self, run_id: int, status: str, offers_count: int) -> None:
        self._execute(
            "UPDATE runs SET status=?, finished_at=?, offers_count=? WHERE id=?",
            (status, datetime.now(timezone.utc).isoformat(), offers_count, run_id),
        )

    def get_stats(self) -> dict:
        total_row = self._query_one("SELECT COUNT(*) as c FROM offers")
        total = (total_row["c"] if total_row else 0) if isinstance(total_row, dict) else (total_row[0] if total_row else 0)
        
        by_tier_rows = self._query("SELECT credibility_tier, COUNT(*) as c FROM offers GROUP BY credibility_tier")
        by_tier = {r["credibility_tier"]: r["c"] if isinstance(r, dict) else r[1] for r in by_tier_rows}
        
        by_type_rows = self._query("SELECT offer_type, COUNT(*) as c FROM offers GROUP BY offer_type")
        by_type = {r["offer_type"]: r["c"] if isinstance(r, dict) else r[1] for r in by_type_rows}
        
        by_net_rows = self._query("SELECT network, COUNT(*) as c FROM offers GROUP BY network")
        by_network = {r["network"]: r["c"] if isinstance(r, dict) else r[1] for r in by_net_rows}
        
        favs_row = self._query_one("SELECT COUNT(*) as c FROM favorites")
        favs = (favs_row["c"] if favs_row else 0) if isinstance(favs_row, dict) else (favs_row[0] if favs_row else 0)
        
        runs_row = self._query_one("SELECT COUNT(*) as c FROM runs")
        runs = (runs_row["c"] if runs_row else 0) if isinstance(runs_row, dict) else (runs_row[0] if runs_row else 0)
        
        return {
            "total": total, "by_tier": by_tier, "by_type": by_type,
            "by_network": by_network, "favorites": favs, "runs": runs,
            "backend": "postgresql" if self.use_pg else "sqlite",
        }

    def _row_to_offer(self, r) -> Offer:
        try:
            image_url = r["image_url"] or ""
        except (KeyError, IndexError):
            image_url = ""
        return Offer(
            id=r["id"], dna_hash=r["dna_hash"], title=r["title"], url=r["url"],
            source=r["source"], agent=r["agent"], provider=r["provider"],
            offer_type=r["offer_type"], network=r["network"], description=r["description"],
            keywords_matched=json.loads(r["keywords_json"] or "[]"),
            found_at=r["found_at"], first_seen=r["first_seen"], last_seen=r["last_seen"],
            expires_at=r["expires_at"], credibility_tier=r["credibility_tier"],
            credibility_score=r["credibility_score"],
            provenance=json.loads(r["provenance_json"] or "[]"),
            status=r["status"], vanished=r["vanished"],
            image_url=image_url,
        )

    def add_alert(self, email: str, query: str, category: str, location: Optional[str] = None) -> int:
        if self.use_pg:
            with self.conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO alerts (email, query, category, location) VALUES (%s,%s,%s,%s) RETURNING id",
                    (email, query, category, location)
                )
                return cur.fetchone()[0]
        else:
            cursor = self.conn.execute(
                "INSERT INTO alerts (email, query, category, location) VALUES (?,?,?,?)",
                (email, query, category, location)
            )
            self.conn.commit()
            return cursor.lastrowid

    def get_alerts(self, active_only: bool = True) -> List[dict]:
        q = "SELECT * FROM alerts"
        if active_only:
            q += " WHERE active=1"
        q += " ORDER BY created_at DESC"
        rows = self._query(q)
        return [dict(r) for r in rows]

    def delete_alert(self, alert_id: int) -> None:
        self._execute("DELETE FROM alerts WHERE id=?", (alert_id,))

    def record_alert_notification(self, alert_id: int, offer_id: str) -> None:
        """Enregistre qu'une offre a déjà été notifiée pour une alerte donnée."""
        self._execute(
            "INSERT OR IGNORE INTO alert_notifications (alert_id, offer_id) VALUES (?,?)",
            (alert_id, offer_id),
        )

    def get_notified_offer_ids(self, alert_id: int) -> set:
        """Renvoie l'ensemble des offer_id déjà notifiés pour cette alerte."""
        rows = self._query(
            "SELECT offer_id FROM alert_notifications WHERE alert_id=?", (alert_id,)
        )
        return {r["offer_id"] if isinstance(r, dict) else r[0] for r in rows}

    def update_alert_last_notified(self, alert_id: int) -> None:
        """Met à jour l'horodatage de dernière notification."""
        now = datetime.now(timezone.utc).isoformat()
        self._execute("UPDATE alerts SET last_notified_at=? WHERE id=?", (now, alert_id))

    def close(self):
        if self.conn:
            try:
                self.conn.close()
            except Exception:
                pass


