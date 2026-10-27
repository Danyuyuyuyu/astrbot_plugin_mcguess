"""Tests for the MCGuess HTML card renderer (pure, stdlib only)."""

from __future__ import annotations

import json

from astrbot_plugin_mcguess.card_renderer import (
    build_guess_card_html,
    build_help_card_html,
    make_guess_card_view,
)


def _feedback() -> list[dict]:
    return [
        {
            "key": "hardness",
            "label": "硬度",
            "status": "exact",
            "direction": None,
            "value": "3",
            "hits": [],
            "hit_keys": [],
        },
        {
            "key": "tool",
            "label": "工具",
            "status": "close",
            "direction": "up",
            "value": "镐",
            "hits": [],
            "hit_keys": [],
        },
        {
            "key": "obtaining",
            "label": "获得方式",
            "status": "close",
            "direction": None,
            "value": "合成, 采矿",
            "hits": ["合成"],
            "hit_keys": ["Crafting"],
        },
        {  # miss tiles may omit optional keys entirely
            "key": "initial_release",
            "label": "初始版本",
            "status": "miss",
            "direction": "down",
            "value": "Alpha 1.0.1",
        },
    ]


def _view(**overrides) -> dict:
    kwargs = {
        "category_label": "方块",
        "category_icon": "🧱",
        "difficulty_label": "普通",
        "attempt": 3,
        "max_attempts": 8,
        "remaining": 5,
        "guess_name": "金块",
        "guess_name_en": "Gold Block",
        "feedback": _feedback(),
        "status_text": "还剩 5 次",
        "columns": 4,
    }
    kwargs.update(overrides)
    return make_guess_card_view(**kwargs)


def _html(**overrides) -> str:
    return build_guess_card_html(_view(**overrides))


def test_view_maps_feedback_in_order():
    view = _view()
    tiles = view["tiles"]
    assert [tile["key"] for tile in tiles] == ["hardness", "tool", "obtaining", "initial_release"]
    assert [tile["status"] for tile in tiles] == ["exact", "close", "close", "miss"]
    assert tiles[0]["label"] == "硬度" and tiles[0]["value"] == "3"
    assert tiles[0]["status_label"] == "完全相同" and tiles[0]["status_symbol"] == "🟩"
    assert tiles[1]["arrow"] == "⬆" and tiles[1]["direction"] == "up"
    assert tiles[3]["arrow"] == "⬇"
    assert tiles[2]["hits"] == ["合成"]
    assert tiles[3]["hits"] == []
    assert view["has_feedback"] is True


def test_view_metadata_and_defaults():
    view = _view()
    assert view["category_label"] == "方块"
    assert view["category_icon"] == "🧱"
    assert view["difficulty_label"] == "普通"
    assert (view["attempt"], view["max_attempts"], view["remaining"]) == (3, 8, 5)
    assert view["guess_name"] == "金块"
    assert view["guess_name_en"] == "Gold Block"
    assert view["status_text"] == "还剩 5 次"
    assert view["columns"] == 4
    # The view is plain JSON-serializable data.
    json.dumps(view, ensure_ascii=False)


def test_view_accepts_none_feedback():
    view = _view(feedback=None)
    assert view["tiles"] == []
    assert view["has_feedback"] is False


def test_view_skips_non_dict_feedback_items():
    view = _view(feedback=[{"key": "a", "label": "甲", "status": "exact", "value": "1"}, "junk", None])
    assert len(view["tiles"]) == 1


def test_negative_remaining_is_clamped():
    assert _view(remaining=-2)["remaining"] == 0


def test_columns_normalized_and_clamped():
    assert _view(columns=3)["columns"] == 3
    assert _view(columns=99)["columns"] == 6
    assert _view(columns=0)["columns"] == 1
    assert _view(columns="3; }body{display:none}")["columns"] == 4
    assert _view(columns=None)["columns"] == 4


