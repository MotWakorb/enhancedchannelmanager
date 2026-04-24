#!/usr/bin/env bash
# ADR-001 fresh-image smoke test for dep bumps.
#
# Builds the ECM image fresh from the current branch (no cache, unique tag),
# brings it up in an isolated container on a random host port, hits a matrix
# of health/CRUD/list endpoints, then tears down. Exit code 0 = all checks
# passed, non-zero = one or more checks failed.
#
# Why this exists: ADR-001 (docs/adr/ADR-001-dependency-upgrade-validation-gate.md)
# requires a fresh-image smoke per dependency bump to close the silent-skew
# window between the engineer's mutated dev container and the CI-built image.
# This script automates that contract so it can be run locally before opening
# a PR and is reusable from CI.
#
# Usage:
#   ./scripts/smoke_test_dev_container.sh                # Run smoke; tear down on exit
#   ./scripts/smoke_test_dev_container.sh --keep-container  # Leave container running for debug
#   ./scripts/smoke_test_dev_container.sh --json         # Emit a JSON report on stdout (still prints PASS/FAIL lines to stderr)
#   ./scripts/smoke_test_dev_container.sh --no-build     # Skip the build (use existing tag); useful for re-running checks
#   ./scripts/smoke_test_dev_container.sh --image TAG    # Override the image tag (default: ecm-smoke-<short-sha>)
#
# Constraints:
# - Does NOT touch the running ecm-ecm-1 container or its image. Uses a
#   unique image tag derived from the current short SHA to avoid clobbering.
# - Picks a random free host port to avoid colliding with anything else on
#   the host (in particular, the developer's running ecm container).
# - Binds the smoke container to 127.0.0.1 only — this is a local probe,
#   not a publicly reachable service.

set -u
set -o pipefail

# ── Argument parsing ────────────────────────────────────────────────────────
KEEP_CONTAINER=0
JSON_MODE=0
DO_BUILD=1
IMAGE_TAG=""

while [ $# -gt 0 ]; do
    case "$1" in
        --keep-container)
            KEEP_CONTAINER=1
            shift
            ;;
        --json)
            JSON_MODE=1
            shift
            ;;
        --no-build)
            DO_BUILD=0
            shift
            ;;
        --image)
            IMAGE_TAG="${2:-}"
            if [ -z "$IMAGE_TAG" ]; then
                echo "ERROR: --image requires a tag argument" >&2
                exit 2
            fi
            shift 2
            ;;
        -h|--help)
            sed -n '2,30p' "$0"
            exit 0
            ;;
        *)
            echo "ERROR: unknown argument: $1" >&2
            echo "Run with --help for usage." >&2
            exit 2
            ;;
    esac
done

# ── Locate repo root ────────────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$REPO_ROOT"

# In JSON mode, route human-readable output to stderr so the JSON report
# on stdout stays clean and machine-parseable.
if [ "$JSON_MODE" -eq 1 ]; then
    LOG_FD=2
else
    LOG_FD=1
fi

log() {
    if [ "$LOG_FD" -eq 2 ]; then
        echo "$@" >&2
    else
        echo "$@"
    fi
}

# ── Tool availability check ─────────────────────────────────────────────────
for tool in docker curl jq; do
    if ! command -v "$tool" >/dev/null 2>&1; then
        echo "ERROR: required tool '$tool' not found in PATH" >&2
        exit 2
    fi
done

# ── Image tag + container name ──────────────────────────────────────────────
SHORT_SHA="$(git rev-parse --short HEAD 2>/dev/null || echo unknown)"
if [ -z "$IMAGE_TAG" ]; then
    IMAGE_TAG="ecm-smoke:${SHORT_SHA}"
fi
CONTAINER_NAME="ecm-smoke-${SHORT_SHA}-$$"

# ── Pick a random free host port (49152–65535 ephemeral range) ──────────────
# Try up to 10 times to avoid a rare collision; bail if we can't find one.
HOST_PORT=""
for _ in $(seq 1 10); do
    CANDIDATE=$(( (RANDOM % 16384) + 49152 ))
    # Use python as a portable port-availability check that doesn't depend on
    # ss/netstat layouts. Returns 0 if the port can be bound on 127.0.0.1.
    if python3 -c "
import socket, sys
s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
try:
    s.bind(('127.0.0.1', $CANDIDATE))
except OSError:
    sys.exit(1)
finally:
    s.close()
