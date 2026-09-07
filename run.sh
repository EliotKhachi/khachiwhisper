#!/bin/zsh
# Run Khachiwhisper in the foreground (logs to stdout + khachiwhisper.log).
# Goes through Khachiwhisper.app so macOS attributes permissions to "Khachiwhisper".
cd "$(dirname "$0")"
[ -x Khachiwhisper.app/Contents/MacOS/Khachiwhisper ] || ./build-app.sh
exec ./Khachiwhisper.app/Contents/MacOS/Khachiwhisper "$@"
