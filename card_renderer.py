"""HTML card rendering for MCGuess feedback, status and help images.

Stdlib only: view builders turn domain data (``core.evaluate_guess`` feedback,
``core.summarize_clues`` clues, round history) into plain template-ready dicts,
and the ``build_*_html`` functions turn those into self-contained pages.

Design notes
------------
* 800 px wide so a phone shows the card at a readable size without zooming.
* Every state is shown by shape *and* colour (✓ / ≈ / ✕ icons), never colour only.
* All icons are inline SVG pixel art – no emoji, web fonts or network resources,
  so the card looks the same on any server font setup.
* Every external string goes through ``html.escape``.
"""

from __future__ import annotations

from html import escape
from typing import Any

CARD_WIDTH = 800
MAX_COLUMNS = 6
DEFAULT_COLUMNS = 4
HISTORY_ROWS = 6

_STATES: dict[str, str] = {"exact": "相同", "close": "接近", "miss": "不同"}
_DIRECTIONS: dict[str, str] = {"up": "答案更大", "down": "答案更小"}
_OUTCOMES = {"playing", "won", "lost", "gaveup", "expired"}
_MODES = {"block", "item", "mob", "rank"}
_MODE_BY_LABEL = {"方块": "block", "物品": "item", "生物": "mob"}


# ---------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------
def _text(value: Any) -> str:
    """Escape one external value for safe interpolation into HTML."""
    return escape("" if value is None else str(value), quote=True)


def _int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _status(value: Any) -> str:
    state = str(value or "").strip()
    return state if state in _STATES else "miss"


def _direction(value: Any) -> str:
    direction = str(value or "").strip().lower()
    return direction if direction in _DIRECTIONS else ""


def _columns(value: Any) -> int:
    return max(1, min(_int(value, DEFAULT_COLUMNS), MAX_COLUMNS))


def _mode(category_key: Any, category_label: Any = "") -> str:
    key = str(category_key or "").strip()
    if key in _MODES:
        return key
    return _MODE_BY_LABEL.get(str(category_label or "").strip(), "block")


def auto_columns(count: int) -> int:
    """Pick a column count that leaves the fewest holes (4 for blocks, 3 for items/mobs)."""
    if count <= 4:
        return max(1, count)
    if count % 3 == 0:
        return 3
    return 4


# ---------------------------------------------------------------------------
# pixel-art icons (inline SVG)
# ---------------------------------------------------------------------------
_PIXEL_ART: dict[str, tuple[dict[str, str], list[str]]] = {
    "block": (  # grass block
        {"g": "#7CC44A", "G": "#5E9E36", "d": "#8A5A33", "D": "#6B4425", "s": "#A0704A"},
        ["gGgggGgg", "ggGgGggG", "GdgGdgGd", "ddDddsdd", "dsddDddD", "dDddsddd", "ddsdDdsd", "DdddddDd"],
    ),
    "item": (  # diamond
        {"w": "#E8FFFB", "c": "#5DE3D3", "C": "#2CB5A8", "k": "#167068", ".": ""},
        ["..kkkk..", ".kwccck.", "kwccCcCk", "kcccCCCk", ".kcCCCk.", "..kCCk..", "...kk...", "........"],
    ),
    "mob": (  # creeper face
        {"g": "#62B34A", "G": "#4C963A", "l": "#86CF6A", "k": "#14210F"},
        ["gGlggGgl", "gkkGgkkg", "lkkglkkG", "gGgkkgGg", "Ggkkkkgl", "gGkkkkGg", "lgkGgkgG", "gGgglgGg"],
    ),
    "rank": (  # trophy
        {"y": "#F2C94C", "Y": "#C9962B", "w": "#FFF1B8", "b": "#8A5A33", ".": ""},
        ["yyyyyyyy", "ywyyyyYy", ".yyyyYy.", "..yyYy..", "...yY...", "...yY...", "..bbbb..", ".bbbbbb."],
    ),
}


def pixel_icon(mode: str, size: int = 48) -> str:
    palette, rows = _PIXEL_ART.get(mode, _PIXEL_ART["block"])
    rects = []
    for y, row in enumerate(rows):
        for x, char in enumerate(row):
            color = palette.get(char, "")
            if color:
                rects.append(f'<rect x="{x}" y="{y}" width="1" height="1" fill="{color}"/>')
    return (
        f'<svg class="px" width="{size}" height="{size}" viewBox="0 0 8 8" shape-rendering="crispEdges">'
        + "".join(rects)
        + "</svg>"
    )


