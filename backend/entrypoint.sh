#!/bin/sh
set -e

# Default ports
ECM_PORT=${ECM_PORT:-6100}
ECM_HTTPS_PORT=${ECM_HTTPS_PORT:-6143}

# Durable-data location. Mirrors backend/config.py's CONFIG_DIR resolution
# (same env var, same /config default) so the persistence warning below talks
# about the directory the application actually writes to.
CONFIG_DIR=${CONFIG_DIR:-/config}

# PUID/PGID defaults (match typical first non-root user)
PUID=${PUID:-1000}
PGID=${PGID:-1000}

# Colors for output
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Unicode symbols
CHECK_MARK="✓"
CROSS_MARK="✗"
ARROW="→"

# Print functions
print_header() {
    echo ""
    echo "${BLUE}════════════════════════════════════════════════════════════${NC}"
    echo "${BLUE}  Enhanced Channel Manager - Startup Preflight Checks${NC}"
    echo "${BLUE}════════════════════════════════════════════════════════════${NC}"
    echo ""
}

print_success() {
    echo "${GREEN}${CHECK_MARK}${NC} $1"
}

print_error() {
    echo "${RED}${CROSS_MARK}${NC} $1"
}

print_warning() {
    echo "${YELLOW}!${NC} $1"
}

print_info() {
    echo "${BLUE}${ARROW}${NC} $1"
}

# --- Interpreter resolution (bead enhancedchannelmanager-0oi96) ------------
# ECM's dependencies live in the image virtualenv at /opt/venv, which the
# Dockerfile puts on PATH. Resolving `python3`/`uvicorn` through PATH made
# every check below silently follow an operator-side PATH override (or a
# bind mount shadowing /opt/venv) down to the Debian system interpreter —
# which reports a reassuring "Python 3.12.13" while having none of ECM's
# packages. Field symptom on 0.18.0: "✗ Required Python packages missing"
# followed by ModuleNotFoundError: No module named 'fastapi'.
#
# So resolve both binaries ONCE, here, pinned to absolute venv paths, and
# use those everywhere. Both stay overridable (custom images may put the
# venv elsewhere) and both fall back to a PATH lookup if the pinned path is
# not executable, so an image without /opt/venv behaves as it did before.
#
# The [ ! -x ] fallbacks below cannot tell "the default pin is absent, fall
# back" from "the operator set this explicitly and typo'd it, fall back
# wrongly" (bead enhancedchannelmanager-wpzo6). Capture the override BEFORE
# the default expansion erases the distinction, so check_python can say which
# one happened — a discarded override that resolves something else silently
# hands the operator back the same diagnostics that sent them here.
VENV_BIN=/opt/venv/bin

ECM_PYTHON_DISCARDED=""
ECM_UVICORN_DISCARDED=""

_ecm_python_override=${ECM_PYTHON:-}
ECM_PYTHON="${ECM_PYTHON:-${VENV_BIN}/python}"
if [ ! -x "$ECM_PYTHON" ]; then
    ECM_PYTHON_DISCARDED=$_ecm_python_override
    ECM_PYTHON=$(command -v python3 2>/dev/null || echo "$ECM_PYTHON")
fi

_ecm_uvicorn_override=${ECM_UVICORN:-}
ECM_UVICORN="${ECM_UVICORN:-${VENV_BIN}/uvicorn}"
if [ ! -x "$ECM_UVICORN" ]; then
    ECM_UVICORN_DISCARDED=$_ecm_uvicorn_override
    ECM_UVICORN=$(command -v uvicorn 2>/dev/null || echo "$ECM_UVICORN")
fi

