"""Only a THIRD-PARTY PROVIDER credential crosses in cleartext (bead ``…-fmtg0``).

THE INVARIANT, and it is a property rather than a key list::

    A key that authenticates to a THIRD-PARTY PROVIDER crosses in cleartext
    inside the provider sections. A key that authenticates to ECM itself, or to
    a service the OPERATOR runs downstream, never crosses — in any section, at
    any nesting depth, under any future widening of the redactor's denylist.

WHY THE ORIGINAL SET WAS TOO WIDE. Under the PO's 2026-08-22 per-cycle ruling
``tasks.dbas_sync_engine`` preserves credential-class keys inside
``m3u_accounts`` and ``epg_sources``. The SECTION scope was right; the KEY set
was ``_REDACT_KEYS | _PROVIDER_IDENTITY_KEYS`` — 25 keys, including
``smtp_password``, ``plex_token``, ``mcp_api_key``, ``dispatcharr_api_key``,
``telegram_bot_token``, ``discord_webhook_url`` and ``private_key``. None of
those authenticates to an IPTV provider. The constant's docstring justified the
width by asserting that "within these two sections every one of those keys IS a
third-party provider credential" — an assumption, and false on its face for
``smtp_password``.

WHY IT WAS NOT THEORETICAL. Bead ``…-vn63c`` established that Dispatcharr
stores the provider's ``player_api`` reply VERBATIM in
``profiles[].custom_properties`` — a nested blob whose key names nobody
controls. Any credential-shaped key that lands in one of those blobs crossed
PRESERVED rather than sentinelled. :func:`test_an_ecm_own_secret_inside_a_provider_blob_is_still_redacted`
is that case, and it FAILED against the pre-fmtg0 derivation.

HOW THE SUBSET WAS DETERMINED — by reading what Dispatcharr actually puts on
the two entities, not by reading key names. Against Dispatcharr 0.29.0 source
in the live test instance::

    docker exec dbas-sync-testenv-dispatcharr-a-web-1 sh -c \\
      'cat /app/apps/m3u/serializers.py; cat /app/apps/epg/serializers.py;
       grep -nE "^\\s+[a-z_]+ = models\\." /app/apps/m3u/models.py \\
                                           /app/apps/epg/models.py;
       grep -rnE "api_key|apikey|token|secret|passwd|private_key" \\
            /app/apps/m3u /app/apps/epg --include=*.py'

``M3UAccountSerializer`` exposes exactly two credential fields (``username``,
``password``); ``EPGSourceSerializer`` exposes exactly the same two. Neither
model declares an ``api_key``, a token or a secret field, and the grep finds no
such name anywhere in either app outside test fixtures. So the docstring's
"an EPG source given a URL but no ``api_key`` fetches nothing" described a
field that does not exist.

WHAT IS NOT IN THE KEY SET AND MUST STILL CROSS. A plain-M3U account carries
its whole credential in ``server_url``'s query string and an authenticated
XMLTV source carries it in ``url``; both cross because
:func:`tasks.dbas_sync_engine._redact_sync_sections` disables the URL rule for
the provider sections, which is a separate mechanism from ``preserve_keys`` and
is untouched here. The Schedules Direct password is injected onto ``password``
AFTER redaction from the sync target's own encrypted store. All four paths are
pinned below so a future narrowing cannot quietly stop a replica
authenticating.
"""

import json

import pytest

from routers.backup import (
    _PROVIDER_IDENTITY_KEYS,
    _REDACT_KEYS,
    REDACTED,
)
from tasks.dbas_sync_engine import (
    _PROVIDER_AUTH_FIELD_NAMES,
    PROVIDER_CREDENTIAL_KEYS,
    PROVIDER_CREDENTIAL_SECTIONS,
    SCHEDULES_DIRECT_SOURCE_TYPE,
    _inject_schedules_direct_password,
    _redact_sync_sections,
)

# Secret names that authenticate to ECM ITSELF or to a service the OPERATOR
# runs downstream. Not one of them names a third-party IPTV provider, so not
# one of them may ever be preserved. Enumerated as a literal on purpose: this
# file is the place the property is stated, so a reader can audit the list
# without re-deriving it, and a future widening of ``_REDACT_KEYS`` that
# re-admits any of them fails here by name.
ECM_OWN_SECRET_NAMES = frozenset(
    {
        "access_key",
        "access_token",
        "api_key",
        "auth_token",
        "bearer_token",
        "bot_token",
        "client_secret",
        "discord_webhook_url",
        "dispatcharr_api_key",
        "emby_api_key",
        "jellyfin_api_key",
        "mcp_api_key",
        "plex_token",
        "private_key",
        "refresh_token",
        "secret",
        "secret_key",
        "smtp_password",
        "smtp_user",
        "telegram_bot_token",
        "telegram_chat_id",
        "webhook_url",
    }
)