_GLYPHS = {
    "exact": '<path d="M3.5 8.5l3 3 6-7" fill="none" stroke="currentColor" stroke-width="2.4" '
    'stroke-linecap="square"/>',
    "close": '<path d="M3.5 6.5c2-1.6 3.5 1.6 5.5 0s3-1.2 3.5-.6M3.5 10.5c2-1.6 3.5 1.6 5.5 0s3-1.2 3.5-.6" '
    'fill="none" stroke="currentColor" stroke-width="2.1" stroke-linecap="round"/>',
    "miss": '<path d="M4.5 4.5l7 7M11.5 4.5l-7 7" fill="none" stroke="currentColor" stroke-width="2.2" '
    'stroke-linecap="square"/>',
    "up": '<path d="M8 3l5 6H9.6v4H6.4V9H3z" fill="currentColor"/>',
    "down": '<path d="M8 13L3 7h3.4V3h3.2v4H13z" fill="currentColor"/>',
}


def glyph(name: str, size: int = 14) -> str:
    return (
        f'<svg class="gl" width="{size}" height="{size}" viewBox="0 0 16 16" aria-hidden="true">'
        f"{_GLYPHS.get(name, _GLYPHS['miss'])}</svg>"
    )


# ---------------------------------------------------------------------------
# view builders
# ---------------------------------------------------------------------------
def _tiles_from_feedback(feedback: Any, clues: Any) -> list[dict[str, Any]]:
    clue_map: dict[str, dict[str, Any]] = {}
    for clue in clues or []:
        if isinstance(clue, dict) and clue.get("key"):
            clue_map[str(clue["key"])] = clue
    tiles: list[dict[str, Any]] = []
    for item in feedback or []:
        if not isinstance(item, dict):
            continue
        state = _status(item.get("status"))
        key = str(item.get("key") or "")
        hits = []
        if isinstance(item.get("hits"), (list, tuple)):
            hits = [str(hit) for hit in item["hits"] if str(hit).strip()]
        clue = clue_map.get(key) or {}
        clue_text = str(clue.get("text") or "") if state != "exact" else ""
        tiles.append(
            {
                "key": key,
                "label": str(item.get("label") or key or "属性"),
                "value": str(item.get("value") if item.get("value") is not None else "—"),
                "status": state,
                "status_label": _STATES[state],
                "direction": _direction(item.get("direction")),
                "hits": hits if state != "exact" else [],
                "clue": clue_text,
                "clue_known": clue.get("state") == "known",
            }
        )
    return tiles


def _history_rows(history: Any) -> list[dict[str, Any]]:
    rows = []
    for item in history or []:
        if not isinstance(item, dict):
            continue
        states = [_status(state) for state in (item.get("states") or [])]
        rows.append(
            {
                "n": max(0, _int(item.get("n"), 0)),
                "name": str(item.get("name") or "—"),
                "player": str(item.get("player") or ""),
                "states": states,
                "hit": sum(1 for state in states if state == "exact"),
                "correct": bool(item.get("correct")),
            }
        )
    return rows


def make_guess_card_view(
    *,
    category_label: str,
    difficulty_label: str,
    attempt: int,
    max_attempts: int,
    remaining: int,
    guess_name: str,
    guess_name_en: str = "",
    feedback: list[dict[str, Any]] | None = None,
    status_text: str = "",
    category_key: str = "",
    category_icon: str = "",  # kept for backwards compatibility; the card draws its own pixel icon
    columns: int | None = None,
    player: str = "",
    clues: list[dict[str, Any]] | None = None,
    history: list[dict[str, Any]] | None = None,
    history_total: int | None = None,
    outcome: str = "playing",
    answer_name: str = "",
    answer_name_en: str = "",
    hints_used: int = 0,
    hint_limit: int = 0,
    hint_notes: list[str] | None = None,
    participants: int = 0,
) -> dict[str, Any]:
    """Convert one guess (plus round context) into template-ready card data."""
    tiles = _tiles_from_feedback(feedback, clues)
    rows = _history_rows(history)
    total = max(len(rows), _int(history_total, len(rows)))
    outcome = outcome if outcome in _OUTCOMES else "playing"
    return {
        "kind": "guess",
        "mode": _mode(category_key, category_label),
        "category_label": str(category_label or ""),
        "category_icon": str(category_icon or ""),
        "difficulty_label": str(difficulty_label or ""),
        "attempt": max(0, _int(attempt, 0)),
        "max_attempts": max(0, _int(max_attempts, 0)),
        "remaining": max(0, _int(remaining, 0)),
        "guess_name": str(guess_name or ""),
        "guess_name_en": str(guess_name_en or ""),
        "player": str(player or ""),
        "status_text": str(status_text or ""),
        "columns": _columns(columns) if columns is not None else auto_columns(len(tiles)),
        "tiles": tiles,
        "has_feedback": bool(tiles),
        "hit": sum(1 for tile in tiles if tile["status"] == "exact"),
        "history": rows[:HISTORY_ROWS],
        "history_more": max(0, total - min(len(rows), HISTORY_ROWS)),
        "outcome": outcome,
        "answer_name": str(answer_name or ""),
        "answer_name_en": str(answer_name_en or ""),
        "hints_used": max(0, _int(hints_used, 0)),
        "hint_limit": max(0, _int(hint_limit, 0)),
        "hint_notes": [str(note) for note in (hint_notes or []) if str(note).strip()],
        "participants": max(0, _int(participants, 0)),
    }


