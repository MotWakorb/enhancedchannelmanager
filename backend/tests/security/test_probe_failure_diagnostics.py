"""A probe failure must name its cause without ever naming a credential.

Bead ``enhancedchannelmanager-3dn59``. ``stream_prober`` logged only
``type(e).__name__``, so an operator watching an entire provider fail saw
``probe failed (SSRFError)`` several hundred times with no reason, no
distinction between causes, and a run report carrying a bare failure count.
Answering "why?" took source reading plus a live curl against the provider.

The suppression it replaced is CORRECT in its original scope and is preserved:
ffmpeg/ffprobe diagnostics can contain the provider URL with embedded
credentials, so subprocess text is still never copied into logs or persisted
state. What changed is the classification, and the classification is by
exception ORIGIN -- an explicit allowlist of types ECM raises itself -- never by
string-matching or scrubbing a message.

Origin-based classification only stays safe while ECM's own guard messages stay
credential-free, so that property is tested rather than assumed, in two
complementary ways:

* **Empirically** -- every SSRF-guard rejection reachable from the probe path is
  provoked with a credentialed provider URL, and the resulting message must
  contain neither the username nor the password. This proves today's messages.
* **Structurally** -- an AST scan proves no ``raise SSRFError`` in either guard
  module interpolates a URL-valued expression. This is the half that covers
  messages nobody has written yet: a future guard that starts naming the URL
  turns red here instead of slipping through on the strength of its class.

Red-first proof (run 2026-08-23, before the tests were declared done): making
``validate_redirect``'s downgrade message interpolate ``to_url`` failed
``test_no_guard_message_interpolates_a_url`` and
``test_guard_messages_never_contain_credentials[downgrade-refused]``. Reverting
the interpolation returned both to green.
"""
import ast
import ipaddress
import logging
import socket
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from security import ssrf
from security.ssrf import (
    MAX_REDIRECTS,
    SSRFError,
    SSRFMode,
    SchemeDowngrade,
    check_redirect_depth,
    validate_outbound_url,
    validate_redirect,
)
from security.stream_outbound import _LocalStreamRelay, validate_stream_subprocess_url
from stream_prober import (
    OPERATOR_SAFE_EXCEPTION_TYPES,
    PROBE_NETWORK_ROUTE_GUIDANCE,
    ProbeNetworkRouteError,
    StreamProber,
    operator_safe_detail,
)

BACKEND = Path(__file__).resolve().parents[2]

# Synthetic credentials, distinctive enough that a substring test cannot be
# satisfied by accident. XC provider URLs carry the account in the userinfo AND
# again in the path, so both placements are exercised.
USER = "xcuser7391"
PASSWORD = "xcpass8137"
CRED_URL = f"https://{USER}:{PASSWORD}@crx.watch/live/{USER}/{PASSWORD}/13365.ts"


def _patch_dns(*ips: str):
    return patch.object(
        ssrf, "_resolve", lambda host, port: [ipaddress.ip_address(i) for i in ips]
    )


def _raise_gaierror(host, port):
    raise socket.gaierror("Name or service not known")


# ---------------------------------------------------------------------------
# Classification is by ORIGIN, and by EXACT type.
# ---------------------------------------------------------------------------

class TestExceptionOriginClassification:
    def test_ssrf_guard_message_is_operator_safe(self):
        assert operator_safe_detail(SSRFError("Refusing redirect")) == "Refusing redirect"

    def test_probe_network_route_error_is_operator_safe(self):
        assert (
            operator_safe_detail(ProbeNetworkRouteError("Provider connection failed"))
            == "Provider connection failed"
        )

    @pytest.mark.parametrize(
        "exc",
        [
            RuntimeError(f"ffprobe: 401 Unauthorized for {CRED_URL}"),
            ValueError(CRED_URL),
            OSError(f"connection to {CRED_URL} failed"),
        ],
        ids=["ffprobe-runtime", "value", "os"],
    )
    def test_foreign_exceptions_are_never_operator_safe(self, exc):
        """Subprocess and client text stays suppressed -- this is msqf7's defect."""
        assert operator_safe_detail(exc) is None

    def test_a_subclass_does_not_inherit_the_allowance(self):
        """Exact-type membership: an allowlisted BASE class must not launder a
        subclass whose message came from somewhere else."""

        class VendorSSRFError(SSRFError):
            pass

        assert operator_safe_detail(VendorSSRFError(CRED_URL)) is None

    def test_plain_runtime_error_is_not_laundered_by_its_subclass(self):
        """``ProbeNetworkRouteError`` is a RuntimeError; RuntimeError is not it."""
        assert issubclass(ProbeNetworkRouteError, RuntimeError)
        assert operator_safe_detail(RuntimeError("ffprobe failed: secret")) is None

    def test_allowlist_holds_only_types_ecm_raises_itself(self):
        assert set(OPERATOR_SAFE_EXCEPTION_TYPES) == {SSRFError, ProbeNetworkRouteError}

    def test_empty_message_reports_no_detail(self):
        assert operator_safe_detail(SSRFError("   ")) is None