# Emitted on any interpreter-related preflight failure. The whole point is
# that the first log paste an operator sends is enough to diagnose the
# case — which interpreter we actually ran, what environment it came from,
# and whether the image virtualenv is still where we expect it.
print_python_diagnostics() {
    print_info "Interpreter diagnostics:"
    print_info "  resolved python : ${ECM_PYTHON}"
    if [ -x "$ECM_UVICORN" ]; then
        print_info "  resolved uvicorn: ${ECM_UVICORN}"
    else
        print_info "  resolved uvicorn: ${ECM_UVICORN} (NOT EXECUTABLE — the app launch would fail here too)"
    fi
    print_info "  sys.prefix      : $("$ECM_PYTHON" -c 'import sys; print(sys.prefix)' 2>/dev/null || echo '<interpreter did not run>')"
    print_info "  PATH            : ${PATH}"
    if [ -x "${VENV_BIN}/python" ]; then
        print_info "  ${VENV_BIN}/python: present"
    else
        print_info "  ${VENV_BIN}/python: MISSING — the image virtualenv is not where ECM expects it"
    fi
    print_info "ECM installs its Python packages into the image virtualenv at"
    print_info "/opt/venv. If you override PATH or bind-mount over /opt/venv,"
    print_info "that virtualenv is bypassed. Remove the override, or point"
    print_info "ECM_PYTHON/ECM_UVICORN at the interpreter that has ECM's deps."
}

setup_user() {
    print_info "Setting up user/group identity..."
    print_info "PUID=${PUID} PGID=${PGID}"

    # Create/modify the appuser group to match PGID
    CURRENT_GID=$(id -g appuser 2>/dev/null || echo "")
    if [ "$CURRENT_GID" != "$PGID" ]; then
        # Check if a group with this GID already exists
        EXISTING_GROUP=$(getent group "$PGID" 2>/dev/null | cut -d: -f1 || true)
        if [ -n "$EXISTING_GROUP" ] && [ "$EXISTING_GROUP" != "appuser" ]; then
            groupmod -g "99${PGID}" "$EXISTING_GROUP" 2>/dev/null || true
        fi
        groupmod -o -g "$PGID" appuser 2>/dev/null || true
    fi

    # Modify appuser UID to match PUID
    CURRENT_UID=$(id -u appuser 2>/dev/null || echo "")
    if [ "$CURRENT_UID" != "$PUID" ]; then
        # Check if a user with this UID already exists
        EXISTING_USER=$(getent passwd "$PUID" 2>/dev/null | cut -d: -f1 || true)
        if [ -n "$EXISTING_USER" ] && [ "$EXISTING_USER" != "appuser" ]; then
            usermod -o -u "99${PUID}" "$EXISTING_USER" 2>/dev/null || true
        fi
        usermod -o -u "$PUID" appuser 2>/dev/null || true
    fi

    print_success "Running as uid=$(id -u appuser) gid=$(id -g appuser)"
}

# Preflight check functions
check_python() {
    print_info "Checking Python environment..."

    # An explicit override we could not use (bead enhancedchannelmanager-wpzo6).
    # Advisory, not fatal: whatever we fell back to still has to pass the
    # checks below, and those report the path they actually used.
    if [ -n "$ECM_PYTHON_DISCARDED" ]; then
        print_warning "ECM_PYTHON=${ECM_PYTHON_DISCARDED} is not executable — falling back to a PATH lookup"
    fi
    if [ -n "$ECM_UVICORN_DISCARDED" ]; then
        print_warning "ECM_UVICORN=${ECM_UVICORN_DISCARDED} is not executable — falling back to a PATH lookup"
    fi

    # No pipe in the condition: piping to `cut` would mask the interpreter's
    # own exit status (the pipeline reports cut's), so a missing interpreter
    # would read as success.
    if PYTHON_VERSION_RAW=$("$ECM_PYTHON" --version 2>&1); then
        PYTHON_VERSION=$(echo "$PYTHON_VERSION_RAW" | cut -d' ' -f2)
        print_success "Python ${PYTHON_VERSION} found at ${ECM_PYTHON}"
    else
        print_error "Python 3 not found (tried: ${ECM_PYTHON})"
        print_python_diagnostics
        return 1
    fi

    # Check if we can import main modules
    if "$ECM_PYTHON" -c "import fastapi, uvicorn" 2>/dev/null; then
        print_success "FastAPI and Uvicorn available"
    else
        print_error "Required Python packages missing from ${ECM_PYTHON}"
        print_python_diagnostics
        return 1
    fi

    # `import uvicorn` above proves the MODULE is importable, which says
    # nothing about the launcher script this entrypoint execs — uvicorn ships
    # only in the virtualenv, so an all-green preflight was still followed by
    # a bare not-found from the exec line (bead enhancedchannelmanager-wpzo6).
    if [ ! -x "$ECM_UVICORN" ]; then
        print_error "Uvicorn launcher not executable: ${ECM_UVICORN}"
        print_python_diagnostics
        return 1
    fi
    print_success "Uvicorn launcher at ${ECM_UVICORN}"

    return 0
}

