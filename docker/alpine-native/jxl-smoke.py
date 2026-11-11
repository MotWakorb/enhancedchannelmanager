"""Exercise real JXL decoding, JPEG reconstruction and the threads provider."""

import argparse
import ctypes as C
import hashlib
import json
from pathlib import Path


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def bind(library, name, args, result=C.c_int):
    function = getattr(library, name)
    function.argtypes, function.restype = args, result
    return function


class PixelFormat(C.Structure):
    _fields_ = [
        ("channels", C.c_uint32),
        ("type", C.c_int),
        ("endian", C.c_int),
        ("align", C.c_size_t),
    ]


def decode(library, data, *, reconstruct=False):
    create = bind(library, "JxlDecoderCreate", [C.c_void_p], C.c_void_p)
    destroy = bind(library, "JxlDecoderDestroy", [C.c_void_p], None)
    subscribe = bind(library, "JxlDecoderSubscribeEvents", [C.c_void_p, C.c_int])
    set_input = bind(
        library, "JxlDecoderSetInput", [C.c_void_p, C.c_void_p, C.c_size_t]
    )
    close = bind(library, "JxlDecoderCloseInput", [C.c_void_p], None)
    process = bind(library, "JxlDecoderProcessInput", [C.c_void_p])
    get_size = bind(
        library,
        "JxlDecoderImageOutBufferSize",
        [C.c_void_p, C.POINTER(PixelFormat), C.POINTER(C.c_size_t)],
    )
    set_output = bind(
        library,
        "JxlDecoderSetImageOutBuffer",
        [C.c_void_p, C.POINTER(PixelFormat), C.c_void_p, C.c_size_t],
    )
    set_jpeg = bind(
        library, "JxlDecoderSetJPEGBuffer", [C.c_void_p, C.c_void_p, C.c_size_t]
    )
    release_jpeg = bind(
        library, "JxlDecoderReleaseJPEGBuffer", [C.c_void_p], C.c_size_t
    )
    decoder = create(None)
    require(decoder, "JXL decoder unavailable")
    source = C.create_string_buffer(data)
    pixels, jpeg = None, None
    fmt = PixelFormat(3, 2, 0, 0)  # JXL_TYPE_UINT8=2; zero is FLOAT.
    try:
        require(
            subscribe(decoder, 0x40 | 0x100 | 0x1000 | 0x2000) == 0, "decoder events"
        )
        require(set_input(decoder, source, len(data)) == 0, "decoder input")
        close(decoder)
        for _ in range(32):
            event = process(decoder)
            if event == 5:
                size = C.c_size_t()
                require(
                    get_size(decoder, C.byref(fmt), C.byref(size)) == 0
                    and 0 < size.value <= 1024 * 1024,
                    "bounded pixel output",
                )
                pixels = C.create_string_buffer(size.value)
                require(
                    set_output(decoder, C.byref(fmt), pixels, size.value) == 0,
                    "decoder pixel buffer",
                )
            elif event == 0x2000:
                require(reconstruct, "unexpected JPEG reconstruction")
                jpeg = C.create_string_buffer(1024 * 1024)
                require(set_jpeg(decoder, jpeg, len(jpeg)) == 0, "JPEG output buffer")
            elif event == 0:
                require(pixels is not None or reconstruct, "decoded pixels missing")
                if reconstruct:
                    require(jpeg is not None, "JPEG reconstruction feature missing")
                    unused = release_jpeg(decoder)
                    require(unused <= len(jpeg), "JPEG output length")
                    return jpeg.raw[: len(jpeg) - unused]
                return pixels.raw
            else:
                require(
                    event not in {1, 2, 6},
                    "decoder failure or incomplete/oversized output",
                )
        raise RuntimeError("decoder exceeded bounded event count")
    finally:
        destroy(decoder)


def encode_jpeg(library, data):
    encoder = bind(library, "JxlEncoderCreate", [C.c_void_p], C.c_void_p)(None)
    require(encoder, "JXL encoder unavailable")
    destroy = bind(library, "JxlEncoderDestroy", [C.c_void_p], None)
    try:
        require(
            bind(library, "JxlEncoderUseContainer", [C.c_void_p, C.c_int])(encoder, 1)
            == 0,
            "JXL container support",
        )
        require(
            bind(library, "JxlEncoderStoreJPEGMetadata", [C.c_void_p, C.c_int])(
                encoder, 1
            )
            == 0,
            "JPEG metadata support",
        )
        settings = bind(
            library,
            "JxlEncoderFrameSettingsCreate",
            [C.c_void_p, C.c_void_p],
            C.c_void_p,
        )(encoder, None)
        require(settings, "encoder settings unavailable")
        source = C.create_string_buffer(data)
        require(
            bind(
                library, "JxlEncoderAddJPEGFrame", [C.c_void_p, C.c_void_p, C.c_size_t]
            )(settings, source, len(data))
            == 0,
            "JPEG transcoding support",
        )
        bind(library, "JxlEncoderCloseInput", [C.c_void_p], None)(encoder)
        output = C.create_string_buffer(1024 * 1024)
        pointer = C.cast(output, C.POINTER(C.c_ubyte))
        available = C.c_size_t(len(output))
        status = bind(
            library,
            "JxlEncoderProcessOutput",
            [C.c_void_p, C.POINTER(C.POINTER(C.c_ubyte)), C.POINTER(C.c_size_t)],
        )(encoder, C.byref(pointer), C.byref(available))
        require(
            status == 0 and available.value < len(output),
            "bounded complete encoded output",
        )
        return output.raw[: len(output) - available.value]
    finally:
        destroy(encoder)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--library", type=Path, required=True)
    args = parser.parse_args()
    library = C.CDLL(str(args.library.resolve()))
    versions = {
        name: bind(library, name, [], C.c_uint32)()
        for name in ["JxlDecoderVersion", "JxlEncoderVersion"]
    }
    require(
        set(versions.values()) == {12000},
        "actual JXL API version differs from baseline",
    )
    assets = Path(__file__).parent
    pixels = decode(library, (assets / "jxl-probe.jxl").read_bytes())
    rgb_sha = hashlib.sha256(pixels).hexdigest()
    require(
        len(pixels) == 768
        and rgb_sha
        == "a0d99fe349fe48d113c16adf5c3f582b70dbfb3cf5fa8bd0e59e0833b78c2a93",
        "baseline RGB24 output mismatch",
    )
    original = (assets / "jxl-transcode.jpg").read_bytes()
    reconstructed = decode(library, encode_jpeg(library, original), reconstruct=True)
    require(original == reconstructed, "JPEG reconstruction differs")
    threads = C.CDLL(str(args.library.parent / "libjxl_threads.so.0.12"))
    runner = bind(
        threads, "JxlThreadParallelRunnerCreate", [C.c_void_p, C.c_size_t], C.c_void_p
    )(None, 1)
    require(runner, "native thread runner unavailable")
    bind(threads, "JxlThreadParallelRunnerDestroy", [C.c_void_p], None)(runner)
    print(
        json.dumps(
            {
                "versions": versions,
                "rgb_sha256": rgb_sha,
                "jpeg_reconstruction_exact": True,
                "native_thread_runner": True,
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
