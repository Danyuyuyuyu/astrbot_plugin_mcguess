"""Unit tests for the local Playwright renderer, using a fake Playwright module."""

from __future__ import annotations

import asyncio
import sys
import types

import pytest

from astrbot_plugin_mcguess import renderer as renderer_module
from astrbot_plugin_mcguess.renderer import (
    LocalCardRenderer,
    LocalRenderSettings,
    RenderError,
)


def run(coro):
    return asyncio.run(coro)


class FakeRoute:
    def __init__(self) -> None:
        self.aborted = False

    async def abort(self) -> None:
        self.aborted = True


class FakePage:
    def __init__(self, image: bytes = b"IMAGE", *, fail_content: bool = False, fail_shot: bool = False):
        self.image = image
        self.fail_content = fail_content
        self.fail_shot = fail_shot
        self.route_handler = None
        self.content = None
        self.wait_until = None
        self.timeout = None
        self.shot_kwargs = None
        self.closed = False

    async def route(self, pattern, handler) -> None:
        self.route_handler = handler

    async def set_content(self, html: str, *, wait_until=None, timeout=None) -> None:
        if self.fail_content:
            raise RuntimeError("set_content boom")
        self.content = html
        self.wait_until = wait_until
        self.timeout = timeout

    async def evaluate(self, expression: str):
        return True

    async def screenshot(self, **kwargs):
        self.shot_kwargs = kwargs
        if self.fail_shot:
            raise RuntimeError("screenshot boom")
        return self.image

    async def close(self) -> None:
        self.closed = True


class FakeContext:
    def __init__(self, pages: list[FakePage]) -> None:
        self.pages = pages
        self.closed = False
        self.kwargs = None

    async def new_page(self) -> FakePage:
        return self.pages.pop(0) if len(self.pages) > 1 else self.pages[0]

    async def close(self) -> None:
        self.closed = True


class FakeBrowser:
    def __init__(self, context: FakeContext) -> None:
        self.context = context
        self.closed = False

    def is_connected(self) -> bool:
        return not self.closed

    async def new_context(self, **kwargs) -> FakeContext:
        self.context.kwargs = kwargs
        return self.context

    async def close(self) -> None:
        self.closed = True


class FakeChromium:
    def __init__(self, browser: FakeBrowser, *, fail_launches: int = 0) -> None:
        self.browser = browser
        self.fail_launches = fail_launches
        self.calls: list[dict] = []

    async def launch(self, **kwargs) -> FakeBrowser:
        self.calls.append(kwargs)
        if len(self.calls) <= self.fail_launches:
            raise RuntimeError("Failed to launch browser")
        return self.browser


class FakePlaywright:
    def __init__(self, chromium: FakeChromium) -> None:
        self.chromium = chromium
        self.stopped = False

    async def stop(self) -> None:
        self.stopped = True


class FakeManager:
    def __init__(self, playwright: FakePlaywright) -> None:
        self.playwright = playwright
        self.started = False

    async def start(self) -> FakePlaywright:
        self.started = True
        return self.playwright


def install_fake_playwright(monkeypatch, chromium: FakeChromium) -> FakeManager:
    playwright = FakePlaywright(chromium)
    manager = FakeManager(playwright)
    package = types.ModuleType("playwright")
    async_api = types.ModuleType("playwright.async_api")
    async_api.async_playwright = lambda: manager  # type: ignore[attr-defined]
    package.async_api = async_api  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "playwright", package)
    monkeypatch.setitem(sys.modules, "playwright.async_api", async_api)
    return manager


def make_renderer(monkeypatch, **settings) -> tuple[LocalCardRenderer, FakePage, FakeContext, FakeBrowser, FakeChromium, FakeManager]:
    page = FakePage()
    context = FakeContext([page])
    browser = FakeBrowser(context)
    chromium = FakeChromium(browser)
    manager = install_fake_playwright(monkeypatch, chromium)
    monkeypatch.setattr(renderer_module, "browser_candidates", lambda: [])
    renderer = LocalCardRenderer(LocalRenderSettings(**settings))
    return renderer, page, context, browser, chromium, manager


