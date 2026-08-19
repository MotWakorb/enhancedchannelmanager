#!/usr/bin/env bash
#
# End-to-end isolation gate for the ECM MCP sidecar
# (enhancedchannelmanager-04c0u.8).
#
# This brings up the SHIPPED topology — `docker compose -f docker-compose.yml
# -f docker-compose.mcp.yml` — on brand-new, unprovisioned Docker volumes, with
# no manual privilege or ownership step, and drives the whole credential path:
#
#     ECM writes the projection  ->  the sidecar reads it  ->  a client
#     authenticates with it  ->  ECM rotates it  ->  the superseded key dies.
#
# The producer is deliberately the real one. An earlier revision of this script
# hand-seeded the projection from `docker run --user 0:0` and then chowned the
# files to the target uid. That substituted root for the actor whose writability
# is actually in question — the ECM backend at PUID, writing into a directory
# Docker created as root:root 0755 — so every downstream assertion was true of a
# hand-seeded container and said nothing about the shipped deployment. It passed
# green while a first-run `docker compose up` could not start ECM at all.
#
# What is asserted, all against live containers:
#
#   * ECM starts on unprovisioned volumes and reaches /api/health/ready,
#   * ECM — not this script — writes both projection files, owner-only and
#     owned by PUID/PGID,
#   * the sidecar runs as that same fixed non-root uid,
#   * the sidecar can read its projection and nothing else: no ECM settings,
#     auth data, journal, TLS keys or backups exist for it to open,
#   * a different uid inside the sidecar cannot read the projection,
#   * the sidecar's root filesystem is read-only, all capabilities are dropped,
#     no-new-privileges is set, and /tmp is a bounded noexec/nosuid/nodev tmpfs,
#   * an authenticated MCP session lists tools through all of that,
#   * rotating the key through ECM takes effect without a restart, with the
#     superseded key rejected, and
#   * a BROKEN projection degrades rather than 5xx-ing authenticated routes,
#     logs once rather than once per request, and repairs itself without a
#     restart once the fault is cleared.
#
# Usage: scripts/test_mcp_container_hardening.sh
#
# Environment:
#   PUID / PGID   runtime identity to test (default 1000/1000). The invariant is
#                 "any pair the compose file accepts", so CI runs this twice.
#   ECM_PORT, ECM_HTTPS_PORT, MCP_PORT
#                 published ports; free ports are chosen automatically when unset
#                 so a developer's own ECM stack is never disturbed.
#   COMPOSE_BUILD if "false", require the images to exist already.
#   MCP_GATE_PROJECT
#                 compose project name (default "ecmmcpgate").
#
# Pass --build-only to build the two images and exit, so CI can report a
# transient registry/mirror build failure under its own step name instead of
# under a name that reads as a security regression.
set -euo pipefail

repository_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# Deterministic project name so a separate build step and this gate address the
# same compose-built images (`<project>-ecm`, `<project>-ecm-mcp`). The gate is
# serial by nature; override MCP_GATE_PROJECT if you need two on one host.
project="${MCP_GATE_PROJECT:-ecmmcpgate}"
projection_dir=/run/secrets/ecm-mcp

build_only=false
if [ "${1:-}" = "--build-only" ]; then
  build_only=true
fi

export PUID="${PUID:-1000}"
export PGID="${PGID:-1000}"

pick_free_port() {
  python3 - <<'PY'
import socket
with socket.socket() as probe:
    probe.bind(("127.0.0.1", 0))
    print(probe.getsockname()[1])
PY
}

ports_are_operator_supplied=true
if [ -z "${ECM_PORT:-}" ] && [ -z "${ECM_HTTPS_PORT:-}" ] && [ -z "${MCP_PORT:-}" ]; then
  ports_are_operator_supplied=false
fi