def make_status_card_view(
    *,
    category_label: str,
    difficulty_label: str,
    attempt: int,
    max_attempts: int,
    remaining: int,
    clues: list[dict[str, Any]] | None,
    category_key: str = "",
    history: list[dict[str, Any]] | None = None,
    history_total: int | None = None,
    outcome: str = "playing",
    answer_name: str = "",
    answer_name_en: str = "",
    hints_used: int = 0,
    hint_limit: int = 0,
    hint_notes: list[str] | None = None,
    participants: int = 0,
) -> dict[str, Any]:
    """Clue board for ``/mc猜 状态`` and the give-up reveal: what the group knows."""
    view = make_guess_card_view(
        category_key=category_key,
        category_label=category_label,
        difficulty_label=difficulty_label,
        attempt=attempt,
        max_attempts=max_attempts,
        remaining=remaining,
        guess_name="",
        history=history,
        history_total=history_total,
        outcome=outcome,
        answer_name=answer_name,
        answer_name_en=answer_name_en,
        hints_used=hints_used,
        hint_limit=hint_limit,
        hint_notes=hint_notes,
        participants=participants,
    )
    board = []
    for clue in clues or []:
        if not isinstance(clue, dict):
            continue
        state = str(clue.get("state") or "none")
        board.append(
            {
                "key": str(clue.get("key") or ""),
                "label": str(clue.get("label") or clue.get("key") or "属性"),
                "text": str(clue.get("text") or ""),
                "state": state if state in ("known", "partial") else "none",
            }
        )
    view.update(kind="status", clues=board, columns=auto_columns(len(board)))
    return view