" 2>/dev/null; then
        HOST_PORT=$CANDIDATE
        break
    fi
done

if [ -z "$HOST_PORT" ]; then
    echo "ERROR: could not find a free host port after 10 attempts" >&2
    exit 2
fi

# ── Cleanup trap ────────────────────────────────────────────────────────────
cleanup() {
    local exit_code=$?
    if [ "$KEEP_CONTAINER" -eq 1 ]; then
        log ""
        log "──────────────────────────────────────────────────────────────"
        log "  --keep-container set; smoke container left running for debug"
        log "  Container: $CONTAINER_NAME"
        log "  URL:       http://127.0.0.1:$HOST_PORT"
        log "  Inspect:   docker logs $CONTAINER_NAME"
        log "  Stop:      docker rm -f $CONTAINER_NAME"
        log "──────────────────────────────────────────────────────────────"
    else
        if docker inspect "$CONTAINER_NAME" >/dev/null 2>&1; then
            log ""
            log "Tearing down smoke container..."
            docker rm -f "$CONTAINER_NAME" >/dev/null 2>&1 || true
        fi
    fi
    exit "$exit_code"
}
trap cleanup EXIT INT TERM

# ── Build (unless --no-build) ───────────────────────────────────────────────
log "=========================================="
log "ADR-001 Fresh-Image Smoke Test"
log "=========================================="
log "  Repo root:    $REPO_ROOT"
log "  Branch:       $(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo unknown)"
log "  Short SHA:    $SHORT_SHA"
log "  Image tag:    $IMAGE_TAG"
log "  Container:    $CONTAINER_NAME"
log "  Host port:    127.0.0.1:$HOST_PORT"
log ""

WALL_START=$(date +%s)

if [ "$DO_BUILD" -eq 1 ]; then
    log "── Building fresh image (no cache) ──"
    BUILD_START=$(date +%s)
    if ! docker build \
        --no-cache \
        --build-arg "GIT_COMMIT=$SHORT_SHA" \
        --build-arg "ECM_VERSION=smoke-$SHORT_SHA" \
        --build-arg "RELEASE_CHANNEL=smoke" \
        -t "$IMAGE_TAG" \
        . >&2; then
        log ""
        log "FAIL: docker build failed."
        exit 1
    fi
    BUILD_END=$(date +%s)
    log ""
    log "Build completed in $((BUILD_END - BUILD_START))s."
else
    log "── Skipping build (--no-build) ──"
    if ! docker image inspect "$IMAGE_TAG" >/dev/null 2>&1; then
        log "FAIL: image '$IMAGE_TAG' not found and --no-build was passed."
        exit 1
    fi
fi
log ""

# ── Start the container ─────────────────────────────────────────────────────
log "── Starting smoke container ──"
# Bind to 127.0.0.1 so the smoke probe is never reachable from the network.
# Use ECM_PORT=6100 (the in-container port) and a random host port.
# We intentionally do NOT set DISPATCHARR_URL — a fresh container has no
# config and no upstream; endpoints that proxy to Dispatcharr will return
# 500, which the channels/epg checks accept as proof the route is wired
# (the handler ran). The local-DB endpoints (auto-creation, journal) and
# the schema/auth probes do not depend on Dispatcharr.
if ! docker run -d --rm \
    --name "$CONTAINER_NAME" \
    -p "127.0.0.1:${HOST_PORT}:6100" \
    -e ECM_PORT=6100 \
    "$IMAGE_TAG" >/dev/null; then
    log "FAIL: docker run failed."
    exit 1
fi

BASE_URL="http://127.0.0.1:${HOST_PORT}"

# ── Wait for /api/health to come up ────────────────────────────────────────
log "Waiting for /api/health to become ready (timeout 90s)..."
READY=0
for i in $(seq 1 45); do
    if curl -sf -o /dev/null --max-time 2 "${BASE_URL}/api/health"; then
        READY=1
        log "  ready after ~$((i * 2))s"
        break
    fi
    sleep 2
done

if [ "$READY" -eq 0 ]; then
    log ""
    log "FAIL: container did not become ready within 90s."
    log "── Container logs (last 80 lines) ──"
    docker logs --tail 80 "$CONTAINER_NAME" >&2 || true
    exit 1
fi
log ""

