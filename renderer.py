"""Local headless-Chromium card renderer built on Playwright.

This module has **no AstrBot imports**: it only turns an HTML string into
PNG/JPEG bytes, so it can be unit-tested with a fake Playwright module and does
not break plugin loading when the browser stack is unavailable. Backend
selection, timeouts and fallbacks live in :mod:`render_service`.
"""

from __future__ import annotations

import asyncio
import importlib.util
import os
import re
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

RENDER_FORMATS = ("jpeg", "png")
DEFAULT_VIEWPORT_WIDTH = 800  # matches card_renderer.CARD_WIDTH
DEFAULT_VIEWPORT_HEIGHT = 240
DEFAULT_PAGE_TIMEOUT = 20.0
MIN_QUALITY = 30
MAX_QUALITY = 95
MIN_SCALE = 0.5
MAX_SCALE = 2.0
MAX_AUTO_BROWSER_CANDIDATES = 4

# Chromium switches that are safe on desktop and helpful in containers/headless.
LAUNCH_ARGS = (
    "--disable-dev-shm-usage",
    "--disable-gpu",
    "--hide-scrollbars",
    "--force-color-profile=srgb",
)
NO_SANDBOX_ARGS = ("--no-sandbox",)

# Playwright stores downloaded browsers in a per-OS cache; a venv upgrade can
# leave the package expecting a newer build than the one already on disk, so we
# discover any usable executable as a fallback.
BROWSER_CACHE_DIRS = (
    Path.home() / ".cache" / "ms-playwright",
    Path.home() / "Library" / "Caches" / "ms-playwright",
    Path(os.environ.get("LOCALAPPDATA", "")) / "ms-playwright",
)
BROWSER_PATTERNS = (
    "chromium-*/chrome-linux64/chrome",
    "chromium-*/chrome-linux/chrome",
    "chromium-*/chrome-win/chrome.exe",
    "chromium-*/chrome-win64/chrome.exe",
    "chromium-*/chrome-mac/Chromium.app/Contents/MacOS/Chromium",
    "chromium_headless_shell-*/chrome-headless-shell-linux64/chrome-headless-shell",
    "chromium_headless_shell-*/chrome-headless-shell-win64/chrome-headless-shell.exe",
    "chromium_headless_shell-*/chrome-headless-shell-mac*/chrome-headless-shell",
)


def browser_candidates() -> list[str]:
    """Return locally cached Chromium executables, newest build first."""
    roots: list[Path] = []
    override = os.environ.get("PLAYWRIGHT_BROWSERS_PATH", "").strip()
    if override and override not in {"0", "1"}:
        roots.append(Path(override))
    roots.extend(BROWSER_CACHE_DIRS)

    found: list[Path] = []
    seen: set[str] = set()
    for root in roots:
        if not root.is_dir():
            continue
        for pattern in BROWSER_PATTERNS:
            for path in root.glob(pattern):
                if not path.is_file():
                    continue
                key = str(path.resolve())
                if key in seen:
                    continue
                seen.add(key)
                found.append(path)

    def sort_key(path: Path) -> tuple[int, int]:
        match = re.search(r"chromium(?:-headless_shell)?-(\d+)", str(path))
        build = int(match.group(1)) if match else 0
        is_headless_shell = "chrome-headless-shell" in path.name
        return (-build, 1 if is_headless_shell else 0)

    return [str(path) for path in sorted(found, key=sort_key)]


class RenderError(RuntimeError):
    """Raised when the local browser cannot produce an image."""