# ---- settings --------------------------------------------------------------
def test_settings_are_normalized():
    settings = LocalRenderSettings(
        image_format="WEBP",
        quality=500,
        scale=9,
        viewport_width=10,
        browser_path="  /usr/bin/chromium  ",
    ).normalized()
    assert settings.image_format == "jpeg"
    assert settings.quality == 95
    assert settings.scale == 2.0
    assert settings.viewport_width == 600
    assert settings.browser_path == "/usr/bin/chromium"


def test_is_supported_uses_find_spec(monkeypatch):
    monkeypatch.setattr("importlib.util.find_spec", lambda name: None)
    assert LocalCardRenderer.is_supported() is False
    monkeypatch.setattr("importlib.util.find_spec", lambda name: object())
    assert LocalCardRenderer.is_supported() is True


# ---- rendering -------------------------------------------------------------
def test_render_jpeg_records_screenshot_options(monkeypatch):
    renderer, page, _context, _browser, _chromium, _manager = make_renderer(monkeypatch)
    try:
        data = run(renderer.render("<html>card</html>"))
    finally:
        run(renderer.close())
    assert data == b"IMAGE"
    assert page.content == "<html>card</html>"
    assert page.wait_until == "load"
    assert page.shot_kwargs == {
        "full_page": True,
        "animations": "disabled",
        "type": "jpeg",
        "quality": 80,
        "timeout": 20000.0,
    }


def test_render_png_omits_quality(monkeypatch):
    renderer, page, *_ = make_renderer(monkeypatch, image_format="png", quality=60)
    try:
        run(renderer.render("<html/>"))
    finally:
        run(renderer.close())
    assert page.shot_kwargs["type"] == "png"
    assert "quality" not in page.shot_kwargs


def test_render_blocks_external_requests(monkeypatch):
    renderer, page, *_ = make_renderer(monkeypatch)
    try:
        run(renderer.start())
        route = FakeRoute()
        run(page.route_handler(route))
    finally:
        run(renderer.close())
    assert route.aborted is True


def test_launch_retries_without_sandbox(monkeypatch):
    page = FakePage()
    context = FakeContext([page])
    browser = FakeBrowser(context)
    chromium = FakeChromium(browser, fail_launches=1)
    install_fake_playwright(monkeypatch, chromium)
    monkeypatch.setattr(renderer_module, "browser_candidates", lambda: [])
    renderer = LocalCardRenderer()
    try:
        run(renderer.render("<html/>"))
    finally:
        run(renderer.close())
    assert len(chromium.calls) == 2
    assert "--no-sandbox" in chromium.calls[1]["args"]
    assert chromium.calls[1]["headless"] is True


def test_launch_failure_raises_render_error(monkeypatch):
    page = FakePage()
    browser = FakeBrowser(FakeContext([page]))
    chromium = FakeChromium(browser, fail_launches=2)
    install_fake_playwright(monkeypatch, chromium)
    monkeypatch.setattr(renderer_module, "browser_candidates", lambda: [])
    renderer = LocalCardRenderer()
    with pytest.raises(RenderError, match="无法启动 Chromium"):
        run(renderer.render("<html/>"))
    run(renderer.close())


def test_auto_detects_cached_chromium_when_default_build_is_missing(monkeypatch):
    class RefusingChromium(FakeChromium):
        async def launch(self, **kwargs):
            self.calls.append(kwargs)
            if kwargs.get("executable_path") != "/cached/chromium/chrome":
                raise RuntimeError("Executable doesn't exist")
            return self.browser

    page = FakePage()
    browser = FakeBrowser(FakeContext([page]))
    chromium = RefusingChromium(browser)
    install_fake_playwright(monkeypatch, chromium)
    monkeypatch.setattr(renderer_module, "browser_candidates", lambda: ["/cached/chromium/chrome"])
    renderer = LocalCardRenderer()
    try:
        assert run(renderer.render("<html/>")) == b"IMAGE"
    finally:
        run(renderer.close())
    assert any(call.get("executable_path") == "/cached/chromium/chrome" for call in chromium.calls)


