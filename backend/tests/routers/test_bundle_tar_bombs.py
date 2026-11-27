"""analyze/from-bundle must not inflate tar extended headers or gzip bombs."""

import io
import tarfile
import tracemalloc
import zlib
from unittest.mock import patch

import pytest

BUNDLE_ROUTE = "/api/auto-creation/rules/analyze/from-bundle"
RULES = b"version: 1\nrules: []\n"
BLOCK = 512
MIB = 1024 * 1024


def gz(parts):
    """Stream-compress ``parts`` so building a bomb never holds it expanded."""
    compressor = zlib.compressobj(9, zlib.DEFLATED, 31)
    out = io.BytesIO()
    for part in parts:
        out.write(compressor.compress(part))
    out.write(compressor.flush())
    return out.getvalue()


def member(name, data, fmt=tarfile.USTAR_FORMAT):
    info = tarfile.TarInfo(name)
    info.size = len(data)
    return info.tobuf(format=fmt) + data + b"\0" * ((-len(data)) % BLOCK)


def end():
    return b"\0" * (BLOCK * 2)


def filler(size):
    chunk = b"a" * MIB
    left = size
    while left > 0:
        piece = chunk[: min(left, len(chunk))]
        left -= len(piece)
        yield piece


def gnu_long_bomb(typeflag, size):
    def parts():
        header = tarfile.TarInfo("././@LongLink")
        header.type = typeflag
        header.size = size
        yield header.tobuf(format=tarfile.GNU_FORMAT)
        # A long name replaces the next member's name, so end it in rules.yaml.
        yield from filler(size - len(b"/rules.yaml"))
        yield b"/rules.yaml"
        yield b"\0" * ((-size) % BLOCK)
        yield member("rules.yaml", RULES, tarfile.GNU_FORMAT)
        yield end()

    return gz(parts())


def pax_bomb(typeflag, size):
    def parts():
        header = tarfile.TarInfo("././@PaxHeader")
        header.type = typeflag
        header.size = size
        yield header.tobuf(format=tarfile.USTAR_FORMAT)
        record = b"%d comment=" % size
        yield record
        yield from filler(size - len(record) - 1)
        yield b"\n"
        yield b"\0" * ((-size) % BLOCK)
        yield member("rules.yaml", RULES)
        yield end()

    return gz(parts())


BOMBS = {
    "gnu-longname": lambda size: gnu_long_bomb(tarfile.GNUTYPE_LONGNAME, size),
    "gnu-longlink": lambda size: gnu_long_bomb(tarfile.GNUTYPE_LONGLINK, size),
    "pax-local": lambda size: pax_bomb(tarfile.XHDTYPE, size),
    "pax-global": lambda size: pax_bomb(tarfile.XGLTYPE, size),
}


