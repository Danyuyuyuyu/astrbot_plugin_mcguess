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

RECENT_ANSWERS_KEEP = 20
PLAYER_NAME_MAX = 24
LEADERBOARD_SIZE = 10


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
        self.stats_dir = Path(data_dir) / "stats"
        self.stats_dir.mkdir(parents=True, exist_ok=True)
        self.max_attempts = max(1, int(max_attempts))
        self.hint_limit = max(0, int(hint_limit))
        self.idle_timeout_minutes = max(0, int(idle_timeout_minutes))
        # One lock per chat session: groups never wait on each other.
        self._locks: dict[str, asyncio.Lock] = {}

    # -- storage --------------------------------------------------------
    @staticmethod
    def _safe_session(session_id: str) -> str:
        return hashlib.sha256(str(session_id).encode("utf-8")).hexdigest()

    def _path(self, session_id: str) -> Path:
        return self.games_dir / f"{self._safe_session(session_id)}.json"

    def _stats_path(self, session_id: str) -> Path:
        return self.stats_dir / f"{self._safe_session(session_id)}.json"

    def _session_lock(self, session_id: str) -> asyncio.Lock:
        lock = self._locks.get(session_id)
        if lock is None:
            lock = self._locks[session_id] = asyncio.Lock()
        return lock

    @staticmethod
    def _load_json(path: Path) -> dict[str, Any] | None:
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        return data if isinstance(data, dict) else None

    @staticmethod
    def _dump_json(path: Path, data: dict[str, Any]) -> None:
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        temporary.replace(path)

    async def _read(self, session_id: str) -> dict[str, Any] | None:
        # File I/O runs in a worker thread so a slow disk never blocks the event loop.
        return await asyncio.to_thread(self._load_json, self._path(session_id))

    async def _write(self, session_id: str, state: dict[str, Any]) -> None:
        await asyncio.to_thread(self._dump_json, self._path(session_id), state)

    # -- leaderboard ----------------------------------------------------
    async def _record_round(self, session_id: str, state: dict[str, Any]) -> None:
        """Fold one finished round into the session leaderboard (called once per round)."""
        names = [str(item) for item in (state.get("players") or [])]
        ids = [str(item) for item in (state.get("playerIds") or [])]
        keys = [(ids[i] if i < len(ids) and ids[i] else f"name:{name}") for i, name in enumerate(names)]
        if not any(name for name in names):
            return
        path = self._stats_path(session_id)
        stats = await asyncio.to_thread(self._load_json, path) or {}
        players: dict[str, Any] = stats.get("players") if isinstance(stats.get("players"), dict) else {}
        for key, name in zip(keys, names):
            if not name:
                continue
            record = players.setdefault(key, {"name": name, "wins": 0, "rounds": 0, "guesses": 0, "best": 0})
            record["name"] = name
            record["guesses"] = int(record.get("guesses") or 0) + 1
        for key in dict.fromkeys(k for k, n in zip(keys, names) if n):
            players[key]["rounds"] = int(players[key].get("rounds") or 0) + 1
        if state.get("status") == WON and names and names[-1]:
            winner = players[keys[-1]]
            attempt = len(state.get("guesses") or [])
            winner["wins"] = int(winner.get("wins") or 0) + 1
            best = int(winner.get("best") or 0)
            winner["best"] = attempt if best <= 0 else min(best, attempt)
            winner["lastWin"] = time.time()
        stats["players"] = players
        stats["rounds"] = int(stats.get("rounds") or 0) + 1
        await asyncio.to_thread(self._dump_json, path, stats)

    async def leaderboard(self, session_id: str, limit: int = LEADERBOARD_SIZE) -> dict[str, Any]:
        """Ranked players of one chat: wins, then best (fewest) attempts, then rounds played."""
        async with self._session_lock(session_id):
            stats = await asyncio.to_thread(self._load_json, self._stats_path(session_id)) or {}
        players = stats.get("players") if isinstance(stats.get("players"), dict) else {}
        rows = []
        for record in players.values():
            if not isinstance(record, dict) or not record.get("name"):
                continue
            rows.append(
                {
                    "name": str(record["name"]),
                    "wins": int(record.get("wins") or 0),
                    "rounds": int(record.get("rounds") or 0),
                    "guesses": int(record.get("guesses") or 0),
                    "best": int(record.get("best") or 0),
                }
            )
        rows.sort(key=lambda r: (-r["wins"], r["best"] if r["best"] > 0 else 10**6, -r["rounds"], r["name"]))
        for index, row in enumerate(rows):
            row["rank"] = index + 1
        return {"rounds": int(stats.get("rounds") or 0), "players": rows[: max(1, limit)], "total": len(rows)}

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
        async with self._session_lock(session_id):
            current = await self._read(session_id)
            if current is not None and self._is_expired(current, now):
                current = {**current, "status": EXPIRED, "updatedAt": now}
                await self._write(session_id, current)
            if require_finish and current and current.get("status") == PLAYING:
                raise ValueError("当前对局还没结束，请先猜中、用完次数或发送 `/mc猜 放弃`。")
            recent = [str(item) for item in ((current or {}).get("recentAnswers") or [])]
            fresh = [entry for entry in pool if str(entry["id"]) not in recent]
            # Avoid repeating recent answers in the same chat, but never empty the pool.
            answer = random.choice(fresh if len(fresh) >= max(1, len(pool) // 4) else pool)
            recent = [*recent, str(answer["id"])][-min(RECENT_ANSWERS_KEEP, max(0, len(pool) - 1)) :]
            state = {
                "category": category.key,
                "difficulty": str(difficulty),
                "answerId": str(answer["id"]),
                "guesses": [],
                "players": [],
                "playerIds": [],
                "hints": [],
                "recentAnswers": recent if len(pool) > 1 else [],
                "status": PLAYING,
                "createdAt": now,
                "updatedAt": now,
            }
            await self._write(session_id, state)
            return state

    async def current(self, session_id: str) -> dict[str, Any] | None:
        now = time.time()
        async with self._session_lock(session_id):
            state = await self._read(session_id)
            if state is None:
                return None
            if self._is_expired(state, now):
                state = {**state, "status": EXPIRED, "updatedAt": now}
                await self._write(session_id, state)
            return state

    async def guess(
        self,
        session_id: str,
        guess_entry: dict[str, Any],
        catalogue: Catalogue,
        player: str = "",
        player_id: str = "",
    ) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
        now = time.time()
        async with self._session_lock(session_id):
            state = await self._read(session_id)
            if state is None:
                raise ValueError("本会话还没有对局，请先发送 `/mc猜 开始 方块`（或物品/生物）。")
            if self._is_expired(state, now):
                state = {**state, "status": EXPIRED, "updatedAt": now}
                await self._write(session_id, state)
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
            players = [str(item) for item in (state.get("players") or [])]
            # Older saves have no player list: pad so indexes keep lining up with guesses.
            players += [""] * (len(state.get("guesses") or []) - len(players))
            players.append(str(player or "").strip()[:PLAYER_NAME_MAX])
            player_ids = [str(item) for item in (state.get("playerIds") or [])]
            player_ids += [""] * (len(players) - 1 - len(player_ids))
            player_ids.append(str(player_id or "").strip())
            updated["players"] = players
            updated["playerIds"] = player_ids
            row["player"] = players[-1]
            updated["updatedAt"] = now
            await self._write(session_id, updated)
            if updated.get("status") in (WON, LOST):
                await self._record_round(session_id, updated)
            return updated, row, answer

    async def hint(
        self,
        session_id: str,
        catalogue: Catalogue,
    ) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
        now = time.time()
        async with self._session_lock(session_id):
            state = await self._read(session_id)
            if state is None:
                raise ValueError("本会话还没有对局，请先发送 `/mc猜 开始 方块`（或物品/生物）。")
            if self._is_expired(state, now):
                state = {**state, "status": EXPIRED, "updatedAt": now}
                await self._write(session_id, state)
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
            await self._write(session_id, updated)
            return updated, hint, answer

    async def give_up(
        self,
        session_id: str,
        catalogue: Catalogue,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        now = time.time()
        async with self._session_lock(session_id):
            state = await self._read(session_id)
            if state is None:
                raise ValueError("本会话还没有对局，请先发送 `/mc猜 开始 方块`（或物品/生物）。")
            answer = self.answer_of(state, catalogue)
            if answer is None:
                raise ValueError("本局答案已失效，请发送 `/mc猜 开始` 新开一局。")
            if state.get("status") == PLAYING:
                state = {**state, "status": GAVE_UP, "updatedAt": now}
                await self._write(session_id, state)
                await self._record_round(session_id, state)
            return state, answer

    async def reset(self, session_id: str) -> None:
        """Forget the saved round for a session (used by tests and admin tooling)."""
        async with self._session_lock(session_id):
            path = self._path(session_id)
            if path.exists():
                path.unlink()