# ---------------------------------------------------------------------------
# What the operator actually sees in the log.
# ---------------------------------------------------------------------------

def _prober():
    prober = StreamProber(MagicMock())
    prober._save_probe_result = MagicMock(
        side_effect=lambda *args, **kwargs: {
            "probe_status": args[3],
            "error_message": args[4],
        }
    )
    return prober


class TestProbeFailureLogging:
    @pytest.mark.asyncio
    async def test_guard_rejection_logs_its_reason(self, caplog):
        prober = _prober()
        reason = "Refusing redirect that downgrades https → http"
        with patch.object(prober, "_run_ffprobe", side_effect=SSRFError(reason)):
            with caplog.at_level(logging.ERROR, logger="stream_prober"):
                result = await prober.probe_stream(228547, CRED_URL, "BBC One")

        assert reason in caplog.text
        assert "SSRFError" in caplog.text
        assert result["error_message"] == reason

    @pytest.mark.asyncio
    async def test_subprocess_text_is_still_suppressed(self, caplog):
        prober = _prober()
        with patch.object(
            prober,
            "_run_ffprobe",
            side_effect=RuntimeError(f"ffprobe failed on {CRED_URL}"),
        ):
            with caplog.at_level(logging.ERROR, logger="stream_prober"):
                result = await prober.probe_stream(228547, CRED_URL, "BBC One")

        assert "RuntimeError" in caplog.text
        assert USER not in caplog.text
        assert PASSWORD not in caplog.text
        assert result["error_message"] == "Probe failed"

    @pytest.mark.asyncio
    async def test_network_route_failure_keeps_its_operator_guidance(self, caplog):
        prober = _prober()
        with patch.object(
            prober,
            "_run_ffprobe",
            side_effect=ProbeNetworkRouteError("Provider connection failed"),
        ):
            with caplog.at_level(logging.ERROR, logger="stream_prober"):
                result = await prober.probe_stream(228547, CRED_URL, "BBC One")

        assert result["error_message"] == PROBE_NETWORK_ROUTE_GUIDANCE
        assert "Provider connection failed" in caplog.text

    @pytest.mark.asyncio
    async def test_no_probe_failure_log_leaks_the_provider_credentials(self, caplog):
        """The whole point of the original suppression, kept as a standing check."""
        prober = _prober()
        failures = [
            SSRFError("Refusing redirect that downgrades https → http"),
            SSRFError("Cross-origin request requires an isolated transport"),
            ProbeNetworkRouteError("Provider connection failed"),
            RuntimeError(f"ffprobe failed: [{CRED_URL}]"),
        ]
        with caplog.at_level(logging.ERROR, logger="stream_prober"):
            for failure in failures:
                with patch.object(prober, "_run_ffprobe", side_effect=failure):
                    await prober.probe_stream(228547, CRED_URL, "BBC One")

        assert USER not in caplog.text
        assert PASSWORD not in caplog.text


# ---------------------------------------------------------------------------
# Empirical half: every guard rejection reachable from the probe path, provoked
# against a credentialed URL.
# ---------------------------------------------------------------------------