@dataclass(frozen=True)
class LocalRenderSettings:
    """Knobs for the local renderer; unknown values are clamped by ``normalized``."""

    image_format: str = "jpeg"
    quality: int = 80
    scale: float = 1.0
    viewport_width: int = DEFAULT_VIEWPORT_WIDTH
    browser_path: str = ""
    page_timeout: float = DEFAULT_PAGE_TIMEOUT

    def normalized(self) -> "LocalRenderSettings":
        image_format = str(self.image_format).strip().lower()
        if image_format not in RENDER_FORMATS:
            image_format = "jpeg"
        try:
            quality = int(self.quality)
        except (TypeError, ValueError):
            quality = 80
        try:
            scale = float(self.scale)
        except (TypeError, ValueError):
            scale = 1.0
        try:
            viewport_width = int(self.viewport_width)
        except (TypeError, ValueError):
            viewport_width = DEFAULT_VIEWPORT_WIDTH
        try:
            page_timeout = float(self.page_timeout)
        except (TypeError, ValueError):
            page_timeout = DEFAULT_PAGE_TIMEOUT
        return replace(
            self,
            image_format=image_format,
            quality=max(MIN_QUALITY, min(MAX_QUALITY, quality)),
            scale=max(MIN_SCALE, min(MAX_SCALE, scale)),
            viewport_width=max(600, min(2000, viewport_width)),
            browser_path=str(self.browser_path or "").strip(),
            page_timeout=max(2.0, min(120.0, page_timeout)),
        )


