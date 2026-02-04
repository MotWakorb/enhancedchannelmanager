#!/bin/sh
set -e

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

# Preflight check functions
check_python() {
    print_info "Checking Python environment..."

    if command -v python3 >/dev/null 2>&1; then
        PYTHON_VERSION=$(python3 --version 2>&1 | cut -d' ' -f2)
        print_success "Python ${PYTHON_VERSION} found"
    else
        print_error "Python 3 not found"
        return 1
    fi

    # Check if we can import main modules
    if python3 -c "import fastapi, uvicorn" 2>/dev/null; then
        print_success "FastAPI and Uvicorn available"
    else
        print_error "Required Python packages missing"
        return 1
    fi

    return 0
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

    # Fix permissions
    chown -R appuser:appuser /config 2>/dev/null || true

    # Check if writable
    if gosu appuser touch /config/.write_test 2>/dev/null; then
        rm -f /config/.write_test
        print_success "Config directory is writable"
    else
        print_error "Config directory is not writable"
        return 1
    fi

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

    # Check if port 6100 (HTTP) is available
    if ! netstat -tuln 2>/dev/null | grep -q ":6100 "; then
        print_success "Port 6100 (HTTP) is available"
    else
        print_warning "Port 6100 (HTTP) is already in use"
    fi

    # Check if port 6143 (HTTPS) is available
    if ! netstat -tuln 2>/dev/null | grep -q ":6143 "; then
        print_success "Port 6143 (HTTPS) is available"
    else
        print_warning "Port 6143 (HTTPS) is already in use"
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
    if python3 -c "import main" 2>/dev/null; then
        print_success "Application module loads successfully"
    else
        print_error "Application module failed to load"
        echo ""
        echo "${RED}Full traceback:${NC}"
        python3 -c "import main" 2>&1
        echo ""
        return 1
    fi

    return 0
}

check_tls_config() {
    # Check if TLS is configured by reading the settings file
    TLS_CONFIG="/config/tls_settings.json"
    TLS_CERT="/config/tls/cert.pem"
    TLS_KEY="/config/tls/key.pem"

    if [ -f "$TLS_CONFIG" ]; then
        # Extract enabled, https_port, and check if certs exist
        TLS_ENABLED=$(python3 -c "import json; print(json.load(open('$TLS_CONFIG')).get('enabled', False))" 2>/dev/null || echo "False")
        HTTPS_PORT=$(python3 -c "import json; print(json.load(open('$TLS_CONFIG')).get('https_port', 6143))" 2>/dev/null || echo "6143")

        if [ "$TLS_ENABLED" = "True" ] && [ -f "$TLS_CERT" ] && [ -f "$TLS_KEY" ]; then
            print_success "TLS enabled with valid certificates"
            print_info "HTTPS will be available on port $HTTPS_PORT"
            return 0  # TLS enabled
        elif [ "$TLS_ENABLED" = "True" ]; then
            print_warning "TLS enabled but certificates not found, starting HTTP only"
            return 1  # TLS configured but certs missing
        fi
    fi

    print_info "TLS not configured, using HTTP only"
    return 1  # TLS not enabled
}

print_startup_info() {
    echo ""
    echo "${GREEN}════════════════════════════════════════════════════════════${NC}"
    echo "${GREEN}  All preflight checks passed!${NC}"
    echo "${GREEN}════════════════════════════════════════════════════════════${NC}"
    echo ""
    print_info "Starting Enhanced Channel Manager..."
    print_info "HTTP Server: http://0.0.0.0:6100 (always available)"
    if [ "$USE_TLS" = "1" ]; then
        print_info "HTTPS Server: https://0.0.0.0:${HTTPS_PORT} (TLS enabled)"
    fi
    print_info "Health Check: http://0.0.0.0:6100/api/health"
    echo ""
}

# Run all preflight checks
run_preflight_checks() {
    print_header

    FAILED=0

    check_python || FAILED=1
    check_filesystem || FAILED=1
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

# Check TLS configuration and set HTTPS_PORT
USE_TLS=0
HTTPS_PORT=6143
TLS_CONFIG="/config/tls_settings.json"
if [ -f "$TLS_CONFIG" ]; then
    HTTPS_PORT=$(python3 -c "import json; print(json.load(open('$TLS_CONFIG')).get('https_port', 6143))" 2>/dev/null || echo "6143")
fi
if check_tls_config; then
    USE_TLS=1
fi

# Switch to non-root user and run the application
cd /app
if [ "$USE_TLS" = "1" ]; then
    # Dual-port mode: HTTP on 6100 (fallback) + HTTPS on configured port
    # Start HTTP server in background (always available as fallback)
    print_info "Starting HTTP fallback server on port 6100..."
    gosu appuser uvicorn main:app --host 0.0.0.0 --port 6100 &
    HTTP_PID=$!

    # Give HTTP server a moment to start
    sleep 1

    # Start HTTPS server as main process (foreground)
    print_info "Starting HTTPS server on port ${HTTPS_PORT}..."
    exec gosu appuser uvicorn main:app --host 0.0.0.0 --port ${HTTPS_PORT} \
        --ssl-keyfile /config/tls/key.pem \
        --ssl-certfile /config/tls/cert.pem
else
    # HTTP-only mode
    exec gosu appuser uvicorn main:app --host 0.0.0.0 --port 6100
fi