# --- Config persistence (bead enhancedchannelmanager-ebl4n) ----------------
# Is CONFIG_DIR backed by a bind mount or a named volume, rather than by the
# container's own writable layer? /proc/self/mountinfo is the one place both
# kinds show up from inside the container (field 5 is the mount point), and
# it is readable without privileges. A mount on an ANCESTOR of CONFIG_DIR
# counts too — the data is durable either way, and a false "your data is
# ephemeral" would be worse than saying nothing.
#
# Mount points containing whitespace are octal-escaped by the kernel, so the
# field-5 comparison below is exact rather than a substring match: a sibling
# mount (/config-backup) must not satisfy the check for /config.
ECM_MOUNTINFO=${ECM_MOUNTINFO:-/proc/self/mountinfo}

config_dir_is_persistent() {
    _dir=$1

    # Unreadable mountinfo => we cannot tell. Stay quiet rather than cry wolf.
    [ -r "$ECM_MOUNTINFO" ] || return 0

    awk -v dir="$_dir" '
        { mp = $5 }
        mp == "/"  { next }
        mp == dir  { found = 1 }
        substr(dir, 1, length(mp) + 1) == mp "/" { found = 1 }
        END { exit !found }
    ' "$ECM_MOUNTINFO"
}

# Advisory, never fatal: a first-run or throwaway container legitimately has
# no mount. But the operator has to be told, because every recreate — including
# the Portainer/Unraid/Watchtower "update" flow — silently destroys the lot.
check_config_persistence() {
    if config_dir_is_persistent "$CONFIG_DIR"; then
        print_success "Config directory ${CONFIG_DIR} is on a mounted volume (data persists across recreates)"
        return 0
    fi

    print_warning "DATA IS NOT PERSISTENT: ${CONFIG_DIR} is not a mounted volume."
    print_warning "  Everything ECM stores — settings.json, channel data, journal.db,"
    print_warning "  uploaded logos, TLS certificates — lives in this container's"
    print_warning "  writable layer and is DESTROYED whenever the container is removed"
    print_warning "  or recreated. That includes an 'update' in Portainer, Unraid or"
    print_warning "  Watchtower, and any 'docker compose up --force-recreate'."
    print_warning "  Fix: add a mount for ${CONFIG_DIR} and recreate the container —"
    print_warning "  'volumes: [\"ecm-config:${CONFIG_DIR}\"]' in compose, or"
    print_warning "  '-v ecm-config:${CONFIG_DIR}' on docker run."
    print_warning "  Back up first (Settings -> Backup & Restore) if this container"
    print_warning "  already holds data you care about — recreating without a mount"
    print_warning "  in place will lose it."
    return 0
}

