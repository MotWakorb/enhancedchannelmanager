#!/bin/bash
# VLC Protocol Handler Setup for macOS
# This script creates an AppleScript app to handle vlc:// URLs
# Run with: chmod +x vlc-protocol-macos.sh && ./vlc-protocol-macos.sh

set -e

# --- Configuration ---
APP_NAME="VLC Handler"
INSTALL_DIR="/Applications"
HANDLER_PATH="$INSTALL_DIR/$APP_NAME.app"
VLC_APP_PATH="/Applications/VLC.app"

echo "=================================================="
echo "      VLC Protocol Handler Installer for macOS"
echo "=================================================="
echo ""

# 1. Check for VLC Media Player
if [ ! -d "$VLC_APP_PATH" ]; then
    echo "[ERROR] VLC is not found in /Applications."
    echo "Please install VLC from https://www.videolan.org/"
    echo "or move it to the Applications folder."
    exit 1
fi
echo "[OK] Found VLC at $VLC_APP_PATH"

# 2. Create the AppleScript Handler
# The script strips vlc://, URL decodes, and fixes the missing colon
SCRIPT_SRC=$(cat <<'EOF'
on open location inputURL
    -- The URL comes in as "vlc://http//..." (note: colon is stripped by macOS)
    -- Remove the "vlc://" prefix (6 characters)
    set cleanURL to text 7 through -1 of inputURL

    -- URL decode the string
    set cleanURL to do shell script "python3 -c \"import sys, urllib.parse; print(urllib.parse.unquote(sys.argv[1]))\" " & quoted form of cleanURL

    -- Fix missing colon after http/https (macOS strips it)
    if cleanURL starts with "http//" then
        set cleanURL to "http:" & text 5 through -1 of cleanURL
    else if cleanURL starts with "https//" then
        set cleanURL to "https:" & text 6 through -1 of cleanURL
    end if

    -- Open VLC with the clean URL
    do shell script "open -a VLC " & quoted form of cleanURL
end open location
EOF
)

echo "Compiling handler application..."
echo "$SCRIPT_SRC" | osacompile -o "$HANDLER_PATH"

if [ ! -d "$HANDLER_PATH" ]; then
    echo "[ERROR] Failed to create the application. Permission denied?"
    echo "Try running with sudo: sudo ./vlc-protocol-macos.sh"
    exit 1
fi
echo "[OK] Handler app created at $HANDLER_PATH"

# 3. Configure Info.plist (Protocol + Background Mode)
PLIST_PATH="$HANDLER_PATH/Contents/Info.plist"

echo "Configuring protocol handler..."

# Insert URL Scheme (vlc://)
plutil -insert CFBundleURLTypes -xml '
<array>
    <dict>
        <key>CFBundleURLName</key>
        <string>VLC Handler Protocol</string>
        <key>CFBundleURLSchemes</key>
        <array>
            <string>vlc</string>
        </array>
    </dict>
</array>
' "$PLIST_PATH"

# Hide from Dock (Run as background agent)
plutil -replace LSUIElement -bool true "$PLIST_PATH"

echo "[OK] Protocol handler configured"

# 4. Icon Customization (Use VLC's icon)
echo "Applying VLC icon to the handler..."

SOURCE_ICON="$VLC_APP_PATH/Contents/Resources/vlc.icns"
DEST_ICON="$HANDLER_PATH/Contents/Resources/applet.icns"

if [ -f "$SOURCE_ICON" ]; then
    cp "$SOURCE_ICON" "$DEST_ICON"
    echo "[OK] Icon copied"
else
    # Fallback: try finding any .icns
    FALLBACK_ICON=$(find "$VLC_APP_PATH/Contents/Resources" -name "*.icns" -maxdepth 1 | head -n 1)
    if [ -n "$FALLBACK_ICON" ]; then
        cp "$FALLBACK_ICON" "$DEST_ICON"
        echo "[OK] Icon copied (using fallback)"
    else
        echo "[WARNING] Could not find VLC icon. Keeping default icon."
    fi
fi

# 5. Final Registration
echo "Registering with Launch Services..."
/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister -f "$HANDLER_PATH"

# Touch the app to force Finder to refresh the icon cache
touch "$HANDLER_PATH"

echo ""
echo "=================================================="
echo "           Installation Complete!"
echo "=================================================="
echo ""
echo "Files created:"
echo "  $HANDLER_PATH"
echo ""
echo "You can now use vlc:// links in your browser."
echo "Your browser may ask for permission the first time."
echo ""
echo "Note: If the icon doesn't appear immediately, restart Finder."
echo ""
