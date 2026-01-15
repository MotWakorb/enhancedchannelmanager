#!/bin/bash
set -e

# Colors for output
GREEN='\033[0;32m'
RED='\033[0;31m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Unicode symbols
CHECK_MARK="✓"
CROSS_MARK="✗"
ARROW="→"

echo ""
echo -e "${BLUE}════════════════════════════════════════════════════════════${NC}"
echo -e "${BLUE}  Enhanced Channel Manager - Quality Gates${NC}"
echo -e "${BLUE}════════════════════════════════════════════════════════════${NC}"
echo ""

FAILED=0

# Backend Python Syntax Check
echo -e "${BLUE}${ARROW}${NC} Checking Python syntax..."
if python -m py_compile backend/main.py 2>/dev/null; then
    echo -e "${GREEN}${CHECK_MARK}${NC} Python syntax check passed"
else
    echo -e "${RED}${CROSS_MARK}${NC} Python syntax check FAILED"
    python -m py_compile backend/main.py
    FAILED=1
fi

# Frontend TypeScript Compilation and Build
echo -e "${BLUE}${ARROW}${NC} Building frontend (TypeScript + Vite)..."
if cd frontend && npm run build >/dev/null 2>&1; then
    echo -e "${GREEN}${CHECK_MARK}${NC} Frontend build passed"
    cd ..
else
    echo -e "${RED}${CROSS_MARK}${NC} Frontend build FAILED"
    cd frontend
    npm run build
    FAILED=1
fi

echo ""
if [ $FAILED -eq 0 ]; then
    echo -e "${GREEN}════════════════════════════════════════════════════════════${NC}"
    echo -e "${GREEN}  All quality gates passed! ✓${NC}"
    echo -e "${GREEN}════════════════════════════════════════════════════════════${NC}"
    echo ""
    exit 0
else
    echo -e "${RED}════════════════════════════════════════════════════════════${NC}"
    echo -e "${RED}  Quality gates FAILED! Fix errors before committing.${NC}"
    echo -e "${RED}════════════════════════════════════════════════════════════${NC}"
    echo ""
    exit 1
fi
