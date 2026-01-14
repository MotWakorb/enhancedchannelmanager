#!/usr/bin/env python3
"""
Dispatcharr API Compatibility Test Suite

Run this script after a Dispatcharr version upgrade to verify all API endpoints
that ECM depends on are still functioning correctly.

Usage:
    python test_dispatcharr_api.py [--url URL] [--username USER] [--password PASS]

    Or set environment variables:
        DISPATCHARR_URL, DISPATCHARR_USERNAME, DISPATCHARR_PASSWORD

Examples:
    # Using command line args
    python test_dispatcharr_api.py --url http://localhost:9000 --username admin --password secret

    # Using environment variables
    export DISPATCHARR_URL=http://localhost:9000
    export DISPATCHARR_USERNAME=admin
    export DISPATCHARR_PASSWORD=secret
    python test_dispatcharr_api.py

    # Run specific test categories
    python test_dispatcharr_api.py --only auth,channels

    # Skip destructive tests (create/update/delete)
    python test_dispatcharr_api.py --read-only
"""

import argparse
import asyncio
import json
import os
import sys
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Callable, Optional

import httpx


class TestStatus(Enum):
    PASSED = "PASSED"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"
    WARNING = "WARNING"


@dataclass
class TestResult:
    name: str
    endpoint: str
    method: str
    status: TestStatus
    message: str = ""
    response_time_ms: float = 0
    response_code: Optional[int] = None
    details: dict = field(default_factory=dict)


@dataclass
class TestSuiteResult:
    category: str
    results: list[TestResult] = field(default_factory=list)

    @property
    def passed(self) -> int:
        return sum(1 for r in self.results if r.status == TestStatus.PASSED)

    @property
    def failed(self) -> int:
        return sum(1 for r in self.results if r.status == TestStatus.FAILED)

    @property
    def warnings(self) -> int:
        return sum(1 for r in self.results if r.status == TestStatus.WARNING)

    @property
    def skipped(self) -> int:
        return sum(1 for r in self.results if r.status == TestStatus.SKIPPED)


