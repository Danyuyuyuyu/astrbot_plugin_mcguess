"""Behaviour tests for the plugin entry point (commands, free text, LLM tool)."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest

pytest.importorskip("astrbot")

from astrbot.core.message.components import Image as ImageComponent  # noqa: E402
from astrbot_plugin_mcguess import main as plugin_main  # noqa: E402
from astrbot_plugin_mcguess.game_service import GameService  # noqa: E402
from astrbot_plugin_mcguess.render_service import RenderedCard  # noqa: E402


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
        self.stopped = False

    def is_private_chat(self) -> bool:
        return self._private

    def plain_result(self, text: str):
        return ("text", text)

    def image_result(self, url: str):
        return ("image", url)

    def chain_result(self, chain):
        return ("chain", chain)

    def stop_event(self) -> None:
        self.stopped = True

    async def send(self, result) -> None:
        self.sent.append(result)


class FakeRendering:
    """Stand-in for CardRenderService: returns a fixed card (or None)."""

    def __init__(self, card: RenderedCard | None = None) -> None:
        self.card = card
        self.calls = 0
        self.closed = False

    async def render(self, html: str):
        self.calls += 1
        return self.card

    async def close(self) -> None:
        self.closed = True

    def describe(self) -> str:
        return "fake rendering"


def make_plugin(tmp_dir, catalogue, *, config=None, rendering=None):
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
    plugin.rendering = rendering or FakeRendering()
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
        plugin = make_plugin(tmp_dir, catalogue, config={"group_guess_without_at": False})
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
def test_local_card_bytes_are_sent_as_image_chain(tmp_dir, catalogue):
    async def scenario():
        rendering = FakeRendering(RenderedCard(backend="local", image=b"CARD-BYTES"))
        plugin = make_plugin(tmp_dir, catalogue, config={"render_cards": True}, rendering=rendering)
        event = FakeEvent("/mc猜 开始 方块 简单")
        await collect(plugin.mc_guess(event))
        state = await plugin.games.current(event.unified_msg_origin)
        answer = answer_of(catalogue, state)

        results = await collect(plugin.mc_guess(FakeEvent(f"/mc猜 猜 {answer['name']}")))
        assert results[0][0] == "chain"
        chain = results[0][1]
        assert len(chain) == 1 and isinstance(chain[0], ImageComponent)
        assert chain[0].file.startswith("base64://")
        assert rendering.calls == 1

    run(scenario())


def test_remote_card_url_is_sent_as_image(tmp_dir, catalogue):
    async def scenario():
        rendering = FakeRendering(RenderedCard(backend="remote", image_url="http://example.invalid/card.png"))
        plugin = make_plugin(tmp_dir, catalogue, config={"render_cards": True}, rendering=rendering)
        event = FakeEvent("/mc猜 开始 方块 简单")
        await collect(plugin.mc_guess(event))
        state = await plugin.games.current(event.unified_msg_origin)
        answer = answer_of(catalogue, state)

        results = await collect(plugin.mc_guess(FakeEvent(f"/mc猜 猜 {answer['name']}")))
        assert results[0] == ("image", "http://example.invalid/card.png")

    run(scenario())


def test_card_failure_falls_back_to_text(tmp_dir, catalogue):
    async def scenario():
        rendering = FakeRendering(None)
        plugin = make_plugin(tmp_dir, catalogue, config={"render_cards": True}, rendering=rendering)
        event = FakeEvent("/mc猜 开始 方块 简单")
        await collect(plugin.mc_guess(event))
        state = await plugin.games.current(event.unified_msg_origin)
        answer = answer_of(catalogue, state)

        results = await collect(plugin.mc_guess(FakeEvent(f"/mc猜 猜 {answer['name']}")))
        assert results[0][0] == "text"
        assert "猜中了" in results[0][1]
        assert rendering.calls == 1

    run(scenario())


def test_help_card_uses_rendering_service(tmp_dir, catalogue):
    async def scenario():
        rendering = FakeRendering(RenderedCard(backend="local", image=b"HELP-CARD"))
        plugin = make_plugin(tmp_dir, catalogue, config={"render_cards": True}, rendering=rendering)
        results = await collect(plugin.mc_guess(FakeEvent("/mc猜 帮助")))
        assert results[0][0] == "chain"
        assert isinstance(results[0][1][0], ImageComponent)
        assert rendering.calls == 1

    run(scenario())


def test_render_cards_disabled_never_calls_renderer(tmp_dir, catalogue):
    async def scenario():
        rendering = FakeRendering(RenderedCard(backend="local", image=b"X"))
        plugin = make_plugin(tmp_dir, catalogue, config={"render_cards": False}, rendering=rendering)
        event = FakeEvent("/mc猜 开始 方块 简单")
        await collect(plugin.mc_guess(event))
        state = await plugin.games.current(event.unified_msg_origin)
        answer = answer_of(catalogue, state)
        results = await collect(plugin.mc_guess(FakeEvent(f"/mc猜 猜 {answer['name']}")))
        assert results[0][0] == "text"
        assert rendering.calls == 0

    run(scenario())


def test_llm_tool_sends_card_and_ends_turn(tmp_dir, catalogue):
    async def scenario():
        rendering = FakeRendering(RenderedCard(backend="local", image=b"CARD"))
        plugin = make_plugin(tmp_dir, catalogue, config={"render_cards": True}, rendering=rendering)
        await plugin.mc_guess_tool(FakeEvent("开始"), action="开始", category="方块", difficulty="简单")

        event = FakeEvent("猜")
        state = await plugin.games.current(event.unified_msg_origin)
        answer = answer_of(catalogue, state)
        guess_event = FakeEvent(answer["name"])
        result = await plugin.mc_guess_tool(guess_event, action="猜", name=answer["name"])
        assert result is None  # card sent + cards-only: no extra model call
        assert guess_event.sent and guess_event.sent[0][0] == "chain"

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


class FakeToolSet:
    def __init__(self) -> None:
        self.removed: list[str] = []

    def remove_tool(self, name: str) -> None:
        self.removed.append(name)


def llm_request() -> SimpleNamespace:
    return SimpleNamespace(
        system_prompt="BASE",
        func_tool=FakeToolSet(),
        extra_user_content_parts=[],
    )


def test_playing_prompt_uses_temporary_content_part(tmp_dir, catalogue):
    if plugin_main._TextPart is None:
        pytest.skip("this AstrBot build has no extra user content parts")

    async def scenario():
        plugin = make_plugin(tmp_dir, catalogue)
        await collect(plugin.mc_guess(FakeEvent("/mc猜 开始 方块 简单")))
        req = llm_request()
        await plugin._offer_guess_tool(FakeEvent("我猜一下"), req)

        # The cached system prompt stays byte-for-byte untouched.
        assert req.system_prompt == "BASE"
        assert len(req.extra_user_content_parts) == 1
        part = req.extra_user_content_parts[0]
        assert part.text == plugin_main.PLAYING_SYSTEM_PROMPT
        assert part.model_dump_for_context().get("_no_save") is True

        # Repeated hook calls must not stack duplicate reminders.
        await plugin._offer_guess_tool(FakeEvent("我猜一下"), req)
        assert len(req.extra_user_content_parts) == 1

    run(scenario())


def test_prompt_not_injected_without_playing_round(tmp_dir, catalogue):
    async def scenario():
        plugin = make_plugin(tmp_dir, catalogue)
        req = llm_request()
        await plugin._offer_guess_tool(FakeEvent("今天天气不错"), req)

        assert req.system_prompt == "BASE"
        assert req.extra_user_content_parts == []
        assert req.func_tool.removed == [plugin_main.llm_tool.NAME]

    run(scenario())


# ---- v1.3: guesser credit, clue board card -----------------------------------
class NamedEvent(FakeEvent):
    def get_sender_name(self) -> str:
        return "Steve"


def test_guess_records_sender_name(tmp_dir, catalogue):
    async def scenario():
        plugin = make_plugin(tmp_dir, catalogue)
        await collect(plugin.mc_guess(NamedEvent("/mc猜 开始 方块 困难")))
        state = await plugin.games.current("aiocqhttp:GroupMessage:1")
        answer = answer_of(catalogue, state)
        other = next(e for e in catalogue.entries("block") if e["id"] != answer["id"])
        await collect(plugin.mc_guess(NamedEvent(f"/mc猜 猜 {other['name']}")))
        state = await plugin.games.current("aiocqhttp:GroupMessage:1")
        assert state["players"] == ["Steve"]
        # adapters without get_sender_name still work
        assert plugin._player_name(FakeEvent("x")) == ""

    run(scenario())


def test_status_and_give_up_render_clue_board(tmp_dir, catalogue):
    async def scenario():
        rendering = FakeRendering(RenderedCard(backend="local", image=b"CARD"))
        plugin = make_plugin(tmp_dir, catalogue, config={"render_cards": True}, rendering=rendering)
        await collect(plugin.mc_guess(FakeEvent("/mc猜 开始 方块 简单")))
        results = await collect(plugin.mc_guess(FakeEvent("/mc猜 状态")))
        assert results[0][0] == "chain" and rendering.calls == 1
        results = await collect(plugin.mc_guess(FakeEvent("/mc猜 放弃")))
        assert results[0][0] == "chain" and rendering.calls == 2

    run(scenario())


def test_status_falls_back_to_text_when_render_fails(tmp_dir, catalogue):
    async def scenario():
        plugin = make_plugin(tmp_dir, catalogue, config={"render_cards": True}, rendering=FakeRendering(None))
        await collect(plugin.mc_guess(FakeEvent("/mc猜 开始 方块")))
        results = await collect(plugin.mc_guess(FakeEvent("/mc猜 状态")))
        assert results[0][0] == "text" and "进度" in results[0][1]

    run(scenario())


# ---- v1.4: group guesses without @, leaderboard --------------------------------
def test_group_guess_without_at_only_for_exact_names(tmp_dir, catalogue):
    async def scenario():
        plugin = make_plugin(tmp_dir, catalogue)
        await collect(plugin.mc_guess(FakeEvent("/mc猜 开始 方块 简单")))
        state = await plugin.games.current("aiocqhttp:GroupMessage:1")
        answer = answer_of(catalogue, state)
        other = next(e for e in catalogue.entries("block") if e["id"] != answer["id"])

        # ordinary chatter and shortcuts are ignored without @
        for text in ("今天吃什么", "放弃", "提示", f"是{other['name']}"):
            assert await collect(plugin.free_text_guess(FakeEvent(text, at=False))) == []
        assert (await plugin.games.current("aiocqhttp:GroupMessage:1"))["status"] == "playing"

        quiet = FakeEvent(other["name"], at=False)
        results = await collect(plugin.free_text_guess(quiet))
        assert results and quiet.stopped
        # repeating an already-guessed name without @ stays silent
        assert await collect(plugin.free_text_guess(FakeEvent(other["name"], at=False))) == []
        results = await collect(plugin.free_text_guess(FakeEvent(f"我猜{answer['name']}", at=False)))
        assert "猜中了" in feedback_text(results)

    run(scenario())


class Player(FakeEvent):
    def __init__(self, text, name, uid, **kwargs):
        super().__init__(text, **kwargs)
        self._name, self._uid = name, uid

    def get_sender_name(self):
        return self._name

    def get_sender_id(self):
        return self._uid


def test_leaderboard_counts_wins_and_participation(tmp_dir, catalogue):
    async def scenario():
        plugin = make_plugin(tmp_dir, catalogue)
        for winner in ("Alex", "Alex", "Steve"):
            await collect(plugin.mc_guess(FakeEvent("/mc猜 开始 方块 简单")))
            state = await plugin.games.current("aiocqhttp:GroupMessage:1")
            answer = answer_of(catalogue, state)
            other = next(e for e in catalogue.entries("block") if e["id"] != answer["id"])
            loser = "Steve" if winner == "Alex" else "Alex"
            await collect(plugin.mc_guess(Player(f"/mc猜 猜 {other['name']}", loser, f"id-{loser}")))
            await collect(plugin.mc_guess(Player(f"/mc猜 猜 {answer['name']}", winner, f"id-{winner}")))
        # same id with a new nickname is still the same player
        board = await plugin.games.leaderboard("aiocqhttp:GroupMessage:1")
        assert board["rounds"] == 3
        assert [(r["name"], r["wins"], r["rounds"], r["best"]) for r in board["players"]] == [
            ("Alex", 2, 3, 2),
            ("Steve", 1, 3, 2),
        ]
        text = feedback_text(await collect(plugin.mc_guess(FakeEvent("/mc猜 排行"))))
        assert "1. Alex：猜中 2 次" in text

    run(scenario())


def test_rank_card_and_empty_board(tmp_dir, catalogue):
    async def scenario():
        rendering = FakeRendering(RenderedCard(backend="local", image=b"CARD"))
        plugin = make_plugin(tmp_dir, catalogue, config={"render_cards": True}, rendering=rendering)
        results = await collect(plugin.mc_guess(FakeEvent("/mc猜 排行榜")))
        assert results[0][0] == "chain" and rendering.calls == 1
        plain = make_plugin(tmp_dir, catalogue)
        assert "还没有完成的对局" in feedback_text(await collect(plain.mc_guess(FakeEvent("/mc猜 排行"))))

    run(scenario())


def test_last_tool_use_is_pruned(tmp_dir, catalogue):
    plugin = make_plugin(tmp_dir, catalogue)
    plugin._last_tool_use = {f"s{i}": -10_000.0 for i in range(100)}
    plugin._note_tool_use("fresh")
    assert list(plugin._last_tool_use) == ["fresh"]