# ---------------------------------------------------------------------------
# CSS
# ---------------------------------------------------------------------------
_CSS = """
* { box-sizing: border-box; margin: 0; padding: 0; }
html, body { background: #111315; }
body {
  width: %(width)dpx;
  color: #F2F4F0;
  font-family: "Noto Sans CJK SC", "Noto Sans SC", "Source Han Sans SC", "PingFang SC", "Microsoft YaHei",
    "WenQuanYi Micro Hei", sans-serif;
  font-size: 15px;
  line-height: 1.45;
  -webkit-font-smoothing: antialiased;
}
.card { --accent: #6DB33F; background: #181B1F; overflow: hidden; }
.card.item { --accent: #4FB9C9; }
.card.mob { --accent: #E97366; }
.card.rank { --accent: #F2C94C; }
.strip { height: 6px; background: var(--accent); }
.wrap { padding: 24px 28px 22px; }
.px { display: block; image-rendering: pixelated; }
.gl { display: block; flex: none; }

/* header */
.head { display: flex; align-items: center; gap: 14px; }
.icon { flex: none; padding: 6px; border-radius: 8px; background: #20242A; border: 1px solid rgba(255,255,255,.08); }
.title { flex: 1; min-width: 0; }
.eyebrow { font-size: 13px; color: rgba(242,244,240,.55); letter-spacing: .12em; }
.title h1 { font-size: 26px; font-weight: 800; line-height: 1.25; letter-spacing: .02em; }
.meta { display: flex; gap: 8px; margin-top: 4px; }
.pill { font-size: 13px; line-height: 20px; padding: 0 9px; border-radius: 999px; color: rgba(242,244,240,.82);
  background: rgba(255,255,255,.06); border: 1px solid rgba(255,255,255,.10); }
.pill.easy { color: #8FD6A4; border-color: rgba(95,184,120,.45); }
.pill.hard { color: #F2A196; border-color: rgba(233,115,102,.45); }
.chances { flex: none; width: 210px; text-align: right; }
.chances .num { font-size: 34px; font-weight: 800; line-height: 1; font-variant-numeric: tabular-nums; }
.chances .num small { font-size: 15px; font-weight: 500; color: rgba(242,244,240,.6); margin-left: 4px; }
.chances .num.low { color: #E3B341; } .chances .num.crit { color: #E97366; }
.chances .cap { font-size: 13px; color: rgba(242,244,240,.6); margin-top: 4px; }
.seg { display: flex; gap: 2px; height: 8px; margin-top: 8px; }
.seg i { flex: 1; border-radius: 1px; background: rgba(255,255,255,.10); }
.seg i.on { background: #5FB878; }
.seg.low i.on { background: #E3B341; } .seg.crit i.on { background: #E97366; }

/* current guess / outcome banner */
.guess { margin-top: 20px; display: flex; align-items: center; gap: 16px; padding: 14px 18px;
  border-radius: 10px; background: #20242A; border: 1px solid rgba(255,255,255,.08); }
.guess .who { font-size: 14px; color: rgba(242,244,240,.62); }
.guess .who b { color: #F2F4F0; font-weight: 700; }
.guess .main { flex: 1; min-width: 0; }
.guess .name { font-size: 28px; font-weight: 800; line-height: 1.25; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.guess .name span { font-size: 16px; font-weight: 500; color: rgba(242,244,240,.55); margin-left: 10px; }
.score { flex: none; text-align: right; }
.score .v { font-size: 26px; font-weight: 800; line-height: 1.1; font-variant-numeric: tabular-nums; }
.score .v small { font-size: 15px; font-weight: 500; color: rgba(242,244,240,.55); }
.score .c { font-size: 13px; color: rgba(242,244,240,.6); }
.guess.won { background: rgba(95,184,120,.16); border-color: rgba(95,184,120,.55); }
.guess.lost, .guess.expired { background: rgba(233,115,102,.12); border-color: rgba(233,115,102,.45); }
.guess.gaveup { background: rgba(255,255,255,.05); border-color: rgba(255,255,255,.16); }
.guess .badge { flex: none; width: 44px; height: 44px; border-radius: 10px; display: flex; align-items: center;
  justify-content: center; background: #5FB878; color: #0F2416; }
.guess.lost .badge, .guess.expired .badge { background: #E97366; color: #2A0E0B; }
.guess.gaveup .badge { background: rgba(255,255,255,.16); color: #F2F4F0; }

/* attribute tiles */
.grid { margin-top: 14px; display: grid; grid-template-columns: repeat(var(--cols, 4), minmax(0, 1fr)); gap: 10px; }
.t { display: flex; flex-direction: column; min-height: 108px; padding: 11px 13px 12px; border-radius: 8px;
  background: #1F2328; border: 1px solid rgba(255,255,255,.09); }
.t.exact { background: rgba(95,184,120,.15); border-color: rgba(95,184,120,.55); }
.t.close { background: rgba(227,179,65,.14); border-color: rgba(227,179,65,.60); }
.t .top { display: flex; align-items: center; justify-content: space-between; gap: 6px; font-size: 14px; color: rgba(242,244,240,.68); }
.t .top span:first-child { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.st { flex: none; width: 22px; height: 22px; border-radius: 6px; display: flex; align-items: center; justify-content: center;
  background: rgba(255,255,255,.08); color: rgba(242,244,240,.6); }
.exact .st, .known .st { background: #5FB878; color: #0F2416; }
.close .st { background: #E3B341; color: #2B2006; }
.t .val { margin-top: 5px; display: flex; flex-wrap: wrap; align-items: center; gap: 4px 8px; font-size: 22px; font-weight: 800; line-height: 1.25; }
.t .val .txt { min-width: 0; overflow-wrap: anywhere; display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; }
.t.miss .val { color: rgba(242,244,240,.80); }
.dir { display: inline-flex; align-items: center; gap: 2px; font-size: 13px; font-weight: 700; line-height: 20px;
  padding: 0 7px 0 5px; border-radius: 5px; background: rgba(222,146,85,.18); color: #F0A76B; white-space: nowrap; }
.hits { display: flex; flex-wrap: wrap; gap: 4px; margin-top: 6px; }
.hit { font-size: 13px; line-height: 20px; padding: 0 7px; border-radius: 5px; background: rgba(227,179,65,.22); color: #F3D27C; }
.clue { margin-top: auto; padding-top: 7px; font-size: 13px; color: rgba(242,244,240,.6);
  display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; }
.clue b { font-weight: 700; color: rgba(242,244,240,.8); margin-right: 4px; }
.clue.known, .clue.known b { color: #8FD6A4; }
.t.note { border-style: dashed; background: transparent; justify-content: center; gap: 6px; font-size: 14px; color: rgba(242,244,240,.62); }
.t.note b { color: #F2F4F0; font-weight: 700; white-space: nowrap; }
.t.note .line { display: block; }
.t.note .line + .line { margin-top: 2px; }

/* status board (clues) */
.t.known { background: rgba(95,184,120,.15); border-color: rgba(95,184,120,.55); }
.t.none .val { color: rgba(242,244,240,.36); font-weight: 600; font-size: 17px; }
.t.partial .val, .t.known .val { font-size: 18px; line-height: 1.35; }
.t .val .seg2 { display: block; font-size: 14px; font-weight: 600; color: rgba(242,244,240,.6); margin-top: 3px; }
.t.status-tile .val .txt { -webkit-line-clamp: 3; }

/* history */
.sec { margin-top: 20px; display: flex; justify-content: space-between; align-items: baseline; }
.sec h2 { font-size: 16px; font-weight: 700; }
.sec span { font-size: 13px; color: rgba(242,244,240,.55); }
.hist { margin-top: 8px; border-radius: 10px; border: 1px solid rgba(255,255,255,.08); overflow: hidden; }
.row { display: flex; align-items: center; gap: 12px; padding: 8px 14px; font-size: 15px; }
.row + .row { border-top: 1px solid rgba(255,255,255,.06); }
.row.cur { background: rgba(255,255,255,.04); }
.row .n { width: 30px; color: rgba(242,244,240,.5); font-variant-numeric: tabular-nums; }
.row .nm { width: 170px; font-weight: 700; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.row .pl { flex: 1; min-width: 0; color: rgba(242,244,240,.55); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.sq { display: flex; gap: 3px; }
.sq i { width: 18px; height: 18px; border-radius: 4px; display: flex; align-items: center; justify-content: center;
  background: rgba(255,255,255,.09); color: rgba(242,244,240,.45); }
.sq i.exact { background: #5FB878; color: #0F2416; }
.sq i.close { background: #E3B341; color: #2B2006; }
.row .h { width: 44px; text-align: right; color: rgba(242,244,240,.55); font-variant-numeric: tabular-nums; }
.row.win .nm { color: #8FD6A4; }

/* footer */
.foot { margin-top: 18px; padding-top: 14px; border-top: 1px solid rgba(255,255,255,.07);
  display: flex; justify-content: space-between; align-items: center; gap: 16px; font-size: 13px; color: rgba(242,244,240,.6); }
.legend { display: flex; gap: 12px; align-items: center; flex-wrap: wrap; }
.legend span { display: inline-flex; align-items: center; gap: 5px; }
.legend .st { width: 18px; height: 18px; border-radius: 5px; }
.legend .exact { background: #5FB878; color: #0F2416; } .legend .close { background: #E3B341; color: #2B2006; }
.tip { white-space: nowrap; color: rgba(242,244,240,.72); }
.tip code { font-family: inherit; font-weight: 700; color: #8FD6A4; }

/* leaderboard */
.lb { margin-top: 20px; border-radius: 10px; border: 1px solid rgba(255,255,255,.08); overflow: hidden; }
.lb .r { display: grid; grid-template-columns: 44px minmax(0, 1fr) 84px 92px 84px 84px; align-items: center; gap: 8px; padding: 10px 16px; font-size: 15px; }
.lb .r + .r { border-top: 1px solid rgba(255,255,255,.06); }
.lb .r.th { padding: 8px 16px; font-size: 13px; color: rgba(242,244,240,.55); background: rgba(255,255,255,.03); }
.lb .r > span:nth-child(n+3) { text-align: right; font-variant-numeric: tabular-nums; }
.lb .nm { font-weight: 700; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.lb .w { font-size: 20px; font-weight: 800; }
.lb .w small { font-size: 13px; font-weight: 500; color: rgba(242,244,240,.55); margin-left: 2px; }
.lb .dim { color: rgba(242,244,240,.65); }
.medal { width: 28px; height: 28px; border-radius: 7px; display: flex; align-items: center; justify-content: center;
  font-weight: 800; font-size: 14px; background: rgba(255,255,255,.07); color: rgba(242,244,240,.7); }
.r.top1 .medal { background: #F2C94C; color: #3A2A05; }
.r.top2 .medal { background: #C8CDD3; color: #22262B; }
.r.top3 .medal { background: #C98A4E; color: #2E1A08; }
.r.top1 { background: rgba(242,201,76,.08); }

/* help */
.cmds { margin-top: 20px; display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 10px; }
.cmd { padding: 14px 16px; border-radius: 8px; background: #1F2328; border: 1px solid rgba(255,255,255,.09); }
.cmd code { display: block; font-family: inherit; font-size: 17px; font-weight: 800; color: #F2F4F0; overflow-wrap: anywhere; }
.cmd p { margin-top: 6px; font-size: 14px; color: rgba(242,244,240,.65); overflow-wrap: anywhere; }
.how { margin-top: 20px; display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 10px; }
.how div { padding: 12px 14px; border-radius: 8px; font-size: 14px; color: rgba(242,244,240,.72); background: #1F2328; border: 1px solid rgba(255,255,255,.09); }
.how .st { width: 20px; height: 20px; border-radius: 5px; }
.how .exact { background: #5FB878; color: #0F2416; } .how .close { background: #E3B341; color: #2B2006; }
.how b { display: flex; align-items: center; gap: 6px; font-size: 15px; color: #F2F4F0; margin-bottom: 4px; }
.empty { grid-column: 1 / -1; padding: 36px 20px; text-align: center; border-radius: 8px; border: 1px dashed rgba(255,255,255,.18); color: rgba(242,244,240,.6); }
""" % {"width": CARD_WIDTH}