def test_html_contains_header_details():
    html = _html()
    assert "<!doctype html>" in html
    assert "<title>MCGuess · 方块</title>" in html
    assert "MINECRAFT 猜谜 · MCGUESS" in html
    assert "🧱" in html
    assert "难度 普通" in html
    assert "第 3/8 次" in html
    assert "剩余次数" in html
    assert "<strong>5</strong>" in html
    assert "本次猜测" in html and "金块" in html and "Gold Block" in html
    assert "还剩 5 次" in html


def test_html_grid_columns_follow_view():
    assert 'style="--cols: 4"' in _html()
    assert 'style="--cols: 3"' in _html(columns=3)
    assert "repeat(var(--cols, 4), minmax(0, 1fr))" in _html()


def test_html_renders_three_states_and_arrows():
    html = _html()
    assert '<article class="tile exact">' in html
    assert '<article class="tile close">' in html
    assert '<article class="tile miss">' in html
    assert html.count('<article class="tile ') == 4
    assert '<span class="dir up">⬆</span>' in html
    assert '<span class="dir down">⬇</span>' in html
    assert "🟩 完全相同" in html
    assert "🟨 接近" in html
    assert "⬜ 不匹配" in html


def test_html_highlights_set_hits_only_as_labels():
    html = _html()
    assert '<div class="hits">' in html
    assert '<span class="hit">合成</span>' in html
    assert "命中" in html
    # Raw internal values must not leak into the card.
    assert "Crafting" not in html
    assert "hit_keys" not in html


def test_html_unknown_status_and_direction_fall_back():
    feedback = [
        {"key": "x", "label": "甲", "status": "weird", "direction": "sideways", "value": "1"},
    ]
    html = _html(feedback=feedback)
    assert '<article class="tile miss">' in html
    assert '<span class="dir' not in html


def test_html_escapes_user_controlled_text():
    payload = '<script>alert("x")</script>'
    html = _html(
        category_label=payload,
        category_icon=payload,
        difficulty_label=payload,
        guess_name=payload,
        guess_name_en=payload,
        status_text='"><img src=x onerror=alert(1)>',
        feedback=[
            {"key": "k", "label": payload, "status": "close", "direction": "up", "value": payload, "hits": [payload]}
        ],
    )
    assert "<script" not in html.lower()
    assert "<img" not in html.lower()
    assert "&quot;&gt;&lt;img src=x onerror=alert(1)&gt;" in html
    assert "&lt;script&gt;alert(&quot;x&quot;)&lt;/script&gt;" in html
    assert "&lt;img src=x onerror=alert(1)&gt;" in html
    assert '<span class="hit">&lt;script&gt;' in html


def test_empty_feedback_renders_empty_state():
    html = _html(feedback=[])
    assert "本次还没有可展示的属性反馈" in html
    assert "猜中一个名字后" in html
    assert '<article class="tile ' not in html
    assert "还剩 5 次" in html
    assert "剩余次数" in html
    assert "完全相同" in html  # legend still present


def test_card_is_self_contained_without_external_resources():
    for html in (_html(), _html(feedback=[]), build_help_card_html(_help_view())):
        assert "http://" not in html
        assert "https://" not in html
        assert "<img" not in html.lower()
        assert "@import" not in html
        assert "url(" not in html
        assert "<script" not in html.lower()


def test_html_without_guess_name_hides_guessbar():
    html = _html(guess_name="", guess_name_en="")
    assert "本次猜测" not in html
    assert "第 3/8 次" in html


def _help_view() -> dict:
    return {
        "title": "《我的世界猜谜》玩法",
        "subtitle": "按属性提示逐步锁定答案",
        "commands": [
            {"command": "/猜方 开始 [方块|物品|生物] [简单|普通|困难]", "description": "新开一局", "tag": "开始游戏"},
            {"command": "/猜方 猜 <名字>", "description": "提交一次猜测", "tag": "提交猜测"},
            {"command": "/猜方 状态", "description": "查看进度", "tag": "查看进度"},
            {"command": "/猜方 放弃", "description": "公布答案", "tag": "结束本局"},
        ],
        "footer": "每局最多 8 次猜测。🟩 完全相同　🟨 接近　⬜ 不匹配",
    }


