"""
Exit-path diagnostics for the ECM backend process (bd-0gt2i / GH #546).

GH #546 reported the backend process disappearing under concurrent
JWT-cookie requests with ExitCode 0, no traceback, no faulthandler output,
and no uvicorn shutdown sequence. uvicorn's ``run()`` only catches
``KeyboardInterrupt`` — a ``SystemExit``-class ``BaseException`` escaping
the event loop produces exactly that silent status-0 exit, skipping
``Server.shutdown()``.

This module makes any future silent death self-diagnosing from
``docker logs``:

- **atexit line, no exception logged above it** => the interpreter exited
  through the normal shutdown path without uvicorn's shutdown sequence — a
  ``SystemExit``-class exception most likely escaped the event loop
  (``SystemExit`` is special-cased by CPython and never reaches
  ``sys.excepthook``).
- **[EXIT-DIAG] exception log + atexit line** => a real uncaught exception;
  the traceback is in the log.
- **Total silence (no atexit line at all)** => ``os._exit()`` or a hard
  signal (SIGKILL / OOM) — atexit hooks never run on those paths.

Deliberately dependency-free (stdlib only) and side-effect-free: every hook
logs loudly with the ``[EXIT-DIAG]`` prefix and then delegates to the
previously-installed hook (or the interpreter default), changing no
behavior.

Wiring: ``install()`` is called at import time from ``main.py``;
``install_loop_handler()`` is called from the FastAPI startup event once
the event loop is running.
"""
import asyncio
import atexit
import logging
import sys
import threading
import traceback

logger = logging.getLogger(__name__)

_PREFIX = "[EXIT-DIAG]"

_installed = False
_prev_sys_excepthook = None
_prev_threading_excepthook = None


def _format_exception(exc_type, exc_value, exc_tb) -> str:
    """Render a traceback string, never raising."""
    try:
        return "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
    except Exception:  # pragma: no cover — formatting must never break the hook
        return f"<unformattable exception {exc_type!r}: {exc_value!r}>"


def log_atexit() -> None:
    """atexit hook — last line a cleanly-exiting interpreter emits.

    Handler-level errors are suppressed during the emit: at interpreter
    shutdown a log stream may already be closed (e.g. pytest's capture
    streams), and logging would otherwise print a spurious
    '--- Logging error ---' traceback. In production stderr is still open,
    so the line reaches docker logs normally.
    """
    prev_raise = logging.raiseExceptions
    logging.raiseExceptions = False
    try:
        _log_atexit_message()
    finally:
        logging.raiseExceptions = prev_raise


def _log_atexit_message() -> None:
    logger.error(
        "%s ECM process exiting: atexit reached — interpreter is shutting down "
        "via the normal exit path. If no uvicorn shutdown sequence and no %s "
        "exception appears above, a SystemExit-class BaseException most likely "
        "escaped the event loop (uvicorn run() only catches KeyboardInterrupt; "
        "SystemExit never reaches sys.excepthook). Total silence — no atexit "
        "line at all — would instead indicate os._exit() or a hard signal "
        "(SIGKILL/OOM).",
        _PREFIX,
        _PREFIX,
    )


def log_uncaught_exception(exc_type, exc_value, exc_tb) -> None:
    """Log an exception that reached sys.excepthook (main thread, top level)."""
    logger.critical(
        "%s ECM process dying: uncaught exception reached sys.excepthook: "
        "%s: %s\n%s",
        _PREFIX,
        getattr(exc_type, "__name__", exc_type),
        exc_value,
        _format_exception(exc_type, exc_value, exc_tb),
    )


def log_thread_exception(args) -> None:
    """Log an unhandled exception from a non-main thread (threading.excepthook)."""
    thread_name = args.thread.name if args.thread is not None else "<unknown>"
    logger.critical(
        "%s Unhandled exception in thread %r: %s: %s\n%s",
        _PREFIX,
        thread_name,
        getattr(args.exc_type, "__name__", args.exc_type),
        args.exc_value,
        _format_exception(args.exc_type, args.exc_value, args.exc_traceback),
    )


def log_loop_exception(context) -> None:
    """Log an asyncio loop exception-handler context dict."""
    message = context.get("message") or "unhandled exception in event loop"
    exc = context.get("exception")
    if exc is not None:
        logger.critical(
            "%s Asyncio loop exception handler fired: %s — %s: %s\n%s",
            _PREFIX,
            message,
            type(exc).__name__,
            exc,
            _format_exception(type(exc), exc, exc.__traceback__),
        )
    else:
        logger.critical(
            "%s Asyncio loop exception handler fired: %s (context keys: %s)",
            _PREFIX,
            message,
            sorted(context.keys()),
        )


def asyncio_exception_handler(loop, context) -> None:
    """Asyncio loop exception handler — logs loudly, then delegates to the
    loop's default handler so stock behavior is preserved."""
    log_loop_exception(context)
    loop.default_exception_handler(context)


def _sys_excepthook(exc_type, exc_value, exc_tb) -> None:
    try:
        log_uncaught_exception(exc_type, exc_value, exc_tb)
    finally:
        prev = _prev_sys_excepthook or sys.__excepthook__
        prev(exc_type, exc_value, exc_tb)