_PAGE = """<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width={width}">
<title>{title}</title>
<style>{css}</style>
</head>
<body>
{body}
</body>
</html>"""


def _page(title: str, body: str) -> str:
    return _PAGE.format(width=CARD_WIDTH, title=title, css=_CSS, body=body)


# ---------------------------------------------------------------------------
# fragments
# ---------------------------------------------------------------------------
_DIFF_CLASS = {"简单": "easy", "困难": "hard"}


def _header(view: dict[str, Any]) -> str:
    mode = _mode(view.get("mode"))
    category = _text(view.get("category_label") or "猜谜")
    difficulty = str(view.get("difficulty_label") or "普通")
    attempt = max(0, _int(view.get("attempt"), 0))
    maximum = max(0, _int(view.get("max_attempts"), 0))
    remaining = max(0, min(maximum, _int(view.get("remaining"), 0))) if maximum else 0
    outcome = view.get("outcome") or "playing"
    hint_limit = _int(view.get("hint_limit"), 0)
    pills = [f'<span class="pill {_DIFF_CLASS.get(difficulty, "")}">{_text(difficulty)}</span>']
    if hint_limit > 0:
        pills.append(f'<span class="pill">提示 {_int(view.get("hints_used"), 0)}/{hint_limit}</span>')

    ratio = remaining / maximum if maximum else 0
    level = "crit" if ratio <= 0.2 else ("low" if ratio <= 0.4 else "")
    segments = "".join(f'<i class="{"on" if i < remaining else ""}"></i>' for i in range(min(maximum, 50)))
    if outcome == "playing":
        number = f'<div class="num {level}">{remaining}<small>/ {maximum}</small></div><div class="cap">剩余机会</div>'
    else:
        number = f'<div class="num">{attempt}<small>次</small></div><div class="cap">本局共猜</div>'
    return f"""<div class="head">
  <div class="icon">{pixel_icon(mode, 44)}</div>
  <div class="title">
    <div class="eyebrow">MINECRAFT 猜谜</div>
    <h1>猜{category}</h1>
    <div class="meta">{"".join(pills)}</div>
  </div>
  <div class="chances">{number}<div class="seg {level if outcome == "playing" else ""}">{segments}</div></div>
</div>"""


