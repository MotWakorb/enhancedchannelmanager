"""The two alert-method READ routes must never emit a raw credential value.

Bead enhancedchannelmanager-9kwzp.13.

WHAT WENT WRONG, so the guard is read as a guard rather than as coverage.
``models.AlertMethod.to_dict(include_sensitive=False)`` has masked
``password``, ``bot_token``, ``webhook_url`` and ``api_key`` since it was
written, and its docstring says so. ``GET /api/alert-methods`` and
``GET /api/alert-methods/{id}`` never called it: each hand-rolled its own
response dict and put ``json.loads(m.config)`` into it verbatim, so the
Discord webhook URL, the Telegram bot token and the SMTP password went out in
clear to every permitted caller. That went unnoticed for as long as it did
partly because ``routers/backup.py`` asserted in a comment that its DBAS
redaction denylist stayed "in lock-step with the API-response masking already
shipped to clients", which was simply false, and a reader auditing where alert
credentials can leak got the wrong answer from it.

So the durable half of the fix is not the two ``to_dict`` calls. It is this
file, which pins the PROPERTY (no credential value in a read response) rather
than the implementation, and pins it against the same key tuple
``routers/backup.py`` redacts with, so the API-masking surface and the backup-
redaction surface cannot drift apart again without a test going red.

The gate half of this router is a separate concern and lives in
``tests/test_admin_gate_inventory.py`` and
``tests/routers/test_9kwzp10_12_gate_verdicts.py``. Nothing here asserts who
may call these routes; masking is what a permitted caller gets.

CREDENTIAL FIXTURES. Values are angle-bracket placeholders per
``docs/pytest_conventions.md`` -> "Credential Fixtures in Security Tests":
the ``SECRET`` regex in the former ``scripts/check_secrets.py`` had a ``(?=\\w+)``
lookahead, so a value starting with ``<`` is never a scan candidate. The
masking is keyed on the config KEY and never looks at the value, so nothing
here depends on the fixtures having a realistic shape.
"""
import json

import pytest

from models import AlertMethod
from routers.backup import _ALERT_METHOD_CREDENTIAL_KEYS

# The literal the model substitutes. Deliberately re-stated here rather than
# imported: importing it from the code under test would let a change to the
# mask satisfy this file by construction.
MASK = "********"

# One synthetic value per credential-class key. Distinct strings so a failure
# names which key leaked.
FAKE_CREDENTIALS = {
    "password": "<synthetic-smtp-password>",
    "bot_token": "<synthetic-telegram-bot-token>",
    "webhook_url": "<synthetic-discord-webhook-url>",
    "api_key": "<synthetic-generic-api-key>",
}


def _create_method(session, **overrides):
    """Insert an AlertMethod row directly, bypassing route validation.

    Rows are written straight to the session on purpose: the point is to prove
    what the READ routes emit for a given stored blob, including blobs no
    current writer would produce (every credential key at once).
    """
    defaults = {
        "name": "Test Method",
        "method_type": "discord",
        "enabled": True,
        "config": json.dumps({"webhook_url": FAKE_CREDENTIALS["webhook_url"]}),
        "notify_info": False,
        "notify_success": True,
        "notify_warning": True,
        "notify_error": True,
    }
    defaults.update(overrides)
    method = AlertMethod(**defaults)
    session.add(method)
    session.commit()
    session.refresh(method)
    return method


class TestReadRoutesMaskCredentials:
    """Neither read route may emit a stored credential value."""

    @pytest.mark.asyncio
    async def test_list_masks_every_backup_denylisted_key(self, async_client, test_session):
        """GET /api/alert-methods masks every key routers/backup.py redacts.

        Parametrizing over ``_ALERT_METHOD_CREDENTIAL_KEYS`` is the anti-drift
        half: adding a key to the backup denylist without adding it to the
        model's masking set fails here.
        """
        _create_method(
            test_session,
            name="Everything",
            config=json.dumps(dict(FAKE_CREDENTIALS)),
        )

        response = await async_client.get("/api/alert-methods")
        assert response.status_code == 200
        config = response.json()[0]["config"]

        for key in _ALERT_METHOD_CREDENTIAL_KEYS:
            assert config[key] == MASK, f"{key} was not masked in the list response"

    @pytest.mark.asyncio
    async def test_get_by_id_masks_every_backup_denylisted_key(self, async_client, test_session):
        """GET /api/alert-methods/{id} masks the same set as the list route."""
        method = _create_method(
            test_session,
            name="Everything",
            config=json.dumps(dict(FAKE_CREDENTIALS)),
        )

        response = await async_client.get(f"/api/alert-methods/{method.id}")
        assert response.status_code == 200
        config = response.json()["config"]

        for key in _ALERT_METHOD_CREDENTIAL_KEYS:
            assert config[key] == MASK, f"{key} was not masked in the single-method response"

    @pytest.mark.asyncio
    async def test_no_raw_credential_value_appears_anywhere_in_either_body(
        self, async_client, test_session
    ):
        """The property, asserted against the RAW response text.

        Checking ``config[key]`` proves the intended field is masked. This
        checks the whole serialized body, so a future handler that also
        surfaces a credential somewhere else (a summary line, an echoed error,
        a duplicated blob under another name) fails too.
        """
        method = _create_method(
            test_session,
            name="Everything",
            config=json.dumps(dict(FAKE_CREDENTIALS)),
        )

        list_response = await async_client.get("/api/alert-methods")
        single_response = await async_client.get(f"/api/alert-methods/{method.id}")

        for label, response in (("list", list_response), ("single", single_response)):
            assert response.status_code == 200
            body = response.text
            for key, value in FAKE_CREDENTIALS.items():
                assert value not in body, f"{key} leaked verbatim in the {label} response"

    @pytest.mark.asyncio
    async def test_masks_each_real_method_type(self, async_client, test_session):
        """Discord, Telegram and SMTP rows in one list, as an install has them."""
        _create_method(
            test_session,
            name="Discord Alerts",
            method_type="discord",
            config=json.dumps({"webhook_url": FAKE_CREDENTIALS["webhook_url"]}),
        )
        _create_method(
            test_session,
            name="Telegram Alerts",
            method_type="telegram",
            config=json.dumps({
                "bot_token": FAKE_CREDENTIALS["bot_token"],
                "chat_id": "-1001234567890",
            }),
        )
        _create_method(
            test_session,
            name="Email",
            method_type="smtp",
            config=json.dumps({"to_emails": ["alice@example.com"]}),
        )

        response = await async_client.get("/api/alert-methods")
        assert response.status_code == 200
        by_name = {m["name"]: m for m in response.json()}

        assert by_name["Discord Alerts"]["config"]["webhook_url"] == MASK
        assert by_name["Telegram Alerts"]["config"]["bot_token"] == MASK
        # Not credentials, and callers depend on them: the Settings tab reads
        # `to_emails` back to populate the Email alert recipients field, so
        # over-masking would silently blank an operator's recipient list.
        assert by_name["Telegram Alerts"]["config"]["chat_id"] == "-1001234567890"
        assert by_name["Email"]["config"]["to_emails"] == ["alice@example.com"]

    @pytest.mark.asyncio
    async def test_empty_credential_reads_as_null_not_as_the_mask(
        self, async_client, test_session
    ):
        """A stored empty credential must not read back as ``********``.

        ``to_dict`` emits ``None`` for a falsy value. That distinction is the
        difference between "configured, value withheld" and "not configured",
        and a UI or an operator triaging a silent alert channel needs it.
        """
        method = _create_method(
            test_session,
            name="Unconfigured Discord",
            config=json.dumps({"webhook_url": ""}),
        )

        response = await async_client.get(f"/api/alert-methods/{method.id}")
        assert response.status_code == 200
        assert response.json()["config"]["webhook_url"] is None


