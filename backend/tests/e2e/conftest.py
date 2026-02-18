"""
E2E test configuration and shared fixtures.

E2E tests hit the live container at localhost:6100.
They are skipped automatically when the container is unreachable.

Run: cd backend && python -m pytest tests/e2e/ -q
"""
import httpx
import pytest

BASE_URL = "http://localhost:6100"
AUTH_CREDS = {"username": "e2e_test", "password": "e2e_test_password"}


def _container_reachable() -> bool:
    """Check if the ECM container is reachable."""
    try:
        resp = httpx.get(f"{BASE_URL}/api/health", timeout=3)
        return resp.status_code == 200
    except (httpx.ConnectError, httpx.TimeoutException):
        return False


# Skip all E2E tests if container is not available
pytestmark = pytest.mark.skipif(
    not _container_reachable(),
    reason="ECM container not reachable at localhost:6100",
)


@pytest.fixture(scope="session")
def base_url():
    """Return the base URL for the ECM container."""
    return BASE_URL


@pytest.fixture(scope="session")
def e2e_client():
    """Create an authenticated httpx client for E2E tests."""
    with httpx.Client(base_url=BASE_URL, timeout=15) as client:
        # Login to get session cookie
        resp = client.post("/api/auth/login", json=AUTH_CREDS)
        if resp.status_code == 200:
            # Cookies are automatically stored by httpx Client
            pass
        yield client


def is_json_response(response):
    """Check if a response is JSON (not SPA catch-all HTML)."""
    ct = response.headers.get("content-type", "")
    return "application/json" in ct or "text/csv" in ct


def skip_if_not_api(response):
    """Skip test if the endpoint returned the SPA catch-all instead of API JSON."""
    if not is_json_response(response) and response.status_code == 200:
        pytest.skip("Endpoint not available in this container version (SPA catch-all)")
