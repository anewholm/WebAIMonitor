#!/usr/bin/env bash
# WebAI WhatsApp Monitor — one-time setup
# Run this once before starting the daemon for the first time.
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
MCP_PROFILE="$HOME/.cache/ms-playwright/mcp-firefox-7e45e33"
DEST_PROFILE="$SCRIPT_DIR/.browser_profile"
SERVICE_DIR="$HOME/.config/systemd/user"
SERVICE_FILE="$SERVICE_DIR/webaimonitor.service"

echo "=== WebAI Monitor Setup ==="
echo ""

# 1. Install Python dependencies
echo ">> Installing Python dependencies..."
python3 -m pip install --user -r "$SCRIPT_DIR/requirements.txt"
echo ""

# 2. Install Playwright Firefox browser (skips if already present)
echo ">> Installing Playwright Firefox browser..."
python3 -m playwright install firefox
echo ""

# 3. Copy MCP Firefox profile (carries the WhatsApp Web session cookie)
if [ -d "$DEST_PROFILE" ]; then
    echo ">> .browser_profile/ already exists — skipping copy."
    echo "   (Delete it and re-run install.sh if you need to reset the session.)"
elif [ -d "$MCP_PROFILE" ]; then
    echo ">> Copying MCP Firefox profile to .browser_profile/ ..."
    cp -r "$MCP_PROFILE" "$DEST_PROFILE"
    echo "   Done. WhatsApp Web session cookies copied."
else
    echo ">> WARNING: MCP Firefox profile not found at $MCP_PROFILE"
    echo "   The daemon will create an empty profile. You will need to scan"
    echo "   a WhatsApp QR code on first run."
    echo "   To do this: run monitor.py once with headless=False, scan QR,"
    echo "   then restart normally."
fi
echo ""

# 4. Install systemd user service
echo ">> Installing systemd user service..."
mkdir -p "$SERVICE_DIR"
cat > "$SERVICE_FILE" << EOF
[Unit]
Description=WebAI WhatsApp Monitor
After=network-online.target
Wants=network-online.target

[Service]
WorkingDirectory=$SCRIPT_DIR
ExecStart=/usr/bin/python3 $SCRIPT_DIR/monitor.py
Restart=always
RestartSec=30
Environment=PATH=/usr/local/bin:/usr/bin:/bin
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=default.target
EOF

systemctl --user daemon-reload
systemctl --user enable webaimonitor.service
echo "   Service installed and enabled."
echo ""

echo "=== Setup complete ==="
echo ""
echo "Test run (interactive, Ctrl+C to stop):"
echo "  python3 $SCRIPT_DIR/monitor.py"
echo ""
echo "Start as background service:"
echo "  systemctl --user start webaimonitor"
echo ""
echo "View logs:"
echo "  journalctl --user -u webaimonitor -f"
echo "  tail -f $SCRIPT_DIR/monitor.log"
