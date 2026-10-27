"""Unit tests for the pure gameplay rules in ``core``."""

from __future__ import annotations

import pytest

from astrbot_plugin_mcguess import core


def make_field(**kwargs):
    field = {"key": "x", "label": "属性", "kind": "enum"}
    field.update(kwargs)
    return field


ENTRY = {
    "id": "diamond_ore",
    "name": "钻石矿石",
    "attrs": {"hardness": 3, "tool": "pickaxe", "tags": ["a", "b"], "release": "1.16"},
}
OTHER = {
    "id": "stone",
    "name": "石头",
    "attrs": {"hardness": 1.5, "tool": "axe", "tags": ["b", "c"], "release": "pre-alpha"},
}
FIELDS = [
    {"key": "hardness", "label": "硬度", "kind": "number", "tolerance": 0.5},
    {
        "key": "tool",
        "label": "工具",
        "kind": "enum",
        "values": {"pickaxe": "镐", "axe": "斧头", "none": "无"},
        "similar": [["pickaxe", "axe"]],
    },
    {"key": "tags", "label": "标签", "kind": "set", "values": {"a": "甲", "b": "乙", "c": "丙"}},
    {"key": "release", "label": "版本", "kind": "version", "order": {"pre-alpha": 0, "1.16": 5}},
]


# ---- formatting ------------------------------------------------------------
def test_format_number_keeps_numbers_readable():
    assert core.format_number(3.0, {}) == "3"
    assert core.format_number(0.5, {}) == "0.5"
    assert core.format_number(3.14159, {"precision": 2}) == "3.14"
    assert core.format_number(3600000, {}) == "3,600,000"
    assert core.format_number("oops", {}) == "oops"


def test_format_value_localizes_enum_and_adds_units():
    field = make_field(kind="enum", values={"pickaxe": "镐"})
    assert core.format_value(field, "pickaxe") == "镐"
    assert core.format_value(field, "unknown") == "unknown"
    assert core.format_value({"kind": "number", "unit": "点"}, 20) == "20点"


def test_format_values_joins_set_labels():
    field = make_field(kind="set", values={"a": "甲", "b": "乙"})
    assert core.format_values(field, ["a", "b"]) == "甲、乙"
    assert core.format_values(field, []) == "无"


# ---- numeric comparison ----------------------------------------------------
def test_number_exact_match_has_no_direction():
    result = core.compare_field(FIELDS[0], 3, 3)
    assert result["status"] == core.EXACT
    assert result["direction"] is None
    assert result["value"] == "3"


def test_number_close_match_points_to_answer():
    close = core.compare_field(FIELDS[0], 2.8, 3)
    assert close["status"] == core.CLOSE
    assert close["direction"] == core.UP
    far = core.compare_field(FIELDS[0], 1, 3)
    assert far["status"] == core.MISS
    assert far["direction"] == core.UP
    down = core.compare_field(FIELDS[0], 10, 3)
    assert down["direction"] == core.DOWN


def test_number_relative_tolerance():
    field = make_field(kind="number", tolerance_ratio=0.25)
    assert core.compare_field(field, 800, 1000)["status"] == core.CLOSE
    assert core.compare_field(field, 500, 1000)["status"] == core.MISS


# ---- enum comparison -------------------------------------------------------
def test_enum_similar_values_count_as_close():
    field = FIELDS[1]
    assert core.compare_field(field, "pickaxe", "pickaxe")["status"] == core.EXACT
    assert core.compare_field(field, "pickaxe", "axe")["status"] == core.CLOSE
    assert core.compare_field(field, "none", "axe")["status"] == core.MISS


# ---- set comparison --------------------------------------------------------
def test_set_equality_and_intersection():
    field = FIELDS[2]
    equal = core.compare_field(field, ["a", "b"], ["b", "a"])
    assert equal["status"] == core.EXACT
    assert equal["value"] == "甲、乙"

    partial = core.compare_field(field, ["a", "b"], ["b", "c"])
    assert partial["status"] == core.CLOSE
    assert partial["hits"] == ["乙"]
    assert partial["hit_keys"] == ["b"]

    miss = core.compare_field(field, ["a"], ["c"])
    assert miss["status"] == core.MISS
    assert miss["hits"] == []


