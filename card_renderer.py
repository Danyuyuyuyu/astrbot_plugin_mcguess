"""HTML card rendering for MCGuess feedback and help images.

The renderer is intentionally dependency-free (stdlib only): it turns the feedback
list produced by ``core.evaluate_guess`` into a template-ready view dict and then
into a self-contained dark Minecraft-styled HTML page that AstrBot's
``html_render(..., options={"type": "png", "full_page": True})`` can screenshot.

Every string that comes from the outside world is escaped with ``html.escape``;
the templates reference no external images, fonts or network resources.
"""

from __future__ import annotations

from html import escape
from typing import Any

# Status vocabulary used by core.evaluate_guess -> (Chinese label, emoji symbol).
_STATES: dict[str, tuple[str, str]] = {
    "exact": ("完全相同", "🟩"),
    "close": ("接近", "🟨"),
    "miss": ("不匹配", "⬜"),
}

# Numeric / version direction: "up" means the answer is greater than the guess.
_ARROWS: dict[str, str] = {"up": "⬆", "down": "⬇"}

MAX_COLUMNS = 6
DEFAULT_COLUMNS = 4


def _text(value: Any) -> str:
    """Escape one external value for safe interpolation into HTML."""
    return escape("" if value is None else str(value), quote=True)


def _int(value: Any, default: int) -> int:
    """Best-effort int conversion; never raises on model/user supplied data."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _status(value: Any) -> str:
    """Normalize an unknown status onto the three supported card states."""
    state = str(value or "").strip()
    return state if state in _STATES else "miss"


def _columns(value: Any) -> int:
    """Clamp the grid column count so it can be embedded as a CSS variable."""
    return max(1, min(_int(value, DEFAULT_COLUMNS), MAX_COLUMNS))


def _direction(value: Any) -> str:
    """Normalize a direction onto ``up`` / ``down`` / ````."""
    direction = str(value or "").strip().lower()
    return direction if direction in _ARROWS else ""


def make_guess_card_view(
    *,
    category_label: str,
    category_icon: str,
    difficulty_label: str,
    attempt: int,
    max_attempts: int,
    remaining: int,
    guess_name: str,
    guess_name_en: str,
    feedback: list[dict[str, Any]] | None,
    status_text: str,
    columns: int = DEFAULT_COLUMNS,
) -> dict[str, Any]:
    """Convert domain feedback into localized, template-ready card data.

    The returned dict is plain JSON-serializable data (strings, ints, lists and
    dicts); ``build_guess_card_html`` is the only consumer.
    """
    tiles: list[dict[str, Any]] = []
    for item in feedback or []:
        if not isinstance(item, dict):
            continue
        state = _status(item.get("status"))
        direction = _direction(item.get("direction"))
        hits: list[str] = []
        raw_hits = item.get("hits")
        if isinstance(raw_hits, (list, tuple)):
            hits = [str(hit) for hit in raw_hits if str(hit).strip()]
        tiles.append(
            {
                "key": str(item.get("key") or ""),
                "label": str(item.get("label") or item.get("key") or "属性"),
                "value": str(item.get("value") if item.get("value") is not None else "—"),
                "status": state,
                "status_label": _STATES[state][0],
                "status_symbol": _STATES[state][1],
                "direction": direction,
                "arrow": _ARROWS.get(direction, ""),
                "hits": hits,
            }
        )
    return {
        "category_label": str(category_label or ""),
        "category_icon": str(category_icon or ""),
        "difficulty_label": str(difficulty_label or ""),
        "attempt": _int(attempt, 0),
        "max_attempts": _int(max_attempts, 0),
        "remaining": max(0, _int(remaining, 0)),
        "guess_name": str(guess_name or ""),
        "guess_name_en": str(guess_name_en or ""),
        "status_text": str(status_text or ""),
        "columns": _columns(columns),
        "tiles": tiles,
        "has_feedback": bool(tiles),
    }


_BASE_CSS = """
* { box-sizing: border-box; margin: 0; padding: 0; }
body {
  padding: 34px 24px 40px;
  background-color: #090d10;
  background-image:
    repeating-linear-gradient(0deg, rgba(255, 255, 255, .022) 0 3px, transparent 3px 6px),
    repeating-linear-gradient(90deg, rgba(0, 0, 0, .22) 0 6px, transparent 6px 12px);
  color: #e9f2e6;
  font-family: "Microsoft YaHei", "PingFang SC", "Noto Sans CJK SC", "Noto Sans SC", Arial, sans-serif;
  -webkit-font-smoothing: antialiased;
}
.board {
  position: relative;
  width: 1060px;
  margin: 0 auto;
  overflow: hidden;
  background: linear-gradient(180deg, #1e262c 0%, #161c21 58%, #12171b 100%);
  border: 4px solid #04070a;
  box-shadow: 0 0 0 2px #37452f, 0 0 0 6px #04070a, 0 26px 64px rgba(0, 0, 0, .66);
}
.grass {
  height: 20px;
  background:
    repeating-linear-gradient(90deg, #63b23f 0 14px, #4b9230 14px 26px, #71c34a 26px 34px, #4b9230 34px 44px);
  border-bottom: 3px solid #04070a;
}
.dirt {
  height: 12px;
  background:
    repeating-linear-gradient(90deg, #7a5330 0 10px, #64431f 10px 21px, #8a5f37 21px 30px, #64431f 30px 40px);
  border-bottom: 3px solid #04070a;
}
.titlebar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 26px;
  padding: 22px 30px 20px;
  background: linear-gradient(180deg, #242e34 0%, #1a2228 100%);
  border-bottom: 3px solid #04070a;
}
.eyebrow { margin-bottom: 8px; color: #8fa896; font-size: 15px; letter-spacing: .18em; }
.mode { display: flex; align-items: center; gap: 18px; min-width: 0; }
.mode-icon {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 74px;
  height: 74px;
  font-size: 42px;
  line-height: 1;
  background: linear-gradient(180deg, #38473a 0%, #263228 100%);
  border: 3px solid #050907;
  box-shadow: inset 0 0 0 3px #4d6049;
}
.mode-name {
  color: #f4f9ef;
  font-size: 36px;
  font-weight: 800;
  line-height: 1.12;
  letter-spacing: .05em;
  word-break: break-word;
  text-shadow: 0 3px 0 rgba(0, 0, 0, .55);
}
.meta { display: flex; flex-wrap: wrap; gap: 10px; margin-top: 11px; }
.chip {
  padding: 5px 13px;
  color: #cfe0c8;
  font-size: 16px;
  background: #26332b;
  border: 2px solid #0a0f0b;
  box-shadow: inset 0 -3px 0 rgba(0, 0, 0, .38);
}
.chip.diff { color: #ffe9a8; background: #3d3115; }
.counter {
  flex: none;
  min-width: 152px;
  padding: 11px 18px 12px;
  text-align: center;
  background: #1e2d1a;
  border: 3px solid #070c06;
  box-shadow: inset 0 0 0 3px #3f5c39, 0 6px 0 rgba(0, 0, 0, .42);
}
.counter small { display: block; color: #a9c39d; font-size: 14px; letter-spacing: .16em; }
.counter strong { display: block; color: #b9f08d; font-size: 40px; font-weight: 800; line-height: 1.1; }
.guessbar {
  display: flex;
  align-items: baseline;
  flex-wrap: wrap;
  gap: 14px;
  padding: 15px 30px;
  background: #182027;
  border-bottom: 3px solid #04070a;
}
.guessbar .label { color: #8fa596; font-size: 15px; letter-spacing: .16em; }
.guessbar .name { min-width: 0; color: #ffffff; font-size: 28px; font-weight: 700; word-break: break-word; }
.guessbar .en { min-width: 0; color: #93a89b; font-size: 17px; word-break: break-word; }
.footer {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 24px;
  padding: 16px 30px 18px;
  background: #13191d;
  border-top: 3px solid #04070a;
}
.legend { display: flex; flex-wrap: wrap; gap: 8px 16px; color: #a4b6a4; font-size: 15px; word-break: break-word; }
.legend b { color: #dbe8d5; font-weight: 600; }
.status {
  flex: none;
  max-width: 540px;
  padding: 10px 16px;
  color: #f0f7ea;
  font-size: 17px;
  text-align: right;
  background: #1f2a20;
  border: 2px solid #0a0f09;
  box-shadow: inset 0 0 0 2px #3c5137;
  word-break: break-word;
  overflow-wrap: anywhere;
}
"""

_GUESS_CSS = """
.grid {
  display: grid;
  grid-template-columns: repeat(var(--cols, 4), minmax(0, 1fr));
  gap: 14px;
  padding: 22px 30px 26px;
}
.tile {
  display: flex;
  flex-direction: column;
  min-height: 136px;
  padding: 14px 16px 15px;
  background: linear-gradient(180deg, #2b353c 0%, #222a31 100%);
  border: 3px solid #0b0f12;
  box-shadow: inset 0 0 0 2px #465561, 0 5px 0 rgba(0, 0, 0, .3);
}
.tile.exact {
  background: linear-gradient(180deg, #2c6b3f 0%, #1e5030 100%);
  box-shadow: inset 0 0 0 2px #6fce86, 0 5px 0 rgba(0, 0, 0, .36);
}
.tile.close {
  background: linear-gradient(180deg, #6d5318 0%, #523d0f 100%);
  box-shadow: inset 0 0 0 2px #e2bd48, 0 5px 0 rgba(0, 0, 0, .36);
}
.tile.miss {
  background: linear-gradient(180deg, #333b42 0%, #262d33 100%);
  box-shadow: inset 0 0 0 2px #55636e, 0 5px 0 rgba(0, 0, 0, .36);
}
.tile-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  color: #dbe7d3;
  font-size: 17px;
  letter-spacing: .04em;
}
.state { flex: none; font-size: 14px; color: #c9d6c3; }
.tile.exact .state { color: #bdf5c8; }
.tile.close .state { color: #ffe29a; }
.tile.miss .state { color: #b9c6cf; }
.tile-value {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 13px;
  color: #ffffff;
  font-size: 28px;
  font-weight: 800;
  line-height: 1.16;
  word-break: break-word;
}
.tile-value .txt { min-width: 0; }
.dir { flex: none; font-size: 27px; font-weight: 800; line-height: 1; }
.dir.up { color: #a8f09b; }
.dir.down { color: #ffc08a; }
.hits { display: flex; flex-wrap: wrap; gap: 6px; margin-top: auto; padding-top: 12px; }
.hits .lead { color: #d5e4cd; font-size: 13px; letter-spacing: .1em; align-self: center; }
.hit {
  padding: 2px 9px;
  color: #10200f;
  font-size: 14px;
  background: #a8e885;
  border: 2px solid #0a1408;
}
.tile.exact .hit { background: #d3f9b7; }
.empty {
  grid-column: 1 / -1;
  padding: 46px 24px 48px;
  text-align: center;
  background: linear-gradient(180deg, #20282e 0%, #1a2126 100%);
  border: 3px dashed #3d4a53;
}
.empty .big { color: #c5d4c0; font-size: 26px; font-weight: 700; }
.empty .hint { margin-top: 11px; color: #8ea292; font-size: 16px; }
"""

_HELP_CSS = """
.titlebar.help-head { align-items: flex-start; }
.subtitle { margin-top: 10px; color: #aebfae; font-size: 18px; line-height: 1.45; word-break: break-word; }
.commands {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 15px;
  padding: 24px 30px 28px;
}
.cmd {
  min-height: 150px;
  padding: 16px 18px 17px;
  background: linear-gradient(180deg, #263129 0%, #1d2620 100%);
  border: 3px solid #0a0f0b;
  box-shadow: inset 0 0 0 2px #445442, 0 5px 0 rgba(0, 0, 0, .3);
}
.cmd-head { display: flex; align-items: center; justify-content: space-between; min-height: 26px; }
.tag {
  padding: 3px 10px;
  color: #dff3c9;
  font-size: 14px;
  letter-spacing: .06em;
  background: #33452c;
  border: 2px solid #0a0f0b;
}
.cmd code {
  display: block;
  margin-top: 14px;
  color: #ffffff;
  font-family: ui-monospace, SFMono-Regular, Consolas, "Courier New", monospace;
  font-size: 23px;
  font-weight: 700;
  line-height: 1.25;
  word-break: break-word;
}
.cmd p { margin-top: 12px; color: #b7c7b4; font-size: 16px; line-height: 1.5; word-break: break-word; overflow-wrap: anywhere; }
.commands .empty { grid-column: 1 / -1; }
"""

_PAGE = """<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=1108">
<title>{title}</title>
<style>{css}</style>
</head>
<body>
{body}
</body>
</html>"""


def _page(title: str, css: str, body: str) -> str:
    return _PAGE.format(title=title, css=css, body=body)


def _tile_html(tile: dict[str, Any]) -> str:
    state = _status(tile.get("status"))
    label = _text(tile.get("label") or "属性")
    value = _text(tile.get("value"))
    status_label = _text(_STATES[state][0])
    status_symbol = _text(_STATES[state][1])
    arrow = _text(tile.get("arrow"))
    direction = _direction(tile.get("direction"))
    arrow_html = f'<span class="dir {direction}">{arrow}</span>' if arrow else ""
    hits: list[str] = []
    raw_hits = tile.get("hits")
    if isinstance(raw_hits, (list, tuple)):
        hits = [_text(hit) for hit in raw_hits if str(hit).strip()]
    hits_html = ""
    if hits:
        tags = "".join(f'<span class="hit">{hit}</span>' for hit in hits)
        hits_html = f'<div class="hits"><span class="lead">命中</span>{tags}</div>'
    return (
        f'<article class="tile {state}">'
        f'<div class="tile-head"><span>{label}</span>'
        f'<span class="state">{status_symbol} {status_label}</span></div>'
        f'<div class="tile-value"><span class="txt">{value}</span>{arrow_html}</div>'
        f"{hits_html}"
        f"</article>"
    )


def build_guess_card_html(view: dict[str, Any]) -> str:
    """Build a self-contained, injection-safe guess feedback card page."""
    view = view if isinstance(view, dict) else {}
    columns = _columns(view.get("columns"))

    icon = _text(view.get("category_icon"))
    category = _text(view.get("category_label") or "猜谜")
    difficulty = _text(view.get("difficulty_label") or "普通")
    attempt = _text(view.get("attempt", 0))
    max_attempts = _text(view.get("max_attempts", 0))
    remaining = _text(view.get("remaining", 0))
    status_text = _text(view.get("status_text"))
    guess_name = _text(view.get("guess_name"))
    guess_name_en = _text(view.get("guess_name_en"))

    tiles = [tile for tile in view.get("tiles") or [] if isinstance(tile, dict)]
    if tiles:
        grid = "".join(_tile_html(tile) for tile in tiles)
    else:
        grid = (
            '<div class="empty"><div class="big">本次还没有可展示的属性反馈</div>'
            '<div class="hint">猜中一个名字后，这里会逐项列出它与答案的对比结果。</div></div>'
        )

    guessbar = ""
    if guess_name or guess_name_en:
        en_html = f'<span class="en">{guess_name_en}</span>' if guess_name_en else ""
        guessbar = (
            '<div class="guessbar"><span class="label">本次猜测</span>'
            f'<span class="name">{guess_name or "—"}</span>{en_html}</div>'
        )

    status_html = status_text or "继续猜猜看吧"
    body = f"""<section class="board">
  <div class="grass"></div>
  <div class="dirt"></div>
  <header class="titlebar">
    <div class="mode">
      <div class="mode-icon">{icon}</div>
      <div>
        <p class="eyebrow">MINECRAFT 猜谜 · MCGUESS</p>
        <h1 class="mode-name">{category}</h1>
        <div class="meta">
          <span class="chip diff">难度 {difficulty}</span>
          <span class="chip">第 {attempt}/{max_attempts} 次</span>
        </div>
      </div>
    </div>
    <div class="counter"><small>剩余次数</small><strong>{remaining}</strong></div>
  </header>
  {guessbar}
  <main class="grid" style="--cols: {columns}">{grid}</main>
  <footer class="footer">
    <div class="legend">
      <span>🟩 <b>完全相同</b></span>
      <span>🟨 <b>接近</b></span>
      <span>⬜ <b>不匹配</b></span>
      <span>⬆⬇ <b>答案更大 / 更小</b></span>
    </div>
    <div class="status">{status_html}</div>
  </footer>
</section>"""
    return _page("MCGuess · " + category, _BASE_CSS + _GUESS_CSS, body)


def build_help_card_html(view: dict[str, Any]) -> str:
    """Build an injection-safe command reference card page."""
    if isinstance(view, list):  # tolerate a bare command list
        view = {"commands": view}
    view = view if isinstance(view, dict) else {}

    title = _text(view.get("title") or "MCGuess · 我的世界猜谜")
    subtitle = _text(view.get("subtitle"))
    footer = _text(view.get("footer"))

    cards: list[str] = []
    for item in view.get("commands") or []:
        if not isinstance(item, dict):
            continue
        tag = _text(item.get("tag"))
        command = _text(item.get("command"))
        description = _text(item.get("description"))
        description_html = f"<p>{description}</p>" if description else ""
        head_html = f'<div class="cmd-head"><span class="tag">{tag}</span></div>' if tag else ""
        cards.append(
            '<article class="cmd">'
            f"{head_html}"
            f"<code>{command}</code>{description_html}"
            "</article>"
        )
    if cards:
        grid = "".join(cards)
    else:
        grid = '<div class="empty"><div class="big">暂无可用指令</div></div>'

    subtitle_html = f'<p class="subtitle">{subtitle}</p>' if subtitle else ""
    if footer:
        # The caller-provided footer is usually the legend itself; do not duplicate it.
        footer_html = f'<div class="legend">{footer}</div>'
    else:
        footer_html = (
            '<div class="legend">'
            "<span>🟩 <b>完全相同</b></span>"
            "<span>🟨 <b>接近</b></span>"
            "<span>⬜ <b>不匹配</b></span>"
            '<span>⬆⬇ <b>答案更大 / 更小</b></span>'
            "</div>"
        )
    body = f"""<section class="board">
  <div class="grass"></div>
  <div class="dirt"></div>
  <header class="titlebar help-head">
    <div>
      <p class="eyebrow">MCGUESS · COMMAND GUIDE</p>
      <h1 class="mode-name">{title}</h1>
      {subtitle_html}
    </div>
  </header>
  <main class="commands">{grid}</main>
  <footer class="footer">
    {footer_html}
  </footer>
</section>"""
    return _page(title, _BASE_CSS + _HELP_CSS, body)
