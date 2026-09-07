# Khachiwhisper

Local, hotkey-driven voice-to-text for macOS. A SuperWhisper clone you own forever.

- **tap right ⌘** (default) — open the floating waveform panel and start recording
- **tap right ⌘** again — stop, transcribe on-device, paste the text at your cursor
- **esc** — cancel

The shortcut is yours to pick: in Settings › General click the shortcut and press what you
want — a modifier key tapped on its own (right ⌘, right ⌥, fn…), a function key, or any
combination such as ⌃⇧D or ⌥Space. A tapped modifier keeps working in normal shortcuts like
right ⌘ + C.

Nothing leaves the machine. The model stays loaded in memory, so a typical
sentence transcribes in well under a second on an M4.

## Install (for anyone with an Apple Silicon Mac)

1. Download the latest `Khachiwhisper-<version>.dmg` from [Releases](https://github.com/EliotKhachi/khachiwhisper/releases), open it, drag **Khachiwhisper** to Applications.
2. Launch it. Because the app isn't notarized, macOS will refuse the first time. Go to
   System Settings > Privacy & Security, scroll down, and click **Open Anyway** next to
   Khachiwhisper (or run `xattr -dr com.apple.quarantine /Applications/Khachiwhisper.app`).
3. A 🎙 icon appears in the menu bar and the **Settings window** opens. It downloads
   Cohere Transcribe (about 2.4 GB) with a progress bar, and shows the two permissions you need to
   grant: **Accessibility** (hotkey + paste) and **Microphone**. Click Grant / Allow next to each.
4. Tap the right ⌘ key, talk, tap again. Text lands at your cursor.

The Settings window (menu bar 🎙 > Settings, or just open the app again) has four pages:

- **General**: shortcut recorder (click, then press the keys), language, start at login
- **Models**: an "Active model" dropdown plus the list of the four models with download buttons
  and progress. Picking a model that isn't downloaded yet downloads it first.
  - Cohere Transcribe (2.4 GB) — best accuracy, 14 languages, the default
  - Whisper large-v3-turbo (1.6 GB) — 99 languages including Persian, fast
  - Whisper large-v3 (3.1 GB) — 99 languages, slower, slightly more accurate
  - Parakeet TDT 0.6B v3 (2.5 GB) — fastest, English + 24 European languages
- **Permissions**: Accessibility and Microphone status with Grant / Allow buttons
- **About**: version, and buttons for the model cache, config file, and log

Models are cached in `~/.cache/huggingface/hub/`; settings in `~/Library/Application Support/Khachiwhisper/`.

Requirements: Apple Silicon, macOS 14 or newer, ~3 GB disk for the model, 16 GB RAM
recommended (8 GB works, the model uses about 2.6 GB while resident).

## Design

The interface is drawn with **Hush**, Khachiwhisper's own design system (`hush.py`), not with
AppKit's default controls. Deep ink surfaces that get lighter as they come forward, warm off-white
type, one coral accent for anything live or interactive (recording dot, selected page, primary
buttons, the shortcut recorder while it listens), mint for "ready". Every control — buttons,
dropdown, toggle, keycaps, pills, progress bar, sidebar items, even the window's close and
minimise buttons — is a small hand-drawn view built from the same tokens, so the app looks the
same on every macOS version and in both system themes. The floating dictation panel and the
menu bar glyph use the same palette.

## Layout

| File | Purpose |
|---|---|
| `khachiwhisper.py` | the app: settings window, overlay panel, hotkey tap, mic capture, models, paste |
| `hush.py` | the Hush design system: tokens and hand-drawn components |
| `config.json` | backend / language / mic settings (see below) |
| `Khachiwhisper.app` | dev launcher bundle (built by `build-app.sh`) that runs the repo's `.venv` |
| `build-release.sh` | builds the self-contained app + DMG + zip in `dist/` (bundled Python, no Homebrew needed) |
| `app/` | launcher source, Info.plist, icon |
| `run.sh` | run in the foreground for testing |
| `setup.sh` | one-shot install on a fresh Mac (Python, venv, model download) |
| `install-launchd.sh` | install as a login item that stays running |
| `khachiwhisper.log` | runtime log (timings, transcripts) |

## Developing

Clone the repo and run `./setup.sh` (needs Homebrew). `./run.sh` runs the dev build from the
repo's `.venv`; `./build-release.sh` produces the distributable app.

## Dev build

```bash
./run.sh                     # foreground, logs to khachiwhisper.log in the repo
./install-launchd.sh         # optional: keep the dev build running via launchd
./khachiwhisper.py --render panel.png   # draw the overlay to a PNG, no permissions needed
./khachiwhisper.py --demo 5             # show the overlay with fake levels for 5s
```

The dev build reads `config.json` from the repo; the release app uses
`~/Library/Application Support/Khachiwhisper/`. Both show up as "Khachiwhisper" in
Privacy & Security, but they are separate entries.

## Config

Everything in the Settings window is stored in `config.json`. A few extra knobs live only there:

```json
{
  "hotkey": {"kind": "tap", "keycode": 54},   // or {"kind": "combo", "keycode": 2, "mods": ["ctrl","shift"], "key": "D"}
  "model": "cohere",        // cohere | whisper-turbo | whisper-v3 | parakeet
  "language": "en",
  "input_device": null,     // null = default mic, or a substring like "EarPods"
  "min_seconds": 0.4,       // drop recordings shorter than this
  "silence_rms": 0.004,     // drop recordings quieter than this
  "restore_clipboard": true // put your previous clipboard back after pasting
}
```

## Models and dependencies

All four run in pure MLX. Whisper (`mlx-whisper`) and Parakeet (`parakeet-mlx`) declare heavy
dependencies they only need for conversion or word timestamps (PyTorch, numba, scipy, librosa),
so they are installed with `--no-deps` and `shims/` supplies the three tiny pieces they import at
load time. The librosa mel filterbank shim is numerically identical to librosa's for the settings
Parakeet uses.

## Installing your own build

```bash
./app/make-signing-cert.sh      # once: a local signing identity in its own keychain
./build-release.sh && ./install-local.sh
```

Why the signing identity: an ad-hoc signature changes on every build, and macOS then silently
drops the app's Accessibility grant (the toggle in System Settings still shows on). Signing with
a certificate keeps the app's identity stable, so you grant permissions once and rebuilds keep
them. The identity is self-signed and lives in `~/Library/Keychains/khachiwhisper-dev.keychain-db`,
which the build script unlocks itself, so you're never asked for your login password. Gatekeeper
still treats the app as unidentified; only a paid Developer ID changes that.

If a grant ever looks on in System Settings but the app says "Not granted" (e.g. after switching
from an ad-hoc build), run `./install-local.sh --reset` and grant it again once.

## Releasing

```bash
./build-release.sh                       # dist/Khachiwhisper-1.0.dmg
gh release create v1.0 dist/Khachiwhisper-1.0.dmg --title "Khachiwhisper 1.0"
```

Bump `CFBundleShortVersionString` in `app/Info.plist` for each release. The app is ad-hoc signed;
proper Developer ID signing + notarization (Apple developer account, $99/yr) would remove the
"Open Anyway" step for users.

## License

MIT. The bundled models keep their own licenses: Cohere Transcribe and Whisper are Apache 2.0, Parakeet is CC-BY-4.0.
