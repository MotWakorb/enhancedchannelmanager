#!/usr/bin/env bash
#
# Runtime-hardening assertions for the ECM MCP sidecar
# (enhancedchannelmanager-04c0u.8). Everything here is checked against a live
# container, because the container is the thing being claimed about:
#
#   * the sidecar runs as a fixed non-root uid,
#   * it can read its credential projection and nothing else — no ECM
#     settings, auth data, journal, TLS keys or backups exist for it to open,
#   * the projection is owner-only, so a different uid in the same container
#     cannot read the credentials,
#   * the root filesystem is read-only, all capabilities are dropped,
#     no-new-privileges is set, and /tmp is a bounded noexec/nosuid/nodev
#     tmpfs,
#   * an authenticated MCP session still lists tools through all of that,
#   * and rotating the projected key takes effect without a restart, with the
#     superseded key rejected.
#
# Usage: scripts/test_mcp_container_hardening.sh [image-tag]
set -euo pipefail

image="${1:-ecm-mcp-hardening-test:local}"
repository_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
suffix="$$-${RANDOM}"
container="ecm-mcp-hardening-${suffix}"
volume="ecm-mcp-hardening-secrets-${suffix}"

# Must match the image's appuser/appgroup so the owner-only projection is
# readable exactly the way it is in production, where ECM and the sidecar share
# PUID/PGID (enhancedchannelmanager-04c0u.7).
sidecar_uid=1000
sidecar_gid=1000
projection_dir=/run/secrets/ecm-mcp

old_key='<Synthetic-Old-MCP-Key-04c0u8>'
new_key='<Synthetic-New-MCP-Key-04c0u8>'
backend_key='<Synthetic-Backend-Principal-Key-04c0u8-aaaaaaaaaa>'
confirmation_key='<Synthetic-Confirmation-Signing-Key-04c0u8-bbbbbb>'

cleanup() {
  docker rm -f "$container" >/dev/null 2>&1 || true
  docker volume rm -f "$volume" >/dev/null 2>&1 || true
}
trap cleanup EXIT

# Write the projection the way ECM does: owner-only, owned by the shared uid.
# Done from a throwaway root container so the result does not depend on the
# uid the CI runner happens to use.
project_credentials() {
  local public_key="$1"
  docker run --rm --user 0:0 \
    -e PUBLIC_KEY="$public_key" \
    -e BACKEND_KEY="$backend_key" \
    -e CONFIRMATION_KEY="$confirmation_key" \
    -e OWNER="${sidecar_uid}:${sidecar_gid}" \
    -v "$volume:$projection_dir" \
    --entrypoint sh "$image" -eu -c '
      printf "%s\n" "$PUBLIC_KEY" >'"$projection_dir"'/.api-key.tmp
      printf "{\"backend_key\":\"%s\",\"confirmation_key\":\"%s\"}" \
        "$BACKEND_KEY" "$CONFIRMATION_KEY" >'"$projection_dir"'/.mcp-service.tmp
      chown "$OWNER" '"$projection_dir"'/.api-key.tmp '"$projection_dir"'/.mcp-service.tmp
      chmod 0600 '"$projection_dir"'/.api-key.tmp '"$projection_dir"'/.mcp-service.tmp
      mv '"$projection_dir"'/.api-key.tmp '"$projection_dir"'/api-key
      mv '"$projection_dir"'/.mcp-service.tmp '"$projection_dir"'/mcp-service.json
    ' >/dev/null
}

# Run the MCP client from the same image so the check needs no host-side MCP
# dependency, sharing the sidecar's network namespace so loopback reaches it.
verify_authenticated_tools() {
  local key="$1"
  docker run --rm --user "${sidecar_uid}:${sidecar_gid}" \
    --network "container:$container" \
    -e MCP_TEST_URL="http://127.0.0.1:6101/mcp" \
    -e MCP_TEST_KEY="$key" \
    -v "$repository_root/scripts/verify_mcp_authenticated_tools.py:/verify.py:ro" \
    --entrypoint python "$image" /verify.py
}

