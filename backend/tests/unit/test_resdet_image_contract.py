"""The runtime image ships the intentionally narrow resdet build."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]


def test_resdet_image_build_is_y4m_only_and_copies_all_required_notices():
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")

    assert "--disable-everything" in dockerfile
    assert "--omit-pgm-reader" in dockerfile
    assert "--omit-pfm-reader" in dockerfile
    assert "--disable-ffmpeg" in dockerfile
    manifest = (ROOT / "sbom" / "native-dependencies.json").read_text(encoding="utf-8")
    assert '"pixelMax": 8847360' in manifest
    assert '--pixel-max="$RESDET_PIXEL_MAX"' in dockerfile
    assert "$RESDET_Y4M_LIMIT_MULTIPLIER,unsigned char" in dockerfile
    assert 's/^PIXEL_MAX=//p' in dockerfile
    assert "Built with image readers: Y4M" in dockerfile
    assert "ARG RESDET_COMMIT" not in dockerfile
    assert "ARG RESDET_SHA256" not in dockerfile
    assert "native-dependencies.json" in dockerfile
    assert "/tmp/resdet/lib/kissfft/COPYING" in dockerfile
    assert "/tmp/resdet/lib/kissfft/LICENSES/BSD-3-Clause" in dockerfile
