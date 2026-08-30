"""SSRF chokepoint for operator-configurable outbound destinations (DBAS).

Bead ``enhancedchannelmanager-0i2vt.5``. The authoritative spec is
``docs/security/threat_model_dbas_import.md`` §9 (Addendum B), in particular
§9.4 (the validator requirements) and §9.4 item 8 (the regression corpus that
``tests/test_ssrf_guard.py`` implements).

Why this module exists
----------------------
v0.18.0 lets an authenticated admin point ECM at arbitrary outbound URLs
(S3-compatible / WebDAV *endpoint URLs*, OneDrive/Dropbox/GDrive APIs, a second
Dispatcharr instance). ECM runs inside the operator's network, so an
attacker-influenceable URL is a classic SSRF primitive: hit the cloud metadata
endpoint (``169.254.169.254``) for instance credentials, scan internal
infrastructure, or use ECM as a proxy. This module is the single validation
chokepoint every outbound DBAS request must pass through before connect.

Design (per §9.4)
-----------------
* **Standalone.** No import of the settings router (avoids the ``wlvxh`` cyclic
  import); it reads the *mode* lazily from ``config.get_settings()`` and is
  otherwise pure. Adapters (which import ``cloud_storage.types``) and the
  settings router can both import this.
* **Scheme allowlist** — only ``http`` / ``https``.
* **Always-on denylist** (BOTH modes, no opt-out, no settings key, no
  allowlist): ``0.0.0.0/8``, ``169.254.0.0/16`` (incl. IMDS ``169.254.169.254``),
  ``::``, ``fc00::/7`` (ULA),
  ``fe80::/10`` (link-local), ``fec0::/10`` (site-local), IPv4-mapped IPv6
  ``::ffff:0:0/96`` (unwrapped + re-checked against the v4 rules), multicast
  (``224.0.0.0/4`` / ``ff00::/8``). Backed by the stdlib ``ipaddress``
  ``is_private`` / ``is_reserved`` / ``is_loopback`` / ``is_link_local`` /
  ``is_multicast`` properties as a **fail-closed backstop** so a future special
  range we did not enumerate is still denied.
* **Wizard-toggled band** — RFC1918 (``10/8``, ``172.16/12``, ``192.168/16``),
  RFC 6598 Shared Address Space (``100.64/10``), and loopback
  (``127.0.0.0/8`` and ``::1``) are ALLOWED in LAN-friendly
  (default) and REJECTED in public-only. IPv6 unique-local ``fc00::/7`` is
  *always* denied — there is no LAN IPv6 ULA carve-out in §9.4.

  ``::1`` sits in the toggled band, not the always-on list (GH #754, bead
  ``0yh70``). §9.4 item 2 is self-contradictory on this point — its always-on
  bullet lists ``::1/128`` while its wizard-toggled bullet reads
  "``127.0.0.0/8`` and RFC1918 ... **+ IPv6 equivalents**" — and we resolve it
  toward the toggled bullet, for loopback ONLY, because (a) ``::1`` and
  ``127.0.0.1`` are the same trust domain (this host's own loopback
  interface), so denying one while permitting the other blocks no attacker
  capability; and (b) Docker's generated ``/etc/hosts`` maps ``localhost``
  to BOTH ``::1`` and ``127.0.0.1``, so with reject-if-any-record-denied
  (below) an always-on ``::1`` makes ``http://localhost:<port>`` unusable in
  LAN-friendly mode on every container that is not ``network_mode: host``.
  ULA / link-local / site-local are NOT part of that carve-out: those address
  a *different* host on the network, not this one.
* **Resolve-then-connect-by-IP** — resolve the hostname ONCE, validate EVERY A
  and AAAA record (any denied → reject the whole request), and return the
  validated IP so the caller connects by IP with the hostname preserved for
  TLS SNI / ``Host:``. There is no second DNS lookup between validate and
  connect.
* **Fail-closed** — DNS failure, empty resolution, or any ambiguity REJECTS
  (unlike the media-server validator in ``routers/settings.py`` which fails
  open; migrating those callers onto this module is a deliberate follow-up, NOT
  done here).
* **Redirect re-validation** — :func:`validate_redirect` re-runs the full check
  on a redirect target, rejects ``https → http`` downgrades, and
  :func:`check_redirect_depth` caps the chain at :data:`MAX_REDIRECTS`.

Scheme-downgrade scope (bead ``enhancedchannelmanager-iyvl9``)
-------------------------------------------------------------
The ``https → http`` refusal is the DEFAULT for every outbound path and stays
that way. Exactly one caller may opt out: the stream-probe path, via the
explicit :class:`SchemeDowngrade` parameter on :func:`validate_redirect`
(``SchemeDowngrade.ALLOW_STREAM_PROBE``). It is a per-call argument on purpose —
not a global flag, not a settings key, not an environment variable — so that
reading :func:`validate_redirect` tells you who is allowed to downgrade, and so
that ``grep -rn ALLOW_STREAM_PROBE`` enumerates every site that does.

Why the probe path and nothing else: XC providers routinely 302 an
``https://<portal>/live/<user>/<pass>/<id>.ts`` request onto a plain-HTTP edge
node, and they serve the video over HTTP at that edge regardless. Playback is
already unencrypted in transit, and the redirect target's path is an opaque
token carrying no credentials, so refusing the hop buys no confidentiality — it
only costs the operator the ability to learn whether a stream works. Every other
outbound destination (cloud backup targets, EPG sources, a second Dispatcharr
instance) is a credentialed API where a downgrade IS a real loss, so those keep
the refusal.

The relaxation is narrow in a second sense: it waives ONLY the scheme-downgrade
clause. The denylist, resolve-then-connect-by-IP, redirect depth capping and
origin pinning all still apply to the probe path unchanged.

Seam for bead .8
----------------
:func:`validate_outbound_url` returns a :class:`ResolvedTarget` (validated IP +
preserved hostname + scheme + port). Bead .8 wires the adapters onto this:
* httpx adapters: build a client whose transport connects to
  ``target.ip`` while sending ``Host: target.hostname`` and using the hostname
  for SNI (see :func:`connect_kwargs` for the shape .8 will consume).
* SDKs that do their own DNS (boto3 S3, Google) — per §9.2 row B4 option (b),
  .8 pre-resolves via this validator and hands the SDK an IP + ``Host:``
  override (documented residual rebinding window in §9.5).

TLS posture (§9.4 item 6): callers default ``verify=True``. The per-target
``insecure=true`` flag + per-request audit row lives on the ``CloudTarget``
model (bead .4), which is not built yet — see :data:`INSECURE_FLAG_TODO`.
"""
from __future__ import annotations

