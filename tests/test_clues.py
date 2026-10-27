"""Clue-board aggregation (core.summarize_clues) and round -> card views."""

from __future__ import annotations

from pathlib import Path

import pytest

from astrbot_plugin_mcguess import card_views, core
from astrbot_plugin_mcguess.catalog import Catalogue
from astrbot_plugin_mcguess.game_service import GameService

CATALOG = Path(__file__).resolve().parent.parent / "data" / "catalog.json"


@pytest.fixture(scope="module")
def real():
    return Catalogue.load(CATALOG)


def _clues(category, answer, guesses, hints=None):
    entries = [category.match_exact(name) for name in guesses]
    assert all(entries), guesses
    return {c["key"]: c for c in core.summarize_clues(category.fields, entries, category.match_exact(answer), hints)}


def test_number_bounds_use_direction_and_tolerance(real):
    block = real.category("block")
    clues = _clues(block, "铁块", ["金块", "黑曜石"])  # hardness 3 (miss, up) and 50 (miss, down); tolerance 0.5
    assert clues["hardness"] == {"key": "hardness", "label": "硬度", "state": "partial", "text": "3.5 < ? < 49.5"}
    assert clues["blast_resistance"]["state"] == "known"


def test_no_guesses_means_no_clues(real):
    block = real.category("block")
    clues = _clues(block, "铁块", [])
    assert {c["state"] for c in clues.values()} == {"none"}


def test_enum_deduction_by_elimination():
    field = {
        "key": "renewable",
        "kind": "enum",
        "values": {"no": "否", "partial": "部分", "yes": "是"},
        "similar": [["no", "partial"], ["yes", "partial"]],
    }
    # guessing "yes" and missing rules out yes and its similar value "partial"
    clue = core.summarize_clues([field], [{"attrs": {"renewable": "yes"}}], {"attrs": {"renewable": "no"}})[0]
    assert clue["state"] == "known" and clue["text"] == "= 否（推断）"


def test_set_clue_collects_included_and_excluded():
    field = {"key": "spawn", "label": "生成", "kind": "set", "values": {"a": "甲", "b": "乙", "c": "丙"}}
    clue = core.summarize_clues([field], [{"attrs": {"spawn": ["a", "b"]}}], {"attrs": {"spawn": ["a", "c"]}})[0]
    assert clue["state"] == "partial" and clue["text"] == "含 甲；不含 乙"


def test_version_clue_bounds_and_inference():
    order = {"v1": 1, "v2": 2, "v3": 3}
    field = {"key": "initial_release", "kind": "version", "order": order}
    answer = {"attrs": {"initial_release": "v2"}}
    one = core.summarize_clues([field], [{"attrs": {"initial_release": "v1"}}], answer)[0]
    assert one["text"] == "可能 v2 / v3"
    both = core.summarize_clues(
        [field], [{"attrs": {"initial_release": "v1"}}, {"attrs": {"initial_release": "v3"}}], answer
    )[0]
    assert both["state"] == "known" and both["text"] == "= v2（推断）"


def test_hinted_field_is_known_without_leaking_others(real):
    block = real.category("block")
    clues = _clues(block, "铁块", [], hints=[{"field": "tool"}])
    assert clues["tool"]["state"] == "known" and "提示" in clues["tool"]["text"]
    assert clues["hardness"]["state"] == "none"


def test_guess_view_and_status_view_from_saved_round(real, tmp_dir):
    block = real.category("block")
    answer = block.match_exact("铁块")
    state = {"category": "block", "difficulty": "normal", "guesses": [], "players": [], "hints": [], "status": "playing"}
    row = None
    for name, player in (("金块", "Steve"), ("玻璃", "")):
        state, row = core.apply_guess(state, block.match_exact(name), answer, block.fields)
        state["players"] = [*state["players"], player]
        row["player"] = player
    view = card_views.guess_view(state, block, row, block.match_exact("玻璃"), answer, max_attempts=25, hint_limit=3)
    assert [r["name"] for r in view["history"]] == ["玻璃", "金块"]
    assert view["history"][1]["player"] == "Steve"
    assert view["participants"] == 1 and view["answer_name"] == ""  # never reveal while playing
    status = card_views.status_view(state, block, answer, max_attempts=25, hint_limit=3)
    assert status["kind"] == "status" and len(status["clues"]) == len(block.fields)




def test_game_service_records_players_and_avoids_recent_answers(real, tmp_dir):
    import asyncio

    service = GameService(tmp_dir, max_attempts=25)

    async def run():
        seen = []
        for _ in range(8):
            state = await service.start("s", "block", "normal", real)
            seen.append(state["answerId"])
        guess = real.category("block").match_exact("金块")
        if seen[-1] == guess["id"]:
            guess = real.category("block").match_exact("玻璃")
        state, row, _ = await service.guess("s", guess, real, player="Steve")
        return seen, state, row

    seen, state, row = asyncio.run(run())
    assert len(set(seen)) == len(seen)  # 227-entry pool: no repeats within 8 rounds
    assert state["players"] == ["Steve"] and row["player"] == "Steve"
    assert len(state["recentAnswers"]) == 8


def test_version_family_and_close():
    assert core.version_family("1.16.2") == "1.16" == core.version_family("1.16")
    assert core.version_family("Alpha 1.0.14") == "Alpha 1.0"
    assert core.version_family("pre-alpha") == "pre-alpha"
    field = {"key": "v", "kind": "version", "order": {"1.15": 1, "1.16": 2, "1.16.2": 3, "1.17": 4}}
    close = core.compare_field(field, "1.16", "1.16.2")
    assert close["status"] == "close" and close["direction"] == "up"
    assert core.compare_field(field, "1.15", "1.16.2")["status"] == "miss"
    assert core.compare_field({**field, "family_close": False}, "1.16", "1.16.2")["status"] == "miss"


def test_version_clue_uses_family(real):
    order = {f"1.{i}": i for i in range(10, 22)} | {"1.21.2": 22, "1.21.4": 23, "1.21.5": 24, "1.21.9": 25}
    field = {"key": "v", "kind": "version", "order": order}
    answer = {"attrs": {"v": "1.21.5"}}
    clue = core.summarize_clues([field], [{"attrs": {"v": "1.21"}}], answer)[0]
    assert clue == {"key": "v", "label": "v", "state": "partial", "text": "1.21.x 系列"}
    guesses = [{"attrs": {"v": "1.20"}}, {"attrs": {"v": "1.21.9"}}, {"attrs": {"v": "1.21.2"}}]
    clue = core.summarize_clues([field], guesses, answer)[0]
    assert clue["text"] == "可能 1.21.4 / 1.21.5"
