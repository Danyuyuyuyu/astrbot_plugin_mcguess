"""Persistent, session-isolated MC guessing rounds."""

from __future__ import annotations

import asyncio
import hashlib
import json
import random
import time
from pathlib import Path
from typing import Any

from . import core
from .catalog import Catalogue, Category

PLAYING = "playing"
WON = "won"
LOST = "lost"
GAVE_UP = "gaveup"
EXPIRED = "expired"

ENDED_STATUSES = {WON, LOST, GAVE_UP, EXPIRED}


class GameService:
    """Keep one independent saved round per AstrBot session id."""

    def __init__(
        self,
        data_dir: Path,
        *,
        max_attempts: int = 25,
        hint_limit: int = 3,
        idle_timeout_minutes: int = 30,
    ) -> None:
        self.games_dir = Path(data_dir) / "games"
        self.games_dir.mkdir(parents=True, exist_ok=True)
        self.max_attempts = max(1, int(max_attempts))
        self.hint_limit = max(0, int(hint_limit))
        self.idle_timeout_minutes = max(0, int(idle_timeout_minutes))
        self._lock = asyncio.Lock()

    # -- storage --------------------------------------------------------
    @staticmethod
    def _safe_session(session_id: str) -> str:
        return hashlib.sha256(str(session_id).encode("utf-8")).hexdigest()

    def _path(self, session_id: str) -> Path:
        return self.games_dir / f"{self._safe_session(session_id)}.json"

    def _read(self, session_id: str) -> dict[str, Any] | None:
        path = self._path(session_id)
        if not path.exists():
            return None
        try:
            state = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        return state if isinstance(state, dict) else None

    def _write(self, session_id: str, state: dict[str, Any]) -> None:
        path = self._path(session_id)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
        temporary.replace(path)

    # -- helpers --------------------------------------------------------
    def _is_expired(self, state: dict[str, Any], now: float) -> bool:
        if self.idle_timeout_minutes <= 0 or state.get("status") != PLAYING:
            return False
        updated_at = float(state.get("updatedAt") or state.get("createdAt") or 0)
        if updated_at <= 0:
            return False
        return now - updated_at >= self.idle_timeout_minutes * 60

    @staticmethod
    def answer_of(state: dict[str, Any], catalogue: Catalogue) -> dict[str, Any] | None:
        return catalogue.entry(str(state.get("category")), str(state.get("answerId")))

    def _ended_message(self, state: dict[str, Any], answer: dict[str, Any] | None) -> str:
        status = str(state.get("status") or "")
        name = str((answer or {}).get("name") or "未知")
        if status == WON:
            return f"本局已经猜中了（答案是 {name}），发送 `/mc猜 开始` 新开一局。"
        if status == EXPIRED:
            return f"本局因闲置时间过长已结束（答案是 {name}），发送 `/mc猜 开始` 新开一局。"
        return f"本局已经结束（答案是 {name}），发送 `/mc猜 开始` 新开一局。"

    # -- public API -----------------------------------------------------
    async def start(
        self,
        session_id: str,
        category_key: str,
        difficulty: str,
        catalogue: Catalogue,
        *,
        require_finish: bool = False,
    ) -> dict[str, Any]:
        category = catalogue.category(category_key)
        pool = category.answer_pool(difficulty)
        if not pool:
            raise ValueError(f"{category.label}题库在「{difficulty}」难度下没有可用答案。")
        now = time.time()
        async with self._lock:
            current = self._read(session_id)
            if current is not None and self._is_expired(current, now):
                current = {**current, "status": EXPIRED, "updatedAt": now}
                self._write(session_id, current)
            if require_finish and current and current.get("status") == PLAYING:
                raise ValueError("当前对局还没结束，请先猜中、用完次数或发送 `/mc猜 放弃`。")
            answer = random.choice(pool)
            state = {
                "category": category.key,
                "difficulty": str(difficulty),
                "answerId": str(answer["id"]),
                "guesses": [],
                "hints": [],
                "status": PLAYING,
                "createdAt": now,
                "updatedAt": now,
            }
            self._write(session_id, state)
            return state

    async def current(self, session_id: str) -> dict[str, Any] | None:
        now = time.time()
        async with self._lock:
            state = self._read(session_id)
            if state is None:
                return None
            if self._is_expired(state, now):
                state = {**state, "status": EXPIRED, "updatedAt": now}
                self._write(session_id, state)
            return state

    async def guess(
        self,
        session_id: str,
        guess_entry: dict[str, Any],
        catalogue: Catalogue,
    ) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
        now = time.time()
        async with self._lock:
            state = self._read(session_id)
            if state is None:
                raise ValueError("本会话还没有对局，请先发送 `/mc猜 开始 方块`（或物品/生物）。")
            if self._is_expired(state, now):
                state = {**state, "status": EXPIRED, "updatedAt": now}
                self._write(session_id, state)
            category: Category = catalogue.category(str(state.get("category")))
            answer = category.entry(str(state.get("answerId")))
            if answer is None:
                raise ValueError("本局答案已失效，请发送 `/mc猜 开始` 新开一局。")
            if state.get("status") != PLAYING:
                raise ValueError(self._ended_message(state, answer))
            updated, row = core.apply_guess(
                state,
                guess_entry,
                answer,
                category.fields,
                max_attempts=self.max_attempts,
            )
            updated["updatedAt"] = now
            self._write(session_id, updated)
            return updated, row, answer

    async def hint(
        self,
        session_id: str,
        catalogue: Catalogue,
    ) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
        now = time.time()
        async with self._lock:
            state = self._read(session_id)
            if state is None:
                raise ValueError("本会话还没有对局，请先发送 `/mc猜 开始 方块`（或物品/生物）。")
            if self._is_expired(state, now):
                state = {**state, "status": EXPIRED, "updatedAt": now}
                self._write(session_id, state)
            category: Category = catalogue.category(str(state.get("category")))
            answer = category.entry(str(state.get("answerId")))
            if answer is None:
                raise ValueError("本局答案已失效，请发送 `/mc猜 开始` 新开一局。")
            if state.get("status") != PLAYING:
                raise ValueError(self._ended_message(state, answer))
            hints = list(state.get("hints") or [])
            hint = core.build_hint(
                answer,
                category.fields,
                category.hint_fields,
                [str(item.get("field")) for item in hints],
                limit=self.hint_limit,
            )
            hints.append(hint)
            updated = {**state, "hints": hints, "updatedAt": now}
            self._write(session_id, updated)
            return updated, hint, answer

    async def give_up(
        self,
        session_id: str,
        catalogue: Catalogue,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        now = time.time()
        async with self._lock:
            state = self._read(session_id)
            if state is None:
                raise ValueError("本会话还没有对局，请先发送 `/mc猜 开始 方块`（或物品/生物）。")
            answer = self.answer_of(state, catalogue)
            if answer is None:
                raise ValueError("本局答案已失效，请发送 `/mc猜 开始` 新开一局。")
            if state.get("status") == PLAYING:
                state = {**state, "status": GAVE_UP, "updatedAt": now}
                self._write(session_id, state)
            return state, answer

    async def reset(self, session_id: str) -> None:
        """Forget the saved round for a session (used by tests and admin tooling)."""
        async with self._lock:
            path = self._path(session_id)
            if path.exists():
                path.unlink()
