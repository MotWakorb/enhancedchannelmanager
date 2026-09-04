"""The runtime image ships the intentionally narrow resdet build."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]


def test_resdet_image_build_is_y4m_only_and_copies_all_required_notices():
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")

    assert "--disable-everything" in dockerfile
    assert "--omit-pgm-reader" in dockerfile
    assert "--omit-pfm-reader" in dockerfile
    assert "--disable-ffmpeg" in dockerfile
    assert "Built with image readers: Y4M" in dockerfile
    assert "/tmp/resdet/lib/kissfft/COPYING" in dockerfile
    assert "/tmp/resdet/lib/kissfft/LICENSES/BSD-3-Clause" in dockerfile