import ipaddress
import logging
import socket
from dataclasses import dataclass
from enum import Enum
from typing import Optional, Union
from urllib.parse import urlsplit

logger = logging.getLogger(__name__)

_IPAddress = Union[ipaddress.IPv4Address, ipaddress.IPv6Address]

# --- TLS / insecure-flag seam (bead .4) -----------------------------------
# §9.4 item 6: verify=True is the default. The per-target ``insecure=true``
# escape hatch (and its mandatory per-request ``journal.log_entry`` with
# category='backup_outbound', tls_verified=false) belongs on the CloudTarget
# model, which bead .4 builds. Callers should default verify=True; bead .8 wires
# the per-target override + audit row. Intentionally NOT implemented here.
INSECURE_FLAG_TODO = (
    "TODO(.4/.8): per-target insecure=true + per-request audit row "
    "(journal category='backup_outbound', tls_verified=false)"
)

# Redirect chain cap (§9.4 item 4).
MAX_REDIRECTS = 5

ALLOWED_SCHEMES = ("http", "https")


class SSRFMode(str, Enum):
    """Outbound-destination policy mode (the single wizard knob, §9.4 item 7)."""

    #: RFC1918 private + 127/8 loopback ALLOWED (default — ADR-012 D4).
    LAN_FRIENDLY = "lan_friendly"
    #: RFC1918 private + loopback BLOCKED (public destinations only).
    PUBLIC_ONLY = "public_only"


class SchemeDowngrade(str, Enum):
    """Whether a caller may follow an ``https → http`` redirect.

    Bead ``enhancedchannelmanager-iyvl9``. This is a per-call argument to
    :func:`validate_redirect`, deliberately NOT a global flag or a setting: the
    only way to obtain the relaxation is to name it at the call site, which
    makes every site that has it greppable
    (``grep -rn ALLOW_STREAM_PROBE backend/``).

    The default is :data:`REFUSE` and every existing caller gets it implicitly,
    so a new outbound path cannot acquire the relaxation by accident — only by
    writing the member name.
    """

    #: Refuse the downgrade. The default, and the policy for EVERY outbound
    #: path other than the stream probe.
    REFUSE = "refuse"
    #: Follow the downgrade. Reserved for the stream-probe path (ffprobe /
    #: bitrate measurement / black-screen detection), where the provider serves
    #: the media over plain HTTP anyway and the redirect target carries no
    #: credentials. See the module docstring for the full rationale.
    ALLOW_STREAM_PROBE = "allow_stream_probe"


