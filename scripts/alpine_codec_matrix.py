#!/usr/bin/env python3
"""Bounded same-input Codec2 probe. Invoke each case in a fresh native process.

The parent compares the complete encoded/decoded byte hashes, not a tolerance.
This is a diagnostic corpus, not exhaustive codec or whole-image acceptance.
"""

from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import platform
import struct
from pathlib import Path

MODES = {0: "3200", 1: "2400", 2: "1600", 3: "1400", 4: "1300", 5: "1200", 8: "700C"}
CORPORA = ("silence", "saw", "impulse", "noise", "alternating")


def samples(corpus: str, count: int) -> list[int]:
    if corpus not in CORPORA:
        raise ValueError("unknown codec corpus")
    state, result = 1, []
    for i in range(count):
        state = (1664525 * state + 1013904223) & 0xFFFFFFFF
        value = {
            "silence": 0,
            "saw": ((i * 173) % 24001) - 12000,
            "impulse": 24000 if i % 160 == 0 else 0,
            "noise": ((state >> 16) % 16001) - 8000,
            "alternating": 16000 if i % 2 else -16000,
        }[corpus]
        result.append(value)
    return result


def run_case(
    library: Path, mode: int, corpus: str, packed_input: bytes | None = None
) -> dict:
    lib = ctypes.CDLL(str(library.resolve()))
    lib.codec2_create.argtypes, lib.codec2_create.restype = (
        [ctypes.c_int],
        ctypes.c_void_p,
    )
    lib.codec2_destroy.argtypes = [ctypes.c_void_p]
    for name in ["codec2_samples_per_frame", "codec2_bits_per_frame"]:
        getattr(lib, name).argtypes, getattr(lib, name).restype = (
            [ctypes.c_void_p],
            ctypes.c_int,
        )
    lib.codec2_encode.argtypes = [
        ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_ubyte),
        ctypes.POINTER(ctypes.c_short),
    ]
    lib.codec2_decode.argtypes = [
        ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_short),
        ctypes.POINTER(ctypes.c_ubyte),
    ]
    encoder, decoder = lib.codec2_create(mode), lib.codec2_create(mode)
    if not encoder or not decoder:
        raise RuntimeError("existing codec mode unavailable")
    try:
        n, bits = (
            lib.codec2_samples_per_frame(encoder),
            lib.codec2_bits_per_frame(encoder),
        )
        if n not in {160, 320} or not 0 < bits <= 128:
            raise RuntimeError("unexpected codec dimensions")
        width, frames = (bits + 7) // 8, 16
        if packed_input is not None and len(packed_input) != width * frames:
            raise RuntimeError("decode-only stream size mismatch")
        source = samples(corpus, frames * n)
        encoded, decoded = bytearray(), bytearray()
        for frame in range(frames):
            pcm = (ctypes.c_short * n)(*source[frame * n : (frame + 1) * n])
            packed, output = (ctypes.c_ubyte * width)(), (ctypes.c_short * n)()
            if packed_input is None:
                lib.codec2_encode(encoder, packed, pcm)
            else:
                ctypes.memmove(
                    packed, packed_input[frame * width : (frame + 1) * width], width
                )
            lib.codec2_decode(decoder, output, packed)
            encoded.extend(bytes(packed))
            decoded.extend(struct.pack("<" + "h" * n, *output))
        # Capture the actual loaded-library mapping in the process that called
        # the codec, rather than a separate ctypes loader probe.
        maps = Path("/proc/self/maps").read_text().splitlines()
        loaded = sorted(
            {
                line.split()[-1]
                for line in maps
                if "/" in line and "codec2" in line.split()[-1]
            }
        )
        return {
            "mode": mode,
            "mode_name": MODES[mode],
            "corpus": corpus,
            "machine": platform.machine(),
            "python_version": platform.python_version(),
            "frames": frames,
            "samples_per_frame": n,
            "bits_per_frame": bits,
            "input_sha256": hashlib.sha256(
                struct.pack("<" + "h" * len(source), *source)
            ).hexdigest(),
            "encoded_sha256": hashlib.sha256(encoded).hexdigest(),
            "encoded_hex": encoded.hex(),
            "decoded_sha256": hashlib.sha256(decoded).hexdigest(),
            "decoded_bytes": len(decoded),
            "library_sha256": hashlib.sha256(library.read_bytes()).hexdigest(),
            "loaded_codec_paths": loaded,
            "loaded_codec_hashes": {
                p: hashlib.sha256(Path(p).read_bytes()).hexdigest() for p in loaded
            },
        }
    finally:
        lib.codec2_destroy(encoder)
        lib.codec2_destroy(decoder)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--library", required=True, type=Path)
    parser.add_argument("--mode", required=True, type=int, choices=MODES)
    parser.add_argument("--corpus", required=True, choices=CORPORA)
    parser.add_argument("--decode-hex")
    args = parser.parse_args()
    result = run_case(
        args.library,
        args.mode,
        args.corpus,
        bytes.fromhex(args.decode_hex) if args.decode_hex is not None else None,
    )
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
