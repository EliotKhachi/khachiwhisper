#!/bin/zsh
# One-shot setup for a fresh Mac (Apple Silicon only).
# Installs Homebrew Python 3.13 if missing, builds the venv, downloads the Cohere model.
set -e
DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DIR"

if ! command -v brew >/dev/null; then
  echo "Homebrew is required: https://brew.sh"; exit 1
fi
PY=/opt/homebrew/opt/python@3.13/bin/python3.13
[ -x "$PY" ] || brew install python@3.13

[ -d .venv ] || "$PY" -m venv .venv
./.venv/bin/pip install -q --upgrade pip
./.venv/bin/pip install -q mlx-speech sounddevice numpy pynput tqdm \
  pyobjc-framework-Cocoa pyobjc-framework-Quartz pyobjc-framework-ServiceManagement pyobjc-framework-AVFoundation
./.venv/bin/pip install -q --no-deps mlx-whisper parakeet-mlx
./.venv/bin/pip install -q tiktoken more-itertools dacite

echo "(The app downloads Cohere Transcribe, ~2.4 GB, on first launch.)"

chmod +x run.sh install-launchd.sh build-app.sh
./build-app.sh

echo
echo "Done. Start it with:  $DIR/run.sh"
echo "Then grant Khachiwhisper Accessibility + Microphone access when macOS asks."
echo "To run at login:      $DIR/install-launchd.sh"
