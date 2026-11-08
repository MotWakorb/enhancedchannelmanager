"""Recorded actual FFmpeg capability and complete frame-output identities."""

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture
def verifier():
    spec = importlib.util.spec_from_file_location(
        "verify_alpine_ffmpeg", ROOT / "scripts/verify_alpine_ffmpeg.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_reference_covers_all_five_real_inputs_and_six_complete_listings(verifier):
    reference = verifier.load_reference()
    assert set(reference["frames"]) == {
        "synthetic.c2",
        "synthetic.jxl",
        "synthetic.mod",
        "synthetic.svg",
        "synthetic.vgm",
    }
    assert set(reference["capabilities"]) == {
        "encoders",
        "decoders",
        "demuxers",
        "muxers",
        "filters",
        "protocols",
    }
    assert (
        reference["frames"]["synthetic.jxl"]
        == "522ca5a2926e8622b539ef51509b243fa9f652b4d72837f7263ca8abf79a381e"
    )
    verifier.validate_hashes(reference["frames"], reference["frames"], "frames")
    for bad in [
        {},
        {
            **reference["frames"],
            "synthetic.jxl": "b139e5e568dd113dd082330c3727b1fc731d8ac40118dc6c02b4faa0cfa95fc7",
        },
    ]:
        with pytest.raises(verifier.VerificationError, match="frames"):
            verifier.validate_hashes(bad, reference["frames"], "frames")
