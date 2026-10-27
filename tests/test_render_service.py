"""Unit tests for the card rendering orchestration service."""

from __future__ import annotations

import asyncio

import pytest

from astrbot_plugin_mcguess.render_service import (
    BACKEND_LOCAL,
    BACKEND_REMOTE,
    CardRenderService,
    RenderSettings,
    RenderedCard,
)
from astrbot_plugin_mcguess.renderer import RenderError


def run(coro):
    return asyncio.run(coro)


class FakeLogger:
    """No-op stand-in for the plugin's AstrBot logger."""

    def warning(self, *args: object, **kwargs: object) -> None:
        pass


class FakeLocal:
    def __init__(self, *results: object) -> None:
        self.results = list(results)
        self.calls = 0
        self.closed = False

    async def render(self, html: str) -> bytes:
        self.calls += 1
        item = self.results.pop(0) if self.results else b"IMAGE"
        if isinstance(item, BaseException):
            raise item
        assert isinstance(item, bytes)
        return item

    async def close(self) -> None:
        self.closed = True


class SlowLocal(FakeLocal):
    def __init__(self, delay: float) -> None:
        super().__init__(b"SLOW")
        self.delay = delay

    async def render(self, html: str) -> bytes:
        self.calls += 1
        await asyncio.sleep(self.delay)
        return b"SLOW"


class FakeRemote:
    def __init__(self, *results: object) -> None:
        self.results = list(results)
        self.calls = 0

    async def __call__(self, html: str) -> str:
        self.calls += 1
        item = self.results.pop(0) if self.results else "https://img.example/card.png"
        if isinstance(item, BaseException):
            raise item
        assert isinstance(item, str)
        return item


def service(**kwargs) -> CardRenderService:
    settings = RenderSettings(**kwargs.pop("settings", {}))
    return CardRenderService(settings, logger=FakeLogger(), **kwargs)


# ---- backend planning ------------------------------------------------------
def test_auto_prefers_local_backend():
    local = FakeLocal(b"IMAGE")
    remote = FakeRemote("https://img")
    card = run(service(local=local, remote=remote).render("<html/>"))
    assert card is not None
    assert card.backend == BACKEND_LOCAL
    assert card.image == b"IMAGE" and card.image_url is None
    assert local.calls == 1 and remote.calls == 0


def test_local_failure_falls_back_to_remote():
    local = FakeLocal(RenderError("browser exploded"))
    remote = FakeRemote("https://img.example/card.png")
    card = run(service(local=local, remote=remote).render("<html/>"))
    assert card is not None
    assert card.backend == BACKEND_REMOTE
    assert card.image is None and card.image_url == "https://img.example/card.png"
    assert local.calls == 1 and remote.calls == 1


def test_all_backends_failing_returns_none():
    local = FakeLocal(RenderError("local down"))
    remote = FakeRemote(RuntimeError("remote down"))
    renderer = service(local=local, remote=remote)
    assert run(renderer.render("<html/>")) is None
    assert local.calls == 1 and remote.calls == 1


def test_remote_only_backend_never_touches_local():
    local = FakeLocal(b"IMAGE")
    remote = FakeRemote("https://img")
    card = run(service(settings={"backend": "remote"}, local=local, remote=remote).render("<html/>"))
    assert card is not None and card.backend == BACKEND_REMOTE
    assert local.calls == 0


def test_local_without_fallback_returns_none():
    local = FakeLocal(RenderError("local down"))
    remote = FakeRemote("https://img")
    card = run(
        service(
            settings={"backend": "local", "remote_fallback": False},
            local=local,
            remote=remote,
        ).render("<html/>")
    )
    assert card is None
    assert remote.calls == 0


def test_auto_without_local_uses_remote():
    remote = FakeRemote("https://img")
    card = run(service(local=None, remote=remote).render("<html/>"))
    assert card is not None and card.backend == BACKEND_REMOTE
    assert remote.calls == 1


# ---- circuit breaker -------------------------------------------------------
def test_breaker_skips_failed_backend_until_cooldown():
    local = FakeLocal(RenderError("boom"))
    remote = FakeRemote("https://img1", "https://img2")
    renderer = service(settings={"block_seconds": 60}, local=local, remote=remote)

    first = run(renderer.render("<html/>"))
    second = run(renderer.render("<html/>"))
    assert first is not None and second is not None
    assert first.backend == BACKEND_REMOTE and second.backend == BACKEND_REMOTE
    assert local.calls == 1
    assert remote.calls == 2
    assert renderer.is_blocked(BACKEND_LOCAL) is True


def test_breaker_snapshot_reports_state():
    local = FakeLocal(RenderError("boom"))
    renderer = service(local=local, remote=FakeRemote("https://img"))
    run(renderer.render("<html/>"))
    snapshot = renderer.snapshot()
    assert snapshot["failures"][BACKEND_LOCAL] == 1
    assert snapshot["blocked"][BACKEND_LOCAL] is True


# ---- timeouts --------------------------------------------------------------
def test_local_timeout_falls_back_to_remote():
    local = SlowLocal(delay=0.3)
    remote = FakeRemote("https://img")
    card = run(
        service(
            settings={"local_timeout": 0.05, "remote_timeout": 1.0},
            local=local,
            remote=remote,
        ).render("<html/>")
    )
    assert card is not None and card.backend == BACKEND_REMOTE
    assert remote.calls == 1


def test_remote_timeout_returns_none():
    class SlowRemote:
        async def __call__(self, html: str) -> str:
            await asyncio.sleep(0.3)
            return "https://img"

    card = run(
        service(
            settings={"backend": "remote", "remote_timeout": 0.05},
            local=None,
            remote=SlowRemote(),
        ).render("<html/>")
    )
    assert card is None


# ---- lifecycle / settings --------------------------------------------------
def test_close_releases_local_backend():
    local = FakeLocal(b"IMAGE")
    renderer = service(local=local, remote=FakeRemote())
    run(renderer.render("<html/>"))
    run(renderer.close())
    assert local.closed is True
    assert renderer.local_available is False
    run(renderer.close())  # idempotent


def test_describe_lists_backends():
    renderer = service(local=FakeLocal(), remote=FakeRemote())
    description = renderer.describe()
    assert "本地 Playwright" in description
    assert "远端 html_render" in description
    text_only = service(local=None, remote=None).describe()
    assert "仅文本" in text_only


def test_settings_are_normalized():
    settings = RenderSettings(
        backend="UNKNOWN",
        local_timeout=0,
        remote_timeout=999,
        failure_threshold=0,
        block_seconds=0,
    ).normalized()
    assert settings.backend == "auto"
    assert settings.local_timeout == 0.05
    assert settings.remote_timeout == 120.0
    assert settings.failure_threshold == 1
    assert settings.block_seconds == 0.05


def test_rendered_card_elapsed_is_recorded():
    card = run(service(local=FakeLocal(b"IMAGE")).render("<html/>"))
    assert isinstance(card, RenderedCard)
    assert card.elapsed >= 0


def test_remote_returning_empty_string_is_treated_as_failure():
    remote = FakeRemote("")
    renderer = service(settings={"backend": "remote"}, local=None, remote=remote)
    assert run(renderer.render("<html/>")) is None


@pytest.mark.parametrize("backend", ["auto", "local", "remote"])
def test_every_backend_value_is_accepted(backend):
    renderer = service(settings={"backend": backend}, local=FakeLocal(b"IMAGE"), remote=FakeRemote("https://img"))
    assert renderer.settings.backend == backend
