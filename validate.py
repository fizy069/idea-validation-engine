"""Command-line entry point for the OASIS idea validator.

Example::

    python validate.py "A SaaS that turns Slack threads into searchable docs"
    python validate.py --agents 25 --steps 4 --json "..."
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import sys
import uuid
from pathlib import Path
from typing import Optional

import click
from dotenv import load_dotenv

from oasis_validator.report import render_console, render_json

ROOT = Path(__file__).resolve().parent
DEFAULT_PERSONAS = ROOT / "data" / "personas.json"
DEFAULT_RUNS_DIR = ROOT / "data" / "runs"


def _configure_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("openai").setLevel(logging.WARNING)


def _ensure_api_key() -> None:
    """Make sure ``OPENAI_API_KEY`` is set before doing any LLM work.

    We deliberately read it from the environment only and never echo
    it. ``.env`` is loaded via ``python-dotenv`` if present.
    """
    if not os.environ.get("OPENAI_API_KEY"):
        click.secho(
            "Error: OPENAI_API_KEY is not set. Add it to your environment "
            "or to a .env file in the project root.",
            fg="red",
            err=True,
        )
        sys.exit(2)


def _resolve_db_path(db_arg: Optional[str]) -> Path:
    """Pick a safe DB path inside the project for this run."""
    if db_arg:
        candidate = Path(db_arg).expanduser().resolve()
        try:
            candidate.relative_to(ROOT)
        except ValueError:
            click.secho(
                "Error: --db must be a path inside the project directory.",
                fg="red",
                err=True,
            )
            sys.exit(2)
        if candidate.suffix != ".db":
            click.secho(
                "Error: --db must end with .db",
                fg="red",
                err=True,
            )
            sys.exit(2)
        return candidate

    DEFAULT_RUNS_DIR.mkdir(parents=True, exist_ok=True)
    run_id = uuid.uuid4().hex[:8]
    return DEFAULT_RUNS_DIR / f"validation_{run_id}.db"


@click.command(context_settings={"help_option_names": ["-h", "--help"]})
@click.argument("idea", type=str)
@click.option(
    "--agents",
    "num_agents",
    type=click.IntRange(2, 1000),
    default=20,
    show_default=True,
    help="Audience size (number of OASIS agents to activate).",
)
@click.option(
    "--steps",
    "num_reaction_steps",
    type=click.IntRange(1, 50),
    default=3,
    show_default=True,
    help="Number of LLM reaction timesteps after the seed post.",
)
@click.option(
    "--interviews",
    "num_interviews",
    type=click.IntRange(0, 1000),
    default=8,
    show_default=True,
    help="How many agents to interview at the end (capped at --agents).",
)
@click.option(
    "--model",
    "model_name",
    type=str,
    default="gpt-4o-mini",
    show_default=True,
    help="OpenAI model used by both agents and the judge.",
)
@click.option(
    "--judge-model",
    "judge_model",
    type=str,
    default=None,
    help="Override the judge model (defaults to --model).",
)
@click.option(
    "--personas",
    "personas",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    default=DEFAULT_PERSONAS,
    show_default=True,
    help="Path to a Reddit-format persona JSON file.",
)
@click.option(
    "--db",
    "db_arg",
    type=str,
    default=None,
    help="Path to write the SQLite simulation DB (must be inside the project).",
)
@click.option(
    "--seed",
    "seed",
    type=int,
    default=None,
    help="Optional random seed for interview-sample selection.",
)
@click.option(
    "--json",
    "as_json",
    is_flag=True,
    default=False,
    help="Print machine-readable JSON instead of a console report.",
)
@click.option(
    "-v",
    "--verbose",
    is_flag=True,
    default=False,
    help="Enable debug logging.",
)
def main(
    idea: str,
    num_agents: int,
    num_reaction_steps: int,
    num_interviews: int,
    model_name: str,
    judge_model: Optional[str],
    personas: Path,
    db_arg: Optional[str],
    seed: Optional[int],
    as_json: bool,
    verbose: bool,
) -> None:
    """Validate IDEA by simulating audience reactions in OASIS."""
    load_dotenv(ROOT / ".env", override=False)
    _configure_logging(verbose)
    _ensure_api_key()

    idea_text = idea.strip()
    if not idea_text:
        click.secho("Error: idea must be a non-empty string.", fg="red", err=True)
        sys.exit(2)
    if len(idea_text) > 4000:
        click.secho(
            "Error: idea must be at most 4000 characters.", fg="red", err=True
        )
        sys.exit(2)

    db_path = _resolve_db_path(db_arg)
    persona_path = personas.expanduser().resolve()

    try:
        from oasis_validator.pipeline import run_validation
    except ImportError as exc:
        click.secho(
            "Error: OASIS is not installed. Install dependencies with "
            "`pip install -r requirements.txt` (requires Python 3.10 or 3.11).\n"
            f"Underlying error: {exc}",
            fg="red",
            err=True,
        )
        sys.exit(1)

    # In --json mode, OASIS / CAMEL emit stray prints to stdout (e.g.
    # "db_path ..." from oasis.social_platform.database). Capturing
    # stdout into stderr during the simulation keeps stdout clean for
    # the JSON payload itself.
    stdout_redirector = (
        contextlib.redirect_stdout(sys.stderr)
        if as_json
        else contextlib.nullcontext()
    )

    try:
        with stdout_redirector:
            result = asyncio.run(
                run_validation(
                    idea=idea_text,
                    persona_path=persona_path,
                    db_path=db_path,
                    model_name=model_name,
                    judge_model=judge_model,
                    num_agents=num_agents,
                    num_reaction_steps=num_reaction_steps,
                    num_interviews=min(num_interviews, num_agents),
                    seed=seed,
                )
            )
    except KeyboardInterrupt:
        click.secho("\nAborted by user.", fg="yellow", err=True)
        sys.exit(130)
    except Exception as exc:
        logging.getLogger(__name__).exception("Validation failed")
        click.secho(f"Error: validation failed: {exc}", fg="red", err=True)
        sys.exit(1)

    if as_json:
        click.echo(render_json(result))
    else:
        render_console(result)
        click.echo()
        click.echo(f"Run database: {db_path}")


if __name__ == "__main__":
    main()