pick_ports() {
  export ECM_PORT="${ECM_PORT:-$(pick_free_port)}"
  export ECM_HTTPS_PORT="${ECM_HTTPS_PORT:-$(pick_free_port)}"
  export MCP_PORT="${MCP_PORT:-$(pick_free_port)}"
}

pick_ports

compose() {
  docker compose \
    -p "$project" \
    -f "$repository_root/docker-compose.yml" \
    -f "$repository_root/docker-compose.mcp.yml" \
    "$@"
}

fail() {
  echo "ISOLATION GATE FAILED: $1" >&2
  exit 1
}

# An assertion helper that names the property, because a CI failure reporting
# only a line number tells the next engineer nothing.
assert_equal() {
  if [ "$1" != "$2" ]; then
    fail "$3 (expected '$2', got '$1')"
  fi
}

# Dump both services' logs on any failure. "Did not run" and "found nothing"
# must be distinguishable from the CI output alone.
cleanup() {
  status=$?
  if [ "$status" -ne 0 ]; then
    echo "== compose logs (gate failed with status $status) ==" >&2
    compose logs --no-color --tail 200 >&2 2>&1 || true
  fi
  compose down -v --remove-orphans --timeout 5 >/dev/null 2>&1 || true
  return $status
}

trap cleanup EXIT

echo "== MCP isolation gate: project=$project PUID=$PUID PGID=$PGID"
echo "== ports: ecm=$ECM_PORT https=$ECM_HTTPS_PORT mcp=$MCP_PORT"

# Brand-new, unprovisioned resources. `down -v` in the trap removes them again;
# nothing here pre-creates, pre-chowns or pre-seeds a mount, which is the whole
# point — a first-run operator does none of that either.
compose down -v --remove-orphans >/dev/null 2>&1 || true

if [ "${COMPOSE_BUILD:-true}" != "false" ] || [ "$build_only" = true ]; then
  compose build
fi
if [ "$build_only" = true ]; then
  echo "built $project images for the MCP isolation gate"
  exit 0
fi

# `depends_on: ecm: condition: service_healthy` means this call itself waits
# for ECM to pass its healthcheck before the sidecar starts, so a backend that
# cannot start fails here rather than later.
#
# An auto-picked port can be claimed by another process between the probe and
# the bind, which is a flaky infrastructure failure and not the property under
# test. Re-pick and retry; an operator-supplied port is never silently
# replaced.
started=false
for attempt in 1 2 3; do
  if compose up -d --no-build; then
    started=true
    break
  fi
  if [ "$ports_are_operator_supplied" = true ] || [ "$attempt" = 3 ]; then
    break
  fi
  echo "-- compose up failed on attempt ${attempt}; re-picking ports and retrying" >&2
  compose down -v --remove-orphans >/dev/null 2>&1 || true
  unset ECM_PORT ECM_HTTPS_PORT MCP_PORT
  pick_ports
  echo "== ports: ecm=$ECM_PORT https=$ECM_HTTPS_PORT mcp=$MCP_PORT"
done
if [ "$started" != true ]; then
  fail "docker compose up did not bring the stack up on unprovisioned volumes"
fi

ecm_container="$(compose ps -q ecm)"
sidecar_container="$(compose ps -q ecm-mcp)"
[ -n "$ecm_container" ] || fail "the ecm container was not created"

# ── ECM starts on unprovisioned volumes ───────────────────────────────────
# This is the BLOCK-1 invariant. Before the entrypoint prepared MCP_SECRETS_DIR
# while still root, ECM raised PermissionError inside its FastAPI startup
# handler, uvicorn logged "Application startup failed. Exiting.", and this loop
# never went green.
ready=false
for _ in $(seq 1 120); do
  if curl -fsS "http://127.0.0.1:${ECM_PORT}/api/health/ready" >/dev/null 2>&1; then
    ready=true
    break
  fi
  if [ -z "$(docker ps -q --filter "id=${ecm_container}")" ]; then
    docker logs "$ecm_container" >&2 || true
    fail "the ECM container exited before becoming ready"
  fi
  sleep 2
