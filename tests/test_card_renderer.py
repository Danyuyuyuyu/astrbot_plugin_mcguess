"""Tests for the MCGuess HTML card renderer (pure, stdlib only)."""

from __future__ import annotations

import json
import re

from astrbot_plugin_mcguess.card_renderer import (
    auto_columns,
    build_guess_card_html,
    build_help_card_html,
    build_status_card_html,
    make_guess_card_view,
    make_status_card_view,
)


def _feedback() -> list[dict]:
    return [
        {"key": "hardness", "label": "硬度", "status": "exact", "direction": None, "value": "3", "hits": []},
        {"key": "tool", "label": "工具", "status": "close", "direction": "up", "value": "镐", "hits": []},
        {
            "key": "obtaining",
            "label": "获得方式",
            "status": "close",
            "direction": None,
            "value": "合成, 采矿",
            "hits": ["合成"],
            "hit_keys": ["Crafting"],
        },
        {"key": "initial_release", "label": "初始版本", "status": "miss", "direction": "down", "value": "Alpha 1.0.1"},
    ]


def _history() -> list[dict]:
    return [
        {"n": 3, "name": "金块", "player": "Steve", "states": ["exact", "close", "close", "miss"]},
        {"n": 2, "name": "玻璃", "player": "Alex", "states": ["miss", "miss", "exact", "miss"]},
    ]


def _view(**overrides) -> dict:
    kwargs = {
        "category_key": "block",
        "category_label": "方块",
        "difficulty_label": "普通",
        "attempt": 3,
        "max_attempts": 25,
        "remaining": 22,
        "guess_name": "金块",
        "guess_name_en": "Gold Block",
        "feedback": _feedback(),
        "player": "Steve",
        "clues": [{"key": "initial_release", "state": "partial", "text": "早于 Alpha 1.0.1"}],
        "history": _history(),
        "hints_used": 1,
        "hint_limit": 3,
    }
    kwargs.update(overrides)
    return make_guess_card_view(**kwargs)


def _html(**overrides) -> str:
    return build_guess_card_html(_view(**overrides))


# ---- view ------------------------------------------------------------------
def test_view_maps_feedback_in_order():
    tiles = _view()["tiles"]
    assert [tile["key"] for tile in tiles] == ["hardness", "tool", "obtaining", "initial_release"]
    assert [tile["status"] for tile in tiles] == ["exact", "close", "close", "miss"]
    assert tiles[1]["direction"] == "up" and tiles[3]["direction"] == "down"
    assert tiles[2]["hits"] == ["合成"]
    assert tiles[3]["clue"] == "早于 Alpha 1.0.1"


def test_view_counts_hits_and_history():
    view = _view()
    assert view["hit"] == 1
    assert [row["hit"] for row in view["history"]] == [1, 1]
    assert view["history_more"] == 0
    assert _view(history_total=10)["history_more"] == 8


def test_exact_tiles_never_show_redundant_clue_or_hits():
    view = _view(clues=[{"key": "hardness", "state": "known", "text": "= 3"}])
    assert view["tiles"][0]["clue"] == "" and view["tiles"][0]["hits"] == []


def test_view_normalizes_junk_input():
    view = _view(feedback=[{"status": "weird", "direction": "sideways"}, "junk"], remaining=-2, outcome="??")
    assert len(view["tiles"]) == 1
    assert view["tiles"][0]["status"] == "miss" and view["tiles"][0]["direction"] == ""
    assert view["remaining"] == 0 and view["outcome"] == "playing"


def test_columns_auto_and_clamped():
    assert auto_columns(11) == 4 and auto_columns(9) == 3 and auto_columns(6) == 3 and auto_columns(2) == 2
    assert _view(columns=99)["columns"] == 6
    assert _view(columns="3; }body{display:none}")["columns"] == 4


# ---- guess card ------------------------------------------------------------
def test_guess_card_header_and_current_guess():
    html = _html()
    assert "<!doctype html>" in html and "<title>MCGuess · 方块</title>" in html
    assert "猜方块" in html and "普通" in html and "提示 1/3" in html
    assert "22<small>/ 25</small>" in html and "剩余机会" in html
    assert "<b>Steve</b> 第 3 次猜" in html and "金块<span>Gold Block</span>" in html
    assert "1<small>/4</small>" in html  # attribute hits


def test_segment_bar_matches_remaining_and_is_clamped():
    assert _html().count('<i class="on">') == 22
    assert _html(remaining=999).count('<i class="on">') == 25
    assert _html(max_attempts=0, remaining=0).count('<i class="on">') == 0
    assert 'class="num crit"' in _html(remaining=3)


def test_mode_accent_classes():
    assert 'class="card block"' in _html()
    assert 'class="card item"' in _html(category_key="item", category_label="物品")
    assert 'class="card mob"' in _html(category_key="", category_label="生物")
    assert 'class="card block"' in _html(category_key="", category_label="未知类别")


def test_states_use_shape_not_just_colour():
    html = _html()
    assert '<div class="t exact">' in html and '<div class="t close">' in html and '<div class="t miss">' in html
    assert html.count('class="st" title=') == 4
    assert "更大</span>" in html and "更小</span>" in html
    assert '<span class="hit">含 合成</span>' in html
    assert "Crafting" not in html  # raw keys never leak


