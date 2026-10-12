"""Authentification B2B des professionnels de santé (JWT HMAC maison)."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import sqlite3
import time
from pathlib import Path
from typing import Optional

from bot.config import DATA_DIR

log = logging.getLogger(__name__)

AUTH_DB = DATA_DIR / "auth.db"
_SECRET_INSECURE_DEFAULTS = ("", "change-me-in-production")
SECRET = os.getenv("MEDICAL_AUTH_SECRET", "change-me-in-production")
TTL_HOURS = int(os.getenv("JWT_TTL_HOURS", "72"))
AUTH_OPTIONAL = os.getenv("MEDICAL_AUTH_OPTIONAL", "0") == "1"

if SECRET in _SECRET_INSECURE_DEFAULTS:
    if AUTH_OPTIONAL:
        log.warning(
            "[auth] MEDICAL_AUTH_SECRET absent ou par défaut : JWT signés avec une clé "
            "publiquement connue. À réserver au développement local."
        )
    else:
        raise RuntimeError(
            "MEDICAL_AUTH_SECRET manquant ou par défaut en mode auth obligatoire : "
            "définissez un secret fort avant de lancer l'application."
        )


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64url_decode(data: str) -> bytes:
    pad = "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(data + pad)


def hash_password(password: str, salt: Optional[str] = None) -> str:
    salt = salt or _b64url(os.urandom(16))
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), 120_000)
    return f"{salt}${_b64url(digest)}"


def verify_password(password: str, stored: str) -> bool:
    try:
        salt, _ = stored.split("$", 1)
    except ValueError:
        return False
    return hmac.compare_digest(hash_password(password, salt), stored)


def create_token(payload: dict) -> str:
    header = {"alg": "HS256", "typ": "JWT"}
    body = dict(payload)
    body["exp"] = int(time.time()) + TTL_HOURS * 3600
    body["iat"] = int(time.time())
    h = _b64url(json.dumps(header, separators=(",", ":")).encode())
    p = _b64url(json.dumps(body, separators=(",", ":")).encode())
    sig = hmac.new(SECRET.encode(), f"{h}.{p}".encode(), hashlib.sha256).digest()
    return f"{h}.{p}.{_b64url(sig)}"


def decode_token(token: str) -> Optional[dict]:
    try:
        h, p, s = token.split(".")
        expected = _b64url(hmac.new(SECRET.encode(), f"{h}.{p}".encode(), hashlib.sha256).digest())
        if not hmac.compare_digest(expected, s):
            return None
        payload = json.loads(_b64url_decode(p))
        if int(payload.get("exp", 0)) < int(time.time()):
            return None
        return payload
    except Exception:
        return None


class AuthStore:
    def __init__(self, path: Path = AUTH_DB):
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute(
            """CREATE TABLE IF NOT EXISTS professionals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT UNIQUE NOT NULL,
                license_number TEXT NOT NULL,
                password_hash TEXT NOT NULL,
                full_name TEXT,
                created_at TEXT DEFAULT (datetime('now', 'utc'))
            )"""
        )
        self.conn.execute(
            """CREATE TABLE IF NOT EXISTS access_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT,
                path TEXT,
                created_at TEXT DEFAULT (datetime('now', 'utc'))
            )"""
        )

    def close(self) -> None:
        if self.conn is not None:
            try:
                self.conn.close()
            except Exception:
                pass
            self.conn = None

    def register(self, email: str, license_number: str, password: str, full_name: str = "") -> dict:
        email = email.strip().lower()
        if len(license_number.strip()) < 5:
            raise ValueError("Numéro de licence trop court (min. 5 caractères).")
        if len(password) < 8:
            raise ValueError("Mot de passe trop court (min. 8 caractères).")
        pw = hash_password(password)
        try:
            cur = self.conn.execute(
                "INSERT INTO professionals (email, license_number, password_hash, full_name) VALUES (?,?,?,?)",
                (email, license_number.strip(), pw, full_name.strip()),
            )
        except sqlite3.IntegrityError as e:
            raise ValueError("Cet email est déjà enregistré.") from e
        return {"id": cur.lastrowid, "email": email, "license_number": license_number.strip()}

    def authenticate(self, email: str, password: str, license_number: str = "") -> Optional[dict]:
        email = email.strip().lower()
        row = self.conn.execute("SELECT * FROM professionals WHERE email=?", (email,)).fetchone()
        if not row:
            return None
        if not verify_password(password, row["password_hash"]):
            return None
        if license_number and row["license_number"] != license_number.strip():
            return None
        return {
            "id": row["id"],
            "email": row["email"],
            "license_number": row["license_number"],
            "full_name": row["full_name"] or "",
        }

    def log_access(self, email: Optional[str], path: str) -> None:
        self.conn.execute(
            "INSERT INTO access_logs (email, path) VALUES (?,?)",
            (email or "anonymous", path),
        )


_auth_store: Optional[AuthStore] = None


def get_auth_store() -> AuthStore:
    global _auth_store
    if _auth_store is None:
        _auth_store = AuthStore()
    return _auth_store


def extract_bearer(authorization: Optional[str]) -> Optional[str]:
    if not authorization:
        return None
    parts = authorization.split(" ", 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return None
    return parts[1].strip()


def require_professional(authorization: Optional[str]) -> Optional[dict]:
    """Retourne le payload JWT, ou None si optionnel, ou lève ValueError si requis et invalide."""
    token = extract_bearer(authorization)
    if token:
        payload = decode_token(token)
        if payload:
            return payload
        if not AUTH_OPTIONAL:
            raise ValueError("Token invalide ou expiré.")
        return None
    if AUTH_OPTIONAL:
        return None
    raise ValueError("Authentification professionnelle requise.")
