"""Persistence layer for completed market simulations."""

from __future__ import annotations

import json
import os
import secrets
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SQLITE_PATH = ROOT / "data" / "validations.db"


@dataclass
class PersistedValidation:
    slug: str
    created_at: str
    idea: str
    target_user: str
    subreddit: str
    num_vocal: int
    turns: int
    result: Dict[str, Any]
    interviews: List[Dict[str, Any]]


def get_db_path() -> Path:
    configured = os.environ.get("SQLITE_DB_PATH", "").strip()
    if configured:
        return Path(configured).expanduser().resolve()
    return DEFAULT_SQLITE_PATH


def _connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_storage(db_path: Optional[Path] = None) -> None:
    path = db_path or get_db_path()
    with _connect(path) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS validations (
                slug TEXT PRIMARY KEY,
                created_at TEXT NOT NULL,
                idea TEXT NOT NULL,
                target_user TEXT NOT NULL,
                subreddit TEXT NOT NULL,
                num_vocal INTEGER NOT NULL,
                turns INTEGER NOT NULL,
                result_json TEXT NOT NULL,
                interviews_json TEXT NOT NULL
            )
            """
        )
        conn.commit()


def generate_slug(db_path: Optional[Path] = None) -> str:
    path = db_path or get_db_path()
    init_storage(path)
    for _ in range(32):
        candidate = secrets.token_hex(8)
        with _connect(path) as conn:
            row = conn.execute(
                "SELECT 1 FROM validations WHERE slug = ? LIMIT 1",
                (candidate,),
            ).fetchone()
            if row is None:
                return candidate
    raise RuntimeError("could not allocate a unique slug")


def save_validation(
    *,
    slug: str,
    idea: str,
    target_user: str,
    subreddit: str,
    num_vocal: int,
    turns: int,
    result: Dict[str, Any],
    interviews: List[Dict[str, Any]],
    created_at: Optional[str] = None,
    db_path: Optional[Path] = None,
) -> str:
    path = db_path or get_db_path()
    init_storage(path)
    final_created_at = created_at or (
        datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    )
    result_json = json.dumps(result, ensure_ascii=False)
    interviews_json = json.dumps(interviews, ensure_ascii=False)

    with _connect(path) as conn:
        conn.execute(
            """
            INSERT INTO validations (
                slug,
                created_at,
                idea,
                target_user,
                subreddit,
                num_vocal,
                turns,
                result_json,
                interviews_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                slug,
                final_created_at,
                idea,
                target_user,
                subreddit,
                num_vocal,
                turns,
                result_json,
                interviews_json,
            ),
        )
        conn.commit()

    return final_created_at


def get_validation_by_slug(
    slug: str,
    db_path: Optional[Path] = None,
) -> Optional[PersistedValidation]:
    path = db_path or get_db_path()
    init_storage(path)
    with _connect(path) as conn:
        row = conn.execute(
            """
            SELECT
                slug,
                created_at,
                idea,
                target_user,
                subreddit,
                num_vocal,
                turns,
                result_json,
                interviews_json
            FROM validations
            WHERE slug = ?
            LIMIT 1
            """,
            (slug,),
        ).fetchone()

    if row is None:
        return None

    return PersistedValidation(
        slug=str(row["slug"]),
        created_at=str(row["created_at"]),
        idea=str(row["idea"]),
        target_user=str(row["target_user"]),
        subreddit=str(row["subreddit"]),
        num_vocal=int(row["num_vocal"]),
        turns=int(row["turns"]),
        result=json.loads(row["result_json"]),
        interviews=json.loads(row["interviews_json"]),
    )