docker volume create "$volume" >/dev/null
project_credentials "$old_key"

docker run -d --name "$container" \
  --user "${sidecar_uid}:${sidecar_gid}" \
  --read-only \
  --cap-drop ALL \
  --security-opt no-new-privileges \
  --pids-limit 128 \
  --memory 256m \
  --cpus 1 \
  --tmpfs /tmp:rw,noexec,nosuid,nodev,size=16m,mode=1777 \
  --mount "type=volume,src=$volume,dst=$projection_dir,readonly" \
  "$image" >/dev/null

ready=false
for _ in $(seq 1 60); do
  if docker exec "$container" python -c \
    "import urllib.request; urllib.request.urlopen('http://localhost:6101/health')" \
    >/dev/null 2>&1; then
    ready=true
    break
  fi
  if [ -z "$(docker ps -q --filter "name=^${container}$")" ]; then
    echo "MCP container exited before becoming ready" >&2
    docker logs "$container" >&2 || true
    exit 1
  fi
  sleep 1
done
if [ "$ready" != true ]; then
  echo "MCP container never reported ready" >&2
  docker logs "$container" >&2 || true
  exit 1
fi

# ── Identity ──────────────────────────────────────────────────────────────
observed_uid="$(docker exec "$container" id -u)"
observed_gid="$(docker exec "$container" id -g)"
test "$observed_uid" = "$sidecar_uid"
test "$observed_gid" = "$sidecar_gid"
test "$observed_uid" != 0

# ── Runtime confinement and blast radius ──────────────────────────────────
docker exec "$container" sh -eu -c '
  test "$(awk "/^CapEff/ {print \$2}" /proc/self/status)" = 0000000000000000
  test "$(awk "/^NoNewPrivs/ {print \$2}" /proc/self/status)" = 1
  grep -Eq "^[^ ]+ / [^ ]+ ro([, ]|$)" /proc/mounts
  grep -Eq "^[^ ]+ /tmp [^ ]+ [^ ]*noexec" /proc/mounts
  grep -Eq "^[^ ]+ /tmp [^ ]+ [^ ]*nosuid" /proc/mounts
  grep -Eq "^[^ ]+ /tmp [^ ]+ [^ ]*nodev" /proc/mounts

  # The credential projection is present, owner-only, and holds nothing else.
  test -r '"$projection_dir"'/api-key
  test -r '"$projection_dir"'/mcp-service.json
  test "$(stat -c %a '"$projection_dir"'/api-key)" = 600
  test "$(stat -c %a '"$projection_dir"'/mcp-service.json)" = 600
  test "$(ls '"$projection_dir"' | sort | tr "\n" " ")" = "api-key mcp-service.json "

  # ECM secrets are simply not reachable from this process.
  test ! -e /config
  test ! -e /config/settings.json
  test ! -e /config/auth_settings.json
  test ! -e /config/journal.db
  test ! -e /config/tls
  test ! -e /config/backups

  # Nothing is writable: not the app, not the read-only projection mount.
  ! touch /app/forbidden 2>/dev/null
  ! touch '"$projection_dir"'/forbidden 2>/dev/null
'

# Owner-only means owner-only: another uid in the same container cannot read
# the projected credentials even though the mount is visible to it.
if docker exec --user 1234:1234 "$container" \
  cat "$projection_dir/api-key" >/dev/null 2>&1; then
  echo "projected MCP key was readable by a non-owner uid" >&2
  exit 1
fi

# ── Authenticated tool access, then live rotation ─────────────────────────
verify_authenticated_tools "$old_key"

project_credentials "$new_key"

if verify_authenticated_tools "$old_key" >/dev/null 2>&1; then
  echo "superseded MCP key remained valid after rotation" >&2
  exit 1
fi
verify_authenticated_tools "$new_key"

echo "MCP container isolation, authenticated tools, and live key rotation verified"
