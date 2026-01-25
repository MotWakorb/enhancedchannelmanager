#!/bin/bash
# CSS Visual Smoke Test
# Quick visual sanity check after CSS changes (~30 seconds vs full suite)
#
# Usage:
#   ./scripts/css-smoke-test.sh              # Run smoke tests
#   ./scripts/css-smoke-test.sh --update     # Update baseline screenshots

set -e

cd "$(dirname "$0")/.."

echo "=========================================="
echo "CSS Visual Smoke Tests"
echo "=========================================="
echo ""

# Check for update flag
UPDATE_FLAG=""
if [ "$1" = "--update" ] || [ "$1" = "-u" ]; then
  UPDATE_FLAG="--update-snapshots"
  echo "Mode: Updating baseline screenshots"
else
  echo "Mode: Comparing against baselines"
fi
echo ""

# Run the CSS smoke tests
echo "Running CSS smoke tests..."
echo ""

if npx playwright test e2e/css-smoke.spec.ts --reporter=list $UPDATE_FLAG; then
  echo ""
  echo "=========================================="
  echo "✓ CSS smoke tests passed"
  echo "=========================================="
  exit 0
else
  echo ""
  echo "=========================================="
  echo "✗ Visual differences detected!"
  echo "=========================================="
  echo ""
  echo "To view differences:"
  echo "  npx playwright show-report"
  echo ""
  echo "To update baselines (if changes are intentional):"
  echo "  ./scripts/css-smoke-test.sh --update"
  echo ""
  exit 1
fi