def test_missing_configured_browser_path_falls_back_to_default(monkeypatch):
    page = FakePage()
    browser = FakeBrowser(FakeContext([page]))
    chromium = FakeChromium(browser)
    install_fake_playwright(monkeypatch, chromium)
    monkeypatch.setattr(renderer_module, "browser_candidates", lambda: [])
    renderer = LocalCardRenderer(LocalRenderSettings(browser_path="/definitely/missing/chrome"))
    try:
        assert run(renderer.render("<html/>")) == b"IMAGE"
    finally:
        run(renderer.close())
    assert "executable_path" not in chromium.calls[0]


def test_configured_browser_path_is_used(monkeypatch, tmp_dir):
    executable = tmp_dir / "chrome"
    executable.write_text("fake chromium", encoding="utf-8")
    page = FakePage()
    browser = FakeBrowser(FakeContext([page]))
    chromium = FakeChromium(browser)
    install_fake_playwright(monkeypatch, chromium)
    monkeypatch.setattr(renderer_module, "browser_candidates", lambda: [])
    renderer = LocalCardRenderer(LocalRenderSettings(browser_path=str(executable)))
    try:
        run(renderer.render("<html/>"))
    finally:
        run(renderer.close())
    assert chromium.calls[0]["executable_path"] == str(executable)


def test_browser_candidates_prefers_newest_full_chromium(monkeypatch, tmp_dir):
    cache = tmp_dir / "ms-playwright"
    full_new = cache / "chromium-1243" / "chrome-linux64" / "chrome"
    full_new.parent.mkdir(parents=True)
    full_new.write_text("x", encoding="utf-8")
    shell_old = cache / "chromium_headless_shell-1200" / "chrome-headless-shell-linux64" / "chrome-headless-shell"
    shell_old.parent.mkdir(parents=True)
    shell_old.write_text("x", encoding="utf-8")
    monkeypatch.setattr(renderer_module, "BROWSER_CACHE_DIRS", (cache,))
    monkeypatch.delenv("PLAYWRIGHT_BROWSERS_PATH", raising=False)
    candidates = renderer_module.browser_candidates()
    assert candidates[0] == str(full_new)
    assert str(shell_old) in candidates


def test_render_failure_is_wrapped_and_page_is_recreated(monkeypatch):
    broken = FakePage(fail_shot=True)
    healthy = FakePage(image=b"SECOND")
    context = FakeContext([broken, healthy])
    browser = FakeBrowser(context)
    install_fake_playwright(monkeypatch, FakeChromium(browser))
    monkeypatch.setattr(renderer_module, "browser_candidates", lambda: [])
    renderer = LocalCardRenderer()
    try:
        with pytest.raises(RenderError, match="本地渲染失败"):
            run(renderer.render("<html/>"))
        assert broken.closed is True
        assert run(renderer.render("<html/>")) == b"SECOND"
    finally:
        run(renderer.close())


def test_close_releases_all_resources_and_is_idempotent(monkeypatch):
    renderer, page, context, browser, _chromium, manager = make_renderer(monkeypatch)
    run(renderer.start())
    run(renderer.close())
    run(renderer.close())
    assert page.closed is True
    assert context.closed is True
    assert browser.closed is True
    assert manager.started is True
    assert manager.playwright.stopped is True
    with pytest.raises(RenderError, match="已关闭"):
        run(renderer.render("<html/>"))


def test_missing_playwright_package_raises_render_error(monkeypatch):
    monkeypatch.setitem(sys.modules, "playwright", None)
    monkeypatch.setitem(sys.modules, "playwright.async_api", None)
    renderer = LocalCardRenderer()
    with pytest.raises(RenderError, match="未安装 playwright"):
        run(renderer.render("<html/>"))
