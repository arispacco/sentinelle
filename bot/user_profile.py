"""Profil utilisateur local (compétences, intérêts, localisation, niveau trading)."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional

from bot.config import DATA_DIR

PROFILE_DB = DATA_DIR / "user_profile.db"
DEFAULT_ID = 1


class UserProfileStore:
    def __init__(self, path: Path = PROFILE_DB):
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute(
            """CREATE TABLE IF NOT EXISTS user_profiles (
                id INTEGER PRIMARY KEY,
                display_name TEXT DEFAULT '',
                skills_json TEXT DEFAULT '[]',
                interests_json TEXT DEFAULT '[]',
                city TEXT DEFAULT '',
                country TEXT DEFAULT '',
                preferred_companies_json TEXT DEFAULT '[]',
                trading_level TEXT DEFAULT 'beginner',
                updated_at TEXT DEFAULT (datetime('now', 'utc'))
            )"""
        )
        row = self.conn.execute("SELECT id FROM user_profiles WHERE id=?", (DEFAULT_ID,)).fetchone()
        if not row:
            self.conn.execute(
                """INSERT INTO user_profiles
                   (id, display_name, skills_json, interests_json, city, country, preferred_companies_json, trading_level)
                   VALUES (?,?,?,?,?,?,?,?)""",
                (DEFAULT_ID, "", "[]", "[]", "", "", "[]", "beginner"),
            )

    def close(self) -> None:
        try:
            self.conn.close()
        except Exception:
            pass

    def get(self) -> Dict[str, Any]:
        row = self.conn.execute("SELECT * FROM user_profiles WHERE id=?", (DEFAULT_ID,)).fetchone()
        return self._row(row)

    def update(self, data: dict) -> Dict[str, Any]:
        cur = self.get()
        display_name = data.get("display_name", cur["display_name"])
        skills = data.get("skills", cur["skills"])
        interests = data.get("interests", cur["interests"])
        city = data.get("city", cur["city"])
        country = data.get("country", cur["country"])
        companies = data.get("preferred_companies", cur["preferred_companies"])
        level = data.get("trading_level", cur["trading_level"])
        if level not in ("beginner", "intermediate", "pro"):
            level = "beginner"
        if isinstance(skills, str):
            skills = [s.strip() for s in skills.split(",") if s.strip()]
        if isinstance(interests, str):
            interests = [s.strip() for s in interests.split(",") if s.strip()]
        if isinstance(companies, str):
            companies = [s.strip() for s in companies.split(",") if s.strip()]
        self.conn.execute(
            """UPDATE user_profiles SET
                display_name=?, skills_json=?, interests_json=?, city=?, country=?,
                preferred_companies_json=?, trading_level=?,
                updated_at=datetime('now', 'utc')
               WHERE id=?""",
            (
                display_name or "",
                json.dumps(list(skills), ensure_ascii=False),
                json.dumps(list(interests), ensure_ascii=False),
                city or "",
                country or "",
                json.dumps(list(companies), ensure_ascii=False),
                level,
                DEFAULT_ID,
            ),
        )
        return self.get()

    def _row(self, row) -> Dict[str, Any]:
        d = dict(row)
        for key, out in (
            ("skills_json", "skills"),
            ("interests_json", "interests"),
            ("preferred_companies_json", "preferred_companies"),
        ):
            try:
                d[out] = json.loads(d.get(key) or "[]")
            except Exception:
                d[out] = []
            d.pop(key, None)
        return d


_profile: Optional[UserProfileStore] = None


def get_profile_store() -> UserProfileStore:
    global _profile
    if _profile is None:
        _profile = UserProfileStore()
    return _profile


def image_store():
    """Store partagé (bot v2) pour le cache og:image ; None si indisponible."""
    try:
        from bot.store import Store
        return Store()
    except Exception:
        return None
