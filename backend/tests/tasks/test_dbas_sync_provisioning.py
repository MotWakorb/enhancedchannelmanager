"""One-time credential provisioning (bead enhancedchannelmanager-wd20y).

ADR-013's 2026-08-22 amendment, decisions **S10-S13**, invariants **INV-3**,
**INV-4**, **INV-6**, **INV-8**, **INV-9**; threat model §11.5 rows
**D11-D16**. INV-2 has its own file
(``tests/tasks/test_sync_provisioning_reachability.py``) because it is a
structural guard rather than a behavioural one.

Every test below states a PROPERTY. Where a specific measured case appears — the
XC account, the plain-M3U ``server_url``, the ``xmltv`` ``url``, the Schedules
Direct password — it is an EXAMPLE of the property, never the specification. The
review round that produced that rule is recorded in ``CLAUDE.md``: two competent,
red-proven fixes each closed the reviewer's literal reproduction and left the
defect live by another route.
"""
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from credential_sentinel import REDACTION_SENTINEL
from dbas.restore_contracts import EntityType
from tasks import dbas_sync_provisioning as prov


# ---------------------------------------------------------------------------
# Fixtures — the four credential-bearing types, as they really come back
# ---------------------------------------------------------------------------

XC_PASSWORD = "xc-provider-pass-9931"
XC_USERNAME = "xc-provider-user"
STD_URL = "http://plain.example.com/get.php?username=stdu&password=std-secret-4417"
XMLTV_URL = "http://guide.example.com/xmltv.php?username=epgu&password=epg-secret-8823"


def _xc_account(**over):
    """An Xtream Codes M3U account as ``/api/m3u/accounts/`` returns it.

    Dispatcharr's ``M3UAccountSerializer`` marks ``password`` write_only but its
    ``to_representation`` re-adds it for ``user_level >= 10``, which ECM always
    is — measured on 0.29.0, and bead ``kdz6p`` read the value out of A's
    database 316 times.
    """
    base = {
        "id": 1,
        "name": "Provider XC",
        "account_type": "XC",
        "server_url": "http://xc.example.com",
        "username": XC_USERNAME,
        "password": XC_PASSWORD,
        "max_streams": 4,
        "is_active": True,
    }
    base.update(over)
    return base


def _std_account(**over):
    """A plain-M3U account: the credential is INSIDE ``server_url``.

    There are no username/password fields on this type at all. A design that
    writes "username and password" onto B covers XC and silently misses this.
    """
    base = {
        "id": 2,
        "name": "Provider STD",
        "account_type": "STD",
        "server_url": STD_URL,
        "is_active": True,
    }
    base.update(over)
    return base


def _tuner_account(**over):
    """A plain-M3U HDHomeRun tuner: a LAN URL with no credential at all."""
    base = {
        "id": 3,
        "name": "Living Room Tuner",
        "account_type": "STD",
        "server_url": "http://192.168.1.40/lineup.m3u",
        "is_active": True,
    }
    base.update(over)
    return base


def _xmltv_source(**over):
    base = {
        "id": 11,
        "name": "Guide XMLTV",
        "source_type": "xmltv",
        "url": XMLTV_URL,
        "is_active": True,
    }
    base.update(over)
    return base


def _sd_source(**over):
    """Schedules Direct: ``username`` comes back, ``password`` NEVER does.

    Write-only upstream with no admin re-add, SHA1-hashed at fetch. The absence
    of ``password`` here is not an omission in the fixture — it is the measured
    upstream behaviour the whole D15 finding rests on.
    """
    base = {
        "id": 12,
        "name": "Guide SD",
        "source_type": "schedules_direct",
        "username": "sd-user",
        "is_active": True,
    }
    base.update(over)
    return base


def _target(**over):
    base = {
        "id": 7,
        "name": "standby-b",
        "base_url": "https://b.example.com",
        "insecure": False,
        "credentials_provisioned_at": None,
        "destination_credentials_observed_at": None,
    }
    base.update(over)
    return SimpleNamespace(**base)


class _FakeClient:
    """A destination that records every write it was asked to make.

    ``fail_on`` names the destination ids whose write must raise, which is how
    the INV-9 partial/total-failure paths are exercised without pretending a
    network exists.
    """

    def __init__(self, accounts=None, sources=None, fail_on=()):
        self._accounts = accounts if accounts is not None else []
        self._sources = sources if sources is not None else []
        self.fail_on = set(fail_on)
        self.account_writes: list[tuple[int, dict]] = []
        self.source_writes: list[tuple[int, dict]] = []

    async def get_m3u_accounts(self):
        return self._accounts

    async def get_epg_sources(self):
        return self._sources

    async def patch_m3u_account(self, account_id, data):
        if account_id in self.fail_on:
            raise RuntimeError("destination rejected the write for %s" % account_id)
        self.account_writes.append((account_id, data))
        return {"id": account_id}

    async def update_epg_source(self, source_id, data):
        if source_id in self.fail_on:
            raise RuntimeError("destination rejected the write for %s" % source_id)
        self.source_writes.append((source_id, data))
        return {"id": source_id}

    async def close(self):
        return None

    @property
    def all_writes(self):
        return [*self.account_writes, *self.source_writes]


def _run_with(source_sections, client, monkeypatch):
    """Wire the module's two seams: the local gather and the remote client."""
    monkeypatch.setattr(
        prov, "_gather_dispatcharr_sections", AsyncMock(return_value=source_sections)
    )
    monkeypatch.setattr(prov, "make_remote_client", lambda target: client)


# ---------------------------------------------------------------------------
# INV-6 — the writable field set IS the redactor's own field set
# ---------------------------------------------------------------------------