def _guard_rejections():
    """(id, callable) pairs that each raise SSRFError from a credentialed URL.

    One entry per rejection reachable from the probe path -- ``security.ssrf``
    (validation, redirect re-validation, depth cap) and
    ``security.stream_outbound`` (direct subprocess transports, relay limits).
    """
    edge = f"http://{USER}:{PASSWORD}@50.7.4.250/opaque-token/serve"

    def relay_over_cap():
        relay = _LocalStreamRelay(CRED_URL, None, None)
        for index in range(2000):
            relay._register(f"{CRED_URL}?segment={index}")

    return [
        (
            "downgrade-refused",
            lambda: validate_redirect(CRED_URL, edge, SSRFMode.LAN_FRIENDLY),
        ),
        (
            "redirect-to-denied-ip",
            lambda: validate_redirect(
                CRED_URL,
                f"https://{USER}:{PASSWORD}@169.254.169.254/latest/meta-data/",
                SSRFMode.LAN_FRIENDLY,
                scheme_downgrade=SchemeDowngrade.ALLOW_STREAM_PROBE,
            ),
        ),
        (
            "denied-literal-ip",
            lambda: validate_outbound_url(
                f"http://{USER}:{PASSWORD}@169.254.169.254/latest/",
                SSRFMode.LAN_FRIENDLY,
            ),
        ),
        (
            "denied-mode-band",
            lambda: validate_outbound_url(
                f"http://{USER}:{PASSWORD}@192.168.1.10/live.ts",
                SSRFMode.PUBLIC_ONLY,
            ),
        ),
        (
            "disallowed-scheme",
            lambda: validate_outbound_url(
                f"file://{USER}:{PASSWORD}@crx.watch/etc/passwd", SSRFMode.LAN_FRIENDLY
            ),
        ),
        (
            "missing-hostname",
            lambda: validate_outbound_url(
                f"https://{USER}:{PASSWORD}@/live/13365.ts", SSRFMode.LAN_FRIENDLY
            ),
        ),
        (
            "invalid-port",
            lambda: validate_outbound_url(
                f"https://{USER}:{PASSWORD}@crx.watch:not-a-port/live.ts",
                SSRFMode.LAN_FRIENDLY,
            ),
        ),
        (
            "unparseable-url",
            lambda: validate_outbound_url(
                f"https://{USER}:{PASSWORD}@[::1/live.ts", SSRFMode.LAN_FRIENDLY
            ),
        ),
        ("depth-cap", lambda: check_redirect_depth(MAX_REDIRECTS + 1)),
        (
            "subprocess-disallowed-scheme",
            lambda: validate_stream_subprocess_url(
                f"srt://{USER}:{PASSWORD}@crx.watch:9000"
            ),
        ),
        (
            "subprocess-missing-hostname",
            lambda: validate_stream_subprocess_url(f"udp://{USER}:{PASSWORD}@:5004"),
        ),
        (
            "subprocess-invalid-port",
            lambda: validate_stream_subprocess_url(
                f"udp://{USER}:{PASSWORD}@crx.watch:not-a-port"
            ),
        ),
        ("relay-resource-cap", relay_over_cap),
    ]


def _dns_failure_rejections():
    """Rejections that need the resolver stubbed; kept separate for clarity."""
    return [
        (
            "resolution-failed",
            lambda: validate_outbound_url(CRED_URL, SSRFMode.LAN_FRIENDLY),
            patch.object(ssrf, "_resolve", _raise_gaierror),
        ),
        (
            "resolution-empty",
            lambda: validate_outbound_url(CRED_URL, SSRFMode.LAN_FRIENDLY),
            patch.object(ssrf, "_resolve", lambda host, port: []),
        ),
        (
            "resolves-to-denied-record",
            lambda: validate_outbound_url(CRED_URL, SSRFMode.LAN_FRIENDLY),
            _patch_dns("93.184.216.34", "169.254.169.254"),
        ),
    ]


@pytest.mark.parametrize(
    "case_id,provoke", _guard_rejections(), ids=[c[0] for c in _guard_rejections()]
)
def test_guard_messages_never_contain_credentials(case_id, provoke):
    with pytest.raises(SSRFError) as excinfo:
        provoke()

    message = str(excinfo.value)
    assert message, f"{case_id}: guard raised an empty message"
    assert USER not in message, f"{case_id}: guard message leaked the username"
    assert PASSWORD not in message, f"{case_id}: guard message leaked the password"


@pytest.mark.parametrize(
    "case_id,provoke,dns",
    _dns_failure_rejections(),
    ids=[c[0] for c in _dns_failure_rejections()],
)
def test_dns_guard_messages_never_contain_credentials(case_id, provoke, dns):
    with dns:
        with pytest.raises(SSRFError) as excinfo:
            provoke()

    message = str(excinfo.value)
    assert USER not in message, f"{case_id}: guard message leaked the username"
    assert PASSWORD not in message, f"{case_id}: guard message leaked the password"


def test_the_corpus_covers_every_guard_raise_site():
    """Keeps the empirical half honest as the guard modules grow.

    A raise site nobody provoked is a message nobody has checked. The count is
    deliberately blunt: adding a rejection makes this fail, which is the prompt
    to extend the corpus above.
    """
    sites = 0
    for module in ("security/ssrf.py", "security/stream_outbound.py"):
        tree = ast.parse((BACKEND / module).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Raise) and _raises_ssrf_error(node):
                sites += 1

    covered = len(_guard_rejections()) + len(_dns_failure_rejections())
    assert sites <= covered, (
        f"{sites} SSRFError raise sites across the guard modules but only "
        f"{covered} provoked in this file. Add the new rejection to "
        "_guard_rejections() so its message is checked for credentials."
    )


# ---------------------------------------------------------------------------
# Structural half: no guard message may interpolate a URL.
# ---------------------------------------------------------------------------

# Names that hold a whole URL. Interpolating any of these into a guard message
# would put the provider's embedded credentials into a string the prober is now
# allowed to log, which is exactly the regression origin-based classification
# must not permit.
_URL_VALUED_NAMES = {
    "url", "to_url", "from_url", "original_url", "next_url", "base_url",
    "current_url", "location", "raw_url", "target_url", "endpoint_url",
}

