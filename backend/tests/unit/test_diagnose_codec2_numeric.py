"""Numerical investigations cannot alter an acceptance recipe or golden."""

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture
def diagnostic(monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / "scripts"))
    spec = importlib.util.spec_from_file_location(
        "diagnose_codec2_numeric", ROOT / "scripts/diagnose_codec2_numeric.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_variants_are_bounded_and_do_not_mutate_the_original_source(
    diagnostic, tmp_path
):
    source = tmp_path / "source"
    source.mkdir()
    original = b"target_compile_definitions(codec2 PRIVATE\n USE_GLIBC_ABI=0 sincosf=codec2_sincosf sinf=codec2_sinf\n cosf=codec2_cosf atan2f=codec2_atan2f log10f=codec2_log10f)\ntarget_compile_options(codec2 PRIVATE -ffp-contract=off)\n"
    (source / "CMakeLists.txt").write_bytes(original)
    (source / "defines.h").write_text(
        "#define POW10F(x) ((float)pow(10.0, (double)(float)(x)))\n"
    )
    diagnostic.variant_source(source, tmp_path / "variant", "fast", "native", "expf")
    assert (source / "CMakeLists.txt").read_bytes() == original
    text = (tmp_path / "variant/CMakeLists.txt").read_text()
    assert "-ffp-contract=fast" in text and "log10f=codec2_log10f" not in text
    assert "sincosf=codec2_sincosf" in text
    assert "expf(" in (tmp_path / "variant/defines.h").read_text()


def test_unknown_source_layout_is_denied_not_silently_unmodified(diagnostic, tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "CMakeLists.txt").write_text("unexpected source")
    (source / "defines.h").write_text("unexpected macro")
    with pytest.raises(diagnostic.MatrixError, match="source witness"):
        diagnostic.variant_source(
            source, tmp_path / "variant", "fast", "native", "expf"
        )