class TestFieldSetIsDerivedFromTheRedactor:
    """INV-6, and under the harvest it is the only thing standing between
    "the fields we meant" and "whatever the gather returned"."""

    def _fields(self, sections, kind, index=0):
        redacted = prov._redact_sections(sections)
        if kind == "m3u":
            from dbas.restore_contracts import IdRemapTable

            return prov.m3u_credential_paths(
                redacted["m3u_accounts"][index], IdRemapTable()
            )
        return prov.epg_credential_paths(redacted["epg_sources"][index])

    def test_xc_account_yields_username_and_password(self):
        fields = self._fields({"m3u_accounts": [_xc_account()]}, "m3u")
        assert set(fields) >= {"username", "password"}

    def test_plain_m3u_yields_server_url_not_username_password(self):
        """The credential is IN the URL; a user/pass form silently misses it."""
        fields = self._fields({"m3u_accounts": [_std_account()]}, "m3u")
        assert "server_url" in fields
        assert "username" not in fields and "password" not in fields

    def test_xmltv_source_yields_url(self):
        fields = self._fields({"epg_sources": [_xmltv_source()]}, "epg")
        assert "url" in fields

    def test_credential_free_tuner_yields_nothing(self):
        """A faithful absence is not a shortfall (bead …-15g1j)."""
        assert self._fields({"m3u_accounts": [_tuner_account()]}, "m3u") == []

    def test_schedules_direct_password_is_never_a_derived_field(self):
        """Because it never enters the gather — absence here means UNREADABLE.

        This is the whole of D15: a presence-driven reporter can never name it,
        which is why the statement must be driven by ``source_type``.
        """
        fields = self._fields({"epg_sources": [_sd_source()]}, "epg")
        assert "password" not in fields

    def test_the_field_set_is_not_a_literal_list(self):
        """A NEW credential-named key on a record joins the set automatically.

        The property INV-6 actually asserts: the provisionable set tracks the
        redactor, so it cannot drift. A maintained literal would pass every test
        above and fail this one.
        """
        fields = self._fields(
            {"m3u_accounts": [_xc_account(auth_token="a-brand-new-secret-value")]},
            "m3u",
        )
        assert "auth_token" in fields

    def test_harvest_reads_the_raw_values_at_the_redactors_paths(self):
        raw = _xc_account()
        values = prov._harvest_values(raw, ["username", "password"])
        assert values == {"username": XC_USERNAME, "password": XC_PASSWORD}

    def test_harvest_refuses_a_sentinel_valued_path(self):
        """A placeholder is ABSENT, never a credential to push (bead …-6pilh).

        Writing ``***REDACTED***`` onto B produces an account that LOOKS
        configured, authenticates nowhere and materializes zero streams — worse
        than an empty password, which is visibly incomplete.
        """
        raw = _xc_account(password=REDACTION_SENTINEL)
        assert prov._harvest_values(raw, ["password"]) == {}

    def test_cached_provider_response_paths_are_never_written(self):
        """``custom_properties.user_info.*`` is B's own cache of the reply.

        There is no field to write them into and B rewrites the blob itself on
        its next successful refresh (bead …-posm1). They are excluded by the
        SHIPPED predicate, not by a list maintained here.
        """
        writable, unwritable = prov._partition_paths(
            [
                "password",
                "profiles[0].custom_properties.user_info.password",
                "profiles[0].custom_properties.user_info.username",
            ]
        )
        assert writable == ["password"]
        assert unwritable == []


# ---------------------------------------------------------------------------
# INV-4 / S11 — the insecure gate, symmetric, at the service layer
# ---------------------------------------------------------------------------


class TestInsecureGate:
    def test_provisioning_is_refused_on_an_insecure_target(self):
        reason = prov.provision_refusal_reason(_target(insecure=True))
        assert reason and "TLS verification is disabled" in reason
        assert "Turn TLS verification back on" in reason, (
            "a refusal must state the remedy, not only the reason"
        )

    def test_provisioning_is_allowed_on_a_verified_target(self):
        assert prov.provision_refusal_reason(_target(insecure=False)) is None

    def test_enabling_insecure_is_refused_on_a_recorded_provisioned_target(self):
        target = _target(credentials_provisioned_at=datetime.now(timezone.utc))
        reason = prov.insecure_refusal_reason(target, requested_insecure=True)
        assert reason and "De-provision" in reason

    def test_enabling_insecure_is_refused_on_an_OBSERVED_credential(self):
        """The half the recorded marker is structurally blind to (row D16).

        The operator entered the provider credential on B BY HAND — the recovery
        ECM's own guide documents — so ECM never wrote it and
        ``credentials_provisioned_at`` is NULL. The per-cycle destination read
        still carries that credential back to A on every cycle.
        """
        target = _target(
            destination_credentials_observed_at=datetime.now(timezone.utc)
        )
        reason = prov.insecure_refusal_reason(target, requested_insecure=True)
        assert reason is not None

    def test_the_observed_refusal_does_not_offer_de_provision_as_the_remedy(self):
        """An observed credential ECM did not write has no marker to clear.

        Naming the wrong remedy is worse than naming none: the operator
        de-provisions, nothing changes on B because ECM wrote nothing there, and
        they conclude the control is broken.
        """
        target = _target(
            destination_credentials_observed_at=datetime.now(timezone.utc)
        )
        reason = prov.insecure_refusal_reason(target, requested_insecure=True)
        assert "cannot de-provision what it did not provision" in reason
        assert "clear the credential on the replica" in reason

    def test_clearing_insecure_is_always_allowed(self):
        """True -> False can only tighten, on either half of the predicate."""
        for target in (
            _target(insecure=True, credentials_provisioned_at=datetime.now(timezone.utc)),
            _target(
                insecure=True,
                destination_credentials_observed_at=datetime.now(timezone.utc),
            ),
        ):
            assert prov.insecure_refusal_reason(target, requested_insecure=False) is None

    def test_an_unprovisioned_unobserved_target_may_set_insecure(self):
        assert (
            prov.insecure_refusal_reason(_target(), requested_insecure=True) is None
        )

    def test_target_holds_credentials_is_recorded_OR_observed(self):
        """The predicate as a property, over every combination."""
        now = datetime.now(timezone.utc)
        assert not prov.target_holds_credentials(_target())
        assert prov.target_holds_credentials(_target(credentials_provisioned_at=now))
        assert prov.target_holds_credentials(
            _target(destination_credentials_observed_at=now)
        )
        assert prov.target_holds_credentials(
            _target(credentials_provisioned_at=now, destination_credentials_observed_at=now)
        )

    @pytest.mark.asyncio
    async def test_provision_raises_rather_than_writing_on_an_insecure_target(self):
        """The refusal happens BEFORE any destination write is attempted."""
        client = _FakeClient()
        with patch.object(prov, "make_remote_client", lambda t: client):
            with pytest.raises(prov.ProvisioningRefused):
                await prov.provision_target_credentials(
                    session=None, sync_target=_target(insecure=True)
                )
        assert client.all_writes == []


