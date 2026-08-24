"""The scheme-downgrade waiver is scoped to the stream-probe path, and nowhere else.

Bead ``enhancedchannelmanager-iyvl9``. A production incident: every probe against
the operator's XC provider failed, because the provider 302s
``https://<portal>/live/<user>/<pass>/<id>.ts`` onto a plain-HTTP edge node and
``security.ssrf.validate_redirect`` refused the downgrade. The provider serves
the video over HTTP at that edge anyway, so the refusal bought no
confidentiality and cost the operator their entire probe capability.

The acceptance criterion is an INVARIANT, not the reproduction above:

    The stream-probe path MAY follow an ``https -> http`` redirect.
    EVERY other outbound path MUST still refuse one.

Both halves are proven here on purpose. A test that only showed probes working
again would pass just as happily if the downgrade guard had been deleted
globally -- which is the dangerous failure, not the one the operator reported.
So the second half is enforced three ways, each catching a different mistake:

* **Behaviour** -- the default call still raises, at the validator AND through
  the real ``stream_request`` redirect loop.
* **Signature** -- every entrypoint's ``scheme_downgrade`` default is still
  ``REFUSE``, so flipping a default to re-open the guard for everyone turns this
  file red even if no call site changed.
* **Reachability** -- an AST scan proves ``ALLOW_STREAM_PROBE`` is named by
  exactly one production module, so a new caller cannot quietly acquire the
  waiver.

The waiver is also narrow in kind: it waives ONLY the scheme downgrade. The
denylist, resolve-then-connect-by-IP and depth cap still apply to the probe
path, which is asserted below rather than assumed.
"""
import ast
import inspect
import ipaddress
from pathlib import Path
from unittest.mock import patch

import httpx
import pytest

import stream_prober
from security import ssrf
from security.ssrf import (
    MAX_REDIRECTS,
    ResolvedTarget,
    SSRFError,
    SSRFMode,
    SchemeDowngrade,
    check_redirect_depth,
    validate_redirect,
)
from security.stream_outbound import (
    SSRFPinnedTransport,
    stream_request,
    validated_subprocess_input,
)

BACKEND = Path(__file__).resolve().parents[2]

# The measured incident, with the credentials replaced by synthetic values.
PORTAL_URL = "https://crx.watch/live/probe-user/probe-pass/13365.ts"
EDGE_URL = "http://50.7.4.250/f3386e51aa0e4f0f9c5f2f0c9f7a1b2c/serve"
EDGE_IP = "50.7.4.250"


def _patch_dns(*ips: str):
    return patch.object(
        ssrf, "_resolve", lambda host, port: [ipaddress.ip_address(i) for i in ips]
    )


def _target(url: str, ip: str):
    parsed = httpx.URL(url)
    return ResolvedTarget(
        scheme=parsed.scheme,
        hostname=parsed.host,
        port=parsed.port or (443 if parsed.scheme == "https" else 80),
        ip=ipaddress.ip_address(ip),
        url=url,
    )


# ---------------------------------------------------------------------------
# Half one: the probe path MAY follow the downgrade.
# ---------------------------------------------------------------------------