class DispatcharrAPITester:
    """Test client for Dispatcharr API endpoints."""

    def __init__(self, base_url: str, username: str, password: str, read_only: bool = False):
        self.base_url = base_url.rstrip("/")
        self.username = username
        self.password = password
        self.read_only = read_only
        self.access_token: Optional[str] = None
        self.refresh_token: Optional[str] = None
        self._client = httpx.AsyncClient(timeout=30.0)
        self.results: list[TestSuiteResult] = []

        # Track created resources for cleanup
        self._created_resources: dict[str, list[int]] = {
            "channels": [],
            "channel_groups": [],
            "logos": [],
            "epg_sources": [],
            "channel_profiles": [],
        }

    async def close(self):
        """Close the HTTP client."""
        await self._client.aclose()

    async def _timed_request(
        self,
        method: str,
        path: str,
        **kwargs,
    ) -> tuple[httpx.Response, float]:
        """Make a request and return response with timing."""
        headers = kwargs.pop("headers", {})
        if self.access_token:
            headers["Authorization"] = f"Bearer {self.access_token}"

        start = datetime.now()
        response = await self._client.request(
            method,
            f"{self.base_url}{path}",
            headers=headers,
            **kwargs,
        )
        elapsed_ms = (datetime.now() - start).total_seconds() * 1000
        return response, elapsed_ms

    def _result(
        self,
        name: str,
        endpoint: str,
        method: str,
        status: TestStatus,
        message: str = "",
        response_time_ms: float = 0,
        response_code: Optional[int] = None,
        details: dict = None,
    ) -> TestResult:
        """Create a test result."""
        return TestResult(
            name=name,
            endpoint=endpoint,
            method=method,
            status=status,
            message=message,
            response_time_ms=response_time_ms,
            response_code=response_code,
            details=details or {},
        )

    # =========================================================================
    # Authentication Tests
    # =========================================================================

    async def test_auth(self) -> TestSuiteResult:
        """Test authentication endpoints."""
        suite = TestSuiteResult(category="Authentication")

        # Test: Login (obtain tokens)
        try:
            response, elapsed = await self._timed_request(
                "POST",
                "/api/accounts/token/",
                json={"username": self.username, "password": self.password},
            )
            if response.status_code == 200:
                data = response.json()
                if "access" in data:
                    self.access_token = data["access"]
                    self.refresh_token = data.get("refresh")
                    suite.results.append(self._result(
                        "Login",
                        "/api/accounts/token/",
                        "POST",
                        TestStatus.PASSED,
                        "Successfully obtained access token",
                        elapsed,
                        response.status_code,
                        {"has_refresh": bool(self.refresh_token)},
                    ))
                else:
                    suite.results.append(self._result(
                        "Login",
                        "/api/accounts/token/",
                        "POST",
                        TestStatus.FAILED,
                        "Response missing 'access' token field",
                        elapsed,
                        response.status_code,
                    ))
            else:
                suite.results.append(self._result(
                    "Login",
                    "/api/accounts/token/",
                    "POST",
                    TestStatus.FAILED,
                    f"Login failed: {response.text[:200]}",
                    elapsed,
                    response.status_code,
                ))
        except Exception as e:
            suite.results.append(self._result(
                "Login",
                "/api/accounts/token/",
                "POST",
                TestStatus.FAILED,
                f"Exception: {e}",
            ))

        # Test: Token Refresh
        if self.refresh_token:
            try:
                response, elapsed = await self._timed_request(
                    "POST",
                    "/api/accounts/token/refresh/",
                    json={"refresh": self.refresh_token},
                )
                if response.status_code == 200:
                    data = response.json()
                    if "access" in data:
                        self.access_token = data["access"]
                        suite.results.append(self._result(
                            "Token Refresh",
                            "/api/accounts/token/refresh/",
                            "POST",
                            TestStatus.PASSED,
                            "Successfully refreshed access token",
                            elapsed,
                            response.status_code,
                        ))
                    else:
                        suite.results.append(self._result(
                            "Token Refresh",
                            "/api/accounts/token/refresh/",
                            "POST",
                            TestStatus.FAILED,
                            "Response missing 'access' token field",
                            elapsed,
                            response.status_code,
                        ))
                else:
                    suite.results.append(self._result(
                        "Token Refresh",
                        "/api/accounts/token/refresh/",
                        "POST",
                        TestStatus.WARNING,
                        f"Token refresh returned {response.status_code}",
                        elapsed,
                        response.status_code,
                    ))
            except Exception as e:
                suite.results.append(self._result(
                    "Token Refresh",
                    "/api/accounts/token/refresh/",
                    "POST",
                    TestStatus.FAILED,
                    f"Exception: {e}",
                ))
        else:
            suite.results.append(self._result(
                "Token Refresh",
                "/api/accounts/token/refresh/",
                "POST",
                TestStatus.SKIPPED,
                "No refresh token available",
            ))

        self.results.append(suite)
        return suite

    # =========================================================================
    # Channel Tests
    # =========================================================================

    async def test_channels(self) -> TestSuiteResult:
        """Test channel endpoints."""
        suite = TestSuiteResult(category="Channels")
        test_channel_id = None

        # Test: List Channels (paginated)
        try:
            response, elapsed = await self._timed_request(
                "GET",
                "/api/channels/channels/",
                params={"page": 1, "page_size": 10},
            )
            if response.status_code == 200:
                data = response.json()
                has_results = "results" in data
                has_count = "count" in data
                suite.results.append(self._result(
                    "List Channels",
                    "/api/channels/channels/",
                    "GET",
                    TestStatus.PASSED if has_results else TestStatus.WARNING,
                    f"Found {data.get('count', len(data.get('results', [])))} channels",
                    elapsed,
                    response.status_code,
                    {"has_pagination": has_results and has_count},
                ))
                # Get a channel ID for further tests
                if data.get("results"):
                    test_channel_id = data["results"][0]["id"]
            else:
                suite.results.append(self._result(
                    "List Channels",
                    "/api/channels/channels/",
                    "GET",
                    TestStatus.FAILED,
                    f"Failed: {response.text[:200]}",
                    elapsed,
                    response.status_code,
                ))
        except Exception as e:
            suite.results.append(self._result(
                "List Channels",
                "/api/channels/channels/",
                "GET",
                TestStatus.FAILED,
                f"Exception: {e}",
            ))

        # Test: List Channels with search
        try:
            response, elapsed = await self._timed_request(
                "GET",
                "/api/channels/channels/",
                params={"page": 1, "page_size": 10, "search": "test"},
            )
            suite.results.append(self._result(
                "List Channels (search)",
                "/api/channels/channels/?search=",
                "GET",
                TestStatus.PASSED if response.status_code == 200 else TestStatus.FAILED,
                "Search parameter accepted" if response.status_code == 200 else f"Failed: {response.status_code}",
                elapsed,
                response.status_code,
            ))
        except Exception as e:
            suite.results.append(self._result(
                "List Channels (search)",
                "/api/channels/channels/?search=",
                "GET",
                TestStatus.FAILED,
                f"Exception: {e}",
            ))

        # Test: Get Single Channel
        if test_channel_id:
            try:
                response, elapsed = await self._timed_request(
                    "GET",
                    f"/api/channels/channels/{test_channel_id}/",
                )
                if response.status_code == 200:
                    data = response.json()
                    suite.results.append(self._result(
                        "Get Channel",
                        f"/api/channels/channels/{{id}}/",
                        "GET",
                        TestStatus.PASSED,
                        f"Retrieved channel: {data.get('name', 'unknown')}",
                        elapsed,
                        response.status_code,
                        {"fields": list(data.keys())[:10]},
                    ))
                else:
                    suite.results.append(self._result(
                        "Get Channel",
                        f"/api/channels/channels/{{id}}/",
                        "GET",
                        TestStatus.FAILED,
                        f"Failed: {response.text[:200]}",
                        elapsed,
                        response.status_code,
                    ))
            except Exception as e:
                suite.results.append(self._result(
                    "Get Channel",
                    f"/api/channels/channels/{{id}}/",
                    "GET",
                    TestStatus.FAILED,
                    f"Exception: {e}",
                ))
        else:
            suite.results.append(self._result(
                "Get Channel",
                f"/api/channels/channels/{{id}}/",
                "GET",
                TestStatus.SKIPPED,
                "No channel ID available",
            ))

        # Test: Get Channel Streams
        if test_channel_id:
            try:
                response, elapsed = await self._timed_request(
                    "GET",
                    f"/api/channels/channels/{test_channel_id}/streams/",
                )
                suite.results.append(self._result(
                    "Get Channel Streams",
                    f"/api/channels/channels/{{id}}/streams/",
                    "GET",
                    TestStatus.PASSED if response.status_code == 200 else TestStatus.FAILED,
                    f"Found {len(response.json()) if response.status_code == 200 else 0} streams",
                    elapsed,
                    response.status_code,
                ))
            except Exception as e:
                suite.results.append(self._result(
                    "Get Channel Streams",
                    f"/api/channels/channels/{{id}}/streams/",
                    "GET",
                    TestStatus.FAILED,
                    f"Exception: {e}",
                ))
        else:
            suite.results.append(self._result(
                "Get Channel Streams",
                f"/api/channels/channels/{{id}}/streams/",
                "GET",
                TestStatus.SKIPPED,
                "No channel ID available",
            ))

        # Test: Create Channel (if not read-only)
        if not self.read_only:
            try:
                test_channel_data = {
                    "name": f"ECM_API_Test_{datetime.now().strftime('%Y%m%d%H%M%S')}",
                    "channel_number": 99999,
                }
                response, elapsed = await self._timed_request(
                    "POST",
                    "/api/channels/channels/",
                    json=test_channel_data,
                )
                if response.status_code in (200, 201):
                    data = response.json()
                    created_id = data.get("id")
                    if created_id:
                        self._created_resources["channels"].append(created_id)
                    suite.results.append(self._result(
                        "Create Channel",
                        "/api/channels/channels/",
                        "POST",
                        TestStatus.PASSED,
                        f"Created channel ID: {created_id}",
                        elapsed,
                        response.status_code,
                    ))

                    # Test: Update Channel
                    if created_id:
                        try:
                            response, elapsed = await self._timed_request(
                                "PATCH",
                                f"/api/channels/channels/{created_id}/",
                                json={"name": f"{test_channel_data['name']}_updated"},
                            )
                            suite.results.append(self._result(
                                "Update Channel",
                                f"/api/channels/channels/{{id}}/",
                                "PATCH",
                                TestStatus.PASSED if response.status_code == 200 else TestStatus.FAILED,
                                "Channel updated" if response.status_code == 200 else f"Failed: {response.status_code}",
                                elapsed,
                                response.status_code,
                            ))
                        except Exception as e:
                            suite.results.append(self._result(
                                "Update Channel",
                                f"/api/channels/channels/{{id}}/",
                                "PATCH",
                                TestStatus.FAILED,
                                f"Exception: {e}",
                            ))

                        # Test: Delete Channel
                        try:
                            response, elapsed = await self._timed_request(
                                "DELETE",
                                f"/api/channels/channels/{created_id}/",
                            )
                            if response.status_code in (200, 204):
                                self._created_resources["channels"].remove(created_id)
                            suite.results.append(self._result(
                                "Delete Channel",
                                f"/api/channels/channels/{{id}}/",
                                "DELETE",
                                TestStatus.PASSED if response.status_code in (200, 204) else TestStatus.FAILED,
                                "Channel deleted" if response.status_code in (200, 204) else f"Failed: {response.status_code}",
                                elapsed,
                                response.status_code,
                            ))
                        except Exception as e:
                            suite.results.append(self._result(
                                "Delete Channel",
                                f"/api/channels/channels/{{id}}/",
                                "DELETE",
                                TestStatus.FAILED,
                                f"Exception: {e}",
                            ))
                else:
                    suite.results.append(self._result(
                        "Create Channel",
                        "/api/channels/channels/",
                        "POST",
                        TestStatus.FAILED,
                        f"Failed: {response.text[:200]}",
                        elapsed,
                        response.status_code,
                    ))
            except Exception as e:
                suite.results.append(self._result(
                    "Create Channel",
                    "/api/channels/channels/",
                    "POST",
                    TestStatus.FAILED,
                    f"Exception: {e}",
                ))
        else:
            suite.results.append(self._result(
                "Create Channel",
                "/api/channels/channels/",
                "POST",
                TestStatus.SKIPPED,
                "Read-only mode",
            ))
            suite.results.append(self._result(
                "Update Channel",
                f"/api/channels/channels/{{id}}/",
                "PATCH",
                TestStatus.SKIPPED,
                "Read-only mode",
            ))
            suite.results.append(self._result(
                "Delete Channel",
                f"/api/channels/channels/{{id}}/",
                "DELETE",
                TestStatus.SKIPPED,
                "Read-only mode",
            ))

        # Test: Assign Channel Numbers
        try:
            response, elapsed = await self._timed_request(
                "POST",
                "/api/channels/channels/assign/",
                json={"channel_ids": [], "starting_number": 1},
            )
            # Empty list should be accepted (no-op)
            suite.results.append(self._result(
                "Assign Channel Numbers",
                "/api/channels/channels/assign/",
                "POST",
                TestStatus.PASSED if response.status_code in (200, 400) else TestStatus.WARNING,
                "Endpoint accessible" if response.status_code in (200, 400) else f"Status: {response.status_code}",
                elapsed,
                response.status_code,
            ))
        except Exception as e:
            suite.results.append(self._result(
                "Assign Channel Numbers",
                "/api/channels/channels/assign/",
                "POST",
                TestStatus.FAILED,
                f"Exception: {e}",
            ))

        self.results.append(suite)
        return suite

    # =========================================================================
    # Channel Groups Tests
    # =========================================================================

    async def test_channel_groups(self) -> TestSuiteResult:
        """Test channel group endpoints."""
        suite = TestSuiteResult(category="Channel Groups")

        # Test: List Channel Groups
        try:
            response, elapsed = await self._timed_request(
                "GET",
                "/api/channels/groups/",
            )
            if response.status_code == 200:
                data = response.json()
                suite.results.append(self._result(
                    "List Channel Groups",
                    "/api/channels/groups/",
                    "GET",
                    TestStatus.PASSED,
                    f"Found {len(data)} channel groups",
                    elapsed,
                    response.status_code,
                ))
            else:
                suite.results.append(self._result(
                    "List Channel Groups",
                    "/api/channels/groups/",
                    "GET",
                    TestStatus.FAILED,
                    f"Failed: {response.text[:200]}",
                    elapsed,
                    response.status_code,
                ))
        except Exception as e:
            suite.results.append(self._result(
                "List Channel Groups",
                "/api/channels/groups/",
                "GET",
                TestStatus.FAILED,
                f"Exception: {e}",
            ))

        # Test: Create/Update/Delete Channel Group (if not read-only)
        if not self.read_only:
            created_group_id = None
            try:
                response, elapsed = await self._timed_request(
                    "POST",
                    "/api/channels/groups/",
                    json={"name": f"ECM_Test_Group_{datetime.now().strftime('%Y%m%d%H%M%S')}"},
                )
                if response.status_code in (200, 201):
                    data = response.json()
                    created_group_id = data.get("id")
                    if created_group_id:
                        self._created_resources["channel_groups"].append(created_group_id)
                    suite.results.append(self._result(
                        "Create Channel Group",
                        "/api/channels/groups/",
                        "POST",
                        TestStatus.PASSED,
                        f"Created group ID: {created_group_id}",
                        elapsed,
                        response.status_code,
                    ))
                else:
                    suite.results.append(self._result(
                        "Create Channel Group",
                        "/api/channels/groups/",
                        "POST",
                        TestStatus.FAILED,
                        f"Failed: {response.text[:200]}",
                        elapsed,
                        response.status_code,
                    ))
            except Exception as e:
                suite.results.append(self._result(
                    "Create Channel Group",
                    "/api/channels/groups/",
                    "POST",
                    TestStatus.FAILED,
                    f"Exception: {e}",
                ))

            if created_group_id:
                # Test: Update Channel Group
                try:
                    response, elapsed = await self._timed_request(
                        "PATCH",
                        f"/api/channels/groups/{created_group_id}/",
                        json={"name": f"ECM_Test_Group_Updated"},
                    )
                    suite.results.append(self._result(
                        "Update Channel Group",
                        f"/api/channels/groups/{{id}}/",
                        "PATCH",
                        TestStatus.PASSED if response.status_code == 200 else TestStatus.FAILED,
                        "Group updated" if response.status_code == 200 else f"Failed: {response.status_code}",
                        elapsed,
                        response.status_code,
                    ))
                except Exception as e:
                    suite.results.append(self._result(
                        "Update Channel Group",
                        f"/api/channels/groups/{{id}}/",
                        "PATCH",
                        TestStatus.FAILED,
                        f"Exception: {e}",
                    ))

                # Test: Delete Channel Group
                try:
                    response, elapsed = await self._timed_request(
                        "DELETE",
                        f"/api/channels/groups/{created_group_id}/",
                    )
                    if response.status_code in (200, 204):
                        self._created_resources["channel_groups"].remove(created_group_id)
                    suite.results.append(self._result(
                        "Delete Channel Group",
                        f"/api/channels/groups/{{id}}/",
                        "DELETE",
                        TestStatus.PASSED if response.status_code in (200, 204) else TestStatus.FAILED,
                        "Group deleted" if response.status_code in (200, 204) else f"Failed: {response.status_code}",
                        elapsed,
                        response.status_code,
                    ))
                except Exception as e:
                    suite.results.append(self._result(
                        "Delete Channel Group",
                        f"/api/channels/groups/{{id}}/",
                        "DELETE",
                        TestStatus.FAILED,
                        f"Exception: {e}",
                    ))
        else:
            suite.results.append(self._result(
                "Create Channel Group",
                "/api/channels/groups/",
                "POST",
                TestStatus.SKIPPED,
                "Read-only mode",
            ))
            suite.results.append(self._result(
                "Update Channel Group",
                f"/api/channels/groups/{{id}}/",
                "PATCH",
                TestStatus.SKIPPED,
                "Read-only mode",
            ))
            suite.results.append(self._result(
                "Delete Channel Group",
                f"/api/channels/groups/{{id}}/",
                "DELETE",
                TestStatus.SKIPPED,
                "Read-only mode",
            ))

        self.results.append(suite)
        return suite

    # =========================================================================
    # Streams Tests
    # =========================================================================

    async def test_streams(self) -> TestSuiteResult:
        """Test stream endpoints."""
        suite = TestSuiteResult(category="Streams")
        test_stream_id = None

        # Test: List Streams (paginated)
        try:
            response, elapsed = await self._timed_request(
                "GET",
                "/api/channels/streams/",
                params={"page": 1, "page_size": 10},
            )
            if response.status_code == 200:
                data = response.json()
                suite.results.append(self._result(
                    "List Streams",
                    "/api/channels/streams/",
                    "GET",
                    TestStatus.PASSED,
                    f"Found {data.get('count', len(data.get('results', [])))} streams",
                    elapsed,
                    response.status_code,
                    {"has_pagination": "results" in data},
                ))
                if data.get("results"):
                    test_stream_id = data["results"][0]["id"]
            else:
                suite.results.append(self._result(
                    "List Streams",
                    "/api/channels/streams/",
                    "GET",
                    TestStatus.FAILED,
                    f"Failed: {response.text[:200]}",
                    elapsed,
                    response.status_code,
                ))
        except Exception as e:
            suite.results.append(self._result(
                "List Streams",
                "/api/channels/streams/",
                "GET",
                TestStatus.FAILED,
                f"Exception: {e}",
            ))

        # Test: List Streams with filters
        try:
            response, elapsed = await self._timed_request(
                "GET",
                "/api/channels/streams/",
                params={"page": 1, "page_size": 10, "search": "test"},
            )
            suite.results.append(self._result(
                "List Streams (search)",
                "/api/channels/streams/?search=",
                "GET",
                TestStatus.PASSED if response.status_code == 200 else TestStatus.FAILED,
                "Search parameter accepted" if response.status_code == 200 else f"Failed: {response.status_code}",
                elapsed,
                response.status_code,
            ))
        except Exception as e:
            suite.results.append(self._result(
                "List Streams (search)",
                "/api/channels/streams/?search=",
                "GET",
                TestStatus.FAILED,
                f"Exception: {e}",
            ))

        # Test: Get Single Stream
        if test_stream_id:
            try:
                response, elapsed = await self._timed_request(
                    "GET",
                    f"/api/channels/streams/{test_stream_id}/",
                )
                suite.results.append(self._result(
                    "Get Stream",
                    f"/api/channels/streams/{{id}}/",
                    "GET",
                    TestStatus.PASSED if response.status_code == 200 else TestStatus.FAILED,
                    f"Retrieved stream" if response.status_code == 200 else f"Failed: {response.status_code}",
                    elapsed,
                    response.status_code,
                ))
            except Exception as e:
                suite.results.append(self._result(
                    "Get Stream",
                    f"/api/channels/streams/{{id}}/",
                    "GET",
                    TestStatus.FAILED,
                    f"Exception: {e}",
                ))
        else:
            suite.results.append(self._result(
                "Get Stream",
                f"/api/channels/streams/{{id}}/",
                "GET",
                TestStatus.SKIPPED,
                "No stream ID available",
            ))

        # Test: Get Streams by IDs
        if test_stream_id:
            try:
                response, elapsed = await self._timed_request(
                    "POST",
                    "/api/channels/streams/by-ids/",
                    json={"ids": [test_stream_id]},
                )
                suite.results.append(self._result(
                    "Get Streams by IDs",
                    "/api/channels/streams/by-ids/",
                    "POST",
                    TestStatus.PASSED if response.status_code == 200 else TestStatus.FAILED,
                    f"Retrieved {len(response.json()) if response.status_code == 200 else 0} streams",
                    elapsed,
                    response.status_code,
                ))
            except Exception as e:
                suite.results.append(self._result(
                    "Get Streams by IDs",
                    "/api/channels/streams/by-ids/",
                    "POST",
                    TestStatus.FAILED,
                    f"Exception: {e}",
                ))
        else:
            suite.results.append(self._result(
                "Get Streams by IDs",
                "/api/channels/streams/by-ids/",
                "POST",
                TestStatus.SKIPPED,
                "No stream ID available",
            ))

        # Test: Get Stream Groups
        try:
            response, elapsed = await self._timed_request(
                "GET",
                "/api/channels/streams/groups/",
            )
            suite.results.append(self._result(
                "Get Stream Groups",
                "/api/channels/streams/groups/",
                "GET",
                TestStatus.PASSED if response.status_code == 200 else TestStatus.FAILED,
                f"Found {len(response.json()) if response.status_code == 200 else 0} stream groups",
                elapsed,
                response.status_code,
            ))
        except Exception as e:
            suite.results.append(self._result(
                "Get Stream Groups",
                "/api/channels/streams/groups/",
                "GET",
                TestStatus.FAILED,
                f"Exception: {e}",
            ))

        self.results.append(suite)
        return suite

    # =========================================================================
    # M3U Account Tests
    # =========================================================================

    async def test_m3u_accounts(self) -> TestSuiteResult:
        """Test M3U account endpoints."""
        suite = TestSuiteResult(category="M3U Accounts")
        test_account_id = None

        # Test: List M3U Accounts
        try:
            response, elapsed = await self._timed_request(
                "GET",
                "/api/m3u/accounts/",
            )
            if response.status_code == 200:
                data = response.json()
                suite.results.append(self._result(
                    "List M3U Accounts",
                    "/api/m3u/accounts/",
                    "GET",
                    TestStatus.PASSED,
                    f"Found {len(data)} M3U accounts",
                    elapsed,
                    response.status_code,
                ))
                if data:
                    test_account_id = data[0]["id"]
                    # Check for channel_groups in response (used by ECM)
                    has_channel_groups = "channel_groups" in data[0]
                    suite.results.append(self._result(
                        "M3U Account has channel_groups",
                        "/api/m3u/accounts/",
                        "GET",
                        TestStatus.PASSED if has_channel_groups else TestStatus.WARNING,
                        "channel_groups field present" if has_channel_groups else "channel_groups field missing",
                        0,
                        200,
                    ))
            else:
                suite.results.append(self._result(
                    "List M3U Accounts",
                    "/api/m3u/accounts/",
                    "GET",
                    TestStatus.FAILED,
                    f"Failed: {response.text[:200]}",
                    elapsed,
                    response.status_code,
                ))
        except Exception as e:
            suite.results.append(self._result(
                "List M3U Accounts",
                "/api/m3u/accounts/",
                "GET",
                TestStatus.FAILED,
                f"Exception: {e}",
            ))

        # Test: Get Single M3U Account
        if test_account_id:
            try:
                response, elapsed = await self._timed_request(
                    "GET",
                    f"/api/m3u/accounts/{test_account_id}/",
                )
                suite.results.append(self._result(
                    "Get M3U Account",
                    f"/api/m3u/accounts/{{id}}/",
                    "GET",
                    TestStatus.PASSED if response.status_code == 200 else TestStatus.FAILED,
                    "Retrieved account" if response.status_code == 200 else f"Failed: {response.status_code}",
                    elapsed,
                    response.status_code,
                ))
            except Exception as e:
                suite.results.append(self._result(
                    "Get M3U Account",
                    f"/api/m3u/accounts/{{id}}/",
                    "GET",
                    TestStatus.FAILED,
                    f"Exception: {e}",
                ))

            # Test: Get M3U Filters
            try:
                response, elapsed = await self._timed_request(
                    "GET",
                    f"/api/m3u/accounts/{test_account_id}/filters/",
                )
                suite.results.append(self._result(
                    "Get M3U Filters",
                    f"/api/m3u/accounts/{{id}}/filters/",
                    "GET",
                    TestStatus.PASSED if response.status_code == 200 else TestStatus.FAILED,
                    f"Found {len(response.json()) if response.status_code == 200 else 0} filters",
                    elapsed,
                    response.status_code,
                ))
            except Exception as e:
                suite.results.append(self._result(
                    "Get M3U Filters",
                    f"/api/m3u/accounts/{{id}}/filters/",
                    "GET",
                    TestStatus.FAILED,
                    f"Exception: {e}",
                ))

            # Test: Get M3U Profiles
            try:
                response, elapsed = await self._timed_request(
                    "GET",
                    f"/api/m3u/accounts/{test_account_id}/profiles/",
                )
                suite.results.append(self._result(
                    "Get M3U Profiles",
                    f"/api/m3u/accounts/{{id}}/profiles/",
                    "GET",
                    TestStatus.PASSED if response.status_code == 200 else TestStatus.FAILED,
                    f"Found {len(response.json()) if response.status_code == 200 else 0} profiles",
                    elapsed,
                    response.status_code,
                ))
            except Exception as e:
                suite.results.append(self._result(
                    "Get M3U Profiles",
                    f"/api/m3u/accounts/{{id}}/profiles/",
                    "GET",
                    TestStatus.FAILED,
                    f"Exception: {e}",
                ))

            # Test: PATCH M3U Account (toggle is_active)
            if not self.read_only:
                try:
                    response, elapsed = await self._timed_request(
                        "PATCH",
                        f"/api/m3u/accounts/{test_account_id}/",
                        json={"is_active": True},
                    )
                    suite.results.append(self._result(
                        "Patch M3U Account",
                        f"/api/m3u/accounts/{{id}}/",
                        "PATCH",
                        TestStatus.PASSED if response.status_code == 200 else TestStatus.FAILED,
                        "Account patched" if response.status_code == 200 else f"Failed: {response.status_code}",
                        elapsed,
                        response.status_code,
                    ))
                except Exception as e:
                    suite.results.append(self._result(
                        "Patch M3U Account",
                        f"/api/m3u/accounts/{{id}}/",
                        "PATCH",
                        TestStatus.FAILED,
                        f"Exception: {e}",
                    ))

                # Test: Update Group Settings
                try:
                    response, elapsed = await self._timed_request(
                        "PATCH",
                        f"/api/m3u/accounts/{test_account_id}/group-settings/",
                        json={"group_settings": []},
                    )
                    suite.results.append(self._result(
                        "Update Group Settings",
                        f"/api/m3u/accounts/{{id}}/group-settings/",
                        "PATCH",
                        TestStatus.PASSED if response.status_code == 200 else TestStatus.WARNING,
                        "Group settings endpoint accessible" if response.status_code in (200, 400) else f"Status: {response.status_code}",
                        elapsed,
                        response.status_code,
                    ))
                except Exception as e:
                    suite.results.append(self._result(
                        "Update Group Settings",
                        f"/api/m3u/accounts/{{id}}/group-settings/",
                        "PATCH",
                        TestStatus.FAILED,
                        f"Exception: {e}",
                    ))
            else:
                suite.results.append(self._result(
                    "Patch M3U Account",
                    f"/api/m3u/accounts/{{id}}/",
                    "PATCH",
                    TestStatus.SKIPPED,
                    "Read-only mode",
                ))
                suite.results.append(self._result(
                    "Update Group Settings",
                    f"/api/m3u/accounts/{{id}}/group-settings/",
                    "PATCH",
                    TestStatus.SKIPPED,
                    "Read-only mode",
                ))
        else:
            for test_name in ["Get M3U Account", "Get M3U Filters", "Get M3U Profiles", "Patch M3U Account", "Update Group Settings"]:
                suite.results.append(self._result(
                    test_name,
                    "/api/m3u/accounts/...",
                    "GET/PATCH",
                    TestStatus.SKIPPED,
                    "No M3U account available",
                ))

        # Test: Get Server Groups
        try:
            response, elapsed = await self._timed_request(
                "GET",
                "/api/m3u/server-groups/",
            )
            suite.results.append(self._result(
                "Get Server Groups",
                "/api/m3u/server-groups/",
                "GET",
                TestStatus.PASSED if response.status_code == 200 else TestStatus.FAILED,
                f"Found {len(response.json()) if response.status_code == 200 else 0} server groups",
                elapsed,
                response.status_code,
            ))
        except Exception as e:
            suite.results.append(self._result(
                "Get Server Groups",
                "/api/m3u/server-groups/",
                "GET",
                TestStatus.FAILED,
                f"Exception: {e}",
            ))

        self.results.append(suite)
        return suite

    # =========================================================================
    # Logos Tests
    # =========================================================================

    async def test_logos(self) -> TestSuiteResult:
        """Test logo endpoints."""
        suite = TestSuiteResult(category="Logos")
        test_logo_id = None

        # Test: List Logos (paginated)
        try:
            response, elapsed = await self._timed_request(
                "GET",
                "/api/channels/logos/",
                params={"page": 1, "page_size": 10},
            )
            if response.status_code == 200:
                data = response.json()
                suite.results.append(self._result(
                    "List Logos",
                    "/api/channels/logos/",
                    "GET",
                    TestStatus.PASSED,
                    f"Found {data.get('count', len(data.get('results', [])))} logos",
                    elapsed,
                    response.status_code,
                ))
                if data.get("results"):
                    test_logo_id = data["results"][0]["id"]
            else:
                suite.results.append(self._result(
                    "List Logos",
                    "/api/channels/logos/",
                    "GET",
                    TestStatus.FAILED,
                    f"Failed: {response.text[:200]}",
                    elapsed,
                    response.status_code,
                ))
        except Exception as e:
            suite.results.append(self._result(
                "List Logos",
                "/api/channels/logos/",
                "GET",
                TestStatus.FAILED,
                f"Exception: {e}",
            ))

        # Test: List Logos with search
        try:
            response, elapsed = await self._timed_request(
                "GET",
                "/api/channels/logos/",
                params={"page": 1, "page_size": 10, "search": "test"},
            )
            suite.results.append(self._result(
                "List Logos (search)",
                "/api/channels/logos/?search=",
                "GET",
                TestStatus.PASSED if response.status_code == 200 else TestStatus.FAILED,
                "Search parameter accepted" if response.status_code == 200 else f"Failed: {response.status_code}",
                elapsed,
                response.status_code,
            ))
        except Exception as e:
            suite.results.append(self._result(
                "List Logos (search)",
                "/api/channels/logos/?search=",
                "GET",
                TestStatus.FAILED,
                f"Exception: {e}",
            ))

        # Test: Get Single Logo
        if test_logo_id:
            try:
                response, elapsed = await self._timed_request(
                    "GET",
                    f"/api/channels/logos/{test_logo_id}/",
                )
                suite.results.append(self._result(
                    "Get Logo",
                    f"/api/channels/logos/{{id}}/",
                    "GET",
                    TestStatus.PASSED if response.status_code == 200 else TestStatus.FAILED,
                    "Retrieved logo" if response.status_code == 200 else f"Failed: {response.status_code}",
                    elapsed,
                    response.status_code,
                ))
            except Exception as e:
                suite.results.append(self._result(
                    "Get Logo",
                    f"/api/channels/logos/{{id}}/",
                    "GET",
                    TestStatus.FAILED,
                    f"Exception: {e}",
                ))
        else:
            suite.results.append(self._result(
                "Get Logo",
                f"/api/channels/logos/{{id}}/",
                "GET",
                TestStatus.SKIPPED,
                "No logo ID available",
            ))

        # Test: Create/Update/Delete Logo (if not read-only)
        if not self.read_only:
            created_logo_id = None
            try:
                test_logo_data = {
                    "name": f"ECM_Test_Logo_{datetime.now().strftime('%Y%m%d%H%M%S')}",
                    "url": f"https://example.com/test_logo_{datetime.now().strftime('%Y%m%d%H%M%S')}.png",
                }
                response, elapsed = await self._timed_request(
                    "POST",
                    "/api/channels/logos/",
                    json=test_logo_data,
                )
                if response.status_code in (200, 201):
                    data = response.json()
                    created_logo_id = data.get("id")
                    if created_logo_id:
                        self._created_resources["logos"].append(created_logo_id)
                    suite.results.append(self._result(
                        "Create Logo",
                        "/api/channels/logos/",
                        "POST",
                        TestStatus.PASSED,
                        f"Created logo ID: {created_logo_id}",
                        elapsed,
                        response.status_code,
                    ))
                else:
                    suite.results.append(self._result(
                        "Create Logo",
                        "/api/channels/logos/",
                        "POST",
                        TestStatus.FAILED,
                        f"Failed: {response.text[:200]}",
                        elapsed,
                        response.status_code,
                    ))
            except Exception as e:
                suite.results.append(self._result(
                    "Create Logo",
                    "/api/channels/logos/",
                    "POST",
                    TestStatus.FAILED,
                    f"Exception: {e}",
                ))

            if created_logo_id:
                # Test: Update Logo
                try:
                    response, elapsed = await self._timed_request(
                        "PATCH",
                        f"/api/channels/logos/{created_logo_id}/",
                        json={"name": "ECM_Test_Logo_Updated"},
                    )
                    suite.results.append(self._result(
                        "Update Logo",
                        f"/api/channels/logos/{{id}}/",
                        "PATCH",
                        TestStatus.PASSED if response.status_code == 200 else TestStatus.FAILED,
                        "Logo updated" if response.status_code == 200 else f"Failed: {response.status_code}",
                        elapsed,
                        response.status_code,
                    ))
                except Exception as e:
                    suite.results.append(self._result(
                        "Update Logo",
                        f"/api/channels/logos/{{id}}/",
                        "PATCH",
                        TestStatus.FAILED,
                        f"Exception: {e}",
                    ))

                # Test: Delete Logo
                try:
                    response, elapsed = await self._timed_request(
                        "DELETE",
                        f"/api/channels/logos/{created_logo_id}/",
                    )
                    if response.status_code in (200, 204):
                        self._created_resources["logos"].remove(created_logo_id)
                    suite.results.append(self._result(
                        "Delete Logo",
                        f"/api/channels/logos/{{id}}/",
                        "DELETE",
                        TestStatus.PASSED if response.status_code in (200, 204) else TestStatus.FAILED,
                        "Logo deleted" if response.status_code in (200, 204) else f"Failed: {response.status_code}",
                        elapsed,
                        response.status_code,
                    ))
                except Exception as e:
                    suite.results.append(self._result(
                        "Delete Logo",
                        f"/api/channels/logos/{{id}}/",
                        "DELETE",
                        TestStatus.FAILED,
                        f"Exception: {e}",
                    ))
        else:
            suite.results.append(self._result(
                "Create Logo",
                "/api/channels/logos/",
                "POST",
                TestStatus.SKIPPED,
                "Read-only mode",
            ))
            suite.results.append(self._result(
                "Update Logo",
                f"/api/channels/logos/{{id}}/",
                "PATCH",
                TestStatus.SKIPPED,
                "Read-only mode",
            ))
            suite.results.append(self._result(
                "Delete Logo",
                f"/api/channels/logos/{{id}}/",
                "DELETE",
                TestStatus.SKIPPED,
                "Read-only mode",
            ))

        self.results.append(suite)
        return suite

    # =========================================================================
    # EPG Tests
    # =========================================================================

    async def test_epg(self) -> TestSuiteResult:
        """Test EPG endpoints."""
        suite = TestSuiteResult(category="EPG")
        test_source_id = None

        # Test: List EPG Sources
        try:
            response, elapsed = await self._timed_request(
                "GET",
                "/api/epg/sources/",
            )
            if response.status_code == 200:
                data = response.json()
                suite.results.append(self._result(
                    "List EPG Sources",
                    "/api/epg/sources/",
                    "GET",
                    TestStatus.PASSED,
                    f"Found {len(data)} EPG sources",
                    elapsed,
                    response.status_code,
                ))
                if data:
                    test_source_id = data[0]["id"]
            else:
                suite.results.append(self._result(
                    "List EPG Sources",
                    "/api/epg/sources/",
                    "GET",
                    TestStatus.FAILED,
                    f"Failed: {response.text[:200]}",
                    elapsed,
                    response.status_code,
                ))
        except Exception as e:
            suite.results.append(self._result(
                "List EPG Sources",
                "/api/epg/sources/",
                "GET",
                TestStatus.FAILED,
                f"Exception: {e}",
            ))

        # Test: Get Single EPG Source
        if test_source_id:
            try:
                response, elapsed = await self._timed_request(
                    "GET",
                    f"/api/epg/sources/{test_source_id}/",
                )
                suite.results.append(self._result(
                    "Get EPG Source",
                    f"/api/epg/sources/{{id}}/",
                    "GET",
                    TestStatus.PASSED if response.status_code == 200 else TestStatus.FAILED,
                    "Retrieved source" if response.status_code == 200 else f"Failed: {response.status_code}",
                    elapsed,
                    response.status_code,
                ))
            except Exception as e:
                suite.results.append(self._result(
                    "Get EPG Source",
                    f"/api/epg/sources/{{id}}/",
                    "GET",
                    TestStatus.FAILED,
                    f"Exception: {e}",
                ))
        else:
            suite.results.append(self._result(
                "Get EPG Source",
                f"/api/epg/sources/{{id}}/",
                "GET",
                TestStatus.SKIPPED,
                "No EPG source available",
            ))

        # Test: List EPG Data (may be paginated dict or list)
        try:
            response, elapsed = await self._timed_request(
                "GET",
                "/api/epg/epgdata/",
                params={"page": 1, "page_size": 10},
            )
            if response.status_code == 200:
                data = response.json()
                # Handle both list and paginated dict responses
                if isinstance(data, list):
                    count = len(data)
                else:
                    count = data.get('count', len(data.get('results', [])))
                suite.results.append(self._result(
                    "List EPG Data",
                    "/api/epg/epgdata/",
                    "GET",
                    TestStatus.PASSED,
                    f"Found {count} EPG entries",
                    elapsed,
                    response.status_code,
                ))
            else:
                suite.results.append(self._result(
                    "List EPG Data",
                    "/api/epg/epgdata/",
                    "GET",
                    TestStatus.FAILED,
                    f"Failed: {response.text[:200]}",
                    elapsed,
                    response.status_code,
                ))
        except Exception as e:
            suite.results.append(self._result(
                "List EPG Data",
                "/api/epg/epgdata/",
                "GET",
                TestStatus.FAILED,
                f"Exception: {e}",
            ))

        # Test: Get EPG Programs (can be slow - large dataset)
        try:
            response, elapsed = await self._timed_request(
                "GET",
                "/api/epg/programs/",
            )
            if response.status_code == 200:
                data = response.json()
                count = len(data) if isinstance(data, list) else len(data.get("results", data.get("data", [])))
                suite.results.append(self._result(
                    "Get EPG Programs",
                    "/api/epg/programs/",
                    "GET",
                    TestStatus.PASSED,
                    f"Retrieved {count} programs ({elapsed:.0f}ms)",
                    elapsed,
                    response.status_code,
                    {"response_format": "list" if isinstance(data, list) else "dict"},
                ))
            else:
                suite.results.append(self._result(
                    "Get EPG Programs",
                    "/api/epg/programs/",
                    "GET",
                    TestStatus.FAILED,
                    f"Failed: {response.text[:200]}",
                    elapsed,
                    response.status_code,
                ))
        except Exception as e:
            suite.results.append(self._result(
                "Get EPG Programs",
                "/api/epg/programs/",
                "GET",
                TestStatus.FAILED,
                f"Exception: {e}",
            ))

        # Test: EPG Import endpoint
        if test_source_id and not self.read_only:
            try:
                response, elapsed = await self._timed_request(
                    "POST",
                    "/api/epg/import/",
                    json={"id": test_source_id},
                )
                suite.results.append(self._result(
                    "Trigger EPG Import",
                    "/api/epg/import/",
                    "POST",
                    TestStatus.PASSED if response.status_code in (200, 202) else TestStatus.WARNING,
                    "Import triggered" if response.status_code in (200, 202) else f"Status: {response.status_code}",
                    elapsed,
                    response.status_code,
                ))
            except Exception as e:
                suite.results.append(self._result(
                    "Trigger EPG Import",
                    "/api/epg/import/",
                    "POST",
                    TestStatus.FAILED,
                    f"Exception: {e}",
                ))
        else:
            suite.results.append(self._result(
                "Trigger EPG Import",
                "/api/epg/import/",
                "POST",
                TestStatus.SKIPPED,
                "Read-only mode or no EPG source",
            ))

        self.results.append(suite)
        return suite

    # =========================================================================
    # Channel Profiles Tests
    # =========================================================================

    async def test_channel_profiles(self) -> TestSuiteResult:
        """Test channel profile endpoints."""
        suite = TestSuiteResult(category="Channel Profiles")
        test_profile_id = None

        # Test: List Channel Profiles
        try:
            response, elapsed = await self._timed_request(
                "GET",
                "/api/channels/profiles/",
            )
            if response.status_code == 200:
                data = response.json()
                suite.results.append(self._result(
                    "List Channel Profiles",
                    "/api/channels/profiles/",
                    "GET",
                    TestStatus.PASSED,
                    f"Found {len(data)} channel profiles",
                    elapsed,
                    response.status_code,
                ))
                if data:
                    test_profile_id = data[0]["id"]
            else:
                suite.results.append(self._result(
                    "List Channel Profiles",
                    "/api/channels/profiles/",
                    "GET",
                    TestStatus.FAILED,
                    f"Failed: {response.text[:200]}",
                    elapsed,
                    response.status_code,
                ))
        except Exception as e:
            suite.results.append(self._result(
                "List Channel Profiles",
                "/api/channels/profiles/",
                "GET",
                TestStatus.FAILED,
                f"Exception: {e}",
            ))

        # Test: Get Single Channel Profile
        if test_profile_id:
            try:
                response, elapsed = await self._timed_request(
                    "GET",
                    f"/api/channels/profiles/{test_profile_id}/",
                )
                suite.results.append(self._result(
                    "Get Channel Profile",
                    f"/api/channels/profiles/{{id}}/",
                    "GET",
                    TestStatus.PASSED if response.status_code == 200 else TestStatus.FAILED,
                    "Retrieved profile" if response.status_code == 200 else f"Failed: {response.status_code}",
                    elapsed,
                    response.status_code,
                ))
            except Exception as e:
                suite.results.append(self._result(
                    "Get Channel Profile",
                    f"/api/channels/profiles/{{id}}/",
                    "GET",
                    TestStatus.FAILED,
                    f"Exception: {e}",
                ))

            # Test: Bulk Update Profile Channels endpoint
            if not self.read_only:
                try:
                    response, elapsed = await self._timed_request(
                        "PATCH",
                        f"/api/channels/profiles/{test_profile_id}/channels/bulk-update/",
                        json={"channels": []},
                    )
                    suite.results.append(self._result(
                        "Bulk Update Profile Channels",
                        f"/api/channels/profiles/{{id}}/channels/bulk-update/",
                        "PATCH",
                        TestStatus.PASSED if response.status_code in (200, 400) else TestStatus.WARNING,
                        "Endpoint accessible" if response.status_code in (200, 400) else f"Status: {response.status_code}",
                        elapsed,
                        response.status_code,
                    ))
                except Exception as e:
                    suite.results.append(self._result(
                        "Bulk Update Profile Channels",
                        f"/api/channels/profiles/{{id}}/channels/bulk-update/",
                        "PATCH",
                        TestStatus.FAILED,
                        f"Exception: {e}",
                    ))
            else:
                suite.results.append(self._result(
                    "Bulk Update Profile Channels",
                    f"/api/channels/profiles/{{id}}/channels/bulk-update/",
                    "PATCH",
                    TestStatus.SKIPPED,
                    "Read-only mode",
                ))
        else:
            suite.results.append(self._result(
                "Get Channel Profile",
                f"/api/channels/profiles/{{id}}/",
                "GET",
                TestStatus.SKIPPED,
                "No channel profile available",
            ))
            suite.results.append(self._result(
                "Bulk Update Profile Channels",
                f"/api/channels/profiles/{{id}}/channels/bulk-update/",
                "PATCH",
                TestStatus.SKIPPED,
                "No channel profile available",
            ))

        # Test: Stream Profiles
        try:
            response, elapsed = await self._timed_request(
                "GET",
                "/api/core/streamprofiles/",
            )
            suite.results.append(self._result(
                "List Stream Profiles",
                "/api/core/streamprofiles/",
                "GET",
                TestStatus.PASSED if response.status_code == 200 else TestStatus.FAILED,
                f"Found {len(response.json()) if response.status_code == 200 else 0} stream profiles",
                elapsed,
                response.status_code,
            ))
        except Exception as e:
            suite.results.append(self._result(
                "List Stream Profiles",
                "/api/core/streamprofiles/",
                "GET",
                TestStatus.FAILED,
                f"Exception: {e}",
            ))

        self.results.append(suite)
        return suite

    # =========================================================================
    # Stats & Monitoring Tests
    # =========================================================================

    async def test_stats(self) -> TestSuiteResult:
        """Test stats and monitoring endpoints."""
        suite = TestSuiteResult(category="Stats & Monitoring")

        # Test: Get Channel Stats
        try:
            response, elapsed = await self._timed_request(
                "GET",
                "/proxy/ts/status",
            )
            suite.results.append(self._result(
                "Get Channel Stats",
                "/proxy/ts/status",
                "GET",
                TestStatus.PASSED if response.status_code == 200 else TestStatus.WARNING,
                "Stats endpoint accessible" if response.status_code == 200 else f"Status: {response.status_code}",
                elapsed,
                response.status_code,
            ))
        except Exception as e:
            suite.results.append(self._result(
                "Get Channel Stats",
                "/proxy/ts/status",
                "GET",
                TestStatus.FAILED,
                f"Exception: {e}",
            ))

        # Test: Get System Events
        try:
            response, elapsed = await self._timed_request(
                "GET",
                "/api/core/system-events/",
                params={"limit": 10, "offset": 0},
            )
            suite.results.append(self._result(
                "Get System Events",
                "/api/core/system-events/",
                "GET",
                TestStatus.PASSED if response.status_code == 200 else TestStatus.FAILED,
                f"Retrieved events" if response.status_code == 200 else f"Failed: {response.status_code}",
                elapsed,
                response.status_code,
            ))
        except Exception as e:
            suite.results.append(self._result(
                "Get System Events",
                "/api/core/system-events/",
                "GET",
                TestStatus.FAILED,
                f"Exception: {e}",
            ))

        self.results.append(suite)
        return suite

    # =========================================================================
    # M3U Refresh Tests
    # =========================================================================

    async def test_m3u_refresh(self) -> TestSuiteResult:
        """Test M3U refresh endpoints."""
        suite = TestSuiteResult(category="M3U Refresh")

        if self.read_only:
            suite.results.append(self._result(
                "Refresh All M3U",
                "/api/m3u/refresh/",
                "POST",
                TestStatus.SKIPPED,
                "Read-only mode",
            ))
            suite.results.append(self._result(
                "Refresh Single M3U",
                "/api/m3u/refresh/{id}/",
                "POST",
                TestStatus.SKIPPED,
                "Read-only mode",
            ))
            self.results.append(suite)
            return suite

        # Test: Refresh All M3U Accounts
        try:
            response, elapsed = await self._timed_request(
                "POST",
                "/api/m3u/refresh/",
            )
            suite.results.append(self._result(
                "Refresh All M3U",
                "/api/m3u/refresh/",
                "POST",
                TestStatus.PASSED if response.status_code in (200, 202) else TestStatus.WARNING,
                "Refresh triggered" if response.status_code in (200, 202) else f"Status: {response.status_code}",
                elapsed,
                response.status_code,
            ))
        except Exception as e:
            suite.results.append(self._result(
                "Refresh All M3U",
                "/api/m3u/refresh/",
                "POST",
                TestStatus.FAILED,
                f"Exception: {e}",
            ))

        # Get an account ID for single refresh test
        try:
            response, _ = await self._timed_request("GET", "/api/m3u/accounts/")
            if response.status_code == 200:
                accounts = response.json()
                if accounts:
                    account_id = accounts[0]["id"]
                    response, elapsed = await self._timed_request(
                        "POST",
                        f"/api/m3u/refresh/{account_id}/",
                    )
                    suite.results.append(self._result(
                        "Refresh Single M3U",
                        "/api/m3u/refresh/{id}/",
                        "POST",
                        TestStatus.PASSED if response.status_code in (200, 202) else TestStatus.WARNING,
                        "Refresh triggered" if response.status_code in (200, 202) else f"Status: {response.status_code}",
                        elapsed,
                        response.status_code,
                    ))
                else:
                    suite.results.append(self._result(
                        "Refresh Single M3U",
                        "/api/m3u/refresh/{id}/",
                        "POST",
                        TestStatus.SKIPPED,
                        "No M3U accounts available",
                    ))
        except Exception as e:
            suite.results.append(self._result(
                "Refresh Single M3U",
                "/api/m3u/refresh/{id}/",
                "POST",
                TestStatus.FAILED,
                f"Exception: {e}",
            ))

        self.results.append(suite)
        return suite

    # =========================================================================
    # Cleanup
    # =========================================================================

    async def cleanup(self) -> None:
        """Clean up any resources created during testing."""
        for resource_type, ids in self._created_resources.items():
            endpoint_map = {
                "channels": "/api/channels/channels",
                "channel_groups": "/api/channels/groups",
                "logos": "/api/channels/logos",
                "epg_sources": "/api/epg/sources",
                "channel_profiles": "/api/channels/profiles",
            }
            endpoint = endpoint_map.get(resource_type)
            if endpoint:
                for resource_id in ids[:]:
                    try:
                        await self._timed_request("DELETE", f"{endpoint}/{resource_id}/")
                        ids.remove(resource_id)
                    except Exception:
                        pass

    # =========================================================================
    # Run All Tests
    # =========================================================================

    async def run_all(self, categories: Optional[list[str]] = None) -> list[TestSuiteResult]:
        """Run all test suites."""
        all_tests = {
            "auth": self.test_auth,
            "channels": self.test_channels,
            "channel_groups": self.test_channel_groups,
            "streams": self.test_streams,
            "m3u_accounts": self.test_m3u_accounts,
            "logos": self.test_logos,
            "epg": self.test_epg,
            "channel_profiles": self.test_channel_profiles,
            "stats": self.test_stats,
            "m3u_refresh": self.test_m3u_refresh,
        }

        # Always run auth first
        await self.test_auth()

        # Check if auth passed
        auth_suite = self.results[0] if self.results else None
        if auth_suite and auth_suite.failed > 0:
            print("\n[!] Authentication failed - cannot proceed with other tests")
            return self.results

        # Run selected or all tests
        tests_to_run = categories if categories else list(all_tests.keys())
        for test_name in tests_to_run:
            if test_name == "auth":
                continue  # Already ran
            test_func = all_tests.get(test_name)
            if test_func:
                await test_func()

        # Cleanup
        await self.cleanup()

        return self.results


