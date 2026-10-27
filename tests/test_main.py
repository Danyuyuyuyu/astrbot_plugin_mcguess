"""Behaviour tests for the plugin entry point (commands, free text, LLM tool)."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest

pytest.importorskip("astrbot")

from astrbot_plugin_mcguess import main as plugin_main  # noqa: E402
from astrbot_plugin_mcguess.game_service import GameService  # noqa: E402


class FakeEvent:
    """Only the AstrMessageEvent surface used by the plugin."""

    def __init__(
        self,
        text: str,
        *,
        umo: str = "aiocqhttp:GroupMessage:1",
        group_id: str = "1",
        private: bool = False,
        at: bool = True,
    ) -> None:
        self.message_str = text
        self.unified_msg_origin = umo
        self.message_obj = SimpleNamespace(session_id=umo.rsplit(":", 1)[-1], group_id=group_id)
        self.is_at_or_wake_command = at
        self._private = private
        self.sent = []

    def is_private_chat(self) -> bool:
        return self._private

    def plain_result(self, text: str):
        return ("text", text)

    def image_result(self, url: str):
        return ("image", url)

    async def send(self, result) -> None:
        self.sent.append(result)


def make_plugin(tmp_dir, catalogue, *, config=None, html_render=None):
    plugin = object.__new__(plugin_main.MCGuessPlugin)
    plugin.config = {
        "render_cards": False,
        "free_text_guess": True,
        "llm_tool_enabled": True,
        "llm_tool_cards_only": True,
        "require_finish_in_group": True,
    }
    plugin.config.update(config or {})
    plugin.max_attempts = 8
    plugin.games = GameService(tmp_dir, max_attempts=8, hint_limit=3, idle_timeout_minutes=30)
    plugin.catalogue = catalogue
    plugin._last_tool_use = {}
    plugin._render_failures = 0
    plugin._render_block_until = 0.0
    if html_render is not None:
        plugin.html_render = html_render
    return plugin


async def collect(generator):
    return [item async for item in generator]


def run(coro):
    return asyncio.run(coro)


def feedback_text(results) -> str:
    assert results, "handler produced no reply"
    return results[0][1]


def answer_of(catalogue, state):
    return catalogue.entry(state["category"], state["answerId"])


# ---- command parsing --------------------------------------------------------
def test_command_args_drops_wake_prefix_and_command_word():
    plugin = object.__new__(plugin_main.MCGuessPlugin)
    assert plugin._command_args("/mc猜 开始 方块") == "开始 方块"
    assert plugin._command_args("mc猜 猜 金块") == "猜 金块"
    assert plugin._command_args("/猜方块 困难") == "困难"
    assert plugin._command_args("随便说点什么") == "随便说点什么"


def test_help_and_unknown_subcommand(tmp_dir):
    async def scenario():
        plugin = make_plugin(tmp_dir, None)
        help_reply = feedback_text(await collect(plugin.mc_guess(FakeEvent("/mc猜 帮助"))))
        assert "玩法" in help_reply and "/mc猜 开始 方块" in help_reply
        unknown = feedback_text(await collect(plugin.mc_guess(FakeEvent("/mc猜 乱写"))))
        assert "未知子命令" in unknown

    run(scenario())


# ---- start / guess / status -------------------------------------------------
def test_start_guess_and_status_flow(tmp_dir, catalogue):
    async def scenario():
        plugin = make_plugin(tmp_dir, catalogue)
        event = FakeEvent("/mc猜 开始 方块 简单")
        started = feedback_text(await collect(plugin.mc_guess(event)))
        assert "已开始「方块」猜谜" in started and "简单" in started

        state = await plugin.games.current(event.unified_msg_origin)
        assert state["category"] == "block" and state["difficulty"] == "easy"

        answer = answer_of(catalogue, state)
        guessed = feedback_text(
            await collect(plugin.mc_guess(FakeEvent(f"/mc猜 猜 {answer['name']}")))
        )
        assert "猜中了" in guessed
        assert "🟩" in guessed

        status = feedback_text(await collect(plugin.mc_guess(FakeEvent("/mc猜 状态"))))
        assert "已经猜中" in status

    run(scenario())


def test_unknown_name_reports_candidates(tmp_dir, catalogue):
    async def scenario():
        plugin = make_plugin(tmp_dir, catalogue)
        event = FakeEvent("/mc猜 开始 方块 简单")
        await collect(plugin.mc_guess(event))
        reply = feedback_text(await collect(plugin.mc_guess(FakeEvent("/mc猜 猜 钻"))))
        assert "匹配到多个方块" in reply and "钻石矿石" in reply

        reply = feedback_text(await collect(plugin.mc_guess(FakeEvent("/mc猜 猜 不存在"))))
        assert "没有找到" in reply

    run(scenario())


def test_group_start_requires_finishing_current_round(tmp_dir, catalogue):
    async def scenario():
        plugin = make_plugin(tmp_dir, catalogue)
        await collect(plugin.mc_guess(FakeEvent("/mc猜 开始 方块 简单")))
        reply = feedback_text(await collect(plugin.mc_guess(FakeEvent("/mc猜 开始 物品 简单"))))
        assert "还没结束" in reply

        private = FakeEvent("/mc猜 开始 物品 简单", umo="aiocqhttp:FriendMessage:2", private=True)
        reply = feedback_text(await collect(plugin.mc_guess(private)))
        assert "已开始「物品」猜谜" in reply

    run(scenario())


def test_hint_and_give_up(tmp_dir, catalogue):
    async def scenario():
        plugin = make_plugin(tmp_dir, catalogue)
        event = FakeEvent("/mc猜 开始 方块 简单")
        await collect(plugin.mc_guess(event))

        hint = feedback_text(await collect(plugin.mc_guess(FakeEvent("/mc猜 提示"))))
        assert "提示（1/3）" in hint
        assert "工具" in hint

        give_up = feedback_text(await collect(plugin.mc_guess(FakeEvent("/mc猜 放弃"))))
        assert "本局结束" in give_up and "答案是" in give_up

    run(scenario())


def test_status_without_round_is_an_error(tmp_dir, catalogue):
    async def scenario():
        plugin = make_plugin(tmp_dir, catalogue)
        reply = feedback_text(await collect(plugin.mc_guess(FakeEvent("/mc猜 状态"))))
        assert "还没有对局" in reply

    run(scenario())


def test_name_without_guess_keyword_is_treated_as_guess(tmp_dir, catalogue):
    async def scenario():
        plugin = make_plugin(tmp_dir, catalogue)
        event = FakeEvent("/mc猜 开始 方块 简单")
        await collect(plugin.mc_guess(event))
        state = await plugin.games.current(event.unified_msg_origin)
        answer = answer_of(catalogue, state)

        reply = feedback_text(await collect(plugin.mc_guess(FakeEvent(f"/mc猜 {answer['name']}"))))
        assert "猜中了" in reply

        idle = make_plugin(tmp_dir, catalogue)
        reply = feedback_text(
            await collect(idle.mc_guess(FakeEvent("/mc猜 铁砧", umo="aiocqhttp:GroupMessage:2")))
        )
        assert "未知子命令" in reply

    run(scenario())


# ---- free text shortcut -----------------------------------------------------
def test_free_text_guesses_when_awake(tmp_dir, catalogue):
    async def scenario():
        plugin = make_plugin(tmp_dir, catalogue)
        event = FakeEvent("/mc猜 开始 方块 简单")
        await collect(plugin.mc_guess(event))
        state = await plugin.games.current(event.unified_msg_origin)
        answer = answer_of(catalogue, state)

        results = await collect(plugin.free_text_guess(FakeEvent(answer["name"])))
        assert results and "猜中了" in results[0][1]

    run(scenario())


def test_free_text_ignored_without_wake_in_group(tmp_dir, catalogue):
    async def scenario():
        plugin = make_plugin(tmp_dir, catalogue)
        event = FakeEvent("/mc猜 开始 方块 简单")
        await collect(plugin.mc_guess(event))
        state = await plugin.games.current(event.unified_msg_origin)
        answer = answer_of(catalogue, state)

        results = await collect(plugin.free_text_guess(FakeEvent(answer["name"], at=False)))
        assert results == []
        assert (await plugin.games.current(event.unified_msg_origin))["guesses"] == []

        private = FakeEvent(answer["name"], umo="aiocqhttp:FriendMessage:2", private=True, at=False)
        results = await collect(plugin.free_text_guess(private))
        assert results == []  # no round for that session

    run(scenario())


@pytest.mark.parametrize("text", ["/mc猜 提示", "mc猜 状态", "今天天气不错", "这个方块好难猜啊我真的不知道"])
def test_free_text_leaves_other_messages_alone(tmp_dir, catalogue, text):
    async def scenario():
        plugin = make_plugin(tmp_dir, catalogue)
        event = FakeEvent("/mc猜 开始 方块 简单")
        await collect(plugin.mc_guess(event))
        assert await collect(plugin.free_text_guess(FakeEvent(text))) == []

    run(scenario())


def test_free_text_accepts_guess_prefixes(tmp_dir, catalogue):
    async def scenario():
        plugin = make_plugin(tmp_dir, catalogue)
        event = FakeEvent("/mc猜 开始 方块 简单")
        await collect(plugin.mc_guess(event))
        state = await plugin.games.current(event.unified_msg_origin)
        answer = answer_of(catalogue, state)

        results = await collect(plugin.free_text_guess(FakeEvent(f"我猜{answer['name']}")))
        assert results and "猜中了" in results[0][1]

    run(scenario())


def test_free_text_can_be_disabled(tmp_dir, catalogue):
    async def scenario():
        plugin = make_plugin(tmp_dir, catalogue, config={"free_text_guess": False})
        event = FakeEvent("/mc猜 开始 方块 简单")
        await collect(plugin.mc_guess(event))
        state = await plugin.games.current(event.unified_msg_origin)
        answer = answer_of(catalogue, state)
        assert await collect(plugin.free_text_guess(FakeEvent(answer["name"]))) == []

    run(scenario())


# ---- card rendering + fallback ---------------------------------------------
def test_cards_are_used_when_renderer_works(tmp_dir, catalogue):
    async def fake_html_render(tmpl, data, return_url=True, options=None, umo=None):
        assert "<html" in tmpl.lower()
        return "http://example.invalid/card.png"

    async def scenario():
        plugin = make_plugin(tmp_dir, catalogue, config={"render_cards": True}, html_render=fake_html_render)
        event = FakeEvent("/mc猜 开始 方块 简单")
        await collect(plugin.mc_guess(event))
        state = await plugin.games.current(event.unified_msg_origin)
        answer = answer_of(catalogue, state)

        results = await collect(plugin.mc_guess(FakeEvent(f"/mc猜 猜 {answer['name']}")))
        assert results[0][0] == "image"
        assert results[0][1] == "http://example.invalid/card.png"

    run(scenario())


def test_card_failure_falls_back_to_text(tmp_dir, catalogue):
    async def broken_html_render(*args, **kwargs):
        raise RuntimeError("no browser")

    async def scenario():
        plugin = make_plugin(tmp_dir, catalogue, config={"render_cards": True}, html_render=broken_html_render)
        event = FakeEvent("/mc猜 开始 方块 简单")
        await collect(plugin.mc_guess(event))
        state = await plugin.games.current(event.unified_msg_origin)
        answer = answer_of(catalogue, state)

        results = await collect(plugin.mc_guess(FakeEvent(f"/mc猜 猜 {answer['name']}")))
        assert results[0][0] == "text"
        assert "猜中了" in results[0][1]
        assert plugin._render_block_until > 0  # breaker opened after the failure

    run(scenario())


def test_slow_renderer_times_out_and_breaker_skips_next_render(tmp_dir, catalogue):
    calls = {"count": 0}

    async def slow_renderer(tmpl, data, return_url=True, options=None, umo=None):
        calls["count"] += 1
        await asyncio.sleep(0.3)
        return "http://example.invalid/slow.png"

    def pick_wrong(state, exclude=()):
        category = catalogue.category(state["category"])
        pool = {entry["id"] for entry in category.answer_pool(state["difficulty"])}
        for entry in category.entries:
            if entry["id"] in pool and entry["id"] != state["answerId"] and entry["id"] not in exclude:
                return entry
        raise AssertionError("catalogue needs another guessable entry")

    async def scenario():
        plugin = make_plugin(
            tmp_dir,
            catalogue,
            config={"render_cards": True, "render_timeout_seconds": 0.05},
            html_render=slow_renderer,
        )
        event = FakeEvent("/mc猜 开始 方块 简单")
        await collect(plugin.mc_guess(event))
        state = await plugin.games.current(event.unified_msg_origin)

        first = pick_wrong(state)
        reply = await collect(plugin.mc_guess(FakeEvent(f"/mc猜 猜 {first['name']}")))
        assert reply[0][0] == "text"
        assert calls["count"] == 1
        assert plugin._render_block_until > 0

        state = await plugin.games.current(event.unified_msg_origin)
        second = pick_wrong(state, exclude={first["id"]})
        reply = await collect(plugin.mc_guess(FakeEvent(f"/mc猜 猜 {second['name']}")))
        assert reply[0][0] == "text"
        assert calls["count"] == 1  # breaker short-circuited the second render

    run(scenario())


# ---- LLM tool ---------------------------------------------------------------
def test_llm_tool_dispatch_and_json_shape(tmp_dir, catalogue):
    async def scenario():
        plugin = make_plugin(tmp_dir, catalogue)
        start_payload = json.loads(
            await plugin.mc_guess_tool(FakeEvent("帮我猜个方块"), action="开始", category="方块", difficulty="简单")
        )
        assert start_payload["status"] == "ok"
        assert "已开始" in start_payload["text"]

        event = FakeEvent("我这局玩什么")
        state = await plugin.games.current(event.unified_msg_origin)
        answer = answer_of(catalogue, state)
        guess_payload = json.loads(
            await plugin.mc_guess_tool(FakeEvent(answer["name"]), action="猜", name=answer["name"])
        )
        assert guess_payload["status"] == "ok" and "猜中了" in guess_payload["text"]

        error_payload = json.loads(await plugin.mc_guess_tool(FakeEvent("再猜"), action="猜"))
        assert error_payload["status"] == "invalid"

    run(scenario())


def test_llm_tool_without_round_returns_error(tmp_dir, catalogue):
    async def scenario():
        plugin = make_plugin(tmp_dir, catalogue)
        payload = json.loads(await plugin.mc_guess_tool(FakeEvent("状态"), action="状态"))
        assert payload["status"] == "error"
        assert "还没有对局" in payload["message"]

    run(scenario())


def test_tool_visibility_follows_game_state(tmp_dir, catalogue):
    async def scenario():
        plugin = make_plugin(tmp_dir, catalogue)
        visible, playing = await plugin._tool_visibility(FakeEvent("今天天气不错"))
        assert visible is False and playing is False

        event = FakeEvent("/mc猜 开始 方块 简单")
        await collect(plugin.mc_guess(event))
        visible, playing = await plugin._tool_visibility(FakeEvent("我猜一下"))
        assert visible is True and playing is True

    run(scenario())
