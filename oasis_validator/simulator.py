"""OASIS simulation runner for idea validation.

This module wires up an OASIS Reddit-like environment, seeds it with the
user's idea as a single post, lets a fixed audience of LLM-driven personas
react across several timesteps, and finally interviews a sample of agents
for qualitative validation feedback.

Design notes:

- All inputs (idea text, persona file path, model name, etc.) are validated
  before being passed to OASIS or sqlite. This satisfies the workspace's
  input-validation rules.
- The OpenAI API key is never read or referenced here; the OpenAI SDK
  (used internally by CAMEL/OASIS) reads ``OPENAI_API_KEY`` directly from
  the process environment. Callers are responsible for loading ``.env``.
- The OASIS ``INTERVIEW`` action is intentionally absent from
  ``available_actions`` so that LLM agents do not interview each other
  spontaneously; we only ever issue interviews as ``ManualAction`` instances.
"""

from __future__ import annotations

import json
import logging
import os
import random
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

import oasis
from camel.models import ModelFactory
from camel.types import ModelPlatformType, ModelType
from oasis import (
    ActionType,
    LLMAction,
    ManualAction,
    generate_reddit_agent_graph,
)

from oasis_validator.types import SimulationOutcome

logger = logging.getLogger(__name__)


DEFAULT_INTERVIEW_PROMPT = (
    "You just saw the post above describing an idea. Speaking honestly as "
    "yourself, please answer in 4-6 sentences:\n"
    "1) Would you personally use, support, or pay for this idea? Why or why not?\n"
    "2) What is the single biggest concern you have about it?\n"
    "3) What is one concrete change that would make you more likely to support it?"
)


@dataclass
class SimulationConfig:
    """Configuration for a single idea-validation simulation run."""

    idea: str
    persona_path: Path
    db_path: Path
    model_name: str = "gpt-4o-mini"
    num_agents: int = 20
    num_reaction_steps: int = 3
    num_interviews: int = 8
    interview_prompt: str = DEFAULT_INTERVIEW_PROMPT
    seed: Optional[int] = None
    available_actions: List[ActionType] = field(
        default_factory=lambda: [
            ActionType.LIKE_POST,
            ActionType.DISLIKE_POST,
            ActionType.CREATE_COMMENT,
            ActionType.LIKE_COMMENT,
            ActionType.DISLIKE_COMMENT,
            ActionType.SEARCH_POSTS,
            ActionType.REFRESH,
            ActionType.DO_NOTHING,
        ]
    )

    def validate(self) -> None:
        """Validate config values before any external call.

        Raises ``ValueError`` for any invalid input. We do this up-front so
        that the OASIS environment (and the SQLite file it creates) are
        only built when the inputs are sane.
        """
        idea = (self.idea or "").strip()
        if not idea:
            raise ValueError("idea must be a non-empty string")
        if len(idea) > 4000:
            raise ValueError("idea must be at most 4000 characters")

        if not isinstance(self.persona_path, Path):
            raise ValueError("persona_path must be a pathlib.Path")
        if not self.persona_path.is_file():
            raise ValueError(f"persona file not found: {self.persona_path}")

        if not isinstance(self.db_path, Path):
            raise ValueError("db_path must be a pathlib.Path")
        if self.db_path.suffix != ".db":
            raise ValueError("db_path must end with .db")

        if not (1 <= self.num_agents <= 1000):
            raise ValueError("num_agents must be between 1 and 1000")
        if not (1 <= self.num_reaction_steps <= 50):
            raise ValueError("num_reaction_steps must be between 1 and 50")
        if not (0 <= self.num_interviews <= self.num_agents):
            raise ValueError(
                "num_interviews must be between 0 and num_agents"
            )

        if not self.model_name or not isinstance(self.model_name, str):
            raise ValueError("model_name must be a non-empty string")

        base_url = os.environ.get("OPENAI_API_BASE_URL")
        if base_url and not base_url.startswith("https://"):
            raise ValueError(
                "OPENAI_API_BASE_URL must use https:// (got non-HTTPS URL)"
            )