done
if [ "$ready" != true ]; then
  docker logs "$ecm_container" >&2 || true
  fail "ECM never reached /api/health/ready on unprovisioned volumes"
fi
echo "-- ECM reached /api/health/ready on brand-new volumes"

# ── ECM, not this script, publishes the credentials ───────────────────────
# A first-run instance has no operator identity yet, so this is the same
# anonymous call the setup flow makes. ECM's real save_settings() +
# rotate_mcp_service_credentials() write the projection at PUID.
generate_key() {
  curl -fsS -X POST "http://127.0.0.1:${ECM_PORT}/api/settings/mcp-api-key" \
    -H 'Content-Type: application/json' \
    | python3 -c 'import json,sys; print(json.load(sys.stdin)["mcp_api_key"])'
}

old_key="$(generate_key)"
[ -n "$old_key" ] || fail "ECM did not return a generated MCP key"
echo "-- ECM generated and projected an MCP key"

# ── The projection is what ECM wrote, under the terms it claims ───────────
observed_owner="$(docker exec "$ecm_container" stat -c '%u:%g' "${projection_dir}/api-key")"
assert_equal "$observed_owner" "${PUID}:${PGID}" \
  "the projected public key is not owned by the configured PUID/PGID"
observed_owner="$(docker exec "$ecm_container" stat -c '%u:%g' "${projection_dir}/mcp-service.json")"
assert_equal "$observed_owner" "${PUID}:${PGID}" \
  "the projected private credentials are not owned by the configured PUID/PGID"

# ── Sidecar readiness through the projection ECM wrote ────────────────────
[ -n "$sidecar_container" ] || fail "the ecm-mcp container was not created"
sidecar_ready=false
for _ in $(seq 1 90); do
  status="$(docker exec "$sidecar_container" python -c "
import json, urllib.request
with urllib.request.urlopen('http://localhost:${MCP_PORT}/health') as response:
    print(json.load(response)['api_key_status'])
" 2>/dev/null || true)"
  if [ "$status" = "ok" ]; then
    sidecar_ready=true
    break
  fi
  if [ -z "$(docker ps -q --filter "id=${sidecar_container}")" ]; then
    docker logs "$sidecar_container" >&2 || true
    fail "the MCP sidecar exited before becoming ready"
  fi
  sleep 1
done
if [ "$sidecar_ready" != true ]; then
  docker logs "$sidecar_container" >&2 || true
  fail "the MCP sidecar never reported api_key_status=ok (last: ${status:-unknown})"
fi
echo "-- the sidecar read the projection ECM wrote (api_key_status=ok)"

# ── Identity ──────────────────────────────────────────────────────────────
observed_uid="$(docker exec "$sidecar_container" id -u)"
observed_gid="$(docker exec "$sidecar_container" id -g)"
assert_equal "$observed_uid" "$PUID" "the sidecar is not running as the configured PUID"
assert_equal "$observed_gid" "$PGID" "the sidecar is not running as the configured PGID"
if [ "$observed_uid" = 0 ]; then
  fail "the sidecar is running as root"
fi

