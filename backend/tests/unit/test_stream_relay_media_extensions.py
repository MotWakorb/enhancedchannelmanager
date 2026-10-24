"""Keep HLS demuxer checks without exposing provider URLs (n3efz/x1xne)."""

import re
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from aiohttp import web
from security import stream_outbound


@pytest.mark.parametrize(
    ("path", "suffix"),
    [
        ("master.m3u8", ".m3u8"),
        ("segment.ts", ".ts"),
        ("segment.TS", ".ts"),
        ("fragment.m4s", ".m4s"),
        ("init.mp4", ".mp4"),
        ("audio.aac", ".aac"),
        ("audio.ac3", ".ac3"),
        ("captions.vtt", ".vtt"),
        ("captions.webvtt", ".webvtt"),
        ("segment", ""),
        ("secret.private", ""),
        ("secret.php", ""),
        ("file.ts/secret", ""),
        ("file%2Ets", ""),
    ],
)
def test_local_url_keeps_only_known_media_suffix(path, suffix):
    upstream = f"https://user:password@provider.invalid/private/{path}?token=credential"
    relay = stream_outbound._LocalStreamRelay(
        "https://provider.invalid/master.m3u8", {}, 1
    )
    request = SimpleNamespace(scheme="http", host="127.0.0.1:12345")

    local = relay._local_url(request, upstream)

    token = local.rsplit("/", 1)[-1]
    assert re.fullmatch(r"[0-9a-f]{32}" + re.escape(suffix), token)
    assert relay._targets[token] == upstream
    assert relay._local_url(request, upstream) == local
    assert local.startswith("http://127.0.0.1:12345/resource/")
    for private in ("user", "password", "provider.invalid", "private", "credential"):
        assert private not in local


def test_manifest_retains_media_types_but_not_provider_paths_or_credentials():
    relay = stream_outbound._LocalStreamRelay(
        "https://provider.invalid/master.m3u8", {}, 1
    )
    request = SimpleNamespace(scheme="http", host="127.0.0.1:12345")
    manifest = (
        b'#EXTM3U\n#EXT-X-KEY:METHOD=AES-128,URI="../secrets/key.php?token=key-secret"\n'
        b'#EXT-X-MAP:URI="init.mp4?token=init-secret"\n'
        b"#EXTINF:1,\nsegment.ts?token=segment-secret\n#EXT-X-ENDLIST\n"
    )

    rewritten = relay._rewrite_hls(
        request, manifest, "https://provider.invalid/media/list.m3u8"
    ).decode()

    assert re.search(r"/resource/[0-9a-f]{32}\.ts\n", rewritten)
    assert re.search(r'/resource/[0-9a-f]{32}\.mp4"', rewritten)
    assert re.search(r'/resource/[0-9a-f]{32}"', rewritten)
    assert set(relay._targets.values()) == {
        "https://provider.invalid/master.m3u8",
        "https://provider.invalid/secrets/key.php?token=key-secret",
        "https://provider.invalid/media/init.mp4?token=init-secret",
        "https://provider.invalid/media/segment.ts?token=segment-secret",
    }
    for private in (
        "provider.invalid",
        "key-secret",
        "init-secret",
        "segment-secret",
        "key.php",
        "segment.ts",
    ):
        assert private not in rewritten


@pytest.mark.asyncio
async def test_suffix_substitution_is_unknown_token_and_never_fetches(monkeypatch):
    relay = stream_outbound._LocalStreamRelay(
        "https://provider.invalid/master.m3u8", {}, 1
    )
    token = relay._register("https://provider.invalid/segment.ts")
    assert token.endswith(".ts")
    forged = token.removesuffix(".ts") + ".mp4"
    fetch = Mock(
        side_effect=AssertionError("unknown token must not initiate upstream traffic")
    )
    monkeypatch.setattr(stream_outbound, "stream_request", fetch)

    with pytest.raises(web.HTTPNotFound):
        await relay._handle(SimpleNamespace(match_info={"token": forged}))

    fetch.assert_not_called()


def test_collision_and_resource_limit_still_apply_to_suffixed_tokens(monkeypatch):
    values = iter(["a" * 32, "b" * 32, "b" * 32, "c" * 32])
    monkeypatch.setattr(stream_outbound.secrets, "token_hex", lambda size: next(values))
    relay = stream_outbound._LocalStreamRelay(
        "https://provider.invalid/master.m3u8", {}, 1
    )
    first = relay._register("https://provider.invalid/one.ts")
    second = relay._register("https://provider.invalid/two.ts")
    assert first == "b" * 32 + ".ts"
    assert second == "c" * 32 + ".ts"
    monkeypatch.setattr(stream_outbound, "_MAX_RELAY_RESOURCES", 3)
    with pytest.raises(stream_outbound.SSRFError, match="resource limit"):
        relay._register("https://provider.invalid/three.ts")
