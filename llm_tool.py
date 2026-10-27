"""Structured LLM tool for MCGuess: validated JSON arguments in, JSON result out.

The model never composes command strings: every argument has its own field (with
Chinese enums where possible) and is validated here. Bad arguments come back as
status "invalid" with what to fix instead of a random guess.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

NAME = "mc_guess"

# Chinese action words -> canonical action ids. English aliases are accepted too
# (case-insensitively) because models occasionally answer with the canonical id.
ACTIONS = {
    "开始": "start",
    "猜": "guess",
    "状态": "status",
    "提示": "hint",
    "放弃": "giveup",
    "帮助": "help",
    "排行": "rank",
    "排行榜": "rank",
    "rank": "rank",
    "start": "start",
    "guess": "guess",
    "status": "status",
    "hint": "hint",
    "giveup": "giveup",
    "help": "help",
}

CATEGORIES = {
    "方块": "block",
    "物品": "item",
    "生物": "mob",
    "block": "block",
    "item": "item",
    "mob": "mob",
}

DIFFICULTIES = {
    "简单": "easy",
    "普通": "normal",
    "困难": "hard",
    "easy": "easy",
    "normal": "normal",
    "hard": "hard",
}

MAX_NAME_LENGTH = 32

DESCRIPTION = (
    "玩《我的世界猜谜》（MCGuess）：开始一局、按名字猜方块/物品/生物、查看进度、要提示、放弃看答案、查看玩法。"
    "用户说“开始猜方块/物品/生物”“我猜金块”“还剩几次”“给个提示”“放弃”“排行榜”“怎么玩”时调用。"
    "返回 JSON：status、text（可直接转述给用户的要点）、next（下一步建议）；"
    "不要编造猜测结果或属性反馈，结果以 text 为准。"
)

# Chinese enum lists shown to the model; parse_args also accepts English aliases.
SCHEMA = {
    "type": "object",
    "properties": {
        "action": {
            "type": "string",
            "enum": ["开始", "猜", "状态", "提示", "放弃", "排行", "帮助"],
            "description": "开始=新开一局；猜=提交一次名字；状态=查看本局进度；提示=要一条线索；放弃=结束并公布答案；排行=本群排行榜；帮助=玩法与指令说明",
        },
        "category": {
            "type": "string",
            "enum": ["方块", "物品", "生物"],
            "description": "action=开始 时的题目类型：方块/物品/生物；不填则由用户或默认值决定",
        },
        "difficulty": {
            "type": "string",
            "enum": ["简单", "普通", "困难"],
            "description": "action=开始 时的难度，默认普通",
        },
        "name": {
            "type": "string",
            "description": "action=猜 时必须填：用户要猜的方块/物品/生物名字，只填一个名字（≤32 个字符），不要带整句话",
        },
    },
    "required": ["action"],
    "additionalProperties": False,
}

NEXT = "继续猜就直接说下一个名字；看进度说“状态”；卡住了说“提示”；结束说“放弃”；新开一局说“开始”。"

_ACTION_HINT = "开始 / 猜 / 状态 / 提示 / 放弃 / 排行 / 帮助"


class InvalidArgs(ValueError):
    """The tool arguments do not match the expected shape."""


@dataclass
class Request:
    """One validated tool call mapped to a game action.

    Fields irrelevant to ``action`` are left empty by :func:`parse_args`.
    """

    action: str
    category: str = ""
    difficulty: str = ""
    name: str = ""


def _text(args: dict[str, Any], key: str) -> str:
    value = args.get(key)
    return "" if value is None else str(value).strip()


def _lookup(table: dict[str, str], raw: str) -> str:
    """Map a Chinese or English (case-insensitive) word onto its canonical id."""
    if not raw:
        return ""
    return table.get(raw) or table.get(raw.lower()) or ""


def parse_args(args: dict[str, Any]) -> Request:
    """Validate model-provided arguments and map them onto a game action."""
    if not isinstance(args, dict):
        raise InvalidArgs("参数必须是一个 JSON 对象，例如 {\"action\": \"开始\"}")

    action = _lookup(ACTIONS, _text(args, "action"))
    if not action:
        raise InvalidArgs("action 只能是：" + _ACTION_HINT + "，或英文 start/guess/status/hint/giveup/rank/help")

    if action == "start":
        category = _validated(args, "category", CATEGORIES, "方块 / 物品 / 生物", "block/item/mob")
        difficulty = _validated(
            args, "difficulty", DIFFICULTIES, "简单 / 普通 / 困难", "easy/normal/hard"
        )
        return Request(action, category, difficulty)

    if action == "guess":
        category = _validated(args, "category", CATEGORIES, "方块 / 物品 / 生物", "block/item/mob")
        name = _text(args, "name")
        if not name:
            raise InvalidArgs("action=猜 时必须填 name（用户要猜的名字）")
        if len(name) > MAX_NAME_LENGTH:
            raise InvalidArgs(
                f"name 只填一个名字，最长 {MAX_NAME_LENGTH} 个字符，不要把整句话塞进来"
            )
        return Request(action, category, name=name)

    return Request(action)


def _validated(
    args: dict[str, Any],
    field: str,
    table: dict[str, str],
    chinese: str,
    english: str,
) -> str:
    """Validate an optional enum field; empty means "not provided"."""
    raw = _text(args, field)
    if not raw:
        return ""
    value = _lookup(table, raw)
    if not value:
        raise InvalidArgs(f"{field} 只能是：{chinese}（或 {english}）")
    return value


def _dump(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False)


def result(text: str, *, card_sent: bool = False) -> str:
    """Successful tool result; ``text`` can be relayed to the user."""
    return _dump({"status": "ok", "text": text, "card_sent_to_user": card_sent, "next": NEXT})


def error(message: str) -> str:
    """A game-level failure the model should explain to the user."""
    return _dump({"status": "error", "message": message, "next": NEXT})


def invalid(message: str) -> str:
    """Bad tool arguments; the model should fix them or ask the user."""
    return _dump({"status": "invalid", "message": message, "next": NEXT})