class TestReadRouteContract:
    """Masking must not have cost the read routes a field a caller relies on."""

    @pytest.mark.asyncio
    async def test_list_still_carries_every_previously_returned_field(
        self, async_client, test_session
    ):
        """The hand-rolled dict these routes used to build, field for field.

        Switching to ``to_dict`` is a shape change as well as a masking
        change: it ADDS ``updated_at`` (additive, harmless) and must drop
        nothing. ``AlertMethodsSection`` renders id/name/method_type/enabled,
        the Settings tab reads ``config.to_emails``, and the MCP
        ``list_alert_methods`` tool prints the id, name, type, enabled flag
        and all four ``notify_*`` flags.
        """
        _create_method(test_session, name="Contract")

        response = await async_client.get("/api/alert-methods")
        assert response.status_code == 200
        record = response.json()[0]

        for field in (
            "id",
            "name",
            "method_type",
            "enabled",
            "config",
            "notify_info",
            "notify_success",
            "notify_warning",
            "notify_error",
            "alert_sources",
            "last_sent_at",
            "created_at",
        ):
            assert field in record, f"{field} disappeared from the list response"

    @pytest.mark.asyncio
    async def test_get_by_id_still_carries_every_previously_returned_field(
        self, async_client, test_session
    ):
        """Same contract on the single-method read."""
        method = _create_method(test_session, name="Contract")

        response = await async_client.get(f"/api/alert-methods/{method.id}")
        assert response.status_code == 200
        record = response.json()

        for field in (
            "id",
            "name",
            "method_type",
            "enabled",
            "config",
            "notify_info",
            "notify_success",
            "notify_warning",
            "notify_error",
            "alert_sources",
            "last_sent_at",
            "created_at",
        ):
            assert field in record, f"{field} disappeared from the single-method response"

    @pytest.mark.asyncio
    async def test_alert_sources_still_parses_to_an_object(self, async_client, test_session):
        """``alert_sources`` is stored as JSON text and must read back parsed."""
        method = _create_method(
            test_session,
            name="Filtered",
            alert_sources=json.dumps({"version": 1, "probe_failures": {"min_failures": 3}}),
        )

        response = await async_client.get(f"/api/alert-methods/{method.id}")
        assert response.status_code == 200
        assert response.json()["alert_sources"] == {
            "version": 1,
            "probe_failures": {"min_failures": 3},
        }


class TestNoUnmaskedPathOverHTTP:
    """No HTTP caller may reach ``include_sensitive=True``.

    Bead 9kwzp.13 asked for a deliberate verdict on whether any caller should
    ever receive the unmasked form over HTTP. The verdict is NO: every
    legitimate reason to know a stored credential is served by the operator
    re-entering it or by ``POST /api/alert-methods/{id}/test``, which sends
    with the stored value without disclosing it. This test makes adding such a
    path a deliberate edit rather than a one-word default flip.
    """

    def test_no_router_passes_include_sensitive_true(self):
        """Source-level assertion over the whole router package.

        Parsed rather than grepped: these handlers document the verdict in
        their own docstrings, so a substring search would match the prose that
        says the flag is never set and fail on the explanation of the rule.
        """
        import ast
        from pathlib import Path

        import routers

        offenders = []
        for path in sorted(Path(routers.__file__).parent.rglob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                for keyword in node.keywords:
                    if (
                        keyword.arg == "include_sensitive"
                        and isinstance(keyword.value, ast.Constant)
                        and keyword.value.value is True
                    ):
                        offenders.append(f"{path.name}:{node.lineno}")

        assert offenders == [], (
            "a router asks for the unmasked form, which returns stored "
            f"credentials over HTTP: {offenders}"
        )