# Prepare the MCP sidecar credential projection while we are still root
# (bead enhancedchannelmanager-04c0u.8).
#
# Under the MCP overlay MCP_SECRETS_DIR is a SEPARATE mount from CONFIG_DIR,
# and nothing else in the container prepares it:
#
#   * a brand-new Docker named volume is created root:root 0755, and
#   * a bind mount arrives owned by whatever host uid created the directory,
#
# so after the exec-time drop to `gosu appuser` the backend cannot create
# ``api-key`` or ``mcp-service.json`` there. That is not a degraded sidecar:
# backend/main.py materializes the private projection inside the FastAPI
# startup handler, and an exception out of a startup handler aborts the ASGI
# lifespan ("Application startup failed. Exiting."), so the whole of ECM fails
# to start. Chowning here is what makes a first-run deployment work with no
# manual privilege step, for ANY PUID/PGID — setup_user() has already moved
# appuser onto the requested uid/gid by the time this runs.
#
# Parameterised rather than hardcoded so backend/tests/unit/
# test_entrypoint_mcp_projection.py can exercise the real function without
# root or gosu, and so no new operator-settable environment seam is created
# (contrast ECM_MOUNTINFO, bead enhancedchannelmanager-nz8q4 item 2).
#
# Usage: prepare_mcp_projection_dir <dir> <user> <group> <probe-runner...>
prepare_mcp_projection_dir() {
    projection_dir="$1"
    projection_user="$2"
    projection_group="$3"
    shift 3

    if [ -z "$projection_dir" ]; then
        return 0
    fi
    if [ "$projection_dir" = "$CONFIG_DIR" ]; then
        # No overlay: the projection lands in CONFIG_DIR, which the config
        # checks above already create, chown and probe. Nothing extra to do.
        return 0
    fi

    if ! mkdir -p "$projection_dir" 2>/dev/null; then
        print_error "Failed to create MCP credential projection directory ${projection_dir}"
        return 1
    fi

    # Ownership is the mechanism, not the mode: the projected files are 0600
    # and the sidecar can read them only because it shares PUID/PGID with ECM.
    chown "${projection_user}:${projection_group}" "$projection_dir" 2>/dev/null || true
    chmod 700 "$projection_dir" 2>/dev/null || true

    # The probe is the check. chown/chmod above are best-effort because the
    # directory may already be correct without us being root; what must hold
    # is that the account ECM runs as can actually create a file here.
    if "$@" touch "${projection_dir}/.mcp_write_test" 2>/dev/null; then
        rm -f "${projection_dir}/.mcp_write_test"
        print_success "MCP credential projection ${projection_dir} is writable"
        return 0
    fi

    print_error "MCP credential projection ${projection_dir} is not writable by ${projection_user}"
    print_error "  ECM writes ${projection_dir}/api-key and ${projection_dir}/mcp-service.json there."
    print_error "  Fix: chown it to PUID=${PUID} PGID=${PGID} on the host (bind mount), or"
    print_error "  remove the volume and recreate the container so ECM can initialize it."
    return 1
}

check_filesystem() {
    print_info "Checking filesystem..."

    # Check config directory
    if [ -d "/config" ]; then
        print_success "Config directory exists"
    else
        print_warning "Config directory missing, creating..."
        mkdir -p /config || {
            print_error "Failed to create config directory"
            return 1
        }
        print_success "Config directory created"
    fi

    # Ensure subdirectories exist (volume mounts lose Dockerfile-created dirs)
    mkdir -p /config/tls /config/uploads/logos

    # Fix permissions
    chown -R appuser:appuser /config 2>/dev/null || true
    chmod 700 /config/tls

    # Check if writable
    if gosu appuser touch /config/.write_test 2>/dev/null; then
        rm -f /config/.write_test
        print_success "Config directory is writable"
    else
        print_error "Config directory is not writable"
        return 1
    fi

    # MCP sidecar credential projection (…-04c0u.8) — see the function above.
    prepare_mcp_projection_dir \
        "${MCP_SECRETS_DIR:-}" appuser appuser gosu appuser || return 1

    # Check frontend build
    if [ -d "/app/static" ]; then
        print_success "Frontend build found"
    else
        print_warning "Frontend build directory not found"
    fi

    return 0
}

