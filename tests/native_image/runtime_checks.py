"""Real native operations in a network-disabled image, using generated data only.

The host mounts this directory read-only and invokes the image's absolute venv
Python. Loopback fixture servers are the only network endpoints. No pytest or
host/application dependencies are installed. Version evidence is NOT CVE proof.
"""

import asyncio
import base64
import binascii
import functools
import hashlib
import http.server
import importlib
import importlib.util
import json
import os
import platform
import re
import sqlite3
import ssl
import subprocess
import sys
import tempfile
import threading
import time
from contextlib import asynccontextmanager, contextmanager, suppress
from datetime import UTC, datetime, timedelta
from ipaddress import ip_address
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

PIXEL_MAX = 8_847_360
FRAME_MAX_BYTES = PIXEL_MAX * 3 // 2 + 8192
_RELAY_URL_RE = re.compile(
    r"http://127\.0\.0\.1:[0-9]+/resource/[0-9a-f]{32}(?P<suffix>\.[a-z0-9]+)?"
)
_HLS_KEY_URI_RE = re.compile(r'#EXT-X-KEY:[^\n]*URI="([^"]+)"')
_FRAME_MD5_RE = re.compile(r"[0-9a-f]{32}")
# Exact owned bytes and baseline metadata, not arbitrary media supplied by JSON.
_RARE_INPUTS = {
    "synthetic.c2": (
        63,
        "1e6481a7721df66f01c4f6655161e7beb7e76107cb7fdcf3795df743445c4604",
        {
            "format_name": "codec2",
            "codec_type": "audio",
            "codec_name": "codec2",
            "sample_rate": "8000",
            "channels": 1,
        },
    ),
    "synthetic.jxl": (
        64,
        "6fe9765c1f0614056c13dd236536e5958099b7937b9eed4d2309962fc3c502bd",
        {
            "format_name": "jpegxl_pipe",
            "codec_type": "video",
            "codec_name": "jpegxl",
            "width": 16,
            "height": 16,
        },
    ),
    "synthetic.svg": (
        115,
        "9d6be6e2353498d2723c64a50795603c6dd7d4ff96c637b2987053281207b938",
        {
            "format_name": "svg_pipe",
            "codec_type": "video",
            "codec_name": "svg",
            "width": 16,
            "height": 16,
        },
    ),
    "synthetic.vgm": (
        131,
        "3ca313b3924a1ea89c9aa0f859884b9a0934b57c23b217467955d6055ab7c294",
        {
            "format_name": "libgme",
            "codec_type": "audio",
            "codec_name": "pcm_s16le",
            "sample_rate": "44100",
            "channels": 2,
        },
    ),
    "synthetic.mod": (
        2108,
        "b62b7bb8fcba672d2689a9fb2c6b22c8be43e5c4b10d845efe89a69f6c1f904c",
        {
            "format_name": "libopenmpt",
            "codec_type": "audio",
            "codec_name": "pcm_f32le",
            "sample_rate": "48000",
            "channels": 2,
        },
    ),
}
_RARE_PROVENANCE = {
    "image": "ghcr.io/motwakorb/enhancedchannelmanager@sha256:8ac2b20f9f85416d088d3adf8479a7c3b6c55236b282dad0a91a63b2bb4319f9",
    "commit": "bdef595bafc40128e7df49b8dcb55dde98e9aee2",
    "generator": "compatibility-fixtures/generate_and_check.py",
    "generator_sha256": "269b1801fff716faf591ecc10f6794a9b1a5af158a33230284320114b4818ff0",
    "owned_synthetic": True,
}
_DECODER_RE = re.compile(r"^\s*[VAS][A-Z.]{5}\s+([a-z0-9_-]+)\s+", re.MULTILINE)
_DEMUXER_RE = re.compile(r"^\s*D\s+([a-z0-9_,]+)\s+", re.MULTILINE)
_PROTOCOL_RE = re.compile(r"[a-z0-9_]+")
_COMMON_DECODERS = frozenset(
    (
        "h264",
        "hevc",
        "vp8",
        "vp9",
        "mpeg2video",
        "mpeg4",
        "mjpeg",
        "png",
        "rawvideo",
        "aac",
        "aac_latm",
        "ac3",
        "eac3",
        "mp2",
        "mp3",
        "opus",
        "vorbis",
        "flac",
        "pcm_s16le",
    )
)
_INPUT_DEMUXERS = frozenset(
    (
        "mpegts",
        "hls",
        "dash",
        "mov",
        "matroska",
        "webm",
        "aac",
        "ac3",
        "eac3",
        "flac",
        "mp3",
        "ogg",
        "wav",
        "codec2",
        "codec2raw",
        "jpegxl_pipe",
        "svg_pipe",
        "libgme",
        "libopenmpt",
    )
)
_INPUT_PROTOCOLS = frozenset(
    ("file", "pipe", "http", "https", "tcp", "tls", "crypto", "udp", "rtp", "rtmp")
)
# Reviewed exact root DER fingerprints, not names or package-version inference.
# Test both the environment-selected system store
# and the explicitly selected certifi bundle on every image architecture.
_COMPROMISED_G3 = "86a1ecba089c4a8d3bbe2734c612ba341d813e043cf9e8a862cd5c57a36bbe6b"
_COMPROMISED_COMODO_AAA = (
    "d7a7a0fb5d7e2731d771e9484ebcdef71d5f0c3e0a2948782bc83ee0ea699ef4"
)
_UNAFFECTED_G1 = "40f6af0346a99aa1cd1d555a4e9cce62c7f9634603ee406615833dc8c8d00367"
_UPSTREAM_BASE = "https://synthetic-user:synthetic-password@media.example/live"
_UPSTREAM_QUERY = "?provider_token=synthetic-resource-token"
_UPSTREAM_HEADERS = {
    "User-Agent": "ECM-native-gate/synthetic",
    "Authorization": "Bearer synthetic-only",
}