def _banner(view: dict[str, Any]) -> str:
    outcome = view.get("outcome") or "playing"
    kind = view.get("kind")
    player = _text(view.get("player"))
    attempt = _int(view.get("attempt"), 0)
    total = len(view.get("tiles") or [])
    hit = _int(view.get("hit"), 0)
    score = f'<div class="score"><div class="v">{hit}<small>/{total}</small></div><div class="c">属性命中</div></div>'
    answer = _text(view.get("answer_name") or view.get("guess_name"))
    answer_en = _text(view.get("answer_name_en") or view.get("guess_name_en"))
    answer_html = f'{answer}<span>{answer_en}</span>' if answer_en else answer
    participants = _int(view.get("participants"), 0)
    crowd = f" · {participants} 人参与" if participants > 1 else ""

    if outcome == "won":
        who = f"<b>{player}</b> 猜中了！" if player else "猜中了！"
        return (
            f'<div class="guess won"><div class="badge">{glyph("exact", 24)}</div><div class="main">'
            f'<div class="who">{who}第 {attempt} 次{crowd}</div><div class="name">{answer_html}</div></div></div>'
        )
    if outcome in ("lost", "gaveup", "expired"):
        label = {"lost": "机会用完", "gaveup": "本局已放弃", "expired": "对局超时结束"}[outcome]
        return (
            f'<div class="guess {outcome}"><div class="badge">{glyph("miss", 22)}</div><div class="main">'
            f'<div class="who">{label} · 答案揭晓{crowd}</div><div class="name">{answer_html}</div></div></div>'
        )
    if kind == "status":
        return ""
    name = _text(view.get("guess_name") or "—")
    en = _text(view.get("guess_name_en"))
    name_html = f"{name}<span>{en}</span>" if en else name
    who = f"<b>{player}</b> 第 {attempt} 次猜" if player else f"第 {attempt} 次猜"
    return (
        f'<div class="guess"><div class="main"><div class="who">{who}</div>'
        f'<div class="name">{name_html}</div></div>{score}</div>'
    )


def _guess_tile(tile: dict[str, Any]) -> str:
    state = _status(tile.get("status"))
    direction = _direction(tile.get("direction"))
    value = f'<span class="txt">{_text(tile.get("value"))}</span>'
    if direction:
        value += f'<span class="dir">{glyph(direction, 13)}{_DIRECTIONS[direction][2:]}</span>'
    hits = [hit for hit in (tile.get("hits") or []) if str(hit).strip()]
    hits_html = ""
    if hits:
        hits_html = '<div class="hits">' + "".join(f'<span class="hit">含 {_text(hit)}</span>' for hit in hits) + "</div>"
    clue_html = ""
    clue = str(tile.get("clue") or "")
    if clue:
        known = " known" if tile.get("clue_known") else ""
        lead = "已确定" if known else "线索"
        clue_html = f'<div class="clue{known}"><b>{lead}</b>{_text(clue)}</div>'
    return (
        f'<div class="t {state}"><div class="top"><span>{_text(tile.get("label") or "属性")}</span>'
        f'<span class="st" title="{_STATES[state]}">{glyph(state)}</span></div>'
        f'<div class="val">{value}</div>{hits_html}{clue_html}</div>'
    )