# ── Runtime confinement and blast radius ──────────────────────────────────
# Every check is an explicit if/exit. `! cmd` is NOT a usable assertion here:
# `set -e` is specified to ignore a pipeline beginning with `!`, so the two
# unwritability checks below used to continue on failure and report success.
docker exec "$sidecar_container" sh -eu -c '
  fail() { echo "ISOLATION GATE FAILED: $1" >&2; exit 1; }

  observed="$(awk "/^CapEff/ {print \$2}" /proc/self/status)"
  [ "$observed" = 0000000000000000 ] || fail "sidecar retains capabilities: CapEff=$observed"
  observed="$(awk "/^NoNewPrivs/ {print \$2}" /proc/self/status)"
  [ "$observed" = 1 ] || fail "no-new-privileges is not set: NoNewPrivs=$observed"
  grep -Eq "^[^ ]+ / [^ ]+ ro([, ]|$)" /proc/mounts || fail "the sidecar root filesystem is not read-only"
  grep -Eq "^[^ ]+ /tmp [^ ]+ [^ ]*noexec" /proc/mounts || fail "/tmp is not noexec"
  grep -Eq "^[^ ]+ /tmp [^ ]+ [^ ]*nosuid" /proc/mounts || fail "/tmp is not nosuid"
  grep -Eq "^[^ ]+ /tmp [^ ]+ [^ ]*nodev" /proc/mounts || fail "/tmp is not nodev"

  # The credential projection is present, owner-only, and holds nothing else.
  [ -r '"$projection_dir"'/api-key ] || fail "the sidecar cannot read the public key projection"
  [ -r '"$projection_dir"'/mcp-service.json ] || fail "the sidecar cannot read the private credential projection"
  observed="$(stat -c %a '"$projection_dir"'/api-key)"
  [ "$observed" = 600 ] || fail "the public key projection is not owner-only (mode $observed)"
  observed="$(stat -c %a '"$projection_dir"'/mcp-service.json)"
  [ "$observed" = 600 ] || fail "the private credential projection is not owner-only (mode $observed)"
  observed="$(ls '"$projection_dir"' | sort | tr "\n" " ")"
  [ "$observed" = "api-key mcp-service.json " ] || fail "the projection directory holds more than MCP credential material: $observed"

  # ECM secrets are simply not reachable from this process.
  for forbidden in /config /config/settings.json /config/auth_settings.json \
                   /config/journal.db /config/tls /config/backups; do
    [ ! -e "$forbidden" ] || fail "the sidecar can see $forbidden"
  done

  # Nothing is writable: not the app, not the read-only projection mount.
  if touch /app/forbidden 2>/dev/null; then
    fail "the sidecar application directory is writable"
  fi
  if touch '"$projection_dir"'/forbidden 2>/dev/null; then
    fail "the credential projection mount is writable by the sidecar"
  fi
'
echo "-- sidecar confinement, projection modes and blast radius verified"

# Owner-only means owner-only: another uid in the same container cannot read
# the projected credentials even though the mount is visible to it.
if docker exec --user 1234:1234 "$sidecar_container" \
  cat "$projection_dir/api-key" >/dev/null 2>&1; then
  fail "the projected MCP key was readable by a non-owner uid"
fi

# ── Authenticated tool access, then live rotation ─────────────────────────
# The client runs from the sidecar's own image so the check needs no host-side
# MCP dependency, sharing the sidecar's network namespace so loopback reaches it.
sidecar_image="$(docker inspect -f '{{.Config.Image}}' "$sidecar_container")"
verify_authenticated_tools() {
  docker run --rm --user "${PUID}:${PGID}" \
    --network "container:$sidecar_container" \
    -e MCP_TEST_URL="http://127.0.0.1:${MCP_PORT}/mcp" \
    -e MCP_TEST_KEY="$1" \
    -v "$repository_root/scripts/verify_mcp_authenticated_tools.py:/verify.py:ro" \
    --entrypoint python "$sidecar_image" /verify.py
}

verify_authenticated_tools "$old_key"

new_key="$(generate_key)"
[ "$new_key" != "$old_key" ] || fail "rotation returned the same MCP key"

if verify_authenticated_tools "$old_key" >/dev/null 2>&1; then
  fail "the superseded MCP key remained valid after rotation"
fi
verify_authenticated_tools "$new_key"

