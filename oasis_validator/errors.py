"""Backend-facing exception types."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict


@dataclass
class ApiError(Exception):
    """Structured API error with HTTP metadata."""

    status_code: int
    error: str
    message: str
    headers: Dict[str, str] = field(default_factory=dict)