# ---------------------------------------------------------------------------
# S10 — the provisioning action itself
# ---------------------------------------------------------------------------


class TestProvisioning:
    @pytest.mark.asyncio
    async def test_writes_every_credential_bearing_type_to_the_replica(
        self, monkeypatch
    ):
        """The acceptance property, over all four types at once.

        Not "the XC case works" — the XC case is one example. A replica whose
        plain-M3U or xmltv credential did not cross still cannot serve or guide
        the channels fed by it.
        """
        client = _FakeClient(
            accounts=[
                {"id": 101, "name": "Provider XC"},
                {"id": 102, "name": "Provider STD"},
                {"id": 103, "name": "Living Room Tuner"},
            ],
            sources=[
                {"id": 201, "name": "Guide XMLTV", "source_type": "xmltv"},
            ],
        )
        _run_with(
            {
                "m3u_accounts": [_xc_account(), _std_account(), _tuner_account()],
                "epg_sources": [_xmltv_source()],
            },
            client,
            monkeypatch,
        )
        target = _target()
        outcome = await prov.provision_target_credentials(
            session=None, sync_target=target
        )

        assert outcome.succeeded
        assert dict(client.account_writes)[101] == {
            "username": XC_USERNAME,
            "password": XC_PASSWORD,
        }
        assert dict(client.account_writes)[102] == {"server_url": STD_URL}
        assert dict(client.source_writes)[201] == {"url": XMLTV_URL}
        # The tuner has no credential, so it is not written to at all.
        assert 103 not in dict(client.account_writes)
        assert target.credentials_provisioned_at is not None

    @pytest.mark.asyncio
    async def test_only_the_two_provisionable_categories_are_ever_gathered(
        self, monkeypatch
    ):
        """Row D14: a harvest is a loop, and a loop widens by accident.

        The gather is called with a CLOSED set. ECM's own settings secrets,
        alert-method secrets, cloud/sync-target credentials and
        ``dispatcharr_users`` cannot become provisioning inputs by a gather
        starting to return more.
        """
        gather = AsyncMock(return_value={"m3u_accounts": [], "epg_sources": []})
        monkeypatch.setattr(prov, "_gather_dispatcharr_sections", gather)
        monkeypatch.setattr(prov, "make_remote_client", lambda t: _FakeClient())
        await prov.provision_target_credentials(session=None, sync_target=_target())
        assert gather.await_args.args[0] == {"m3u_accounts", "epg_sources"}

    @pytest.mark.asyncio
    async def test_the_provisionable_set_is_narrower_than_the_sync_set(self):
        """Two allowlists, two owners, neither inheriting the other's coverage."""
        from tasks.dbas_sync_engine import SYNC_ALL_CATEGORIES

        assert prov.PROVISIONABLE_SECTIONS < SYNC_ALL_CATEGORIES
        assert "users" not in prov.PROVISIONABLE_SECTIONS
        assert "channels" not in prov.PROVISIONABLE_SECTIONS
        assert "logos" not in prov.PROVISIONABLE_SECTIONS

    @pytest.mark.asyncio
    async def test_an_unmatched_source_account_is_named_not_silent(self, monkeypatch):
        client = _FakeClient(accounts=[], sources=[])
        _run_with({"m3u_accounts": [_xc_account()], "epg_sources": []}, client, monkeypatch)
        outcome = await prov.provision_target_credentials(
            session=None, sync_target=_target()
        )
        assert outcome.unmatched == ["Provider XC"]
        assert client.all_writes == []

    @pytest.mark.asyncio
    async def test_re_running_is_the_rotation_control(self, monkeypatch):
        """S12(a): the same action, re-run, needs no input under the harvest.

        The second run reads A's CURRENT values, so a rotated provider password
        crosses without the operator typing anything.
        """
        client = _FakeClient(accounts=[{"id": 101, "name": "Provider XC"}], sources=[])
        _run_with({"m3u_accounts": [_xc_account()], "epg_sources": []}, client, monkeypatch)
        target = _target()
        await prov.provision_target_credentials(session=None, sync_target=target)

        rotated = "xc-provider-pass-ROTATED"
        _run_with(
            {"m3u_accounts": [_xc_account(password=rotated)], "epg_sources": []},
            client,
            monkeypatch,
        )
        await prov.provision_target_credentials(session=None, sync_target=target)
        assert client.account_writes[-1][1]["password"] == rotated


