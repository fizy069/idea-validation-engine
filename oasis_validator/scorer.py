"""Scoring layer for OASIS idea validation runs.

Reads the SQLite database produced by ``simulator.run_simulation`` and
combines two signals into one 0-100 hybrid score:

1. Engagement score: derived from likes, dislikes, comment volume and
   shares on the seed post, normalized by audience size.
2. Sentiment score: an LLM "judge" reads every comment on the seed post
   and every interview answer, then returns a structured JSON verdict
   that we validate strictly with Pydantic before consuming.

Security / robustness notes:

- All SQLite queries are parameterized; no string concatenation of
  untrusted values goes into SQL.
- The judge model is prompted to emit JSON, but its output is treated as
  untrusted. We require ``response_format=json_object`` on the API call
  *and* validate the parsed JSON against a Pydantic schema before any
  field is accessed.
- The OpenAI API key is read by the SDK from ``OPENAI_API_KEY`` in the
  environment; this module does not log, store, or echo it.
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from openai import OpenAI
from pydantic import BaseModel, Field, ValidationError

from oasis_validator.types import INTERVIEW_ACTION_VALUE, SimulationOutcome

logger = logging.getLogger(__name__)

JUDGE_MAX_PRAISE_OR_CONCERN = 5
JUDGE_MAX_FEEDBACK_CHARS = 24_000


@dataclass
class EngagementBreakdown:
    """Raw and normalized engagement signals on the seed post."""

    audience_size: int
    num_likes: int
    num_dislikes: int
    num_comments: int
    num_shares: int
    score: float


@dataclass
class JudgeVerdict:
    """LLM judge output (already validated)."""

    score: float
    summary: str
    top_praises: List[str]
    top_concerns: List[str]
    audience_fit: str


@dataclass
class ValidationResult:
    """Final, user-facing output of a validation run."""

    idea: str
    final_score: float
    engagement: EngagementBreakdown
    sentiment: JudgeVerdict
    sample_comments: List[str] = field(default_factory=list)
    sample_interviews: List[Dict[str, str]] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "idea": self.idea,
            "final_score": self.final_score,
            "engagement": asdict(self.engagement),
            "sentiment": asdict(self.sentiment),
            "sample_comments": self.sample_comments,
            "sample_interviews": self.sample_interviews,
            "notes": self.notes,
        }


class _JudgeSchema(BaseModel):
    """Strict schema for the LLM judge response.

    We coerce numeric strings into floats and clamp ``score`` into the
    [0, 100] range elsewhere; here we just enforce the shape.
    """

    score: float = Field(..., ge=0, le=100)
    summary: str = Field(..., min_length=1, max_length=2000)
    top_praises: List[str] = Field(default_factory=list, max_length=10)
    top_concerns: List[str] = Field(default_factory=list, max_length=10)
    audience_fit: str = Field(default="", max_length=1000)


def _read_engagement(
    db_path: Path, seed_post_id: int, audience_size: int
) -> EngagementBreakdown:
    """Compute the engagement sub-score from the SQLite trace.

    Formula (intentionally simple and tunable):

        raw = (likes - dislikes) / N
            + 0.5 * comments / N
            + 0.3 * shares / N

    where N = max(1, audience_size). We map raw -> [0, 100] with a
    clamped piecewise: <= -1 maps to 0, >= 1.5 maps to 100, linear in
    between, with neutral activity (~0) sitting at 40 to reflect the
    fact that "no reaction" is mildly negative for an idea.
    """
    if not db_path.is_file():
        raise FileNotFoundError(f"simulation db not found: {db_path}")

    with sqlite3.connect(str(db_path)) as conn:
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()

        cur.execute(
            "SELECT num_likes, num_dislikes, num_shares "
            "FROM post WHERE post_id = ?",
            (seed_post_id,),
        )
        row = cur.fetchone()
        if row is None:
            num_likes = num_dislikes = num_shares = 0
        else:
            num_likes = int(row["num_likes"] or 0)
            num_dislikes = int(row["num_dislikes"] or 0)
            num_shares = int(row["num_shares"] or 0)

        cur.execute(
            "SELECT COUNT(*) AS n FROM comment WHERE post_id = ?",
            (seed_post_id,),
        )
        num_comments = int(cur.fetchone()["n"] or 0)

    n = max(1, audience_size)
    raw = (
        (num_likes - num_dislikes) / n
        + 0.5 * num_comments / n
        + 0.3 * num_shares / n
    )

    if raw <= -1.0:
        score = 0.0
    elif raw >= 1.5:
        score = 100.0
    elif raw < 0:
        score = 40.0 * (1.0 + raw)
    else:
        score = 40.0 + (raw / 1.5) * 60.0

    score = max(0.0, min(100.0, round(score, 2)))

    return EngagementBreakdown(
        audience_size=audience_size,
        num_likes=num_likes,
        num_dislikes=num_dislikes,
        num_comments=num_comments,
        num_shares=num_shares,
        score=score,
    )


def _read_comments(db_path: Path, seed_post_id: int) -> List[str]:
    with sqlite3.connect(str(db_path)) as conn:
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute(
            "SELECT content FROM comment WHERE post_id = ? ORDER BY comment_id",
            (seed_post_id,),
        )
        rows = cur.fetchall()
    return [(r["content"] or "").strip() for r in rows if (r["content"] or "").strip()]


def _read_interviews(db_path: Path) -> List[Dict[str, str]]:
    """Return a list of ``{user_id, prompt, response}`` dicts."""
    interviews: List[Dict[str, str]] = []
    with sqlite3.connect(str(db_path)) as conn:
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute(
            "SELECT user_id, info FROM trace WHERE action = ? ORDER BY created_at",
            (INTERVIEW_ACTION_VALUE,),
        )
        rows = cur.fetchall()

    for row in rows:
        try:
            info = json.loads(row["info"] or "{}")
        except json.JSONDecodeError:
            continue
        if not isinstance(info, dict):
            continue
        response = str(info.get("response") or "").strip()
        if not response:
            continue
        interviews.append(
            {
                "user_id": str(row["user_id"]),
                "prompt": str(info.get("prompt") or ""),
                "response": response,
            }
        )
    return interviews


def _truncate_for_judge(items: List[str], max_chars: int) -> List[str]:
    """Trim a list of strings so the joined length stays under ``max_chars``."""
    out: List[str] = []
    used = 0
    for item in items:
        item = item.strip()
        if not item:
            continue
        cost = len(item) + 4
        if used + cost > max_chars:
            break
        out.append(item)
        used += cost
    return out


def _build_judge_messages(
    idea: str, comments: List[str], interviews: List[Dict[str, str]]
) -> List[Dict[str, str]]:
    safe_comments = _truncate_for_judge(comments, JUDGE_MAX_FEEDBACK_CHARS // 2)
    safe_interviews = _truncate_for_judge(
        [f"Q: {i['prompt']}\nA: {i['response']}" for i in interviews],
        JUDGE_MAX_FEEDBACK_CHARS // 2,
    )

    system = (
        "You are an impartial product-validation judge. You will be shown "
        "an idea, a set of comments from simulated users, and a set of "
        "interview answers. Score how well the idea resonates with the "
        "audience and summarize qualitative feedback.\n\n"
        "Respond with a single JSON object using exactly these keys:\n"
        '  "score": number 0-100,\n'
        '  "summary": short paragraph (<= 6 sentences),\n'
        f'  "top_praises": list of up to {JUDGE_MAX_PRAISE_OR_CONCERN} short strings,\n'
        f'  "top_concerns": list of up to {JUDGE_MAX_PRAISE_OR_CONCERN} short strings,\n'
        '  "audience_fit": short description of who this resonates with.\n'
        "Be honest, balanced, and concrete. Do not invent feedback that is "
        "not supported by the comments or interviews provided."
    )

    user_payload = {
        "idea": idea,
        "comments": safe_comments,
        "interviews": safe_interviews,
    }
    user = (
        "Score this idea based on the simulated audience reactions below.\n\n"
        f"```json\n{json.dumps(user_payload, ensure_ascii=False)}\n```"
    )
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


def _judge_with_llm(
    idea: str,
    comments: List[str],
    interviews: List[Dict[str, str]],
    model_name: str,
) -> JudgeVerdict:
    """Call the LLM judge and return a validated verdict.

    Falls back to a neutral verdict (with a note) if there is no
    qualitative feedback at all, or if the judge response cannot be
    parsed/validated.
    """
    if not comments and not interviews:
        return JudgeVerdict(
            score=40.0,
            summary=(
                "No comments or interview responses were collected, so the "
                "qualitative signal is essentially neutral by default."
            ),
            top_praises=[],
            top_concerns=["No simulated user wrote anything substantive."],
            audience_fit="Unclear (no qualitative data).",
        )

    base_url = os.environ.get("OPENAI_API_BASE_URL")
    if base_url and not base_url.startswith("https://"):
        raise ValueError("OPENAI_API_BASE_URL must use https://")

    client_kwargs: Dict[str, Any] = {}
    if base_url:
        client_kwargs["base_url"] = base_url
    client = OpenAI(**client_kwargs)

    messages = _build_judge_messages(idea, comments, interviews)

    try:
        completion = client.chat.completions.create(
            model=model_name,
            messages=messages,
            response_format={"type": "json_object"},
            temperature=0.2,
        )
    except Exception as exc:
        logger.warning("LLM judge call failed: %s", exc)
        return JudgeVerdict(
            score=40.0,
            summary=f"LLM judge unavailable ({type(exc).__name__}); used neutral default.",
            top_praises=[],
            top_concerns=[],
            audience_fit="",
        )

    raw = (completion.choices[0].message.content or "").strip()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("LLM judge returned non-JSON: %r", raw[:200])
        return JudgeVerdict(
            score=40.0,
            summary="LLM judge returned malformed JSON; used neutral default.",
            top_praises=[],
            top_concerns=[],
            audience_fit="",
        )

    try:
        validated = _JudgeSchema.model_validate(data)
    except ValidationError as exc:
        logger.warning("LLM judge JSON failed schema validation: %s", exc)
        return JudgeVerdict(
            score=40.0,
            summary="LLM judge JSON failed validation; used neutral default.",
            top_praises=[],
            top_concerns=[],
            audience_fit="",
        )

    return JudgeVerdict(
        score=round(float(validated.score), 2),
        summary=validated.summary.strip(),
        top_praises=[s.strip() for s in validated.top_praises if s.strip()][
            :JUDGE_MAX_PRAISE_OR_CONCERN
        ],
        top_concerns=[s.strip() for s in validated.top_concerns if s.strip()][
            :JUDGE_MAX_PRAISE_OR_CONCERN
        ],
        audience_fit=validated.audience_fit.strip(),
    )


def score_run(
    outcome: SimulationOutcome,
    *,
    idea: str,
    judge_model: str = "gpt-4o-mini",
    sample_limit: int = 6,
) -> ValidationResult:
    """Compute the hybrid validation score from a finished simulation."""
    if not idea or not idea.strip():
        raise ValueError("idea must be a non-empty string")
    if sample_limit < 0:
        raise ValueError("sample_limit must be >= 0")

    engagement = _read_engagement(
        db_path=outcome.db_path,
        seed_post_id=outcome.seed_post_id,
        audience_size=outcome.num_agents,
    )
    comments = _read_comments(outcome.db_path, outcome.seed_post_id)
    interviews = _read_interviews(outcome.db_path)

    sentiment = _judge_with_llm(
        idea=idea,
        comments=comments,
        interviews=interviews,
        model_name=judge_model,
    )

    final_score = round(0.5 * engagement.score + 0.5 * sentiment.score, 2)

    notes: List[str] = []
    if not comments:
        notes.append("No comments were generated by the audience.")
    if not interviews:
        notes.append("No interview responses were collected.")

    return ValidationResult(
        idea=idea,
        final_score=final_score,
        engagement=engagement,
        sentiment=sentiment,
        sample_comments=comments[:sample_limit],
        sample_interviews=interviews[:sample_limit],
        notes=notes,
    )
