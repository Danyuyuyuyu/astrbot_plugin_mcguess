"""Card rendering orchestration: backend choice, timeouts, breakers, fallback.

The service is AstrBot-agnostic: the remote backend is injected as an async
callable (``html -> image url``), so the whole flow can be unit-tested without a
running bot. It never raises for expected failures; ``None`` means "fall back to
plain text".
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from .renderer import LocalCardRenderer, RenderError

BACKEND_AUTO = "auto"
BACKEND_LOCAL = "local"
BACKEND_REMOTE = "remote"
BACKENDS = (BACKEND_AUTO, BACKEND_LOCAL, BACKEND_REMOTE)


@dataclass(frozen=True)
class RenderSettings:
    """Per-backend timeouts and circuit-breaker policy."""

    backend: str = BACKEND_AUTO
    remote_fallback: bool = True
    local_timeout: float = 20.0
    remote_timeout: float = 15.0
    failure_threshold: int = 1
    block_seconds: float = 60.0

    def normalized(self) -> "RenderSettings":
        backend = str(self.backend).strip().lower()
        if backend not in BACKENDS:
            backend = BACKEND_AUTO
        return RenderSettings(
            backend=backend,
            remote_fallback=bool(self.remote_fallback),
            local_timeout=max(0.05, min(120.0, float(self.local_timeout))),
            remote_timeout=max(0.05, min(120.0, float(self.remote_timeout))),
            failure_threshold=max(1, int(self.failure_threshold)),
            block_seconds=max(0.05, float(self.block_seconds)),
        )


@dataclass(frozen=True)
class RenderedCard:
    """One successfully rendered card: either local bytes or a remote image URL."""

    backend: str
    image: bytes | None = None
    image_url: str | None = None
    elapsed: float = 0.0


RemoteRenderer = Callable[[str], Awaitable[str]]


class CardRenderService:
    """Try the configured backend(s) in order and hide all failures from callers."""

    def __init__(
        self,
        settings: RenderSettings | None = None,
        *,
        local: LocalCardRenderer | None = None,
        remote: RemoteRenderer | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        self._settings = (settings or RenderSettings()).normalized()
        self._local = local
        self._remote = remote
        self._logger = logger or logging.getLogger(__name__)
        self._failures: dict[str, int] = {BACKEND_LOCAL: 0, BACKEND_REMOTE: 0}
        self._blocked_until: dict[str, float] = {BACKEND_LOCAL: 0.0, BACKEND_REMOTE: 0.0}

    # -- introspection ---------------------------------------------------
    @property
    def settings(self) -> RenderSettings:
        return self._settings

    @property
    def local_available(self) -> bool:
        return self._local is not None

    @property
    def remote_available(self) -> bool:
        return self._remote is not None

    def describe(self) -> str:
        backends = []
        if self._local is not None:
            backends.append("本地 Playwright")
        if self._remote is not None:
            backends.append("远端 html_render")
        return f"backend={self._settings.backend}, " + (" -> ".join(backends) if backends else "仅文本")

    def is_blocked(self, backend: str) -> bool:
        return time.monotonic() < self._blocked_until.get(backend, 0.0)

    # -- rendering -------------------------------------------------------
    async def render(self, html: str) -> RenderedCard | None:
        """Render ``html``; return ``None`` when every backend failed."""
        for backend in self._plan():
            if self.is_blocked(backend):
                continue
            started = time.monotonic()
            try:
                if backend == BACKEND_LOCAL:
                    image = await asyncio.wait_for(
                        self._render_local(html),
                        timeout=self._settings.local_timeout,
                    )
                    self._record_success(BACKEND_LOCAL)
                    return RenderedCard(
                        backend=BACKEND_LOCAL,
                        image=image,
                        elapsed=time.monotonic() - started,
                    )
                image_url = await asyncio.wait_for(
                    self._render_remote(html),
                    timeout=self._settings.remote_timeout,
                )
                self._record_success(BACKEND_REMOTE)
                return RenderedCard(
                    backend=BACKEND_REMOTE,
                    image_url=image_url,
                    elapsed=time.monotonic() - started,
                )
            except asyncio.TimeoutError:
                self._record_failure(backend, f"超时（{self._timeout_for(backend):g}s）")
            except RenderError as exc:
                self._record_failure(backend, str(exc))
            except Exception as exc:  # noqa: BLE001 - rendering must never break a game
                self._record_failure(backend, f"{type(exc).__name__}: {exc}")
        return None

    async def _render_local(self, html: str) -> bytes:
        if self._local is None:
            raise RenderError("本地渲染不可用。")
        return await self._local.render(html)

    async def _render_remote(self, html: str) -> str:
        if self._remote is None:
            raise RenderError("远端渲染不可用。")
        result = await self._remote(html)
        if not isinstance(result, str) or not result.strip():
            raise RenderError("远端渲染没有返回图片地址。")
        return result

    def _timeout_for(self, backend: str) -> float:
        return self._settings.local_timeout if backend == BACKEND_LOCAL else self._settings.remote_timeout

    # -- backend planning -------------------------------------------------
    def _plan(self) -> list[str]:
        backend = self._settings.backend
        has_local = self._local is not None
        has_remote = self._remote is not None
        if backend == BACKEND_REMOTE:
            return [BACKEND_REMOTE] if has_remote else []
        order: list[str] = []
        if has_local:
            order.append(BACKEND_LOCAL)
        if has_remote and (backend == BACKEND_AUTO or self._settings.remote_fallback or not has_local):
            order.append(BACKEND_REMOTE)
        return order

    # -- breaker ---------------------------------------------------------
    def _record_success(self, backend: str) -> None:
        self._failures[backend] = 0
        self._blocked_until[backend] = 0.0

    def _record_failure(self, backend: str, reason: str) -> None:
        self._failures[backend] += 1
        label = "本地" if backend == BACKEND_LOCAL else "远端"
        if self._failures[backend] >= self._settings.failure_threshold:
            self._blocked_until[backend] = time.monotonic() + self._settings.block_seconds
            self._logger.warning(
                "MC 猜谜%s卡片渲染失败（%s），暂停该后端 %g 秒。",
                label,
                reason,
                self._settings.block_seconds,
            )
        else:
            self._logger.warning("MC 猜谜%s卡片渲染失败（%s），本次改用下一后端。", label, reason)

    # -- lifecycle -------------------------------------------------------
    async def close(self) -> None:
        if self._local is None:
            return
        try:
            await self._local.close()
        except Exception:  # noqa: BLE001 - teardown must not raise
            pass
        self._local = None

    def snapshot(self) -> dict[str, Any]:
        """Small debug view used by tests/logs."""
        return {
            "backend": self._settings.backend,
            "failures": dict(self._failures),
            "blocked": {key: self.is_blocked(key) for key in self._failures},
        }