# ---------------------------------------------------------------------------
# INV-3 — nothing is persisted on A for this purpose
# ---------------------------------------------------------------------------


class TestNothingIsPersistedOnA:
    @pytest.mark.asyncio
    async def test_no_harvested_value_lands_on_the_sync_target_row(self, monkeypatch):
        """INV-3, asserted over the WHOLE row rather than over named columns.

        A named-column check would pass while a future column carried the value.
        This scans every attribute for every secret the fixtures hold.
        """
        client = _FakeClient(
            accounts=[{"id": 101, "name": "Provider XC"}, {"id": 102, "name": "Provider STD"}],
            sources=[{"id": 201, "name": "Guide XMLTV", "source_type": "xmltv"}],
        )
        _run_with(
            {
                "m3u_accounts": [_xc_account(), _std_account()],
                "epg_sources": [_xmltv_source()],
            },
            client,
            monkeypatch,
        )
        target = _target()
        await prov.provision_target_credentials(session=None, sync_target=target)

        blob = repr(vars(target))
        for secret in (XC_PASSWORD, XC_USERNAME, STD_URL, XMLTV_URL, "std-secret-4417"):
            assert secret not in blob, (
                "a harvested provider credential was persisted on the SyncTarget "
                "row (INV-3): %r" % secret
            )

    @pytest.mark.asyncio
    async def test_the_marker_is_a_timestamp(self, monkeypatch):
        client = _FakeClient(accounts=[{"id": 101, "name": "Provider XC"}], sources=[])
        _run_with({"m3u_accounts": [_xc_account()], "epg_sources": []}, client, monkeypatch)
        target = _target()
        await prov.provision_target_credentials(session=None, sync_target=target)
        assert isinstance(target.credentials_provisioned_at, datetime)

    @pytest.mark.asyncio
    async def test_no_credential_value_reaches_the_outcome_or_the_audit_shape(
        self, monkeypatch
    ):
        """S13: no value, no fragment of a value, no masked tail of a value."""
        client = _FakeClient(
            accounts=[{"id": 101, "name": "Provider XC"}, {"id": 102, "name": "Provider STD"}],
            sources=[{"id": 201, "name": "Guide XMLTV", "source_type": "xmltv"}],
        )
        _run_with(
            {
                "m3u_accounts": [_xc_account(), _std_account()],
                "epg_sources": [_xmltv_source()],
            },
            client,
            monkeypatch,
        )
        outcome = await prov.provision_target_credentials(
            session=None, sync_target=_target()
        )
        blob = repr(outcome.as_response())
        for secret in (XC_PASSWORD, XC_USERNAME, STD_URL, XMLTV_URL):
            assert secret not in blob
        # Not even a tail: the last four characters of the password.
        assert XC_PASSWORD[-4:] not in blob
        # But the FIELD NAMES are there — that is what the row is for.
        assert {"username", "password", "server_url", "url"} <= set(
            outcome.field_names
        )


# ---------------------------------------------------------------------------
# S13 — audit
# ---------------------------------------------------------------------------


class TestAudit:
    @pytest.mark.asyncio
    async def test_a_successful_provisioning_writes_exactly_one_row(self, monkeypatch):
        client = _FakeClient(accounts=[{"id": 101, "name": "Provider XC"}], sources=[])
        _run_with({"m3u_accounts": [_xc_account()], "epg_sources": []}, client, monkeypatch)
        log = MagicMock()
        monkeypatch.setattr(prov.journal, "log_entry", log)
        await prov.provision_target_credentials(
            session=None, sync_target=_target(), actor="alice", surface=prov.SURFACE_REST
        )
        assert log.call_count == 1
        kwargs = log.call_args.kwargs
        assert kwargs["action_type"] == prov.PROVISION_ACTION_TYPE
        after = kwargs["after_value"]
        assert after["actor"] == "alice"
        assert after["surface"] == prov.SURFACE_REST
        assert after["tls_verified"] is True
        assert after["fields"] == ["password", "username"]
        assert after["accounts_written"] == 1
        assert after["accounts"][0]["name"] == "Provider XC"
        assert after["accounts"][0]["destination_id"] == 101

    @pytest.mark.asyncio
    async def test_a_FAILED_provisioning_also_writes_exactly_one_row(self, monkeypatch):
        """Success AND failure both record. A failed attempt that leaves no
        trace is the same blind spot ``msqf7`` was filed for, inverted."""
        client = _FakeClient(
            accounts=[{"id": 101, "name": "Provider XC"}], sources=[], fail_on={101}
        )
        _run_with({"m3u_accounts": [_xc_account()], "epg_sources": []}, client, monkeypatch)
        log = MagicMock()
        monkeypatch.setattr(prov.journal, "log_entry", log)
        outcome = await prov.provision_target_credentials(
            session=None, sync_target=_target()
        )
        assert not outcome.succeeded
        assert log.call_count == 1
        assert log.call_args.kwargs["after_value"]["succeeded"] is False

    @pytest.mark.asyncio
    async def test_the_mcp_surface_is_recorded_distinctly(self, monkeypatch):
        client = _FakeClient(accounts=[{"id": 101, "name": "Provider XC"}], sources=[])
        _run_with({"m3u_accounts": [_xc_account()], "epg_sources": []}, client, monkeypatch)
        log = MagicMock()
        monkeypatch.setattr(prov.journal, "log_entry", log)
        await prov.provision_target_credentials(
            session=None, sync_target=_target(), surface=prov.SURFACE_MCP
        )
        assert log.call_args.kwargs["after_value"]["surface"] == prov.SURFACE_MCP

    def test_the_two_action_types_are_distinct_and_greppable(self):
        assert prov.PROVISION_ACTION_TYPE != prov.DEPROVISION_ACTION_TYPE
        assert prov.PROVISION_ACTION_TYPE == "sync_provision_credentials"

    def test_no_journal_row_carries_a_value_in_its_description(self, monkeypatch):
        """The description line is prose and prose is where a value slips in."""
        outcome = prov.ProvisioningOutcome(
            action=prov.PROVISION_ACTION_TYPE,
            target_id=7,
            target_name="standby-b",
            tls_verified=True,
            written=[
                prov.AccountWrite(
                    entity_type=EntityType.M3U_ACCOUNT,
                    destination_id=101,
                    label="Provider XC",
                    fields=["password", "username"],
                    ok=True,
                )
            ],
        )
        log = MagicMock()
        monkeypatch.setattr(prov.journal, "log_entry", log)
        prov._journal_provisioning(
            action_type=prov.PROVISION_ACTION_TYPE,
            outcome=outcome,
            actor="alice",
            surface=prov.SURFACE_REST,
        )
        description = log.call_args.kwargs["description"]
        assert "password" in description  # the NAME
        assert XC_PASSWORD not in description  # never the value