def command(*args, binary=False):
    result = subprocess.run(
        args, check=False, capture_output=True, text=not binary, timeout=30
    )
    if result.returncode:
        stderr = result.stderr.decode(errors="replace") if binary else result.stderr
        raise AssertionError(f"{args[0]} exited {result.returncode}: {stderr[-2000:]}")
    return result.stdout


def ffmpeg(*args):
    return command("ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *args)


class QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *_args):
        pass


@contextmanager
def server(directory, context=None):
    instance = http.server.ThreadingHTTPServer(
        ("127.0.0.1", 0),
        functools.partial(QuietHandler, directory=str(directory)),
    )
    if context is not None:
        instance.socket = context.wrap_socket(instance.socket, server_side=True)
    worker = threading.Thread(target=instance.serve_forever, daemon=True)
    worker.start()
    try:
        yield f"{'https' if context else 'http'}://127.0.0.1:{instance.server_port}"
    finally:
        instance.shutdown()
        instance.server_close()
        worker.join(timeout=5)
        assert not worker.is_alive(), "loopback fixture server did not stop"


def tls_context(directory):
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "synthetic loopback")])
    now = datetime.now(UTC)
    certificate = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(hours=1))
        .add_extension(
            x509.SubjectAlternativeName([x509.IPAddress(ip_address("127.0.0.1"))]),
            critical=False,
        )
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(key, hashes.SHA256())
    )
    cert_path, key_path = (
        directory / "synthetic-cert.pem",
        directory / "synthetic-key.pem",
    )
    cert_path.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(cert_path, key_path)
    return context, cert_path


def probe(url, *options, audio=True):
    streams = json.loads(
        command(
            "ffprobe",
            "-v",
            "error",
            *options,
            "-show_streams",
            "-of",
            "json",
            str(url),
        )
    )["streams"]
    video = next(stream for stream in streams if stream["codec_type"] == "video")
    assert (video["codec_name"], video["width"], video["height"]) == (
        "h264",
        128,
        72,
    ), video
    tracks = [stream for stream in streams if stream["codec_type"] == "audio"]
    if audio:
        assert len(tracks) == 1 and tracks[0]["codec_name"] == "aac", tracks
    else:
        assert tracks == [], tracks
    return streams


def assert_relay_url(url, suffix):
    """Only an unpredictable token and the expected public media hint may escape."""
    match = _RELAY_URL_RE.fullmatch(url)
    assert match is not None, (
        "relay exposed a provider URL, path, query, or credentials"
    )
    assert (match.group("suffix") or "") == suffix, "relay media suffix mismatch"


def validate_software_inputs(decoders, demuxers, protocols):
    """Require actual software registrations, not codec descriptors/build flags."""
    available = set(_DECODER_RE.findall(decoders))
    missing = sorted(
        (_COMMON_DECODERS | {"libcodec2", "libjxl", "librsvg", "sonic"}) - available
    )
    # Reviewed implementation-name equivalents only. v308/v408/v410 MOV tags
    # may use rawvideo in newer FFmpeg; rawvideo remains required independently.
    for name, equivalents in {
        "av1": {"av1", "libdav1d", "libaom-av1"},
        "gsm": {"gsm", "libgsm"},
        "gsm_ms": {"gsm_ms", "libgsm_ms"},
    }.items():
        if not available.intersection(equivalents):
            missing.append(name)
    assert not missing, "missing software input decoders: " + ", ".join(missing)
    formats = {
        alias for names in _DEMUXER_RE.findall(demuxers) for alias in names.split(",")
    }
    missing = sorted(_INPUT_DEMUXERS - formats)
    assert not missing, "missing input demuxers: " + ", ".join(missing)
    assert "Input:" in protocols and "Output:" in protocols, (
        "malformed input protocol inventory"
    )
    inputs = protocols.split("Input:", 1)[1].split("Output:", 1)[0]
    inputs = {
        line.strip()
        for line in inputs.splitlines()
        if _PROTOCOL_RE.fullmatch(line.strip())
    }
    missing = sorted(_INPUT_PROTOCOLS - inputs)
    assert not missing, "missing input protocols: " + ", ".join(missing)
    return {
        "software_input_format_support": {
            "decoders": sorted(available),
            "demuxers": sorted(formats),
            "input_protocols": sorted(inputs),
            "sonic": "registered; experimental decoding not enabled or claimed",
            "scope": "Registration guard, not rare-media decoding equivalence or general GPU/CLI parity.",
        }
    }


def software_input_format_support():
    return validate_software_inputs(
        **{
            name: command("ffmpeg", "-hide_banner", "-" + name)
            for name in ("decoders", "demuxers", "protocols")
        }
    )