def print_results(results: list[TestSuiteResult], verbose: bool = False) -> tuple[int, int, int, int]:
    """Print test results in a formatted way."""
    total_passed = 0
    total_failed = 0
    total_warnings = 0
    total_skipped = 0

    print("\n" + "=" * 70)
    print("DISPATCHARR API COMPATIBILITY TEST RESULTS")
    print("=" * 70)

    for suite in results:
        total_passed += suite.passed
        total_failed += suite.failed
        total_warnings += suite.warnings
        total_skipped += suite.skipped

        # Suite header
        status_icon = "[PASS]" if suite.failed == 0 else "[FAIL]"
        print(f"\n{status_icon} {suite.category}")
        print("-" * 50)

        for result in suite.results:
            icon = {
                TestStatus.PASSED: "[OK]",
                TestStatus.FAILED: "[X]",
                TestStatus.WARNING: "[!]",
                TestStatus.SKIPPED: "[-]",
            }[result.status]

            time_str = f" ({result.response_time_ms:.0f}ms)" if result.response_time_ms > 0 else ""
            print(f"  {icon} {result.name}{time_str}")

            if verbose or result.status == TestStatus.FAILED:
                if result.message:
                    print(f"      {result.message}")
                if result.endpoint:
                    print(f"      Endpoint: {result.method} {result.endpoint}")
                if result.response_code and result.status == TestStatus.FAILED:
                    print(f"      HTTP Status: {result.response_code}")

    # Summary
    print("\n" + "=" * 70)
    print("SUMMARY")
    print("=" * 70)
    print(f"  Passed:   {total_passed}")
    print(f"  Failed:   {total_failed}")
    print(f"  Warnings: {total_warnings}")
    print(f"  Skipped:  {total_skipped}")
    print(f"  Total:    {total_passed + total_failed + total_warnings + total_skipped}")
    print("=" * 70)

    if total_failed == 0:
        print("\n[SUCCESS] All API endpoints are compatible!")
    else:
        print(f"\n[FAILURE] {total_failed} endpoint(s) failed compatibility check")

    return total_passed, total_failed, total_warnings, total_skipped


