"""Shared fixtures for the plugin test-suite."""

from __future__ import annotations

import shutil
import sys
import uuid
from pathlib import Path

import pytest

PLUGIN_PARENT = Path(__file__).resolve().parents[2]
if str(PLUGIN_PARENT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_PARENT))

from astrbot_plugin_mcguess.catalog import Catalogue  # noqa: E402


@pytest.fixture()
def tmp_dir():
    """Workspace-local temp directory.

    ``tmp_path`` is unusable in the Windows sandbox used for local development
    because pytest creates its temp dirs with mode ``0o700``, which drops the
    sandbox ACE. Plain ``mkdir`` keeps the inherited ACL.
    """
    base = Path(__file__).resolve().parent / "_tmp"
    base.mkdir(parents=True, exist_ok=True)
    path = base / uuid.uuid4().hex
    path.mkdir()
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)

SAMPLE_PAYLOAD = {
    "schema_version": 1,
    "game_version": "1.21.4",
    "release_order": {"pre-alpha": 0, "Alpha 1.0.1": 1, "1.16": 5, "1.21": 9},
    "categories": {
        "block": {
            "label": "方块",
            "icon": "🧱",
            "fields": [
                {"key": "hardness", "label": "硬度", "kind": "number", "tolerance": 0.5},
                {
                    "key": "tool",
                    "label": "工具",
                    "kind": "enum",
                    "values": {"pickaxe": "镐", "axe": "斧头", "none": "无"},
                },
                {
                    "key": "transparent",
                    "label": "透明",
                    "kind": "enum",
                    "values": {"yes": "是", "no": "否", "partial": "部分"},
                    "similar": [["yes", "partial"], ["no", "partial"]],
                },
                {"key": "stackable", "label": "堆叠", "kind": "number"},
                {"key": "initial_release", "label": "初始版本", "kind": "version"},
            ],
            "hint_fields": ["tool", "hardness"],
            "entries": [
                {
                    "id": "diamond_ore",
                    "name": "钻石矿石",
                    "name_en": "Diamond Ore",
                    "tier": 1,
                    "aliases": ["钻石矿"],
                    "attrs": {
                        "hardness": 3,
                        "tool": "pickaxe",
                        "transparent": "no",
                        "stackable": 64,
                        "initial_release": "1.16",
                    },
                },
                {
                    "id": "stone",
                    "name": "石头",
                    "name_en": "Stone",
                    "tier": 1,
                    "attrs": {
                        "hardness": 1.5,
                        "tool": "pickaxe",
                        "transparent": "no",
                        "stackable": 64,
                        "initial_release": "pre-alpha",
                    },
                },
                {
                    "id": "glass",
                    "name": "玻璃",
                    "name_en": "Glass",
                    "tier": 1,
                    "attrs": {
                        "hardness": 0.3,
                        "tool": "none",
                        "transparent": "partial",
                        "stackable": 64,
                        "initial_release": "pre-alpha",
                    },
                },
                {
                    "id": "oak_log",
                    "name": "原木",
                    "name_en": "Oak Log",
                    "tier": 2,
                    "aliases": ["木头"],
                    "attrs": {
                        "hardness": 2,
                        "tool": "axe",
                        "transparent": "no",
                        "stackable": 64,
                        "initial_release": "Alpha 1.0.1",
                    },
                },
                {
                    "id": "bedrock",
                    "name": "基岩",
                    "name_en": "Bedrock",
                    "tier": 3,
                    "answerable": False,
                    "attrs": {
                        "hardness": 50,
                        "tool": "none",
                        "transparent": "no",
                        "stackable": 64,
                        "initial_release": "pre-alpha",
                    },
                },
                {
                    "id": "ancient_debris",
                    "name": "远古残骸",
                    "name_en": "Ancient Debris",
                    "tier": 3,
                    "attrs": {
                        "hardness": 30,
                        "tool": "pickaxe",
                        "transparent": "no",
                        "stackable": 64,
                        "initial_release": "1.16",
                    },
                },
                {
                    "id": "diamond_block",
                    "name": "钻石块",
                    "name_en": "Diamond Block",
                    "tier": 2,
                    "attrs": {
                        "hardness": 5,
                        "tool": "pickaxe",
                        "transparent": "no",
                        "stackable": 64,
                        "initial_release": "Alpha 1.0.1",
                    },
                },
            ],
        },
        "item": {
            "label": "物品",
            "icon": "🎒",
            "fields": [
                {
                    "key": "rarity",
                    "label": "稀有度",
                    "kind": "enum",
                    "values": {"common": "普通", "rare": "稀有"},
                },
                {
                    "key": "obtaining",
                    "label": "获得方式",
                    "kind": "set",
                    "values": {"Crafting": "合成", "Mining": "采矿", "Mob loot": "怪物掉落"},
                },
            ],
            "hint_fields": ["rarity"],
            "entries": [
                {
                    "id": "diamond",
                    "name": "钻石",
                    "name_en": "Diamond",
                    "tier": 1,
                    "attrs": {"rarity": "common", "obtaining": ["Mining", "Crafting"]},
                },
                {
                    "id": "sword",
                    "name": "剑",
                    "name_en": "Sword",
                    "tier": 1,
                    "attrs": {"rarity": "common", "obtaining": ["Crafting"]},
                },
                {
                    "id": "shield",
                    "name": "盾牌",
                    "name_en": "Shield",
                    "tier": 2,
                    "attrs": {"rarity": "common", "obtaining": ["Crafting"]},
                },
                {
                    "id": "nether_star",
                    "name": "下界之星",
                    "name_en": "Nether Star",
                    "tier": 3,
                    "attrs": {"rarity": "rare", "obtaining": ["Mob loot"]},
                },
            ],
        },
        "mob": {
            "label": "生物",
            "icon": "🐷",
            "fields": [
                {"key": "health", "label": "生命值", "kind": "number", "tolerance": 2},
                {
                    "key": "behavior",
                    "label": "行为",
                    "kind": "set",
                    "values": {"passive": "被动", "neutral": "中立", "hostile": "敌对"},
                },
            ],
            "hint_fields": ["behavior"],
            "entries": [
                {
                    "id": "zombie",
                    "name": "僵尸",
                    "name_en": "Zombie",
                    "tier": 1,
                    "attrs": {"health": 20, "behavior": ["hostile"]},
                },
                {
                    "id": "creeper",
                    "name": "苦力怕",
                    "name_en": "Creeper",
                    "tier": 1,
                    "aliases": ["爬行者"],
                    "attrs": {"health": 20, "behavior": ["hostile"]},
                },
                {
                    "id": "pig",
                    "name": "猪",
                    "name_en": "Pig",
                    "tier": 1,
                    "attrs": {"health": 10, "behavior": ["passive"]},
                },
            ],
        },
    },
}


@pytest.fixture()
def payload() -> dict:
    import copy

    return copy.deepcopy(SAMPLE_PAYLOAD)


@pytest.fixture()
def catalogue(payload) -> Catalogue:
    return Catalogue.from_payload(payload)


@pytest.fixture()
def block_fields(catalogue):
    return catalogue.category("block").fields
