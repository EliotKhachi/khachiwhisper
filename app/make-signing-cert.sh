#!/bin/zsh
# Creates a self-signed "Khachiwhisper Dev" code-signing identity in a dedicated keychain
# (~/Library/Keychains/khachiwhisper-dev.keychain-db) that the build scripts unlock themselves,
# so signing never prompts for your login password.
#
# Why sign with a certificate at all: an ad-hoc signature changes on every build, and macOS then
# silently drops the app's Accessibility grant. A certificate-based signature keeps the same
# designated requirement across rebuilds, so you grant permissions once. Gatekeeper still treats
# the app as from an unidentified developer (that needs a paid Apple Developer ID).
set -e
NAME="Khachiwhisper Dev"
KC="$HOME/Library/Keychains/khachiwhisper-dev.keychain-db"
PW="khachiwhisper-local-signing"   # protects nothing valuable: a self-signed key with no external trust

if [ -f "$KC" ] && security find-identity -p codesigning "$KC" 2>/dev/null | grep -q "\"$NAME\""; then
  echo "identity '$NAME' already exists in $KC"; exit 0
fi
[ -f "$KC" ] || security create-keychain -p "$PW" "$KC"
security set-keychain-settings "$KC"            # never auto-lock
security unlock-keychain -p "$PW" "$KC"

T="$(mktemp -d)"; cd "$T"
openssl req -x509 -newkey rsa:2048 -keyout key.pem -out cert.pem -days 3650 -nodes \
  -subj "/CN=$NAME/O=Khachiwhisper" \
  -addext "keyUsage=critical,digitalSignature" -addext "extendedKeyUsage=critical,codeSigning" \
  -addext "basicConstraints=critical,CA:false" 2>/dev/null
openssl pkcs12 -export -legacy -out dev.p12 -inkey key.pem -in cert.pem -passout pass:x -name "$NAME"
security import dev.p12 -k "$KC" -P x -T /usr/bin/codesign -T /usr/bin/security >/dev/null
# let Apple's tools use the key without a per-use prompt
security set-key-partition-list -S apple-tool:,apple: -s -k "$PW" "$KC" >/dev/null
rm -rf "$T"

# add the keychain to the user search list (keeps the existing ones)
existing=("${(@f)$(security list-keychains -d user | sed 's/^ *"//; s/"$//')}")
security list-keychains -d user -s "${existing[@]}" "$KC"
echo "created identity '$NAME' in $KC"