class TestProbePathMayDowngrade:
    def test_validator_follows_the_measured_incident_redirect(self):
        with _patch_dns(EDGE_IP):
            target = validate_redirect(
                PORTAL_URL,
                EDGE_URL,
                SSRFMode.LAN_FRIENDLY,
                scheme_downgrade=SchemeDowngrade.ALLOW_STREAM_PROBE,
            )

        assert target.scheme == "http"
        assert str(target.ip) == EDGE_IP

    @pytest.mark.asyncio
    async def test_stream_request_follows_the_downgrade_end_to_end(self):
        """The whole redirect loop, not just the validator, honours the waiver."""
        seen = []

        async def handler(request: httpx.Request):
            seen.append(str(request.url))
            if len(seen) == 1:
                return httpx.Response(
                    302, headers={"Location": EDGE_URL}, request=request
                )
            return httpx.Response(200, content=b"media", request=request)

        transport = SSRFPinnedTransport(
            inner_factory=lambda: httpx.MockTransport(handler),
            mode=SSRFMode.LAN_FRIENDLY,
            scheme_downgrade=SchemeDowngrade.ALLOW_STREAM_PROBE,
        )
        with patch(
            "security.stream_outbound.validate_outbound_url",
            return_value=_target(PORTAL_URL, "93.184.216.34"),
        ), _patch_dns(EDGE_IP):
            async with stream_request(PORTAL_URL, transport=transport) as response:
                assert await response.aread() == b"media"

        assert len(seen) == 2

    def test_waiver_does_not_relax_the_denylist(self):
        """Only the downgrade clause is waived -- IMDS is still refused."""
        with _patch_dns("169.254.169.254"):
            with pytest.raises(SSRFError):
                validate_redirect(
                    PORTAL_URL,
                    "http://169.254.169.254/latest/meta-data/",
                    SSRFMode.LAN_FRIENDLY,
                    scheme_downgrade=SchemeDowngrade.ALLOW_STREAM_PROBE,
                )

    def test_waiver_does_not_relax_public_only_mode(self):
        with _patch_dns("192.168.1.10"):
            with pytest.raises(SSRFError):
                validate_redirect(
                    PORTAL_URL,
                    "http://nas.lan/serve",
                    SSRFMode.PUBLIC_ONLY,
                    scheme_downgrade=SchemeDowngrade.ALLOW_STREAM_PROBE,
                )

    def test_waiver_does_not_relax_the_depth_cap(self):
        """The cap is policy-independent -- it takes no downgrade argument."""
        assert "scheme_downgrade" not in inspect.signature(
            check_redirect_depth
        ).parameters
        with pytest.raises(SSRFError):
            check_redirect_depth(MAX_REDIRECTS + 1)


# ---------------------------------------------------------------------------
# Half two, by behaviour: every other path still refuses.
# ---------------------------------------------------------------------------

class TestEveryOtherPathStillRefuses:
    def test_default_call_still_refuses_the_downgrade(self):
        with _patch_dns(EDGE_IP):
            with pytest.raises(SSRFError, match="downgrades"):
                validate_redirect(PORTAL_URL, EDGE_URL, SSRFMode.LAN_FRIENDLY)

    def test_explicit_refuse_still_refuses_the_downgrade(self):
        with _patch_dns(EDGE_IP):
            with pytest.raises(SSRFError, match="downgrades"):
                validate_redirect(
                    PORTAL_URL,
                    EDGE_URL,
                    SSRFMode.LAN_FRIENDLY,
                    scheme_downgrade=SchemeDowngrade.REFUSE,
                )

    @pytest.mark.asyncio
    async def test_stream_request_refuses_the_downgrade_by_default(self):
        """A non-probe consumer (e.g. the browser preview router) still fails closed."""

        async def handler(request: httpx.Request):
            return httpx.Response(302, headers={"Location": EDGE_URL}, request=request)

        transport = SSRFPinnedTransport(
            inner_factory=lambda: httpx.MockTransport(handler),
            mode=SSRFMode.LAN_FRIENDLY,
        )
        with patch(
            "security.stream_outbound.validate_outbound_url",
            return_value=_target(PORTAL_URL, "93.184.216.34"),
        ), _patch_dns(EDGE_IP):
            with pytest.raises(SSRFError, match="downgrades"):
                async with stream_request(PORTAL_URL, transport=transport):
                    pass

    @pytest.mark.asyncio
    async def test_stream_request_rejects_a_policy_it_cannot_apply(self):
        """A supplied transport owns its own policy -- fail loudly, never silently."""
        transport = SSRFPinnedTransport(
            inner=httpx.MockTransport(lambda request: httpx.Response(200)),
            mode=SSRFMode.LAN_FRIENDLY,
        )
        with pytest.raises(ValueError, match="scheme_downgrade"):
            async with stream_request(
                PORTAL_URL,
                transport=transport,
                scheme_downgrade=SchemeDowngrade.ALLOW_STREAM_PROBE,
            ):
                pass


# ---------------------------------------------------------------------------
# Half two, by signature: the safe default cannot be flipped globally.
# ---------------------------------------------------------------------------

