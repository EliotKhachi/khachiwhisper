#!/bin/zsh
# Installs dist/Khachiwhisper.app into /Applications and relaunches it.
# Run app/make-signing-cert.sh once so builds are signed with a stable local identity;
# otherwise (ad-hoc) every rebuild invalidates the Accessibility grant and you need --reset.
set -e
DIR="$(cd "$(dirname "$0")" && pwd)"
[ -d "$DIR/dist/Khachiwhisper.app" ] || "$DIR/build-release.sh"
pkill -f "Khachiwhisper.app/Contents/MacOS/Khachiwhisper" 2>/dev/null || true
sleep 1
rm -rf /Applications/Khachiwhisper.app
cp -R "$DIR/dist/Khachiwhisper.app" /Applications/
# With the "Khachiwhisper Dev" signing identity, grants survive rebuilds; pass --reset to force
# a fresh Accessibility prompt (needed once when switching from an ad-hoc build).
if [[ "$1" == "--reset" ]]; then
  tccutil reset Accessibility com.myoboku.khachiwhisper >/dev/null 2>&1 || true
fi
open /Applications/Khachiwhisper.app
echo "Installed and launched."