_GUARD_MODULES = ("security/ssrf.py", "security/stream_outbound.py")


def _raises_ssrf_error(node: ast.Raise) -> bool:
    exc = node.exc
    if isinstance(exc, ast.Call):
        func = exc.func
        name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", None)
        return name == "SSRFError"
    return False


def _interpolated_names(node: ast.AST):
    """Names and attribute names appearing inside f-string substitutions."""
    for child in ast.walk(node):
        if not isinstance(child, ast.FormattedValue):
            continue
        for inner in ast.walk(child.value):
            if isinstance(inner, ast.Name):
                yield inner.id
            elif isinstance(inner, ast.Attribute):
                yield inner.attr


@pytest.mark.parametrize("module", _GUARD_MODULES)
def test_no_guard_message_interpolates_a_url(module):
    source = (BACKEND / module).read_text(encoding="utf-8")
    tree = ast.parse(source)

    offenders = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Raise) or not _raises_ssrf_error(node):
            continue
        leaked = sorted(set(_interpolated_names(node.exc)) & _URL_VALUED_NAMES)
        if leaked:
            offenders.append(f"{module}:{node.lineno} interpolates {leaked}")

    assert not offenders, (
        "An SSRF guard message must not interpolate a URL: the stream prober "
        "logs these messages on the strength of their exception TYPE (bead "
        "enhancedchannelmanager-3dn59), and a provider URL carries embedded "
        "credentials. Name the host or the reason instead. Offenders: "
        + "; ".join(offenders)
    )


# ---------------------------------------------------------------------------
# The run report: a cause, not just a count.
# ---------------------------------------------------------------------------

class TestFailureBreakdown:
    def test_breakdown_groups_and_ranks_causes(self):
        prober = StreamProber(MagicMock())
        downgrade = "Refusing redirect that downgrades https → http"
        prober._probe_failed_streams = [
            {"id": 1, "name": "a", "error": downgrade},
            {"id": 2, "name": "b", "error": "Probe failed"},
            {"id": 3, "name": "c", "error": downgrade},
            {"id": 4, "name": "d", "error": downgrade},
        ]

        assert prober._failure_breakdown() == [
            {"reason": downgrade, "count": 3},
            {"reason": "Probe failed", "count": 1},
        ]

    def test_missing_or_blank_reason_is_labelled_not_dropped(self):
        prober = StreamProber(MagicMock())
        prober._probe_failed_streams = [
            {"id": 1, "name": "a"},
            {"id": 2, "name": "b", "error": "  "},
            {"id": 3, "name": "c", "error": None},
        ]

        assert prober._failure_breakdown() == [{"reason": "Unknown error", "count": 3}]

    def test_no_failures_yields_an_empty_breakdown(self):
        prober = StreamProber(MagicMock())
        prober._probe_failed_streams = []

        assert prober._failure_breakdown() == []

    @pytest.mark.asyncio
    async def test_scheduled_task_report_names_the_dominant_cause(self):
        """The scheduled run's own report, which is what an operator reads later.

        ``details["failed_streams"]`` is capped at 50 for storage, so on the run
        that caused this incident -- hundreds of streams, one cause -- the cause
        was invisible there. The breakdown is computed over ALL failures.
        """
        from tasks.stream_probe import StreamProbeTask

        downgrade = "Refusing redirect that downgrades https → http"
        prober = StreamProber(MagicMock())
        prober._probe_failed_streams = [
            {"id": index, "name": str(index), "error": downgrade}
            for index in range(300)
        ]

        async def fake_probe_all_streams(**_kwargs):
            prober._probe_progress_total = 300
            prober._probe_progress_failed_count = 300
            prober._probe_progress_success_count = 0
            prober._probe_progress_skipped_count = 0

        prober.probe_all_streams = fake_probe_all_streams

        task = StreamProbeTask()
        task._prober = prober
        task._auto_sync_groups = True

        result = await task.execute()

        assert downgrade in result.message
        assert result.details["failure_breakdown"] == [
            {"reason": downgrade, "count": 300}
        ]
        # The capped list alone could not have told the operator this.
        assert len(result.details["failed_streams"]) == 50

    def test_probe_results_expose_the_breakdown(self):
        """The operator's answer is in the report, not only in the log."""
        prober = StreamProber(MagicMock())
        downgrade = "Refusing redirect that downgrades https → http"
        prober._probe_failed_streams = [
            {"id": index, "name": str(index), "error": downgrade}
            for index in range(300)
        ]

        results = prober.get_probe_results()

        assert results["failed_count"] == 300
        assert results["failure_breakdown"] == [{"reason": downgrade, "count": 300}]
