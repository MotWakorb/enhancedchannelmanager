"""
Regression tests for GH #546 / bead xzdx9 — BaseException containment.

GH #546: concurrent JWT-cookie requests could kill the backend process with
ExitCode 0, no traceback, no faulthandler output, and no uvicorn shutdown
sequence. Verified mechanism on this stack (uvicorn + uvloop + Starlette
``BaseHTTPMiddleware``): each ``@app.middleware("http")`` layer runs inner
layers in a separate asyncio task, and ``asyncio.Task.__step`` re-raises
``SystemExit`` / ``KeyboardInterrupt`` out of the event loop itself instead
of storing them on the task — aborting uvicorn's ``serve()`` with no
shutdown sequence and exiting the interpreter with status 0.

``exit_diagnostics.BaseExceptionContainmentMiddleware`` is the innermost
user middleware (first ``add_middleware`` call in main.py), so it runs in
the same task as route handlers and converts such BaseExceptions into an
ordinary ``RuntimeError`` (-> logged 500) before the task machinery can
kill the loop.
"""
import asyncio
import logging

import pytest
from fastapi import FastAPI, Request
from httpx import ASGITransport, AsyncClient

from exit_diagnostics import BaseExceptionContainmentMiddleware


def _build_app(with_guard: bool) -> FastAPI:
    """A minimal app mirroring ECM's layering: an outer BaseHTTPMiddleware
    layer (which introduces the task boundary) around the optional guard."""
    app = FastAPI()

    if with_guard:
        # First add_middleware call == innermost, exactly like main.py.
        app.add_middleware(BaseExceptionContainmentMiddleware)

    @app.middleware("http")
    async def passthrough(request: Request, call_next):
        return await call_next(request)

    @app.get("/sysexit")
    async def sysexit():
        raise SystemExit

    @app.get("/kbint")
    async def kbint():
        raise KeyboardInterrupt

    @app.get("/valueerror")
    async def valueerror():
        raise ValueError("ordinary error")

    @app.get("/ok")
    async def ok():
        return {"ok": True}

    return app


