"""Tests for catalogue loading, validation and name resolution."""

from __future__ import annotations

import json

import pytest

from astrbot_plugin_mcguess.catalog import CatalogError, Catalogue, normalize_name


def test_normalize_name_is_forgiving():
    assert normalize_name("  Diamond Ore ") == "diamondore"
    assert normalize_name("diamond_ore") == "diamondore"
    assert normalize_name("钻石矿石") == "钻石矿石"


def test_categories_and_fields_are_loaded(catalogue):
    assert list(catalogue.category_keys()) == ["block", "item", "mob"]
    block = catalogue.category("block")
    assert block.label == "方块" and block.icon == "🧱"
    assert [field["key"] for field in block.fields][:3] == ["hardness", "tool", "transparent"]
    version_field = block.field("initial_release")
    assert version_field["kind"] == "version"
    assert version_field["order"]["1.16"] == 5


def test_match_exact_supports_all_name_forms(catalogue):
    block = catalogue.category("block")
    for query in ("钻石矿石", "Diamond Ore", "diamond ore", "diamond_ore", "钻石矿", "  钻石矿石  "):
        entry = block.match_exact(query)
        assert entry is not None, query
        assert entry["id"] == "diamond_ore"
    assert block.match_exact("钻") is None
    assert block.match_exact("不存在的东西") is None


def test_resolve_returns_disambiguation_candidates(catalogue):
    block = catalogue.category("block")
    entry, candidates = block.resolve("钻")
    assert entry is None
    assert {item["name"] for item in candidates} == {"钻石矿石", "钻石块"}

    entry, candidates = block.resolve("远古")
    assert entry is not None and entry["id"] == "ancient_debris"
    assert candidates == []


def test_answer_pool_respects_tier_and_answerable(catalogue):
    block = catalogue.category("block")
    assert {entry["id"] for entry in block.answer_pool("easy")} == {"diamond_ore", "stone", "glass"}
    assert len(block.answer_pool("normal")) == 5
    hard_ids = {entry["id"] for entry in block.answer_pool("hard")}
    assert "ancient_debris" in hard_ids
    assert "bedrock" not in hard_ids  # answerable=false
    assert block.pool_size("unknown") == len(block.answer_pool("normal"))


def test_entry_lookup_and_all_names(catalogue):
    assert catalogue.entry("mob", "zombie")["name"] == "僵尸"
    assert catalogue.entry("mob", "missing") is None
    assert "猪" in list(catalogue.all_names("mob"))
    with pytest.raises(CatalogError):
        catalogue.category("unknown")


def test_catalogue_loads_from_disk(tmp_dir, payload):
    path = tmp_dir / "catalog.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    loaded = Catalogue.load(path)
    assert loaded.category("block").label == "方块"


def test_load_reports_missing_and_broken_files(tmp_dir):
    with pytest.raises(CatalogError, match="不存在"):
        Catalogue.load(tmp_dir / "nope.json")
    broken = tmp_dir / "broken.json"
    broken.write_text("{not json", encoding="utf-8")
    with pytest.raises(CatalogError, match="损坏"):
        Catalogue.load(broken)


def test_validation_rejects_duplicate_ids(payload):
    payload["categories"]["block"]["entries"][1]["id"] = payload["categories"]["block"]["entries"][0]["id"]
    with pytest.raises(CatalogError, match="id 重复"):
        Catalogue.from_payload(payload)


def test_validation_rejects_duplicate_names(payload):
    payload["categories"]["block"]["entries"][1]["name"] = payload["categories"]["block"]["entries"][0]["name"]
    with pytest.raises(CatalogError, match="名称重复"):
        Catalogue.from_payload(payload)


def test_validation_rejects_empty_catalogue(payload):
    payload["categories"]["block"]["entries"] = []
    with pytest.raises(CatalogError, match="没有任何词条"):
        Catalogue.from_payload(payload)


def test_validation_rejects_catalogue_without_answerable_entries(payload):
    for entry in payload["categories"]["mob"]["entries"]:
        entry["answerable"] = False
    with pytest.raises(CatalogError, match="没有可作为答案"):
        Catalogue.from_payload(payload)


def test_validation_rejects_missing_categories(payload):
    payload.pop("categories")
    with pytest.raises(CatalogError, match="categories"):
        Catalogue.from_payload(payload)