def test_clue_line_and_note_tile_fill_the_grid():
    html = _html()
    assert "<b>线索</b>早于 Alpha 1.0.1" in html
    # 4 tiles in 4 columns: no hole, no hint note
    assert "grid-column: span" not in html
    html = _html(columns=3)
    assert "grid-column: span 2" in html and "/mc猜 提示" in html and "还剩 2 次" in html


def test_history_rows_newest_first_with_players():
    html = _html()
    assert html.index(">#3<") < html.index(">#2<")
    assert '<span class="pl">Alex</span>' in html
    assert 'class="row cur"' in html


def test_won_card_hides_tiles_and_reveals_answer():
    html = _html(outcome="won", answer_name="金块", participants=3)
    assert 'class="guess won"' in html and "<b>Steve</b> 猜中了！" in html and "3 人参与" in html
    assert '<div class="grid"' not in html
    assert "/mc猜 开始" in html


def test_lost_card_reveals_answer_and_keeps_last_feedback():
    html = _html(outcome="lost", answer_name="铁块", answer_name_en="Block of Iron", remaining=0)
    assert 'class="guess lost"' in html and "机会用完" in html and "铁块<span>Block of Iron</span>" in html
    assert '<div class="grid"' in html


def test_html_escapes_user_controlled_text():
    evil = '<script>alert("x")</script>'
    html = _html(
        guess_name=evil,
        guess_name_en=evil,
        player=evil,
        category_label=evil,
        difficulty_label=evil,
        feedback=[{"label": evil, "value": evil, "status": "close", "hits": [evil, 42]}],
        clues=[{"key": "", "text": evil}],
        history=[{"n": 1, "name": evil, "player": evil, "states": ["exact"]}],
        hint_notes=[evil],
    )
    assert "<script>" not in html
    assert "&lt;script&gt;" in html


def test_empty_feedback_renders_empty_state():
    assert "还没有可展示的属性" in _html(feedback=None)


def test_card_is_self_contained():
    html = _html() + build_status_card_html(make_status_card_view(**_status_kwargs())) + build_help_card_html({})
    assert not re.search(r"(src|href)=|url\(|@import|https?://", html)


def test_build_functions_tolerate_bad_views():
    assert "<!doctype html>" in build_guess_card_html(None)  # type: ignore[arg-type]
    assert "<!doctype html>" in build_status_card_html("junk")  # type: ignore[arg-type]
    assert 'style="--cols: 6"' in build_guess_card_html({**_view(), "columns": "99"})


def test_view_is_json_serializable():
    json.dumps(_view())
    json.dumps(make_status_card_view(**_status_kwargs()))


# ---- status card -----------------------------------------------------------
def _status_kwargs(**overrides) -> dict:
    kwargs = {
        "category_key": "item",
        "category_label": "物品",
        "difficulty_label": "困难",
        "attempt": 2,
        "max_attempts": 25,
        "remaining": 23,
        "clues": [
            {"key": "rarity", "label": "稀有度", "state": "known", "text": "= 普通"},
            {"key": "usage", "label": "用途", "state": "partial", "text": "含 合成材料；不含 燃料"},
            {"key": "loot", "label": "掉落方式", "state": "none", "text": ""},
        ],
        "history": _history(),
    }
    kwargs.update(overrides)
    return kwargs


def test_status_card_shows_clue_board():
    html = build_status_card_html(make_status_card_view(**_status_kwargs()))
    assert "已知线索" in html and 'class="pill hard"' in html
    assert '<div class="t status-tile known">' in html and "= 普通" in html
    assert '含 合成材料<span class="seg2">不含 燃料</span>' in html
    assert "暂无线索" in html
    assert 'class="row cur"' not in html  # no "current guess" on the board


def test_give_up_status_card_reveals_answer():
    view = make_status_card_view(**_status_kwargs(outcome="gaveup", answer_name="钻石"))
    html = build_status_card_html(view)
    assert 'class="guess gaveup"' in html and "本局已放弃" in html and "钻石" in html


# ---- help card -------------------------------------------------------------
def test_help_card_renders_commands_and_escapes():
    html = build_help_card_html(
        {
            "title": "MC 猜谜",
            "subtitle": "<b>x</b>",
            "commands": [{"command": "/mc猜 提示", "description": "免费提示"}, "junk", {"command": "<i>"}],
        }
    )
    assert html.count('<div class="cmd">') == 2
    assert "/mc猜 提示" in html and "&lt;b&gt;x&lt;/b&gt;" in html and "&lt;i&gt;" in html


def test_help_card_accepts_bare_list_and_empty():
    assert html_has(build_help_card_html([{"command": "/a"}]), "/a")
    assert "暂无可用指令" in build_help_card_html({})


def html_has(html: str, needle: str) -> bool:
    return needle in html


def test_renderer_does_not_import_astrbot():
    import astrbot_plugin_mcguess.card_renderer as module

    source = open(module.__file__, encoding="utf-8").read()
    assert "astrbot" not in source.split('"""', 2)[2].lower().replace("astrbot_plugin_mcguess", "")
