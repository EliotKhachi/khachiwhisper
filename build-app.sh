#!/bin/zsh
# Builds Khachiwhisper.app (a tiny launcher around khachiwhisper.py) and ad-hoc signs it.
set -e
DIR="$(cd "$(dirname "$0")" && pwd)"
# Sign with the local "Khachiwhisper Dev" identity when present (see app/make-signing-cert.sh),
# otherwise ad-hoc. A stable identity keeps macOS permission grants across rebuilds.
SIGN="-"; SIGN_ARGS=()
KC="$HOME/Library/Keychains/khachiwhisper-dev.keychain-db"
if [ -f "$KC" ] && security find-identity -p codesigning "$KC" 2>/dev/null | grep -q '"Khachiwhisper Dev"'; then
  security unlock-keychain -p "khachiwhisper-local-signing" "$KC" 2>/dev/null || true
  SIGN="Khachiwhisper Dev"; SIGN_ARGS=(--keychain "$KC")
fi
APP="$DIR/Khachiwhisper.app"
rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"
cc -O2 -Wall -fobjc-arc -framework Cocoa -o "$APP/Contents/MacOS/Khachiwhisper" "$DIR/app/launcher.m"
cp "$DIR/app/Info.plist" "$APP/Contents/"
[ -f "$DIR/app/Khachiwhisper.icns" ] && cp "$DIR/app/Khachiwhisper.icns" "$APP/Contents/Resources/"
codesign --force --sign "$SIGN" "${SIGN_ARGS[@]}" --identifier com.myoboku.khachiwhisper "$APP"
echo "built $APP"