class TestACycleNeverWritesAProvisioningRow:
    """The property that makes the audit row a DETECTOR, not just a record.

    A ``sync_provision_credentials`` row whose actor is the scheduler is not a
    log line, it is THE ALARM — the only detector for D12, the failure mode of
    the one-time path becoming recurring.
    """

    def test_no_cycle_code_path_emits_the_provisioning_action_types(self):
        import subprocess

        from pathlib import Path

        backend = Path(prov.__file__).resolve().parent.parent
        hits = subprocess.run(
            [
                "grep", "-rn",
                "sync_provision_credentials\\|sync_deprovision_credentials",
                str(backend / "tasks"),
                str(backend / "dbas"),
                str(backend / "routers"),
                "--include=*.py",
            ],
            capture_output=True,
            text=True,
        ).stdout.splitlines()
        emitters = {
            line.split(":")[0]
            for line in hits
            if "PROVISION_ACTION_TYPE" in line or "sync_provision_credentials" in line
        }
        offenders = {
            path
            for path in emitters
            if "dbas_sync_provisioning" not in path
        }
        assert not offenders, (
            "a module other than the provisioning writer names a provisioning "
            "action type; a cycle must never be able to write one: %r"
            % sorted(offenders)
        )


# ---------------------------------------------------------------------------
# INV-9 — de-provision honesty. The failure paths ARE the invariant.
# ---------------------------------------------------------------------------


