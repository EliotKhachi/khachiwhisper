#!/bin/zsh
# Builds a self-contained, relocatable Khachiwhisper.app (bundled Python + deps,
# no Homebrew needed on the target Mac) and packages it as a DMG and a zip.
#
#   ./build-release.sh            -> dist/Khachiwhisper.app, dist/Khachiwhisper-<ver>.dmg, .zip
#
# No speech model is bundled; the app downloads Cohere Transcribe (~2.4 GB) on first launch
# and the others on demand from the Settings window.
set -e
setopt null_glob
DIR="$(cd "$(dirname "$0")" && pwd)"
# Sign with the local "Khachiwhisper Dev" identity when present (see app/make-signing-cert.sh),
# otherwise ad-hoc. A stable identity keeps macOS permission grants across rebuilds.
SIGN="-"; SIGN_ARGS=()
KC="$HOME/Library/Keychains/khachiwhisper-dev.keychain-db"
if [ -f "$KC" ] && security find-identity -p codesigning "$KC" 2>/dev/null | grep -q '"Khachiwhisper Dev"'; then
  security unlock-keychain -p "khachiwhisper-local-signing" "$KC" 2>/dev/null || true
  SIGN="Khachiwhisper Dev"; SIGN_ARGS=(--keychain "$KC")
fi
cd "$DIR"
VERSION="$(sed -n 's/.*CFBundleShortVersionString<\/key><string>\([^<]*\)<\/string>.*/\1/p' app/Info.plist)"
PY_URL="https://github.com/astral-sh/python-build-standalone/releases/download/20260901/cpython-3.13.15%2B20260901-aarch64-apple-darwin-install_only.tar.gz"
CACHE="$DIR/app/cache"; mkdir -p "$CACHE"
TARBALL="$CACHE/cpython-3.13-aarch64.tar.gz"
DIST="$DIR/dist"; APP="$DIST/Khachiwhisper.app"
RES="$APP/Contents/Resources"

[ -f "$TARBALL" ] || { echo "Downloading relocatable CPython…"; curl -L -o "$TARBALL" "$PY_URL"; }

rm -rf "$APP"; mkdir -p "$APP/Contents/MacOS" "$RES"
echo "Unpacking Python…"
tar xzf "$TARBALL" -C "$RES"          # -> Resources/python
PY="$RES/python/bin/python3"

echo "Installing dependencies…"
"$PY" -m pip install -q --no-cache-dir --upgrade pip
"$PY" -m pip install -q --no-cache-dir \
  mlx-speech sounddevice numpy pynput tqdm \
  pyobjc-framework-Cocoa pyobjc-framework-Quartz pyobjc-framework-ServiceManagement pyobjc-framework-AVFoundation
# Whisper + Parakeet without their heavy declared deps (torch, numba, scipy, librosa, typer);
# shims/ provides the tiny pieces they import at load time.
"$PY" -m pip install -q --no-cache-dir --no-deps mlx-whisper parakeet-mlx
"$PY" -m pip install -q --no-cache-dir tiktoken more-itertools dacite

echo "Pruning…"
SP="$RES/python/lib/python3.13/site-packages"
rm -rf "$SP/PyObjCTest" "$SP/pip" "$SP/setuptools" "$SP"/pip-* "$SP"/setuptools-* \
       "$RES/python/lib/python3.13/test" "$RES/python/lib/python3.13/idlelib" \
       "$RES/python/lib/python3.13/tkinter" "$RES/python/lib/python3.13/turtledemo" \
       "$RES/python/lib/python3.13/ensurepip" "$RES/python/share" "$RES/python/include"
find "$RES/python" -name "__pycache__" -type d -prune -exec rm -rf {} +
find "$RES/python" -name "*.dist-info" -type d -exec rm -rf {}/RECORD \; 2>/dev/null || true
find "$SP" -type d \( -name tests -o -name test \) -prune -exec rm -rf {} + 2>/dev/null || true

cp khachiwhisper.py hush.py "$RES/"
cp -R shims "$RES/shims"
cc -O2 -Wall -fobjc-arc -framework Cocoa -o "$APP/Contents/MacOS/Khachiwhisper" app/launcher.m
cp app/Info.plist "$APP/Contents/"
cp app/Khachiwhisper.icns "$RES/"
echo "APPL????" > "$APP/Contents/PkgInfo"

echo "Precompiling (so Python never needs to write into the signed bundle)…"
"$PY" -m compileall -q "$RES/python/lib/python3.13" "$RES/khachiwhisper.py" "$RES/hush.py" "$RES/shims" >/dev/null 2>&1 || true

echo "Signing ($SIGN)…"
find "$RES/python" -type f \( -name "*.so" -o -name "*.dylib" -o -perm -u+x \) -print0 \
  | xargs -0 -n 50 codesign --force --sign "$SIGN" "${SIGN_ARGS[@]}" 2>/dev/null || true
codesign --force --sign "$SIGN" "${SIGN_ARGS[@]}" --identifier com.myoboku.khachiwhisper "$APP"
codesign --verify --deep "$APP" && echo "signature ok"

echo "Packaging…"
cd "$DIST"
rm -rf dmgroot "Khachiwhisper-$VERSION.dmg" "Khachiwhisper-$VERSION.zip"
mkdir dmgroot && cp -R Khachiwhisper.app dmgroot/ && ln -s /Applications dmgroot/Applications
hdiutil create -quiet -volname "Khachiwhisper" -srcfolder dmgroot -ov -format UDZO "Khachiwhisper-$VERSION.dmg"
rm -rf dmgroot
ditto -c -k --keepParent Khachiwhisper.app "Khachiwhisper-$VERSION.zip"
echo
du -sh Khachiwhisper.app "Khachiwhisper-$VERSION.dmg" "Khachiwhisper-$VERSION.zip"