def _build_model(model_name: str):
    """Construct a CAMEL OpenAI model.

    We map a small set of friendly names to ``ModelType`` enum values when
    possible (so type-checked clients keep working) and otherwise fall
    back to passing the raw string, which CAMEL accepts for custom
    deployments.
    """
    name_map = {
        "gpt-4o-mini": ModelType.GPT_4O_MINI,
        "gpt-4o": ModelType.GPT_4O,
        "gpt-4.1-mini": getattr(ModelType, "GPT_4_1_MINI", model_name),
        "gpt-4.1": getattr(ModelType, "GPT_4_1", model_name),
    }
    model_type = name_map.get(model_name, model_name)

    return ModelFactory.create(
        model_platform=ModelPlatformType.OPENAI,
        model_type=model_type,
    )


def _select_audience_ids(total: int, requested: int) -> List[int]:
    """Pick which agent ids participate in the simulation.

    We always include agent 0 (the poster) and then a deterministic-or-
    random subset of the remaining agents up to ``requested``.
    """
    if requested >= total:
        return list(range(total))
    return list(range(requested))


async def run_simulation(config: SimulationConfig) -> SimulationOutcome:
    """Run a full OASIS validation simulation.

    Steps:
      1. Validate config + load personas.
      2. Build agent graph from personas.
      3. Make a Reddit-like environment backed by SQLite.
      4. Have agent 0 post the idea (timestep 1).
      5. Run ``num_reaction_steps`` rounds of LLM-driven reactions.
      6. Manually interview a sample of agents (final step).
      7. Close the env and return a summary.
    """
    config.validate()

    if config.seed is not None:
        random.seed(config.seed)

    with config.persona_path.open("r", encoding="utf-8") as fh:
        personas = json.load(fh)
    if not isinstance(personas, list) or not personas:
        raise ValueError(
            f"persona file must contain a non-empty JSON list: {config.persona_path}"
        )
    available_persona_count = len(personas)
    if config.num_agents > available_persona_count:
        logger.warning(
            "num_agents=%d exceeds available personas=%d; clamping",
            config.num_agents,
            available_persona_count,
        )
    effective_agents = min(config.num_agents, available_persona_count)

    config.db_path.parent.mkdir(parents=True, exist_ok=True)
    if config.db_path.exists():
        config.db_path.unlink()

    model = _build_model(config.model_name)

    agent_graph = await generate_reddit_agent_graph(
        profile_path=str(config.persona_path),
        model=model,
        available_actions=config.available_actions,
    )

    total_in_graph = agent_graph.get_num_nodes()
    if total_in_graph == 0:
        raise RuntimeError("OASIS agent graph is empty after generation")

    audience_ids = _select_audience_ids(total_in_graph, effective_agents)
    poster_id = audience_ids[0]
    reactor_ids = audience_ids[1:] if len(audience_ids) > 1 else audience_ids

    env = oasis.make(
        agent_graph=agent_graph,
        platform=oasis.DefaultPlatformType.REDDIT,
        database_path=str(config.db_path),
    )

    await env.reset()

    seed_action = {
        env.agent_graph.get_agent(poster_id): ManualAction(
            action_type=ActionType.CREATE_POST,
            action_args={"content": config.idea},
        )
    }
    await env.step(seed_action)
    seed_post_id = 1

    for step_idx in range(config.num_reaction_steps):
        logger.info(
            "Reaction step %d/%d (agents=%d)",
            step_idx + 1,
            config.num_reaction_steps,
            len(reactor_ids),
        )
        actions = {
            agent: LLMAction()
            for _, agent in env.agent_graph.get_agents(reactor_ids)
        }
        if actions:
            await env.step(actions)

    interview_pool = list(reactor_ids) if reactor_ids else list(audience_ids)
    if config.num_interviews > 0 and interview_pool:
        sample_size = min(config.num_interviews, len(interview_pool))
        interview_ids = random.sample(interview_pool, sample_size)
        logger.info("Interviewing %d agents", len(interview_ids))
        interview_actions = {
            env.agent_graph.get_agent(agent_id): ManualAction(
                action_type=ActionType.INTERVIEW,
                action_args={"prompt": config.interview_prompt},
            )
            for agent_id in interview_ids
        }
        await env.step(interview_actions)
    else:
        interview_ids = []

    await env.close()

    return SimulationOutcome(
        db_path=config.db_path,
        seed_post_id=seed_post_id,
        poster_agent_id=poster_id,
        interviewed_agent_ids=interview_ids,
        num_agents=effective_agents,
        num_reaction_steps=config.num_reaction_steps,
    )