class SSRFError(Exception):
    """Raised when an outbound URL is rejected by the SSRF chokepoint.

    The cloud-upload path treats this as a hard, fail-closed rejection. The
    message is safe to log; do NOT echo it verbatim to an unauthenticated
    surface (these are admin-only flows, so admin-facing surfacing is fine).
    """


@dataclass(frozen=True)
class ResolvedTarget:
    """A validated outbound target — the seam bead .8's adapters consume.

    Attributes:
        scheme: ``http`` or ``https`` (validated).
        hostname: original hostname (preserved for TLS SNI / ``Host:`` header).
        port: effective port (explicit, or scheme default).
        ip: the validated IP to CONNECT to (no second DNS lookup).
        url: the original request URL (unmodified path/query for the caller).
    """

    scheme: str
    hostname: str
    port: int
    ip: _IPAddress
    url: str

    @property
    def host_header(self) -> str:
        """Value for the ``Host:`` header (host[:port] when non-default)."""
        default = 443 if self.scheme == "https" else 80
        hostname = f"[{self.hostname}]" if ":" in self.hostname else self.hostname
        if self.port == default:
            return hostname
        return f"{hostname}:{self.port}"


# ---------------------------------------------------------------------------
# Always-on denylist CIDRs (rejected in BOTH modes — §9.4 item 2).
# ---------------------------------------------------------------------------
_ALWAYS_ON_V4 = tuple(ipaddress.ip_network(c) for c in (
    "0.0.0.0/8",            # "this" network / wildcard
    "169.254.0.0/16",      # link-local (incl. IMDS 169.254.169.254)
    "224.0.0.0/4",         # multicast
))
_ALWAYS_ON_V6 = tuple(ipaddress.ip_network(c) for c in (
    "::/128",              # unspecified
    "fc00::/7",            # unique-local (ULA) — a DIFFERENT host, not this one
    "fe80::/10",           # link-local
    "fec0::/10",           # site-local (deprecated, still denied)
    "ff00::/8",            # multicast
))

# ---------------------------------------------------------------------------
# Wizard-toggled band — ALLOWED in LAN-friendly, DENIED in public-only.
# ---------------------------------------------------------------------------
_TOGGLED_V4 = tuple(ipaddress.ip_network(c) for c in (
    "10.0.0.0/8",
    "172.16.0.0/12",
    "192.168.0.0/16",
    "100.64.0.0/10",       # RFC 6598 Shared Address Space / intended peers
    "127.0.0.0/8",         # loopback toggles with the band per §9.4 item 2
))

_TOGGLED_V6 = tuple(ipaddress.ip_network(c) for c in (
    # The v6 equivalent of 127.0.0.0/8 — same trust domain, so it follows the
    # same toggle (GH #754 / bead 0yh70; rationale in the module docstring).
    # NOTE: this is the ONLY v6 carve-out. fc00::/7 stays always-on denied.
    "::1/128",
))


def _in_any(ip: _IPAddress, nets) -> bool:
    return any(ip in net for net in nets)


def _is_always_on_denied(ip: _IPAddress) -> bool:
    """Always-on denial — explicit CIDRs + fail-closed ipaddress backstop.

    The explicit CIDR list is the contract; the ``ipaddress`` property backstop
    (§9.5) closes the gap for special-purpose ranges we did not enumerate. Note
    we deliberately do NOT use ``is_private`` here because RFC1918 is the
    wizard-toggled band, handled separately — ``is_private`` would also match
    RFC1918 and break LAN-friendly mode. Likewise ``is_loopback`` is excluded
    from the backstop in BOTH address families: ``127.0.0.0/8`` and ``::1``
    are the toggled band (GH #754 / bead ``0yh70``), not always-on denials.
    """
    if ip.version == 4:
        if _in_any(ip, _ALWAYS_ON_V4):
            return True
        # Backstop: reserved / multicast / unspecified (NOT is_private — that's
        # the toggled band; NOT is_loopback — 127/8 is the toggled band).
        if ip.is_reserved or ip.is_multicast or ip.is_unspecified:
            return True
        if ip.is_link_local:  # 169.254/16 — already covered, belt-and-braces
            return True
        return False

    # IPv6
    if _in_any(ip, _ALWAYS_ON_V6):
        return True
    if _in_any(ip, _TOGGLED_V6):
        # ``::1`` — the mode decides in _ip_allowed, not here. This early
        # return is load-bearing: ``::1`` IS IETF-reserved, so the is_reserved
        # backstop below would otherwise deny it unconditionally and silently
        # re-create GH #754 (the v4 branch needs no equivalent — 127.0.0.0/8
        # is not is_reserved).
        return False
    # Backstop — NOT is_loopback: ``::1`` is the toggled band (see docstring).
    if ip.is_link_local or ip.is_multicast or ip.is_unspecified:
        return True
    if ip.is_reserved:
        return True
    # site-local (fec0::/10) is deprecated; ipaddress has is_site_local on some
    # versions — covered by the explicit CIDR above regardless.
    return False