# ── Degraded projection: ECM stays up AND stays usable ────────────────────
# Everything above exercises only the HEALTHY projection, which is how a route
# dependency regression survived a first remediation round. The middleware and
# the startup handler both degraded correctly; /api/health and /api/health/ready
# are in AUTH_EXEMPT_PATHS and never touch the projection, so the container
# HEALTHCHECK kept reporting healthy and `depends_on: service_healthy` stayed
# satisfied — while auth.dependencies._is_mcp_service_token, which
# get_current_user calls BEFORE the JWT decode for every token-bearing request,
# still raised and 500'd every dependency-guarded route.
#
# The invariant, not that reproduction: no reachable call site may raise out of
# a request or startup path on an unusable projection, on any branch.
docker exec --user 0:0 "$ecm_container" \
  sh -c "printf 'not json at all' > ${projection_dir}/mcp-service.json"

curl -fsS "http://127.0.0.1:${ECM_PORT}/api/health/ready" >/dev/null \
  || fail "ECM stopped being ready with a corrupt credential projection"

# /api/auth/me depends on get_current_user unconditionally, so it is the
# dependency seam whether or not auth has been configured. A bearer token that
# is not a JWT must reach the ordinary 401 — never a 5xx.
degraded_requests=20
observed=""
for _ in $(seq 1 "$degraded_requests"); do
  observed="$(curl -s -o /dev/null -w '%{http_code}' \
    -H 'Authorization: Bearer not-a-jwt-at-all' \
    "http://127.0.0.1:${ECM_PORT}/api/auth/me")"
  if [ "$observed" != 401 ]; then
    docker logs --tail 100 "$ecm_container" >&2 || true
    # Exactly 401, not merely "below 500": a 404 from a route that moved would
    # otherwise satisfy a <500 check while exercising nothing at all.
    fail "a corrupt credential projection did not degrade to the ordinary 401 (got $observed)"
  fi
done
echo "-- a corrupt projection degrades to HTTP 401 at the route dependency, not 5xx"

# The journal's actor-resolution middleware calls the same helper on every
# /api/* request and swallows exceptions into a warning. A line here means the
# dependency seam is raising again behind that except clause.
if docker logs "$ecm_container" 2>&1 | grep -q "Failed to resolve request mutation source"; then
  fail "the degraded projection is still raising inside the journal actor middleware"
fi

# ... and degraded mode must not become a log amplifier: one report per
# unhealthy episode, not one stack trace per request, because the likeliest
# cause of the broken state is a disk problem. 2 tolerates a second ECM process
# holding its own latch; it is decisively below $degraded_requests either way.
reports="$(docker logs "$ecm_container" 2>&1 \
  | grep -c "MCP sidecar credential projection .* is unusable" || true)"
if [ "$reports" -lt 1 ]; then
  fail "degraded mode never reported the broken credential projection at all"
fi
if [ "$reports" -gt 2 ]; then
  fail "degraded mode logged the broken projection $reports times over $degraded_requests requests; it must latch"
fi
echo "-- degraded mode reported the broken projection $reports time(s) over $degraded_requests requests"

# Recovery: ECM owns the projection, so clearing the fault must re-arm it
# without a restart.
docker exec --user 0:0 "$ecm_container" rm -f "${projection_dir}/mcp-service.json"
curl -s -o /dev/null -H 'Authorization: Bearer not-a-jwt-at-all' \
  "http://127.0.0.1:${ECM_PORT}/api/auth/me"
docker exec "$ecm_container" test -f "${projection_dir}/mcp-service.json" \
  || fail "ECM did not rebuild the credential projection after the fault was cleared"
observed_owner="$(docker exec "$ecm_container" stat -c '%u:%g' "${projection_dir}/mcp-service.json")"
assert_equal "$observed_owner" "${PUID}:${PGID}" \
  "the rebuilt private projection is not owned by the configured PUID/PGID"
observed="$(docker exec "$ecm_container" stat -c %a "${projection_dir}/mcp-service.json")"
assert_equal "$observed" "600" "the rebuilt private projection is not owner-only"
echo "-- ECM rebuilt the projection at PUID/PGID, owner-only, with no restart"

echo "MCP container isolation, real-producer projection, authenticated tools, live key rotation, and degraded-projection survival verified (PUID=$PUID PGID=$PGID)"
