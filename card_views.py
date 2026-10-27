"""Glue between a saved round and the card view builders (no AstrBot imports)."""

from __future__ import annotations

from typing import Any

from . import core
from .card_renderer import HISTORY_ROWS, make_guess_card_view, make_status_card_view
from .catalog import Category

DIFFICULTY_LABELS = {"easy": "简单", "normal": "普通", "hard": "困难"}
OUTCOME_BY_STATUS = {"playing": "playing", "won": "won", "lost": "lost", "gaveup": "gaveup", "expired": "expired"}


def _round_context(state: dict[str, Any], category: Category, answer: dict[str, Any]) -> dict[str, Any]:
    guess_ids = [str(item) for item in (state.get("guesses") or [])]
    players = [str(item) for item in (state.get("players") or [])]
    entries = []
    history = []
    for index, entry_id in enumerate(guess_ids):
        entry = category.entry(entry_id)
        if entry is None:
            continue
        entries.append(entry)
        evaluation = core.evaluate_guess(category.fields, entry, answer)
        history.append(
            {
                "n": index + 1,
                "name": entry.get("name") or entry_id,
                "player": players[index] if index < len(players) else "",
                "states": [item["status"] for item in evaluation["feedback"]],
                "correct": evaluation["correct"],
            }
        )
    hints = [item for item in (state.get("hints") or []) if isinstance(item, dict)]
    clues = core.summarize_clues(category.fields, entries, answer, hints)
    hint_notes = [
        str(item.get("text") or "") for item in hints if item.get("field") in (core.HINT_FIRST_CHAR, core.HINT_NAME_LENGTH)
    ]
    return {
        "history": list(reversed(history))[:HISTORY_ROWS],
        "history_total": len(history),
        "clues": clues,
        "hint_notes": hint_notes,
        "hints_used": len(hints),
        "participants": len({p for p in players if p}),
        "attempt": len(guess_ids),
    }


def guess_view(
    state: dict[str, Any],
    category: Category,
    row: dict[str, Any],
    guess: dict[str, Any],
    answer: dict[str, Any],
    *,
    max_attempts: int,
    hint_limit: int,
    status_text: str = "",
) -> dict[str, Any]:
    context = _round_context(state, category, answer)
    status = str(state.get("status") or "playing")
    ended = status != "playing"
    return make_guess_card_view(
        category_key=category.key,
        category_label=category.label,
        difficulty_label=DIFFICULTY_LABELS.get(str(state.get("difficulty")), str(state.get("difficulty") or "")),
        attempt=int(row.get("attempt") or context["attempt"]),
        max_attempts=max_attempts,
        remaining=max(0, max_attempts - context["attempt"]),
        guess_name=str(guess.get("name") or ""),
        guess_name_en=str(guess.get("name_en") or ""),
        player=str(row.get("player") or ""),
        feedback=row.get("feedback"),
        status_text=status_text,
        clues=context["clues"],
        history=context["history"],
        history_total=context["history_total"],
        outcome=OUTCOME_BY_STATUS.get(status, "playing"),
        answer_name=str(answer.get("name") or "") if ended else "",
        answer_name_en=str(answer.get("name_en") or "") if ended else "",
        hints_used=context["hints_used"],
        hint_limit=hint_limit,
        hint_notes=context["hint_notes"],
        participants=context["participants"],
    )


def status_view(
    state: dict[str, Any],
    category: Category,
    answer: dict[str, Any],
    *,
    max_attempts: int,
    hint_limit: int,
) -> dict[str, Any]:
    context = _round_context(state, category, answer)
    status = str(state.get("status") or "playing")
    ended = status != "playing"
    return make_status_card_view(
        category_key=category.key,
        category_label=category.label,
        difficulty_label=DIFFICULTY_LABELS.get(str(state.get("difficulty")), str(state.get("difficulty") or "")),
        attempt=context["attempt"],
        max_attempts=max_attempts,
        remaining=max(0, max_attempts - context["attempt"]),
        clues=context["clues"],
        history=context["history"],
        history_total=context["history_total"],
        outcome=OUTCOME_BY_STATUS.get(status, "playing"),
        answer_name=str(answer.get("name") or "") if ended else "",
        answer_name_en=str(answer.get("name_en") or "") if ended else "",
        hints_used=context["hints_used"],
        hint_limit=hint_limit,
        hint_notes=context["hint_notes"],
        participants=context["participants"],
    )
