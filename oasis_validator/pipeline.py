"""End-to-end market simulation orchestration for the HTTP backend."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from oasis_validator.scorer import MarketArtifacts, build_market_artifacts
from oasis_validator.simulator import (
    DEFAULT_INTERVIEW_PROMPT,
    SimulationConfig,
    run_simulation,
)

async def run_market_validation(
    *,
    idea: str,
    target_user: str,
    persona_path: Path,
    db_path: Path,
    model_name: str = "gpt-4o-mini",
    judge_model: Optional[str] = None,
    num_vocal: int = 5,
    turns: int = 2,
    num_interviews: Optional[int] = None,
    interview_prompt: str = DEFAULT_INTERVIEW_PROMPT,
    seed: Optional[int] = None,
) -> MarketArtifacts:
    """Run OASIS and format a contract-compliant market payload."""
    interviews_to_collect = (
        min(num_vocal, max(0, num_interviews))
        if num_interviews is not None
        else min(num_vocal, 8)
    )

    config = SimulationConfig(
        idea=idea,
        target_user=target_user,
        persona_path=persona_path,
        db_path=db_path,
        model_name=model_name,
        num_agents=num_vocal,
        num_reaction_steps=turns,
        num_interviews=interviews_to_collect,
        interview_prompt=interview_prompt,
        seed=seed,
    )

    outcome = await run_simulation(config)

    return build_market_artifacts(
        outcome=outcome,
        idea=idea,
        target_user=target_user,
        judge_model=judge_model or model_name,
    )