def _clue_tile(clue: dict[str, Any]) -> str:
    state = clue.get("state") if clue.get("state") in ("known", "partial") else "none"
    parts = [part.strip() for part in str(clue.get("text") or "").split("；") if part.strip()]
    if parts:
        text = _text(parts[0]) + "".join(f'<span class="seg2">{_text(part)}</span>' for part in parts[1:])
    else:
        text = "暂无线索"
    icon = glyph("exact") if state == "known" else ""
    badge = f'<span class="st">{icon}</span>' if icon else ""
    return (
        f'<div class="t status-tile {state}"><div class="top"><span>{_text(clue.get("label") or "属性")}</span>{badge}</div>'
        f'<div class="val"><span class="txt">{text}</span></div></div>'
    )


def _note_tile(view: dict[str, Any], span: int) -> str:
    notes = [_text(note) for note in view.get("hint_notes") or []]
    lines = [f'<div class="line"><b>提示</b>{note}</div>' for note in notes]
    outcome = view.get("outcome") or "playing"
    if not lines:
        limit = _int(view.get("hint_limit"), 0)
        left = max(0, limit - _int(view.get("hints_used"), 0))
        if outcome == "playing" and left:
            lines.append(f'<div class="line">卡住了？发送 <b>/mc猜 提示</b>（还剩 {left} 次）</div>')
        elif outcome == "playing":
            lines.append('<div class="line">综合下方记录和线索继续推理</div>')
        else:
            used = _int(view.get("hints_used"), 0)
            people = _int(view.get("participants"), 0)
            lines.append(f'<div class="line">本局 <b>{people}</b> 人参与</div>' if people else "")
            lines.append(f'<div class="line">用了 <b>{used}</b> 次提示</div>')
    return f'<div class="t note" style="grid-column: span {span}">{"".join(lines)}</div>'


def _grid(view: dict[str, Any]) -> str:
    columns = _columns(view.get("columns"))
    if view.get("kind") == "status":
        items = [_clue_tile(c) for c in view.get("clues") or [] if isinstance(c, dict)]
    else:
        items = [_guess_tile(t) for t in view.get("tiles") or [] if isinstance(t, dict)]
    if not items:
        return '<div class="grid"><div class="empty">还没有可展示的属性，先猜一个名字吧。</div></div>'
    hole = (-len(items)) % columns
    if hole:
        items.append(_note_tile(view, hole))
    elif view.get("hint_notes"):
        items.append(_note_tile(view, columns))
    return f'<div class="grid" style="--cols: {columns}">{"".join(items)}</div>'


def _history(view: dict[str, Any]) -> str:
    rows = [row for row in view.get("history") or [] if isinstance(row, dict)]
    if not rows:
        return ""
    more = _int(view.get("history_more"), 0)
    caption = f"最近 {len(rows)} 次" + (f" · 更早还有 {more} 次" if more else "")
    html_rows = []
    for index, row in enumerate(rows):
        states = row.get("states") or []
        squares = "".join(
            f'<i class="{state}">{glyph(state, 12) if state != "miss" else ""}</i>' for state in states
        )
        classes = ["row"]
        if index == 0 and view.get("kind") != "status":
            classes.append("cur")
        if row.get("correct"):
            classes.append("win")
        html_rows.append(
            f'<div class="{" ".join(classes)}"><span class="n">#{_int(row.get("n"), 0)}</span>'
            f'<span class="nm">{_text(row.get("name"))}</span><span class="pl">{_text(row.get("player"))}</span>'
            f'<span class="sq">{squares}</span><span class="h">{_int(row.get("hit"), 0)}/{len(states)}</span></div>'
        )
    return (
        f'<div class="sec"><h2>猜测记录</h2><span>{caption}</span></div>'
        f'<div class="hist">{"".join(html_rows)}</div>'
    )


def _legend() -> str:
    return (
        '<div class="legend">'
        f'<span><i class="st exact">{glyph("exact", 12)}</i>相同</span>'
        f'<span><i class="st close">{glyph("close", 12)}</i>接近</span>'
        f'<span><i class="st">{glyph("miss", 12)}</i>不同</span>'
        f'<span>{glyph("up", 15)}{glyph("down", 15)}答案更大 / 更小</span>'
        "</div>"
    )


def _tip(view: dict[str, Any]) -> str:
    if (view.get("outcome") or "playing") != "playing":
        return '<div class="tip">发送 <code>/mc猜 开始</code> 再来一局</div>'
    if view.get("kind") == "status":
        return '<div class="tip">@我 直接发名字即可猜</div>'
    return '<div class="tip"><code>/mc猜 状态</code> 看全部线索</div>'


def _card(view: dict[str, Any], inner: str) -> str:
    mode = _mode(view.get("mode"))
    return f'<section class="card {mode}"><div class="strip"></div><div class="wrap">{inner}</div></section>'


