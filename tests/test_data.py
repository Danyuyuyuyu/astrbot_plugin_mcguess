"""``data/catalog.json`` 的结构与内容校验。

这些用例只读仓库内的文件，不联网、不使用临时目录（Windows 沙箱下 ``tmp_path`` 会丢 ACE），
确定性测试直接调用 ``tools/build_catalog.py`` 的纯函数与已提交的题库逐字节比较。
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

PLUGIN_DIR = Path(__file__).resolve().parents[1]
CATALOG_PATH = PLUGIN_DIR / "data" / "catalog.json"
TOOLS_DIR = PLUGIN_DIR / "tools"
BUILD_SCRIPT = TOOLS_DIR / "build_catalog.py"
RAW_DIR = TOOLS_DIR / "mcdle_raw"

CATEGORY_ORDER = ["block", "item", "mob"]
FIELD_ORDER = {
    "block": [
        "hardness",
        "blast_resistance",
        "tool",
        "luminous",
        "transparent",
        "full_block",
        "flammable",
        "fire_catch",
        "stackable",
        "renewable",
        "initial_release",
    ],
    "item": [
        "rarity",
        "stackable",
        "renewable",
        "obtaining",
        "usage",
        "inventory_categories",
        "recipe",
        "loot",
        "initial_release",
    ],
    "mob": ["health", "height", "behavior", "spawn", "classification", "initial_release"],
}
ALLOWED_KINDS = {"number", "enum", "set", "version"}
TIER1_MIN = {"block": 60, "item": 50, "mob": 35}
EXPECTED_NUMBERS = {
    "block": {"hardness": 0.5, "blast_resistance": 0.25, "stackable": 0},
    "item": {"stackable": 0},
    "mob": {"health": 2, "height": 0.1},
}
RELATIVE_TOLERANCE_FIELDS = {("block", "blast_resistance")}
TECHNICAL_UNANSWERABLE = {
    "barrier",
    "bedrock",
    "command_block",
    "end_portal_frame",
    "fire",
    "infested_block",
    "jigsaw",
    "light",
    "petrified_oak_slab",
    "reinforced_deepslate",
    "spawner",
    "structure_block",
    "structure_void",
    "test_block",
    "test_instance_block",
    "trial_spawner",
    "vault",
}
ICONIC_TIER1 = {
    "block": ["stone", "dirt", "grass_block", "diamond_ore", "obsidian", "crafting_table", "tnt", "wool", "sand"],
    "item": ["diamond", "apple", "bread", "pickaxe", "sword", "iron_ingot", "ender_pearl"],
    "mob": ["creeper", "zombie", "enderman", "villager", "ender_dragon", "witch"],
}
EXPECTED_NAMES = {
    ("block", "sand"): "沙子",
    ("block", "test_instance_block"): "测试实例方块",
    ("block", "nether_bricks"): "下界砖块",
    ("block", "prismarine"): "海晶石",
    ("block", "pumpkin"): "南瓜",
    ("block", "sign"): "告示牌",
    ("block", "glass_pane"): "玻璃板",
    ("mob", "creeper"): "苦力怕",
    ("item", "enchanted_golden_apple"): "附魔金苹果",
}
EXPECTED_ALIASES = {
    ("block", "diamond_ore"): "钻石矿",
    ("block", "clay"): "粘土",
    ("block", "gold_block"): "金砖",
    ("item", "name_tag"): "名牌",
    ("item", "lead"): "栓绳",
    ("item", "potato"): "土豆",
    ("mob", "creeper"): "爬行者",
    ("mob", "enderman"): "小黑",
    ("mob", "wither"): "凋零",
}
FORBIDDEN_NAME_FRAGMENTS = ("绒球葱", "雕纹下界", "雕纹石砖", "雕纹磨制黑石", "暗海晶石", "红沙")
NORMALIZE_TRIM = (" ", "\u3000", "\t", "_", "-", "·", "・", "\u00b7")


# ---------------------------------------------------------------------------
# 载入
# ---------------------------------------------------------------------------
@pytest.fixture(scope="module")
def payload() -> dict:
    assert CATALOG_PATH.is_file(), f"题库文件缺失：{CATALOG_PATH}"
    return json.loads(CATALOG_PATH.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def categories(payload: dict) -> dict:
    return payload["categories"]


@pytest.fixture(scope="module")
def build_module():
    spec = importlib.util.spec_from_file_location("mcguess_build_catalog", BUILD_SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def entries(categories: dict, category: str) -> list[dict]:
    return categories[category]["entries"]


def fields(categories: dict, category: str) -> list[dict]:
    return categories[category]["fields"]


def field_map(categories: dict, category: str) -> dict[str, dict]:
    return {field["key"]: field for field in fields(categories, category)}


def normalize_name(value: object) -> str:
    text = str(value).casefold().strip()
    for char in NORMALIZE_TRIM:
        text = text.replace(char, "")
    return text


# ---------------------------------------------------------------------------
# 顶层结构
# ---------------------------------------------------------------------------
def test_catalog_is_valid_json_object(payload):
    assert isinstance(payload, dict) and payload


def test_top_level_metadata(payload):
    assert payload["schema_version"] == 1
    assert isinstance(payload["game_version"], str) and payload["game_version"]
    assert "mcdle" in payload["source"].lower()
    release_order = payload["release_order"]
    assert release_order and all(isinstance(key, str) for key in release_order)
    assert all(isinstance(value, int) and not isinstance(value, bool) for value in release_order.values())
    # 序号必须是从 0 开始的连续编号，方便 core 做「先后」比较
    assert sorted(release_order.values()) == list(range(len(release_order)))
    assert payload["game_version"] in release_order


def test_categories_are_complete(categories):
    assert list(categories) == CATEGORY_ORDER
    for category in CATEGORY_ORDER:
        data = categories[category]
        assert data["label"] and data["icon"]
        assert data["entries"], f"{category} 没有词条"
        assert data["fields"], f"{category} 没有属性定义"
        assert data["hint_fields"] and set(data["hint_fields"]) <= set(FIELD_ORDER[category])


def test_field_order_and_kinds(categories):
    for category in CATEGORY_ORDER:
        data = categories[category]
        assert [field["key"] for field in data["fields"]] == FIELD_ORDER[category], category
        for item_field in data["fields"]:
            assert item_field["kind"] in ALLOWED_KINDS, item_field["key"]
            assert item_field["label"], item_field["key"]


def test_version_fields_are_declared_after_numbers(categories):
    for category in CATEGORY_ORDER:
        version_keys = [field["key"] for field in fields(categories, category) if field["kind"] == "version"]
        assert version_keys == ["initial_release"], category


def test_number_fields_have_expected_tolerance(categories):
    for category, expected in EXPECTED_NUMBERS.items():
        mapping = field_map(categories, category)
        for key, tolerance in expected.items():
            item_field = mapping[key]
            assert item_field["kind"] == "number", key
            if (category, key) in RELATIVE_TOLERANCE_FIELDS:
                assert item_field.get("tolerance_ratio") == pytest.approx(tolerance), key
                assert "tolerance" not in item_field, key
            else:
                assert item_field.get("tolerance") == pytest.approx(tolerance), key


def test_enum_and_set_values_cover_every_attribute(categories):
    for category in CATEGORY_ORDER:
        for item_field in fields(categories, category):
            if item_field["kind"] not in {"enum", "set"}:
                continue
            values = item_field["values"]
            assert values, f"{category}.{item_field['key']} 缺少中文映射"
            assert all(isinstance(label, str) and label for label in values.values())
            for entry in entries(categories, category):
                attr = entry["attrs"][item_field["key"]]
                used = attr if isinstance(attr, list) else [attr]
                missing = sorted(set(used) - set(values))
                assert not missing, f"{category}/{entry['id']}.{item_field['key']} 缺少译名 {missing}"
            if item_field["kind"] == "enum":
                for pair in item_field.get("similar") or []:
                    assert len(pair) == 2
                    assert set(pair) <= set(values), (category, item_field["key"], pair)


def test_version_values_have_release_order(payload, categories):
    release_order = payload["release_order"]
    for category in CATEGORY_ORDER:
        for entry in entries(categories, category):
            version = entry["attrs"]["initial_release"]
            assert version in release_order, f"{category}/{entry['id']} 的版本 {version} 不在 release_order 里"


def test_release_order_matches_minecraft_history(payload):
    order = payload["release_order"]
    milestones = ["pre-alpha", "Alpha 1.0.1", "Beta 1.2", "1.0.0", "1.9", "1.10", "1.19", "1.20", "1.21.9"]
    present = [version for version in milestones if version in order]
    assert present[0] == "pre-alpha"
    assert present == sorted(present, key=order.__getitem__), present


# ---------------------------------------------------------------------------
# 词条
# ---------------------------------------------------------------------------
def test_entries_have_unique_ids_and_names(categories):
    for category in CATEGORY_ORDER:
        ids = [entry["id"] for entry in entries(categories, category)]
        names = [entry["name"] for entry in entries(categories, category)]
        assert len(set(ids)) == len(ids), f"{category} id 重复"
        assert len(set(names)) == len(names), f"{category} 中文名重复"
        for entry in entries(categories, category):
            assert entry["name_en"], entry["id"]
            assert isinstance(entry["aliases"], list)
            assert len(set(entry["aliases"])) == len(entry["aliases"]), entry["id"]
            assert all(isinstance(alias, str) and alias for alias in entry["aliases"])


def test_attrs_match_field_definitions(categories):
    for category in CATEGORY_ORDER:
        expected = set(FIELD_ORDER[category])
        for entry in entries(categories, category):
            assert set(entry["attrs"]) == expected, f"{category}/{entry['id']}"
            assert list(entry["attrs"]) == FIELD_ORDER[category], f"{category}/{entry['id']} 属性顺序不对"


def test_attr_value_types(categories):
    for category in CATEGORY_ORDER:
        mapping = field_map(categories, category)
        for entry in entries(categories, category):
            for key, value in entry["attrs"].items():
                kind = mapping[key]["kind"]
                if kind == "number":
                    assert isinstance(value, (int, float)) and not isinstance(value, bool), (entry["id"], key)
                elif kind == "set":
                    assert isinstance(value, list), (entry["id"], key)
                    assert all(isinstance(item, str) and item for item in value), (entry["id"], key)
                else:
                    assert isinstance(value, str) and value, (entry["id"], key)


def test_empty_set_attributes_are_known_source_gaps(categories):
    """数据集里只有 copper_nugget 的 obtaining/usage 是空数组，其余集合字段都不为空。"""
    empty = set()
    for category in CATEGORY_ORDER:
        mapping = field_map(categories, category)
        for entry in entries(categories, category):
            for key, value in entry["attrs"].items():
                if mapping[key]["kind"] == "set" and not value:
                    empty.add((category, entry["id"], key))
    assert empty == {("item", "copper_nugget", "obtaining"), ("item", "copper_nugget", "usage")}


def test_tiers_and_answerable_are_sane(categories):
    for category in CATEGORY_ORDER:
        tier1 = 0
        for entry in entries(categories, category):
            assert entry["tier"] in {1, 2, 3}, entry["id"]
            assert isinstance(entry["answerable"], bool), entry["id"]
            tier1 += entry["tier"] == 1
        assert tier1 >= TIER1_MIN[category], f"{category} 的 tier1 只有 {tier1} 条"


def test_hard_difficulty_has_exclusive_entries(categories):
    for category in CATEGORY_ORDER:
        tiers = {entry["tier"] for entry in entries(categories, category)}
        assert tiers == {1, 2, 3}, f"{category} 缺少某一难度层级"


def test_answer_pools_are_nested(categories):
    for category in CATEGORY_ORDER:
        def pool(limit: int) -> set[str]:
            return {
                entry["id"]
                for entry in entries(categories, category)
                if entry["answerable"] and entry["tier"] <= limit
            }

        easy, normal, hard = pool(1), pool(2), pool(3)
        assert easy and normal and hard
        assert easy < normal < hard
        assert all(entry["answerable"] for entry in entries(categories, category) if entry["tier"] == 1)


def test_technical_entries_are_guessable_but_not_answers(categories):
    for category in CATEGORY_ORDER:
        by_id = {entry["id"]: entry for entry in entries(categories, category)}
        for entry_id in TECHNICAL_UNANSWERABLE & set(by_id):
            assert by_id[entry_id]["answerable"] is False, entry_id
            assert by_id[entry_id]["tier"] > 1, f"{entry_id} 不应出现在简单池"


def test_tier1_contains_only_iconic_entries(categories):
    for category, ids in ICONIC_TIER1.items():
        by_id = {entry["id"]: entry for entry in entries(categories, category)}
        for entry_id in ids:
            assert entry_id in by_id, f"{category} 缺少必知词条 {entry_id}"
            assert by_id[entry_id]["tier"] == 1, f"{category}/{entry_id} 应属于简单池"
        easy_ids = {entry["id"] for entry in entries(categories, category) if entry["tier"] == 1}
        for obscure in ("light", "structure_void", "test_block", "debug_stick", "spectral_arrow"):
            assert obscure not in easy_ids, f"{obscure} 不应出现在简单池"


# ---------------------------------------------------------------------------
# 中文名与别名
# ---------------------------------------------------------------------------
def test_names_use_official_chinese(categories):
    for (category, entry_id), expected in EXPECTED_NAMES.items():
        by_id = {entry["id"]: entry["name"] for entry in entries(categories, category)}
        assert by_id[entry_id] == expected, f"{category}/{entry_id}"
    all_names = [entry["name"] for category in CATEGORY_ORDER for entry in entries(categories, category)]
    for name in all_names:
        for fragment in FORBIDDEN_NAME_FRAGMENTS:
            assert fragment not in name, f"{name} 含有错误译名片段 {fragment}"


def test_aliases_cover_common_slang(categories):
    for (category, entry_id), alias in EXPECTED_ALIASES.items():
        by_id = {entry["id"]: entry for entry in entries(categories, category)}
        assert alias in by_id[entry_id]["aliases"], f"{category}/{entry_id} 缺少别名 {alias}"


def test_names_resolve_unambiguously(categories):
    for category in CATEGORY_ORDER:
        owners: dict[str, str] = {}
        for entry in entries(categories, category):
            labels = [entry["id"], entry["name"], entry["name_en"], *entry["aliases"]]
            for label in labels:
                key = normalize_name(label)
                owner = owners.setdefault(key, entry["id"])
                assert owner == entry["id"], f"{category} 的名称「{label}」同时指向 {owner} 与 {entry['id']}"


# ---------------------------------------------------------------------------
# 离线、体积、可重跑
# ---------------------------------------------------------------------------
def test_catalog_has_no_network_references(payload):
    text = json.dumps(payload, ensure_ascii=False)
    assert "http://" not in text and "https://" not in text
    for category in CATEGORY_ORDER:
        for entry in entries(payload["categories"], category):
            for banned in ("url", "image_url", "wiki_image_url"):
                assert banned not in entry
                assert banned not in entry["attrs"]


def test_catalog_size_within_budget():
    assert CATALOG_PATH.stat().st_size < 400 * 1024


def test_build_is_reproducible(build_module):
    rebuilt = build_module.render(build_module.build_payload(RAW_DIR))
    assert rebuilt == CATALOG_PATH.read_text(encoding="utf-8")


def test_build_check_mode_agrees_with_committed_file(build_module):
    assert build_module.main(["--check", "--quiet"]) == 0


def test_curation_references_only_existing_ids():
    raw_ids = {}
    for category, filename in (("block", "block.json"), ("item", "item.json"), ("mob", "mob.json")):
        raw = json.loads((RAW_DIR / filename).read_text(encoding="utf-8"))
        raw_ids[category] = {str(record["id"]) for record in raw}
    curation = json.loads((RAW_DIR / "curation.json").read_text(encoding="utf-8"))
    for category in CATEGORY_ORDER:
        data = curation[category]
        referenced = set(data.get("tier1") or []) | set(data.get("tier2") or []) | set(data.get("unanswerable") or [])
        referenced |= set(data.get("name_fixes") or {}) | set(data.get("name_en_fixes") or {})
        referenced |= set(data.get("aliases") or {})
        assert referenced <= raw_ids[category], sorted(referenced - raw_ids[category])
