#!/bin/bash

# Search for a stream by name in Dispatcharr
# Usage: ./search-stream.sh <url> <username> <password> <channel_name>

set -e

# Check arguments
if [ $# -ne 4 ]; then
    echo "Usage: $0 <url> <username> <password> <channel_name>"
    echo ""
    echo "Example:"
    echo "  $0 http://172.16.0.20:9191 admin mypass 'HBO Max HD'"
    echo "  $0 http://172.16.0.20:9191 admin mypass '[PPV EVENT 38]'"
    exit 1
fi

URL="$1"
USERNAME="$2"
PASSWORD="$3"
CHANNEL_NAME="$4"

# Get authentication token
echo "Authenticating..."
TOKEN=$(curl -s -X POST "${URL}/api/accounts/token/" \
  -H "Content-Type: application/json" \
  -d "{\"username\": \"${USERNAME}\", \"password\": \"${PASSWORD}\"}" | jq -r '.access')

if [ -z "$TOKEN" ] || [ "$TOKEN" = "null" ]; then
    echo "Error: Failed to authenticate. Check your username and password."
    exit 1
fi

echo "Token received: ${TOKEN:0:20}..."
echo ""
echo "Searching for channel: $CHANNEL_NAME"
echo ""

# Search for the stream
curl -s --get "${URL}/api/channels/streams/" \
  --data-urlencode "search=${CHANNEL_NAME}" \
  -H "Authorization: Bearer ${TOKEN}" | jq '.'
