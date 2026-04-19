"""Read-only inspector for an OASIS validator run database.

Usage:
    python tools/inspect_run.py data/runs/<file>.db
"""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _safe_path(arg: str) -> Path:
    """Resolve and require the DB to live inside the project."""
    candidate = Path(arg).expanduser().resolve()
    try:
        candidate.relative_to(ROOT)
    except ValueError as exc:
        raise SystemExit(f"refusing to read outside project: {candidate}") from exc
    if candidate.suffix != ".db" or not candidate.is_file():
        raise SystemExit(f"not a .db file: {candidate}")
    return candidate


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__)
        return 2
    db_path = _safe_path(argv[1])

    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row

    print(f"=== DB: {db_path.name} ===\n")

    print("--- tables ---")
    for r in con.execute(
        "select name from sqlite_master where type='table' order by name"
    ):
        print(f"  {r['name']}")
    print()

    print("--- seed post ---")
    for r in con.execute(
        "select post_id, user_id, content, num_likes, num_dislikes from post"
    ):
        print(
            f"  post#{r['post_id']}  by user {r['user_id']}  "
            f"likes={r['num_likes']}  dislikes={r['num_dislikes']}"
        )
        print(f"    {r['content']}")
    print()

    print("--- comments ---")
    rows = list(
        con.execute(
            "select comment_id, user_id, content from comment order by comment_id"
        )
    )
    print(f"  {len(rows)} comments")
    for i, r in enumerate(rows, 1):
        body = (r["content"] or "").replace("\n", " ")
        print(f"  [{i}] user {r['user_id']}: {body[:160]}")
    print()

    print("--- like / dislike counts ---")
    likes = con.execute('select count(*) from "like"').fetchone()[0]
    dislikes = con.execute("select count(*) from dislike").fetchone()[0]
    print(f"  likes={likes}  dislikes={dislikes}")
    print()

    print("--- interview traces ---")
    rows = list(
        con.execute(
            "select user_id, info from trace where action='interview' "
            "order by created_at"
        )
    )
    print(f"  {len(rows)} interviews recorded")
    for r in rows:
        info: dict = {}
        if r["info"]:
            try:
                info = json.loads(r["info"])
            except json.JSONDecodeError:
                info = {"_raw": r["info"]}
        resp = info.get("response") or info.get("interview_response") or ""
        if not resp:
            resp = json.dumps(info)[:400]
        print(f"  --- agent {r['user_id']} ---")
        print(f"  {resp[:600]}")
        print()

    con.close()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
