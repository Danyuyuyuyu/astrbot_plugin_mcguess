"""Tests for the MCGuess structured LLM tool argument parsing and JSON results."""

from __future__ import annotations

import json

import pytest

from astrbot_plugin_mcguess.llm_tool import (
    ACTIONS,
    CATEGORIES,
    DESCRIPTION,
    DIFFICULTIES,
    MAX_NAME_LENGTH,
    NAME,
    NEXT,
    SCHEMA,
    InvalidArgs,
    Request,
    error,
    invalid,
    parse_args,
    result,
)


def test_public_constants():
    assert NAME == "mc_guess"
    assert DESCRIPTION and NEXT
    assert "开始" in ACTIONS and ACTIONS["开始"] == "start"
    assert ACTIONS["提示"] == "hint"
    assert CATEGORIES["方块"] == "block" and CATEGORIES["mob"] == "mob"
    assert DIFFICULTIES["简单"] == "easy" and DIFFICULTIES["困难"] == "hard"


def test_schema_is_complete_json_schema():
    assert SCHEMA["type"] == "object"
    assert SCHEMA["required"] == ["action"]
    assert SCHEMA["additionalProperties"] is False
    properties = SCHEMA["properties"]
    assert set(properties) == {"action", "category", "difficulty", "name"}
    for field, spec in properties.items():
        assert spec["type"] == "string", field
        assert spec["description"].strip(), field
    assert properties["action"]["enum"] == ["开始", "猜", "状态", "提示", "放弃", "排行", "帮助"]
    assert properties["category"]["enum"] == ["方块", "物品", "生物"]
    assert properties["difficulty"]["enum"] == ["简单", "普通", "困难"]
    assert str(MAX_NAME_LENGTH) in properties["name"]["description"]
    # The schema must be pretty JSON-serializable for astrbot's registration.
    json.dumps(SCHEMA, ensure_ascii=False)


def test_start_without_optional_fields():
    request = parse_args({"action": "开始"})
    assert request == Request(action="start")
    assert request.category == "" and request.difficulty == "" and request.name == ""


def test_start_with_category_and_difficulty():
    request = parse_args({"action": "开始", "category": "方块", "difficulty": "困难"})
    assert request == Request(action="start", category="block", difficulty="hard")


def test_start_accepts_english_aliases_case_insensitively():
    request = parse_args({"action": "START", "category": "ITEM", "difficulty": "Easy"})
    assert request == Request(action="start", category="item", difficulty="easy")


def test_action_whitespace_is_tolerated():
    assert parse_args({"action": "  开始  "}).action == "start"
    assert parse_args({"action": " 猜 ", "name": "金块"}).action == "guess"
    assert parse_args({"action": " Guess ", "name": "金块"}).action == "guess"


@pytest.mark.parametrize(
    ("word", "expected"),
    [("状态", "status"), ("提示", "hint"), ("放弃", "giveup"), ("排行", "rank"), ("帮助", "help")],
)
def test_simple_actions(word, expected):
    request = parse_args({"action": word})
    assert request.action == expected
    assert request.name == ""
    # Irrelevant fields are ignored for actions that do not use them.
    assert parse_args({"action": word, "name": "金块", "category": "方块"}).name == ""


def test_guess_requires_name():
    request = parse_args({"action": "猜", "name": "金块"})
    assert request == Request(action="guess", name="金块")
    assert parse_args({"action": "猜", "name": "  金块  "}).name == "金块"


def test_guess_accepts_optional_category():
    request = parse_args({"action": "猜", "name": "金块", "category": "方块"})
    assert request == Request(action="guess", category="block", name="金块")


@pytest.mark.parametrize("args", [{"action": "猜"}, {"action": "猜", "name": ""}, {"action": "猜", "name": "   "}])
def test_guess_without_name_is_invalid(args):
    with pytest.raises(InvalidArgs):
        parse_args(args)


def test_guess_name_length_boundary():
    limit = "金" * MAX_NAME_LENGTH
    assert parse_args({"action": "猜", "name": limit}).name == limit
    with pytest.raises(InvalidArgs) as excinfo:
        parse_args({"action": "猜", "name": limit + "金"})
    assert str(MAX_NAME_LENGTH) in str(excinfo.value)


def test_missing_or_unknown_action_is_invalid():
    for args in ({}, {"action": ""}, {"action": "开挂"}, {"action": "start guessing"}):
        with pytest.raises(InvalidArgs):
            parse_args(args)
    with pytest.raises(InvalidArgs) as excinfo:
        parse_args({"action": "开挂"})
    assert "开始" in str(excinfo.value)


def test_invalid_enum_values_are_rejected():
    with pytest.raises(InvalidArgs) as excinfo:
        parse_args({"action": "开始", "category": "矿物"})
    assert "category" in str(excinfo.value)
    with pytest.raises(InvalidArgs) as excinfo:
        parse_args({"action": "开始", "difficulty": "地狱"})
    assert "difficulty" in str(excinfo.value)
    with pytest.raises(InvalidArgs):
        parse_args({"action": "猜", "name": "金块", "category": "矿物"})


@pytest.mark.parametrize("args", [None, [], "开始", 42])
def test_non_dict_arguments_are_invalid(args):
    with pytest.raises(InvalidArgs):
        parse_args(args)


def test_args_values_are_stringified():
    request = parse_args({"action": "开始", "category": "方块", "difficulty": "简单"})
    assert isinstance(request.category, str) and isinstance(request.difficulty, str)


def test_result_json_shape():
    payload = json.loads(result("已开始一局方块猜谜（普通难度）"))
    assert payload["status"] == "ok"
    assert payload["text"] == "已开始一局方块猜谜（普通难度）"
    assert payload["card_sent_to_user"] is False
    assert payload["next"] == NEXT


def test_result_card_sent_flag():
    payload = json.loads(result("猜中了", card_sent=True))
    assert payload["card_sent_to_user"] is True


def test_error_and_invalid_json_shape():
    for dump, status in ((error, "error"), (invalid, "invalid")):
        raw = dump("本局已经结束")
        assert isinstance(raw, str)
        payload = json.loads(raw)
        assert payload["status"] == status
        assert payload["message"] == "本局已经结束"
        assert payload["next"] == NEXT
        # Chinese is not unicode-escaped in the JSON string.
        assert "本局已经结束" in raw and "\\u" not in raw


def test_json_helpers_are_not_html_escaped():
    # LLM tool results are JSON text, not HTML: quotes stay intact inside the string.
    raw = result('他说"开始"')
    assert json.loads(raw)["text"] == '他说"开始"'


def test_module_does_not_import_astrbot():
    import inspect

    import astrbot_plugin_mcguess.llm_tool as llm_tool

    source = inspect.getsource(llm_tool)
    assert "import astrbot" not in source
    assert "from astrbot" not in source
