"""Tests for the persistent per-session round service."""

from __future__ import annotations

import asyncio
import json
import time

import pytest

from astrbot_plugin_mcguess.game_service import EXPIRED, GameService


def run(coro):
    return asyncio.run(coro)


def answer_entry(catalogue, state):
    return catalogue.entry(str(state["category"]), str(state["answerId"]))


def wrong_entry(catalogue, state):
    category = catalogue.category(str(state["category"]))
    for entry in category.entries:
        if entry["id"] != state["answerId"]:
            return entry
    raise AssertionError("catalogue needs at least two entries")


def test_start_creates_a_persisted_round(tmp_dir, catalogue):
    async def scenario():
        service = GameService(tmp_dir)
        state = await service.start("s-1", "block", "easy", catalogue)
        assert state["status"] == "playing"
        assert state["category"] == "block"
        assert state["guesses"] == [] and state["hints"] == []
        pool = {entry["id"] for entry in catalogue.category("block").answer_pool("easy")}
        assert state["answerId"] in pool

        other = GameService(tmp_dir)
        loaded = await other.current("s-1")
        assert loaded is not None and loaded["answerId"] == state["answerId"]

    run(scenario())


def test_start_respects_require_finish(tmp_dir, catalogue):
    async def scenario():
        service = GameService(tmp_dir)
        await service.start("s-1", "block", "easy", catalogue, require_finish=True)
        with pytest.raises(ValueError, match="还没结束"):
            await service.start("s-1", "block", "easy", catalogue, require_finish=True)
        replaced = await service.start("s-1", "item", "easy", catalogue)
        assert replaced["category"] == "item"

    run(scenario())


def test_guess_wins_and_reveals_answer(tmp_dir, catalogue):
    async def scenario():
        service = GameService(tmp_dir)
        state = await service.start("s-1", "block", "easy", catalogue)
        answer = answer_entry(catalogue, state)
        updated, row, returned = await service.guess("s-1", answer, catalogue)
        assert row["correct"] is True
        assert updated["status"] == "won"
        assert returned["id"] == answer["id"]

        current = await service.current("s-1")
        assert current["status"] == "won"

    run(scenario())


def test_guess_loses_after_max_attempts(tmp_dir, catalogue):
    async def scenario():
        service = GameService(tmp_dir, max_attempts=1)
        state = await service.start("s-1", "block", "easy", catalogue)
        updated, row, _answer = await service.guess("s-1", wrong_entry(catalogue, state), catalogue)
        assert row["correct"] is False
        assert updated["status"] == "lost"
        with pytest.raises(ValueError, match="已经结束"):
            await service.guess("s-1", answer_entry(catalogue, state), catalogue)

    run(scenario())


def test_guess_rejects_duplicates_and_missing_round(tmp_dir, catalogue):
    async def scenario():
        service = GameService(tmp_dir)
        with pytest.raises(ValueError, match="还没有对局"):
            await service.guess("nobody", catalogue.entry("block", "stone"), catalogue)

        state = await service.start("s-1", "block", "easy", catalogue)
        entry = wrong_entry(catalogue, state)
        await service.guess("s-1", entry, catalogue)
        with pytest.raises(ValueError, match="已经猜过"):
            await service.guess("s-1", entry, catalogue)

    run(scenario())


def test_hint_walks_the_configured_plan(tmp_dir, catalogue):
    async def scenario():
        service = GameService(tmp_dir, hint_limit=3)
        await service.start("s-1", "block", "easy", catalogue)
        updated, hint, _answer = await service.hint("s-1", catalogue)
        assert hint["field"] == "tool" and "工具" in hint["text"]
        assert len(updated["hints"]) == 1

        updated, hint, _answer = await service.hint("s-1", catalogue)
        assert hint["field"] == "hardness"
        updated, hint, _answer = await service.hint("s-1", catalogue)
        assert hint["field"] == "first_char"

        with pytest.raises(ValueError, match="提示次数已经用完"):
            await service.hint("s-1", catalogue)

    run(scenario())


def test_hint_can_be_disabled(tmp_dir, catalogue):
    async def scenario():
        service = GameService(tmp_dir, hint_limit=0)
        await service.start("s-1", "block", "easy", catalogue)
        with pytest.raises(ValueError, match="提示次数已经用完"):
            await service.hint("s-1", catalogue)

    run(scenario())


def test_give_up_ends_the_round(tmp_dir, catalogue):
    async def scenario():
        service = GameService(tmp_dir)
        await service.start("s-1", "mob", "easy", catalogue)
        state, answer = await service.give_up("s-1", catalogue)
        assert state["status"] == "gaveup"
        assert answer["id"] == state["answerId"]
        again, _ = await service.give_up("s-1", catalogue)
        assert again["status"] == "gaveup"

    run(scenario())


def test_idle_round_expires(tmp_dir, catalogue):
    async def scenario():
        service = GameService(tmp_dir, idle_timeout_minutes=1)
        state = await service.start("s-1", "block", "easy", catalogue)
        path = service._path("s-1")
        stored = json.loads(path.read_text(encoding="utf-8"))
        stored["updatedAt"] = time.time() - 120
        path.write_text(json.dumps(stored), encoding="utf-8")

        current = await service.current("s-1")
        assert current["status"] == EXPIRED
        with pytest.raises(ValueError, match="闲置"):
            await service.guess("s-1", answer_entry(catalogue, state), catalogue)
        assert state["answerId"]

        restarted = await service.start("s-1", "block", "easy", catalogue, require_finish=True)
        assert restarted["status"] == "playing"

    run(scenario())


def test_reset_drops_the_saved_round(tmp_dir, catalogue):
    async def scenario():
        service = GameService(tmp_dir)
        await service.start("s-1", "block", "easy", catalogue)
        await service.reset("s-1")
        assert await service.current("s-1") is None

    run(scenario())
