"""Pure gameplay primitives for the MC guessing game.

No AstrBot imports here: the comparison rules and the round state machine are
plain functions so they can be unit-tested in isolation.
"""

from __future__ import annotations

from typing import Any

EXACT = "exact"
CLOSE = "close"
MISS = "miss"

UP = "up"
DOWN = "down"

FLOAT_EPSILON = 1e-9
RELATIVE_EPSILON = 1e-12

STATUS_EMOJI = {EXACT: "🟩", CLOSE: "🟨", MISS: "⬜"}
DIRECTION_ARROW = {UP: "⬆", DOWN: "⬇", None: ""}

NUMBER_KINDS = {"number"}
SET_KINDS = {"set"}
VERSION_KINDS = {"version"}


# ---------------------------------------------------------------------------
# formatting
# ---------------------------------------------------------------------------
def format_number(value: Any, field: dict[str, Any]) -> str:
    """Format a numeric attribute for display (no unit)."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    precision = field.get("precision")
    if isinstance(precision, int) and not isinstance(precision, bool):
        return f"{number:.{precision}f}"
    if number == int(number) and abs(number) >= 10000:
        return f"{int(number):,}"
    return f"{number:g}"


def format_value(field: dict[str, Any], value: Any) -> str:
    """Format one raw attribute value into its localized display text."""
    kind = field.get("kind")
    if kind in NUMBER_KINDS:
        text = format_number(value, field)
        unit = str(field.get("unit") or "")
        return f"{text}{unit}" if unit else text
    if kind in VERSION_KINDS:
        return str(value)
    labels: dict[str, str] = field.get("values") or {}
    return str(labels.get(str(value), value))


def format_values(field: dict[str, Any], values: Any) -> str:
    """Format a multi-valued attribute (``set``) joined with ``、``."""
    items = values if isinstance(values, (list, tuple, set)) else [values]
    rendered = [format_value(field, item) for item in items if item is not None and str(item) != ""]
    return "、".join(rendered) if rendered else "无"


# ---------------------------------------------------------------------------
# comparison
# ---------------------------------------------------------------------------
def _number(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value))
    except (TypeError, ValueError):
        return None


def _value_set(value: Any) -> set[str]:
    if value is None:
        return set()
    if isinstance(value, (list, tuple, set, frozenset)):
        return {str(item) for item in value}
    return {str(value)}


def _numbers_close(guess: float, answer: float, field: dict[str, Any]) -> bool:
    tolerance = field.get("tolerance")
    if tolerance is not None:
        try:
            if abs(guess - answer) <= float(tolerance) + FLOAT_EPSILON:
                return True
        except (TypeError, ValueError):
            pass
    ratio = field.get("tolerance_ratio")
    if ratio is not None:
        base = max(abs(guess), abs(answer))
        try:
            if base > 0 and abs(guess - answer) / base <= float(ratio) + RELATIVE_EPSILON:
                return True
        except (TypeError, ValueError):
            pass
    return False


def _similar_pair(guess: str, answer: str, pairs: Any) -> bool:
    if not isinstance(pairs, (list, tuple)):
        return False
    needle = {guess, answer}
    for pair in pairs:
        if isinstance(pair, (list, tuple, set)) and len(pair) == 2 and {str(v) for v in pair} == needle:
            return True
    return False


def _result(field: dict[str, Any], guess_value: Any, status: str, direction: str | None = None) -> dict[str, Any]:
    key = str(field.get("key") or "")
    if (field.get("kind") or "enum") in SET_KINDS:
        value_text = format_values(field, guess_value)
    else:
        value_text = format_value(field, guess_value)
    return {
        "key": key,
        "label": str(field.get("label") or key),
        "status": status,
        "direction": direction,
        "value": value_text,
        "hits": [],
        "hit_keys": [],
    }


def compare_field(field: dict[str, Any], guess_value: Any, answer_value: Any) -> dict[str, Any]:
    """Compare one attribute and return a display-ready feedback entry."""
    kind = field.get("kind") or "enum"

    if kind in NUMBER_KINDS:
        guess_number = _number(guess_value)
        answer_number = _number(answer_value)
        if guess_number is None or answer_number is None:
            status = EXACT if str(guess_value) == str(answer_value) else MISS
            return _result(field, guess_value, status)
        if abs(guess_number - answer_number) <= FLOAT_EPSILON:
            return _result(field, guess_value, EXACT)
        status = CLOSE if _numbers_close(guess_number, answer_number, field) else MISS
        direction = UP if answer_number > guess_number else DOWN
        return _result(field, guess_value, status, direction)

    if kind in VERSION_KINDS:
        if str(guess_value) == str(answer_value):
            return _result(field, guess_value, EXACT)
        order: dict[str, int] = field.get("order") or {}
        guess_index = order.get(str(guess_value))
        answer_index = order.get(str(answer_value))
        if guess_index is None or answer_index is None:
            return _result(field, guess_value, MISS)
        if guess_index == answer_index:
            return _result(field, guess_value, EXACT)
        direction = UP if answer_index > guess_index else DOWN
        return _result(field, guess_value, MISS, direction)

    if kind in SET_KINDS:
        guess_set = _value_set(guess_value)
        answer_set = _value_set(answer_value)
        hits = sorted(guess_set & answer_set)
        result = _result(field, guess_value, MISS)
        result["hit_keys"] = hits
        result["hits"] = [format_value(field, hit) for hit in hits]
        if guess_set == answer_set:
            result["status"] = EXACT
        elif hits:
            result["status"] = CLOSE
        return result

    if str(guess_value) == str(answer_value):
        return _result(field, guess_value, EXACT)
    if _similar_pair(str(guess_value), str(answer_value), field.get("similar")):
        return _result(field, guess_value, CLOSE)
    return _result(field, guess_value, MISS)


def evaluate_guess(
    fields: list[dict[str, Any]],
    guess_entry: dict[str, Any],
    answer_entry: dict[str, Any],
) -> dict[str, Any]:
    """Compare every configured attribute of two catalogue entries."""
    guess_attrs = guess_entry.get("attrs") or {}
    answer_attrs = answer_entry.get("attrs") or {}
    feedback = [
        compare_field(field, guess_attrs.get(field.get("key")), answer_attrs.get(field.get("key")))
        for field in fields
    ]
    return {
        "correct": str(guess_entry.get("id")) == str(answer_entry.get("id")),
        "feedback": feedback,
    }


# ---------------------------------------------------------------------------
# round state machine
# ---------------------------------------------------------------------------
def apply_guess(
    state: dict[str, Any],
    guess_entry: dict[str, Any],
    answer_entry: dict[str, Any],
    fields: list[dict[str, Any]],
    *,
    max_attempts: int = 25,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Apply one guess and return the persisted state plus the result row."""
    status = str(state.get("status") or "playing")
    if status != "playing":
        raise ValueError("本局已经结束，发送 `/mc猜 开始` 新开一局。")

    guessed = [str(item) for item in (state.get("guesses") or [])]
    guess_id = str(guess_entry.get("id"))
    if guess_id in guessed:
        raise ValueError("这个已经猜过了，换一个试试。")
    if len(guessed) >= max_attempts:
        raise ValueError("本局次数已经用完，发送 `/mc猜 开始` 新开一局。")

    evaluation = evaluate_guess(fields, guess_entry, answer_entry)
    guessed.append(guess_id)
    if evaluation["correct"]:
        next_status = "won"
    elif len(guessed) >= max_attempts:
        next_status = "lost"
    else:
        next_status = "playing"
    row = {
        "attempt": len(guessed),
        "guessId": guess_id,
        "guessName": str(guess_entry.get("name") or ""),
        "correct": evaluation["correct"],
        "feedback": evaluation["feedback"],
    }
    return {**state, "guesses": guessed, "status": next_status}, row