class TestDeprovisionHonesty:
    @staticmethod
    def _provisioned_target():
        return _target(credentials_provisioned_at=datetime(2026, 8, 22, tzinfo=timezone.utc))

    @pytest.mark.asyncio
    async def test_a_successful_clear_writes_to_B_and_then_clears_the_marker(
        self, monkeypatch
    ):
        client = _FakeClient(accounts=[{"id": 101, "name": "Provider XC"}], sources=[])
        _run_with({"m3u_accounts": [_xc_account()], "epg_sources": []}, client, monkeypatch)
        target = self._provisioned_target()
        outcome = await prov.deprovision_target_credentials(
            session=None, sync_target=target
        )
        assert outcome.succeeded
        assert dict(client.account_writes)[101] == {"username": "", "password": ""}
        assert target.credentials_provisioned_at is None
        assert outcome.marker_set is False

    @pytest.mark.asyncio
    async def test_a_TOTAL_failure_leaves_the_marker_SET(self, monkeypatch):
        client = _FakeClient(
            accounts=[{"id": 101, "name": "Provider XC"}], sources=[], fail_on={101}
        )
        _run_with({"m3u_accounts": [_xc_account()], "epg_sources": []}, client, monkeypatch)
        target = self._provisioned_target()
        outcome = await prov.deprovision_target_credentials(
            session=None, sync_target=target
        )
        assert not outcome.succeeded
        assert target.credentials_provisioned_at is not None, (
            "a de-provision that did not clear B cleared the marker anyway "
            "(INV-9) — the marker means 'B may still hold a credential', and a "
            "failed clear is exactly that state"
        )

    @pytest.mark.asyncio
    async def test_a_PARTIAL_failure_leaves_the_marker_SET(self, monkeypatch):
        """One of two accounts cleared. There is no 'close enough'."""
        client = _FakeClient(
            accounts=[
                {"id": 101, "name": "Provider XC"},
                {"id": 102, "name": "Provider STD"},
            ],
            sources=[],
            fail_on={102},
        )
        _run_with(
            {"m3u_accounts": [_xc_account(), _std_account()], "epg_sources": []},
            client,
            monkeypatch,
        )
        target = self._provisioned_target()
        outcome = await prov.deprovision_target_credentials(
            session=None, sync_target=target
        )
        assert not outcome.succeeded
        assert target.credentials_provisioned_at is not None
        assert len(outcome.written) == 1 and len(outcome.failed) == 1

    @pytest.mark.asyncio
    async def test_a_failed_clear_leaves_insecure_STILL_REFUSED(self, monkeypatch):
        """The consequence of the marker staying set, asserted end to end.

        The marker is not decorative: a failed clear must not open the door the
        gate exists to hold shut.
        """
        client = _FakeClient(
            accounts=[{"id": 101, "name": "Provider XC"}], sources=[], fail_on={101}
        )
        _run_with({"m3u_accounts": [_xc_account()], "epg_sources": []}, client, monkeypatch)
        target = self._provisioned_target()
        await prov.deprovision_target_credentials(session=None, sync_target=target)
        assert prov.insecure_refusal_reason(target, requested_insecure=True) is not None

    @pytest.mark.asyncio
    async def test_a_failed_clear_NAMES_the_accounts_still_holding_a_credential(
        self, monkeypatch
    ):
        client = _FakeClient(
            accounts=[
                {"id": 101, "name": "Provider XC"},
                {"id": 102, "name": "Provider STD"},
            ],
            sources=[],
            fail_on={102},
        )
        _run_with(
            {"m3u_accounts": [_xc_account(), _std_account()], "epg_sources": []},
            client,
            monkeypatch,
        )
        outcome = await prov.deprovision_target_credentials(
            session=None, sync_target=self._provisioned_target()
        )
        assert [entry.label for entry in outcome.failed] == ["Provider STD"]

    @pytest.mark.asyncio
    async def test_a_destination_error_cannot_be_swallowed_into_a_success(
        self, monkeypatch
    ):
        """The specific mutation INV-9 exists to catch.

        Any exception shape from the destination — a timeout, a 500, a
        connection reset — must land as a FAILED write, not as an ignored one.
        """
        for boom in (RuntimeError, TimeoutError, ConnectionError, ValueError):

            class _Exploding(_FakeClient):
                async def patch_m3u_account(self, account_id, data):
                    raise boom("destination is unhappy")

            client = _Exploding(accounts=[{"id": 101, "name": "Provider XC"}], sources=[])
            _run_with(
                {"m3u_accounts": [_xc_account()], "epg_sources": []}, client, monkeypatch
            )
            target = self._provisioned_target()
            outcome = await prov.deprovision_target_credentials(
                session=None, sync_target=target
            )
            assert not outcome.succeeded, "%s was swallowed into a success" % boom
            assert target.credentials_provisioned_at is not None

    @pytest.mark.asyncio
    async def test_the_operator_is_told_what_a_de_provision_cannot_guarantee(
        self, monkeypatch
    ):
        """At the moment of the action, not in a doc.

        The residual ships with EVERY de-provision — including a failed one,
        where "it still works" is most likely to be misread as evidence.
        """
        client = _FakeClient(accounts=[{"id": 101, "name": "Provider XC"}], sources=[])
        _run_with({"m3u_accounts": [_xc_account()], "epg_sources": []}, client, monkeypatch)
        outcome = await prov.deprovision_target_credentials(
            session=None, sync_target=self._provisioned_target()
        )
        statement = outcome.as_response()["residual_statement"]
        assert "NOT revocation" in statement
        assert "stream rows" in statement
        assert "does not immediately go dark" in statement

    @pytest.mark.asyncio
    async def test_deprovision_clears_the_same_derived_set_the_provision_wrote(
        self, monkeypatch
    ):
        """INV-6's second half, over every type rather than over XC alone."""
        accounts = [{"id": 101, "name": "Provider XC"}, {"id": 102, "name": "Provider STD"}]
        sources = [{"id": 201, "name": "Guide XMLTV", "source_type": "xmltv"}]
        sections = {
            "m3u_accounts": [_xc_account(), _std_account()],
            "epg_sources": [_xmltv_source()],
        }

        wrote = _FakeClient(accounts=accounts, sources=sources)
        _run_with(sections, wrote, monkeypatch)
        await prov.provision_target_credentials(session=None, sync_target=_target())

        cleared = _FakeClient(accounts=accounts, sources=sources)
        _run_with(sections, cleared, monkeypatch)
        await prov.deprovision_target_credentials(
            session=None, sync_target=self._provisioned_target()
        )

        def _paths(client):
            return {dest: sorted(data) for dest, data in client.all_writes}

        provisioned_paths = _paths(wrote)
        cleared_paths = _paths(cleared)
        # The SD password is additionally always cleared (ECM cannot read the
        # field to know whether it wrote one), so cleared is a superset.
        for dest, fields in provisioned_paths.items():
            assert set(fields) <= set(cleared_paths[dest])
        assert all(
            value == "" for _, data in cleared.all_writes for value in data.values()
        )

    @pytest.mark.asyncio
    async def test_deprovision_is_NOT_refused_on_an_insecure_target(self, monkeypatch):
        """An operator whose certificate broke must still be able to stop B
        re-authenticating; refusing would trap them in the state the gate
        exists to end."""
        client = _FakeClient(accounts=[{"id": 101, "name": "Provider XC"}], sources=[])
        _run_with({"m3u_accounts": [_xc_account()], "epg_sources": []}, client, monkeypatch)
        target = _target(
            insecure=True, credentials_provisioned_at=datetime.now(timezone.utc)
        )
        outcome = await prov.deprovision_target_credentials(
            session=None, sync_target=target
        )
        assert outcome.succeeded
        assert target.credentials_provisioned_at is None


# ---------------------------------------------------------------------------
# D15 — Schedules Direct, stated by source_type and never by presence
# ---------------------------------------------------------------------------