# ---- version comparison ----------------------------------------------------
def test_version_comparison_uses_release_order():
    field = FIELDS[3]
    assert core.compare_field(field, "1.16", "1.16")["status"] == core.EXACT
    newer = core.compare_field(field, "pre-alpha", "1.16")
    assert newer["status"] == core.MISS and newer["direction"] == core.UP
    unknown = core.compare_field(field, "未来版本", "1.16")
    assert unknown["status"] == core.MISS and unknown["direction"] is None


# ---- evaluation / state machine -------------------------------------------
def test_evaluate_guess_marks_correct_answer():
    result = core.evaluate_guess(FIELDS, ENTRY, ENTRY)
    assert result["correct"] is True
    assert [item["status"] for item in result["feedback"]] == [core.EXACT] * 4
    wrong = core.evaluate_guess(FIELDS, OTHER, ENTRY)
    assert wrong["correct"] is False
    assert len(wrong["feedback"]) == len(FIELDS)


def test_apply_guess_win_and_continue():
    state = {"status": "playing", "guesses": [], "category": "block"}
    updated, row = core.apply_guess(state, OTHER, ENTRY, FIELDS, max_attempts=8)
    assert row["attempt"] == 1 and row["correct"] is False
    assert updated["status"] == "playing"
    assert updated["guesses"] == ["stone"]

    won, win_row = core.apply_guess(updated, ENTRY, ENTRY, FIELDS, max_attempts=8)
    assert win_row["correct"] is True
    assert won["status"] == "won"


def test_apply_guess_loses_after_last_attempt():
    state = {"status": "playing", "guesses": []}
    updated, _row = core.apply_guess(state, OTHER, ENTRY, FIELDS, max_attempts=1)
    assert updated["status"] == "lost"


def test_apply_guess_rejects_duplicates_ended_and_exhausted():
    state = {"status": "playing", "guesses": ["stone"]}
    with pytest.raises(ValueError, match="已经猜过"):
        core.apply_guess(state, OTHER, ENTRY, FIELDS, max_attempts=8)

    with pytest.raises(ValueError, match="已经结束"):
        core.apply_guess({"status": "won", "guesses": []}, OTHER, ENTRY, FIELDS)

    with pytest.raises(ValueError, match="次数已经用完"):
        core.apply_guess({"status": "playing", "guesses": ["stone"]}, ENTRY, ENTRY, FIELDS, max_attempts=1)


# ---- hints -----------------------------------------------------------------
def test_build_hint_walks_configured_fields_then_name_hints():
    used: list[str] = []
    first = core.build_hint(ENTRY, FIELDS, ["tool", "hardness"], used, limit=5)
    assert first["field"] == "tool" and "镐" in first["text"]
    used.append(first["field"])

    second = core.build_hint(ENTRY, FIELDS, ["tool", "hardness"], used, limit=5)
    assert second["field"] == "hardness" and "3" in second["text"]
    used.append(second["field"])

    third = core.build_hint(ENTRY, FIELDS, ["tool", "hardness"], used, limit=5)
    assert third["field"] == core.HINT_FIRST_CHAR and "钻" in third["text"]
    used.append(third["field"])

    fourth = core.build_hint(ENTRY, FIELDS, ["tool", "hardness"], used, limit=5)
    assert fourth["field"] == core.HINT_NAME_LENGTH and "4" in fourth["text"]


def test_build_hint_raises_when_limit_reached():
    with pytest.raises(ValueError, match="提示次数已经用完"):
        core.build_hint(ENTRY, FIELDS, [], [], limit=0)


def test_build_hint_reports_plan_exhaustion_separately():
    used = [core.HINT_FIRST_CHAR, core.HINT_NAME_LENGTH]
    with pytest.raises(ValueError, match="没有更多可用的提示"):
        core.build_hint(ENTRY, FIELDS, [], used, limit=5)


def test_build_hint_ignores_unknown_configured_fields():
    hint = core.build_hint(ENTRY, FIELDS, ["no_such_field"], [], limit=5)
    assert hint["field"] == core.HINT_FIRST_CHAR
