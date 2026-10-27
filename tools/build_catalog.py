#!/usr/bin/env python3
"""离线构建 ``astrbot_plugin_mcguess`` 的题库 ``data/catalog.json``。

输入（本目录下的 ``mcdle_raw/``）：

* ``block.json`` / ``item.json`` / ``mob.json``：从 koishi-plugin-mcdle 数据集裁剪出的原始词条；
* ``value_map.json``：原始枚举值 → Minecraft 官方中文译名（只复用，不自造）；
* ``curation.json``：人工策展 —— 难度分级、别名、不可作为答案的条目、译名修正。

用法（在任意目录执行均可，路径都相对本文件解析）：

    python astrbot_plugin_mcguess/tools/build_catalog.py            # 重建题库
    python astrbot_plugin_mcguess/tools/build_catalog.py --check    # 校验已提交的题库是否最新
    python astrbot_plugin_mcguess/tools/build_catalog.py --refresh  # 从 _design 源数据重裁 raw 后重建

输出是确定性的：词条按 ``(tier, id)`` 排序，枚举/集合取值映射按键排序，不含时间戳或随机量，
因此同样的输入必然得到逐字节相同的输出。
"""

from __future__ import annotations

import argparse
import itertools
import json
import re
import sys
from pathlib import Path
from typing import Any

TOOLS_DIR = Path(__file__).resolve().parent
PLUGIN_DIR = TOOLS_DIR.parent
RAW_DIR = TOOLS_DIR / "mcdle_raw"
CATALOG_PATH = PLUGIN_DIR / "data" / "catalog.json"
DEFAULT_SOURCE_DIR = PLUGIN_DIR.parent / "_design" / "samples" / "mcdle" / "json"

SCHEMA_VERSION = 1
# 数据集里包含到 1.21.9（铜器时代）的内容，所以题库等价于该版本。
GAME_VERSION = "1.21.9"
SOURCE_NOTE = "Minecraft Wiki data, cleaned from koishi-plugin-mcdle (MIT OR Apache-2.0)"

RAW_FILES = {"block": "block.json", "item": "item.json", "mob": "mob.json"}
SOURCE_FILES = {
    "block": "blockData.json",
    "item": "itemData.json",
    "mob": "mobData.json",
}
SOURCE_VALUE_MAP = "valueMap.json"
VALUE_MAP_FILE = "value_map.json"
CURATION_FILE = "curation.json"
# 原始数据里带的外链字段一律丢弃：题库离线可用，不引用图片/网络资源。
DROP_RAW_FIELDS = ("url", "image_url", "wiki_image_url")

CATEGORY_META: dict[str, dict[str, Any]] = {
    "block": {
        "label": "方块",
        "icon": "🧱",
        "hint_fields": ["tool", "hardness", "transparent"],
        "tier1_min": 60,
    },
    "item": {
        "label": "物品",
        "icon": "🎒",
        "hint_fields": ["rarity", "obtaining", "renewable"],
        "tier1_min": 50,
    },
    "mob": {
        "label": "生物",
        "icon": "🐷",
        "hint_fields": ["behavior", "health", "classification"],
        "tier1_min": 35,
    },
}

