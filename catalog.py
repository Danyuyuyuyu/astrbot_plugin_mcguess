"""Catalogue loading and fuzzy name resolution for the MC guess game."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable

DIFFICULTY_TIERS = {"easy": 1, "normal": 2, "hard": 3}
DEFAULT_DIFFICULTY = "normal"

_TRIM_CHARS = (" ", "\u3000", "\t", "_", "-", "·", "・", "\u00b7")


class CatalogError(RuntimeError):
    """Raised when the bundled catalogue is missing or malformed."""


def normalize_name(value: Any) -> str:
    """Case-fold and strip padding so ``Gold Block``, ``gold_block`` and ``GoldBlock`` match."""
    text = str(value).casefold().strip()
    for char in _TRIM_CHARS:
        text = text.replace(char, "")
    return text


def _entry_names(entry: dict[str, Any]) -> set[str]:
    raw = [entry.get("id"), entry.get("name"), entry.get("name_en"), *(entry.get("aliases") or [])]
    return {normalize_name(value) for value in raw if value not in (None, "")}


class Category:
    """One answer category (blocks / items / mobs) with its own attributes."""

    def __init__(self, key: str, payload: dict[str, Any], release_order: dict[str, int]) -> None:
        self.key = key
        self.label = str(payload.get("label") or key)
        self.icon = str(payload.get("icon") or "")
        self.fields: list[dict[str, Any]] = []
        for raw_field in payload.get("fields") or []:
            field = dict(raw_field)
            field.setdefault("key", "")
            field.setdefault("label", field["key"])
            field.setdefault("kind", "enum")
            if field["kind"] == "version":
                field.setdefault("order", release_order)
            self.fields.append(field)
        self.field_map = {str(field["key"]): field for field in self.fields}
        self.hint_fields = [str(item) for item in payload.get("hint_fields") or []]

        self.entries: list[dict[str, Any]] = []
        self.by_id: dict[str, dict[str, Any]] = {}
        self._by_name: dict[str, list[dict[str, Any]]] = {}
        for raw_entry in payload.get("entries") or []:
            entry = self._normalize_entry(raw_entry)
            self.entries.append(entry)
            self.by_id[entry["id"]] = entry
            for name in _entry_names(entry):
                self._by_name.setdefault(name, []).append(entry)

    @staticmethod
    def _normalize_entry(raw: dict[str, Any]) -> dict[str, Any]:
        entry = dict(raw)
        entry["id"] = str(entry.get("id") or "")
        entry["name"] = str(entry.get("name") or entry["id"])
        entry["name_en"] = str(entry.get("name_en") or "")
        entry["aliases"] = [str(alias) for alias in (entry.get("aliases") or []) if str(alias).strip()]
        entry["attrs"] = dict(entry.get("attrs") or {})
        entry["tier"] = int(entry.get("tier") or 3)
        entry["answerable"] = bool(entry.get("answerable", True))
        return entry

    # -- lookup ---------------------------------------------------------
    def field(self, key: str) -> dict[str, Any] | None:
        return self.field_map.get(key)

    def entry(self, entry_id: str) -> dict[str, Any] | None:
        return self.by_id.get(str(entry_id))

    def match_exact(self, query: str) -> dict[str, Any] | None:
        """Return the single entry whose name/id/alias exactly equals ``query``."""
        entries = self._by_name.get(normalize_name(query), ())
        return entries[0] if len(entries) == 1 else None

    def matches(self, query: str) -> list[dict[str, Any]]:
        """All entries whose names exactly equal ``query`` (may be ambiguous)."""
        return list(self._by_name.get(normalize_name(query), ()))

    def resolve(self, query: str, limit: int = 8) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
        """Resolve exactly, else fall back to prefix/substring candidates for disambiguation."""
        needle = normalize_name(query)
        if not needle:
            return None, []
        exact = self._by_name.get(needle, ())
        if len(exact) == 1:
            return exact[0], []
        if exact:
            return None, list(exact[:limit])

        prefix: list[dict[str, Any]] = []
        contains: list[dict[str, Any]] = []
        seen: set[str] = set()
        for name, entries in self._by_name.items():
            if not needle:
                continue
            if name.startswith(needle):
                bucket = prefix
            elif needle in name:
                bucket = contains
            else:
                continue
            for entry in entries:
                if entry["id"] in seen:
                    continue
                seen.add(entry["id"])
                bucket.append(entry)
        for bucket in (prefix, contains):
            if len(bucket) == 1:
                return bucket[0], []
            if bucket:
                return None, bucket[:limit]
        return None, []

    def answer_pool(self, difficulty: str = DEFAULT_DIFFICULTY) -> list[dict[str, Any]]:
        """Answerable entries within the tier budget of the given difficulty."""
        max_tier = DIFFICULTY_TIERS.get(str(difficulty), DIFFICULTY_TIERS[DEFAULT_DIFFICULTY])
        return [entry for entry in self.entries if entry["answerable"] and entry["tier"] <= max_tier]

    def pool_size(self, difficulty: str = DEFAULT_DIFFICULTY) -> int:
        return len(self.answer_pool(difficulty))


class Catalogue:
    """Validated view over ``data/catalog.json``."""

    def __init__(self, payload: dict[str, Any]) -> None:
        if not isinstance(payload, dict):
            raise CatalogError("题库格式无效：顶层必须是对象。")
        self.payload = payload
        self.release_order: dict[str, int] = {
            str(key): int(value) for key, value in (payload.get("release_order") or {}).items()
        }
        raw_categories = payload.get("categories")
        if not isinstance(raw_categories, dict) or not raw_categories:
            raise CatalogError("题库格式无效：缺少 categories。")
        self.categories: dict[str, Category] = {}
        for key, category_payload in raw_categories.items():
            if not isinstance(category_payload, dict):
                raise CatalogError(f"题库格式无效：类别 {key} 不是对象。")
            category = Category(str(key), category_payload, self.release_order)
            self._validate_category(category)
            self.categories[str(key)] = category

    @classmethod
    def load(cls, path: str | Path) -> "Catalogue":
        data_path = Path(path)
        try:
            payload = json.loads(data_path.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise CatalogError(f"题库文件不存在：{data_path}") from exc
        except json.JSONDecodeError as exc:
            raise CatalogError(f"题库文件损坏：{data_path.name}") from exc
        return cls(payload)

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> "Catalogue":
        return cls(payload)

    @staticmethod
    def _validate_category(category: Category) -> None:
        if not category.entries:
            raise CatalogError(f"题库类别 {category.key} 没有任何词条。")
        if not category.fields:
            raise CatalogError(f"题库类别 {category.key} 没有属性定义。")
        ids: set[str] = set()
        names: set[str] = set()
        for entry in category.entries:
            if not entry["id"]:
                raise CatalogError(f"题库类别 {category.key} 存在缺少 id 的词条。")
            if entry["id"] in ids:
                raise CatalogError(f"题库类别 {category.key} 的 id 重复：{entry['id']}")
            if entry["name"] in names:
                raise CatalogError(f"题库类别 {category.key} 的名称重复：{entry['name']}")
            ids.add(entry["id"])
            names.add(entry["name"])
        answerable = [entry for entry in category.entries if entry["answerable"]]
        if not answerable:
            raise CatalogError(f"题库类别 {category.key} 没有可作为答案的词条。")

    # -- convenience ----------------------------------------------------
    def category(self, key: str) -> Category:
        category = self.categories.get(str(key))
        if category is None:
            raise CatalogError(f"未知题库类别：{key}")
        return category

    def has_category(self, key: str) -> bool:
        return str(key) in self.categories

    def category_keys(self) -> list[str]:
        return list(self.categories)

    def entries(self, key: str) -> list[dict[str, Any]]:
        return self.category(key).entries

    def entry(self, key: str, entry_id: str) -> dict[str, Any] | None:
        return self.category(key).entry(entry_id)

    def answer_pool(self, key: str, difficulty: str = DEFAULT_DIFFICULTY) -> list[dict[str, Any]]:
        return self.category(key).answer_pool(difficulty)

    def resolve(self, key: str, query: str, limit: int = 8):
        return self.category(key).resolve(query, limit=limit)

    def match_exact(self, key: str, query: str) -> dict[str, Any] | None:
        return self.category(key).match_exact(query)

    def all_names(self, key: str) -> Iterable[str]:
        for entry in self.category(key).entries:
            yield entry["name"]