def _is_toggled_band(ip: _IPAddress) -> bool:
    """RFC1918, RFC 6598, and loopback — the LAN-friendly policy band."""
    if ip.version == 4:
        return _in_any(ip, _TOGGLED_V4)
    # The only v6 member is ``::1`` (GH #754 / bead 0yh70). There is still no
    # LAN IPv6 *network* carve-out — ULA / link-local stay always-on denied.
    # (IPv4-mapped IPv6 is unwrapped before we ever reach here.)
    return _in_any(ip, _TOGGLED_V6)


def _unwrap_mapped(ip: _IPAddress) -> _IPAddress:
    """Unwrap an IPv4-mapped IPv6 address (``::ffff:a.b.c.d``) to the v4 form.

    §9.4 item 2: ``::ffff:0:0/96`` must be unwrapped and re-checked against the
    IPv4 rules so ``::ffff:169.254.169.254`` is caught. Also unwraps the rarer
    IPv4-compatible (deprecated) ``::a.b.c.d`` and 6to4 ``2002::/16`` mappings.
    """
    if ip.version != 6:
        return ip
    mapped = getattr(ip, "ipv4_mapped", None)
    if mapped is not None:
        return mapped
    # IPv4-compatible (deprecated) ::a.b.c.d — ipaddress exposes ipv4_mapped only
    # for the ::ffff:0:0/96 block, so handle the compat block defensively.
    sixto4 = getattr(ip, "sixtofour", None)
    if sixto4 is not None:
        return sixto4
    return ip


def _ip_allowed(ip: _IPAddress, mode: SSRFMode) -> bool:
    """Return True if ``ip`` is permitted under ``mode``; False = reject.

    Order matters: unwrap mapped → always-on denylist (both modes) → toggled
    band (mode-dependent) → public is allowed.
    """
    ip = _unwrap_mapped(ip)

    if _is_always_on_denied(ip):
        return False

    if _is_toggled_band(ip):
        # RFC1918 / RFC 6598 / loopback: the explicit LAN-friendly allowlist —
        # allowed only in LAN-friendly. A mapped ::ffff:<RFC1918> reaches after
        # unwrap and follows the same toggle (§9.4 item 2: "re-checked against
        # the IPv4 rules").
        return mode is SSRFMode.LAN_FRIENDLY

    # Fail-closed backstop (§9.5): anything the stdlib still considers
    # non-global / non-public but that is NOT in our explicit LAN allowlist
    # (TEST-NET documentation ranges, 240/4 reserved-future, benchmarking,
    # and any future special-purpose prefix we did not enumerate) is DENIED in
    # BOTH modes. The LAN toggle is a tight allowlist of real LAN ranges, not
    # "everything private" — so we never silently allow a documentation/reserved
    # range just because it parses as private.
    if not ip.is_global:
        return False

    return True


def _resolve(host: str, port: Optional[int]) -> list[_IPAddress]:
    """Resolve ``host`` to a list of IP addresses (ONE lookup).

    Isolated so tests can patch it deterministically (no real network). Raises
    on resolution failure — the caller turns that into a fail-closed
    :class:`SSRFError`. Returns an empty list only if the resolver yields no
    usable address (also fail-closed by the caller).
    """
    infos = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
    out: list[_IPAddress] = []
    seen: set[str] = set()
    for info in infos:
        sockaddr = info[4]
        addr = sockaddr[0]
        if addr in seen:
            continue
        seen.add(addr)
        try:
            out.append(ipaddress.ip_address(addr))
        except ValueError:
            continue
    return out


