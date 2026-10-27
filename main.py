"""AstrBot plugin: Minecraft-themed guessing game (blocks / items / mobs)."""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.event.filter import EventMessageType
from astrbot.api.star import Context, Star, StarTools
from astrbot.core.message.components import Image as ImageComponent
from astrbot.core.provider.register import llm_tools

try:  # AstrBot >= 4.20 ships provider-facing content parts; older builds do not.
    from astrbot.core.agent.message import TextPart as _TextPart
except ImportError:  # pragma: no cover - keep old AstrBot builds loadable
    _TextPart = None

from . import core, llm_tool
from . import card_views
from .card_renderer import build_guess_card_html, build_help_card_html, build_rank_card_html, build_status_card_html
from .catalog import CatalogError, Catalogue
from .game_service import EXPIRED, GAVE_UP, LOST, PLAYING, WON, GameService
from .render_service import BACKEND_AUTO, CardRenderService, RenderSettings, RenderedCard
from .renderer import LocalCardRenderer, LocalRenderSettings

PLUGIN_NAME = "astrbot_plugin_mcguess"
CATALOG_FILE = Path(__file__).parent / "data" / "catalog.json"

FREE_TEXT_MAX_LENGTH = 16
FOLLOW_UP_SECONDS = 600

CATEGORY_WORDS = {
    "方块": "block",
    "方塊": "block",
    "block": "block",
    "blocks": "block",
    "物品": "item",
    "item": "item",
    "items": "item",
    "生物": "mob",
    "怪物": "mob",
    "mob": "mob",
    "mobs": "mob",
    "entity": "mob",
}

DIFFICULTY_WORDS = {
    "简单": "easy",
    "簡單": "easy",
    "easy": "easy",
    "新手": "easy",
    "普通": "normal",
    "normal": "normal",
    "标准": "normal",
    "標準": "normal",
    "困难": "hard",
    "困難": "hard",
    "hard": "hard",
    "地狱": "hard",
    "地獄": "hard",
}

DIFFICULTY_LABELS = {"easy": "简单", "normal": "普通", "hard": "困难"}

COMMAND_ACTIONS = {
    "": "help",
    "帮助": "help",
    "help": "help",
    "玩法": "help",
    "怎么玩": "help",
    "h": "help",
    "开始": "start",
    "开局": "start",
    "start": "start",
    "新局": "start",
    "猜": "guess",
    "猜一下": "guess",
    "guess": "guess",
    "状态": "status",
    "进度": "status",
    "status": "status",
    "提示": "hint",
    "hint": "hint",
    "tip": "hint",
    "放弃": "giveup",
    "结束": "giveup",
    "giveup": "giveup",
    "quit": "giveup",
    "排行": "rank",
    "排行榜": "rank",
    "排名": "rank",
    "榜": "rank",
    "rank": "rank",
    "top": "rank",
}

COMMAND_WORDS = {
    "mc猜",
    "mcguess",
    "猜mc",
    "猜方块",
    "猜个方块",
    "猜物品",
    "猜个物品",
    "猜生物",
    "猜个生物",
}

SHORTCUT_WORDS = {"状态": "status", "进度": "status", "提示": "hint", "放弃": "giveup"}

GUESS_PREFIXES = ("我猜是", "我猜", "猜是", "猜一下", "是", "猜")
# Without @ only explicit guesses count: "是X" is too common in normal chat.
QUIET_GUESS_PREFIXES = ("我猜是", "我猜", "猜是", "猜一下", "猜")

PLAYING_SYSTEM_PROMPT = (
    "\n\n【MC 猜谜】当前会话有一局《我的世界》猜谜正在进行。"
    "用户只发一个方块/物品/生物名字、或让你猜某个名字时，调用 mc_guess 工具"
    "（action=\"猜\", name=用户说的名字），不要自己编造属性反馈或答案。"
    "用户问进度用 action=\"状态\"，要提示用 action=\"提示\"，放弃用 action=\"放弃\"。"
)


def _temp_text_part(text: str) -> Any | None:
    """Wrap ``text`` in a provider-only content part that is never saved to history."""
    if _TextPart is None:  # pragma: no cover - old AstrBot without content parts
        return None
    part = _TextPart(text=text)
    mark_as_temp = getattr(part, "mark_as_temp", None)
    if callable(mark_as_temp):
        part = mark_as_temp()
    return part


