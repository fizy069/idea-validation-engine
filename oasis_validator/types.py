"""Lightweight types shared between simulator and scorer.

Kept free of OASIS / CAMEL imports so the scoring + reporting layer can
be unit-tested without installing OASIS itself (camel-oasis pins
Python <3.12).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import List

INTERVIEW_ACTION_VALUE = "interview"
"""Value that OASIS writes into ``trace.action`` for interview events.

Mirrors ``oasis.ActionType.INTERVIEW.value``. Hard-coded here so that
the scorer can be imported without the OASIS package being installed.
"""


@dataclass
class SimulationOutcome:
    """Lightweight summary of what the simulation produced."""

    db_path: Path
    seed_post_id: int
    poster_agent_id: int
    interviewed_agent_ids: List[int]
    num_agents: int
    num_reaction_steps: int
