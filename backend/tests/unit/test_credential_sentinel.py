"""Tests for the shared redaction-sentinel contract (bead …-6pilh).

The DBAS backup pipeline replaces every credential-class value with ONE
sentinel string before a byte enters the archive. Before this module that
sentinel had no single home and no consumer-side predicate, so restore-side
code could — and did — write the placeholder straight into a destination
credential field, producing an account that LOOKS configured and cannot
authenticate.

Three properties are pinned here:

1. The sentinel is a single shared constant; ``routers.backup.REDACTED``
   is that same value (the artifact producer and the restore consumer can
   never drift apart).
2. ``credential_is_present`` is NOT truthiness — the product's own
   placeholder must read as ABSENT, otherwise every "is a credential set?"
   check reports a dead account as healthy.
3. ``strip_redaction_sentinels`` removes sentinel-valued keys (at any depth)
   rather than forwarding them, and reports which field paths it removed so
   the caller can tell the operator what to re-enter.
"""
from credential_sentinel import (
    REDACTION_SENTINEL,
    credential_is_present,
    is_redaction_sentinel,
    strip_redaction_sentinels,
)


class TestSentinelIdentity:
    def test_sentinel_is_the_value_the_backup_pipeline_writes(self):
        from routers import backup

        assert backup.REDACTED == REDACTION_SENTINEL
        assert REDACTION_SENTINEL == "***REDACTED***"

    def test_is_redaction_sentinel_matches_only_the_exact_string(self):
        assert is_redaction_sentinel("***REDACTED***") is True
        assert is_redaction_sentinel("  ***REDACTED***  ") is False
        assert is_redaction_sentinel("REDACTED") is False
        assert is_redaction_sentinel("") is False
        assert is_redaction_sentinel(None) is False
        assert is_redaction_sentinel(0) is False


class TestCredentialPresence:
    def test_a_real_credential_is_present(self):
        assert credential_is_present("63832936") is True

    def test_the_products_own_placeholder_is_not_present(self):
        # THE regression this whole bead exists for: a truthiness check reports
        # True here, which is how a dead XC account passed a byte-identical
        # before/after inventory diff.
        assert credential_is_present(REDACTION_SENTINEL) is False

    def test_unset_values_are_not_present(self):
        assert credential_is_present("") is False
        assert credential_is_present(None) is False


class TestStripRedactionSentinels:
    def test_sentinel_valued_key_is_removed_not_forwarded(self):
        cleaned, removed = strip_redaction_sentinels(
            {"name": "Infinity", "username": "mot2", "password": REDACTION_SENTINEL}
        )

        assert "password" not in cleaned
        assert cleaned == {"name": "Infinity", "username": "mot2"}
        assert removed == ["password"]

    def test_real_credentials_survive_untouched(self):
        payload = {"name": "Infinity", "username": "mot2", "password": "63832936"}

        cleaned, removed = strip_redaction_sentinels(payload)

        assert cleaned == payload
        assert removed == []

    def test_nested_sentinels_are_removed_and_reported_by_path(self):
        cleaned, removed = strip_redaction_sentinels(
            {
                "name": "Infinity",
                "custom_properties": {
                    "keep": 1,
                    "access_token": REDACTION_SENTINEL,
                },
                "profiles": [{"secret": REDACTION_SENTINEL, "id": 4}],
            }
        )

        assert cleaned == {
            "name": "Infinity",
            "custom_properties": {"keep": 1},
            "profiles": [{"id": 4}],
        }
        assert removed == ["custom_properties.access_token", "profiles[0].secret"]

    def test_input_payload_is_not_mutated(self):
        payload = {"password": REDACTION_SENTINEL, "name": "Infinity"}

        strip_redaction_sentinels(payload)

        assert payload["password"] == REDACTION_SENTINEL

    def test_empty_string_credential_is_left_alone(self):
        # "" is a MEANINGFUL restore value (explicitly unset upstream); only the
        # placeholder is stripped.
        cleaned, removed = strip_redaction_sentinels({"password": ""})

        assert cleaned == {"password": ""}
        assert removed == []


class TestDispatcharrSettingsIsConfigured:
    def test_password_placeholder_does_not_read_as_configured(self):
        from config import DispatcharrSettings

        settings = DispatcharrSettings(
            url="http://dispatcharr:9191",
            auth_method="password",
            username="admin",
            password=REDACTION_SENTINEL,
        )

        assert settings.is_configured() is False

    def test_api_key_placeholder_does_not_read_as_configured(self):
        from config import DispatcharrSettings

        settings = DispatcharrSettings(
            url="http://dispatcharr:9191",
            auth_method="api_key",
            dispatcharr_api_key=REDACTION_SENTINEL,
        )

        assert settings.is_configured() is False

    def test_real_credentials_still_read_as_configured(self):
        from config import DispatcharrSettings

        settings = DispatcharrSettings(
            url="http://dispatcharr:9191",
            auth_method="password",
            username="admin",
            password="hunter2",
        )

        assert settings.is_configured() is True