def test_help_card_renders_title_commands_and_footer():
    html = build_help_card_html(_help_view())
    assert "<!doctype html>" in html
    assert "《我的世界猜谜》玩法" in html
    assert "按属性提示逐步锁定答案" in html
    assert html.count('<article class="cmd">') == 4
    assert "/猜方 开始 [方块|物品|生物] [简单|普通|困难]" in html
    assert ">开始游戏<" in html
    assert "提交一次猜测" in html
    assert "每局最多 8 次猜测" in html


def test_help_card_empty_commands():
    html = build_help_card_html({"title": "玩法", "subtitle": "", "commands": [], "footer": ""})
    assert "暂无可用指令" in html
    assert '<article class="cmd">' not in html


def test_help_card_does_not_fabricate_empty_tags():
    html = build_help_card_html(
        {"title": "玩法", "commands": [{"command": "/mc猜 帮助", "description": "显示说明", "tag": ""}]}
    )
    assert 'class="cmd-head"' not in html
    assert 'class="tag"' not in html
    assert "/mc猜 帮助" in html


def test_help_card_footer_is_not_duplicated_with_default_legend():
    html = build_help_card_html(_help_view())
    assert html.count("每局最多 8 次猜测") == 1
    assert "<b>完全相同</b>" not in html
    assert 'class="legend"' in html


def test_help_card_escapes_user_controlled_text():
    payload = '<script>alert("help")</script>'
    html = build_help_card_html(
        {
            "title": payload,
            "subtitle": payload,
            "commands": [{"command": payload, "description": payload, "tag": payload}],
            "footer": payload,
        }
    )
    assert "<script" not in html.lower()
    assert "&lt;script&gt;alert(&quot;help&quot;)&lt;/script&gt;" in html


def test_help_card_layout_is_two_columns():
    html = build_help_card_html(_help_view())
    assert "grid-template-columns: repeat(2, minmax(0, 1fr))" in html


def test_renderer_does_not_import_astrbot():
    import inspect

    import astrbot_plugin_mcguess.card_renderer as renderer

    source = inspect.getsource(renderer)
    assert "import astrbot" not in source
    assert "from astrbot" not in source


# ---- boundary / hardening -------------------------------------------------


def test_long_unbroken_names_do_not_break_rendering():
    long_cn = "方块" * 20
    long_ascii = "A" * 200
    html = _html(guess_name=long_cn, guess_name_en=long_ascii, category_label=long_cn)
    assert long_cn in html and long_ascii in html
    assert "word-break: break-word" in html
    assert html.count("<article") == html.count("</article>")


def test_build_html_clamps_malicious_columns_directly():
    html = build_guess_card_html(
        {
            "tiles": [],
            "columns": "3;}body{display:none}",
            "category_label": "方块",
        }
    )
    assert "display:none" not in html
    assert "display: none" not in html
    assert 'style="--cols: 4"' in html


def test_hits_with_non_string_values_are_safely_escaped():
    feedback = [
        {
            "key": "obtaining",
            "label": "获得方式",
            "status": "close",
            "direction": None,
            "value": "合成",
            "hits": [123, "<img src=x onerror=alert(1)>"],
        }
    ]
    html = _html(feedback=feedback)
    assert '<span class="hit">123</span>' in html
    assert "<img" not in html.lower()
    assert "&lt;img src=x onerror=alert(1)&gt;" in html


def test_build_guess_card_html_tolerates_non_dict_view():
    html = build_guess_card_html(None)  # type: ignore[arg-type]
    assert "本次还没有可展示的属性反馈" in html
    assert "<title>MCGuess · 猜谜</title>" in html


def test_help_card_tolerates_bare_command_list_and_skips_junk():
    html = build_help_card_html(
        [  # type: ignore[arg-type]
            {"command": "/mc猜 状态", "description": "查看进度", "tag": "查看"},
            "junk",
            None,
        ]
    )
    assert html.count('<article class="cmd">') == 1
    assert "查看进度" in html
