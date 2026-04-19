"""Transform OASIS SQLite output into backend API payloads."""

from __future__ import annotations

import json
import logging
import os
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from openai import OpenAI
from pydantic import BaseModel, Field, ValidationError

from oasis_validator.types import INTERVIEW_ACTION_VALUE, SimulationOutcome

logger = logging.getLogger(__name__)

JUDGE_MAX_FEEDBACK_CHARS = 24_000


@dataclass
class CommentRecord:
    comment_id: int
    user_id: int
    content: str
    likes: int
    dislikes: int
    created_at: str
    parent_comment_id: Optional[int]


@dataclass
class MarketArtifacts:
    post: Dict[str, Any]
    thread: List[Dict[str, Any]]
    interviews: List[Dict[str, Any]]
    traction_score: float
    summary: str


@dataclass
class JudgeVerdict:
    score: float
    summary: str


class _JudgeSchema(BaseModel):
    score: float = Field(..., ge=0, le=100)
    summary: str = Field(..., min_length=1, max_length=2000)


def _to_iso_utc(value: Optional[str]) -> str:
    if not value:
        return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

    try:
        cleaned = value.replace("Z", "+00:00")
        dt = datetime.fromisoformat(cleaned)
    except ValueError:
        try:
            dt = datetime.strptime(value, "%Y-%m-%d %H:%M:%S")
        except ValueError:
            return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        dt = dt.astimezone(timezone.utc)
    return dt.isoformat().replace("+00:00", "Z")


def _load_persona_lookup(persona_path: Path) -> Dict[int, Dict[str, str]]:
    with persona_path.open("r", encoding="utf-8") as handle:
        personas = json.load(handle)
    if not isinstance(personas, list):
        return {}

    lookup: Dict[int, Dict[str, str]] = {}
    for idx, persona in enumerate(personas):
        if not isinstance(persona, dict):
            continue
        agent_name = (
            str(persona.get("username") or persona.get("realname") or f"agent_{idx}")
            .strip()
            .replace(" ", "_")
        )
        description = str(persona.get("bio") or persona.get("persona") or "").strip()
        if len(description) > 220:
            description = f"{description[:217]}..."
        lookup[idx] = {
            "name": agent_name or f"agent_{idx}",
            "description": description,
        }
    return lookup


def _table_columns(conn: sqlite3.Connection, table_name: str) -> set[str]:
    rows = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
    return {str(row["name"]) for row in rows}


def _read_post_stats(
    conn: sqlite3.Connection, seed_post_id: int
) -> Dict[str, Any]:
    columns = _table_columns(conn, "post")
    select_parts = ["post_id"]
    if "num_likes" in columns:
        select_parts.append("num_likes")
    else:
        select_parts.append("0 AS num_likes")
    if "num_dislikes" in columns:
        select_parts.append("num_dislikes")
    else:
        select_parts.append("0 AS num_dislikes")
    if "num_shares" in columns:
        select_parts.append("num_shares")
    else:
        select_parts.append("0 AS num_shares")
    if "created_at" in columns:
        select_parts.append("created_at")
    else:
        select_parts.append("NULL AS created_at")

    row = conn.execute(
        f"SELECT {', '.join(select_parts)} FROM post WHERE post_id = ? LIMIT 1",
        (seed_post_id,),
    ).fetchone()
    if row is None:
        return {
            "likes": 0,
            "dislikes": 0,
            "shares": 0,
            "created_at": _to_iso_utc(None),
        }
    return {
        "likes": int(row["num_likes"] or 0),
        "dislikes": int(row["num_dislikes"] or 0),
        "shares": int(row["num_shares"] or 0),
        "created_at": _to_iso_utc(row["created_at"]),
    }


def _read_comment_records(
    conn: sqlite3.Connection, seed_post_id: int
) -> List[CommentRecord]:
    columns = _table_columns(conn, "comment")
    parent_col = next(
        (
            name
            for name in (
                "parent_comment_id",
                "parent_id",
                "reply_to_comment_id",
                "original_comment_id",
            )
            if name in columns
        ),
        None,
    )

    select_parts = ["comment_id", "user_id", "content"]
    if "num_likes" in columns:
        select_parts.append("num_likes")
    else:
        select_parts.append("0 AS num_likes")
    if "num_dislikes" in columns:
        select_parts.append("num_dislikes")
    else:
        select_parts.append("0 AS num_dislikes")
    if "created_at" in columns:
        select_parts.append("created_at")
    else:
        select_parts.append("NULL AS created_at")
    if parent_col is not None:
        select_parts.append(f"{parent_col} AS parent_comment_id")
    else:
        select_parts.append("NULL AS parent_comment_id")

    order_parts = ["comment_id"]
    if "created_at" in columns:
        order_parts = ["created_at", "comment_id"]

    rows = conn.execute(
        f"""
        SELECT {', '.join(select_parts)}
        FROM comment
        WHERE post_id = ?
        ORDER BY {', '.join(order_parts)}
        """,
        (seed_post_id,),
    ).fetchall()

    records: List[CommentRecord] = []
    for row in rows:
        content = str(row["content"] or "").strip()
        if not content:
            continue
        parent_raw = row["parent_comment_id"]
        parent_comment_id = int(parent_raw) if parent_raw is not None else None
        records.append(
            CommentRecord(
                comment_id=int(row["comment_id"]),
                user_id=int(row["user_id"]),
                content=content,
                likes=int(row["num_likes"] or 0),
                dislikes=int(row["num_dislikes"] or 0),
                created_at=_to_iso_utc(row["created_at"]),
                parent_comment_id=parent_comment_id,
            )
        )
    return records