async def main():
    parser = argparse.ArgumentParser(
        description="Test Dispatcharr API compatibility for ECM",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--url", default=os.environ.get("DISPATCHARR_URL", ""),
                        help="Dispatcharr URL (or set DISPATCHARR_URL env var)")
    parser.add_argument("--username", default=os.environ.get("DISPATCHARR_USERNAME", ""),
                        help="Dispatcharr username (or set DISPATCHARR_USERNAME env var)")
    parser.add_argument("--password", default=os.environ.get("DISPATCHARR_PASSWORD", ""),
                        help="Dispatcharr password (or set DISPATCHARR_PASSWORD env var)")
    parser.add_argument("--read-only", action="store_true",
                        help="Skip create/update/delete tests")
    parser.add_argument("--only", type=str,
                        help="Run only specific test categories (comma-separated: auth,channels,streams,m3u_accounts,logos,epg,channel_profiles,stats,m3u_refresh,channel_groups)")
    parser.add_argument("--verbose", "-v", action="store_true",
                        help="Show detailed output for all tests")
    parser.add_argument("--json", action="store_true",
                        help="Output results as JSON")

    args = parser.parse_args()

    # Validate required args
    if not args.url:
        print("Error: --url or DISPATCHARR_URL environment variable required")
        sys.exit(1)
    if not args.username:
        print("Error: --username or DISPATCHARR_USERNAME environment variable required")
        sys.exit(1)
    if not args.password:
        print("Error: --password or DISPATCHARR_PASSWORD environment variable required")
        sys.exit(1)

    # Parse categories
    categories = None
    if args.only:
        categories = [c.strip() for c in args.only.split(",")]

    # Run tests
    tester = DispatcharrAPITester(
        base_url=args.url,
        username=args.username,
        password=args.password,
        read_only=args.read_only,
    )

    try:
        print(f"\nTesting Dispatcharr API at: {args.url}")
        print(f"Mode: {'Read-only' if args.read_only else 'Full (includes create/update/delete)'}")
        if categories:
            print(f"Categories: {', '.join(categories)}")
        print()

        results = await tester.run_all(categories)

        if args.json:
            # JSON output
            output = {
                "url": args.url,
                "timestamp": datetime.now().isoformat(),
                "read_only": args.read_only,
                "suites": [
                    {
                        "category": suite.category,
                        "passed": suite.passed,
                        "failed": suite.failed,
                        "warnings": suite.warnings,
                        "skipped": suite.skipped,
                        "results": [
                            {
                                "name": r.name,
                                "endpoint": r.endpoint,
                                "method": r.method,
                                "status": r.status.value,
                                "message": r.message,
                                "response_time_ms": r.response_time_ms,
                                "response_code": r.response_code,
                            }
                            for r in suite.results
                        ],
                    }
                    for suite in results
                ],
            }
            print(json.dumps(output, indent=2))
        else:
            _, failed, _, _ = print_results(results, verbose=args.verbose)
            sys.exit(1 if failed > 0 else 0)

    finally:
        await tester.close()


if __name__ == "__main__":
    asyncio.run(main())