# ── Health endpoint matrix ──────────────────────────────────────────────────
# Each check function takes no args, hits one endpoint, and prints exactly
# one PASS/FAIL line. Results are accumulated in arrays for the summary.
CHECK_NAMES=()
CHECK_STATUSES=()  # PASS or FAIL
CHECK_DETAILS=()
CHECK_HTTP_CODES=()
CHECK_DURATIONS_MS=()

run_check() {
    # Args: name, expected_codes (comma-separated, e.g. "200" or "200,500"),
    #       method, path, [extra_curl_args...]
    #
    # Why multiple expected codes? ADR-001's purpose is to validate that the
    # fresh image boots and serves traffic, not that downstream services
    # (Dispatcharr) are reachable. Endpoints that proxy to Dispatcharr will
    # return 500 on a fresh container with no Dispatcharr configured — that
    # response still proves the route is registered, imports loaded, and the
    # FastAPI handler executed. A 404 (route missing) or 502/connect-refused
    # (FastAPI didn't start) would be a real failure. Accepting multiple
    # codes lets the check distinguish "wired correctly, downstream
    # unavailable" from "wiring broken."
    #
    # Optional env: EXPECT_JSONPATH (jq filter), EXPECT_JSONPATH_VALUE —
    # only evaluated when the actual code matches one of the expected codes.
    local name="$1"; shift
    local expected_codes="$1"; shift
    local method="$1"; shift
    local path="$1"; shift

    local start_ms end_ms duration_ms
    start_ms=$(date +%s%3N)

    # -w '%{http_code}' for the status, body to a tempfile.
    local body_file
    body_file=$(mktemp)
    local http_code
    http_code=$(curl -s -o "$body_file" -w '%{http_code}' --max-time 10 -X "$method" "${BASE_URL}${path}" "$@" 2>/dev/null || echo "000")

    end_ms=$(date +%s%3N)
    duration_ms=$((end_ms - start_ms))

    local detail="HTTP $http_code in ${duration_ms}ms"
    local status="PASS"

    # Match against the comma-separated expected_codes set.
    local code_matched=0
    local IFS_BACKUP="$IFS"
    IFS=','
    # shellcheck disable=SC2086
    for code in $expected_codes; do
        if [ "$http_code" = "$code" ]; then
            code_matched=1
            break
        fi
    done
    IFS="$IFS_BACKUP"

    if [ "$code_matched" -eq 0 ]; then
        status="FAIL"
        detail="$detail (expected one of: $expected_codes)"
        local body_snippet
        body_snippet=$(head -c 200 "$body_file" 2>/dev/null | tr '\n' ' ' | tr -d '\r')
        detail="$detail | body: ${body_snippet}"
    elif [ -n "${EXPECT_JSONPATH:-}" ]; then
        local actual
        actual=$(jq -r "$EXPECT_JSONPATH" "$body_file" 2>/dev/null || echo "<jq-error>")
        if [ "$actual" != "${EXPECT_JSONPATH_VALUE:-}" ]; then
            status="FAIL"
            detail="$detail | $EXPECT_JSONPATH expected '${EXPECT_JSONPATH_VALUE:-}', got '$actual'"
        else
            detail="$detail | $EXPECT_JSONPATH = '$actual'"
        fi
    fi

    rm -f "$body_file"

    CHECK_NAMES+=("$name")
    CHECK_STATUSES+=("$status")
    CHECK_DETAILS+=("$detail")
    CHECK_HTTP_CODES+=("$http_code")
    CHECK_DURATIONS_MS+=("$duration_ms")

    log "  [$status] $name — $detail"
}

log "── Endpoint matrix ──"

# 1. /api/health/schema — must return 200 + up_to_date=true.
EXPECT_JSONPATH='.up_to_date' EXPECT_JSONPATH_VALUE='true' \
    run_check "schema_up_to_date" 200 GET "/api/health/schema"
unset EXPECT_JSONPATH EXPECT_JSONPATH_VALUE

# 2. /api/auth/setup-required — confirms the auth stack is reachable on a
#    fresh (no-users) container. We expect 200 with required=true. This is
#    the equivalent of "auth bootstrap" without needing test credentials.
EXPECT_JSONPATH='.required' EXPECT_JSONPATH_VALUE='true' \
    run_check "auth_setup_required" 200 GET "/api/auth/setup-required"
unset EXPECT_JSONPATH EXPECT_JSONPATH_VALUE