# 字段顺序 = 卡片/文本里的展示顺序，必须与 _design/mcguess-interfaces.md 第 1 节一致。
FIELD_SPECS: dict[str, list[dict[str, Any]]] = {
    "block": [
        {"key": "hardness", "label": "硬度", "kind": "number", "tolerance": 0.5},
        {"key": "blast_resistance", "label": "爆炸抗性", "kind": "number", "tolerance_ratio": 0.25},
        {"key": "tool", "label": "工具", "kind": "enum"},
        {"key": "luminous", "label": "发光性", "kind": "enum"},
        {"key": "transparent", "label": "透明性", "kind": "enum"},
        {"key": "full_block", "label": "完整方块", "kind": "enum"},
        {"key": "flammable", "label": "可燃性", "kind": "enum"},
        {"key": "fire_catch", "label": "火焰传播性", "kind": "enum"},
        {"key": "stackable", "label": "堆叠数量", "kind": "number", "tolerance": 0},
        {"key": "renewable", "label": "可再生性", "kind": "enum"},
        {"key": "initial_release", "label": "初始版本", "kind": "version"},
    ],
    "item": [
        {"key": "rarity", "label": "稀有度", "kind": "enum"},
        {"key": "stackable", "label": "堆叠数量", "kind": "number", "tolerance": 0},
        {"key": "renewable", "label": "可再生性", "kind": "enum"},
        {"key": "obtaining", "label": "获得方式", "kind": "set"},
        {"key": "usage", "label": "用途", "kind": "set"},
        {"key": "inventory_categories", "label": "库存类别", "kind": "set"},
        {"key": "recipe", "label": "合成方式", "kind": "set"},
        {"key": "loot", "label": "掉落方式", "kind": "set"},
        {"key": "initial_release", "label": "初始版本", "kind": "version"},
    ],
    "mob": [
        {"key": "health", "label": "生命值", "kind": "number", "tolerance": 2},
        {"key": "height", "label": "高度", "kind": "number", "tolerance": 0.1},
        {"key": "behavior", "label": "行为", "kind": "set"},
        {"key": "spawn", "label": "生成方式", "kind": "set"},
        {"key": "classification", "label": "分类", "kind": "set"},
        {"key": "initial_release", "label": "初始版本", "kind": "version"},
    ],
}

TRI_STATE_FIELDS = {"luminous", "transparent", "full_block", "flammable", "fire_catch", "renewable"}
TRI_STATE_VALUES = ("no", "partial", "yes")
TRI_STATE_SIMILAR = [["no", "partial"], ["yes", "partial"]]
TOOL_SEPARATOR = ", "
RARITY_ADJACENT = [("common", "uncommon"), ("uncommon", "rare"), ("rare", "epic")]

_VERSION_PATTERN = re.compile(r"^(?:(pre-alpha)|(Alpha|Beta) (\d+(?:\.\d+)*)|(\d+(?:\.\d+)*))$")


class BuildError(RuntimeError):
    """Raised when raw data or curation is inconsistent and the catalogue cannot be built."""


# ---------------------------------------------------------------------------
# JSON helpers
# ---------------------------------------------------------------------------
def load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise BuildError(f"找不到输入文件：{path}") from exc
    except json.JSONDecodeError as exc:
        raise BuildError(f"输入文件不是合法 JSON：{path}（{exc}）") from exc


def dump_json(payload: Any, *, indent: int, sort_keys: bool = False) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=indent, sort_keys=sort_keys) + "\n"