def _read_interviews(
    conn: sqlite3.Connection,
    persona_lookup: Dict[int, Dict[str, str]],
) -> List[Dict[str, Any]]:
    try:
        rows = conn.execute(
            """
            SELECT user_id, created_at, info
            FROM trace
            WHERE action = ?
            ORDER BY created_at
            """,
            (INTERVIEW_ACTION_VALUE,),
        ).fetchall()
    except sqlite3.OperationalError:
        return []

    interviews: List[Dict[str, Any]] = []
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

        agent_id = int(row["user_id"])
        persona = persona_lookup.get(agent_id, {})
        interviews.append(
            {
                "agentId": agent_id,
                "agent": persona.get("name", f"agent_{agent_id}"),
                "personaDescription": persona.get("description", ""),
                "prompt": str(info.get("prompt") or "").strip(),
                "response": response,
                "createdAt": _to_iso_utc(row["created_at"]),
            }
        )
    return interviews


def _truncate_for_judge(items: List[str], max_chars: int) -> List[str]:
    output: List[str] = []
    used = 0
    for item in items:
        normalized = item.strip()
        if not normalized:
            continue
        cost = len(normalized) + 2
        if used + cost > max_chars:
            break
        output.append(normalized)
        used += cost
    return output


def _build_judge_messages(
    *,
    idea: str,
    target_user: str,
    comments: List[CommentRecord],
    interviews: List[Dict[str, Any]],
) -> List[Dict[str, str]]:
    safe_comments = _truncate_for_judge(
        [item.content for item in comments],
        JUDGE_MAX_FEEDBACK_CHARS // 2,
    )
    safe_interviews = _truncate_for_judge(
        [item["response"] for item in interviews],
        JUDGE_MAX_FEEDBACK_CHARS // 2,
    )
    payload = {
        "idea": idea,
        "targetUser": target_user,
        "comments": safe_comments,
        "interviews": safe_interviews,
    }

    return [
        {
            "role": "system",
            "content": (
                "You are an impartial product-validation judge. "
                "Return a JSON object with exactly these keys: "
                '"score" (0-100 number), "summary" (max 6 sentences).'
            ),
        },
        {
            "role": "user",
            "content": (
                "Evaluate audience traction based only on this data.\n"
                f"```json\n{json.dumps(payload, ensure_ascii=False)}\n```"
            ),
        },
    ]


def _judge_with_llm(
    *,
    idea: str,
    target_user: str,
    comments: List[CommentRecord],
    interviews: List[Dict[str, Any]],
    model_name: str,
) -> JudgeVerdict:
    if not comments and not interviews:
        return JudgeVerdict(
            score=40.0,
            summary=(
                "No qualitative feedback was collected, so the traction summary "
                "is neutral by default."
            ),
        )

    base_url = os.environ.get("OPENAI_API_BASE_URL")
    if base_url and not base_url.startswith("https://"):
        raise ValueError("OPENAI_API_BASE_URL must use https://")

    client_kwargs: Dict[str, Any] = {}
    if base_url:
        client_kwargs["base_url"] = base_url

    try:
        client = OpenAI(**client_kwargs)
        completion = client.chat.completions.create(
            model=model_name,
            messages=_build_judge_messages(
                idea=idea,
                target_user=target_user,
                comments=comments,
                interviews=interviews,
            ),
            response_format={"type": "json_object"},
            temperature=0.2,
        )
        raw = str(completion.choices[0].message.content or "").strip()
        parsed = json.loads(raw)
        validated = _JudgeSchema.model_validate(parsed)
        return JudgeVerdict(
            score=round(float(validated.score), 2),
            summary=validated.summary.strip(),
        )
    except (ValidationError, json.JSONDecodeError) as exc:
        logger.warning("Judge output validation failed: %s", exc)
    except Exception as exc:
        logger.warning("Judge call failed: %s", exc)

    return JudgeVerdict(
        score=40.0,
        summary="The simulation completed, but summary generation failed.",
    )


