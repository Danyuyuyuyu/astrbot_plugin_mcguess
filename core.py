"""Pure gameplay primitives for the MC guessing game.

No AstrBot imports here: the comparison rules and the round state machine are
plain functions so they can be unit-tested in isolation.
"""

from __future__ import annotations

import re
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


_VERSION_FAMILY = re.compile(r"^\s*([A-Za-z\-]*\s*)?(\d+)(?:\.(\d+))?")


def version_family(value: Any) -> str:
    """Major version a release belongs to: ``1.16.2`` → ``1.16``, ``Alpha 1.0.14`` → ``Alpha 1.0``.

    Values without a version number (``pre-alpha``) form their own family.
    """
    text = str(value or "").strip()
    match = _VERSION_FAMILY.match(text)
    if not match:
        return text
    prefix = (match.group(1) or "").strip()
    number = f"{match.group(2)}.{match.group(3) or '0'}"
    return f"{prefix} {number}".strip()


def _family_close(field: dict[str, Any]) -> bool:
    return field.get("family_close", True) is not False


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
        same_family = _family_close(field) and version_family(guess_value) == version_family(answer_value)
        return _result(field, guess_value, CLOSE if same_family else MISS, direction)

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


# ---------------------------------------------------------------------------
# clue board: what the whole group has learned so far
# ---------------------------------------------------------------------------
CLUE_KNOWN = "known"
CLUE_PARTIAL = "partial"
CLUE_NONE = "none"

_CANDIDATE_LIST_MAX = 3
_CLUE_ITEMS_MAX = 4


def _similar_values(value: str, pairs: Any) -> set[str]:
    result: set[str] = set()
    if not isinstance(pairs, (list, tuple)):
        return result
    for pair in pairs:
        if isinstance(pair, (list, tuple, set)) and len(pair) == 2:
            left, right = (str(item) for item in pair)
            if left == value:
                result.add(right)
            elif right == value:
                result.add(left)
    return result


def _join_limited(items: list[str], limit: int = _CLUE_ITEMS_MAX) -> str:
    shown = "、".join(items[:limit])
    return shown + (f" 等{len(items)}项" if len(items) > limit else "")


def _tighten_low(current: tuple[float, bool] | None, value: float, inclusive: bool) -> tuple[float, bool]:
    if current is None or value > current[0] or (value == current[0] and not inclusive):
        return (value, inclusive)
    return current


def _tighten_high(current: tuple[float, bool] | None, value: float, inclusive: bool) -> tuple[float, bool]:
    if current is None or value < current[0] or (value == current[0] and not inclusive):
        return (value, inclusive)
    return current


def _close_threshold(guess: float, field: dict[str, Any], direction: str) -> float:
    """Furthest answer value that would still be reported as ``close``."""
    threshold = guess
    tolerance = _number(field.get("tolerance"))
    ratio = _number(field.get("tolerance_ratio"))
    if direction == UP:
        if tolerance:
            threshold = max(threshold, guess + tolerance)
        if ratio and 0 < ratio < 1 and guess > 0:
            threshold = max(threshold, guess / (1 - ratio))
    else:
        if tolerance:
            threshold = min(threshold, guess - tolerance)
        if ratio and 0 < ratio < 1 and guess > 0:
            threshold = min(threshold, guess * (1 - ratio))
    return threshold


def _number_clue(field: dict[str, Any], observations: list[tuple[Any, dict[str, Any]]]) -> dict[str, Any]:
    low: tuple[float, bool] | None = None
    high: tuple[float, bool] | None = None
    for raw, result in observations:
        if result["status"] == EXACT:
            return {"state": CLUE_KNOWN, "text": f"= {format_value(field, raw)}"}
        guess = _number(raw)
        direction = result.get("direction")
        if guess is None or direction not in (UP, DOWN):
            continue
        threshold = _close_threshold(guess, field, direction)
        if direction == UP:
            if result["status"] == CLOSE:
                low = _tighten_low(low, guess, False)
                high = _tighten_high(high, threshold, True)
            else:
                low = _tighten_low(low, threshold, False)
        else:
            if result["status"] == CLOSE:
                high = _tighten_high(high, guess, False)
                low = _tighten_low(low, threshold, True)
            else:
                high = _tighten_high(high, threshold, False)
    if low is None and high is None:
        return {"state": CLUE_NONE, "text": ""}
    if low is not None and high is not None and low[0] == high[0] and low[1] and high[1]:
        return {"state": CLUE_KNOWN, "text": f"= {format_value(field, low[0])}"}
    parts: list[str] = []
    if low is not None:
        parts.append(f"{format_value(field, low[0])} {'≤' if low[1] else '<'}")
    parts.append("?")
    if high is not None:
        parts.append(f"{'≤' if high[1] else '<'} {format_value(field, high[0])}")
    return {"state": CLUE_PARTIAL, "text": " ".join(parts)}