def _validate_parsed(scheme: str, hostname: str, port: int, url: str,
                     mode: SSRFMode) -> ResolvedTarget:
    """Validate a parsed (scheme, host, port) and return a pinned target.

    Resolve-then-connect-by-IP (§9.4 item 3): resolve once, validate EVERY
    record, reject the whole request if ANY is denied, return the first
    validated IP.
    """
    if scheme not in ALLOWED_SCHEMES:
        raise SSRFError(f"Disallowed URL scheme '{scheme}' (only http/https permitted)")
    if not hostname:
        raise SSRFError("Outbound URL has no hostname")

    # Literal IP host — no DNS, validate directly.
    try:
        literal = ipaddress.ip_address(hostname)
    except ValueError:
        literal = None

    if literal is not None:
        if not _ip_allowed(literal, mode):
            raise SSRFError(f"Destination IP {hostname} is denied by SSRF policy ({mode.value})")
        return ResolvedTarget(scheme=scheme, hostname=hostname, port=port,
                              ip=_unwrap_mapped(literal), url=url)

    # Hostname — resolve ONCE, fail closed on any error / empty result.
    try:
        records = _resolve(hostname, port)
    except (socket.gaierror, socket.herror, UnicodeError, OSError) as exc:
        # FAIL CLOSED (unlike the media-server validator). An unresolvable or
        # ambiguous host is rejected for the cloud-upload path.
        raise SSRFError(f"Could not resolve host '{hostname}' — rejected (fail-closed)") from exc

    if not records:
        raise SSRFError(f"Host '{hostname}' resolved to no usable address — rejected (fail-closed)")

    # Validate EVERY record; ANY denied → reject the whole request (do not
    # cherry-pick the allowed one — that is the DNS-rebinding bypass).
    for rec in records:
        if not _ip_allowed(rec, mode):
            raise SSRFError(
                f"Host '{hostname}' resolves to denied address {rec} — "
                f"rejected (DNS-rebinding mitigation, {mode.value})"
            )

    validated_ip = _unwrap_mapped(records[0])
    return ResolvedTarget(scheme=scheme, hostname=hostname, port=port,
                          ip=validated_ip, url=url)


def _split(url: str) -> tuple[str, str, int]:
    """Parse a URL into (scheme, hostname, port), filling the default port."""
    try:
        parts = urlsplit(url)
    except ValueError as exc:
        raise SSRFError(f"Could not parse outbound URL: {exc}") from exc

    scheme = (parts.scheme or "").lower()
    if scheme not in ALLOWED_SCHEMES:
        # Reject early with a clear message before touching host/port.
        raise SSRFError(f"Disallowed URL scheme '{scheme or '(none)'}' (only http/https permitted)")

    hostname = parts.hostname or ""
    try:
        port = parts.port if parts.port is not None else (443 if scheme == "https" else 80)
    except ValueError as exc:
        raise SSRFError(f"Invalid port in outbound URL: {exc}") from exc
    return scheme, hostname, port


def validate_outbound_url(url: str, mode: SSRFMode) -> ResolvedTarget:
    """Validate an operator-supplied outbound URL; return a pinned target.

    This is THE chokepoint entrypoint bead .8's adapters route through.

    Args:
        url: the outbound URL (endpoint URL, API base, etc.).
        mode: the active :class:`SSRFMode` (use :func:`get_ssrf_mode` to read
            the persisted global setting).

    Returns:
        A :class:`ResolvedTarget` carrying the validated connect IP plus the
        preserved hostname/scheme/port for SNI + ``Host:``.

    Raises:
        SSRFError: scheme not http(s); hostname missing; any resolved record
            denied by the always-on denylist or the active mode; resolution
            failed or empty (fail-closed).
    """
    scheme, hostname, port = _split(url)
    target = _validate_parsed(scheme, hostname, port, url, mode)
    logger.debug(
        "[SSRF] Validated outbound: scheme=%s host=%s port=%s ip=%s mode=%s",
        target.scheme, target.hostname, target.port, target.ip, mode.value,
    )
    return target