class TestContainmentBehavior:
    """The guard converts loop-killing BaseExceptions into ordinary 500s."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("path", ["/sysexit", "/kbint"])
    async def test_baseexception_becomes_500_response(self, path):
        app = _build_app(with_guard=True)
        transport = ASGITransport(app=app, raise_app_exceptions=False)
        async with AsyncClient(transport=transport, base_url="http://t") as c:
            response = await c.get(path)
        assert response.status_code == 500

    @pytest.mark.asyncio
    @pytest.mark.parametrize("path", ["/sysexit", "/kbint"])
    async def test_converted_exception_is_ordinary_runtimeerror(self, path):
        """What escapes the app is a RuntimeError chained from the original —
        an Exception subclass the task machinery stores instead of re-raising
        into the event loop."""
        app = _build_app(with_guard=True)
        transport = ASGITransport(app=app, raise_app_exceptions=True)
        async with AsyncClient(transport=transport, base_url="http://t") as c:
            with pytest.raises(RuntimeError, match="contained by BaseExceptionContainmentMiddleware") as excinfo:
                await c.get(path)
        assert isinstance(excinfo.value.__cause__, (SystemExit, KeyboardInterrupt))

    @pytest.mark.asyncio
    async def test_containment_logs_critical_exit_diag_with_traceback(self, caplog):
        app = _build_app(with_guard=True)
        transport = ASGITransport(app=app, raise_app_exceptions=False)
        with caplog.at_level(logging.CRITICAL, logger="exit_diagnostics"):
            async with AsyncClient(transport=transport, base_url="http://t") as c:
                await c.get("/sysexit")
        messages = [r.getMessage() for r in caplog.records]
        assert any(
            "[EXIT-DIAG]" in m and "SystemExit" in m and "/sysexit" in m
            for m in messages
        )

    def test_without_guard_systemexit_escapes_the_event_loop(self):
        """Control: without the guard the SystemExit ESCAPES THE EVENT LOOP
        ITSELF — it cannot even be caught at the ``await`` inside the loop
        (``asyncio.Task.__step`` re-raises it into ``run_forever``); it
        surfaces only outside ``asyncio.run``. This is precisely what kills a
        real uvicorn process with ExitCode 0 and no shutdown sequence.

        Deliberately a SYNC test with its own event loop: raised inside a
        pytest-asyncio loop, the exception would blow past ``pytest.raises``
        and fail the test run itself. The thread's current-loop state is
        snapshotted and restored (``asyncio.run`` would leave it cleared,
        breaking every later test that relies on ``asyncio.get_event_loop``).
        """
        async def scenario():
            app = _build_app(with_guard=False)
            transport = ASGITransport(app=app, raise_app_exceptions=True)
            async with AsyncClient(transport=transport, base_url="http://t") as c:
                await c.get("/sysexit")

        # Snapshot/restore via get_event_loop_policy() rather than
        # asyncio.get_event_loop() directly: on 3.12 the latter is
        # deprecated outside a running loop and can raise or warn depending
        # on whether a loop was ever set on this thread. Going through the
        # policy sidesteps that and gives a stable "is one set, and what is
        # it" read we can restore verbatim in the `finally` below.
        policy = asyncio.get_event_loop_policy()
        try:
            prev_loop = policy.get_event_loop()
        except RuntimeError:
            prev_loop = None
        loop = policy.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            with pytest.raises(BaseException) as excinfo:
                loop.run_until_complete(scenario())
        finally:
            asyncio.set_event_loop(prev_loop)
            loop.close()
        # Raw or wrapped in a BaseExceptionGroup depending on which task
        # boundary re-raises first; both are non-Exception BaseExceptions,
        # which is exactly what makes them lethal.
        assert not isinstance(excinfo.value, Exception)

    @pytest.mark.asyncio
    async def test_ordinary_exception_type_passes_through_unchanged(self):
        """Exception subclasses keep flowing to the existing 500 machinery —
        the guard must not re-wrap them."""
        app = _build_app(with_guard=True)
        transport = ASGITransport(app=app, raise_app_exceptions=True)
        async with AsyncClient(transport=transport, base_url="http://t") as c:
            with pytest.raises(ValueError, match="ordinary error"):
                await c.get("/valueerror")

    @pytest.mark.asyncio
    async def test_ok_route_unaffected(self):
        app = _build_app(with_guard=True)
        transport = ASGITransport(app=app, raise_app_exceptions=False)
        async with AsyncClient(transport=transport, base_url="http://t") as c:
            response = await c.get("/ok")
        assert response.status_code == 200
        assert response.json() == {"ok": True}


async def _noop_receive():
    """Stand-in ASGI ``receive`` callable for tests whose inner app raises
    before ever awaiting it — real Starlette middleware always passes a
    live callable here, never ``None``, so tests shouldn't either."""
    return {"type": "http.disconnect"}


async def _noop_send(message):
    """Stand-in ASGI ``send`` callable, matching ``_noop_receive`` above."""


class TestPassthroughSemantics:
    """Cancellation and non-HTTP scopes must be untouched by the guard."""

    @pytest.mark.asyncio
    async def test_cancelled_error_propagates_uncontained(self):
        async def cancelling_app(scope, receive, send):
            raise asyncio.CancelledError()

        guard = BaseExceptionContainmentMiddleware(cancelling_app)
        with pytest.raises(asyncio.CancelledError):
            await guard(
                {"type": "http", "method": "GET", "path": "/x"},
                _noop_receive,
                _noop_send,
            )

    @pytest.mark.asyncio
    async def test_non_http_scope_bypasses_containment(self):
        """websocket/lifespan scopes are not request handling — a BaseException
        there must propagate unchanged (containment is HTTP-only by design)."""

        async def exiting_app(scope, receive, send):
            raise SystemExit

        guard = BaseExceptionContainmentMiddleware(exiting_app)
        with pytest.raises(SystemExit):
            await guard({"type": "lifespan"}, _noop_receive, _noop_send)