check_network() {
    print_info "Checking network configuration..."

    # Check if HTTP port is available
    if ! netstat -tuln 2>/dev/null | grep -q ":${ECM_PORT} "; then
        print_success "Port ${ECM_PORT} (HTTP) is available"
    else
        print_warning "Port ${ECM_PORT} (HTTP) is already in use"
    fi

    # Check if HTTPS port is available
    if ! netstat -tuln 2>/dev/null | grep -q ":${ECM_HTTPS_PORT} "; then
        print_success "Port ${ECM_HTTPS_PORT} (HTTPS) is available"
    else
        print_warning "Port ${ECM_HTTPS_PORT} (HTTPS) is already in use"
    fi

    return 0
}

check_application() {
    print_info "Checking application modules..."

    # Check if main.py exists
    if [ -f "/app/main.py" ]; then
        print_success "Application entry point found"
    else
        print_error "Application entry point (main.py) not found"
        return 1
    fi

    # Try to import the app module
    cd /app
    if "$ECM_PYTHON" -c "import main" 2>/dev/null; then
        print_success "Application module loads successfully"
    else
        print_error "Application module failed to load"
        echo ""
        echo "${RED}Full traceback:${NC}"
        "$ECM_PYTHON" -c "import main" 2>&1
        echo ""
        print_python_diagnostics
        return 1
    fi

    return 0
}

check_tls_config() {
    # Check if TLS is configured (informational only - app manages HTTPS)
    TLS_CONFIG="/config/tls_settings.json"
    TLS_CERT="/config/tls/cert.pem"
    TLS_KEY="/config/tls/key.pem"

    if [ -f "$TLS_CONFIG" ]; then
        TLS_ENABLED=$("$ECM_PYTHON" -c "import json; print(json.load(open('$TLS_CONFIG')).get('enabled', False))" 2>/dev/null || echo "False")
        HTTPS_PORT=$("$ECM_PYTHON" -c "import json; print(json.load(open('$TLS_CONFIG')).get('https_port', $ECM_HTTPS_PORT))" 2>/dev/null || echo "$ECM_HTTPS_PORT")

        if [ "$TLS_ENABLED" = "True" ] && [ -f "$TLS_CERT" ] && [ -f "$TLS_KEY" ]; then
            print_success "TLS enabled with valid certificates"
            print_info "HTTPS will start on port $HTTPS_PORT (managed by application)"
        elif [ "$TLS_ENABLED" = "True" ]; then
            print_warning "TLS enabled but certificates not found"
        else
            print_info "TLS not enabled"
        fi
    else
        print_info "TLS not configured"
    fi
}

print_startup_info() {
    echo ""
    echo "${GREEN}════════════════════════════════════════════════════════════${NC}"
    echo "${GREEN}  All preflight checks passed!${NC}"
    echo "${GREEN}════════════════════════════════════════════════════════════${NC}"
    echo ""
    print_info "Starting Enhanced Channel Manager..."
    print_info "HTTP Server: http://0.0.0.0:${ECM_PORT}"
    print_info "HTTPS Server: Managed by application (if TLS enabled)"
    print_info "Health Check: http://0.0.0.0:${ECM_PORT}/api/health"
    echo ""
}

# Run all preflight checks
run_preflight_checks() {
    print_header

    FAILED=0

    setup_user || FAILED=1
    check_python || FAILED=1
    check_filesystem || FAILED=1
    # Advisory only (bead enhancedchannelmanager-ebl4n) — deliberately not
    # chained into FAILED: an unmounted CONFIG_DIR must warn, not block.
    check_config_persistence
    check_network || FAILED=1
    check_application || FAILED=1

    if [ $FAILED -eq 1 ]; then
        echo ""
        print_error "Preflight checks failed! See errors above."
        echo ""
        exit 1
    fi

    print_startup_info
}

# Main execution
run_preflight_checks

# Display TLS configuration status (informational only)
check_tls_config

