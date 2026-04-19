"""Offline smoke test for the FastAPI backend contract.

This test does not run OASIS or call OpenAI. It patches the simulation
pipeline and verifies:
  - request validation + error envelope
  - simulate -> persist -> fetch roundtrip
  - slug validation and not-found behavior
  - rate limiting semantics

Usage:
    python tests/smoke_test.py
"""

from __future__ import annotations

import asyncio
import importlib
import os
import re
import sys
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from oasis_validator.rate_limit import InMemoryRateLimiter
from oasis_validator.scorer import MarketArtifacts


def _fake_artifacts() -> MarketArtifacts:
    return MarketArtifacts(
        post={
            "title": "A SaaS that turns Slack threads into searchable docs",
            "body": "Engineering managers at 50–500 person companies",
            "likes": 38,
            "dislikes": 6,
            "shares": 9,
            "commentCount": 2,
            "createdAt": "2026-04-19T12:34:56Z",
        },
        thread=[
            {
                "id": "c1",
                "agentId": 7,
                "agent": "PowerUser_42",
                "personaDescription": "Senior backend engineer, opinionated about tooling",
                "type": "vocal",
                "comment": "Strong idea with clear user pain.",
                "likes": 47,
                "dislikes": 3,
                "turn": 1,
                "createdAt": "2026-04-19T12:35:12Z",
                "replies": [],
            },
            {
                "id": "c2",
                "agentId": 12,
                "agent": "Skeptic_9",
                "personaDescription": "Cost-conscious solo founder",
                "type": "vocal",
                "comment": "Differentiation from incumbents is still unclear.",
                "likes": 9,
                "dislikes": 5,
                "turn": 1,
                "createdAt": "2026-04-19T12:36:01Z",
                "replies": [],
            },
        ],
        interviews=[
            {
                "agentId": 7,
                "agent": "PowerUser_42",
                "personaDescription": "Senior backend engineer, opinionated about tooling",
                "prompt": "Would you personally use this?",
                "response": "Yes, if integrations are deep and setup is quick.",
                "createdAt": "2026-04-19T12:38:44Z",
            }
        ],
        traction_score=7.2,
        summary="The market signal is positive, but pricing and differentiation need work.",
    )


def main() -> int:
    print("Smoke test: FastAPI backend contract")
    print("=" * 60)
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp_dir:
        sqlite_path = str(Path(tmp_dir) / "validations.db")
        os.environ["SQLITE_DB_PATH"] = sqlite_path
        os.environ["SIMULATE_RATE_LIMIT_MAX_REQUESTS"] = "10"
        os.environ["SIMULATE_RATE_LIMIT_WINDOW_SECONDS"] = "60"
        os.environ["FRONTEND_ORIGIN"] = "http://localhost:3000"

        import main as backend_main

        importlib.reload(backend_main)
        asyncio.run(backend_main.rate_limiter.reset())
        client = TestClient(backend_main.app)

        health = client.get("/health")
        assert health.status_code == 200, health.text
        assert health.json() == {"status": "ok"}
        print("  health endpoint                OK")

        invalid = client.post(
            "/simulate/market",
            json={
                "idea": "x",
                "targetUser": "y",
                "subreddit": "r/SaaS",
                "extra": "field",
            },
        )
        assert invalid.status_code == 400, invalid.text
        assert invalid.json()["error"] == "invalid_request"
        print("  unknown field rejection        OK")

        with patch(
            "main._run_market_validation",
            new=AsyncMock(return_value=_fake_artifacts()),
        ):
            simulate = client.post(
                "/simulate/market",
                json={
                    "idea": "A SaaS that turns Slack threads into searchable docs",
                    "targetUser": "Engineering managers at 50–500 person companies",
                    "subreddit": "r/SaaS",
                    "numVocal": 5,
                    "turns": 2,
                },
            )
        assert simulate.status_code == 200, simulate.text
        payload = simulate.json()
        assert re.fullmatch(r"^[a-f0-9]{8,64}$", payload["slug"]), payload["slug"]
        assert payload["subreddit"] == "r/SaaS"
        assert payload["tractionScore"] == 7.2
        print("  simulate response shape        OK")

        slug = payload["slug"]
        by_slug = client.get(f"/result/{slug}")
        assert by_slug.status_code == 200, by_slug.text
        by_slug_payload = by_slug.json()
        assert by_slug_payload["slug"] == slug
        assert by_slug_payload["result"] == payload
        assert by_slug.headers.get("cache-control") == "no-store"
        print("  persisted result retrieval     OK")

        interviews = client.get(f"/result/{slug}/interviews")
        assert interviews.status_code == 200, interviews.text
        interviews_payload = interviews.json()
        assert interviews_payload["slug"] == slug
        assert len(interviews_payload["interviews"]) == 1
        assert interviews.headers.get("cache-control") == "no-store"
        print("  interviews retrieval           OK")

        bad_slug = client.get("/result/not-a-valid-slug")
        assert bad_slug.status_code == 400, bad_slug.text
        assert bad_slug.json()["error"] == "invalid_slug"
        print("  invalid slug handling          OK")

        missing = client.get("/result/abcdef12")
        assert missing.status_code == 404, missing.text
        assert missing.json()["error"] == "not_found"
        print("  not found handling             OK")

        backend_main.rate_limiter = InMemoryRateLimiter(
            max_requests=1,
            window_seconds=60,
        )
        with patch(
            "main._run_market_validation",
            new=AsyncMock(return_value=_fake_artifacts()),
        ):
            first = client.post(
                "/simulate/market",
                json={
                    "idea": "a",
                    "targetUser": "b",
                    "subreddit": "r/Test",
                },
            )
            second = client.post(
                "/simulate/market",
                json={
                    "idea": "a2",
                    "targetUser": "b2",
                    "subreddit": "r/Test",
                },
            )
        assert first.status_code == 200, first.text
        assert second.status_code == 429, second.text
        assert second.headers.get("Retry-After"), second.headers
        assert second.json()["error"] == "rate_limited"
        print("  rate limiting + Retry-After    OK")

    print("\nAll smoke checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