def _version_clue(field: dict[str, Any], observations: list[tuple[Any, dict[str, Any]]]) -> dict[str, Any]:
    order: dict[str, int] = field.get("order") or {}
    names = {index: name for name, index in order.items()}
    candidates = set(order)
    family_rule = _family_close(field)
    low: int | None = None
    high: int | None = None
    family = ""
    for raw, result in observations:
        if result["status"] == EXACT:
            return {"state": CLUE_KNOWN, "text": f"= {raw}"}
        index = order.get(str(raw))
        if index is None:
            continue
        candidates.discard(str(raw))
        if result.get("direction") == UP:
            low = index if low is None else max(low, index)
            candidates = {name for name in candidates if order[name] > index}
        elif result.get("direction") == DOWN:
            high = index if high is None else min(high, index)
            candidates = {name for name in candidates if order[name] < index}
        if family_rule:
            guess_family = version_family(raw)
            if result["status"] == CLOSE:
                family = guess_family
                candidates = {name for name in candidates if version_family(name) == guess_family}
            else:
                candidates = {name for name in candidates if version_family(name) != guess_family}
    ordered = sorted(candidates, key=order.get)
    if low is None and high is None and not family:
        return {"state": CLUE_NONE, "text": ""}
    if len(ordered) == 1:
        return {"state": CLUE_KNOWN, "text": f"= {ordered[0]}（推断）"}
    if 1 < len(ordered) <= _CANDIDATE_LIST_MAX:
        return {"state": CLUE_PARTIAL, "text": "可能 " + " / ".join(ordered)}
    if family:
        return {"state": CLUE_PARTIAL, "text": f"{family}.x 系列"}
    if low is not None and high is not None:
        return {"state": CLUE_PARTIAL, "text": f"{names.get(low, low)} ~ {names.get(high, high)} 之间"}
    if low is not None:
        return {"state": CLUE_PARTIAL, "text": f"晚于 {names.get(low, low)}"}
    return {"state": CLUE_PARTIAL, "text": f"早于 {names.get(high, high)}"}


def _enum_clue(field: dict[str, Any], observations: list[tuple[Any, dict[str, Any]]]) -> dict[str, Any]:
    labels: dict[str, str] = field.get("values") or {}
    pairs = field.get("similar")
    candidates = set(labels) if labels else None
    excluded: list[str] = []
    for raw, result in observations:
        value = str(raw)
        if result["status"] == EXACT:
            return {"state": CLUE_KNOWN, "text": f"= {format_value(field, raw)}"}
        similar = _similar_values(value, pairs)
        if result["status"] == CLOSE:
            if candidates is not None:
                candidates &= similar
        else:
            if value not in excluded:
                excluded.append(value)
            if candidates is not None:
                candidates -= {value} | similar
    if candidates is not None and observations:
        ordered = [key for key in labels if key in candidates]
        if len(ordered) == 1:
            return {"state": CLUE_KNOWN, "text": f"= {format_value(field, ordered[0])}（推断）"}
        if 1 < len(ordered) <= _CANDIDATE_LIST_MAX and len(ordered) < len(labels):
            return {"state": CLUE_PARTIAL, "text": "可能 " + " / ".join(format_value(field, v) for v in ordered)}
    if excluded:
        return {"state": CLUE_PARTIAL, "text": "≠ " + _join_limited([format_value(field, v) for v in excluded])}
    return {"state": CLUE_NONE, "text": ""}


def _set_clue(field: dict[str, Any], observations: list[tuple[Any, dict[str, Any]]]) -> dict[str, Any]:
    included: list[str] = []
    excluded: list[str] = []
    for raw, result in observations:
        if result["status"] == EXACT:
            return {"state": CLUE_KNOWN, "text": f"= {format_values(field, raw)}"}
        hits = set(result.get("hit_keys") or [])
        for key in sorted(hits):
            if key not in included:
                included.append(key)
        for key in sorted(_value_set(raw) - hits):
            if key not in excluded:
                excluded.append(key)
    parts: list[str] = []
    if included:
        parts.append("含 " + _join_limited([format_value(field, v) for v in included], 3))
    if excluded:
        parts.append("不含 " + _join_limited([format_value(field, v) for v in excluded], 3))
    if not parts:
        return {"state": CLUE_NONE, "text": ""}
    return {"state": CLUE_PARTIAL, "text": "；".join(parts)}


def summarize_clues(
    fields: list[dict[str, Any]],
    guess_entries: list[dict[str, Any]],
    answer_entry: dict[str, Any],
    hints: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Aggregate every guess (and revealed hint) into one clue per attribute.

    Only information already shown to players is used: the comparison results
    of past guesses plus attributes revealed by hints. The answer entry is read
    solely through ``compare_field`` and for hinted fields.
    """
    answer_attrs = answer_entry.get("attrs") or {}
    hinted = {str(item.get("field")) for item in (hints or []) if isinstance(item, dict)}
    clues: list[dict[str, Any]] = []
    for field in fields:
        key = str(field.get("key") or "")
        kind = field.get("kind") or "enum"
        observations = []
        for entry in guess_entries:
            raw = (entry.get("attrs") or {}).get(key)
            observations.append((raw, compare_field(field, raw, answer_attrs.get(key))))
        if kind in NUMBER_KINDS:
            clue = _number_clue(field, observations)
        elif kind in VERSION_KINDS:
            clue = _version_clue(field, observations)
        elif kind in SET_KINDS:
            clue = _set_clue(field, observations)
        else:
            clue = _enum_clue(field, observations)
        if clue["state"] != CLUE_KNOWN and key in hinted:
            value = answer_attrs.get(key)
            shown = format_values(field, value) if kind in SET_KINDS else format_value(field, value)
            clue = {"state": CLUE_KNOWN, "text": f"= {shown}（提示）"}
        clues.append({"key": key, "label": str(field.get("label") or key), **clue})
    return clues
