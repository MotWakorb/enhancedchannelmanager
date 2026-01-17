#!/bin/bash
echo "=== Checking Docker Container ==="
echo ""
echo "1. Container GIT_COMMIT environment variable:"
docker exec ecm printenv GIT_COMMIT
echo ""
echo "2. First 50 lines of main.py to check for DeleteOrphanedGroupsRequest:"
docker exec ecm head -50 main.py | grep -A 10 "class DeleteOrphanedGroupsRequest"
echo ""
echo "3. Line 1278-1290 of main.py (the endpoint definition):"
docker exec ecm sed -n '1278,1290p' main.py
echo ""
echo "4. Check if debug logging exists in main.py:"
docker exec ecm grep -n "\[DELETE-ORPHANED\]" main.py | head -5
echo ""
echo "5. Check actual Python file modification time:"
docker exec ecm stat -c '%y %n' main.py