async def post(async_client, data):
    return await async_client.post(
        BUNDLE_ROUTE, files={"file": ("bundle.tar.gz", data, "application/gzip")}
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", sorted(BOMBS))
async def test_extended_header_bomb_refused_without_inflating_it(async_client, kind):
    from routers import channel_pipeline

    size = 32 * MIB
    data = BOMBS[kind](size)
    assert len(data) < 256 * 1024
    readers = []

    class Recording(channel_pipeline._CappedReader):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            readers.append(self)

    tracemalloc.start()
    try:
        with patch.object(channel_pipeline, "_CappedReader", Recording):
            response = await post(async_client, data)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()

    assert response.status_code == 413
    assert "extended header" in response.json()["detail"]
    assert readers and max(r.total for r in readers) < 256 * 1024
    assert peak < 16 * MIB


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", sorted(BOMBS))
async def test_small_extended_header_is_accepted(async_client, kind):
    # 2 KiB is well inside the extended-header allowance: such headers are
    # ordinary (long or non-ASCII names) and must keep working.
    response = await post(async_client, BOMBS[kind](2_048))
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_extended_header_chain_is_bounded(async_client):
    def parts():
        for _ in range(200):
            header = tarfile.TarInfo("././@PaxHeader")
            header.type = tarfile.XGLTYPE
            record = b"20 comment=aaaaaaaa\n"
            header.size = len(record)
            yield header.tobuf(format=tarfile.USTAR_FORMAT)
            yield record + b"\0" * ((-len(record)) % BLOCK)
        yield member("rules.yaml", RULES)
        yield end()

    response = await post(async_client, gz(parts()))
    assert response.status_code == 413
    assert "extended headers" in response.json()["detail"]


@pytest.mark.asyncio
async def test_gnu_sparse_member_is_refused(async_client):
    info = tarfile.TarInfo("sparse.bin")
    info.type = tarfile.GNUTYPE_SPARSE
    info.size = 0
    data = gz([info.tobuf(format=tarfile.GNU_FORMAT), member("rules.yaml", RULES), end()])
    response = await post(async_client, data)
    assert response.status_code == 400
    assert "unsupported" in response.json()["detail"]


@pytest.mark.asyncio
async def test_pax_sparse_member_is_refused(async_client):
    # PAX records are "<len> key=value\n", where <len> counts the whole record.
    records = b""
    for kv in (b"GNU.sparse.major=1", b"GNU.sparse.minor=0"):
        body = b" " + kv + b"\n"
        length = len(body) + 2
        records += b"%d" % length + body
    header = tarfile.TarInfo("././@PaxHeader")
    header.type = tarfile.XHDTYPE
    header.size = len(records)
    sparse_map = b"999999999\n" + b"0\n" * 200
    target = tarfile.TarInfo("sparse.bin")
    target.size = len(sparse_map)
    data = gz(
        [
            header.tobuf(format=tarfile.USTAR_FORMAT),
            records + b"\0" * ((-len(records)) % BLOCK),
            target.tobuf(format=tarfile.USTAR_FORMAT),
            sparse_map + b"\0" * ((-len(sparse_map)) % BLOCK),
            member("rules.yaml", RULES),
            end(),
        ]
    )
    response = await post(async_client, data)
    assert response.status_code == 400
    assert "unsupported" in response.json()["detail"]


@pytest.mark.asyncio
async def test_member_data_inflation_is_capped_by_the_reader(async_client):
    from routers import channel_pipeline

    readers = []

    class Recording(channel_pipeline._CappedReader):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            readers.append(self)

    data = gz([member("logs.txt", b"\0" * (8 * MIB)), member("rules.yaml", RULES), end()])
    with (
        patch.object(channel_pipeline, "_CappedReader", Recording),
        patch.object(channel_pipeline, "_BUNDLE_MAX_TOTAL_BYTES", MIB),
        patch.object(channel_pipeline, "_BUNDLE_INFLATE_ALLOWANCE_BYTES", 64 * 1024),
    ):
        response = await post(async_client, data)
    assert response.status_code == 413
    assert max(r.total for r in readers) <= MIB + 64 * 1024 + 64 * 1024


def test_capped_reader_refuses_past_its_limit():
    from routers.channel_pipeline import _BundleRefused, _CappedReader

    reader = _CappedReader(io.BytesIO(b"x" * 10_000), 4_096)
    assert len(reader.read(4_096)) == 4_096
    with pytest.raises(_BundleRefused) as error:
        reader.read(4_096)
    assert error.value.status_code == 413
    assert reader.total <= 4_096 + 4_096


def test_capped_reader_unbounded_read_is_bounded():
    from routers.channel_pipeline import _BundleRefused, _CappedReader

    reader = _CappedReader(io.BytesIO(b"x" * 10_000), 4_096)
    with pytest.raises(_BundleRefused):
        reader.read()
    assert reader.total <= 4_097


def test_tarfile_hooks_the_guard_relies_on_still_exist():
    # The guard overrides these TarInfo methods. A Python upgrade that renames
    # them would silently bypass it, so fail loudly instead.
    for name in ("_proc_member", "_proc_gnusparse_00", "_proc_gnusparse_01", "_proc_gnusparse_10"):
        assert callable(getattr(tarfile.TarInfo, name, None)), name


@pytest.mark.asyncio
async def test_bundle_with_leading_directory_and_pax_names_still_analyzes(async_client):
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz", format=tarfile.PAX_FORMAT) as archive:
        for name, blob in (
            ("ecm-debug-bundle/chännel_notes.txt", "é".encode() * 100),
            ("ecm-debug-bundle/" + "n" * 150 + ".txt", b"long name"),
            ("ecm-debug-bundle/channel_groups_diagnostic.json", b"{}"),
            ("ecm-debug-bundle/rules.yaml", RULES),
        ):
            info = tarfile.TarInfo(name)
            info.size = len(blob)
            archive.addfile(info, io.BytesIO(blob))
    response = await post(async_client, buf.getvalue())
    assert response.status_code == 200
