"""API request/response models for the backend contract."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

SUBREDDIT_PATTERN = r"^r/[A-Za-z0-9_]{1,32}$"
SLUG_PATTERN = r"^[a-f0-9]{8,64}$"


class StrictModel(BaseModel):
    """Base model that rejects unknown fields."""

    model_config = ConfigDict(extra="forbid")


class ErrorResponse(StrictModel):
    error: str
    message: str


class HealthResponse(StrictModel):
    status: Literal["ok"] = "ok"


class SimulateMarketRequest(StrictModel):
    idea: str = Field(..., min_length=1, max_length=4000)
    targetUser: str = Field(..., min_length=1, max_length=500)
    subreddit: str = Field(..., pattern=SUBREDDIT_PATTERN)
    numVocal: int = Field(default=5, ge=1, le=20)
    turns: int = Field(default=2, ge=1, le=5)

    @field_validator("idea", "targetUser", "subreddit", mode="before")
    @classmethod
    def _strip_strings(cls, value: str) -> str:
        if not isinstance(value, str):
            return value
        return value.strip()

    @field_validator("idea", "targetUser")
    @classmethod
    def _non_empty_after_strip(cls, value: str) -> str:
        if not value:
            raise ValueError("must be non-empty after trimming")
        return value


class MarketReply(StrictModel):
    id: str
    agentId: int
    agent: str
    personaDescription: str = ""
    comment: str
    likes: int
    dislikes: int
    turn: int
    createdAt: str


class MarketComment(StrictModel):
    id: str
    agentId: int
    agent: str
    personaDescription: str = ""
    type: Literal["vocal"] = "vocal"
    comment: str
    likes: int
    dislikes: int
    turn: int
    createdAt: str
    replies: list[MarketReply] = Field(default_factory=list)


class MarketPost(StrictModel):
    title: str
    body: str
    likes: int
    dislikes: int
    shares: int
    commentCount: int
    createdAt: str


class SimulateMarketResponse(StrictModel):
    slug: str = Field(..., pattern=SLUG_PATTERN)
    subreddit: str
    post: MarketPost
    thread: list[MarketComment] = Field(default_factory=list)
    tractionScore: float = Field(..., ge=1.0, le=10.0)
    summary: str


class ResultConfig(StrictModel):
    subreddit: str
    numVocal: int
    turns: int


class GetResultResponse(StrictModel):
    slug: str = Field(..., pattern=SLUG_PATTERN)
    createdAt: str
    idea: str
    targetUser: str
    config: ResultConfig
    result: SimulateMarketResponse


class MarketInterview(StrictModel):
    agentId: int
    agent: str
    personaDescription: str = ""
    prompt: str
    response: str
    createdAt: str


class GetInterviewsResponse(StrictModel):
    slug: str = Field(..., pattern=SLUG_PATTERN)
    interviews: list[MarketInterview] = Field(default_factory=list)