def validate_redirect(
    from_url: str,
    to_url: str,
    mode: SSRFMode,
    *,
    scheme_downgrade: SchemeDowngrade = SchemeDowngrade.REFUSE,
) -> ResolvedTarget:
    """Re-validate a redirect target before following it (§9.4 item 4).

    * Re-runs the full denylist + resolve-by-IP on ``to_url``.
    * Rejects an ``https → http`` scheme downgrade UNLESS the caller explicitly
      passes ``scheme_downgrade=SchemeDowngrade.ALLOW_STREAM_PROBE``.

    The caller is responsible for capping the chain length via
    :func:`check_redirect_depth`.

    Who may downgrade
    -----------------
    Only the stream-probe path
    (:mod:`stream_prober` → :mod:`security.stream_outbound`). ``scheme_downgrade``
    is keyword-only and defaults to :data:`SchemeDowngrade.REFUSE`, so every
    other caller — cloud backup targets, EPG source fetches, the
    cross-instance sync client, the browser stream preview — keeps the refusal
    without doing anything, and a new caller cannot inherit the relaxation by
    accident. Waiving the downgrade waives ONLY the downgrade: the denylist,
    resolve-then-connect-by-IP, depth cap and origin pinning are unaffected.
    See the module docstring for why the probe path is the exception.

    Args:
        from_url: the URL that issued the 3xx (its scheme governs downgrade).
        to_url: the ``Location:`` target.
        mode: active SSRF mode.
        scheme_downgrade: downgrade policy for THIS hop. Defaults to
            :data:`SchemeDowngrade.REFUSE`; only the stream-probe path passes
            :data:`SchemeDowngrade.ALLOW_STREAM_PROBE`.

    Returns:
        A validated :class:`ResolvedTarget` for ``to_url``.

    Raises:
        SSRFError: downgrade (unless explicitly allowed for this hop), or any
            reason :func:`validate_outbound_url` would.
    """
    from_scheme, _, _ = _split(from_url)
    to_scheme = (urlsplit(to_url).scheme or "").lower()
    if from_scheme == "https" and to_scheme == "http":
        if scheme_downgrade is not SchemeDowngrade.ALLOW_STREAM_PROBE:
            raise SSRFError("Refusing redirect that downgrades https → http")
        # Deliberate, scoped waiver — record it so the downgrade is never
        # silent. No URL or credential in the message (bead
        # ``enhancedchannelmanager-3dn59``): the probe path surfaces guard
        # messages to the operator, so they must stay credential-free.
        logger.warning(
            "[SSRF] Following an https → http redirect on the stream-probe "
            "path (policy=%s); this hop is not confidential",
            scheme_downgrade.value,
        )
    return validate_outbound_url(to_url, mode)


def check_redirect_depth(depth: int) -> None:
    """Raise :class:`SSRFError` if the redirect chain exceeds :data:`MAX_REDIRECTS`.

    Args:
        depth: number of redirects followed so far (1-based).
    """
    if depth > MAX_REDIRECTS:
        raise SSRFError(f"Redirect chain exceeded cap ({MAX_REDIRECTS})")


def connect_kwargs(target: ResolvedTarget, *, verify: bool = True) -> dict:
    """Return the connect parameters bead .8's httpx transport will consume.

    Centralises the resolve-then-connect-by-IP wiring so every adapter pins the
    SAME way:

    * connect to ``target.ip`` (no second DNS lookup),
    * present ``target.hostname`` as TLS SNI (``server_hostname``),
    * send ``Host: target.host_header``.

    ``verify`` defaults to True (§9.4 item 6). The per-target ``insecure=true``
    override + audit row is bead .4/.8 — see :data:`INSECURE_FLAG_TODO`.
    """
    return {
        "connect_ip": str(target.ip),
        "port": target.port,
        "server_hostname": target.hostname,   # TLS SNI
        "host_header": target.host_header,
        "scheme": target.scheme,
        "verify": verify,
    }


def get_ssrf_mode() -> SSRFMode:
    """Read the persisted global outbound mode from the settings store.

    Single global setting (``ssrf_outbound_mode`` on ``DispatcharrSettings``),
    NOT the per-target insecure flag. Defaults to LAN-friendly (ADR-012 D4) on
    a missing or unrecognised value — fail SAFE for availability, never opening
    the always-on denylist (which is unconditional regardless of mode).
    """
    # Imported lazily to keep this module free of an import cycle with config.
    from config import get_settings

    raw = getattr(get_settings(), "ssrf_outbound_mode", SSRFMode.LAN_FRIENDLY.value)
    try:
        return SSRFMode(raw)
    except ValueError:
        logger.warning(
            "[SSRF] Unknown ssrf_outbound_mode=%r; defaulting to lan_friendly", raw
        )
        return SSRFMode.LAN_FRIENDLY