class TestRefuseIsTheDefaultEverywhere:
    @pytest.mark.parametrize(
        "func",
        [
            validate_redirect,
            stream_request,
            validated_subprocess_input,
            SSRFPinnedTransport.__init__,
        ],
        ids=[
            "validate_redirect",
            "stream_request",
            "validated_subprocess_input",
            "SSRFPinnedTransport.__init__",
        ],
    )
    def test_scheme_downgrade_defaults_to_refuse(self, func):
        target = inspect.unwrap(func)
        parameter = inspect.signature(target).parameters["scheme_downgrade"]
        assert parameter.default is SchemeDowngrade.REFUSE, (
            f"{target.__qualname__} must default to REFUSE -- changing this "
            "default re-opens the https->http downgrade for EVERY caller, "
            "which is the failure this bead exists to prevent"
        )

    def test_validate_redirect_policy_is_keyword_only(self):
        """Keyword-only, so the waiver can never be passed by positional accident."""
        parameter = inspect.signature(validate_redirect).parameters["scheme_downgrade"]
        assert parameter.kind is inspect.Parameter.KEYWORD_ONLY

    def test_only_the_probe_member_waives_the_guard(self):
        """Any policy value other than ALLOW_STREAM_PROBE must still refuse."""
        for member in SchemeDowngrade:
            if member is SchemeDowngrade.ALLOW_STREAM_PROBE:
                continue
            with _patch_dns(EDGE_IP):
                with pytest.raises(SSRFError, match="downgrades"):
                    validate_redirect(
                        PORTAL_URL,
                        EDGE_URL,
                        SSRFMode.LAN_FRIENDLY,
                        scheme_downgrade=member,
                    )


# ---------------------------------------------------------------------------
# Half two, by reachability: only one production module may name the waiver.
# ---------------------------------------------------------------------------

# security/ssrf.py DEFINES the member; stream_prober.py is the one sanctioned
# consumer. Adding a module here is a deliberate act that a reviewer will see.
_SANCTIONED_WAIVER_MODULES = {
    "security/ssrf.py",
    "stream_prober.py",
}

_SKIP_DIRS = {"tests", "__pycache__", ".venv", "node_modules", "migrations"}


def _production_modules():
    for path in sorted(BACKEND.rglob("*.py")):
        relative = path.relative_to(BACKEND)
        if any(part in _SKIP_DIRS for part in relative.parts):
            continue
        yield relative, path


def _names_the_waiver(source: str) -> bool:
    """True if the module's AST references the ``ALLOW_STREAM_PROBE`` member.

    AST, not grep: a substring scan trips on prose in docstrings and comments,
    which several of these modules legitimately contain.
    """
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr == "ALLOW_STREAM_PROBE":
            return True
        if isinstance(node, ast.Name) and node.id == "ALLOW_STREAM_PROBE":
            return True
        if isinstance(node, ast.ImportFrom):
            if any(alias.name == "ALLOW_STREAM_PROBE" for alias in node.names):
                return True
    return False


def test_only_sanctioned_modules_name_the_downgrade_waiver():
    found = {
        str(relative)
        for relative, path in _production_modules()
        if _names_the_waiver(path.read_text(encoding="utf-8"))
    }

    assert found == _SANCTIONED_WAIVER_MODULES, (
        "SchemeDowngrade.ALLOW_STREAM_PROBE waives the https->http redirect "
        "guard and is scoped to the stream-probe path (bead "
        f"enhancedchannelmanager-iyvl9). Modules naming it: {sorted(found)}; "
        f"sanctioned: {sorted(_SANCTIONED_WAIVER_MODULES)}. If a new outbound "
        "path genuinely needs the waiver, that is a product decision, not a "
        "refactor -- justify it and add the module here explicitly."
    )


def test_prober_names_the_waiver_once_and_reuses_it():
    """One named constant, so the waiver is greppable and cannot drift per call site."""
    source = (BACKEND / "stream_prober.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    references = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute) and node.attr == "ALLOW_STREAM_PROBE"
    ]

    assert len(references) == 1
    assert stream_prober.PROBE_SCHEME_DOWNGRADE is SchemeDowngrade.ALLOW_STREAM_PROBE


def test_every_probe_outbound_call_carries_the_policy():
    """All three probe call sites opt in -- a missed one silently re-breaks probing."""
    source = (BACKEND / "stream_prober.py").read_text(encoding="utf-8")
    tree = ast.parse(source)

    outbound = {"validated_subprocess_input", "stream_request"}
    call_sites = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", None)
        if name in outbound:
            call_sites.append(node)

    assert len(call_sites) == 3, (
        "expected exactly 3 outbound calls on the probe path (ffprobe, bitrate "
        f"measurement, black-screen detection); found {len(call_sites)}"
    )
    for node in call_sites:
        keywords = {kw.arg for kw in node.keywords}
        assert "scheme_downgrade" in keywords, (
            f"stream_prober.py line {node.lineno}: probe outbound call omits "
            "scheme_downgrade, so this probe will still fail on providers that "
            "302 to plain HTTP"
        )
