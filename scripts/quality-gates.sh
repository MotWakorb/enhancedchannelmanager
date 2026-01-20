#!/bin/bash
#
# Quality Gates Script
#
# Runs all quality checks before committing code:
# - Backend: Python syntax check and pytest with coverage
# - Frontend: TypeScript compilation and vitest
# - E2E: Playwright tests (always run)
#
# Usage:
#   ./scripts/quality-gates.sh          # Run all checks including E2E
#   SKIP_E2E=1 ./scripts/quality-gates.sh  # Skip E2E tests (use sparingly)
#
# Exit codes:
#   0 - All checks passed
#   1 - One or more checks failed
#

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Track failures
FAILED=0

# Helper function to print status
print_status() {
    if [ "$2" = "success" ]; then
        echo -e "${GREEN}✓${NC} $1"
    elif [ "$2" = "fail" ]; then
        echo -e "${RED}✗${NC} $1"
    elif [ "$2" = "skip" ]; then
        echo -e "${YELLOW}○${NC} $1 (skipped)"
    else
        echo -e "${BLUE}→${NC} $1"
    fi
}

# Helper function to run a check
run_check() {
    local name="$1"
    local command="$2"

    print_status "$name" "running"
    if eval "$command"; then
        print_status "$name" "success"
        return 0
    else
        print_status "$name" "fail"
        FAILED=1
        return 1
    fi
}

echo ""
echo "=========================================="
echo "    Quality Gates - Pre-Commit Checks    "
echo "=========================================="
echo ""

# Get project root (directory containing this script's parent)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

cd "$PROJECT_ROOT"

# -----------------------------------------------------------------------------
# Backend Checks
# -----------------------------------------------------------------------------

echo -e "${BLUE}Backend Checks${NC}"
echo "--------------------------------------------"

# Python syntax check
print_status "Python syntax check (main.py)" "running"
if python -m py_compile backend/main.py 2>/dev/null; then
    print_status "Python syntax check (main.py)" "success"
else
    print_status "Python syntax check (main.py)" "fail"
    FAILED=1
fi

# Python tests (if pytest is available and tests exist)
if command -v pytest &> /dev/null && [ -d "backend/tests" ]; then
    if [ -n "$(find backend/tests -name 'test_*.py' -type f 2>/dev/null)" ]; then
        print_status "Backend unit tests (pytest)" "running"
        if cd backend && pytest tests/ -q --tb=short 2>/dev/null; then
            print_status "Backend unit tests (pytest)" "success"
        else
            print_status "Backend unit tests (pytest)" "fail"
            FAILED=1
        fi
        cd "$PROJECT_ROOT"
    else
        print_status "Backend unit tests (pytest)" "skip"
    fi
else
    print_status "Backend unit tests (pytest)" "skip"
fi

echo ""

# -----------------------------------------------------------------------------
# Frontend Checks
# -----------------------------------------------------------------------------

echo -e "${BLUE}Frontend Checks${NC}"
echo "--------------------------------------------"

# Check if node_modules exists
if [ ! -d "frontend/node_modules" ]; then
    print_status "Installing frontend dependencies" "running"
    if cd frontend && npm install --silent; then
        print_status "Installing frontend dependencies" "success"
    else
        print_status "Installing frontend dependencies" "fail"
        FAILED=1
    fi
    cd "$PROJECT_ROOT"
fi

# TypeScript compilation and build
print_status "Frontend build (vite build)" "running"
if cd frontend && npm run build --silent 2>/dev/null; then
    print_status "Frontend build (vite build)" "success"
else
    print_status "Frontend build (vite build)" "fail"
    FAILED=1
fi
cd "$PROJECT_ROOT"

# Frontend unit tests (if vitest is configured and tests exist)
if [ -f "frontend/vitest.config.ts" ]; then
    if [ -n "$(find frontend/src -name '*.test.ts' -o -name '*.test.tsx' -type f 2>/dev/null)" ]; then
        print_status "Frontend unit tests (vitest)" "running"
        if cd frontend && npm run test --silent 2>/dev/null; then
            print_status "Frontend unit tests (vitest)" "success"
        else
            print_status "Frontend unit tests (vitest)" "fail"
            FAILED=1
        fi
        cd "$PROJECT_ROOT"
    else
        print_status "Frontend unit tests (vitest)" "skip"
    fi
else
    print_status "Frontend unit tests (vitest)" "skip"
fi

echo ""

# -----------------------------------------------------------------------------
# E2E Tests
# -----------------------------------------------------------------------------

echo -e "${BLUE}E2E Tests${NC}"
echo "--------------------------------------------"

if [ "${SKIP_E2E:-0}" = "1" ]; then
    echo -e "${YELLOW}○${NC} E2E tests skipped (SKIP_E2E=1 set)"
else
    if [ -f "playwright.config.ts" ]; then
        if [ -n "$(find e2e -name '*.spec.ts' -type f 2>/dev/null)" ]; then
            print_status "E2E tests (Playwright)" "running"
            if npm run test:e2e --silent 2>/dev/null; then
                print_status "E2E tests (Playwright)" "success"
            else
                print_status "E2E tests (Playwright)" "fail"
                FAILED=1
            fi
        else
            print_status "E2E tests (Playwright)" "skip"
        fi
    else
        print_status "E2E tests (Playwright)" "skip"
    fi
fi

echo ""

# -----------------------------------------------------------------------------
# Summary
# -----------------------------------------------------------------------------

echo "=========================================="
if [ $FAILED -eq 0 ]; then
    echo -e "${GREEN}  All quality gates passed!${NC}"
else
    echo -e "${RED}  Some quality gates failed!${NC}"
fi
echo "=========================================="
echo ""

exit $FAILED
