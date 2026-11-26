"""The candidate cannot silently build a different application from a PR merge."""

import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "scripts/verify_alpine_application_source.py"
VERSION_TOUCHPOINTS = (
    "backend/main.py",
    "backend/routers/backup.py",
    "frontend/package.json",
)


@pytest.fixture
def verifier():
    spec = importlib.util.spec_from_file_location(
        "verify_alpine_application_source",
        ROOT / "scripts/verify_alpine_application_source.py",
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_version_bump_source_preflight_accepts_the_complete_committed_tree(tmp_path):
    """Exercise the same pinned-manifest evidence producer as native CI."""
    output = tmp_path / "application-source.json"
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--output-file", str(output)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    record = json.loads(output.read_text())
    manifest_path = ROOT / "docker/alpine-native/application-source.json"
    manifest = json.loads(manifest_path.read_text())
    assert record["status"] == "PINNED_APPLICATION_SOURCE_ONLY"
    assert record["file_count"] == len(manifest["files"])
    assert (
        record["manifest_sha256"]
        == hashlib.sha256(manifest_path.read_bytes()).hexdigest()
    )


@pytest.mark.parametrize("relative", VERSION_TOUCHPOINTS)
def test_version_bump_source_preflight_rejects_each_stale_touchpoint(
    verifier, tmp_path, relative
):
    """Version declarations remain exact source bytes, never exempt metadata."""
    manifest = verifier.load_manifest(
        ROOT / "docker/alpine-native/application-source.json"
    )
    source_root = tmp_path / "source"
    for row in manifest["files"]:
        source = ROOT / row["path"]
        target = source_root / row["path"]
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        target.chmod(source.stat().st_mode & 0o777)
    version = json.loads((ROOT / "frontend/package.json").read_text())["version"]
    release, separator, build = version.rpartition("-")
    stale = (
        f"{release}-{int(build) - 1:04d}"
        if separator and build.isdigit() and int(build) > 0
        else "0.0.0-0000"
    )
    assert stale != version
    path = source_root / relative
    original = path.read_bytes()
    assert original.count(version.encode()) == 1
    path.write_bytes(original.replace(version.encode(), stale.encode()))
    output = tmp_path / "application-source.json"
    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--root",
            str(source_root),
            "--output-file",
            str(output),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode != 0
    assert "application source bytes or executable bit differ: " + relative in (
        result.stderr
    )
    assert not output.exists()


def test_real_recorded_application_map_retains_date_and_container_adaptations(
    verifier,
):
    manifest = verifier.load_manifest(
        ROOT / "docker/alpine-native/application-source.json"
    )
    assert len(manifest["files"]) == 1097
    assert (
        manifest["baseline_application_commit"]
        == "06cc4bf5a1b9eb1f6ef29f617513d237cda2f531"
    )
    changed = [r for r in manifest["files"] if "adaptation_commit" in r]
    assert len(changed) == 1 and changed[0]["path"] == "backend/date_placeholders.py"
    assert changed[0]["adaptation_commit"] == "df80c49d96d33f6747d5610e98bea732a8618c30"
    assert all(
        r["path"] != "backend/requirements.txt" or "adaptation_commit" not in r
        for r in manifest["files"]
    )
    result = verifier.verify(ROOT, manifest)
    assert {r["path"] for r in result["adaptations"]} == {
        "backend/date_placeholders.py",
        "mcp-server/Dockerfile",
    }


PENDING_MERGE_PREVIEW_FILES = (
    "backend/channel_pipeline_engine.py",
    "backend/channel_pipeline_executor.py",
    "backend/routers/channel_pipeline.py",
    "backend/services/m3u_dedup_hook.py",
    "frontend/src/components/channelPipeline/ChannelPipelineTab.test.tsx",
    "frontend/src/components/channelPipeline/ChannelPipelineTab.tsx",
    "frontend/src/test/mocks/handlers.ts",
    "frontend/src/types/channelPipeline.ts",
)


MCP_TOOLS_HEADING_FILES = (
    "frontend/src/components/settings/MCPSettingsSection.test.tsx",
    "frontend/src/components/settings/MCPSettingsSection.tsx",
    "frontend/src/components/settings/mcpToolCategories.ts",
)


M3U_EMPTY_STATE_FILES = (
    "frontend/src/components/M3ULinkedAccountsModal.tsx",
    "frontend/src/components/TaskEditorModal.tsx",
    "frontend/src/components/tabs/M3UManagerTab.test.tsx",
    "frontend/src/components/tabs/M3UManagerTab.tsx",
    "frontend/src/components/tabs/StatsTab.tsx",
)
M3U_EMPTY_STATE_ADDED_FILES = (
    "frontend/src/utils/m3uAccountVisibility.test.ts",
    "frontend/src/utils/m3uAccountVisibility.ts",
)


SEARCH_RELOAD_MOUNT_FILES = ("frontend/src/App.tsx",)
SEARCH_RELOAD_MOUNT_ADDED_FILES = ("frontend/src/App.searchReload.test.tsx",)


EXPLICIT_PROFILE_SELECTION_FILES = (
    "frontend/src/App.tsx",
    "frontend/src/components/StreamsPane.tsx",
    "frontend/src/components/tabs/ChannelManagerTab.tsx",
)
EXPLICIT_PROFILE_SELECTION_ADDED_FILES = (
    "frontend/src/App.createProfileSelection.test.tsx",
)


def _before_explicit_profile_selection(manifest):
    key = "explicit_profile_selection_update"
    update = manifest[key]
    assert update["base_commit"] == "2d33a086d5b86dcbf6a1b7ba192184b59d1ef20b"
    assert update["bead"] == "enhancedchannelmanager-0m06f.3.4.2"
    assert update["version"] == "0.18.2-0083"
    assert update["previous_manifest_sha256"] == (
        "a3fa9c5672c7fca1ca83e3df0a668092a18e70d38b4f9b85f1af51b2a14c842a"
    )
    assert set(update["added_files"]) == set(EXPLICIT_PROFILE_SELECTION_ADDED_FILES)
    assert set(update["changed_files"]) == (
        set(VERSION_TOUCHPOINTS) | set(EXPLICIT_PROFILE_SELECTION_FILES)
    )
    previous = {name: value for name, value in manifest.items() if name != key}
    previous["files"] = []
    for row in manifest["files"]:
        if row["path"] in update["added_files"]:
            assert row["type"] == "file" and row["executable"] is False
            continue
        prior_sha256 = update["changed_files"].get(row["path"])
        if prior_sha256 is not None:
            assert prior_sha256 != row["sha256"]
            row = {**row, "sha256": prior_sha256}
        previous["files"].append(row)
    assert (
        hashlib.sha256((json.dumps(previous, indent=2) + "\n").encode()).hexdigest()
        == update["previous_manifest_sha256"]
    )
    return previous


def test_explicit_profile_selection_update_preserves_exact_prior_manifest(verifier):
    manifest = verifier.load_manifest(
        ROOT / "docker/alpine-native/application-source.json"
    )
    _before_explicit_profile_selection(manifest)
    result = verifier.verify(ROOT, manifest)
    assert (
        result["explicit_profile_selection_update"]
        == manifest["explicit_profile_selection_update"]
    )


DUAL_FAILURE_ALERT_FILES = ("backend/task_engine.py",)


def _before_dual_failure_alert(manifest):
    key = "dual_failure_alert_update"
    manifest = _before_explicit_profile_selection(manifest)
    update = manifest[key]
    assert update["base_commit"] == "56ee16259892e9e0a8cd988c4ec9e77c903735b9"
    assert update["bead"] == "enhancedchannelmanager-75j5q"
    assert update["version"] == "0.18.2-0082"
    assert update["previous_manifest_sha256"] == (
        "e030c75249276dbcf499991c338a3e34ad88e252108a823bdf7b0573fcb7fd0f"
    )
    assert set(update["changed_files"]) == (
        set(VERSION_TOUCHPOINTS) | set(DUAL_FAILURE_ALERT_FILES)
    )
    previous = {name: value for name, value in manifest.items() if name != key}
    previous["files"] = []
    for row in manifest["files"]:
        prior_sha256 = update["changed_files"].get(row["path"])
        if prior_sha256 is not None:
            assert prior_sha256 != row["sha256"]
            row = {**row, "sha256": prior_sha256}
        previous["files"].append(row)
    assert (
        hashlib.sha256((json.dumps(previous, indent=2) + "\n").encode()).hexdigest()
        == update["previous_manifest_sha256"]
    )
    return previous


def test_dual_failure_alert_update_preserves_exact_prior_manifest(verifier):
    manifest = verifier.load_manifest(
        ROOT / "docker/alpine-native/application-source.json"
    )
    _before_dual_failure_alert(manifest)
    result = verifier.verify(ROOT, manifest)
    assert result["dual_failure_alert_update"] == manifest["dual_failure_alert_update"]


def _before_search_reload_mount(manifest):
    key = "search_reload_mount_update"
    manifest = _before_dual_failure_alert(manifest)
    update = manifest[key]
    assert update["base_commit"] == "e0f7781da6e9b870f8317d9c544a3532600ce0c2"
    assert update["bead"] == "enhancedchannelmanager-bv8n7"
    assert update["version"] == "0.18.2-0081"
    assert update["previous_manifest_sha256"] == (
        "7bb86c07f81f057e0a55b9d8de2da275cc75bd5caecddfb33ad26deedd32493c"
    )
    assert set(update["added_files"]) == set(SEARCH_RELOAD_MOUNT_ADDED_FILES)
    assert set(update["changed_files"]) == (
        set(VERSION_TOUCHPOINTS) | set(SEARCH_RELOAD_MOUNT_FILES)
    )
    previous = {name: value for name, value in manifest.items() if name != key}
    previous["files"] = []
    for row in manifest["files"]:
        if row["path"] in update["added_files"]:
            assert row["type"] == "file" and row["executable"] is False
            continue
        prior_sha256 = update["changed_files"].get(row["path"])
        if prior_sha256 is not None:
            assert prior_sha256 != row["sha256"]
            row = {**row, "sha256": prior_sha256}
        previous["files"].append(row)
    assert (
        hashlib.sha256((json.dumps(previous, indent=2) + "\n").encode()).hexdigest()
        == update["previous_manifest_sha256"]
    )
    return previous


def test_search_reload_mount_update_preserves_exact_prior_manifest(verifier):
    manifest = verifier.load_manifest(
        ROOT / "docker/alpine-native/application-source.json"
    )
    _before_search_reload_mount(manifest)
    result = verifier.verify(ROOT, manifest)
    assert result["search_reload_mount_update"] == manifest["search_reload_mount_update"]


def _before_publish_concurrency(manifest):
    key = "publish_concurrency_update"
    manifest = _before_search_reload_mount(manifest)
    update = manifest[key]
    assert update["base_commit"] == "2b63af8eed785b231c93ad6d6aedd3eb38991779"
    assert update["bead"] == "enhancedchannelmanager-9iagf"
    assert update["version"] == "0.18.2-0080"
    assert update["previous_manifest_sha256"] == (
        "f23c27043d94de56587deeebcfdbe5bf7c1265bc2cc34513825f96f60f84c513"
    )
    assert set(update["changed_files"]) == set(VERSION_TOUCHPOINTS)
    previous = {name: value for name, value in manifest.items() if name != key}
    previous["files"] = []
    for row in manifest["files"]:
        prior_sha256 = update["changed_files"].get(row["path"])
        if prior_sha256 is not None:
            assert prior_sha256 != row["sha256"]
            row = {**row, "sha256": prior_sha256}
        previous["files"].append(row)
    assert (
        hashlib.sha256((json.dumps(previous, indent=2) + "\n").encode()).hexdigest()
        == update["previous_manifest_sha256"]
    )
    return previous


def test_publish_concurrency_update_preserves_exact_prior_manifest(verifier):
    manifest = verifier.load_manifest(
        ROOT / "docker/alpine-native/application-source.json"
    )
    _before_publish_concurrency(manifest)
    result = verifier.verify(ROOT, manifest)
    assert result["publish_concurrency_update"] == manifest["publish_concurrency_update"]


def _before_m3u_empty_state(manifest):
    key = "m3u_empty_state_update"
    manifest = _before_publish_concurrency(manifest)
    update = manifest[key]
    assert update["base_commit"] == "04587fc8033e2a7334529796819cdc282afde347"
    assert update["bead"] == "enhancedchannelmanager-381gu"
    assert update["version"] == "0.18.2-0079"
    assert update["previous_manifest_sha256"] == (
        "72d41b6fc5c9509f276a5204761df454a1a4eecfff97d99dfc275d8c1b4ec453"
    )
    assert set(update["added_files"]) == set(M3U_EMPTY_STATE_ADDED_FILES)
    assert set(update["changed_files"]) == (
        set(VERSION_TOUCHPOINTS) | set(M3U_EMPTY_STATE_FILES)
    )
    previous = {name: value for name, value in manifest.items() if name != key}
    previous["files"] = []
    for row in manifest["files"]:
        if row["path"] in update["added_files"]:
            assert row["type"] == "file" and row["executable"] is False
            continue
        prior_sha256 = update["changed_files"].get(row["path"])
        if prior_sha256 is not None:
            assert prior_sha256 != row["sha256"]
            row = {**row, "sha256": prior_sha256}
        previous["files"].append(row)
    assert (
        hashlib.sha256((json.dumps(previous, indent=2) + "\n").encode()).hexdigest()
        == update["previous_manifest_sha256"]
    )
    return previous


def test_m3u_empty_state_update_preserves_exact_prior_manifest(verifier):
    manifest = verifier.load_manifest(
        ROOT / "docker/alpine-native/application-source.json"
    )
    _before_m3u_empty_state(manifest)
    result = verifier.verify(ROOT, manifest)
    assert result["m3u_empty_state_update"] == manifest["m3u_empty_state_update"]


def _before_mcp_tools_heading(manifest):
    key = "mcp_tools_heading_update"
    manifest = _before_m3u_empty_state(manifest)
    update = manifest[key]
    assert update["base_commit"] == "9744f7637945d11dbf7412db3e80f8e4f5292368"
    assert update["bead"] == "enhancedchannelmanager-xkk54"
    assert update["version"] == "0.18.2-0078"
    assert update["previous_manifest_sha256"] == (
        "ffdc3ae7983cd30e7ab326dd4448d9ba51269934753e415298dc86406316a2dc"
    )
    assert set(update["changed_files"]) == (
        set(VERSION_TOUCHPOINTS) | set(MCP_TOOLS_HEADING_FILES)
    )
    previous = {name: value for name, value in manifest.items() if name != key}
    previous["files"] = []
    for row in manifest["files"]:
        prior_sha256 = update["changed_files"].get(row["path"])
        if prior_sha256 is not None:
            assert prior_sha256 != row["sha256"]
            row = {**row, "sha256": prior_sha256}
        previous["files"].append(row)
    assert (
        hashlib.sha256((json.dumps(previous, indent=2) + "\n").encode()).hexdigest()
        == update["previous_manifest_sha256"]
    )
    return previous


def test_mcp_tools_heading_update_preserves_exact_prior_manifest(verifier):
    manifest = verifier.load_manifest(
        ROOT / "docker/alpine-native/application-source.json"
    )
    _before_mcp_tools_heading(manifest)
    result = verifier.verify(ROOT, manifest)
    assert result["mcp_tools_heading_update"] == manifest["mcp_tools_heading_update"]


def _before_smtp_html_escape(manifest):
    key = "smtp_html_escape_update"
    manifest = _before_mcp_tools_heading(manifest)
    update = manifest[key]
    assert update["base_commit"] == "69c31168b4d77d92f9d526e65fbf8913b1314ade"
    assert update["bead"] == "enhancedchannelmanager-98k3b"
    assert update["version"] == "0.18.2-0077"
    assert update["previous_manifest_sha256"] == (
        "8792a518b7f7cb097befa0ff3dd263e055c731893ba9362caa5bc89164455fa3"
    )
    assert set(update["changed_files"]) == (
        set(VERSION_TOUCHPOINTS) | {"backend/alert_methods_smtp.py"}
    )
    previous = {name: value for name, value in manifest.items() if name != key}
    previous["files"] = []
    for row in manifest["files"]:
        prior_sha256 = update["changed_files"].get(row["path"])
        if prior_sha256 is not None:
            assert prior_sha256 != row["sha256"]
            row = {**row, "sha256": prior_sha256}
        previous["files"].append(row)
    assert (
        hashlib.sha256((json.dumps(previous, indent=2) + "\n").encode()).hexdigest()
        == update["previous_manifest_sha256"]
    )
    return previous


def test_smtp_html_escape_update_preserves_exact_prior_manifest(verifier):
    manifest = verifier.load_manifest(
        ROOT / "docker/alpine-native/application-source.json"
    )
    _before_smtp_html_escape(manifest)
    result = verifier.verify(ROOT, manifest)
    assert result["smtp_html_escape_update"] == manifest["smtp_html_escape_update"]


def _before_telegram_html_escape(manifest):
    key = "telegram_html_escape_update"
    manifest = _before_smtp_html_escape(manifest)
    update = manifest[key]
    assert update["base_commit"] == "494697b69b4f5b862e874029dbfbc040bb0214be"
    assert update["bead"] == "enhancedchannelmanager-j28qs"
    assert update["version"] == "0.18.2-0076"
    assert update["previous_manifest_sha256"] == (
        "d2f75cc8ef5015eaaf60070469bfc1ffc172452763db7a7bda3b816b720fe7dc"
    )
    assert set(update["changed_files"]) == (
        set(VERSION_TOUCHPOINTS) | {"backend/services/notification_service.py"}
    )
    previous = {name: value for name, value in manifest.items() if name != key}
    previous["files"] = []
    for row in manifest["files"]:
        prior_sha256 = update["changed_files"].get(row["path"])
        if prior_sha256 is not None:
            assert prior_sha256 != row["sha256"]
            row = {**row, "sha256": prior_sha256}
        previous["files"].append(row)
    assert (
        hashlib.sha256((json.dumps(previous, indent=2) + "\n").encode()).hexdigest()
        == update["previous_manifest_sha256"]
    )
    return previous


def test_telegram_html_escape_update_preserves_exact_prior_manifest(verifier):
    manifest = verifier.load_manifest(
        ROOT / "docker/alpine-native/application-source.json"
    )
    _before_telegram_html_escape(manifest)
    result = verifier.verify(ROOT, manifest)
    assert result["telegram_html_escape_update"] == manifest["telegram_html_escape_update"]


def _before_pending_merge_preview(manifest):
    key = "pending_merge_preview_update"
    manifest = _before_telegram_html_escape(manifest)
    update = manifest[key]
    assert update["base_commit"] == "ed610cced3c0a8e0b36b80adc88b91a57c534bc2"
    assert update["bead"] == "enhancedchannelmanager-rtst2.13"
    assert update["version"] == "0.18.2-0075"
    assert update["previous_manifest_sha256"] == (
        "e37d92a70050109cde870320c2e389ad2a70d951b00b1833893c8969edfd9070"
    )
    assert set(update["changed_files"]) == (
        set(VERSION_TOUCHPOINTS) | set(PENDING_MERGE_PREVIEW_FILES)
    )
    previous = {name: value for name, value in manifest.items() if name != key}
    previous["files"] = []
    for row in manifest["files"]:
        prior_sha256 = update["changed_files"].get(row["path"])
        if prior_sha256 is not None:
            assert prior_sha256 != row["sha256"]
            row = {**row, "sha256": prior_sha256}
        previous["files"].append(row)
    assert (
        hashlib.sha256((json.dumps(previous, indent=2) + "\n").encode()).hexdigest()
        == update["previous_manifest_sha256"]
    )
    return previous


def test_pending_merge_preview_update_preserves_exact_prior_manifest(verifier):
    manifest = verifier.load_manifest(
        ROOT / "docker/alpine-native/application-source.json"
    )
    _before_pending_merge_preview(manifest)
    result = verifier.verify(ROOT, manifest)
    assert result["pending_merge_preview_update"] == manifest["pending_merge_preview_update"]


def _before_owned_evidence_mode(manifest):
    key = "owned_evidence_mode_update"
    manifest = _before_pending_merge_preview(manifest)
    update = manifest[key]
    assert update["base_commit"] == "43a821c107a556aad117e2ff6af7d87646cf2e07"
    assert update["bead"] == "enhancedchannelmanager-7fzq0"
    assert update["version"] == "0.18.2-0074"
    assert update["previous_manifest_sha256"] == (
        "cd0ca696817566b4281c9c41e6016d43c64db4c9a1f5fd45173596a6de5757b1"
    )
    assert set(update["changed_files"]) == set(VERSION_TOUCHPOINTS)
    previous = {name: value for name, value in manifest.items() if name != key}
    previous["files"] = []
    for row in manifest["files"]:
        prior_sha256 = update["changed_files"].get(row["path"])
        if prior_sha256 is not None:
            assert prior_sha256 != row["sha256"]
            row = {**row, "sha256": prior_sha256}
        previous["files"].append(row)
    assert (
        hashlib.sha256((json.dumps(previous, indent=2) + "\n").encode()).hexdigest()
        == update["previous_manifest_sha256"]
    )
    return previous


def test_owned_evidence_mode_update_preserves_exact_prior_manifest(verifier):
    manifest = verifier.load_manifest(
        ROOT / "docker/alpine-native/application-source.json"
    )
    _before_owned_evidence_mode(manifest)
    result = verifier.verify(ROOT, manifest)
    assert result["owned_evidence_mode_update"] == manifest["owned_evidence_mode_update"]


def _before_vitest_mocker_security(manifest):
    key = "vitest_mocker_security_update"
    manifest = _before_owned_evidence_mode(manifest)
    update = manifest[key]
    assert update["base_commit"] == "5694644dba3f05715ed554dbe42a4b00a22af3d0"
    assert update["bead"] == "enhancedchannelmanager-wgf0b"
    assert update["version"] == "0.18.2-0073"
    assert update["previous_manifest_sha256"] == (
        "000b5340fd654920bf9a92876818edf6907829b3fc5edd890e7c3f9561614301"
    )
    assert set(update["changed_files"]) == {
        *VERSION_TOUCHPOINTS,
        "frontend/package-lock.json",
    }
    previous = {name: value for name, value in manifest.items() if name != key}
    previous["files"] = []
    for row in manifest["files"]:
        prior_sha256 = update["changed_files"].get(row["path"])
        if prior_sha256 is not None:
            assert prior_sha256 != row["sha256"]
            row = {**row, "sha256": prior_sha256}
        previous["files"].append(row)
    assert (
        hashlib.sha256((json.dumps(previous, indent=2) + "\n").encode()).hexdigest()
        == update["previous_manifest_sha256"]
    )
    return previous


def test_vitest_mocker_update_preserves_exact_prior_manifest(verifier):
    manifest = verifier.load_manifest(
        ROOT / "docker/alpine-native/application-source.json"
    )
    _before_vitest_mocker_security(manifest)
    result = verifier.verify(ROOT, manifest)
    assert (
        result["vitest_mocker_security_update"] == manifest["vitest_mocker_security_update"]
    )


def _before_postcreate_profile_number_retry(manifest):
    manifest = _before_vitest_mocker_security(manifest)
    key = "postcreate_profile_number_retry_update"
    update = manifest[key]
    assert update["base_commit"] == "d09d386a749e21163639eb53ccff56564312f002"
    assert update["bead"] == "enhancedchannelmanager-0m06f.3.4.1"
    assert update["version"] == "0.18.2-0072"
    assert update["previous_manifest_sha256"] == "21d359fae93655f3c97a9ba59e5ee0e064110179c191ef26c0ad3355381ce065"
    assert set(update["changed_files"]) == {
        "backend/routers/channels.py", "frontend/src/App.bulkCreateStaging.test.tsx",
        "frontend/src/hooks/useEditMode.ts", "frontend/src/services/api.ts",
        "frontend/src/utils/stagedLedgerStorage.ts",
    }
    original = {k: v for k, v in manifest.items() if k != key}
    previous = []
    for row in manifest["files"]:
        prior_sha256 = update["changed_files"].get(row["path"])
        if prior_sha256 is not None:
            assert prior_sha256 != row["sha256"]
            row = {**row, "sha256": prior_sha256}
        previous.append(row)
    original["files"] = previous
    assert hashlib.sha256((json.dumps(original, indent=2) + "\n").encode()).hexdigest() == update["previous_manifest_sha256"]
    return original


def test_postcreate_profile_number_retry_preserves_exact_prior_manifest(verifier):
    manifest = verifier.load_manifest(ROOT / "docker/alpine-native/application-source.json")
    _before_postcreate_profile_number_retry(manifest)
    assert verifier.verify(ROOT, manifest)["postcreate_profile_number_retry_update"] == manifest["postcreate_profile_number_retry_update"]


def _before_postcreate_profile_review_delta(manifest):
    manifest = _before_postcreate_profile_number_retry(manifest)
    key = "postcreate_profile_review_delta_update"
    update = manifest[key]
    assert update["base_commit"] == "dbffd1e14ec6e89754c4586c4d285d4011baaff3"
    assert update["bead"] == "enhancedchannelmanager-0m06f.3.4.1"
    assert update["version"] == "0.18.2-0072"
    assert update["previous_manifest_sha256"] == "fd4c2f7120e98da0d00ec531ed9cd0615092b5886f37f2ecb9daa512839bd1fc"
    assert set(update["changed_files"]) == {
        "backend/profile_recovery.py", "backend/routers/channels.py", "backend/routers/profiles.py",
        "backend/routers/settings.py", "frontend/src/App.bulkCreateStaging.test.tsx", "frontend/src/App.tsx",
        "frontend/src/components/EditModeExitDialog.tsx", "frontend/src/hooks/useCreatedChannelProfiles.ts",
        "frontend/src/hooks/useEditMode.ts", "frontend/src/services/api.ts", "frontend/src/types/editMode.ts",
        "frontend/src/utils/stagedLedgerStorage.ts",
    }
    original = {k: v for k, v in manifest.items() if k != key}
    previous = []
    for row in manifest["files"]:
        prior_sha256 = update["changed_files"].get(row["path"])
        if prior_sha256 is not None:
            assert prior_sha256 != row["sha256"]
            row = {**row, "sha256": prior_sha256}
        previous.append(row)
    original["files"] = previous
    assert hashlib.sha256((json.dumps(original, indent=2) + "\n").encode()).hexdigest() == update["previous_manifest_sha256"]
    return original


def test_postcreate_profile_review_delta_preserves_exact_prior_manifest(verifier):
    manifest = verifier.load_manifest(ROOT / "docker/alpine-native/application-source.json")
    _before_postcreate_profile_review_delta(manifest)
    assert verifier.verify(ROOT, manifest)["postcreate_profile_review_delta_update"] == manifest["postcreate_profile_review_delta_update"]


def _before_postcreate_profile_review_rows(manifest):
    manifest = _before_postcreate_profile_review_delta(manifest)
    update = manifest["postcreate_profile_review_update"]
    assert update["base_commit"] == "e933013f78edf7f6b730813534506ddd079f93e0"
    assert update["bead"] == "enhancedchannelmanager-0m06f.3.4.1"
    assert update["version"] == "0.18.2-0072"
    assert update["previous_manifest_sha256"] == "d8ca0a64beca3103322608d4af521cd74dd37bfa0b5f9f1db90a5fca818baf12"
    assert update["added_files"] == ["backend/profile_recovery.py"]
    assert set(update["changed_files"]) == {
        "backend/routers/channels.py",
        "backend/routers/profiles.py",
        "frontend/src/App.bulkCreateStaging.test.tsx",
        "frontend/src/App.tsx",
        "frontend/src/components/CreatedChannelProfilesNotice.tsx",
        "frontend/src/hooks/useCreatedChannelProfiles.test.ts",
        "frontend/src/hooks/useCreatedChannelProfiles.ts",
        "frontend/src/hooks/useEditMode.ts",
        "frontend/src/services/api.ts",
        "frontend/src/services/createdChannelProfiles.http.test.ts",
        "frontend/src/services/createdChannelProfiles.test.ts",
        "frontend/src/services/createdChannelProfiles.ts",
        "frontend/src/services/profileMembership.ts",
        "frontend/src/types/editMode.ts",
        "frontend/src/utils/stagedLedgerStorage.ts",
    }
    previous = []
    for row in manifest["files"]:
        if row["path"] in update["added_files"]:
            assert row["type"] == "file" and row["executable"] is False
            continue
        restored = dict(row)
        if row["path"] in update["changed_files"]:
            assert row["sha256"] != update["changed_files"][row["path"]]
            restored["sha256"] = update["changed_files"][row["path"]]
        previous.append(restored)
    original = {k: v for k, v in manifest.items() if k != "postcreate_profile_review_update"}
    original["files"] = previous
    assert hashlib.sha256((json.dumps(original, indent=2) + "\n").encode()).hexdigest() == update["previous_manifest_sha256"]
    return previous


def test_postcreate_profile_review_snapshot_recovers_exact_prior_manifest(verifier):
    manifest = verifier.load_manifest(ROOT / "docker/alpine-native/application-source.json")
    _before_postcreate_profile_review_rows(manifest)
    assert verifier.verify(ROOT, manifest)["postcreate_profile_review_update"] == manifest["postcreate_profile_review_update"]


def _before_postcreate_profile_rows(manifest):
    update = manifest["postcreate_profile_intent_update"]
    assert update["base_commit"] == "bc4215697181cc4e8e3cc62d5c6e4b2c37b3345b"
    assert update["bead"] == "enhancedchannelmanager-0m06f.3.4.1"
    assert update["version"] == "0.18.2-0072"
    assert update["previous_manifest_sha256"] == "3b5ef4030751a0c1d95aa1b2b325ffd2d222fdf0d25d8e0bac56440b966f70af"
    assert set(update["changed_files"]) == {
        "backend/main.py", "backend/routers/backup.py", "frontend/package.json",
        "frontend/src/App.bulkCreateStaging.test.tsx", "frontend/src/App.css",
        "frontend/src/App.tsx", "frontend/src/hooks/useEditMode.ts",
        "frontend/src/services/profileMembership.ts",
    }
    assert set(update["added_files"]) == {
        "frontend/src/components/CreatedChannelProfilesNotice.css",
        "frontend/src/components/CreatedChannelProfilesNotice.test.tsx",
        "frontend/src/components/CreatedChannelProfilesNotice.tsx",
        "frontend/src/hooks/useCreatedChannelProfiles.test.ts",
        "frontend/src/hooks/useCreatedChannelProfiles.ts",
        "frontend/src/services/createdChannelProfiles.http.test.ts",
        "frontend/src/services/createdChannelProfiles.test.ts",
        "frontend/src/services/createdChannelProfiles.ts",
    }
    previous = []
    for row in _before_postcreate_profile_review_rows(manifest):
        if row["path"] in update["added_files"]:
            assert row["type"] == "file" and row["executable"] is False
            continue
        restored = dict(row)
        if row["path"] in update["changed_files"]:
            assert row["sha256"] != update["changed_files"][row["path"]]
            restored["sha256"] = update["changed_files"][row["path"]]
        previous.append(restored)
    assert len(previous) == 1084
    assert hashlib.sha256(json.dumps(previous, sort_keys=True, separators=(",", ":")).encode()).hexdigest() == "6d18db7c1758b0effb4e27c27cb451730a65a5cb0e5b3f30d6f6488b325d2f41"
    return previous


def test_postcreate_profile_snapshot_preserves_complete_previous_inventory(verifier):
    manifest = verifier.load_manifest(ROOT / "docker/alpine-native/application-source.json")
    _before_postcreate_profile_rows(manifest)
    assert verifier.verify(ROOT, manifest)["postcreate_profile_intent_update"] == manifest["postcreate_profile_intent_update"]


def _before_m3u_exclusion_rows(manifest):
    update = manifest["m3u_change_exclusions_update"]
    assert update["base_commit"] == "e8f2d1e984667d3136235a959759205bda3c5fd3"
    assert update["bead"] == "enhancedchannelmanager-z6jtf"
    assert update["version"] == "0.18.2-0071"
    assert update["previous_manifest_sha256"] == "8b310e3a57d8bc1981bab3abc7e1ffe6a50311d745b4c4bbf0e65e838e6f3454"
    assert set(update["added_files"]) == {
        "backend/alembic/versions/20261116_1200_0057_m3u_change_exclusions.py",
        "backend/m3u_change_exclusions.py", "mcp-server/tests/test_m3u_change_exclusions.py",
    }
    previous = []
    for row in _before_postcreate_profile_rows(manifest):
        if row["path"] in update["added_files"]:
            continue
        restored = dict(row)
        if row["path"] in update["changed_files"]:
            assert row["sha256"] != update["changed_files"][row["path"]]
            restored["sha256"] = update["changed_files"][row["path"]]
        previous.append(restored)
    assert len(previous) == 1081
    assert hashlib.sha256(json.dumps(previous, sort_keys=True, separators=(",", ":")).encode()).hexdigest() == "461b983dc18333fbbc9862689e9f15e2a457d84490423ac7340e792e77b644c3"
    return previous


def test_m3u_exclusions_snapshot_preserves_complete_previous_inventory(verifier):
    manifest = verifier.load_manifest(ROOT / "docker/alpine-native/application-source.json")
    _before_m3u_exclusion_rows(manifest)
    assert verifier.verify(ROOT, manifest)["m3u_change_exclusions_update"] == manifest["m3u_change_exclusions_update"]


def _before_ci_runner_repair_rows(manifest):
    update = manifest["ci_runner_repair_update"]
    assert update["base_commit"] == "bffdde2e4f2e660b3cadfd945afb9932711a6b52"
    assert update["bead"] == "enhancedchannelmanager-vn6jt"
    assert update["version"] == "0.18.2-0070"
    assert update["previous_manifest_sha256"] == "29a1e09f0850d98c31ae8bad4b8a0895a912895949821a1808fabf2ffd3dfa10"
    assert set(update["changed_files"]) == {"backend/main.py", "backend/routers/backup.py", "frontend/package.json"}
    previous = []
    for row in _before_m3u_exclusion_rows(manifest):
        restored = dict(row)
        if row["path"] in update["changed_files"]:
            assert row["sha256"] != update["changed_files"][row["path"]]
            restored["sha256"] = update["changed_files"][row["path"]]
        previous.append(restored)
    assert len(previous) == 1081
    assert hashlib.sha256(json.dumps(previous, sort_keys=True, separators=(",", ":")).encode()).hexdigest() == "b52c7200a76c11b5b5543a3afc80a26c02518a0cc74814218cfb03fd2d7755f4"
    return previous


def test_ci_runner_version_snapshot_preserves_complete_application_inventory(verifier):
    manifest = verifier.load_manifest(ROOT / "docker/alpine-native/application-source.json")
    _before_ci_runner_repair_rows(manifest)
    result = verifier.verify(ROOT, manifest)
    assert result["ci_runner_repair_update"] == manifest["ci_runner_repair_update"]


def _before_dependency_security_rows(manifest):
    update = manifest["dependency_security_update"]
    assert update["base_commit"] == "a6fcdce407f5be9ed97b82eb6b5f9f9d01c8144f"
    assert update["bead"] == "enhancedchannelmanager-w8h34"
    assert update["version"] == "0.18.2-0069"
    assert update["previous_manifest_sha256"] == "7b29e924266874eb6bd2a456f5e7861ef0f602b5172e482e4a53400a7b2260e8"
    assert set(update["changed_files"]) == {
        "backend/main.py", "backend/routers/backup.py", "frontend/package.json",
        "backend/requirements.in", "backend/requirements.txt",
    }
    previous = []
    for row in _before_ci_runner_repair_rows(manifest):
        restored = dict(row)
        if row["path"] in update["changed_files"]:
            assert row["sha256"] != update["changed_files"][row["path"]]
            restored["sha256"] = update["changed_files"][row["path"]]
        previous.append(restored)
    assert len(previous) == 1081
    assert hashlib.sha256(json.dumps(previous, sort_keys=True, separators=(",", ":")).encode()).hexdigest() == "8f36b586e209f2ada7c273a4014223c12ebfd888325dc22bcb102f95ddfc1912"
    return previous


def test_dependency_security_snapshot_preserves_previous_complete_inventory(verifier):
    manifest = verifier.load_manifest(ROOT / "docker/alpine-native/application-source.json")
    _before_dependency_security_rows(manifest)
    result = verifier.verify(ROOT, manifest)
    assert result["dependency_security_update"] == manifest["dependency_security_update"]


def _before_best_effort_probe_rows(manifest):
    update = manifest["best_effort_probe_update"]
    assert update["base_commit"] == "349122b7a03de400a28c3354bdfd9b3e0383ce77"
    assert update["bead"] == "enhancedchannelmanager-88yso"
    assert update["version"] == "0.18.2-0068"
    assert update["previous_manifest_sha256"] == "9f0d5feb0b17137b168ef3c4d67b773f5084726ad67df4a7acc43311ae90c57a"
    assert set(update["changed_files"]) == {
        "backend/channel_pipeline_engine.py", "backend/config.py", "backend/main.py",
        "backend/routers/backup.py", "backend/routers/settings.py", "backend/stream_prober.py",
        "backend/tasks/black_screen_scan.py", "frontend/package.json",
        "frontend/src/components/ChannelsPane.tsx", "frontend/src/components/tabs/SettingsTab.tsx",
        "frontend/src/components/tabs/SettingsTab.probeDeferred.test.tsx", "frontend/src/services/api.ts",
    }
    previous = []
    for row in _before_dependency_security_rows(manifest):
        restored = dict(row)
        if row["path"] in update["changed_files"]:
            assert row["sha256"] != update["changed_files"][row["path"]]
            restored["sha256"] = update["changed_files"][row["path"]]
        previous.append(restored)
    assert len(previous) == 1081
    # Pin the COMPLETE old map independently, retaining modes, types, unrelated
    # source and earlier native adaptation records rather than blessing changes.
    assert hashlib.sha256(json.dumps(previous, sort_keys=True, separators=(",", ":")).encode()).hexdigest() == "5b232a3732c3397cbe22915000670884195e5f91d96110f1be8ea5da29653be0"
    return previous


def test_best_effort_probe_snapshot_preserves_the_complete_previous_inventory(verifier):
    manifest = verifier.load_manifest(ROOT / "docker/alpine-native/application-source.json")
    _before_best_effort_probe_rows(manifest)
    result = verifier.verify(ROOT, manifest)
    assert result["best_effort_probe_update"] == manifest["best_effort_probe_update"]


def test_reviewed_group_delete_snapshot_extends_the_complete_previous_inventory(
    verifier,
):
    """Reconstruct the preceding inventory, including path/type/mode/hash data."""
    manifest = verifier.load_manifest(
        ROOT / "docker/alpine-native/application-source.json"
    )
    update = manifest["approved_application_update"]
    assert update["application_commit"] == "4dc2601d578235c3111c81447ce2c00b05c2ebcb"
    assert update["base_commit"] == "95fc8e4e88c22744327de0ed75767ce5e8b28af9"
    assert update["bead"] == "enhancedchannelmanager-jtrlf"
    assert update["version"] == "0.18.2-0067"
    assert (
        update["previous_manifest_sha256"]
        == "a3520f9549250c896d90c5d8477fb5cdf24bc56f9829e83ce9eff4ba2c9e2156"
    )
    assert set(update["changed_files"]) == {
        "backend/main.py",
        "backend/routers/backup.py",
        "frontend/package.json",
        "backend/routers/channel_groups.py",
        "backend/routers/channels.py",
    }
    assert update["added_files"] == ["backend/channel_group_delete.py"]
    rows = {row["path"]: row for row in manifest["files"]}
    assert rows["backend/channel_group_delete.py"]["type"] == "file"
    assert rows["backend/channel_group_delete.py"]["executable"] is False
    previous = []
    for row in _before_best_effort_probe_rows(manifest):
        if row["path"] in update["added_files"]:
            continue
        restored = dict(row)
        if row["path"] in update["changed_files"]:
            assert row["sha256"] != update["changed_files"][row["path"]]
            restored["sha256"] = update["changed_files"][row["path"]]
        previous.append(restored)
    assert len(previous) == 1080
    # Pin independently of the update metadata: resealing a dropped path,
    # changed executable/type or unrelated hash cannot bless a smaller snapshot.
    assert (
        hashlib.sha256(
            json.dumps(previous, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        == "09bd8191d77d71612c0fa3ab9ac3be727da3ca43164934116c3a384ba1f568fd"
    )
    result = verifier.verify(ROOT, manifest)
    assert result["approved_application_update"] == update


@pytest.mark.parametrize("mutation", ["missing", "unlisted", "executable"])
def test_application_source_cli_rejects_incomplete_or_mode_changed_snapshot(
    verifier, tmp_path, mutation
):
    manifest = verifier.load_manifest(
        ROOT / "docker/alpine-native/application-source.json"
    )
    source_root = tmp_path / "source"
    for row in manifest["files"]:
        source = ROOT / row["path"]
        target = source_root / row["path"]
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        target.chmod(source.stat().st_mode & 0o777)
    helper = source_root / "backend/channel_group_delete.py"
    if mutation == "missing":
        helper.unlink()
        expected = "regular application source file required"
    elif mutation == "unlisted":
        (source_root / "backend/unlisted.py").write_text("unreviewed source")
        expected = "application source coverage mismatch"
    else:
        helper.chmod(0o755)
        expected = "application source bytes or executable bit differ"
    output = tmp_path / "application-source.json"
    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--root",
            str(source_root),
            "--output-file",
            str(output),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode != 0
    assert expected in result.stderr
    assert not output.exists()


def test_changed_missing_or_extra_application_source_fails(verifier, tmp_path):
    source = tmp_path / "backend/service.py"
    source.parent.mkdir()
    source.write_bytes(b"original application bytes")
    manifest = {
        "files": [
            {
                "path": "backend/service.py",
                "sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
                "type": "file",
                "executable": False,
            }
        ]
    }
    verifier.verify(tmp_path, manifest)
    source.write_bytes(b"different application")
    with pytest.raises(verifier.SourceError, match="source bytes"):
        verifier.verify(tmp_path, manifest)
    source.write_bytes(b"original application bytes")
    (source.parent / "new_service.py").write_bytes(b"unreviewed service")
    with pytest.raises(verifier.SourceError, match="source coverage"):
        verifier.verify(tmp_path, manifest)


def test_source_symlinks_cannot_redirect_to_operator_files(verifier, tmp_path):
    directory = tmp_path / "backend"
    directory.mkdir()
    (directory / "service.py").symlink_to(tmp_path / "operator-file")
    with pytest.raises(verifier.SourceError, match="source file"):
        verifier.verify(
            tmp_path,
            {
                "files": [
                    {
                        "path": "backend/service.py",
                        "type": "file",
                        "sha256": "0" * 64,
                        "executable": False,
                    }
                ]
            },
        )


@pytest.mark.parametrize(
    "cache_file",
    [".gitignore", "constants/01e8b1e3e53c700f", "examples/a-recorded-example"],
)
def test_generated_hypothesis_cache_is_not_shipped_application_source(
    verifier, tmp_path, cache_file
):
    source = tmp_path / "backend/service.py"
    source.parent.mkdir()
    source.write_bytes(b"original application bytes")
    manifest = {
        "files": [
            {
                "path": "backend/service.py",
                "sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
                "type": "file",
                "executable": False,
            }
        ]
    }
    cache = source.parent / ".hypothesis" / cache_file
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text("recorded generated test-cache bytes")
    assert verifier.verify(tmp_path, manifest)["file_count"] == 1
    assert "**/.hypothesis" in (ROOT / ".dockerignore").read_text().splitlines()
    # The exact cache name is not a blanket exemption for hidden/new code.
    unexpected = source.parent / ".hypothesis_extra/service.py"
    unexpected.parent.mkdir()
    unexpected.write_text("unreviewed application code")
    with pytest.raises(verifier.SourceError, match="source coverage"):
        verifier.verify(tmp_path, manifest)
