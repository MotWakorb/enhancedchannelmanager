#!/bin/bash
# VLC Protocol Handler Setup for Ubuntu/Linux
# This script creates a .desktop file to handle vlc:// URLs
# Run with: chmod +x vlc-protocol-linux.sh && ./vlc-protocol-linux.sh

set -e

echo "=== VLC Protocol Handler Setup for Linux ==="
echo ""

# Check if VLC is installed
VLC_PATH=$(which vlc 2>/dev/null || true)

if [ -z "$VLC_PATH" ]; then
    echo "ERROR: VLC is not installed or not in PATH."
    echo ""
    echo "Install VLC using one of these commands:"
    echo "  Ubuntu/Debian: sudo apt install vlc"
    echo "  Fedora:        sudo dnf install vlc"
    echo "  Arch:          sudo pacman -S vlc"
    echo ""
    exit 1
fi

echo "Found VLC at: $VLC_PATH"
echo ""

# Create applications directory if it doesn't exist
APPLICATIONS_DIR="$HOME/.local/share/applications"
mkdir -p "$APPLICATIONS_DIR"

# Create a wrapper script that strips the vlc:// prefix
WRAPPER_SCRIPT="$HOME/.local/bin/vlc-url-handler"
mkdir -p "$HOME/.local/bin"

echo "Creating wrapper script: $WRAPPER_SCRIPT"

cat > "$WRAPPER_SCRIPT" << 'EOF'
#!/bin/bash
# Strip the vlc:// prefix
url="${1#vlc://}"

# URL decode (browsers encode special characters)
url=$(printf '%b' "${url//%/\\x}")

# Fix missing colon - Linux strips it from http:// and https://
url="${url/#http\/\//http://}"
url="${url/#https\/\//https://}"

exec vlc "$url"
EOF

chmod +x "$WRAPPER_SCRIPT"

# Create the .desktop file for vlc:// protocol
DESKTOP_FILE="$APPLICATIONS_DIR/vlc-protocol.desktop"

echo "Creating desktop entry: $DESKTOP_FILE"

cat > "$DESKTOP_FILE" << EOF
[Desktop Entry]
Name=VLC Protocol Handler
Comment=Handle vlc:// URLs
Exec=$WRAPPER_SCRIPT %u
Terminal=false
Type=Application
MimeType=x-scheme-handler/vlc;
NoDisplay=true
Categories=AudioVideo;Player;Video;
EOF

echo "Desktop entry created."
echo ""

# Update the MIME database
echo "Registering protocol handler..."

# Set VLC as the default handler for vlc:// URLs
xdg-mime default vlc-protocol.desktop x-scheme-handler/vlc

# Update desktop database
if command -v update-desktop-database &> /dev/null; then
    update-desktop-database "$APPLICATIONS_DIR" 2>/dev/null || true
fi

echo ""
echo "SUCCESS! VLC protocol handler registered."
echo ""
echo "Files created:"
echo "  $WRAPPER_SCRIPT"
echo "  $DESKTOP_FILE"
echo ""
echo "You can now use vlc:// links in your browser."
echo "Your browser may ask for permission the first time."
echo ""

# Verify registration
echo "Verifying registration..."
HANDLER=$(xdg-mime query default x-scheme-handler/vlc 2>/dev/null || true)
if [ "$HANDLER" = "vlc-protocol.desktop" ]; then
    echo "Verification: OK - vlc:// is handled by vlc-protocol.desktop"
else
    echo "Warning: Registration may not have completed successfully."
    echo "Current handler: $HANDLER"
    echo ""
    echo "Try running: xdg-mime default vlc-protocol.desktop x-scheme-handler/vlc"
fi

echo ""
echo "Done!"