class LocalCardRenderer:
    """A lazily started, reused Chromium page that screenshots card HTML."""

    def __init__(
        self,
        settings: LocalRenderSettings | None = None,
        *,
        logger: Any,
    ) -> None:
        self._settings = (settings or LocalRenderSettings()).normalized()
        self._logger = logger
        self._playwright: Any = None
        self._browser: Any = None
        self._context: Any = None
        self._page: Any = None
        self._render_lock = asyncio.Lock()
        self._start_lock = asyncio.Lock()
        self._closed = False

    # -- capabilities ---------------------------------------------------
    @staticmethod
    def is_supported() -> bool:
        """Whether the Playwright package is importable (browser may still be absent)."""
        return importlib.util.find_spec("playwright") is not None

    @property
    def settings(self) -> LocalRenderSettings:
        return self._settings

    @property
    def is_running(self) -> bool:
        if self._browser is None:
            return False
        try:
            return bool(self._browser.is_connected())
        except Exception:  # noqa: BLE001 - a broken browser is "not running"
            return False

    # -- lifecycle ------------------------------------------------------
    async def start(self) -> None:
        """Start Chromium and create the reusable page (idempotent)."""
        async with self._start_lock:
            if self.is_running and self._page is not None:
                return
            await self._start_locked()

    async def _start_locked(self) -> None:
        if self._closed:
            raise RenderError("本地卡片渲染器已关闭。")
        try:
            from playwright.async_api import async_playwright
        except ImportError as exc:
            raise RenderError("未安装 playwright，无法使用本地渲染。") from exc

        if self._playwright is None:
            self._playwright = await async_playwright().start()

        self._browser = await self._launch_browser()

        self._context = await self._browser.new_context(
            viewport={
                "width": self._settings.viewport_width,
                "height": DEFAULT_VIEWPORT_HEIGHT,
            },
            device_scale_factor=self._settings.scale,
            locale="zh-CN",
        )
        self._page = await self._context.new_page()
        # Cards are fully self-contained; block any accidental network access.
        await self._page.route("**/*", self._block_request)
        self._logger.info(
            "本地卡片渲染器已启动（%s, quality=%s, scale=%s, width=%s）。",
            self._settings.image_format,
            self._settings.quality,
            self._settings.scale,
            self._settings.viewport_width,
        )

    @staticmethod
    async def _block_request(route: Any) -> None:
        try:
            await route.abort()
        except Exception:  # noqa: BLE001 - a closed page during teardown
            pass

    def _launch_attempts(self) -> list[tuple[str | None, str]]:
        """Executables to try, in order: configured path, default, cached browsers."""
        attempts: list[tuple[str | None, str]] = []
        seen: set[str] = set()
        configured = self._settings.browser_path
        if configured:
            path = Path(configured).expanduser()
            if path.is_file():
                seen.add(str(path.resolve()))
                attempts.append((str(path), "browser_path"))
            else:
                self._logger.warning("browser_path 不存在：%s，改为自动探测浏览器。", configured)
        attempts.append((None, "Playwright 默认浏览器"))
        for candidate in browser_candidates()[:MAX_AUTO_BROWSER_CANDIDATES]:
            key = str(Path(candidate).resolve())
            if key in seen:
                continue
            seen.add(key)
            attempts.append((candidate, "自动探测的 Chromium"))
        return attempts

    async def _launch_browser(self) -> Any:
        """Launch Chromium, retrying without the sandbox and with cached builds.

        A Playwright upgrade can leave the package expecting a browser build that
        is not on disk (e.g. the venv moved to 1.64 while ``chromium-1243`` is
        installed). In that case we fall back to the newest cached executable.
        """
        attempts = self._launch_attempts()
        last_error: Exception | None = None
        for sandbox_args in ((), NO_SANDBOX_ARGS):
            for executable, label in attempts:
                kwargs: dict[str, Any] = {
                    "headless": True,
                    "args": [*LAUNCH_ARGS, *sandbox_args],
                }
                if executable:
                    kwargs["executable_path"] = executable
                try:
                    browser = await self._playwright.chromium.launch(**kwargs)
                except Exception as exc:  # noqa: BLE001 - try the next executable
                    last_error = exc
                    continue
                suffix = "，--no-sandbox" if sandbox_args else ""
                self._logger.info("本地卡片渲染器启动 Chromium（%s%s）。", label, suffix)
                return browser
        raise RenderError(
            f"无法启动 Chromium（{type(last_error).__name__}: {last_error}）",
        ) from last_error

    async def render(self, html: str) -> bytes:
        """Render one HTML document and return the encoded image bytes."""
        async with self._render_lock:
            await self.start()
            page = self._page
            if page is None:
                raise RenderError("本地卡片渲染器尚未就绪。")
            timeout_ms = self._settings.page_timeout * 1000
            try:
                await page.set_content(html, wait_until="load", timeout=timeout_ms)
                await page.evaluate("document.fonts.ready.then(() => true)")
                options: dict[str, Any] = {
                    "full_page": True,
                    "animations": "disabled",
                    "type": self._settings.image_format,
                    "timeout": timeout_ms,
                }
                if self._settings.image_format == "jpeg":
                    options["quality"] = self._settings.quality
                data = await page.screenshot(**options)
            except Exception as exc:  # noqa: BLE001
                await self._recreate_page()
                raise RenderError(f"本地渲染失败：{type(exc).__name__}: {exc}") from exc
            if not isinstance(data, (bytes, bytearray)) or not data:
                raise RenderError("本地渲染没有返回图片数据。")
            return bytes(data)

    async def _recreate_page(self) -> None:
        """Recover from a broken page without tearing the browser down."""
        page, self._page = self._page, None
        if page is not None:
            try:
                await page.close()
            except Exception:  # noqa: BLE001
                pass
        if self._context is None or not self.is_running:
            return
        try:
            self._page = await self._context.new_page()
            await self._page.route("**/*", self._block_request)
        except Exception:  # noqa: BLE001 - next render retries the full start
            self._page = None

    async def close(self) -> None:
        """Shut the browser stack down; safe to call repeatedly."""
        self._closed = True
        async with self._start_lock:
            page, self._page = self._page, None
            context, self._context = self._context, None
            browser, self._browser = self._browser, None
            playwright, self._playwright = self._playwright, None
            for resource, method in (
                (page, "close"),
                (context, "close"),
                (browser, "close"),
                (playwright, "stop"),
            ):
                if resource is None:
                    continue
                try:
                    await getattr(resource, method)()
                except Exception:  # noqa: BLE001 - teardown must not raise
                    pass