class TestRealAppContainment:
    """The guard is actually wired into main.app at the innermost position."""

    @pytest.mark.asyncio
    async def test_main_app_registers_guard_innermost(self):
        from main import app

        classes = [m.cls for m in app.user_middleware]
        assert BaseExceptionContainmentMiddleware in classes
        # add_middleware prepends: the LAST list entry is the INNERMOST layer.
        assert classes[-1] is BaseExceptionContainmentMiddleware

    @pytest.mark.asyncio
    async def test_systemexit_in_real_app_route_is_contained(self, async_client, caplog):
        """Inject a throwaway route into the real app; a SystemExit raised
        there must surface as a contained RuntimeError, never a raw
        SystemExit."""
        from main import app

        async def _boom():
            raise SystemExit

        app.router.add_api_route("/api/_test_gh546_boom", _boom, methods=["GET"])
        try:
            with caplog.at_level(logging.CRITICAL, logger="exit_diagnostics"):
                with pytest.raises(RuntimeError, match="contained by BaseExceptionContainmentMiddleware"):
                    await async_client.get("/api/_test_gh546_boom")
        finally:
            app.router.routes[:] = [
                r for r in app.router.routes
                if getattr(r, "path", None) != "/api/_test_gh546_boom"
            ]
        assert any("[EXIT-DIAG]" in r.getMessage() for r in caplog.records)


class TestConcurrentCookieAuthRegression:
    """The GH #546 reproduction shape: the 3 requests the Auto-Creation tab
    fires on mount, in parallel, authenticated via the access_token cookie."""

    TAB_PATHS = [
        "/api/auto-creation/rules",
        "/api/auto-creation/executions",
        "/api/normalization/rules",
    ]

    @pytest.fixture
    def cookie_auth(self, monkeypatch, test_session):
        """Enable cookie auth end-to-end: a real AuthSettings cache with a
        known JWT secret, and a real user + signed access token."""
        import auth.settings as auth_settings_mod
        from auth.settings import AuthSettings
        from auth.tokens import create_access_token
        from models import User

        settings = AuthSettings()
        settings.setup_complete = True
        settings.require_auth = True
        settings.jwt.secret_key = "gh546-regression-test-secret"
        monkeypatch.setattr(auth_settings_mod, "_cached_auth_settings", settings)

        user = User(
            username="gh546-user",
            email="gh546@example.com",
            password_hash="x",
            is_admin=True,
            is_active=True,
            auth_provider="local",
        )
        test_session.add(user)
        test_session.commit()
        test_session.refresh(user)

        return create_access_token(user.id, user.username)

    @pytest.mark.asyncio
    async def test_concurrent_cookie_authenticated_burst_all_succeed(
        self, async_client, cookie_auth
    ):
        async_client.cookies.set("access_token", cookie_auth)
        try:
            for _ in range(5):  # repeated bursts — the failure was probabilistic
                responses = await asyncio.gather(
                    *(async_client.get(p) for p in self.TAB_PATHS)
                )
                assert [r.status_code for r in responses] == [200, 200, 200]
        finally:
            async_client.cookies.delete("access_token")

    @pytest.mark.asyncio
    async def test_invalid_cookie_still_rejected_401(self, async_client, cookie_auth):
        """Containment must not weaken auth: garbage cookies still 401."""
        async_client.cookies.set("access_token", "not-a-real-token")
        try:
            response = await async_client.get(self.TAB_PATHS[0])
        finally:
            async_client.cookies.delete("access_token")
        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_missing_cookie_rejected_401(self, async_client, cookie_auth):
        response = await async_client.get(self.TAB_PATHS[0])
        assert response.status_code == 401