# ---------------------------------------------------------------------------
# public page builders
# ---------------------------------------------------------------------------
def build_guess_card_html(view: dict[str, Any]) -> str:
    """Feedback card for one guess (also used for the win / lose reveal)."""
    view = view if isinstance(view, dict) else {}
    view = {**view, "kind": "guess"}
    inner = (
        _header(view)
        + _banner(view)
        + ("" if view.get("outcome") == "won" else _grid(view))  # all-green tiles add nothing on a win
        + _history(view)
        + f'<div class="foot">{_legend()}{_tip(view)}</div>'
    )
    return _page("MCGuess · " + _text(view.get("category_label") or "猜谜"), _card(view, inner))


def build_status_card_html(view: dict[str, Any]) -> str:
    """Clue board card: everything the group has deduced so far."""
    view = view if isinstance(view, dict) else {}
    view = {**view, "kind": "status"}
    title = '<div class="sec"><h2>已知线索</h2><span>汇总所有猜测与提示</span></div>'
    inner = (
        _header(view)
        + _banner(view)
        + title
        + _grid(view)
        + _history(view)
        + f'<div class="foot">{_legend()}{_tip(view)}</div>'
    )
    return _page("MCGuess · 线索", _card(view, inner))


def build_help_card_html(view: dict[str, Any]) -> str:
    """Command reference card."""
    if isinstance(view, list):
        view = {"commands": view}
    view = view if isinstance(view, dict) else {}
    title = _text(view.get("title") or "MC 猜谜")
    subtitle = _text(view.get("subtitle"))
    cards = []
    for item in view.get("commands") or []:
        if not isinstance(item, dict):
            continue
        description = _text(item.get("description"))
        cards.append(
            f'<div class="cmd"><code>{_text(item.get("command"))}</code>'
            + (f"<p>{description}</p>" if description else "")
            + "</div>"
        )
    grid = "".join(cards) or '<div class="empty">暂无可用指令</div>'
    how = (
        '<div class="how">'
        f'<div><b><i class="st exact">{glyph("exact", 13)}</i>相同</b>该属性与答案一致</div>'
        f'<div><b><i class="st close">{glyph("close", 13)}</i>接近</b>数值接近，或多值属性有交集</div>'
        f'<div><b>{glyph("up", 16)}{glyph("down", 16)}方向</b>答案的数值 / 版本更大或更小</div>'
        "</div>"
    )
    head = f"""<div class="head">
  <div class="icon">{pixel_icon("block", 44)}</div>
  <div class="title"><div class="eyebrow">MINECRAFT 猜谜</div><h1>{title}</h1>
  {f'<div class="meta"><span class="pill">{subtitle}</span></div>' if subtitle else ""}</div>
</div>"""
    foot = (
        '<div class="foot"><div class="legend">名字支持中文名、英文名、ID 和常见别名</div>'
        '<div class="tip">对局中 <code>@我</code> 直接发名字即可猜</div></div>'
    )
    inner = head + how + f'<div class="cmds">{grid}</div>' + foot
    return _page(title, _card({"mode": "block"}, inner))


def build_rank_card_html(view: dict[str, Any]) -> str:
    """Per-chat leaderboard card."""
    view = view if isinstance(view, dict) else {}
    rounds = max(0, _int(view.get("rounds"), 0))
    total = max(0, _int(view.get("total"), 0))
    rows = []
    for item in view.get("players") or []:
        if not isinstance(item, dict):
            continue
        rank = max(1, _int(item.get("rank"), len(rows) + 1))
        best = _int(item.get("best"), 0)
        top = f" top{rank}" if rank <= 3 else ""
        rows.append(
            f'<div class="r{top}"><span><span class="medal">{rank}</span></span>'
            f'<span class="nm">{_text(item.get("name") or "—")}</span>'
            f'<span class="w">{max(0, _int(item.get("wins"), 0))}<small>次</small></span>'
            f'<span class="dim">{f"{best} 次" if best > 0 else "—"}</span>'
            f'<span class="dim">{max(0, _int(item.get("rounds"), 0))}</span>'
            f'<span class="dim">{max(0, _int(item.get("guesses"), 0))}</span></div>'
        )
    if rows:
        head = (
            '<div class="r th"><span>名次</span><span>玩家</span><span>猜中</span>'
            "<span>最快猜中</span><span>参与局数</span><span>猜测次数</span></div>"
        )
        table = f'<div class="lb">{head}{"".join(rows)}</div>'
    else:
        table = '<div class="grid"><div class="empty">还没有完成的对局，玩一局就能上榜。</div></div>'
    title = _text(view.get("title") or "猜谜排行榜")
    header = f"""<div class="head">
  <div class="icon">{pixel_icon("rank", 44)}</div>
  <div class="title"><div class="eyebrow">MINECRAFT 猜谜</div><h1>{title}</h1>
  <div class="meta"><span class="pill">本群共 {rounds} 局</span><span class="pill">{total} 人上榜</span></div></div>
</div>"""
    foot = (
        '<div class="foot"><div class="legend">按猜中次数排名；相同时比最快猜中，再比参与局数</div>'
        '<div class="tip"><code>/猜方块</code> 开一局</div></div>'
    )
    return _page("MCGuess · 排行榜", _card({"mode": "rank"}, header + table + foot))