def load_rare_inputs(fixture_path=None):
    """Validate the five recorded fixtures before creating any decoder input."""
    path = (
        Path(fixture_path)
        if fixture_path is not None
        else Path(__file__).with_name("rare_inputs.json")
    )
    try:
        assert path.stat().st_size <= 8192, "rare fixture manifest exceeds its bound"
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeError) as exc:
        raise AssertionError("rare fixture manifest unavailable or invalid") from exc
    assert isinstance(document, dict) and set(document) == {
        "schema",
        "provenance",
        "fixtures",
    }
    assert type(document["schema"]) is int and document["schema"] == 1
    assert (
        document["provenance"] == _RARE_PROVENANCE
        and document["provenance"]["owned_synthetic"] is True
    )
    fixtures = document["fixtures"]
    assert isinstance(fixtures, dict) and set(fixtures) == set(_RARE_INPUTS), (
        "rare fixture names must match the five owned inputs"
    )
    validated = {}
    for name, (size, digest, expected) in _RARE_INPUTS.items():
        entry = fixtures[name]
        assert isinstance(entry, dict) and set(entry) == {
            "base64",
            "sha256",
            "expected",
        }, name
        assert entry["sha256"] == digest, f"{name}: unrecognized fixture hash"
        assert json.dumps(entry["expected"], sort_keys=True) == json.dumps(
            expected, sort_keys=True
        ), f"{name}: unexpected fixture metadata contract"
        encoded = entry["base64"]
        assert isinstance(encoded, str) and len(encoded) == 4 * ((size + 2) // 3), name
        try:
            data = base64.b64decode(encoded, validate=True)
        except (ValueError, binascii.Error) as exc:
            raise AssertionError(f"{name}: invalid base64") from exc
        assert len(data) == size and hashlib.sha256(data).hexdigest() == digest, (
            f"{name}: fixture integrity mismatch"
        )
        validated[name] = {
            "data": data,
            "expected": entry["expected"],
            "sha256": digest,
        }
    return validated


def _rare_decoded_frames(output, expected, name):
    headers = {}
    rows = []
    for line in output.splitlines():
        if line.startswith("#"):
            if ":" in line:
                key, value = line[1:].split(":", 1)
                headers[key.strip()] = value.strip()
        elif line.strip():
            fields = [field.strip() for field in line.split(",")]
            assert len(fields) == 6 and fields[0] == "0", (
                f"{name}: invalid decoded frame"
            )
            assert all(field.removeprefix("-").isdigit() for field in fields[1:5]), name
            assert (
                int(fields[3]) > 0
                and int(fields[4]) > 0
                and _FRAME_MD5_RE.fullmatch(fields[5])
            ), f"{name}: empty or invalid decoded frame"
            rows.append(int(fields[4]))
    assert rows, f"{name}: no decoded frames"
    assert headers.get("media_type 0") == expected["codec_type"], (
        f"{name}: wrong decoded media kind"
    )
    if expected["codec_type"] == "video":
        assert headers.get("dimensions 0") == "16x16", name
    else:
        assert headers.get("sample_rate 0") == expected["sample_rate"], name
        assert headers.get("channel_layout_name 0") == (
            "mono" if expected["channels"] == 1 else "stereo"
        ), name
    return len(rows), sum(rows)


def synthetic_rare_input_decode(fixture_path=None):
    """Probe and decode actual owned inputs; never compare PCM across versions."""
    fixtures = load_rare_inputs(fixture_path)
    results = {}
    with tempfile.TemporaryDirectory(prefix="ecm-rare-inputs-") as temporary:
        for name, fixture in fixtures.items():
            path = Path(temporary) / name
            path.write_bytes(fixture["data"])
            metadata = json.loads(
                command(
                    "ffprobe",
                    "-v",
                    "error",
                    "-show_streams",
                    "-show_format",
                    "-of",
                    "json",
                    str(path),
                )
            )
            expected = fixture["expected"]
            assert isinstance(metadata, dict) and isinstance(
                metadata.get("format"), dict
            ), name
            assert metadata["format"].get("format_name") == expected["format_name"], (
                f"{name}: wrong probed format"
            )
            streams = metadata.get("streams")
            assert (
                isinstance(streams, list)
                and len(streams) == 1
                and isinstance(streams[0], dict)
            ), name
            for key, value in expected.items():
                if key != "format_name":
                    actual = streams[0].get(key)
                    assert type(actual) is type(value) and actual == value, (
                        f"{name}: wrong probed {key}"
                    )
            output = command(
                "ffmpeg",
                "-hide_banner",
                "-loglevel",
                "error",
                "-i",
                str(path),
                "-t",
                "0.2",
                "-f",
                "framemd5",
                "-",
            )
            frames, decoded_bytes = _rare_decoded_frames(output, expected, name)
            results[name] = {
                **expected,
                "fixture_sha256": fixture["sha256"],
                "decoded_frames": frames,
                "decoded_bytes": decoded_bytes,
            }
    return {"synthetic_rare_input_decode": results}


def assert_prober_argv(args, timeout):
    """Observe the production method's argv without substituting its subprocess."""
    assert len(args) == 12 and _RELAY_URL_RE.fullmatch(args[-1]), (
        "prober must receive only a loopback token"
    )
    assert list(args) == [
        "ffprobe",
        "-v",
        "error",
        "-protocol_whitelist",
        "http,tcp,crypto",
        "-print_format",
        "json",
        "-show_format",
        "-show_streams",
        "-timeout",
        str(timeout * 1000000),
        args[-1],
    ], "production ffprobe timeout/options changed"


async def _reap_owned_children(records):
    for record in records:
        process = record["process"]
        if process.returncode is None:
            record["fallback_cleanup"] = True
            with suppress(ProcessLookupError):
                record["original_kill"]()
            await asyncio.wait_for(record["original_wait"](), timeout=3)


@asynccontextmanager
async def observe_prober_children(timeout):
    """Forward real spawns unchanged; own only the returned process handles."""
    real_spawn = asyncio.create_subprocess_exec
    records = []

    async def spawn(*args, **kwargs):
        assert_prober_argv(args, timeout)
        assert kwargs == {
            "stdout": asyncio.subprocess.PIPE,
            "stderr": asyncio.subprocess.PIPE,
        }
        process = await real_spawn(*args, **kwargs)
        assert process.pid > 0 and process.pid != os.getpid()
        record = {
            "process": process,
            "kill_calls": 0,
            "reaped": False,
            "fallback_cleanup": False,
            "original_kill": process.kill,
            "original_wait": process.wait,
        }
        records.append(record)

        def kill():
            record["kill_calls"] += 1
            record["original_kill"]()

        async def wait():
            result = await record["original_wait"]()
            record["reaped"] = True
            return result

        process.kill, process.wait = kill, wait
        return process

    with patch("asyncio.create_subprocess_exec", spawn):
        try:
            yield records
        finally:
            await _reap_owned_children(records)


def _trust_policy(context):
    assert context.verify_mode == ssl.CERT_REQUIRED and context.check_hostname
    anchors = {
        hashlib.sha256(cert).hexdigest()
        for cert in context.get_ca_certs(binary_form=True)
    }
    assert _COMPROMISED_G3 not in anchors, "compromised emSign G3 is still trusted"
    assert _COMPROMISED_COMODO_AAA not in anchors, (
        "compromised Comodo AAA is still trusted"
    )
    assert _UNAFFECTED_G1 in anchors, "unaffected emSign G1 positive control is missing"
    return {
        "compromised_g3_trusted": False,
        "compromised_comodo_aaa_trusted": False,
        "unaffected_g1_trusted": True,
        "anchor_count": len(anchors),
    }


def system_default_ca_trust():
    # No cafile override: this must include the image's SSL_CERT_FILE behavior.
    return {"system_default_ca_trust": _trust_policy(ssl.create_default_context())}


def certifi_ca_trust():
    import certifi

    return {
        "certifi_ca_trust": _trust_policy(
            ssl.create_default_context(cafile=certifi.where())
        )
    }


@asynccontextmanager
async def fixture_upstream(
    directory, manifest_base, *, headers=None, stall_started=None
):
    """Use the existing security-test seam, NOT a relaxed network/SSRF policy.

    Only stream_request's upstream bytes are replaced. ECM's actual aiohttp
    relay, token rewriting, production preview command and real FFmpeg run.
    This checks decoder/relay compatibility, not the separate SSRF policy suite.
    """
    import httpx

    expected_headers = _UPSTREAM_HEADERS if headers is None else headers

    class StalledBody(httpx.AsyncByteStream):
        async def __aiter__(self):
            stall_started.set()
            await asyncio.Event().wait()
            yield b""  # Keep this an async byte iterator; cancellation ends it.

    files = {
        f"/live/{path.name}": path.read_bytes()
        for path in directory.iterdir()
        if path.is_file()
    }
    playlist = (
        files["/live/playlist.m3u8"].decode().replace(manifest_base, _UPSTREAM_BASE)
    )
    playlist = playlist.replace(
        'synthetic-aes.key"', f'synthetic-aes.key{_UPSTREAM_QUERY}"'
    )
    files["/live/playlist.m3u8"] = (
        "\n".join(
            line + _UPSTREAM_QUERY if line and not line.startswith("#") else line
            for line in playlist.splitlines()
        )
        + "\n"
    ).encode()
    seen = []

    @asynccontextmanager
    async def fake_request(url, **kwargs):
        parsed = urlsplit(url)
        assert parsed.scheme == "https" and parsed.hostname == "media.example"
        assert (parsed.username, parsed.password) == (
            "synthetic-user",
            "synthetic-password",
        )
        assert parsed.path in files, "unexpected synthetic upstream request"
        assert parsed.query == _UPSTREAM_QUERY[1:], "upstream logical query was lost"
        request_headers = dict(kwargs.get("headers") or {})
        assert request_headers == expected_headers, "upstream UA/auth ownership changed"
        record = {"url": url, "headers": request_headers, "closed": False}
        seen.append(record)
        response = httpx.Response(
            200,
            **(
                {"stream": StalledBody()}
                if stall_started is not None
                else {"content": files[parsed.path]}
            ),
            request=httpx.Request("GET", url),
            extensions={"ssrf_logical_url": url},
            headers={
                "Content-Type": "application/vnd.apple.mpegurl"
                if parsed.path.endswith(".m3u8")
                else "application/octet-stream"
            },
        )
        try:
            yield response
        finally:
            await response.aclose()
            record["closed"] = True

    with patch("security.stream_outbound.stream_request", fake_request):
        yield seen


async def production_preview(directory, base, mode, hls=False):
    from routers.stream_preview import _preview_response

    upstream = (
        _UPSTREAM_BASE + ("/playlist.m3u8" if hls else "/source.ts") + _UPSTREAM_QUERY
    )
    async with fixture_upstream(directory, base) as seen:
        response = await _preview_response(upstream, mode, _UPSTREAM_HEADERS)

        async def consume():
            content = bytearray()
            async for chunk in response.body_iterator:
                content.extend(chunk)
                assert len(content) <= 4 * 1024 * 1024, (
                    "synthetic preview unexpectedly large"
                )
            return content

        content = await asyncio.wait_for(consume(), timeout=30)
    assert seen and all(record["closed"] for record in seen), (
        "preview leaked upstream ownership"
    )
    assert content, "production preview returned no MPEG-TS data"
    destination = directory / f"preview-{'hls-' if hls else ''}{mode}.ts"
    destination.write_bytes(content)
    streams = probe(destination, audio=mode == "transcode")
    if mode == "transcode":
        audio = next(stream for stream in streams if stream["codec_type"] == "audio")
        assert audio["channels"] == 2, audio


async def backend_relay_probe(directory, base):
    import httpx
    from security.stream_outbound import validated_subprocess_input

    async with fixture_upstream(directory, base) as seen:
        logical = _UPSTREAM_BASE + "/playlist.m3u8" + _UPSTREAM_QUERY
        async with validated_subprocess_input(
            logical, headers=_UPSTREAM_HEADERS
        ) as upstream:
            assert_relay_url(upstream.argument, ".m3u8")
            async with httpx.AsyncClient(trust_env=False) as client:
                manifest = (await client.get(upstream.argument)).text
                key_match = _HLS_KEY_URI_RE.search(manifest)
                assert key_match is not None, "relayed HLS lost its AES key"
                key_url = key_match.group(1)
                assert_relay_url(key_url, "")  # .key is not an approved media suffix.
                segments = [
                    line
                    for line in manifest.splitlines()
                    if line and not line.startswith("#")
                ]
                assert segments, "relayed HLS lost its segments"
                for url in segments:
                    assert_relay_url(url, ".ts")
                assert all(
                    value not in manifest
                    for value in (
                        "media.example",
                        "/live/",
                        "synthetic-user",
                        "synthetic-password",
                        "provider_token",
                        "synthetic-resource-token",
                    )
                ), "provider details escaped into relayed HLS"
                targets = upstream._relay._targets

                def token(url):
                    return urlsplit(url).path.rsplit("/", 1)[-1]

                assert targets[token(upstream.argument)] == logical
                assert (
                    targets[token(key_url)]
                    == _UPSTREAM_BASE + "/synthetic-aes.key" + _UPSTREAM_QUERY
                )
                for url in segments:
                    target = targets[token(url)]
                    path = urlsplit(target).path
                    assert (
                        target
                        == _UPSTREAM_BASE + "/" + Path(path).name + _UPSTREAM_QUERY
                    )
                    assert path.startswith("/live/segment") and path.endswith(".ts")
                assert (await client.get(key_url)).content == bytes(range(16)), (
                    "relay served the wrong AES key"
                )
                before = len(seen)
                for forged in (
                    key_url + ".ts",
                    segments[0].removesuffix(".ts") + ".mp4",
                ):
                    assert (await client.get(forged)).status_code == 404
                assert len(seen) == before, (
                    "a forged suffix initiated an upstream fetch"
                )
            # Match production's whitelist. Do NOT add allowed_extensions=ALL,
            # disable extension_picky, or bypass ECM's relay to make this green.
            await asyncio.to_thread(
                probe, upstream.argument, "-protocol_whitelist", "http,tcp,crypto"
            )
            await upstream.raise_for_relay_failure()
        assert any(
            urlsplit(record["url"]).path == "/live/synthetic-aes.key" for record in seen
        )
        assert any(
            urlsplit(record["url"]).path.startswith("/live/segment") for record in seen
        )
    assert all(record["closed"] for record in seen), "probe leaked upstream ownership"


async def backend_relay_previews(directory, base):
    await production_preview(directory, base, "transcode", hls=True)
    await production_preview(directory, base, "video_only", hls=True)


async def production_prober(directory, base, *, hls=False):
    from stream_prober import StreamProber

    prober = StreamProber.__new__(StreamProber)
    prober.probe_timeout = 5
    user_agent = _UPSTREAM_HEADERS["User-Agent"]
    logical = (
        _UPSTREAM_BASE + ("/playlist.m3u8" if hls else "/source.ts") + _UPSTREAM_QUERY
    )
    async with observe_prober_children(prober.probe_timeout) as records:
        async with fixture_upstream(
            directory, base, headers={"User-Agent": user_agent}
        ) as seen:
            data = await asyncio.wait_for(
                prober._run_ffprobe_once(logical, user_agent=user_agent), timeout=15
            )
        assert seen and all(record["closed"] for record in seen), (
            "production prober leaked upstream ownership"
        )
    assert (
        len(records) == 1
        and records[0]["reaped"]
        and not records[0]["fallback_cleanup"]
    )
    assert records[0]["process"].returncode == 0
    stats = SimpleNamespace()
    prober._parse_ffprobe_data(stats, data)
    assert (stats.resolution, stats.video_codec, stats.audio_codec) == (
        "128x72",
        "h264",
        "aac",
    )
    assert stats.stream_type == ("HLS" if hls else "MPEG-TS")
    if hls:
        paths = [urlsplit(record["url"]).path for record in seen]
        assert "/live/synthetic-aes.key" in paths and any(
            path.startswith("/live/segment") for path in paths
        )
    return {
        "production_prober_aes_hls" if hls else "production_prober_ts": {
            "children": 1,
            "pid": records[0]["process"].pid,
            "reaped": True,
            "resolution": stats.resolution,
            "video_codec": stats.video_codec,
            "audio_codec": stats.audio_codec,
            "scope": "Real _run_ffprobe_once and in-memory parsing; no persistence/retry claim.",
        }
    }


async def production_prober_cancellation(directory, base):
    from stream_prober import StreamProber

    prober = StreamProber.__new__(StreamProber)
    prober.probe_timeout = 5
    user_agent = _UPSTREAM_HEADERS["User-Agent"]
    stalled = asyncio.Event()
    async with observe_prober_children(prober.probe_timeout) as records:
        async with fixture_upstream(
            directory, base, headers={"User-Agent": user_agent}, stall_started=stalled
        ) as seen:
            task = asyncio.create_task(
                prober._run_ffprobe_once(
                    _UPSTREAM_BASE + "/source.ts" + _UPSTREAM_QUERY,
                    user_agent=user_agent,
                )
            )
            try:
                await asyncio.wait_for(stalled.wait(), timeout=5)
                assert len(records) == 1 and records[0]["process"].returncode is None
                task.cancel()
                done, _ = await asyncio.wait({task}, timeout=5)
                assert task in done, "production prober cancellation did not finish"
                try:
                    await task
                except asyncio.CancelledError:
                    pass
                else:
                    raise AssertionError("production prober swallowed cancellation")
                record = records[0]
                assert (
                    record["kill_calls"] == 1
                    and record["reaped"]
                    and record["process"].returncode == -9
                )
                assert not record["fallback_cleanup"], (
                    "fixture cleanup substituted for production cleanup"
                )
            finally:
                if not task.done():
                    await _reap_owned_children(records)
                    task.cancel()
                    done, _ = await asyncio.wait({task}, timeout=5)
                    assert task in done, "owned prober task could not be cleaned up"
                if task.done() and not task.cancelled():
                    task.exception()  # Retrieve a failed task without replacing its primary failure.
        assert seen and all(item["closed"] for item in seen), (
            "cancelled prober leaked upstream ownership"
        )
    return {
        "production_prober_cancellation": {
            "owned_children": 1,
            "pid": records[0]["process"].pid,
            "kill_calls": 1,
            "reaped": True,
            "returncode": -9,
        }
    }


def python_imports():
    for module in (
        "_cffi_backend",
        "bcrypt",
        "pydantic_core",
        "greenlet",
        "httptools",
        "uvloop",
        "watchfiles",
        "rapidfuzz",
        "regex",
        "yaml",
        "aiohttp",
        "multidict",
        "yarl",
        "frozenlist",
        "propcache",
        "markupsafe",
        "wrapt",
        "coverage",
    ):
        importlib.import_module(module)


def python_operations():
    import bcrypt
    import cryptography
    import uvloop
    from cryptography.hazmat.backends.openssl.backend import backend
    from cryptography.hazmat.bindings import _rust
    from greenlet import greenlet
    from pydantic_core import SchemaValidator
    from rapidfuzz.fuzz import ratio

    # Check the reviewed wheel's static copy, not ssl's separate system library.
    # Other OpenSSL branches require their own artifact/advisory review.
    bundled_openssl = backend.openssl_version_text()
    match = re.fullmatch(
        r"OpenSSL 4\.0\.(\d+) [0-9]{1,2} [A-Z][a-z]{2} [0-9]{4}",
        bundled_openssl,
    )
    assert match is not None and int(match[1]) >= 3, (
        f"unreviewed or vulnerable bundled OpenSSL: {bundled_openssl}"
    )
    assert ratio("synthetic channel", "synthetic channel") == 100
    assert bcrypt.checkpw(
        b"synthetic-only", bcrypt.hashpw(b"synthetic-only", bcrypt.gensalt(rounds=4))
    )
    assert greenlet(lambda: 42).switch() == 42
    assert SchemaValidator({"type": "int"}).validate_python("42") == 42
    loop = uvloop.new_event_loop()
    try:
        assert loop.run_until_complete(asyncio.sleep(0, result=42)) == 42
    finally:
        loop.close()
    return {
        "cryptography_openssl": {
            "package_version": cryptography.__version__,
            "loaded_version": bundled_openssl,
            "extension_sha256": hashlib.sha256(
                Path(_rust.__file__).read_bytes()
            ).hexdigest(),
        }
    }


def sqlite_checks(directory):
    path = directory / "synthetic.db"
    with sqlite3.connect(path) as database:
        assert database.execute("PRAGMA journal_mode=WAL").fetchone()[0] == "wal"
        database.execute("PRAGMA foreign_keys=ON")
        database.execute("CREATE TABLE parent (id INTEGER PRIMARY KEY)")
        database.execute("CREATE TABLE child (parent_id INTEGER REFERENCES parent(id))")
        database.execute("INSERT INTO parent VALUES (1)")
        database.execute("INSERT INTO child VALUES (1)")
        database.commit()
        try:
            database.execute("INSERT INTO child VALUES (2)")
        except sqlite3.IntegrityError:
            database.rollback()
        else:
            raise AssertionError("SQLite did not enforce foreign keys")
    with sqlite3.connect(path) as database:
        assert database.execute("SELECT parent_id FROM child").fetchall() == [(1,)]


def dst_checks():
    zone = ZoneInfo("America/New_York")
    assert datetime(2026, 3, 8, 6, 30, tzinfo=UTC).astimezone(
        zone
    ).utcoffset() == timedelta(hours=-5)
    assert datetime(2026, 3, 8, 7, 30, tzinfo=UTC).astimezone(
        zone
    ).utcoffset() == timedelta(hours=-4)


def timeout_checks():
    assert "GNU coreutils" in command("/usr/bin/timeout", "--version")
    started = time.monotonic()
    result = subprocess.run(
        [
            "/usr/bin/timeout",
            "--signal=KILL",
            "0.2s",
            sys.executable,
            "-c",
            "import time; time.sleep(10)",
        ],
        check=False,
        capture_output=True,
        timeout=3,
    )
    assert result.returncode in (-9, 137), result.returncode
    assert time.monotonic() - started < 3


def getent_checks():
    user = command("getent", "passwd", "appuser").strip().split(":")
    group = command("getent", "group", "appuser").strip().split(":")
    assert user[0] == group[0] == "appuser"
    assert int(user[2]) > 0 and int(user[3]) == int(group[2]) > 0
    version = command("getent", "--version").splitlines()[0]
    assert re.fullmatch(
        r"getent \((?:GNU libc|Debian GLIBC [0-9][^)]*|glibc-[0-9][^)]*)\) "
        r"[0-9]+\.[0-9]+(?:\.[0-9]+)?",
        version,
    ), version
    return {"getent": version}


def native_metadata():
    version = command("/usr/local/bin/resdet", "-V").splitlines()
    assert version == [
        "resdet version 2.4.3",
        "libresdet version 3.2.0",
        "Built with image readers: Y4M",
    ]
    notices = {}
    for name in (
        "COPYING",
        "COPYING.LGPL.txt",
        "COPYING.MIT.txt",
        "kissfft/COPYING",
        "kissfft/LICENSES/BSD-3-Clause",
    ):
        content = (Path("/usr/share/doc/resdet") / name).read_bytes()
        assert content.strip(), f"missing license notice {name}"
        notices[name] = hashlib.sha256(content).hexdigest()
    return {
        "python": sys.version.split()[0],
        "architecture": platform.machine(),
        "openssl": ssl.OPENSSL_VERSION,
        "sqlite": sqlite3.sqlite_version,
        "ffmpeg": command("ffmpeg", "-version").splitlines()[0],
        "ffmpeg_build_configuration": command("ffmpeg", "-buildconf").strip(),
        "ffmpeg_license": command("ffmpeg", "-L").strip(),
        "resdet": version,
        "resdet_license_notice_sha256": notices,
        "resdet_pixel_max": PIXEL_MAX,
        "scope": "Behavioral compatibility, not a CVE clearance; only this running architecture tested.",
        "baseline_gap": "Raw DASH only: existing ECM relay does not rewrite MPD resources; not a migration regression.",
    }


def resdet_checks(directory, base):
    frame = command(
        "/usr/bin/timeout",
        "--signal=KILL",
        "5s",
        "ffmpeg",
        "-nostdin",
        "-v",
        "error",
        "-protocol_whitelist",
        "http,tcp,crypto",
        "-timeout",
        "5000000",
        "-i",
        f"{base}/source.ts",
        "-frames:v",
        "1",
        "-an",
        "-sn",
        "-dn",
        "-pix_fmt",
        "yuv420p",
        "-f",
        "yuv4mpegpipe",
        "-fs",
        str(FRAME_MAX_BYTES),
        "-y",
        "pipe:1",
        binary=True,
    )
    assert 0 < len(frame) <= FRAME_MAX_BYTES
    from stream_prober import RESDET_FRAME_MAX_BYTES, RESDET_MAX_PIXELS, StreamProber

    assert (RESDET_MAX_PIXELS, RESDET_FRAME_MAX_BYTES) == (PIXEL_MAX, FRAME_MAX_BYTES)
    StreamProber._validate_resdet_y4m_frame(frame)
    destination = directory / "frame.y4m"
    destination.write_bytes(frame)
    result = command(
        "/usr/bin/timeout",
        "--signal=KILL",
        "5s",
        "/usr/local/bin/resdet",
        "-R",
        "Y4M",
        "-v",
        "1",
        "-n",
        "1",
        str(destination),
    ).split()
    assert len(result) == 2 and all(int(value) > 0 for value in result), result


def pixel_ceiling(directory):
    # Feed a real complete frame just beyond the pinned ceiling. The reader
    # must reject it, not silently accept an expanded native attack surface.
    path = directory / "over-limit.y4m"
    width, height = 4096, 2162
    header = f"YUV4MPEG2 W{width} H{height} F1:1 Ip A1:1 C420jpeg\nFRAME\n".encode()
    path.write_bytes(
        header + bytes(width * height + 2 * ((width + 1) // 2) * ((height + 1) // 2))
    )
    result = subprocess.run(
        [
            "/usr/bin/timeout",
            "--signal=KILL",
            "5s",
            "/usr/local/bin/resdet",
            "-R",
            "Y4M",
            "-v",
            "1",
            "-n",
            "1",
            str(path),
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=7,
    )
    assert result.returncode > 0 and result.returncode not in (124, 127, 137), (
        "resdet accepted over-limit frame, crashed, or timed out"
    )


def main():
    checks, failures, metadata = [], {}, {}
    started = time.monotonic()

    def check(name, operation):
        try:
            result = operation()
            if isinstance(result, dict):
                metadata.update(result)
            checks.append(name)
        except Exception as exc:  # noqa: BLE001 - Aggregate unexpected check failures into a failing report; never skip them.
            failures[name] = f"{type(exc).__name__}: {exc}"

    with tempfile.TemporaryDirectory(prefix="ecm-native-fixtures-") as temporary:
        directory = Path(temporary)
        # Application imports must never read or write an operator config.
        os.environ["CONFIG_DIR"] = str(directory / "config")
        os.environ["DATABASE_URL"] = f"sqlite:///{directory}/synthetic-app.db"
        sys.path.insert(0, "/app")
        check("native_python_wheel_imports", python_imports)
        check("native_python_operations", python_operations)
        check("system_default_ca_trust", system_default_ca_trust)
        check("certifi_ca_trust", certifi_ca_trust)
        check("sqlite_wal_foreign_keys_transaction", lambda: sqlite_checks(directory))
        check("named_timezone_dst_transition", dst_checks)
        check("gnu_timeout_kills_child", timeout_checks)
        check("getent_identity_tools", getent_checks)
        check("native_versions_readers_licenses", native_metadata)
        check("software_input_format_support", software_input_format_support)
        check("synthetic_rare_input_decode", synthetic_rare_input_decode)
        source = directory / "source.ts"
        check(
            "generated_h264_aac_mpegts",
            lambda: ffmpeg(
                "-f",
                "lavfi",
                "-i",
                "testsrc2=duration=2:size=128x72:rate=2",
                "-f",
                "lavfi",
                "-i",
                "sine=duration=2:sample_rate=48000",
                "-c:v",
                "libx264",
                "-threads",
                "1",
                "-preset",
                "ultrafast",
                "-g",
                "2",
                "-c:a",
                "aac",
                "-shortest",
                str(source),
            ),
        )
        with server(directory) as base:
            check(
                "http_probe",
                lambda: probe(f"{base}/source.ts", "-rw_timeout", "5000000"),
            )

            def hls():
                (directory / "synthetic-aes.key").write_bytes(bytes(range(16)))
                key_info = directory / "key-info.txt"
                key_info.write_text(
                    f"{base}/synthetic-aes.key\n{directory / 'synthetic-aes.key'}\n00000000000000000000000000000000\n"
                )
                ffmpeg(
                    "-i",
                    str(source),
                    "-c",
                    "copy",
                    "-hls_time",
                    "1",
                    "-hls_playlist_type",
                    "vod",
                    "-hls_key_info_file",
                    str(key_info),
                    "-hls_segment_filename",
                    str(directory / "segment%03d.ts"),
                    str(directory / "playlist.m3u8"),
                )
                probe(
                    f"{base}/playlist.m3u8",
                    "-protocol_whitelist",
                    "http,https,tcp,tls,crypto",
                    "-rw_timeout",
                    "5000000",
                )

            check("aes_encrypted_hls_probe", hls)
            check(
                "production_prober_ts",
                lambda: asyncio.run(production_prober(directory, base)),
            )
            check(
                "production_prober_aes_hls",
                lambda: asyncio.run(production_prober(directory, base, hls=True)),
            )
            check(
                "production_prober_cancellation",
                lambda: asyncio.run(production_prober_cancellation(directory, base)),
            )

            def dash():
                ffmpeg(
                    "-i",
                    str(source),
                    "-c:v",
                    "libx264",
                    "-threads",
                    "1",
                    "-preset",
                    "ultrafast",
                    "-c:a",
                    "aac",
                    "-f",
                    "dash",
                    "-seg_duration",
                    "1",
                    str(directory / "manifest.mpd"),
                )
                probe(f"{base}/manifest.mpd", "-rw_timeout", "5000000")

            check("dash_probe", dash)
            check("bounded_y4m_resdet", lambda: resdet_checks(directory, base))
            check("resdet_pixel_ceiling", lambda: pixel_ceiling(directory))

            def signalstats():
                output = command(
                    "ffmpeg",
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-i",
                    str(source),
                    "-vf",
                    "signalstats,metadata=print:file=-",
                    "-frames:v",
                    "1",
                    "-f",
                    "null",
                    "-",
                )
                assert "lavfi.signalstats.YAVG=" in output, output

            check("signalstats_black_screen_filter", signalstats)
            for mode in ("transcode", "video_only"):
                check(
                    f"mpegts_preview_{mode}",
                    lambda mode=mode: asyncio.run(
                        production_preview(directory, base, mode)
                    ),
                )
            check(
                "aes_hls_backend_relay_probe",
                lambda: asyncio.run(backend_relay_probe(directory, base)),
            )
            check(
                "aes_hls_backend_relay_previews",
                lambda: asyncio.run(backend_relay_previews(directory, base)),
            )
        context, certificate = tls_context(directory)
        with server(directory, context) as base:
            check(
                "verified_https_probe",
                lambda: probe(
                    f"{base}/source.ts",
                    "-tls_verify",
                    "1",
                    "-ca_file",
                    str(certificate),
                    "-rw_timeout",
                    "5000000",
                ),
            )

            def untrusted_tls():
                result = subprocess.run(
                    [
                        "ffprobe",
                        "-v",
                        "error",
                        "-tls_verify",
                        "1",
                        "-rw_timeout",
                        "5000000",
                        f"{base}/source.ts",
                    ],
                    check=False,
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
                refusal = result.stderr.lower()
                assert result.returncode != 0 and (
                    "certificate verify failed" in refusal
                    or "certificate failed verification" in refusal
                ), result.stderr

            check("https_untrusted_certificate_refused", untrusted_tls)
        # -I omits the mounted script directory from sys.path. Import only this
        # read-only sibling, not application or delivered source-code helpers.
        spec = importlib.util.spec_from_file_location(
            "native_source_checks", Path(__file__).with_name("source_checks.py")
        )
        source_checks = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(source_checks)
        check(
            "native_source_and_artifact_integrity",
            source_checks.native_source_and_artifact_integrity,
        )
    expected = json.loads(Path(__file__).with_name("checks.json").read_text())
    assert checks == [name for name in expected if name not in failures], (
        "runtime check contract drifted"
    )
    print(
        json.dumps(
            {
                "status": "FAIL" if failures else "PASS",
                "checks": checks,
                "failures": failures,
                "metadata": metadata,
                "duration_seconds": round(time.monotonic() - started, 3),
            },
            indent=2,
        )
    )
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