# ---------------------------------------------------------------------------
# hints
# ---------------------------------------------------------------------------
HINT_FIRST_CHAR = "first_char"
HINT_NAME_LENGTH = "name_length"


def _hint_text(field: dict[str, Any], answer_value: Any) -> str:
    label = str(field.get("label") or field.get("key") or "")
    kind = field.get("kind")
    if kind in SET_KINDS:
        return f"答案的{label}包含「{format_values(field, answer_value)}」"
    if kind in NUMBER_KINDS:
        return f"答案的{label}是 {format_value(field, answer_value)}"
    if kind in VERSION_KINDS:
        return f"答案首次加入于 {answer_value}"
    return f"答案的{label}是「{format_value(field, answer_value)}」"


def hint_plan(fields: list[dict[str, Any]], hint_fields: list[str] | None) -> list[str]:
    """Ordered hint keys: configured attributes first, then name hints."""
    available = {str(field.get("key")) for field in fields}
    plan = [key for key in (hint_fields or []) if key in available]
    plan.extend([HINT_FIRST_CHAR, HINT_NAME_LENGTH])
    return plan


def build_hint(
    answer_entry: dict[str, Any],
    fields: list[dict[str, Any]],
    hint_fields: list[str] | None,
    used_fields: list[str],
    *,
    limit: int = 3,
) -> dict[str, Any]:
    """Return the next hint for the answer, or raise ``ValueError`` when exhausted."""
    used = [str(item) for item in used_fields]
    if len(used) >= limit:
        raise ValueError("本局提示次数已经用完了。")
    plan = hint_plan(fields, hint_fields)
    field_map = {str(field.get("key")): field for field in fields}
    name = str(answer_entry.get("name") or "")
    answer_attrs = answer_entry.get("attrs") or {}

    for key in plan:
        if key in used:
            continue
        if key == HINT_FIRST_CHAR:
            return {
                "field": key,
                "label": "名称首字",
                "text": f"答案的第一个字是「{name[:1]}」" if name else "答案名称未知",
            }
        if key == HINT_NAME_LENGTH:
            return {
                "field": key,
                "label": "名称长度",
                "text": f"答案共 {len(name)} 个字",
            }
        field = field_map.get(key)
        if field is None:
            continue
        return {
            "field": key,
            "label": str(field.get("label") or key),
            "text": _hint_text(field, answer_attrs.get(key)),
        }
    raise ValueError("本局已经没有更多可用的提示了，直接猜吧。")