def _threading_excepthook(args) -> None:
    try:
        log_thread_exception(args)
    finally:
        prev = _prev_threading_excepthook or threading.__excepthook__
        prev(args)


def install() -> None:
    """Register atexit, sys.excepthook, and threading.excepthook diagnostics.

    Idempotent — safe to call more than once. The asyncio loop handler
    requires a running loop and is installed separately via
    ``install_loop_handler()`` from the startup event.
    """
    global _installed, _prev_sys_excepthook, _prev_threading_excepthook
    if _installed:
        return
    _installed = True

    atexit.register(log_atexit)

    _prev_sys_excepthook = sys.excepthook
    sys.excepthook = _sys_excepthook

    _prev_threading_excepthook = threading.excepthook
    threading.excepthook = _threading_excepthook

    logger.info(
        "%s Exit-path diagnostics installed (atexit, sys.excepthook, "
        "threading.excepthook)",
        _PREFIX,
    )


def install_loop_handler(loop) -> None:
    """Install the asyncio loop exception handler on a running loop.

    If a custom handler is already set, it is preserved: ours logs first,
    then chains to it instead of the default handler.
    """
    prev = loop.get_exception_handler()
    if prev is None:
        loop.set_exception_handler(asyncio_exception_handler)
    else:

        def _chained(inner_loop, context, _prev=prev):
            log_loop_exception(context)
            _prev(inner_loop, context)

        loop.set_exception_handler(_chained)
    logger.info("%s Asyncio loop exception handler installed", _PREFIX)


def uninstall() -> None:
    """Remove the hooks installed by ``install()``. Intended for tests."""
    global _installed, _prev_sys_excepthook, _prev_threading_excepthook
    if not _installed:
        return
    atexit.unregister(log_atexit)
    sys.excepthook = _prev_sys_excepthook or sys.__excepthook__
    threading.excepthook = _prev_threading_excepthook or threading.__excepthook__
    _prev_sys_excepthook = None
    _prev_threading_excepthook = None
    _installed = False


def is_installed() -> bool:
    """Whether the process-level hooks are currently installed."""
    return _installed


class BaseExceptionContainmentMiddleware:
    """Contain ``SystemExit``-class BaseExceptions raised while handling a
    request so they cannot silently kill the whole server process (GH #546).

    Mechanism being defended against (verified empirically on this exact
    stack — uvicorn 0.46 + uvloop + Starlette ``BaseHTTPMiddleware``): every
    ``@app.middleware("http")`` layer runs the layers inside it in a separate
    asyncio task. ``asyncio.Task.__step`` special-cases ``SystemExit`` and
    ``KeyboardInterrupt`` — instead of being stored on the task like ordinary
    exceptions, they are re-raised *out of the event loop itself*
    (``run_forever`` → ``asyncio.run``), aborting uvicorn's ``serve()``
    coroutine. uvicorn performs no shutdown sequence, and a bare
    ``SystemExit`` exits the interpreter with status 0 and no traceback —
    exactly the silent ExitCode-0 death reported in GH #546.

    This middleware is pure ASGI (no task boundary of its own) and MUST be
    registered as the INNERMOST user middleware (the first
    ``app.add_middleware(...)`` call in main.py) so that it runs inside the
    same task as route handlers and their dependencies. Any ``SystemExit`` /
    ``KeyboardInterrupt`` (or exotic third-party ``BaseException``) raised by
    a handler is caught here — inside the task, before ``Task.__step`` can
    re-raise it into the loop — logged with its full traceback, and converted
    to an ordinary ``RuntimeError``. The normal Starlette error path then
    turns it into a logged 500 response; the process survives.

    Deliberately NOT contained:
    - ``Exception`` subclasses — the existing 500 handling owns those.
    - ``asyncio.CancelledError`` — client-disconnect and request-timeout
      cancellation semantics must propagate untouched.
    - ``GeneratorExit`` — only ever raised by the interpreter during
      coroutine finalization; swallowing it corrupts generator cleanup.

    Auth semantics are untouched: this layer is inside ``auth_middleware``
    and only transforms the exception type of an already-authorized request.
    """

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        try:
            await self.app(scope, receive, send)
        except Exception:
            # Ordinary errors: existing ServerErrorMiddleware/logging path.
            raise
        except (asyncio.CancelledError, GeneratorExit):
            # Cancellation / finalization semantics must pass through.
            raise
        except BaseException as exc:
            logger.critical(
                "%s BaseException %s escaped the handler for %s %s — contained "
                "before it could kill the event loop (GH #546). Converting to "
                "RuntimeError so it surfaces as an ordinary 500.\n%s",
                _PREFIX,
                type(exc).__name__,
                scope.get("method", "?"),
                scope.get("path", "?"),
                _format_exception(type(exc), exc, exc.__traceback__),
            )
            raise RuntimeError(
                f"{type(exc).__name__} raised while handling "
                f"{scope.get('method', '?')} {scope.get('path', '?')} "
                "(contained by BaseExceptionContainmentMiddleware, GH #546)"
            ) from exc