def write_json(path: Path, payload: Any, *, indent: int, sort_keys: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(dump_json(payload, indent=indent, sort_keys=sort_keys), encoding="utf-8", newline="\n")


def normalize_name(value: Any) -> str:
    """与 ``catalog.normalize_name`` 保持一致的宽松归一化，用于查重。"""
    text = str(value).casefold().strip()
    for char in (" ", "\u3000", "\t", "_", "-", "·", "・", "\u00b7"):
        text = text.replace(char, "")
    return text


# ---------------------------------------------------------------------------
# 输入：从源数据裁剪出 mcdle_raw/
# ---------------------------------------------------------------------------
def refresh_raw(source_dir: Path, raw_dir: Path = RAW_DIR) -> None:
    """把解包后的 mcdle 数据集裁剪成 ``mcdle_raw/`` 下的构建输入（去掉外链字段）。"""
    if not source_dir.is_dir():
        raise BuildError(f"源数据目录不存在：{source_dir}")

    cleaned: dict[str, list[dict[str, Any]]] = {}
    for category, filename in SOURCE_FILES.items():
        records = load_json(source_dir / filename)
        if not isinstance(records, list) or not records:
            raise BuildError(f"源文件 {filename} 不是非空数组")
        rows: list[dict[str, Any]] = []
        for record in records:
            if not isinstance(record, dict):
                raise BuildError(f"源文件 {filename} 中存在非对象条目")
            rows.append({key: value for key, value in record.items() if key not in DROP_RAW_FIELDS})
        cleaned[category] = rows

    value_map = load_json(source_dir / SOURCE_VALUE_MAP)
    if not isinstance(value_map, dict) or not value_map:
        raise BuildError(f"源文件 {SOURCE_VALUE_MAP} 不是非空对象")

    for category, rows in cleaned.items():
        write_json(raw_dir / RAW_FILES[category], rows, indent=2, sort_keys=True)
    write_json(raw_dir / VALUE_MAP_FILE, value_map, indent=2, sort_keys=True)


# ---------------------------------------------------------------------------
# 属性归一化
# ---------------------------------------------------------------------------
def _as_number(value: Any, where: str) -> int | float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise BuildError(f"{where} 的数值字段不是数字：{value!r}")
    return value


def _as_string(value: Any, where: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise BuildError(f"{where} 的枚举/版本字段不是非空字符串：{value!r}")
    return value.strip()


def _as_string_list(value: Any, where: str) -> list[str]:
    if not isinstance(value, (list, tuple)):
        raise BuildError(f"{where} 的集合字段不是数组：{value!r}")
    items = []
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise BuildError(f"{where} 的集合字段包含非法取值：{item!r}")
        items.append(item.strip())
    return sorted(items)


def _as_tri_state(value: Any, where: str) -> str:
    text = _as_string(value, where)
    if text == "0":  # 数据里 carrots 的亮度写成 "0"，语义等同「否」
        text = "no"
    if text not in TRI_STATE_VALUES:
        raise BuildError(f"{where} 的三态取值非法：{value!r}")
    return text


def normalize_attrs(category: str, record: dict[str, Any]) -> dict[str, Any]:
    """把一条原始词条转换成 ``attrs``，键顺序与 ``FIELD_SPECS`` 一致。"""
    entry_id = str(record.get("id") or "?")
    attrs: dict[str, Any] = {}
    for spec in FIELD_SPECS[category]:
        key = spec["key"]
        where = f"{category}/{entry_id}.{key}"
        if key not in record:
            raise BuildError(f"{category}/{entry_id} 缺少属性 {key}")
        value = record[key]
        if spec["kind"] == "number":
            attrs[key] = _as_number(value, where)
        elif spec["kind"] == "set":
            attrs[key] = _as_string_list(value, where)
        elif key in TRI_STATE_FIELDS:
            attrs[key] = _as_tri_state(value, where)
        else:
            attrs[key] = _as_string(value, where)
    return attrs


# ---------------------------------------------------------------------------
# 字段定义
# ---------------------------------------------------------------------------
def _tool_similar(values: list[str]) -> list[list[str]]:
    """工具要求里只要共享任意一种工具，就算「接近」。"""
    pairs: set[tuple[str, str]] = set()
    for left, right in itertools.combinations(sorted(values), 2):
        if set(left.split(TOOL_SEPARATOR)) & set(right.split(TOOL_SEPARATOR)):
            pairs.add((left, right))
    return [list(pair) for pair in sorted(pairs)]


def _enum_similar(key: str, values: list[str]) -> list[list[str]]:
    if key in TRI_STATE_FIELDS:
        return [list(pair) for pair in TRI_STATE_SIMILAR]
    if key == "tool":
        return _tool_similar(values)
    if key == "rarity":
        pairs = sorted(tuple(sorted(pair)) for pair in RARITY_ADJACENT)
        return [list(pair) for pair in pairs if set(pair) <= set(values)]
    return []


def build_fields(category: str, attrs_list: list[dict[str, Any]], value_map: dict[str, Any]) -> list[dict[str, Any]]:
    fields: list[dict[str, Any]] = []
    for spec in FIELD_SPECS[category]:
        field = dict(spec)
        key = field["key"]
        if field["kind"] in {"enum", "set"}:
            used: set[str] = set()
            for attrs in attrs_list:
                value = attrs[key]
                used.update(value if isinstance(value, list) else [value])
            mapping = value_map.get(key)
            if not isinstance(mapping, dict):
                raise BuildError(f"value_map.json 缺少字段 {key} 的取值映射")
            missing = sorted(used - set(mapping))
            if missing:
                raise BuildError(f"{category}.{key} 存在没有中文译名的取值：{missing}")
            field["values"] = {raw: mapping[raw] for raw in sorted(used)}
            if field["kind"] == "enum":
                field["similar"] = _enum_similar(key, sorted(used))
        fields.append(field)
    return fields


# ---------------------------------------------------------------------------
# 词条
# ---------------------------------------------------------------------------
def _dedupe_aliases(aliases: Any, *, own_names: set[str], where: str) -> list[str]:
    if aliases is None:
        return []
    if not isinstance(aliases, list):
        raise BuildError(f"{where} 的 aliases 必须是数组")
    result: list[str] = []
    for alias in aliases:
        if not isinstance(alias, str) or not alias.strip():
            raise BuildError(f"{where} 的 aliases 含非法取值：{alias!r}")
        text = alias.strip()
        if normalize_name(text) in own_names or text in result:
            continue
        result.append(text)
    return result


def build_entries(
    category: str,
    records: list[dict[str, Any]],
    curated: dict[str, Any],
    attrs_by_id: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    tier1 = {str(item) for item in curated.get("tier1") or []}
    tier2 = {str(item) for item in curated.get("tier2") or []}
    unanswerable = {str(item) for item in curated.get("unanswerable") or []}
    name_fixes: dict[str, str] = dict(curated.get("name_fixes") or {})
    name_en_fixes: dict[str, str] = dict(curated.get("name_en_fixes") or {})
    aliases_map: dict[str, list[str]] = dict(curated.get("aliases") or {})

    known_ids = {str(record["id"]) for record in records}
    referenced = tier1 | tier2 | unanswerable | set(name_fixes) | set(name_en_fixes) | set(aliases_map)
    unknown = sorted(referenced - known_ids)
    if unknown:
        raise BuildError(f"{category} 的策展数据引用了不存在的 id：{unknown}")
    overlap = sorted(tier1 & tier2)
    if overlap:
        raise BuildError(f"{category} 同时被分到 tier1 和 tier2：{overlap}")

    entries: list[dict[str, Any]] = []
    for record in records:
        entry_id = str(record["id"])
        name = str(name_fixes.get(entry_id) or record.get("chinese_title") or "").strip()
        name_en = str(name_en_fixes.get(entry_id) or record.get("title") or "").strip()
        if not name or not name_en:
            raise BuildError(f"{category}/{entry_id} 缺少中文名或英文名")
        own_names = {normalize_name(entry_id), normalize_name(name), normalize_name(name_en)}
        aliases = _dedupe_aliases(aliases_map.get(entry_id), own_names=own_names, where=f"{category}/{entry_id}")
        tier = 1 if entry_id in tier1 else 2 if entry_id in tier2 else 3
        entries.append(
            {
                "id": entry_id,
                "name": name,
                "name_en": name_en,
                "tier": tier,
                "answerable": entry_id not in unanswerable,
                "aliases": aliases,
                "attrs": attrs_by_id[entry_id],
            }
        )
    entries.sort(key=lambda entry: (entry["tier"], entry["id"]))
    return entries


# ---------------------------------------------------------------------------
# 版本顺序
# ---------------------------------------------------------------------------
def version_key(value: str) -> tuple[int, tuple[int, ...]]:
    """把 ``pre-alpha`` / ``Alpha 1.0.1`` / ``1.21.9`` 这类版本字符串变成可比较的键。"""
    match = _VERSION_PATTERN.match(str(value).strip())
    if not match:
        raise BuildError(f"无法解析版本号：{value!r}")
    pre_alpha, stage, stage_version, release_version = match.groups()
    if pre_alpha:
        return (0, ())
    if stage_version:
        numbers = tuple(int(part) for part in stage_version.split("."))
        return (1 if stage == "Alpha" else 2, numbers)
    numbers = tuple(int(part) for part in str(release_version).split("."))
    return (3, numbers)


def build_release_order(records: dict[str, list[dict[str, Any]]]) -> dict[str, int]:
    versions = {str(record["initial_release"]).strip() for rows in records.values() for record in rows}
    ordered = sorted(versions, key=version_key)
    return {version: index for index, version in enumerate(ordered)}


# ---------------------------------------------------------------------------
# 校验
# ---------------------------------------------------------------------------
def validate_category(category: str, payload: dict[str, Any]) -> None:
    entries = payload["entries"]
    fields = payload["fields"]
    if not entries:
        raise BuildError(f"{category} 没有任何词条")
    if category not in CATEGORY_META:
        raise BuildError(f"未知类别：{category}")

    ids: set[str] = set()
    names: set[str] = set()
    resolved: dict[str, str] = {}
    for entry in entries:
        if entry["id"] in ids:
            raise BuildError(f"{category} 的 id 重复：{entry['id']}")
        if entry["name"] in names:
            raise BuildError(f"{category} 的中文名重复：{entry['name']}")
        ids.add(entry["id"])
        names.add(entry["name"])
        for label in [entry["id"], entry["name"], entry["name_en"], *entry["aliases"]]:
            key = normalize_name(label)
            owner = resolved.setdefault(key, entry["id"])
            if owner != entry["id"]:
                raise BuildError(f"{category} 的名称/别名冲突：「{label}」同时指向 {owner} 与 {entry['id']}")
        if set(entry["attrs"]) != {field["key"] for field in fields}:
            raise BuildError(f"{category}/{entry['id']} 的 attrs 键与字段不一致")

    tier_counts = {tier: 0 for tier in (1, 2, 3)}
    answerable = 0
    for entry in entries:
        tier_counts[entry["tier"]] += 1
        answerable += 1 if entry["answerable"] else 0
    if tier_counts[1] < int(CATEGORY_META[category]["tier1_min"]):
        raise BuildError(
            f"{category} 的 tier1 只有 {tier_counts[1]} 条，要求至少 {CATEGORY_META[category]['tier1_min']} 条"
        )
    if not answerable:
        raise BuildError(f"{category} 没有可作为答案的词条")
    for tier in (1, 2, 3):
        pool = [entry for entry in entries if entry["answerable"] and entry["tier"] <= tier]
        if not pool:
            raise BuildError(f"{category} 在难度上限 {tier} 下的答案池为空")


# ---------------------------------------------------------------------------
# 构建
# ---------------------------------------------------------------------------
def build_payload(raw_dir: Path = RAW_DIR) -> dict[str, Any]:
    raw_records = {category: load_json(raw_dir / filename) for category, filename in RAW_FILES.items()}
    value_map = load_json(raw_dir / VALUE_MAP_FILE)
    curation = load_json(raw_dir / CURATION_FILE)
    for name in (raw_records, value_map, curation):
        if not isinstance(name, dict) or not name:
            raise BuildError("构建输入必须是非空 JSON 对象")

    records: dict[str, list[dict[str, Any]]] = {}
    attrs_by_id: dict[str, dict[str, dict[str, Any]]] = {}
    for category in CATEGORY_META:
        rows = raw_records[category]
        if not isinstance(rows, list) or not rows:
            raise BuildError(f"{RAW_FILES[category]} 不是非空数组")
        seen: set[str] = set()
        records[category] = []
        attrs_by_id[category] = {}
        for record in rows:
            if not isinstance(record, dict) or not record.get("id"):
                raise BuildError(f"{RAW_FILES[category]} 中存在缺少 id 的条目")
            entry_id = str(record["id"])
            if entry_id in seen:
                raise BuildError(f"{RAW_FILES[category]} 的 id 重复：{entry_id}")
            seen.add(entry_id)
            records[category].append(record)
            attrs_by_id[category][entry_id] = normalize_attrs(category, record)

    release_order = build_release_order(records)
    categories: dict[str, Any] = {}
    for category, meta in CATEGORY_META.items():
        curated = curation.get(category)
        if not isinstance(curated, dict):
            raise BuildError(f"curation.json 缺少类别 {category}")
        fields = build_fields(category, list(attrs_by_id[category].values()), value_map)
        entries = build_entries(category, records[category], curated, attrs_by_id[category])
        categories[category] = {
            "label": meta["label"],
            "icon": meta["icon"],
            "fields": fields,
            "hint_fields": list(meta["hint_fields"]),
            "entries": entries,
        }
        validate_category(category, categories[category])

    return {
        "schema_version": SCHEMA_VERSION,
        "game_version": GAME_VERSION,
        "source": SOURCE_NOTE,
        "release_order": release_order,
        "categories": categories,
    }


def render(payload: dict[str, Any]) -> str:
    return dump_json(payload, indent=1)


def summarize(payload: dict[str, Any]) -> str:
    lines = [
        f"题库版本：{payload['game_version']}（schema {payload['schema_version']}）",
        f"版本序号：{len(payload['release_order'])} 个",
    ]
    for category, data in payload["categories"].items():
        entries = data["entries"]
        tiers = {tier: sum(1 for entry in entries if entry["tier"] == tier) for tier in (1, 2, 3)}
        answerable = sum(1 for entry in entries if entry["answerable"])
        pools = [
            sum(1 for entry in entries if entry["answerable"] and entry["tier"] <= max_tier) for max_tier in (1, 2, 3)
        ]
        lines.append(
            f"{data['label']}（{category}）：{len(entries)} 条 | tier {tiers[1]}/{tiers[2]}/{tiers[3]}"
            f" | 可作答案 {answerable} | 简单/普通/困难池 {pools[0]}/{pools[1]}/{pools[2]}"
        )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="从 mcdle 原始数据构建 MC 猜谜题库 catalog.json")
    parser.add_argument("--raw-dir", type=Path, default=RAW_DIR, help="构建输入目录（默认 tools/mcdle_raw）")
    parser.add_argument("--out", type=Path, default=CATALOG_PATH, help="输出文件（默认 data/catalog.json）")
    parser.add_argument("--check", action="store_true", help="只检查输出文件是否与构建结果一致")
    parser.add_argument("--refresh", action="store_true", help="先从源数据重新裁剪 mcdle_raw/，再构建")
    parser.add_argument("--source-dir", type=Path, default=None, help="--refresh 使用的源数据目录")
    parser.add_argument("--quiet", action="store_true", help="不打印统计信息")
    args = parser.parse_args(argv)

    try:
        if args.refresh:
            source_dir = args.source_dir or DEFAULT_SOURCE_DIR
            refresh_raw(source_dir, args.raw_dir)
            if not args.quiet:
                print(f"已刷新原始数据：{args.raw_dir}（来源 {source_dir}）")
        payload = build_payload(args.raw_dir)
        text = render(payload)
    except BuildError as exc:
        print(f"构建失败：{exc}", file=sys.stderr)
        return 2

    if args.check:
        if not args.out.exists():
            print(f"题库文件不存在：{args.out}", file=sys.stderr)
            return 1
        if args.out.read_text(encoding="utf-8") != text:
            print(f"题库已过期，请重新运行 {Path(__file__).name}", file=sys.stderr)
            return 1
        print(f"题库与构建结果一致：{args.out}")
        return 0

    try:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text, encoding="utf-8", newline="\n")
    except OSError as exc:
        print(f"写入题库失败：{exc}", file=sys.stderr)
        return 2
    if not args.quiet:
        print(summarize(payload))
        print(f"已写入 {args.out}（{len(text.encode('utf-8'))} 字节）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
