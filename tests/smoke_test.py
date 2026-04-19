"""Offline smoke test for the validator.

This test does NOT call OpenAI or run a real OASIS simulation. It builds
a synthetic SQLite database that mimics the OASIS schema for ``post``,
``comment`` and ``trace``, then drives the scorer + report through it
to verify the wiring.

A real end-to-end run requires Python 3.10 or 3.11 (camel-oasis pins
``<3.12``) and a valid OPENAI_API_KEY; see README for instructions.

Usage:

    python tests/smoke_test.py
"""

from __future__ import annotations

import gc
import json
import sqlite3
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from oasis_validator.report import render_console, render_json
from oasis_validator.scorer import (
    JudgeVerdict,
    _read_comments,
    _read_engagement,
    _read_interviews,
    score_run,
)
from oasis_validator.types import SimulationOutcome


def _make_fake_db(path: Path) -> None:
    conn = sqlite3.connect(str(path))
    cur = conn.cursor()
    cur.executescript(
        """
        CREATE TABLE post (
            post_id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            original_post_id INTEGER,
            content TEXT DEFAULT '',
            quote_content TEXT,
            created_at DATETIME,
            num_likes INTEGER DEFAULT 0,
            num_dislikes INTEGER DEFAULT 0,
            num_shares INTEGER DEFAULT 0,
            num_reports INTEGER DEFAULT 0
        );
        CREATE TABLE comment (
            comment_id INTEGER PRIMARY KEY AUTOINCREMENT,
            post_id INTEGER,
            user_id INTEGER,
            content TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            num_likes INTEGER DEFAULT 0,
            num_dislikes INTEGER DEFAULT 0
        );
        CREATE TABLE trace (
            user_id INTEGER,
            created_at DATETIME,
            action TEXT,
            info TEXT,
            PRIMARY KEY(user_id, created_at, action, info)
        );
        """
    )
    cur.execute(
        "INSERT INTO post (post_id, user_id, content, num_likes, num_dislikes, num_shares) "
        "VALUES (1, 0, 'A SaaS that turns Slack threads into searchable docs', 8, 2, 1)"
    )

    sample_comments = [
        (1, 1, "I would actually pay for this. Searching Slack is a nightmare."),
        (1, 2, "Cool, but how is this different from existing tools like Glean?"),
        (1, 3, "Privacy is the killer concern. Where does the data live?"),
        (1, 4, "Love the wedge. Slack export is a real pain point."),
        (1, 5, "Sounds like a feature, not a product."),
    ]
    cur.executemany(
        "INSERT INTO comment (post_id, user_id, content) VALUES (?, ?, ?)",
        sample_comments,
    )

    interviews = [
        (
            6,
            "2025-01-01 12:00:00",
            "interview",
            json.dumps(
                {
                    "prompt": "Would you use this?",
                    "interview_id": "ts_6",
                    "response": "Yes, but only if SSO and SOC2 are in place.",
                }
            ),
        ),
        (
            7,
            "2025-01-01 12:01:00",
            "interview",
            json.dumps(
                {
                    "prompt": "Would you use this?",
                    "interview_id": "ts_7",
                    "response": "Probably not. Our team uses Notion already.",
                }
            ),
        ),
        (
            8,
            "2025-01-01 12:02:00",
            "interview",
            json.dumps(
                {
                    "prompt": "Would you use this?",
                    "interview_id": "ts_8",
                    "response": "Maybe. Pricing would have to be very low for SMB.",
                }
            ),
        ),
    ]
    cur.executemany(
        "INSERT INTO trace (user_id, created_at, action, info) VALUES (?, ?, ?, ?)",
        interviews,
    )
    conn.commit()
    conn.close()


def main() -> int:
    print("Smoke test: scorer + report (no OASIS, no OpenAI)")
    print("=" * 60)

    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        db_path = Path(tmp) / "fake_run.db"
        _make_fake_db(db_path)

        engagement = _read_engagement(db_path, seed_post_id=1, audience_size=20)
        comments = _read_comments(db_path, seed_post_id=1)
        interviews = _read_interviews(db_path)

        assert engagement.num_likes == 8, engagement
        assert engagement.num_dislikes == 2, engagement
        assert engagement.num_comments == 5, engagement
        assert engagement.num_shares == 1, engagement
        assert 0 <= engagement.score <= 100, engagement
        assert len(comments) == 5, comments
        assert len(interviews) == 3, interviews
        print(f"  engagement.score = {engagement.score:.2f}")
        print(f"  comments         = {len(comments)}")
        print(f"  interviews       = {len(interviews)}")

        outcome = SimulationOutcome(
            db_path=db_path,
            seed_post_id=1,
            poster_agent_id=0,
            interviewed_agent_ids=[6, 7, 8],
            num_agents=20,
            num_reaction_steps=3,
        )

        fake_verdict = JudgeVerdict(
            score=72.0,
            summary=(
                "The audience finds the idea genuinely useful for teams that "
                "live in Slack, with strong concerns about privacy and "
                "differentiation from incumbents like Glean and Notion."
            ),
            top_praises=[
                "Solves a real pain point (Slack search is bad).",
                "Clear wedge for SMB teams.",
            ],
            top_concerns=[
                "Privacy and data residency concerns.",
                "Risk of being 'a feature, not a product'.",
                "Crowded market (Glean, Notion, etc.).",
            ],
            audience_fit="SMB engineering and ops teams that already live in Slack.",
        )

        with patch(
            "oasis_validator.scorer._judge_with_llm",
            return_value=fake_verdict,
        ):
            result = score_run(
                outcome,
                idea="A SaaS that turns Slack threads into searchable internal docs",
                judge_model="gpt-4o-mini",
            )

        assert 0 <= result.final_score <= 100
        assert abs(result.final_score - (0.5 * engagement.score + 0.5 * 72.0)) < 0.01
        assert result.engagement.num_likes == 8
        assert result.sentiment.score == 72.0
        assert len(result.sample_comments) > 0
        print(f"  final_score      = {result.final_score:.2f}")

        json_out = render_json(result)
        parsed = json.loads(json_out)
        assert parsed["final_score"] == result.final_score
        assert parsed["engagement"]["num_likes"] == 8
        assert "summary" in parsed["sentiment"]
        print("  JSON output      = OK")

        print("\n--- console render ---")
        render_console(result)
        print("--- end console render ---")

        del result
        gc.collect()

    print("\nAll smoke checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