_PROVIDER_USER = "xc-subscriber-7741"
_PROVIDER_PASS = "xc-provider-secret-value"
_SD_PASS = "schedules-direct-stored-secret"
_STD_URL_WITH_QUERY_CREDS = (
    "http://provider.example.test/get.php"
    "?username=std-subscriber&password=std-secret&type=m3u_plus"
)
_XMLTV_URL_WITH_QUERY_CREDS = (
    "http://provider.example.test/xmltv.php?username=xmltv-user&password=xmltv-secret"
)


def _sections_with_an_ecm_secret_in_the_provider_blob() -> dict:
    """A gather whose provider rows carry ECM-own secret NAMES in nested blobs.

    The blob positions are the ones bead ``…-vn63c`` measured live: Dispatcharr
    writes the provider's ``player_api`` reply verbatim into
    ``profiles[].custom_properties``, so its key names are the PROVIDER'S
    vocabulary, not ECM's, and a key that merely collides with an ECM secret
    name lands there without anyone choosing it.
    """
    return {
        "m3u_accounts": [
            {
                "id": 1,
                "name": "Provider XC",
                "account_type": "XC",
                "username": _PROVIDER_USER,
                "password": _PROVIDER_PASS,
                "profiles": [
                    {
                        "id": 1,
                        "custom_properties": {
                            "user_info": {
                                "username": _PROVIDER_USER,
                                "password": _PROVIDER_PASS,
                            },
                            "smtp_password": "ECM-OWN-SMTP-SECRET",
                            "mcp_api_key": "ECM-OWN-MCP-KEY",
                            "dispatcharr_api_key": "ECM-OWN-DISPATCHARR-KEY",
                            "discord_webhook_url": "https://discord.invalid/ECM-OWN-HOOK",
                            "private_key": "ECM-OWN-PRIVATE-KEY",
                        },
                    }
                ],
            }
        ],
        "epg_sources": [
            {
                "id": 1,
                "name": "Provider XMLTV",
                "source_type": "xmltv",
                "username": _PROVIDER_USER,
                "password": _PROVIDER_PASS,
                "custom_properties": {
                    "plex_token": "ECM-OWN-PLEX-TOKEN",
                    "telegram_bot_token": "ECM-OWN-TELEGRAM-TOKEN",
                },
            }
        ],
    }


# ---------------------------------------------------------------------------
# 1. The property, stated over the key set itself.
# ---------------------------------------------------------------------------


def test_no_ecm_own_secret_name_is_ever_preserved():
    """The invariant, by name, over every ECM-own secret the redactor knows."""
    admitted = ECM_OWN_SECRET_NAMES & PROVIDER_CREDENTIAL_KEYS
    assert not admitted, (
        "these authenticate to ECM or to an operator-run service, not to a "
        "third-party provider, and must never cross in cleartext: %s"
        % sorted(admitted)
    )


def test_the_preserved_set_cannot_grow_when_the_redactor_grows():
    """A widening of ``_REDACT_KEYS`` cannot silently re-admit anything.

    This is the STRUCTURAL half, and it is what makes the test above hold for
    names nobody has thought of yet: the preserved set is an INTERSECTION with
    a small, hand-audited list of the field names Dispatcharr actually exposes
    on an M3U account or an EPG source, so nothing added to either denylist can
    reach it without also being added to that list deliberately.
    """
    assert PROVIDER_CREDENTIAL_KEYS <= _PROVIDER_AUTH_FIELD_NAMES
    widened = _REDACT_KEYS | _PROVIDER_IDENTITY_KEYS | {"some_new_ecm_secret"}
    assert "some_new_ecm_secret" not in (widened & _PROVIDER_AUTH_FIELD_NAMES)


def test_every_preserved_key_is_one_the_redactor_would_otherwise_sentinel():
    """The DRIFT protection the derivation exists for, kept.

    The failure this guards is the opposite direction and is the reason the
    original constant was derived rather than written out: a field name added
    to :data:`_PROVIDER_AUTH_FIELD_NAMES` that the redactor does not recognise
    would not be redacted anywhere ELSE either, so it would cross in every
    section rather than only the provider ones — and the intersection would
    silently drop it from the provider sections, so the replica would not even
    get it. Both halves are invisible at runtime; this makes them loud here.
    """
    unknown = _PROVIDER_AUTH_FIELD_NAMES - (_REDACT_KEYS | _PROVIDER_IDENTITY_KEYS)
    assert not unknown, (
        "these are preserved in the provider sections but are not in the "
        "redactor's vocabulary, so they are not redacted anywhere else "
        "either: %s" % sorted(unknown)
    )