class TestSchedulesDirect:
    @pytest.mark.asyncio
    async def test_an_SD_source_always_produces_a_statement(self, monkeypatch):
        client = _FakeClient(
            accounts=[],
            sources=[{"id": 202, "name": "Guide SD", "source_type": "schedules_direct"}],
        )
        _run_with({"m3u_accounts": [], "epg_sources": [_sd_source()]}, client, monkeypatch)
        outcome = await prov.provision_target_credentials(
            session=None, sync_target=_target()
        )
        assert len(outcome.schedules_direct_notes) == 1
        note = outcome.schedules_direct_notes[0]
        assert "Guide SD" in note
        assert "unreadable, not unset" in note
        assert "still serves video" in note

    def test_the_statement_is_driven_by_source_type_not_by_presence(self):
        """The property D15 turns on.

        An SD source with NOTHING credential-shaped on it — no username, no
        password, nothing the redactor could ever name — still produces the
        statement. A presence check would produce silence, and the operator
        would read silence as "fully provisioned".
        """
        bare = {"id": 99, "name": "Bare SD", "source_type": "schedules_direct"}
        notes = prov.schedules_direct_notes([bare], password_supplied=False)
        assert len(notes) == 1 and "Bare SD" in notes[0]

    def test_no_statement_for_source_types_that_do_not_need_one(self):
        notes = prov.schedules_direct_notes(
            [_xmltv_source(), {"id": 5, "name": "Dummy", "source_type": "dummy"}],
            password_supplied=False,
        )
        assert notes == []

    @pytest.mark.asyncio
    async def test_a_supplied_SD_password_is_written_and_not_persisted(
        self, monkeypatch
    ):
        """Request-scoped: applied to this run's SD sources, then gone."""
        client = _FakeClient(
            accounts=[],
            sources=[{"id": 202, "name": "Guide SD", "source_type": "schedules_direct"}],
        )
        _run_with({"m3u_accounts": [], "epg_sources": [_sd_source()]}, client, monkeypatch)
        target = _target()
        outcome = await prov.provision_target_credentials(
            session=None,
            sync_target=target,
            schedules_direct_password="sd-typed-secret-7712",
        )
        assert dict(client.source_writes)[202]["password"] == "sd-typed-secret-7712"
        assert "sd-typed-secret-7712" not in repr(vars(target))
        assert "sd-typed-secret-7712" not in repr(outcome.as_response())

    @pytest.mark.asyncio
    async def test_the_statement_still_ships_when_a_password_was_supplied(
        self, monkeypatch
    ):
        """Silence is only permitted when it is true — and it is not here.

        The operator supplied a value for THIS run; nothing was persisted, so
        the next run needs it again. Saying nothing would train them to expect
        it to stick.
        """
        client = _FakeClient(
            accounts=[],
            sources=[{"id": 202, "name": "Guide SD", "source_type": "schedules_direct"}],
        )
        _run_with({"m3u_accounts": [], "epg_sources": [_sd_source()]}, client, monkeypatch)
        outcome = await prov.provision_target_credentials(
            session=None, sync_target=_target(), schedules_direct_password="x"
        )
        assert outcome.schedules_direct_notes
        assert "Nothing was persisted here" in outcome.schedules_direct_notes[0]

    @pytest.mark.asyncio
    async def test_an_SD_password_is_never_harvested_from_A(self, monkeypatch):
        """There is nothing on A to harvest — the value never enters the process."""
        client = _FakeClient(
            accounts=[],
            sources=[{"id": 202, "name": "Guide SD", "source_type": "schedules_direct"}],
        )
        _run_with({"m3u_accounts": [], "epg_sources": [_sd_source()]}, client, monkeypatch)
        await prov.provision_target_credentials(session=None, sync_target=_target())
        written = dict(client.source_writes).get(202, {})
        assert "password" not in written


# ---------------------------------------------------------------------------
# INV-8 / S12 — staleness, without a value comparison and without a push
# ---------------------------------------------------------------------------


class TestStaleness:
    def test_an_errored_account_with_no_streams_is_stale(self):
        """The shape ``avrix`` measured: status=error, zero streams."""
        from dbas.importers.m3u_accounts import destination_account_looks_stale

        assert destination_account_looks_stale(
            {
                "name": "Provider XC",
                "status": "error",
                "stream_count": 0,
                "last_message": "No streams returned from Xtream Codes provider",
            }
        )

    def test_a_healthy_account_is_not_stale(self):
        from dbas.importers.m3u_accounts import destination_account_looks_stale

        assert not destination_account_looks_stale(
            {"name": "Provider XC", "status": "success", "stream_count": 316}
        )

    def test_an_errored_account_that_still_has_streams_is_not_stale(self):
        """status=error alone fires on any transient upstream hiccup."""
        from dbas.importers.m3u_accounts import destination_account_looks_stale

        assert not destination_account_looks_stale(
            {"name": "Provider XC", "status": "error", "stream_count": 316}
        )

    def test_an_idle_account_with_no_streams_is_not_stale(self):
        """Zero streams alone fires on an account that has not refreshed yet."""
        from dbas.importers.m3u_accounts import destination_account_looks_stale

        assert not destination_account_looks_stale(
            {"name": "Provider XC", "status": "idle", "stream_count": 0}
        )

    def test_the_stale_message_never_forwards_the_upstream_body(self):
        """``last_message`` can quote a request URL, credential and all."""
        from dbas.importers.m3u_accounts import stale_account_message

        message = stale_account_message(
            {
                "name": "Provider XC",
                "status": "error",
                "stream_count": 0,
                "last_message": "GET %s failed" % STD_URL,
            }
        )
        assert STD_URL not in message
        assert "Provider XC" in message

    def test_staleness_reads_no_credential_field_at_all(self):
        """INV-8's hard line: never by comparing credential values.

        A record carrying only status/stream_count — no username, no password,
        no url — is enough to decide. That is the property; it means the signal
        cannot have been built from a value comparison.
        """
        from dbas.importers.m3u_accounts import destination_account_looks_stale

        assert destination_account_looks_stale(
            {"name": "X", "status": "error", "stream_count": 0}
        )

    def test_the_staleness_predicate_is_not_reachable_from_the_writer_side(self):
        """It lives with the CYCLE, which is what lets the cycle use it.

        If it lived in the provisioning module the cycle would have to import
        that module to report staleness — which is precisely the INV-2 edge.
        """
        import dbas.importers.m3u_accounts as m3u

        assert hasattr(m3u, "destination_account_looks_stale")
        assert not hasattr(prov, "destination_account_looks_stale")

    def test_a_stale_report_records_an_action_item_not_a_push(self):
        from dbas.restore_contracts import RestoreReport

        report = RestoreReport(is_dry_run=False)
        report.record_provisioned_credential_stale("Account 'X' is in status 'error'")
        assert report.provisioned_credentials_stale == 1
        assert len(report.provisioned_credential_stale_details) == 1
        # Idempotent: the same account on two passes is one action item.
        report.record_provisioned_credential_stale("Account 'X' is in status 'error'")
        assert report.provisioned_credentials_stale == 1

    def test_staleness_is_not_a_delivery_shortfall_member(self):
        """It is an action item, so it must not move the run's outcome.

        The shortfall set is "the source had this and the replica does not".
        A credential that stopped working at the provider is neither.
        """
        from dbas.restore_contracts import RestoreReport

        report = RestoreReport(is_dry_run=False)
        report.record_provisioned_credential_stale("stale")
        assert report.delivery_shortfalls() == {}
        assert "provisioned_credentials_stale" not in report.DELIVERY_SHORTFALL_FIELDS