# Switch to non-root user and run the application
# HTTP server runs as main process on port ECM_PORT (default 6100)
# HTTPS server is managed by the application as a subprocess (if TLS enabled)
#
# Concurrency limits (bd-w3z4h):
# --limit-concurrency caps the number of in-flight requests per worker so a
#   burst of slow requests can't exhaust memory; once reached, uvicorn returns
#   503 instead of queueing indefinitely.
# --timeout-keep-alive closes idle keep-alive connections faster, freeing
#   sockets under sustained load.
# Values chosen to keep healthcheck comfortably inside the 10s HEALTHCHECK
# budget and can be overridden via env vars without rebuilding.
#
# Event loop (bead wadu3, follow-up to GH #546):
# --loop pins the stdlib asyncio event loop. Without it, uvicorn's "auto"
#   policy selects uvloop 0.22.1, which has open upstream issues on exactly
#   this stack: MagicStack/uvloop#645 (responses leaking to the WRONG
#   request under load — data exposure; fix merged upstream but unreleased)
#   and MagicStack/uvloop#706 (segfault in FastAPI-in-container, still
#   open). Set ECM_UVICORN_LOOP=uvloop to opt back in without rebuilding.
cd /app
ECM_LIMIT_CONCURRENCY=${ECM_LIMIT_CONCURRENCY:-100}
ECM_TIMEOUT_KEEP_ALIVE=${ECM_TIMEOUT_KEEP_ALIVE:-30}
# Numeric guard (bead enhancedchannelmanager-1xoiq; docs/style_guide.md
# "Shell Scripting"): these values land on the exec argv below, so
# arbitrary env content must never pass through — digits only. Like the
# ECM_UVICORN_LOOP whitelist, an invalid value fails CLOSED to the default
# with a warning instead of exiting: set -e + exec would turn uvicorn's
# argv rejection into a container restart loop with the same bad env var
# still set on every attempt.
case "${ECM_PORT}" in
    *[!0-9]*|'')
        print_warning "Invalid ECM_PORT='${ECM_PORT}' (must be numeric) — falling back to 6100"
        ECM_PORT=6100
        ;;
esac
case "${ECM_LIMIT_CONCURRENCY}" in
    *[!0-9]*|'')
        print_warning "Invalid ECM_LIMIT_CONCURRENCY='${ECM_LIMIT_CONCURRENCY}' (must be numeric) — falling back to 100"
        ECM_LIMIT_CONCURRENCY=100
        ;;
esac
case "${ECM_TIMEOUT_KEEP_ALIVE}" in
    *[!0-9]*|'')
        print_warning "Invalid ECM_TIMEOUT_KEEP_ALIVE='${ECM_TIMEOUT_KEEP_ALIVE}' (must be numeric) — falling back to 30"
        ECM_TIMEOUT_KEEP_ALIVE=30
        ;;
esac
# Whitelist mirror of tls/https_server.py resolve_loop_choice(): the value
# lands in the uvicorn argv, so arbitrary env content must never pass
# through (quoted expansion below prevents field-splitting a crafted value
# into extra uvicorn flags), and an invalid value must fail CLOSED — fall
# back to asyncio with a warning — instead of crash-looping the container
# (set -e + exec would turn uvicorn's rejection into a restart loop).
case "${ECM_UVICORN_LOOP:-}" in
    auto|asyncio|uvloop)
        ;;
    *)
        if [ -n "${ECM_UVICORN_LOOP:-}" ]; then
            print_warning "Invalid ECM_UVICORN_LOOP='${ECM_UVICORN_LOOP}' (allowed: auto, asyncio, uvloop) — falling back to asyncio"
        fi
        ECM_UVICORN_LOOP=asyncio
        ;;
esac
# Launch via the interpreter resolved at the top of this script, not via a
# PATH lookup (bead enhancedchannelmanager-0oi96): uvicorn exists ONLY in
# /opt/venv/bin, so a PATH override that hides the venv would fail here even
# with every preflight check passing.
exec gosu appuser "$ECM_UVICORN" main:app \
    --host 0.0.0.0 \
    --port "${ECM_PORT}" \
    --loop "${ECM_UVICORN_LOOP}" \
    --limit-concurrency "${ECM_LIMIT_CONCURRENCY}" \
    --timeout-keep-alive "${ECM_TIMEOUT_KEEP_ALIVE}"
