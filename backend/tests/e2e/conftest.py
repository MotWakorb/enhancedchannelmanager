"""
E2E test configuration and shared fixtures.

E2E tests hit the live container at localhost:6100 and mutate it: they mark
notifications read, clear the cache, and create and run pipeline rules. On a
developer host that container is the operator's live instance, so reachability
is not consent. The suite skips unless ECM_E2E_LIVE=1 is set, and also skips
when the container is unreachable. The probe runs only after the opt-in.

Run (only against an instance that may be mutated):
    ECM_E2E_LIVE=1 scripts/backend-gate.sh --subset --allow-live-e2e tests/e2e
"""
import os
from pathlib import Path
import time

import httpx
import pytest

BASE_URL = "http://localhost:6100"
LIVE_OPT_IN_ENV = "ECM_E2E_LIVE"
_E2E_DIR = Path(__file__).resolve().parent
AUTH_CREDS = {"username": "e2e_test", "password": "e2e_test_password"}

# Login rate limit: 5/minute.  On rapid reruns we may hit 429 before the
# window resets.  We retry at most _LOGIN_MAX_RETRIES times, honouring the
# Retry-After header (falling back to _LOGIN_RETRY_WAIT_S seconds) so the
# suite self-heals without cascading 401s across every authed test.
_LOGIN_MAX_RETRIES = 2
_LOGIN_RETRY_WAIT_S = 65  # slightly past the 60-s rate-limit window


def _container_reachable() -> bool:
    """Check if the ECM container is reachable."""
    try:
        resp = httpx.get(f"{BASE_URL}/api/health", timeout=3)
        return resp.status_code == 200
    except (httpx.ConnectError, httpx.TimeoutException):
        return False


def _login(client: httpx.Client) -> bool:
    """
    Attempt to authenticate, retrying up to _LOGIN_MAX_RETRIES times on 429.

    Returns True when a session cookie has been obtained, False otherwise.
    The caller is responsible for skipping or failing the session when False
    is returned — this function never raises.
    """
    for attempt in range(1 + _LOGIN_MAX_RETRIES):
        resp = client.post("/api/auth/login", json=AUTH_CREDS)
        if resp.status_code == 200:
            # httpx.Client stores the Set-Cookie automatically
            return True
        if resp.status_code == 429:
            if attempt < _LOGIN_MAX_RETRIES:
                wait = int(resp.headers.get("Retry-After", _LOGIN_RETRY_WAIT_S))
                print(  # noqa: T201 — pytest captures this in -s / verbose mode
                    f"\n[e2e conftest] Login rate-limited (429). "
                    f"Waiting {wait}s before retry {attempt + 1}/{_LOGIN_MAX_RETRIES}…"
                )
                time.sleep(wait)
                continue
        # Any other failure (4xx/5xx) or exhausted retries — give up
        break
    return False


def _live_skip_reason() -> str | None:
    """Why the live suite must not run here, or None when it may."""
    if os.environ.get(LIVE_OPT_IN_ENV) != "1":
        return (f"live e2e is off: it mutates the ECM at {BASE_URL}; set "
                f"{LIVE_OPT_IN_ENV}=1 only when that instance may be changed")
    if not _container_reachable():
        return "ECM container not reachable at localhost:6100"
    return None


def pytest_collection_modifyitems(config, items):
    """Skip every test in this directory unless opted in and reachable.

    A collection hook, because pytestmark in a conftest applies to nothing.
    """
    ours = [item for item in items if _E2E_DIR in Path(item.path).resolve().parents]
    if not ours:
        return
    reason = _live_skip_reason()
    if reason:
        marker = pytest.mark.skip(reason=reason)
        for item in ours:
            item.add_marker(marker)


@pytest.fixture(scope="session")
def base_url():
    """Return the base URL for the ECM container."""
    return BASE_URL


@pytest.fixture(scope="session")
def e2e_client():
    """
    Create an authenticated httpx client for E2E tests.

    Authentication is attempted once per pytest session (session scope).
    On a 429 the fixture backs off and retries (up to _LOGIN_MAX_RETRIES
    times), honouring the Retry-After header.  If login still fails after
    all retries the entire suite is skipped with an explanatory message
    rather than letting every authed test fail with 401.
    """
    # X-ECM-Automated-Client (bead uliyr): self-declare every request from
    # this harness as automated so the journal rows it churns out (rule
    # create/delete pairs, etc.) carry automated_client=True and are eligible
    # for the Journal Noise Purge task — operator rows (no header) are kept.
    with httpx.Client(
        base_url=BASE_URL,
        timeout=15,
        headers={"X-ECM-Automated-Client": "e2e"},
    ) as client:
        authenticated = _login(client)
        if not authenticated:
            pytest.skip(
                "E2E auth failed: could not obtain a session cookie from "
                f"{BASE_URL}/api/auth/login after {1 + _LOGIN_MAX_RETRIES} "
                "attempts (rate-limited or credentials rejected). "
                "Wait ~60 s and rerun."
            )
        yield client


def is_json_response(response):
    """Check if a response is JSON (not SPA catch-all HTML)."""
    ct = response.headers.get("content-type", "")
    return "application/json" in ct or "text/csv" in ct


def skip_if_not_api(response):
    """Skip test if the endpoint returned the SPA catch-all instead of API JSON."""
    if not is_json_response(response) and response.status_code == 200:
        pytest.skip("Endpoint not available in this container version (SPA catch-all)")
