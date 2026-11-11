"""Inputs for real paired codec execution must not depend on libc float math."""

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture
def matrix():
    spec = importlib.util.spec_from_file_location(
        "alpine_codec_matrix", ROOT / "scripts/alpine_codec_matrix.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_all_seven_existing_codec2_modes_and_integer_corpora_are_exercised(matrix):
    assert set(matrix.MODES) == {0, 1, 2, 3, 4, 5, 8}
    assert set(matrix.CORPORA) == {"silence", "saw", "impulse", "noise", "alternating"}
    for corpus in matrix.CORPORA:
        first = matrix.samples(corpus, 160 * 16)
        assert first == matrix.samples(corpus, 160 * 16)
        assert len(first) == 160 * 16 and all(
            type(v) is int and -32768 <= v <= 32767 for v in first
        )
    assert matrix.samples("silence", 320) == [0] * 320
    assert matrix.samples("impulse", 320).count(24000) == 2


def test_unknown_corpus_is_not_silently_a_different_input(matrix):
    with pytest.raises(ValueError):
        matrix.samples("unknown", 160)