# 3. GET /api/channels?limit=1 — confirms channels CRUD path is wired up.
#    Accept 200 (Dispatcharr reachable) OR 500 (Dispatcharr unreachable but
#    handler executed). For ADR-001 fresh-image validation, what matters is
#    that the route exists, imports loaded, and the handler ran — a 500 from
#    the Dispatcharr proxy still proves all of that. A 404 (route missing)
#    would fail.
run_check "channels_list" "200,500" GET "/api/channels?limit=1"

# 4. GET /api/epg/sources?limit=1 — confirms EPG ingest path is wired up.
#    (The bead spec referenced /api/epg-sources; the actual route is
#    /api/epg/sources per backend/routers/epg.py.)
#    Same wired-vs-reachable rationale as channels_list above.
run_check "epg_sources_list" "200,500" GET "/api/epg/sources?limit=1"

# 5. GET /api/auto-creation/rules?limit=1 — confirms auto-creation path.
#    Pure local-DB endpoint, must return 200.
run_check "auto_creation_rules_list" 200 GET "/api/auto-creation/rules?limit=1"

# 6. GET /api/journal?limit=1 — confirms journal path.
#    Pure local-DB endpoint, must return 200. page_size is the actual query
#    arg (router accepts page_size, not limit), but unknown args are ignored
#    and the endpoint still responds 200.
run_check "journal_list" 200 GET "/api/journal?page_size=1"

# 7. GET /api/journal?batch_id=00000000 — confirms the bd-s4sph batch_id
#    filter shape is accepted (200 with empty results, not 422).
run_check "journal_batch_id_filter" 200 GET "/api/journal?page_size=1&batch_id=00000000"

WALL_END=$(date +%s)
WALL_SECS=$((WALL_END - WALL_START))

# ── Summary ────────────────────────────────────────────────────────────────
PASS_COUNT=0
FAIL_COUNT=0
for s in "${CHECK_STATUSES[@]}"; do
    if [ "$s" = "PASS" ]; then
        PASS_COUNT=$((PASS_COUNT + 1))
    else
        FAIL_COUNT=$((FAIL_COUNT + 1))
    fi
done

log ""
log "=========================================="
log "Smoke summary: ${PASS_COUNT}/${#CHECK_STATUSES[@]} passed"
log "Wall clock:    ${WALL_SECS}s"
log "=========================================="

if [ "$FAIL_COUNT" -gt 0 ]; then
    log ""
    log "── FAILED checks ──"
    for i in "${!CHECK_STATUSES[@]}"; do
        if [ "${CHECK_STATUSES[$i]}" = "FAIL" ]; then
            log "  - ${CHECK_NAMES[$i]}: ${CHECK_DETAILS[$i]}"
        fi
    done
    log ""
    log "── Container logs (last 80 lines) ──"
    docker logs --tail 80 "$CONTAINER_NAME" >&2 || true
fi

# ── JSON report (stdout) ───────────────────────────────────────────────────
if [ "$JSON_MODE" -eq 1 ]; then
    # Build a JSON document with jq so escaping is correct.
    # Use a tempfile of newline-separated check records, then read it in.
    REPORT_FILE=$(mktemp)
    {
        for i in "${!CHECK_NAMES[@]}"; do
            jq -nc \
                --arg name "${CHECK_NAMES[$i]}" \
                --arg status "${CHECK_STATUSES[$i]}" \
                --arg detail "${CHECK_DETAILS[$i]}" \
                --arg http_code "${CHECK_HTTP_CODES[$i]}" \
                --argjson duration_ms "${CHECK_DURATIONS_MS[$i]}" \
                '{name: $name, status: $status, detail: $detail, http_code: $http_code, duration_ms: $duration_ms}'
        done
    } > "$REPORT_FILE"

    jq -s \
        --arg branch "$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo unknown)" \
        --arg sha "$SHORT_SHA" \
        --arg image "$IMAGE_TAG" \
        --argjson wall_seconds "$WALL_SECS" \
        --argjson pass_count "$PASS_COUNT" \
        --argjson fail_count "$FAIL_COUNT" \
        '{
            branch: $branch,
            git_short_sha: $sha,
            image: $image,
            wall_seconds: $wall_seconds,
            pass_count: $pass_count,
            fail_count: $fail_count,
            overall: (if $fail_count == 0 then "PASS" else "FAIL" end),
            checks: .
        }' "$REPORT_FILE"
    rm -f "$REPORT_FILE"
fi

if [ "$FAIL_COUNT" -gt 0 ]; then
    exit 1
fi
exit 0