# ---------------------------------------------------------------------------
# INV-4, observed half — recorded by the cycle from what it already reads
# ---------------------------------------------------------------------------


class TestObservedCredentialRecording:
    def _report(self):
        from dbas.restore_contracts import RestoreReport

        return RestoreReport(is_dry_run=False)

    def _run_reporter(self, existing_acc, archive_account=None):
        from dbas.importers.m3u_accounts import _report_credentials_still_missing
        from dbas.restore_contracts import IdRemapTable
        from routers.backup import _redact_credentials_deep

        report = self._report()
        raw = archive_account or _xc_account()
        redacted = _redact_credentials_deep({"a": raw}, preserve_keys=frozenset())["a"]
        _report_credentials_still_missing(
            report=report,
            archive_account=redacted,
            remap=IdRemapTable(),
            existing_acc=existing_acc,
            label="Provider XC",
            source_id=1,
        )
        return report

    def test_a_destination_holding_the_credential_is_observed(self):
        report = self._run_reporter(
            {"id": 101, "name": "Provider XC", "username": "u", "password": "p"}
        )
        assert report.destination_credentials_observed is True

    def test_a_destination_holding_NOTHING_is_not_observed(self):
        report = self._run_reporter({"id": 101, "name": "Provider XC"})
        assert report.destination_credentials_observed is False

    def test_the_SENTINEL_does_not_count_as_an_observed_credential(self):
        """A value ECM itself wrote as a placeholder is ABSENT, not present.

        Truthiness would answer YES here, which is how a non-functional restored
        account once passed a credential-presence diff as byte-identical while
        the instance was dead (bead …-6pilh).
        """
        report = self._run_reporter(
            {
                "id": 101,
                "name": "Provider XC",
                "username": REDACTION_SENTINEL,
                "password": REDACTION_SENTINEL,
            }
        )
        assert report.destination_credentials_observed is False

    def test_a_PARTIALLY_credentialed_destination_is_observed(self):
        """Presence of ANY provisioned field means B holds a provider secret.

        Requiring all of them would let the gate be defeated by a destination
        that happens to have lost one field.
        """
        report = self._run_reporter(
            {"id": 101, "name": "Provider XC", "username": "u"}
        )
        assert report.destination_credentials_observed is True

    def test_observation_records_presence_only_never_a_value(self):
        report = self._run_reporter(
            {"id": 101, "name": "Provider XC", "username": "u", "password": XC_PASSWORD}
        )
        assert report.destination_credentials_observed is True
        assert XC_PASSWORD not in report.model_dump_json()

    def test_a_source_with_no_credential_can_never_produce_an_observation(self):
        """No redacted field => nothing to observe. The tuner case."""
        report = self._run_reporter(
            {"id": 103, "name": "Living Room Tuner", "server_url": "http://x/y.m3u"},
            archive_account=_tuner_account(),
        )
        assert report.destination_credentials_observed is False


# ---------------------------------------------------------------------------
# The MCP service principal's EFFECTIVE authority over these two routes
# ---------------------------------------------------------------------------


class TestMcpCapabilityVerdict:
    """Deny-by-default is the right answer here — pin it so it stays deliberate.

    ``auth.mcp_capabilities`` is deny-by-default and neither provisioning route
    is declared there, so the static service principal is refused both. That
    follows from an absence, and an absence is exactly the kind of thing a later
    edit changes without anyone deciding to: adding a tool contract would make
    an automation credential able to move an operator's provider password onto a
    remote instance.

    The S11 refusal is at the service layer regardless, so this is about
    capability, not about the gate.
    """

    def test_neither_provisioning_route_is_allowed_to_the_service_principal(self):
        from auth.mcp_capabilities import is_mcp_route_allowed

        for route in (
            "/api/sync-targets/{target_id}/provision-credentials",
            "/api/sync-targets/{target_id}/deprovision-credentials",
        ):
            assert not is_mcp_route_allowed("POST", route), (
                "%s became reachable by the static MCP service principal. That "
                "is a backend authority decision, not a side effect — see "
                "auth/mcp_capabilities.py's docstring." % route
            )

    def test_the_matrix_is_not_vacuously_denying_everything(self):
        """Smoke-test the instrument: it must say YES to something."""
        from auth.mcp_capabilities import MCP_ALLOWED_ROUTES, is_mcp_route_allowed

        assert MCP_ALLOWED_ROUTES, "the capability matrix allows nothing at all"
        method, route = next(iter(MCP_ALLOWED_ROUTES))
        assert is_mcp_route_allowed(method, route)
