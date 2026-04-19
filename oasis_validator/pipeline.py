"""End-to-end orchestration: simulate then score.

Keeps the public API of the package small: callers (CLI, tests, future
notebook usage) only need to know about ``run_validation``.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from oasis_validator.scorer import ValidationResult, score_run
from oasis_validator.simulator import (
    DEFAULT_INTERVIEW_PROMPT,
    SimulationConfig,
    run_simulation,
)

logger = logging.getLogger(__name__)


async def run_validation(
    *,
    idea: str,
    persona_path: Path,
    db_path: Path,
    model_name: str = "gpt-4o-mini",
    judge_model: Optional[str] = None,
    num_agents: int = 20,
    num_reaction_steps: int = 3,
    num_interviews: int = 8,
    interview_prompt: str = DEFAULT_INTERVIEW_PROMPT,
    seed: Optional[int] = None,
) -> ValidationResult:
    """Run an OASIS simulation for ``idea`` and return the validation result.

    All keyword arguments are forwarded to :class:`SimulationConfig` and
    :func:`score_run`. ``judge_model`` defaults to ``model_name`` if not
    set explicitly.
    """
    config = SimulationConfig(
        idea=idea,
        persona_path=persona_path,
        db_path=db_path,
        model_name=model_name,
        num_agents=num_agents,
        num_reaction_steps=num_reaction_steps,
        num_interviews=num_interviews,
        interview_prompt=interview_prompt,
        seed=seed,
    )

    outcome = await run_simulation(config)

    return score_run(
        outcome,
        idea=idea,
        judge_model=judge_model or model_name,
    )