def test_the_section_scope_is_unchanged():
    """fmtg0 narrows the KEY set only. ``core_settings`` stays excluded."""
    assert PROVIDER_CREDENTIAL_SECTIONS == frozenset({"m3u_accounts", "epg_sources"})


# ---------------------------------------------------------------------------
# 2. The property, stated over the redacted payload — the behaviour that
#    failed before the fix.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "secret",
    [
        "ECM-OWN-SMTP-SECRET",
        "ECM-OWN-MCP-KEY",
        "ECM-OWN-DISPATCHARR-KEY",
        "ECM-OWN-HOOK",
        "ECM-OWN-PRIVATE-KEY",
        "ECM-OWN-PLEX-TOKEN",
        "ECM-OWN-TELEGRAM-TOKEN",
    ],
)
def test_an_ecm_own_secret_inside_a_provider_blob_is_still_redacted(secret):
    """RED against the pre-fmtg0 derivation: every one of these crossed clear.

    The nesting is the point. ``preserve_keys`` is matched by KEY NAME at every
    depth, so an ECM-own secret name occurring inside a provider row's
    ``custom_properties`` was preserved exactly as the account's own password
    was — and ``custom_properties`` is a verbatim upstream API response whose
    key names ECM does not choose.
    """
    blob = json.dumps(
        _redact_sync_sections(_sections_with_an_ecm_secret_in_the_provider_blob()),
        default=str,
    )
    assert secret not in blob


def test_the_provider_credential_still_crosses_from_the_same_payload():
    """The CONTRAST, so the test above cannot pass by redacting everything."""
    blob = json.dumps(
        _redact_sync_sections(_sections_with_an_ecm_secret_in_the_provider_blob()),
        default=str,
    )
    assert _PROVIDER_USER in blob
    assert _PROVIDER_PASS in blob


# ---------------------------------------------------------------------------
# 3. The four credential paths PR #910 shipped. A replica that stops
#    authenticating is a worse outcome than an over-broad key set.
# ---------------------------------------------------------------------------


def test_xc_account_username_and_password_still_cross():
    sections = {
        "m3u_accounts": [
            {
                "name": "Provider XC",
                "account_type": "XC",
                "username": _PROVIDER_USER,
                "password": _PROVIDER_PASS,
            }
        ]
    }
    row = _redact_sync_sections(sections)["m3u_accounts"][0]
    assert row["username"] == _PROVIDER_USER
    assert row["password"] == _PROVIDER_PASS


def test_plain_m3u_server_url_still_crosses_whole():
    """The credential is in the QUERY STRING; no key name carries it."""
    sections = {
        "m3u_accounts": [
            {
                "name": "Provider STD",
                "account_type": "STD",
                "server_url": _STD_URL_WITH_QUERY_CREDS,
                "username": None,
                "password": "",
            }
        ]
    }
    row = _redact_sync_sections(sections)["m3u_accounts"][0]
    assert row["server_url"] == _STD_URL_WITH_QUERY_CREDS


def test_xmltv_epg_url_still_crosses_whole():
    sections = {
        "epg_sources": [
            {
                "name": "Provider XMLTV",
                "source_type": "xmltv",
                "url": _XMLTV_URL_WITH_QUERY_CREDS,
            }
        ]
    }
    row = _redact_sync_sections(sections)["epg_sources"][0]
    assert row["url"] == _XMLTV_URL_WITH_QUERY_CREDS


def test_the_stored_schedules_direct_password_still_reaches_the_source():
    """Injected onto ``password`` after redaction; ``password`` must survive.

    Injection happens post-redaction, so the write itself is unconditional —
    but ``credential_bearing_records`` reads ``PROVIDER_CREDENTIAL_KEYS`` to
    say WHICH field crossed, so dropping ``password`` from the set would leave
    the journal row silent about the one credential the operator typed.
    """
    sections = _redact_sync_sections(
        {
            "epg_sources": [
                {
                    "name": "Schedules Direct",
                    "source_type": SCHEDULES_DIRECT_SOURCE_TYPE,
                    "username": "sd-subscriber",
                }
            ]
        }
    )
    _inject_schedules_direct_password(sections, _SD_PASS)
    row = sections["epg_sources"][0]
    assert row["password"] == _SD_PASS
    assert row["username"] == "sd-subscriber"
    assert "password" in PROVIDER_CREDENTIAL_KEYS


def test_ecm_own_sections_are_untouched_by_the_narrowing():
    """The half that was already correct stays correct."""
    sections = {
        "settings": [{"key": "smtp_password", "password": "ECM-OWN-SETTINGS-SECRET"}],
        "alert_methods": [{"name": "Ops", "bot_token": "ECM-OWN-BOT-TOKEN"}],
    }
    redacted = _redact_sync_sections(sections)
    assert redacted["settings"][0]["password"] == REDACTED
    assert redacted["alert_methods"][0]["bot_token"] == REDACTED
