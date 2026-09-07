#!/bin/zsh
# Installs Khachiwhisper as a launchd user agent so it starts at login and stays running.
set -e
DIR="$(cd "$(dirname "$0")" && pwd)"
LABEL="com.myoboku.khachiwhisper"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"

[ -x "$DIR/Khachiwhisper.app/Contents/MacOS/Khachiwhisper" ] || "$DIR/build-app.sh"
mkdir -p "$HOME/Library/LaunchAgents"
cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key>
  <array>
    <string>$DIR/Khachiwhisper.app/Contents/MacOS/Khachiwhisper</string>
  </array>
  <key>WorkingDirectory</key><string>$DIR</string>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>StandardOutPath</key><string>$DIR/launchd.out.log</string>
  <key>StandardErrorPath</key><string>$DIR/launchd.err.log</string>
  <key>EnvironmentVariables</key>
  <dict>
    <key>PATH</key><string>/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin</string>
  </dict>
</dict>
</plist>
EOF

launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PLIST"
echo "Installed and started $LABEL"
echo "  stop:    launchctl bootout gui/$(id -u)/$LABEL"
echo "  restart: launchctl kickstart -k gui/$(id -u)/$LABEL"
echo "  logs:    tail -f $DIR/khachiwhisper.log"