@dataclass
class ActionResult:
    """One user-facing reply: text always, plus an optional rendered card image."""

    text: str
    image: bytes | None = None
    image_url: str | None = None
    error: bool = False

    def has_image(self) -> bool:
        return bool(self.image or self.image_url)


def _mark_doc(text: str):
    """llm_tool reads the tool description from the docstring at decoration time."""

    def decorator(func):
        func.__doc__ = text
        return func

    return decorator


def _tool_schema(name: str, parameters: dict[str, Any]):
    """Replace the auto-generated flat schema with the full JSON schema."""

    def decorator(func):
        for tool in reversed(llm_tools.func_list):
            if tool.name == name:
                tool.parameters = parameters
                break
        return func

    return decorator


def _split_first(text: str) -> tuple[str, str]:
    head, _, rest = (text or "").strip().partition(" ")
    return head, rest.strip()


class MCGuessPlugin(Star):
    """Minecraft block / item / mob guessing game for every chat session."""

    def __init__(self, context: Context, config: AstrBotConfig | None = None) -> None:
        super().__init__(context)
        self.config = config or {}
        self.data_dir = StarTools.get_data_dir(PLUGIN_NAME)
        self.max_attempts = self._int_config("max_attempts", 25, 3, 50)
        hint_limit = self._int_config("hint_limit", 3, 0, 10)
        idle_timeout = self._int_config("idle_timeout_minutes", 30, 0, 24 * 60)
        self.games = GameService(
            self.data_dir,
            max_attempts=self.max_attempts,
            hint_limit=hint_limit,
            idle_timeout_minutes=idle_timeout,
        )
        self._last_tool_use: dict[str, float] = {}
        self.rendering = CardRenderService(
            self._build_render_settings(),
            local=self._build_local_renderer(),
            remote=self._remote_render,
            logger=self.logger,
        )
        self.catalogue: Catalogue | None = None
        try:
            self.catalogue = Catalogue.load(CATALOG_FILE)
        except CatalogError as exc:
            logger.error("MC 猜谜题库加载失败：%s", exc)

    async def initialize(self) -> None:
        self._verify_llm_tools()
        logger.info("MC 猜谜卡片渲染：%s", self.rendering.describe())
        if self.catalogue is None:
            logger.error("MC 猜谜题库不可用，请检查 %s", CATALOG_FILE)
            return
        counts = "、".join(
            f"{self.catalogue.category(key).label} {len(self.catalogue.entries(key))} 条"
            for key in self.catalogue.category_keys()
        )
        logger.info("MC 猜谜题库已加载：%s", counts)

    async def terminate(self) -> None:
        """Release the local Chromium process when the plugin is unloaded."""
        await self.rendering.close()

    def _verify_llm_tools(self) -> None:
        tool = llm_tools.get_func(llm_tool.NAME)
        if tool is None or tool.parameters != llm_tool.SCHEMA:
            logger.error("MC 猜谜 LLM 工具 %s 未按预期注册，请检查 AstrBot 版本兼容性。", llm_tool.NAME)

    # ---- config helpers -------------------------------------------------
    def _config(self, key: str, default: Any) -> Any:
        try:
            value = self.config.get(key, default)
        except AttributeError:
            return default
        return default if value is None else value

    def _bool_config(self, key: str, default: bool) -> bool:
        return bool(self._config(key, default))

    def _int_config(self, key: str, default: int, minimum: int, maximum: int) -> int:
        try:
            value = int(self._config(key, default))
        except (TypeError, ValueError):
            return default
        return max(minimum, min(maximum, value))

    def _float_config(self, key: str, default: float, minimum: float, maximum: float) -> float:
        try:
            value = float(self._config(key, default))
        except (TypeError, ValueError):
            return default
        return max(minimum, min(maximum, value))

    # ---- card rendering wiring -----------------------------------------
    def _build_render_settings(self) -> RenderSettings:
        return RenderSettings(
            backend=str(self._config("render_backend", BACKEND_AUTO)),
            remote_fallback=self._bool_config("remote_render_fallback", True),
            local_timeout=self._float_config("local_render_timeout_seconds", 20.0, 2.0, 120.0),
            remote_timeout=self._float_config("render_timeout_seconds", 15.0, 2.0, 120.0),
        )

    def _build_local_renderer(self) -> LocalCardRenderer | None:
        if not LocalCardRenderer.is_supported():
            logger.warning("未安装 playwright，本地卡片渲染不可用，将回退到 AstrBot 的 html_render。")
            return None
        settings = LocalRenderSettings(
            image_format=str(self._config("card_image_format", "jpeg")),
            quality=self._int_config("card_image_quality", 80, 30, 95),
            scale=self._float_config("render_scale", 1.5, 0.5, 2.0),
            browser_path=str(self._config("browser_path", "") or ""),
        )
        return LocalCardRenderer(settings, logger=self.logger)

    async def _remote_render(self, html: str) -> str:
        """Remote fallback: AstrBot's html_render (t2i service), returns an image URL."""
        image_format = str(self._config("card_image_format", "jpeg")).strip().lower()
        if image_format not in ("jpeg", "png"):
            image_format = "jpeg"
        options: dict[str, Any] = {"full_page": True, "animations": "disabled", "type": image_format}
        if image_format == "jpeg":
            options["quality"] = self._int_config("card_image_quality", 80, 30, 95)
        return await self.html_render(html, {}, options=options)

    # ---- session / event helpers ---------------------------------------
    @staticmethod
    def _session_id(event: AstrMessageEvent) -> str:
        umo = str(getattr(event, "unified_msg_origin", "") or "")
        if umo:
            return umo
        return str(getattr(getattr(event, "message_obj", None), "session_id", "") or "")

    @staticmethod
    def _player_name(event: AstrMessageEvent) -> str:
        """Display name of the sender, used to credit guesses on the card."""
        try:
            name = event.get_sender_name()
        except Exception:  # noqa: BLE001 - optional on some adapters / test doubles
            name = ""
        if not name:
            sender = getattr(getattr(event, "message_obj", None), "sender", None)
            name = getattr(sender, "nickname", "") or getattr(sender, "user_id", "")
        return str(name or "").strip()

    @staticmethod
    def _player_id(event: AstrMessageEvent) -> str:
        """Stable sender id for the leaderboard (nicknames change, ids do not)."""
        try:
            sender_id = event.get_sender_id()
        except Exception:  # noqa: BLE001
            sender_id = ""
        if not sender_id:
            sender = getattr(getattr(event, "message_obj", None), "sender", None)
            sender_id = getattr(sender, "user_id", "")
        return str(sender_id or "").strip()

    def _note_tool_use(self, session_id: str) -> None:
        """Remember the last LLM tool call per session; forget sessions idle past the follow-up window."""
        now = time.monotonic()
        self._last_tool_use[session_id] = now
        if len(self._last_tool_use) > 64:
            cutoff = now - FOLLOW_UP_SECONDS
            for key in [key for key, used in self._last_tool_use.items() if used < cutoff]:
                del self._last_tool_use[key]

    @staticmethod
    def _is_group(event: AstrMessageEvent) -> bool:
        try:
            return not event.is_private_chat()
        except Exception:  # noqa: BLE001 - fall back to message object
            return bool(getattr(event.message_obj, "group_id", ""))

    @staticmethod
    def _command_args(text: str) -> str:
        """Drop a leading wake prefix and the matched command word."""
        cleaned = (text or "").strip()
        if cleaned.startswith(("/", "／")):
            cleaned = cleaned[1:].strip()
        head, rest = _split_first(cleaned)
        if head.casefold() in COMMAND_WORDS:
            return rest
        return cleaned

    def _require_catalogue(self) -> Catalogue:
        if self.catalogue is None:
            raise CatalogError("题库未加载，请检查插件目录下的 data/catalog.json。")
        return self.catalogue

    # ---- command handlers ----------------------------------------------
    @filter.command("mc猜", alias={"mcguess", "猜mc"})
    async def mc_guess(self, event: AstrMessageEvent):
        """MC 猜方块/物品/生物：/mc猜 开始 方块 [简单|普通|困难]。"""
        try:
            result = await self._dispatch(event, self._command_args(event.message_str))
        except CatalogError as exc:
            result = ActionResult(str(exc), error=True)
        yield self._emit(event, result)

    @filter.command("猜方块", alias={"猜个方块"})
    async def guess_block(self, event: AstrMessageEvent):
        """直接开一局猜方块：/猜方块 [简单|普通|困难]。"""
        arguments = self._command_args(event.message_str)
        yield self._emit(event, await self._safe_start(event, "block", arguments))

    @filter.command("猜物品", alias={"猜个物品"})
    async def guess_item(self, event: AstrMessageEvent):
        """直接开一局猜物品：/猜物品 [简单|普通|困难]。"""
        arguments = self._command_args(event.message_str)
        yield self._emit(event, await self._safe_start(event, "item", arguments))

    @filter.command("猜生物", alias={"猜个生物"})
    async def guess_mob(self, event: AstrMessageEvent):
        """直接开一局猜生物：/猜生物 [简单|普通|困难]。"""
        arguments = self._command_args(event.message_str)
        yield self._emit(event, await self._safe_start(event, "mob", arguments))

    # ---- dispatch -------------------------------------------------------
    async def _dispatch(self, event: AstrMessageEvent, arguments: str) -> ActionResult:
        head, rest = _split_first(arguments)
        word = head.casefold()
        action = COMMAND_ACTIONS.get(word)
        if action is None:
            category_key = CATEGORY_WORDS.get(word)
            if category_key is not None:
                return await self._action_start(event, category_key, rest)
            if not head:
                return await self._action_help()
            state = await self.games.current(self._session_id(event))
            if state is not None and state.get("status") == PLAYING:
                # A player typed "/mc猜 钻石矿石" (forgot the 猜 keyword): treat it as a guess.
                return await self._action_guess(event, arguments)
            return ActionResult(f"未知子命令「{head}」。\n\n{self._help_text()}", error=True)
        if action == "help":
            return await self._action_help()
        if action == "start":
            return await self._action_start(event, None, rest)
        if action == "guess":
            return await self._action_guess(event, rest)
        if action == "status":
            return await self._action_status(event)
        if action == "hint":
            return await self._action_hint(event)
        if action == "giveup":
            return await self._action_give_up(event)
        if action == "rank":
            return await self._action_rank(event)
        return await self._action_help()

    async def _safe_start(self, event: AstrMessageEvent, category_key: str, arguments: str) -> ActionResult:
        try:
            return await self._action_start(event, category_key, arguments)
        except CatalogError as exc:
            return ActionResult(str(exc), error=True)

    # ---- rendering ------------------------------------------------------
    async def _render_card(self, html: str) -> RenderedCard | None:
        """Render one card, preferring the local browser and degrading to text.

        All timeout / breaker / fallback policy lives in ``CardRenderService``;
        this method only honours the ``render_cards`` switch.
        """
        if not self._bool_config("render_cards", True):
            return None
        return await self.rendering.render(html)

    @staticmethod
    def _with_card(text: str, card: RenderedCard | None) -> ActionResult:
        if card is None:
            return ActionResult(text)
        return ActionResult(text=text, image=card.image, image_url=card.image_url)

    # ---- actions --------------------------------------------------------
    async def _action_start(
        self,
        event: AstrMessageEvent,
        category_key: str | None,
        arguments: str,
    ) -> ActionResult:
        catalogue = self._require_catalogue()
        tokens = (arguments or "").split()
        index = 0
        if category_key is None:
            if not tokens:
                return ActionResult(
                    "请指定要猜的类型：`/mc猜 开始 方块`、`/mc猜 开始 物品` 或 `/mc猜 开始 生物`。",
                    error=True,
                )
            category_key = CATEGORY_WORDS.get(tokens[0].casefold())
            if category_key is None:
                return ActionResult("类型只能是 方块 / 物品 / 生物。", error=True)
            index = 1
        difficulty = "normal"
        if index < len(tokens):
            difficulty = DIFFICULTY_WORDS.get(tokens[index].casefold())  # type: ignore[assignment]
            if difficulty is None:
                return ActionResult("难度只能是 简单 / 普通 / 困难。", error=True)
            index += 1
        if index < len(tokens):
            return ActionResult("参数过多。用法：`/mc猜 开始 方块 [简单|普通|困难]`", error=True)

        category = catalogue.category(category_key)
        require_finish = self._bool_config("require_finish_in_group", True) and self._is_group(event)
        try:
            await self.games.start(
                self._session_id(event),
                category.key,
                difficulty,
                catalogue,
                require_finish=require_finish,
            )
        except ValueError as exc:
            return ActionResult(str(exc), error=True)
        pool_size = len(catalogue.answer_pool(category.key, difficulty))
        hint_limit = self.games.hint_limit
        return ActionResult(
            f"已开始「{category.label}」猜谜（{DIFFICULTY_LABELS[difficulty]}，答案池 {pool_size} 条）。\n"
            f"本局最多 {self.max_attempts} 次，可用提示 {hint_limit} 次。\n"
            f"发送 `/mc猜 猜 <名字>` 提交；对局中也可以直接发名字。\n"
            f"示例：`/mc猜 猜 钻石矿石`、`/mc猜 提示`、`/mc猜 状态`。"
        )

    async def _action_guess(self, event: AstrMessageEvent, query: str) -> ActionResult:
        catalogue = self._require_catalogue()
        name = (query or "").strip()
        if not name:
            return ActionResult("请提供要猜的名字，例如：`/mc猜 猜 钻石矿石`。", error=True)
        state = await self.games.current(self._session_id(event))
        if state is None:
            return ActionResult("本会话还没有对局，请先发送 `/mc猜 开始 方块`（或物品/生物）。", error=True)
        category = catalogue.category(str(state.get("category")))
        if state.get("status") != PLAYING:
            answer = self.games.answer_of(state, catalogue)
            return ActionResult(self._ended_text(state, answer))
        guess = category.match_exact(name)
        if guess is None:
            matched, candidates = category.resolve(name, limit=6)
            if matched is not None:
                guess = matched
            elif candidates:
                names = "、".join(item["name"] for item in candidates)
                return ActionResult(f"匹配到多个{category.label}：{names}。请输入更完整的名字。", error=True)
            else:
                return ActionResult(
                    f"没有找到叫「{name}」的{category.label}。可以只用中文名，也可以试试英文名或 ID。",
                    error=True,
                )
        try:
            updated, row, answer = await self.games.guess(
                self._session_id(event),
                guess,
                catalogue,
                player=self._player_name(event),
                player_id=self._player_id(event),
            )
        except ValueError as exc:
            return ActionResult(str(exc), error=True)
        return await self._guess_reply(catalogue, updated, row, guess, answer)

    async def _action_status(self, event: AstrMessageEvent) -> ActionResult:
        catalogue = self._require_catalogue()
        state = await self.games.current(self._session_id(event))
        if state is None:
            return ActionResult("本会话还没有对局，发送 `/mc猜 开始 方块` 开一局吧。", error=True)
        category = catalogue.category(str(state.get("category")))
        difficulty = str(state.get("difficulty") or "normal")
        status = str(state.get("status") or PLAYING)
        if status != PLAYING:
            answer = self.games.answer_of(state, catalogue)
            return ActionResult(self._ended_text(state, answer))
        guesses = [str(item) for item in (state.get("guesses") or [])]
        names = []
        for entry_id in guesses:
            entry = category.entry(entry_id)
            names.append(entry["name"] if entry else entry_id)
        hints = list(state.get("hints") or [])
        guessed_text = "、".join(names) if names else "暂无"
        text = (
            f"📋 {category.label}局 · {DIFFICULTY_LABELS.get(difficulty, difficulty)}\n"
            f"进度：{len(guesses)}/{self.max_attempts}，剩余 {max(0, self.max_attempts - len(guesses))} 次\n"
            f"已用提示：{len(hints)}/{self.games.hint_limit}\n"
            f"已猜：{guessed_text}"
        )
        answer = self.games.answer_of(state, catalogue)
        return await self._status_card(text, state, category, answer)

    async def _status_card(self, text: str, state: dict[str, Any], category, answer) -> ActionResult:
        """Clue-board card for status / give-up; plain text when cards are off or fail."""
        if answer is None or not self._bool_config("render_cards", True):
            return ActionResult(text)
        try:
            view = card_views.status_view(
                state, category, answer, max_attempts=self.max_attempts, hint_limit=self.games.hint_limit
            )
            card = await self._render_card(build_status_card_html(view))
        except Exception as exc:  # noqa: BLE001 - fall back to plain text
            logger.warning("MC 猜谜线索卡片构建失败，改用文本：%s", exc)
            return ActionResult(text)
        return self._with_card(text, card)

    async def _action_hint(self, event: AstrMessageEvent) -> ActionResult:
        catalogue = self._require_catalogue()
        try:
            updated, hint, _answer = await self.games.hint(self._session_id(event), catalogue)
        except ValueError as exc:
            return ActionResult(str(exc), error=True)
        used = len(updated.get("hints") or [])
        remaining = max(0, self.games.hint_limit - used)
        return ActionResult(f"💡 提示（{used}/{self.games.hint_limit}）：{hint['text']}\n还剩 {remaining} 次提示。")

    async def _action_give_up(self, event: AstrMessageEvent) -> ActionResult:
        catalogue = self._require_catalogue()
        try:
            state, answer = await self.games.give_up(self._session_id(event), catalogue)
        except ValueError as exc:
            return ActionResult(str(exc), error=True)
        category = catalogue.category(str(state.get("category")))
        guesses = len(state.get("guesses") or [])
        text = (
            f"🏳️ 本局结束。答案是：{answer['name']}"
            + (f"（{answer['name_en']}）" if answer.get("name_en") else "")
            + f"\n类别：{category.label} · 共猜 {guesses} 次"
        )
        return await self._status_card(text, state, category, answer)

    async def _action_rank(self, event: AstrMessageEvent) -> ActionResult:
        board = await self.games.leaderboard(self._session_id(event))
        players = board["players"]
        if players:
            lines = [f"🏆 本群猜谜排行（共 {board['rounds']} 局）"]
            for row in players:
                best = f"，最快 {row['best']} 次" if row["best"] else ""
                lines.append(f"{row['rank']}. {row['name']}：猜中 {row['wins']} 次{best}，参与 {row['rounds']} 局")
            text = "\n".join(lines)
        else:
            text = "本群还没有完成的对局，玩一局就能上榜。"
        if not self._bool_config("render_cards", True):
            return ActionResult(text)
        try:
            card = await self._render_card(build_rank_card_html({"title": "本群猜谜排行榜", **board}))
        except Exception as exc:  # noqa: BLE001 - fall back to plain text
            logger.warning("MC 猜谜排行卡片构建失败，改用文本：%s", exc)
            return ActionResult(text)
        return self._with_card(text, card)

    async def _action_help(self) -> ActionResult:
        commands = self._help_commands()
        if self._bool_config("render_cards", True):
            html = build_help_card_html(
                {
                    "title": "MC 猜谜",
                    "subtitle": "猜方块 / 物品 / 生物 · 按属性推理",
                    "commands": commands,
                    "footer": "🟩 完全一致　🟨 部分符合或接近　⬜ 不符　⬆⬇ 答案数值更大/更小",
                }
            )
            card = await self._render_card(html)
            if card is not None:
                return self._with_card(self._help_text(), card)
        return ActionResult(self._help_text())

    # ---- reply building -------------------------------------------------
    async def _guess_reply(
        self,
        catalogue: Catalogue,
        state: dict[str, Any],
        row: dict[str, Any],
        guess: dict[str, Any],
        answer: dict[str, Any],
    ) -> ActionResult:
        category = catalogue.category(str(state.get("category")))
        attempts = len(state.get("guesses") or [])
        remaining = max(0, self.max_attempts - attempts)
        if row["correct"]:
            status_text = f"🎉 猜中了！答案是：{answer['name']}"
        elif state.get("status") == LOST:
            status_text = f"💥 次数用尽。答案是：{answer['name']}"
        else:
            status_text = f"还剩 {remaining} 次 · 发送 `/mc猜 提示` 可获得提示"

        lines = [
            f"第 {row['attempt']}/{self.max_attempts} 次 · {category.label} · 你猜：{guess['name']}"
            + (f"（{guess['name_en']}）" if guess.get("name_en") else "")
        ]
        for item in row["feedback"]:
            arrow = core.DIRECTION_ARROW.get(item.get("direction"), "")
            line = f"{core.STATUS_EMOJI[item['status']]} {item['label']}：{item['value']}"
            if arrow:
                line += f" {arrow}"
            if item.get("hits") and item["status"] != core.EXACT:
                line += f"（命中：{'、'.join(item['hits'])}）"
            lines.append(line)
        lines.append(status_text)
        text = "\n".join(lines)

        if not self._bool_config("render_cards", True):
            return ActionResult(text)
        try:
            view = card_views.guess_view(
                state,
                category,
                row,
                guess,
                answer,
                max_attempts=self.max_attempts,
                hint_limit=self.games.hint_limit,
                status_text=status_text,
            )
            card = await self._render_card(build_guess_card_html(view))
        except Exception as exc:  # noqa: BLE001 - fall back to plain text
            logger.warning("MC 猜谜卡片构建失败，改用文本：%s", exc)
            return ActionResult(text)
        return self._with_card(text, card)

    def _ended_text(self, state: dict[str, Any], answer: dict[str, Any] | None) -> str:
        name = str((answer or {}).get("name") or "未知")
        english = str((answer or {}).get("name_en") or "")
        suffix = f"（{english}）" if english else ""
        status = str(state.get("status") or "")
        if status == WON:
            return f"本局已经猜中了，答案是：{name}{suffix}。发送 `/mc猜 开始` 新开一局。"
        if status == EXPIRED:
            return f"本局因闲置时间过长已结束，答案是：{name}{suffix}。发送 `/mc猜 开始` 新开一局。"
        if status == GAVE_UP:
            return f"本局已放弃，答案是：{name}{suffix}。发送 `/mc猜 开始` 新开一局。"
        return f"本局已经结束，答案是：{name}{suffix}。发送 `/mc猜 开始` 新开一局。"

    @staticmethod
    def _emit(event: AstrMessageEvent, result: ActionResult):
        if result.image:
            return event.chain_result([ImageComponent.fromBytes(result.image)])
        if result.image_url:
            return event.image_result(result.image_url)
        return event.plain_result(result.text)

    # ---- free text shortcut ---------------------------------------------
    @filter.event_message_type(EventMessageType.ALL)
    async def free_text_guess(self, event: AstrMessageEvent):
        """对局中直接发送词条名即视为一次猜测。"""
        if not self._bool_config("free_text_guess", True):
            return
        text = (event.message_str or "").strip()
        if not text or len(text) > FREE_TEXT_MAX_LENGTH:
            return
        if text.startswith(("/", "／")):
            return
        head = text.split(maxsplit=1)[0].casefold()
        if head in COMMAND_WORDS:
            return
        woken = bool(getattr(event, "is_at_or_wake_command", False))
        quiet = self._is_group(event) and not woken
        if quiet and not self._bool_config("group_guess_without_at", True):
            return  # 群聊里避免把普通聊天误当成猜测
        try:
            catalogue = self._require_catalogue()
        except CatalogError:
            return
        session_id = self._session_id(event)
        state = await self.games.current(session_id)
        if state is None or state.get("status") != PLAYING:
            return
        category = catalogue.category(str(state.get("category")))

        # Without @ only exact names count; shortcuts like 放弃 still need @ so chatter can't end a round.
        shortcut = None if quiet else SHORTCUT_WORDS.get(text)
        if shortcut == "status":
            yield self._emit(event, await self._action_status(event))
            return
        if shortcut == "hint":
            yield self._emit(event, await self._action_hint(event))
            return
        if shortcut == "giveup":
            yield self._emit(event, await self._action_give_up(event))
            return

        guess = category.match_exact(text)
        if guess is None:
            for prefix in QUIET_GUESS_PREFIXES if quiet else GUESS_PREFIXES:
                if text.startswith(prefix) and len(text) > len(prefix):
                    guess = category.match_exact(text[len(prefix) :].strip())
                    if guess is not None:
                        break
        if guess is None:
            return  # 不是词条名：交给 LLM / 其它插件处理
        try:
            updated, row, answer = await self.games.guess(
                session_id,
                guess,
                catalogue,
                player=self._player_name(event),
                player_id=self._player_id(event),
            )
        except ValueError as exc:
            if not quiet:  # 未 @ 时重复的名字多半是闲聊，静默忽略
                yield event.plain_result(str(exc))
            return
        if quiet:
            stop = getattr(event, "stop_event", None)
            if callable(stop):
                stop()  # 已作为猜测处理，不再交给其它插件
        yield self._emit(event, await self._guess_reply(catalogue, updated, row, guess, answer))

    # ---- LLM tool --------------------------------------------------------
    @_tool_schema(llm_tool.NAME, llm_tool.SCHEMA)
    @filter.llm_tool(name=llm_tool.NAME)
    @_mark_doc(llm_tool.DESCRIPTION)
    async def mc_guess_tool(self, event: AstrMessageEvent, **args):
        self._note_tool_use(self._session_id(event))
        try:
            request = llm_tool.parse_args(args)
        except llm_tool.InvalidArgs as exc:
            return llm_tool.invalid(str(exc))

        try:
            result = await self._dispatch_tool(event, request)
        except CatalogError as exc:
            return llm_tool.error(str(exc))
        if result.has_image():
            await event.send(self._image_chain(event, result))
            if self._bool_config("llm_tool_cards_only", True):
                return None  # 卡片就是回答：结束本轮，不再多一次 LLM 请求
        if result.error:
            return llm_tool.error(result.text)
        return llm_tool.result(result.text, card_sent=result.has_image())

    @staticmethod
    def _image_chain(event: AstrMessageEvent, result: ActionResult):
        if result.image:
            return event.chain_result([ImageComponent.fromBytes(result.image)])
        return event.image_result(result.image_url)

    async def _dispatch_tool(self, event: AstrMessageEvent, request: llm_tool.Request) -> ActionResult:
        if request.action == "help":
            return await self._action_help()
        if request.action == "start":
            if not request.category:
                if request.difficulty:
                    return ActionResult(
                        "请指定要猜的类型：方块 / 物品 / 生物。例如「开始猜方块」「换个物品局」。",
                        error=True,
                    )
                return await self._action_start(event, None, "")
            return await self._action_start(event, request.category, request.difficulty)
        if request.action == "guess":
            return await self._action_guess(event, request.name)
        if request.action == "status":
            return await self._action_status(event)
        if request.action == "hint":
            return await self._action_hint(event)
        if request.action == "giveup":
            return await self._action_give_up(event)
        if request.action == "rank":
            return await self._action_rank(event)
        return await self._action_help()

    @filter.on_llm_request()
    async def _offer_guess_tool(self, event: AstrMessageEvent, req) -> None:
        """Only offer the tool when relevant; playing hints ride a temporary part."""
        if not self._bool_config("llm_tool_enabled", True):
            if req.func_tool is not None:
                req.func_tool.remove_tool(llm_tool.NAME)
            return
        if req.func_tool is None:
            return
        offers, playing = await self._tool_visibility(event)
        if not offers:
            req.func_tool.remove_tool(llm_tool.NAME)
            return
        if not playing:
            return
        # Never touch req.system_prompt: mutating it breaks the cached prefix.
        part = _temp_text_part(PLAYING_SYSTEM_PROMPT)
        if part is None:
            return
        parts = getattr(req, "extra_user_content_parts", None)
        if parts is None:
            parts = []
            req.extra_user_content_parts = parts
        if not any(getattr(item, "text", None) == PLAYING_SYSTEM_PROMPT for item in parts):
            parts.append(part)

    async def _tool_visibility(self, event: AstrMessageEvent) -> tuple[bool, bool]:
        session_id = self._session_id(event)
        text = (event.message_str or "").strip()
        try:
            state = await self.games.current(session_id)
        except Exception:  # noqa: BLE001 - visibility must never break LLM requests
            state = None
        playing = bool(state and state.get("status") == PLAYING)
        last_used = self._last_tool_use.get(session_id)
        if last_used is not None and time.monotonic() - last_used < FOLLOW_UP_SECONDS:
            return True, playing
        if text and any(word in text for word in ("猜", "方块", "物品", "生物", "mc", "MC", "我的世界")):
            return True, playing
        if playing and 0 < len(text) <= FREE_TEXT_MAX_LENGTH:
            return True, playing
        return False, playing

    # ---- help ------------------------------------------------------------
    def _help_commands(self) -> list[dict[str, str]]:
        hint_limit = self.games.hint_limit
        return [
            {
                "command": "/mc猜 开始 方块 [简单|普通|困难]",
                "description": "开一局猜方块；物品、生物同理。也可用 /猜方块、/猜物品、/猜生物。",
                "tag": "",
            },
            {
                "command": "/mc猜 猜 <名字>",
                "description": "提交一次猜测；对局中也可以直接发名字，群里免 @。",
                "tag": "",
            },
            {
                "command": "/mc猜 提示",
                "description": f"免费提示，每局 {hint_limit} 次，揭示答案的一条属性。",
                "tag": "",
            },
            {
                "command": "/mc猜 状态",
                "description": "查看全部已知线索与猜测记录（汇总所有人的猜测）。",
                "tag": "",
            },
            {
                "command": "/mc猜 放弃",
                "description": "结束当前对局并公布答案。",
                "tag": "",
            },
            {
                "command": "/mc猜 排行",
                "description": "本群排行榜：猜中次数、最快猜中、参与局数。",
                "tag": "",
            },
            {
                "command": "/mc猜 帮助",
                "description": "显示这张说明卡片。",
                "tag": "",
            },
        ]

    def _help_text(self) -> str:
        return (
            "《MC 猜谜》玩法\n"
            "1. `/mc猜 开始 方块|物品|生物 [简单|普通|困难]` 开局，也可用 `/猜方块`、`/猜物品`、`/猜生物`。\n"
            f"2. 每局最多 {self.max_attempts} 次；每次猜一个名字，机器人按属性给反馈。\n"
            "3. 🟩 完全一致　🟨 部分符合/数值接近　⬜ 不符　⬆⬇ 答案更大/更小。\n"
            f"4. 每局有 {self.games.hint_limit} 次免费提示：`/mc猜 提示`。\n"
            "5. 对局中可直接发名字（群里可免 @，也可用「我猜X」）；`/mc猜 状态`、`/mc猜 放弃`、`/mc猜 排行`。\n"
            "名字支持中文名、英文名、ID 和常见别名。"
        )
