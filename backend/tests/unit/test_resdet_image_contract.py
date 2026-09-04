"""The runtime image ships the intentionally narrow resdet build."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]


def test_resdet_image_build_is_y4m_only_and_copies_all_required_notices():
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    build_script = (ROOT / "scripts" / "build_resdet.py").read_text(encoding="utf-8")

    assert "--disable-everything" in build_script
    assert "--omit-pgm-reader" in build_script
    assert "--omit-pfm-reader" in build_script
    assert "--disable-ffmpeg" in build_script
    manifest = (ROOT / "sbom" / "native-dependencies.json").read_text(encoding="utf-8")
    assert '"pixelMax": 8847360' in manifest
    assert 'f"--pixel-max={build_metadata[\'pixelMax\']}"' in build_script
    assert "UPSTREAM_Y4M_LIMIT" in build_script
    assert "Built with image readers: Y4M" in dockerfile
    assert "resdet version 2.4.3" in dockerfile
    assert "libresdet version 3.2.0" in dockerfile
    assert "pkg-config" in dockerfile
    assert "ARG RESDET_COMMIT" not in dockerfile
    assert "ARG RESDET_SHA256" not in dockerfile
    assert "native-dependencies.json" in dockerfile
    assert "/tmp/resdet-output/licenses/" in dockerfile


def test_runtime_lock_directory_is_prepared_for_the_final_app_identity():
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    entrypoint = (ROOT / "backend" / "entrypoint.sh").read_text(encoding="utf-8")
    assert "/run/ecm" in dockerfile
    assert "chown appuser:appuser /run/ecm" in dockerfile
    assert "chmod 700 /run/ecm" in dockerfile
    assert "chown appuser:appuser /run/ecm" in entrypoint
    assert "chmod 700 /run/ecm" in entrypoint
