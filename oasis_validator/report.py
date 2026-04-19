"""Console rendering for ``ValidationResult``.

Two output modes:

- ``render_console``: rich, human-friendly output using ``rich`` if
  available, falling back to plain text if not.
- ``render_json``: deterministic, machine-readable JSON suitable for
  piping into other tools.
"""

from __future__ import annotations

import json
import sys
from typing import Optional

from oasis_validator.scorer import ValidationResult

try:
    from rich.console import Console
    from rich.panel import Panel
    from rich.table import Table
    from rich.text import Text

    _HAS_RICH = True
except ImportError:
    _HAS_RICH = False


def _verdict_label(score: float) -> str:
    if score >= 80:
        return "Strong validation"
    if score >= 65:
        return "Promising"
    if score >= 50:
        return "Mixed"
    if score >= 35:
        return "Weak"
    return "Poor"


def render_json(result: ValidationResult) -> str:
    """Return a deterministic JSON string for the result."""
    return json.dumps(result.to_dict(), indent=2, ensure_ascii=False)


def render_console(
    result: ValidationResult,
    *,
    console: Optional["Console"] = None,
) -> None:
    """Pretty-print the validation result to stdout."""
    if not _HAS_RICH:
        _render_plain(result)
        return

    cons = console or Console()

    header = Text()
    header.append("Idea Validation Report\n", style="bold")
    header.append(f'"{result.idea}"', style="italic")
    cons.print(Panel(header, border_style="cyan"))

    score_table = Table(show_header=True, header_style="bold", expand=False)
    score_table.add_column("Metric")
    score_table.add_column("Score", justify="right")
    score_table.add_row("Final (hybrid)", f"{result.final_score:.1f} / 100")
    score_table.add_row("Engagement", f"{result.engagement.score:.1f} / 100")
    score_table.add_row("Sentiment", f"{result.sentiment.score:.1f} / 100")
    score_table.add_row("Verdict", _verdict_label(result.final_score))
    cons.print(score_table)

    eng = result.engagement
    eng_table = Table(
        title="Engagement breakdown",
        show_header=True,
        header_style="bold",
        expand=False,
    )
    eng_table.add_column("Signal")
    eng_table.add_column("Count", justify="right")
    eng_table.add_row("Audience size", str(eng.audience_size))
    eng_table.add_row("Likes", str(eng.num_likes))
    eng_table.add_row("Dislikes", str(eng.num_dislikes))
    eng_table.add_row("Comments", str(eng.num_comments))
    eng_table.add_row("Shares", str(eng.num_shares))
    cons.print(eng_table)

    cons.print(
        Panel(
            result.sentiment.summary or "(no summary)",
            title="Judge summary",
            border_style="green",
        )
    )

    if result.sentiment.audience_fit:
        cons.print(
            Panel(
                result.sentiment.audience_fit,
                title="Audience fit",
                border_style="magenta",
            )
        )

    if result.sentiment.top_praises:
        praise_text = "\n".join(f"- {p}" for p in result.sentiment.top_praises)
        cons.print(
            Panel(praise_text, title="Top praises", border_style="green")
        )

    if result.sentiment.top_concerns:
        concern_text = "\n".join(f"- {c}" for c in result.sentiment.top_concerns)
        cons.print(
            Panel(concern_text, title="Top concerns", border_style="red")
        )

    if result.sample_comments:
        sample_text = "\n\n".join(
            f"- {c}" for c in result.sample_comments[:5]
        )
        cons.print(
            Panel(
                sample_text,
                title="Sample comments",
                border_style="blue",
            )
        )

    if result.sample_interviews:
        interview_text = "\n\n".join(
            f"[user {i['user_id']}]\nQ: {i['prompt']}\nA: {i['response']}"
            for i in result.sample_interviews[:3]
        )
        cons.print(
            Panel(
                interview_text,
                title="Sample interviews",
                border_style="yellow",
            )
        )

    if result.notes:
        notes_text = "\n".join(f"- {n}" for n in result.notes)
        cons.print(Panel(notes_text, title="Notes", border_style="dim"))


def _render_plain(result: ValidationResult) -> None:
    """Fallback plain-text rendering when rich is not installed."""
    out = sys.stdout

    out.write("Idea Validation Report\n")
    out.write("======================\n\n")
    out.write(f'Idea: "{result.idea}"\n\n')

    out.write(f"Final score:      {result.final_score:.1f} / 100\n")
    out.write(f"Engagement score: {result.engagement.score:.1f} / 100\n")
    out.write(f"Sentiment score:  {result.sentiment.score:.1f} / 100\n")
    out.write(f"Verdict:          {_verdict_label(result.final_score)}\n\n")

    eng = result.engagement
    out.write("Engagement breakdown\n")
    out.write("--------------------\n")
    out.write(f"Audience size: {eng.audience_size}\n")
    out.write(f"Likes:         {eng.num_likes}\n")
    out.write(f"Dislikes:      {eng.num_dislikes}\n")
    out.write(f"Comments:      {eng.num_comments}\n")
    out.write(f"Shares:        {eng.num_shares}\n\n")

    out.write("Judge summary\n")
    out.write("-------------\n")
    out.write(f"{result.sentiment.summary or '(no summary)'}\n\n")

    if result.sentiment.audience_fit:
        out.write("Audience fit\n")
        out.write("------------\n")
        out.write(f"{result.sentiment.audience_fit}\n\n")

    if result.sentiment.top_praises:
        out.write("Top praises\n")
        out.write("-----------\n")
        for p in result.sentiment.top_praises:
            out.write(f"- {p}\n")
        out.write("\n")

    if result.sentiment.top_concerns:
        out.write("Top concerns\n")
        out.write("------------\n")
        for c in result.sentiment.top_concerns:
            out.write(f"- {c}\n")
        out.write("\n")

    if result.sample_comments:
        out.write("Sample comments\n")
        out.write("---------------\n")
        for c in result.sample_comments[:5]:
            out.write(f"- {c}\n")
        out.write("\n")

    if result.notes:
        out.write("Notes\n")
        out.write("-----\n")
        for n in result.notes:
            out.write(f"- {n}\n")
        out.write("\n")

    out.flush()