def _compute_engagement_score(comments: List[CommentRecord], post_stats: Dict[str, Any]) -> float:
    audience_norm = max(1, len({record.user_id for record in comments}) + 1)
    likes = int(post_stats["likes"])
    dislikes = int(post_stats["dislikes"])
    shares = int(post_stats["shares"])
    comment_count = len(comments)
    raw = (
        (likes - dislikes) / audience_norm
        + 0.5 * comment_count / audience_norm
        + 0.3 * shares / audience_norm
    )

    if raw <= -1.0:
        score = 0.0
    elif raw >= 1.5:
        score = 100.0
    elif raw < 0:
        score = 40.0 * (1.0 + raw)
    else:
        score = 40.0 + (raw / 1.5) * 60.0

    return max(0.0, min(100.0, round(score, 2)))


def _resolve_top_level_parent(
    record: CommentRecord,
    by_id: Dict[int, CommentRecord],
) -> Optional[int]:
    parent_id = record.parent_comment_id
    if parent_id is None:
        return None

    seen: set[int] = set()
    while parent_id is not None and parent_id in by_id and parent_id not in seen:
        seen.add(parent_id)
        parent = by_id[parent_id]
        if parent.parent_comment_id is None:
            return parent_id
        parent_id = parent.parent_comment_id
    return parent_id if parent_id in by_id else None


def _comment_to_api(
    *,
    record: CommentRecord,
    persona_lookup: Dict[int, Dict[str, str]],
) -> Dict[str, Any]:
    persona = persona_lookup.get(record.user_id, {})
    return {
        "id": f"c{record.comment_id}",
        "agentId": record.user_id,
        "agent": persona.get("name", f"agent_{record.user_id}"),
        "personaDescription": persona.get("description", ""),
        "type": "vocal",
        "comment": record.content,
        "likes": record.likes,
        "dislikes": record.dislikes,
        "turn": 1,
        "createdAt": record.created_at,
        "replies": [],
    }


def _reply_to_api(
    *,
    record: CommentRecord,
    parent_comment_id: int,
    persona_lookup: Dict[int, Dict[str, str]],
) -> Dict[str, Any]:
    persona = persona_lookup.get(record.user_id, {})
    return {
        "id": f"c{parent_comment_id}r{record.comment_id}",
        "agentId": record.user_id,
        "agent": persona.get("name", f"agent_{record.user_id}"),
        "personaDescription": persona.get("description", ""),
        "comment": record.content,
        "likes": record.likes,
        "dislikes": record.dislikes,
        "turn": 2,
        "createdAt": record.created_at,
    }


def _build_thread(
    comments: List[CommentRecord],
    persona_lookup: Dict[int, Dict[str, str]],
) -> List[Dict[str, Any]]:
    by_id = {record.comment_id: record for record in comments}
    top_level: List[CommentRecord] = []
    reply_groups: Dict[int, List[CommentRecord]] = {}

    for record in comments:
        top_parent_id = _resolve_top_level_parent(record, by_id)
        if top_parent_id is None or top_parent_id == record.comment_id:
            top_level.append(record)
            continue
        reply_groups.setdefault(top_parent_id, []).append(record)

    thread: List[Dict[str, Any]] = []
    for record in top_level:
        item = _comment_to_api(record=record, persona_lookup=persona_lookup)
        replies = reply_groups.get(record.comment_id, [])
        item["replies"] = [
            _reply_to_api(
                record=reply,
                parent_comment_id=record.comment_id,
                persona_lookup=persona_lookup,
            )
            for reply in replies
        ]
        thread.append(item)
    return thread


def build_market_artifacts(
    *,
    outcome: SimulationOutcome,
    idea: str,
    target_user: str,
    judge_model: str = "gpt-4o-mini",
) -> MarketArtifacts:
    if not outcome.db_path.is_file():
        raise FileNotFoundError(f"simulation db not found: {outcome.db_path}")

    persona_lookup = _load_persona_lookup(outcome.persona_path)
    with sqlite3.connect(str(outcome.db_path)) as conn:
        conn.row_factory = sqlite3.Row
        post_stats = _read_post_stats(conn, outcome.seed_post_id)
        comments = _read_comment_records(conn, outcome.seed_post_id)
        interviews = _read_interviews(conn, persona_lookup)

    engagement_score = _compute_engagement_score(comments, post_stats)
    judge = _judge_with_llm(
        idea=idea,
        target_user=target_user,
        comments=comments,
        interviews=interviews,
        model_name=judge_model,
    )
    combined = 0.5 * engagement_score + 0.5 * judge.score
    traction_score = round(max(1.0, min(10.0, combined / 10.0)), 1)

    post_payload = {
        "title": idea,
        "body": target_user,
        "likes": int(post_stats["likes"]),
        "dislikes": int(post_stats["dislikes"]),
        "shares": int(post_stats["shares"]),
        "commentCount": len(comments),
        "createdAt": post_stats["created_at"],
    }
    thread_payload = _build_thread(comments, persona_lookup)

    return MarketArtifacts(
        post=post_payload,
        thread=thread_payload,
        interviews=interviews,
        traction_score=traction_score,
        summary=judge.summary,
    )
