#!/usr/bin/env python3
"""
Khachiwhisper — local, hotkey-driven voice-to-text for macOS.

Tap the hotkey  -> floating waveform panel opens, mic starts recording.
Tap it again    -> panel closes, audio is transcribed on this Mac, text is pasted at the cursor.
Press esc       -> cancel the current recording.

One process: AppKit (PyObjC) for the menu bar, the settings window and the floating panel;
a pynput event tap for the global hotkey; sounddevice for the mic; MLX for the models.
"""

import json
import os
import signal
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
import objc
import sounddevice as sd
from AppKit import (
    NSApp,
    NSApplication,
    NSApplicationActivationPolicyAccessory,
    NSAnimationContext,
    NSAppearance,
    NSAppearanceNameVibrantDark,
    NSBackingStoreBuffered,
    NSBezierPath,
    NSButton,
    NSColor,
    NSFloatingWindowLevel,
    NSFont,
    NSFontWeightBold,
    NSFontWeightMedium,
    NSFontWeightRegular,
    NSFontWeightSemibold,
    NSImage,
    NSMenu,
    NSMenuItem,
    NSPanel,
    NSPasteboard,
    NSPasteboardTypeString,
    NSPopUpButton,
    NSProgressIndicator,
    NSScreen,
    NSStatusBar,
    NSTextField,
    NSTimer,
    NSVariableStatusItemLength,
    NSView,
    NSVisualEffectBlendingModeBehindWindow,
    NSVisualEffectMaterialHUDWindow,
    NSVisualEffectStateActive,
    NSVisualEffectView,
    NSWindow,
    NSWindowAnimationBehaviorNone,
    NSWindowCollectionBehaviorCanJoinAllSpaces,
    NSWindowCollectionBehaviorFullScreenAuxiliary,
    NSWindowStyleMaskBorderless,
    NSWindowStyleMaskClosable,
    NSWindowStyleMaskMiniaturizable,
    NSWindowStyleMaskNonactivatingPanel,
    NSWindowStyleMaskTitled,
)
from Foundation import NSMakeRect, NSObject
from PyObjCTools import AppHelper
import Quartz
from Quartz import CABasicAnimation, CAMediaTimingFunction
from ApplicationServices import AXIsProcessTrustedWithOptions, kAXTrustedCheckOptionPrompt
from pynput import keyboard

import hush

# --------------------------------------------------------------------------- paths & config

APP_VERSION = "1.2"
HERE = Path(__file__).resolve().parent
BUNDLED = "Khachiwhisper.app/Contents/Resources" in str(HERE)
if BUNDLED:
    DATA_DIR = Path.home() / "Library" / "Application Support" / "Khachiwhisper"
    DATA_DIR.mkdir(parents=True, exist_ok=True)
else:
    DATA_DIR = HERE
CONFIG_PATH = DATA_DIR / "config.json"
# Stand-ins for librosa / numba / scipy so the Whisper and Parakeet packages import
# without PyTorch-sized dependencies (see shims/).
sys.path.insert(0, str(HERE / "shims"))

SAMPLE_RATE = 16000

LANG_NAMES = {
    "auto": "Auto-detect", "en": "English", "fr": "French", "de": "German", "es": "Spanish",
    "it": "Italian", "pt": "Portuguese", "nl": "Dutch", "pl": "Polish", "el": "Greek",
    "ar": "Arabic", "zh": "Chinese", "ja": "Japanese", "ko": "Korean", "vi": "Vietnamese",
    "fa": "Persian", "ru": "Russian", "tr": "Turkish", "hi": "Hindi", "uk": "Ukrainian",
    "sv": "Swedish", "da": "Danish", "fi": "Finnish", "no": "Norwegian", "cs": "Czech",
    "hu": "Hungarian", "ro": "Romanian", "he": "Hebrew", "id": "Indonesian", "th": "Thai",
    "ms": "Malay", "ta": "Tamil", "bn": "Bengali", "ur": "Urdu", "sk": "Slovak", "sl": "Slovenian",
    "hr": "Croatian", "bg": "Bulgarian", "et": "Estonian", "lv": "Latvian", "lt": "Lithuanian",
    "mt": "Maltese", "ca": "Catalan", "tl": "Tagalog", "sw": "Swahili",
}
COHERE_LANGS = ["en", "fr", "de", "es", "it", "pt", "nl", "pl", "el", "ar", "zh", "ja", "ko", "vi"]
PARAKEET_LANGS = ["en", "es", "fr", "de", "it", "pt", "nl", "pl", "ru", "uk", "cs", "sk", "sl", "hr",
                  "bg", "ro", "hu", "el", "da", "sv", "fi", "no", "et", "lv", "lt", "mt"]
WHISPER_LANGS = ["auto", "en", "fa", "fr", "de", "es", "it", "pt", "nl", "pl", "el", "ar", "zh", "ja",
                 "ko", "vi", "ru", "tr", "hi", "uk", "sv", "da", "fi", "no", "cs", "hu", "ro", "he",
                 "id", "th", "ms", "ta", "bn", "ur", "ca", "tl", "sw"]

MODELS = {
    "cohere": dict(name="Cohere Transcribe", repo="appautomaton/cohere-asr-mlx", size_gb=2.4,
                   backend="cohere", langs=COHERE_LANGS,
                   blurb="Best accuracy · 14 languages · recommended"),
    "whisper-turbo": dict(name="Whisper large-v3-turbo", repo="mlx-community/whisper-large-v3-turbo", size_gb=1.6,
                          backend="whisper", langs=WHISPER_LANGS,
                          blurb="99 languages incl. Persian · fast"),
    "whisper-v3": dict(name="Whisper large-v3", repo="mlx-community/whisper-large-v3-mlx", size_gb=3.1,
                       backend="whisper", langs=WHISPER_LANGS,
                       blurb="99 languages · slower, a little more accurate"),
    "parakeet": dict(name="Parakeet TDT 0.6B v3", repo="mlx-community/parakeet-tdt-0.6b-v3", size_gb=2.5,
                     backend="parakeet", langs=PARAKEET_LANGS,
                     blurb="Fastest · English + 24 European languages"),
}
MODEL_ORDER = ["cohere", "whisper-turbo", "whisper-v3", "parakeet"]

# --------------------------------------------------------------------------- shortcuts

MOD_FLAGS = {  # CGEvent and NSEvent use the same bit values for these
    "ctrl": Quartz.kCGEventFlagMaskControl,
    "alt": Quartz.kCGEventFlagMaskAlternate,
    "shift": Quartz.kCGEventFlagMaskShift,
    "cmd": Quartz.kCGEventFlagMaskCommand,
}
MOD_SYMBOL = {"ctrl": "⌃", "alt": "⌥", "shift": "⇧", "cmd": "⌘"}
MOD_ORDER = ["ctrl", "alt", "shift", "cmd"]
ALL_MOD_MASK = sum(MOD_FLAGS.values())
# modifier keys usable on their own as a tap: keycode -> (display name, flag that signals it)
TAP_KEYS = {
    54: ("right ⌘", Quartz.kCGEventFlagMaskCommand), 55: ("left ⌘", Quartz.kCGEventFlagMaskCommand),
    61: ("right ⌥", Quartz.kCGEventFlagMaskAlternate), 58: ("left ⌥", Quartz.kCGEventFlagMaskAlternate),
    62: ("right ⌃", Quartz.kCGEventFlagMaskControl), 59: ("left ⌃", Quartz.kCGEventFlagMaskControl),
    60: ("right ⇧", Quartz.kCGEventFlagMaskShift), 56: ("left ⇧", Quartz.kCGEventFlagMaskShift),
    63: ("fn", Quartz.kCGEventFlagMaskSecondaryFn),
}
KEY_NAMES = {49: "Space", 36: "Return", 48: "Tab", 51: "Delete", 53: "Esc", 76: "Enter", 117: "⌦",
             115: "Home", 119: "End", 116: "Page Up", 121: "Page Down", 123: "←", 124: "→", 125: "↓", 126: "↑",
             122: "F1", 120: "F2", 99: "F3", 118: "F4", 96: "F5", 97: "F6", 98: "F7", 100: "F8", 101: "F9",
             109: "F10", 103: "F11", 111: "F12", 105: "F13", 107: "F14", 113: "F15", 106: "F16", 64: "F17",
             79: "F18", 80: "F19", 90: "F20"}
BARE_OK = {k for k, v in KEY_NAMES.items() if v.startswith("F")}   # function keys work without modifiers
KEY_ESC = 53
PRESET_HOTKEYS = {  # values written by 1.1 configs
    "right_cmd": {"kind": "tap", "keycode": 54},
    "right_alt": {"kind": "tap", "keycode": 61},
    "alt_space": {"kind": "combo", "keycode": 49, "mods": ["alt"], "key": "Space"},
    "ctrl_space": {"kind": "combo", "keycode": 49, "mods": ["ctrl"], "key": "Space"},
}
DEFAULT_HOTKEY = PRESET_HOTKEYS["right_cmd"]


def normalize_hotkey(hk):
    """Return a valid hotkey dict or None."""
    if isinstance(hk, str):
        return dict(PRESET_HOTKEYS[hk]) if hk in PRESET_HOTKEYS else None
    if not isinstance(hk, dict):
        return None
    if hk.get("kind") == "tap" and hk.get("keycode") in TAP_KEYS:
        return {"kind": "tap", "keycode": int(hk["keycode"])}
    if hk.get("kind") == "combo" and isinstance(hk.get("keycode"), int):
        mods = [m for m in MOD_ORDER if m in (hk.get("mods") or [])]
        return {"kind": "combo", "keycode": int(hk["keycode"]), "mods": mods,
                "key": str(hk.get("key") or KEY_NAMES.get(hk["keycode"], f"key {hk['keycode']}"))}
    return None


def hotkey_caps(hk) -> list[str]:
    """Keycaps for the overlay footer, e.g. ['right ⌘'] or ['⌃', '⇧', 'D']."""
    if hk["kind"] == "tap":
        return [TAP_KEYS[hk["keycode"]][0]]
    return [MOD_SYMBOL[m] for m in hk["mods"]] + [hk["key"]]


def hotkey_hint(hk) -> str:
    """Compact label, e.g. 'right ⌘', '⌥Space', '⌃⇧D'."""
    if hk["kind"] == "tap":
        return TAP_KEYS[hk["keycode"]][0]
    return "".join(MOD_SYMBOL[m] for m in hk["mods"]) + hk["key"]


def hotkey_mask(hk) -> int:
    return sum(MOD_FLAGS[m] for m in hk.get("mods", []))


class ShortcutRecorder:
    """Turns raw key events into a hotkey dict. Feed it NSEvent/CGEvent-style values:
    etype 12 = flags changed (modifier keys), 10 = key down."""

    def __init__(self):
        self.held, self.seen, self.other = set(), set(), False

    def feed(self, etype, keycode, flags, chars="", is_repeat=False):
        """Returns ("done", hotkey) | ("cancel", None) | ("invalid", message) | (None, None)."""
        if etype == 12:
            if keycode not in TAP_KEYS:
                return (None, None)
            if flags & TAP_KEYS[keycode][1]:
                self.held.add(keycode)
                self.seen.add(keycode)
            else:
                self.held.discard(keycode)
                if not self.held:
                    if not self.other and len(self.seen) == 1:
                        return ("done", {"kind": "tap", "keycode": next(iter(self.seen))})
                    self.seen.clear()
                    self.other = False
            return (None, None)
        if etype == 10:
            if is_repeat:
                return (None, None)
            flags &= ALL_MOD_MASK
            mods = [m for m in MOD_ORDER if flags & MOD_FLAGS[m]]
            if keycode == KEY_ESC and not mods:
                return ("cancel", None)
            self.other = True
            if not mods and keycode not in BARE_OK:
                return ("invalid", "Letters and most keys need ⌃ ⌥ ⇧ or ⌘ as well")
            key = KEY_NAMES.get(keycode) or ((chars or "").strip().upper() or f"key {keycode}")
            return ("done", {"kind": "combo", "keycode": keycode, "mods": mods, "key": key})
        return (None, None)

DEFAULT_CONFIG = {
    "hotkey": "right_cmd",
    "model": "cohere",
    "language": "en",
    "input_device": None,          # None = system default mic; or a name substring, e.g. "EarPods"
    "min_seconds": 0.4,            # recordings shorter than this are dropped
    "silence_rms": 0.004,          # if the whole clip is below this RMS, treat as empty
    "paste_delay": 0.05,
    "restore_clipboard": True,
    "log": str(DATA_DIR / "khachiwhisper.log"),
}
USER_KEYS = [k for k in DEFAULT_CONFIG if k != "log"]


def save_config(cfg: dict):
    CONFIG_PATH.write_text(json.dumps({k: cfg[k] for k in USER_KEYS}, indent=2) + "\n")


def load_config() -> dict:
    cfg = dict(DEFAULT_CONFIG)
    fresh = not CONFIG_PATH.exists()
    if not fresh:
        try:
            cfg.update(json.loads(CONFIG_PATH.read_text()))
        except ValueError:
            pass
    # migrate from the pre-1.1 "backend" key
    old = cfg.pop("backend", None)
    if old == "whisper":
        cfg["model"] = "whisper-turbo"
    cfg.pop("whisper_model", None)
    if cfg["model"] not in MODELS:
        cfg["model"] = "cohere"
    cfg["hotkey"] = normalize_hotkey(cfg.get("hotkey")) or dict(DEFAULT_HOTKEY)
    cfg["_fresh"] = fresh
    save_config(cfg)
    return cfg


CFG = load_config()


def log(*a):
    line = time.strftime("%H:%M:%S ") + " ".join(str(x) for x in a)
    print(line, flush=True)
    try:
        with open(CFG["log"], "a") as f:
            f.write(line + "\n")
    except OSError:
        pass


# --------------------------------------------------------------------------- models


class ModelManager:
    """Knows the four models, whether they're on disk, downloads them, and keeps one loaded."""

    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.key = None                 # loaded (or loading) model key
        self.model = None
        self.ready = threading.Event()
        self.phase = "idle"             # idle | downloading | loading | ready | error
        self.error = None
        self.progress: dict[str, float] = {}   # model key -> 0..1 while downloading
        self.listeners = []             # callables run on the main thread whenever state changes
        self._lock = threading.Lock()
        # MLX streams are bound to the thread that created them, so everything that touches a
        # model (loading, warm-up, inference) runs on this one worker thread.
        self._worker = ThreadPoolExecutor(max_workers=1, thread_name_prefix="model")

    # -- state
    def notify(self):
        for cb in list(self.listeners):
            AppHelper.callAfter(cb)

    def is_downloaded(self, key: str) -> bool:
        from huggingface_hub import snapshot_download
        try:
            snapshot_download(MODELS[key]["repo"], local_files_only=True)
            return True
        except Exception:  # noqa: BLE001
            return False

    def status_text(self, short: bool = False) -> str:
        name = MODELS[self.key]["name"] if self.key else "no model"
        if short:
            name = name.replace("Transcribe", "").replace("large-v3-turbo", "turbo").replace("large-v3", "v3").replace("TDT 0.6B v3", "").strip()
        if self.phase == "downloading":
            return f"Downloading {name}… {int(self.progress.get(self.key, 0) * 100)}%"
        if self.phase == "loading":
            return f"Loading {name}…"
        if self.phase == "error":
            return f"{name} failed to load (see log)"
        if self.phase == "ready":
            return f"Ready · {name}" if short else f"Ready · {name} · {LANG_NAMES.get(self.cfg['language'], self.cfg['language'])}"
        return "No model loaded"

    # -- downloading
    def download(self, key: str, then_load: bool = False):
        if key in self.progress:
            return  # already downloading
        self.progress[key] = 0.0
        self.notify()

        def run():
            try:
                self._download(key)
                log(f"downloaded {MODELS[key]['name']}")
            except Exception as e:  # noqa: BLE001
                log(f"download of {MODELS[key]['name']} failed:", repr(e))
            finally:
                self.progress.pop(key, None)
                self.notify()
            if then_load:
                self.load(key)

        threading.Thread(target=run, daemon=True, name=f"download-{key}").start()

    def _download(self, key: str):
        from huggingface_hub import HfApi, hf_hub_download, snapshot_download
        from tqdm.auto import tqdm as _tqdm
        repo = MODELS[key]["repo"]
        info = HfApi().model_info(repo, files_metadata=True)
        files = [(f.rfilename, f.size or 0) for f in info.siblings]
        total = sum(sz for _, sz in files) or MODELS[key]["size_gb"] * 1e9
        done = {"bytes": 0}
        me = self

        class _Bar(_tqdm):
            """Byte-level bar hf_hub_download drives; mirrored into self.progress[key]."""
            def __init__(self, *a, **k):
                k.pop("name", None)
                k["file"] = open(os.devnull, "w")
                super().__init__(*a, **k)
                self._last = 0
                self._counts = "reconstruct" not in (self.desc or "")   # xet makes two bars per file

            def refresh(self, *a, **k):
                pass

            def update(self, n=1):
                super().update(n)
                if self._counts:
                    done["bytes"] += self.n - self._last
                    self._last = self.n
                    me.progress[key] = min(0.99, done["bytes"] / total)
                    me.notify()

        for name, _sz in sorted(files, key=lambda f: f[1]):
            hf_hub_download(repo, name, tqdm_class=_Bar)
        snapshot_download(repo)  # writes the snapshot refs; everything is cached now

    def remove(self, key: str) -> bool:
        """Delete a downloaded model from the local cache (never the active one)."""
        if key == self.key or key in self.progress:
            return False
        import shutil
        from huggingface_hub import constants
        d = Path(constants.HF_HUB_CACHE) / ("models--" + MODELS[key]["repo"].replace("/", "--"))
        shutil.rmtree(d, ignore_errors=True)
        log(f"removed {MODELS[key]['name']} from {d}")
        self.notify()
        return True

    # -- loading
    def load(self, key: str):
        """Switch to `key`: download first if needed, then load in the background."""
        with self._lock:
            self.key = key
            self.ready.clear()
            self.error = None
            self.model = None
            self.phase = "loading"
        self.notify()
        self._worker.submit(self._load, key)

    def _load(self, key: str):
        t0 = time.time()
        try:
            if not self.is_downloaded(key):
                self.phase = "downloading"
                self.progress[key] = 0.0
                self.notify()
                try:
                    self._download(key)
                finally:
                    self.progress.pop(key, None)
                self.phase = "loading"
                self.notify()
            if self.key != key:
                return  # user switched again while we were downloading
            backend = MODELS[key]["backend"]
            repo = MODELS[key]["repo"]
            import gc
            gc.collect()
            try:
                import mlx.core as mx
                mx.clear_cache()
            except Exception:  # noqa: BLE001
                pass
            silence = np.zeros(SAMPLE_RATE, dtype=np.float32)
            if backend == "cohere":
                import mlx_speech
                model = mlx_speech.asr.load(repo)
                model.generate(silence, sample_rate=SAMPLE_RATE, language="en")
            elif backend == "whisper":
                import mlx_whisper
                model = mlx_whisper
                mlx_whisper.transcribe(silence, path_or_hf_repo=repo, language="en", fp16=True)
            elif backend == "parakeet":
                from parakeet_mlx import from_pretrained
                import mlx.core as mx
                from parakeet_mlx.audio import get_logmel
                model = from_pretrained(repo)
                model.generate(get_logmel(mx.array(silence), model.preprocessor_config))
            else:
                raise ValueError(backend)
            if self.key != key:
                return
            self.model = model
            self.phase = "ready"
            log(f"model ready: {MODELS[key]['name']} in {time.time() - t0:.1f}s")
        except Exception as e:  # noqa: BLE001
            if self.key == key:
                self.error = e
                self.phase = "error"
            log(f"MODEL LOAD FAILED ({key}):", repr(e))
        finally:
            if self.key == key:
                self.ready.set()
            self.notify()

    # -- inference
    def transcribe(self, audio: np.ndarray) -> str:
        self.ready.wait()
        if self.error:
            raise self.error
        return self._worker.submit(self._transcribe, audio).result()

    def _transcribe(self, audio: np.ndarray) -> str:
        backend = MODELS[self.key]["backend"]
        lang = self.cfg["language"]
        if backend == "cohere":
            return self.model.generate(audio, sample_rate=SAMPLE_RATE, language=lang).text.strip()
        if backend == "whisper":
            out = self.model.transcribe(audio, path_or_hf_repo=MODELS[self.key]["repo"],
                                        language=None if lang == "auto" else lang,
                                        condition_on_previous_text=False, fp16=True)
            return out["text"].strip()
        if backend == "parakeet":
            import mlx.core as mx
            from parakeet_mlx.audio import get_logmel
            mel = get_logmel(mx.array(audio), self.model.preprocessor_config)
            return self.model.generate(mel)[0].text.strip()
        raise ValueError(backend)


# --------------------------------------------------------------------------- mic recorder


class Recorder:
    def __init__(self, on_level):
        self.on_level = on_level
        self.chunks: list[np.ndarray] = []
        self.stream = None
        self.native_rate = SAMPLE_RATE
        self.device = self._pick_device()

    def _pick_device(self):
        want = CFG.get("input_device")
        if not want:
            return None
        for i, d in enumerate(sd.query_devices()):
            if d["max_input_channels"] > 0 and want.lower() in d["name"].lower():
                return i
        log(f"input_device {want!r} not found, using default")
        return None

    def start(self):
        self.chunks = []
        rate = SAMPLE_RATE
        try:
            sd.check_input_settings(device=self.device, samplerate=rate, channels=1)
        except Exception:  # noqa: BLE001
            rate = int(sd.query_devices(self.device, "input")["default_samplerate"])
        self.native_rate = rate
        self.stream = sd.InputStream(
            device=self.device, samplerate=rate, channels=1, dtype="float32",
            blocksize=int(rate * 0.03), callback=self._cb,
        )
        self.stream.start()

    def _cb(self, indata, frames, t, status):
        mono = indata[:, 0].copy()
        self.chunks.append(mono)
        self.on_level(float(np.sqrt(np.mean(mono * mono)) + 1e-9))

    def stop(self) -> np.ndarray:
        if self.stream is not None:
            self.stream.stop()
            self.stream.close()
            self.stream = None
        if not self.chunks:
            return np.zeros(0, dtype=np.float32)
        audio = np.concatenate(self.chunks).astype(np.float32)
        if self.native_rate != SAMPLE_RATE:
            n = int(round(len(audio) * SAMPLE_RATE / self.native_rate))
            audio = np.interp(np.linspace(0, len(audio) - 1, n), np.arange(len(audio)), audio).astype(np.float32)
        return audio


# --------------------------------------------------------------------------- overlay panel

PANEL_W, PANEL_H = 440, 128
FOOTER_H = 44
BAR_W, BAR_GAP = 2.5, 3.0
WAVE_MARGIN = 30
BAR_COUNT = int((PANEL_W - 2 * WAVE_MARGIN + BAR_GAP) // (BAR_W + BAR_GAP))
PUSH_HZ = 33.0  # audio callbacks per second (30 ms blocks)

# dB window for the meter: below FLOOR is silence, above CEIL is full height
DB_FLOOR, DB_CEIL = -52.0, -14.0


def _smoothstep(t):
    t = min(1.0, max(0.0, t))
    return t * t * (3 - 2 * t)


class WaveView(NSView):
    """Scrolling, mirrored level history drawn at 60 fps with sub-bar interpolation."""

    def initWithFrame_(self, frame):
        self = objc.super(WaveView, self).initWithFrame_(frame)
        if self is None:
            return None
        self.reset()
        return self

    def reset(self):
        self.levels = [0.0] * (BAR_COUNT + 1)   # one extra bar hidden off the right edge
        self.env = 0.0
        self.last_push = time.time()
        self.mode = "listen"                     # listen | busy
        self.busy_t0 = 0.0

    def pushLevel_(self, rms):
        db = 20.0 * np.log10(max(rms, 1e-7))
        target = _smoothstep((db - DB_FLOOR) / (DB_CEIL - DB_FLOOR))
        # fast attack, slower release so bars feel alive but not twitchy
        k = 0.55 if target > self.env else 0.25
        self.env += (target - self.env) * k
        self.levels = self.levels[1:] + [self.env]
        self.last_push = time.time()

    def setMode_(self, mode):
        self.mode = mode
        self.busy_t0 = time.time()

    def drawRect_(self, rect):
        b = self.bounds()
        area_h = b.size.height - FOOTER_H
        cy = FOOTER_H + area_h / 2
        pitch = BAR_W + BAR_GAP
        total_w = BAR_COUNT * pitch - BAR_GAP
        x0 = (b.size.width - total_w) / 2
        max_h = area_h * 0.62
        now = time.time()

        if self.mode == "listen":
            # fractional scroll so 33 Hz pushes render as continuous motion at 60 fps
            frac = min(1.0, (now - self.last_push) * PUSH_HZ)
            shift = frac * pitch
        else:
            shift = 0.0

        n = len(self.levels)
        for i, v in enumerate(self.levels):
            x = x0 + (i - 1) * pitch + (pitch - shift)
            if x < x0 - pitch or x > x0 + total_w:
                continue
            # fade toward both edges
            pos = (x - x0) / total_w
            edge = _smoothstep(pos / 0.14) * _smoothstep((1 - pos) / 0.14)
            if self.mode == "busy":
                # bars settle to a quiet idle and a soft highlight sweeps across
                t = now - self.busy_t0
                v = v * 0.35 + 0.05
                sweep = 0.5 + 0.5 * np.cos((pos - (t * 0.6 % 1.4 - 0.2)) * 9.0)
                alpha = (0.28 + 0.5 * (sweep ** 6)) * edge
            else:
                alpha = (0.32 + 0.68 * v) * edge
            h = max(3.0, v * max_h)
            NSColor.colorWithCalibratedWhite_alpha_(1.0, alpha).set()
            NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
                NSMakeRect(x, cy - h / 2, BAR_W, h), BAR_W / 2, BAR_W / 2).fill()


class Overlay:
    """Floating, non-activating frosted panel so the target app keeps keyboard focus."""

    def __init__(self):
        style = NSWindowStyleMaskBorderless | NSWindowStyleMaskNonactivatingPanel
        self.panel = NSPanel.alloc().initWithContentRect_styleMask_backing_defer_(
            NSMakeRect(0, 0, PANEL_W, PANEL_H), style, NSBackingStoreBuffered, False)
        self.panel.setOpaque_(False)
        self.panel.setBackgroundColor_(NSColor.clearColor())
        self.panel.setLevel_(NSFloatingWindowLevel + 1)
        self.panel.setHasShadow_(True)
        self.panel.setIgnoresMouseEvents_(True)
        self.panel.setHidesOnDeactivate_(False)
        self.panel.setAnimationBehavior_(NSWindowAnimationBehaviorNone)
        self.panel.setAppearance_(NSAppearance.appearanceNamed_(NSAppearanceNameVibrantDark))
        self.panel.setCollectionBehavior_(
            NSWindowCollectionBehaviorCanJoinAllSpaces | NSWindowCollectionBehaviorFullScreenAuxiliary)

        # frosted glass root
        root = NSVisualEffectView.alloc().initWithFrame_(NSMakeRect(0, 0, PANEL_W, PANEL_H))
        root.setMaterial_(NSVisualEffectMaterialHUDWindow)
        root.setBlendingMode_(NSVisualEffectBlendingModeBehindWindow)
        root.setState_(NSVisualEffectStateActive)
        root.setWantsLayer_(True)
        root.layer().setCornerRadius_(24)
        root.layer().setMasksToBounds_(True)
        root.layer().setBorderWidth_(1.0)
        root.layer().setBorderColor_(hush.C.LINE_STRONG.CGColor())
        self.panel.setContentView_(root)
        self.root = root

        # dark tint over the blur so it reads as a dark HUD on any wallpaper
        tint = NSView.alloc().initWithFrame_(root.bounds())
        tint.setWantsLayer_(True)
        tint.layer().setBackgroundColor_(hush.rgba("#121316", 0.80).CGColor())
        root.addSubview_(tint)

        # footer band + hairline
        footer = NSView.alloc().initWithFrame_(NSMakeRect(0, 0, PANEL_W, FOOTER_H))
        footer.setWantsLayer_(True)
        footer.layer().setBackgroundColor_(NSColor.colorWithCalibratedWhite_alpha_(1.0, 0.035).CGColor())
        root.addSubview_(footer)
        line = NSView.alloc().initWithFrame_(NSMakeRect(0, FOOTER_H, PANEL_W, 1))
        line.setWantsLayer_(True)
        line.layer().setBackgroundColor_(hush.C.LINE.CGColor())
        root.addSubview_(line)

        self.wave = WaveView.alloc().initWithFrame_(NSMakeRect(0, 0, PANEL_W, PANEL_H))
        root.addSubview_(self.wave)

        # footer contents
        self.dot = self._dot(24, FOOTER_H / 2 - 4)
        self.left = self._label("Listening", 40, 12, 200, 20, weight=NSFontWeightMedium, alpha=0.82)
        # right-aligned: Stop [caps…]   Cancel [esc]
        caps = hotkey_caps(CFG["hotkey"])
        self.hints = []

        def cap_w(t):
            return max(28, int(hush.text_width(t, "mono") + 16))

        x = PANEL_W - 16
        w = cap_w("esc")
        x -= w
        self.hints.append(self._keycap("esc", x, 10, w))
        x -= 6
        lw = int(hush.text_width("Cancel", "body")) + 12
        x -= lw
        self.hints.append(self._label("Cancel", x, 12, lw, 20, alpha=0.55))
        x -= 16
        for cap in reversed(caps):
            w = cap_w(cap)
            x -= w
            self.hints.append(self._keycap(cap, x, 10, w))
            x -= 4
        x -= 2
        lw = int(hush.text_width("Stop", "body")) + 12
        x -= lw
        self.hints.append(self._label("Stop", x, 12, lw, 20, alpha=0.55))

        self.timer = None
        self.visible = False

    # -- widgets
    def _label(self, text, x, y, w, h, weight=None, alpha=0.6, size=13, align=0):
        tf = NSTextField.alloc().initWithFrame_(NSMakeRect(x, y, w, h))
        tf.setStringValue_(text)
        tf.setBezeled_(False)
        tf.setDrawsBackground_(False)
        tf.setEditable_(False)
        tf.setSelectable_(False)
        tf.setFont_(NSFont.systemFontOfSize_weight_(size, weight if weight is not None else NSFontWeightRegular))
        tf.setTextColor_(NSColor.colorWithCalibratedWhite_alpha_(1.0, alpha))
        tf.setAlignment_(align)
        self.root.addSubview_(tf)
        return tf

    def _keycap(self, text, x, y, w):
        cap = hush.Keycap.alloc().initWithFrame_(NSMakeRect(x, y, w, 24))
        cap.text = text
        self.root.addSubview_(cap)
        return cap

    def _dot(self, x, y):
        d = NSView.alloc().initWithFrame_(NSMakeRect(x, y, 8, 8))
        d.setWantsLayer_(True)
        d.layer().setCornerRadius_(4)
        self.root.addSubview_(d)
        return d

    def _set_dot(self, mode):
        lay = self.dot.layer()
        lay.removeAllAnimations()
        if mode == "listen":
            lay.setBackgroundColor_(hush.C.ACCENT.CGColor())
            lay.setShadowColor_(hush.C.ACCENT.CGColor())
            lay.setShadowOpacity_(0.8)
            lay.setShadowRadius_(4)
            lay.setShadowOffset_((0, 0))
            pulse = CABasicAnimation.animationWithKeyPath_("opacity")
            pulse.setFromValue_(1.0)
            pulse.setToValue_(0.35)
            pulse.setDuration_(0.9)
            pulse.setAutoreverses_(True)
            pulse.setRepeatCount_(1e9)
            pulse.setTimingFunction_(CAMediaTimingFunction.functionWithName_("easeInEaseOut"))
            lay.addAnimation_forKey_(pulse, "pulse")
        else:
            lay.setOpacity_(1.0)
            lay.setBackgroundColor_(hush.C.OK.CGColor())
            lay.setShadowColor_(hush.C.OK.CGColor())
            lay.setShadowOpacity_(0.8)
            lay.setShadowRadius_(4)

    # -- placement / animation
    def _target_frame(self):
        f = NSScreen.mainScreen().visibleFrame()
        x = f.origin.x + (f.size.width - PANEL_W) / 2
        y = f.origin.y + 96
        return NSMakeRect(x, y, PANEL_W, PANEL_H)

    def show(self):
        self.wave.reset()
        self.wave.setMode_("listen")
        self.set_status("Listening")
        self._set_dot("listen")
        for v in self.hints:
            v.setHidden_(False)
        self.left.setFrame_(NSMakeRect(40, 12, 200, 20))
        target = self._target_frame()
        start = NSMakeRect(target.origin.x, target.origin.y - 14, PANEL_W, PANEL_H)
        self.panel.setFrame_display_(start, False)
        self.panel.setAlphaValue_(0.0)
        self.panel.orderFrontRegardless()
        NSAnimationContext.beginGrouping()
        ctx = NSAnimationContext.currentContext()
        ctx.setDuration_(0.22)
        ctx.setTimingFunction_(CAMediaTimingFunction.functionWithName_("easeOut"))
        self.panel.animator().setAlphaValue_(1.0)
        self.panel.animator().setFrame_display_(target, True)
        NSAnimationContext.endGrouping()
        self.visible = True
        if self.timer:
            self.timer.invalidate()
        self.timer = NSTimer.scheduledTimerWithTimeInterval_repeats_block_(1 / 60, True, lambda t: self.wave.setNeedsDisplay_(True))

    def set_status(self, text):
        self.left.setStringValue_(text)

    def set_busy(self, text="Transcribing"):
        self.wave.setMode_("busy")
        self._set_dot("busy")
        self.set_status(text)

    def set_notice(self, text):
        """Busy look, hints hidden, label spanning the full width."""
        self.set_busy(text)
        for v in self.hints:
            v.setHidden_(True)
        self.left.setFrame_(NSMakeRect(40, 12, PANEL_W - 60, 20))

    def hide(self):
        if not self.visible:
            return
        self.visible = False
        f = self.panel.frame()
        down = NSMakeRect(f.origin.x, f.origin.y - 10, f.size.width, f.size.height)
        NSAnimationContext.beginGrouping()
        ctx = NSAnimationContext.currentContext()
        ctx.setDuration_(0.16)
        ctx.setTimingFunction_(CAMediaTimingFunction.functionWithName_("easeIn"))
        self.panel.animator().setAlphaValue_(0.0)
        self.panel.animator().setFrame_display_(down, True)
        NSAnimationContext.endGrouping()

        def finish():
            if not self.visible:
                if self.timer:
                    self.timer.invalidate()
                    self.timer = None
                self.panel.orderOut_(None)
        AppHelper.callLater(0.18, finish)

    def push_level(self, level):
        self.wave.pushLevel_(level)


# --------------------------------------------------------------------------- paste


def paste_text(text: str):
    pb = NSPasteboard.generalPasteboard()
    old = pb.stringForType_(NSPasteboardTypeString) if CFG["restore_clipboard"] else None
    pb.clearContents()
    pb.setString_forType_(text, NSPasteboardTypeString)
    time.sleep(CFG["paste_delay"])

    src = Quartz.CGEventSourceCreate(Quartz.kCGEventSourceStateHIDSystemState)
    v_down = Quartz.CGEventCreateKeyboardEvent(src, 9, True)   # 9 = 'v'
    v_up = Quartz.CGEventCreateKeyboardEvent(src, 9, False)
    Quartz.CGEventSetFlags(v_down, Quartz.kCGEventFlagMaskCommand)
    Quartz.CGEventSetFlags(v_up, Quartz.kCGEventFlagMaskCommand)
    Quartz.CGEventPost(Quartz.kCGHIDEventTap, v_down)
    Quartz.CGEventPost(Quartz.kCGHIDEventTap, v_up)

    if old is not None:
        def restore():
            time.sleep(0.6)
            pb.clearContents()
            pb.setString_forType_(old, NSPasteboardTypeString)
        threading.Thread(target=restore, daemon=True).start()




# --------------------------------------------------------------------------- settings window


def mic_status() -> int:
    """0 = not asked, 1 = restricted, 2 = denied, 3 = granted."""
    try:
        from AVFoundation import AVCaptureDevice
        return int(AVCaptureDevice.authorizationStatusForMediaType_("soun"))
    except Exception:  # noqa: BLE001
        return -1


class SettingsWindow:
    """The settings window, drawn entirely with Hush components."""

    W, H = 760, 560
    SIDEBAR = 212
    PAD = 32
    ROW = 56
    PANES = [("General", "gearshape"), ("Models", "cpu"), ("Permissions", "lock.shield"), ("About", "info.circle")]

    def __init__(self, ctrl):
        self.ctrl = ctrl
        self.win, self.root = hush.make_window(self.W, self.H, "Khachiwhisper")
        self.timer = None
        self.current = 0
        self._build_sidebar()
        cx, cw = self.SIDEBAR, self.W - self.SIDEBAR
        self.panes = []
        for builder in (self._build_general, self._build_models, self._build_permissions, self._build_about):
            pane = hush.HView.alloc().initWithFrame_(NSMakeRect(cx, 0, cw, self.H))
            self.root.addSubview_(pane)
            builder(pane, cw)
            self.panes.append(pane)
        self.select(0)

    # -- layout helpers --------------------------------------------------------
    def _title(self, pane, text, w):
        hush.label(pane, text, self.PAD, 48, w - self.PAD * 2, 32, "display")
        return 100

    def _card(self, pane, y, w, rows):
        h = rows * self.ROW
        card = hush.Surface.alloc().initWithFrame_(NSMakeRect(self.PAD, y, w - self.PAD * 2, h))
        pane.addSubview_(card)
        return card, h

    def _row(self, card, i, title, caption=None):
        cw = card.frame().size.width
        y = i * self.ROW
        if i:
            hush.divider(card, 16, y, cw - 32)
        t = hush.label(card, title, 16, y + 11 if caption else y + 19, 320, 18, "body")
        c = hush.label(card, caption or "", 16, y + 31, cw - 200, 14, "caption", hush.C.TEXT2) if caption else None
        return t, c

    def _right(self, card, i, w, h=30):
        """Frame for a right-aligned control in row i."""
        cw = card.frame().size.width
        return NSMakeRect(cw - 16 - w, i * self.ROW + (self.ROW - h) / 2, w, h)

    # -- sidebar -------------------------------------------------------------------
    def _build_sidebar(self):
        side = hush.HView.alloc().initWithFrame_(NSMakeRect(0, 0, self.SIDEBAR, self.H))
        side.setWantsLayer_(True)
        side.layer().setBackgroundColor_(hush.C.INK1.CGColor())
        self.root.addSubview_positioned_relativeTo_(side, -1, None)  # below the window controls
        hush.divider(side, self.SIDEBAR - 1, 0, 1).setFrame_(NSMakeRect(self.SIDEBAR - 1, 0, 1, self.H))

        from AppKit import NSImage, NSImageView
        icns = HERE / "Khachiwhisper.icns" if BUNDLED else HERE / "app" / "Khachiwhisper.icns"
        if icns.exists():
            iv = NSImageView.alloc().initWithFrame_(NSMakeRect(20, 50, 28, 28))
            iv.setImage_(NSImage.alloc().initWithContentsOfFile_(str(icns)))
            side.addSubview_(iv)
        hush.label(side, "Khachiwhisper", 56, 55, self.SIDEBAR - 70, 20, "title")

        self.nav = []
        y = 108
        for i, (title, sym) in enumerate(self.PANES):
            item = hush.nav_item(side, sym, title, 12, y, self.SIDEBAR - 24, on_click=lambda i=i: self.select(i))
            self.nav.append(item)
            y += 36

        self.side_dot = hush.StatusDot.alloc().initWithFrame_(NSMakeRect(18, self.H - 46, 16, 16))
        side.addSubview_(self.side_dot)
        self.side_status = hush.label(side, "", 38, self.H - 48, self.SIDEBAR - 52, 20, "caption", hush.C.TEXT2)
        hush.label(side, f"v{APP_VERSION} · on-device", 38, self.H - 32, self.SIDEBAR - 52, 14, "caption", hush.C.TEXT3)

    # -- panes -----------------------------------------------------------------------
    def _build_general(self, pane, w):
        c = self.ctrl
        y = self._title(pane, "General", w)
        card, h = self._card(pane, y, w, 2)
        _, self.hotkey_sub = self._row(card, 0, "Shortcut", "Toggle recording on and off")
        r = self._right(card, 0, 200, 32)
        self.hotkey_btn = hush.RecorderButton.alloc().initWithFrame_(r)
        self.hotkey_btn.on_click = c.begin_shortcut_recording
        card.addSubview_(self.hotkey_btn)
        self._row(card, 1, "Language", "What you'll be speaking")
        self.lang_select = hush.select(card, [], None, *self._rect(self._right(card, 1, 200)), on_change=c.set_language)
        y += h + 12
        hush.label(pane, "Click the shortcut and press what you want: a modifier tapped on its own (right ⌘, fn…), "
                         "a function key, or a combination like ⌃⇧D. Press once to start, again to stop and paste; "
                         "esc cancels. A tapped modifier still works in shortcuts like right ⌘ + C.",
                   self.PAD, y, w - self.PAD * 2, 50, "caption", hush.C.TEXT2, wrap=True)
        y += 66
        card, h = self._card(pane, y, w, 2)
        self._row(card, 0, "Start at login", "Khachiwhisper lives in the menu bar" if BUNDLED else "Release build only")
        self.login_toggle = hush.toggle(card, False, *self._xy(self._right(card, 0, hush.Toggle.W, hush.Toggle.H)),
                                        on_change=lambda on: c.toggle_login())
        self.login_toggle.setEnabled_(BUNDLED)
        self._row(card, 1, "Restore clipboard after paste", "Put back whatever you had copied before dictating")
        self.clip_toggle = hush.toggle(card, bool(CFG["restore_clipboard"]),
                                       *self._xy(self._right(card, 1, hush.Toggle.W, hush.Toggle.H)),
                                       on_change=c.set_restore_clipboard)

    def _build_models(self, pane, w):
        c = self.ctrl
        y = self._title(pane, "Models", w)
        card, h = self._card(pane, y, w, 1)
        self._row(card, 0, "Active model", "Runs on this Mac · nothing is uploaded")
        self.model_select = hush.select(card, [(MODELS[k]["name"], k) for k in MODEL_ORDER], CFG["model"],
                                        *self._rect(self._right(card, 0, 240)), on_change=c.set_model)
        y += h + 12
        hush.label(pane, "Only downloaded models can be made active; use Download in the list to add one. "
                         "Keep the ones you use, remove the rest to free disk space.",
                   self.PAD, y, w - self.PAD * 2, 34, "caption", hush.C.TEXT2, wrap=True)
        y += 46
        card, h = self._card(pane, y, w, len(MODEL_ORDER))
        cw = card.frame().size.width
        self.rows = {}
        for i, key in enumerate(MODEL_ORDER):
            m = MODELS[key]
            self._row(card, i, m["name"], f"{m['blurb']} · {m['size_gb']:.1f} GB")
            ry = i * self.ROW
            right = cw - 16
            pl = hush.pill(card, "", right, ry + 18, "neutral")
            bar = hush.progress(card, right - 160, ry + 26, 160)
            bar.setHidden_(True)
            dl = hush.button(card, "Download", 0, ry + 13, kind="tonal", on_click=lambda k=key: c.models.download(k))
            dl.setFrame_(NSMakeRect(right - dl.frame().size.width, ry + 13, dl.frame().size.width, 30))
            rm = hush.button(card, "Remove", 0, ry + 13, kind="ghost", on_click=lambda k=key: c.remove_model(k))
            self.rows[key] = dict(pill=pl, bar=bar, dl=dl, rm=rm)

    def _build_permissions(self, pane, w):
        c = self.ctrl
        y = self._title(pane, "Permissions", w)
        card, h = self._card(pane, y, w, 2)
        cw = card.frame().size.width
        self._row(card, 0, "Accessibility", "Needed for the global shortcut and to paste text")
        self.ax_btn = hush.button(card, "Grant", 0, 13, kind="primary", on_click=c.grant_accessibility)
        self.ax_btn.setFrame_(NSMakeRect(cw - 16 - self.ax_btn.frame().size.width, 13, self.ax_btn.frame().size.width, 30))
        self.ax_pill = hush.pill(card, "", cw - 16, 18, "warn")
        self._row(card, 1, "Microphone", "Used only while the recording panel is open")
        self.mic_btn = hush.button(card, "Allow", 0, self.ROW + 13, kind="primary", on_click=c.request_microphone)
        self.mic_btn.setFrame_(NSMakeRect(cw - 16 - self.mic_btn.frame().size.width, self.ROW + 13,
                                          self.mic_btn.frame().size.width, 30))
        self.mic_pill = hush.pill(card, "", cw - 16, self.ROW + 18, "warn")
        y += h + 12
        hush.label(pane, "Both live in System Settings › Privacy & Security under “Khachiwhisper”. Grants survive "
                         "updates. If a toggle there shows on but this page disagrees, remove the entry with – and "
                         "grant it again.",
                   self.PAD, y, w - self.PAD * 2, 50, "caption", hush.C.TEXT2, wrap=True)

    def _build_about(self, pane, w):
        from AppKit import NSImage, NSImageView
        y = self._title(pane, "About", w)
        icns = HERE / "Khachiwhisper.icns" if BUNDLED else HERE / "app" / "Khachiwhisper.icns"
        if icns.exists():
            iv = NSImageView.alloc().initWithFrame_(NSMakeRect(self.PAD, y, 64, 64))
            iv.setImage_(NSImage.alloc().initWithContentsOfFile_(str(icns)))
            pane.addSubview_(iv)
        hush.label(pane, "Khachiwhisper", self.PAD + 80, y + 8, 300, 22, "title")
        hush.label(pane, f"Version {APP_VERSION} · local voice-to-text for Apple Silicon", self.PAD + 80, y + 34, 380, 16,
                   "caption", hush.C.TEXT2)
        y += 84
        hush.label(pane, "Speech recognition runs entirely on this Mac with MLX. Audio never leaves the device. "
                         "Models come from Hugging Face and are cached locally.",
                   self.PAD, y, w - self.PAD * 2, 34, "caption", hush.C.TEXT2, wrap=True)
        y += 48
        card, h = self._card(pane, y, w, 3)
        cw = card.frame().size.width
        for i, (title, sub, btn, fn) in enumerate([
            ("Model cache", "Downloaded models live here", "Show", self.ctrl.open_cache),
            ("Configuration", str(CONFIG_PATH), "Edit", self.ctrl.open_config),
            ("Log", "Timings and transcripts, for troubleshooting", "Show", self.ctrl.show_log),
        ]):
            self._row(card, i, title, sub)
            b = hush.button(card, btn, 0, i * self.ROW + 13, kind="ghost", on_click=fn)
            b.setFrame_(NSMakeRect(cw - 16 - b.frame().size.width, i * self.ROW + 13, b.frame().size.width, 30))
        y += h + 16
        hush.label(pane, "Interface drawn with Hush, Khachiwhisper's own design system: ink surfaces, coral for "
                         "anything live, mint for ready.", self.PAD, y, w - self.PAD * 2, 30, "caption",
                   hush.C.TEXT3, wrap=True)

    @staticmethod
    def _rect(r):
        return (r.origin.x, r.origin.y, r.size.width, r.size.height)

    @staticmethod
    def _xy(r):
        return (r.origin.x, r.origin.y)

    # -- behaviour -------------------------------------------------------------------
    def select(self, index):
        self.current = index
        for i, item in enumerate(self.nav):
            item.setSelected_(i == index)
        for i, p in enumerate(self.panes):
            p.setHidden_(i != index)
        self.refresh()

    def set_recording(self, on):
        self.hotkey_btn.setRecording_(on)
        if on:
            self.set_hint("Press a key combo or tap a modifier key · esc cancels", hush.C.ACCENT)
        else:
            self.refresh()

    def set_hint(self, text, color=None):
        self.hotkey_sub.set(text, color or hush.C.TEXT2)

    def refresh(self):
        c, mm = self.ctrl, self.ctrl.models
        if not c.recording_shortcut:
            self.hotkey_btn.setText_(hotkey_hint(CFG["hotkey"]))
            self.hotkey_sub.set("Toggle recording on and off", hush.C.TEXT2)

        langs = MODELS[CFG["model"]]["langs"]
        items = [(LANG_NAMES.get(code, code), code) for code in langs]
        if self.lang_select.items != items or self.lang_select.value != CFG["language"]:
            self.lang_select.items = items
            self.lang_select.value = CFG["language"]
            self.lang_select.setNeedsDisplay_(True)
        model_items = []
        for k in MODEL_ORDER:
            have, busy = mm.is_downloaded(k), k in mm.progress or (k == mm.key and mm.phase == "downloading")
            suffix = "  · downloading" if busy else "" if have else "  · not downloaded"
            model_items.append((MODELS[k]["name"] + suffix, k, have and not busy))
        if self.model_select.items != model_items or self.model_select.value != CFG["model"]:
            self.model_select.items = model_items
            self.model_select.value = CFG["model"]
            self.model_select.setNeedsDisplay_(True)

        for key, r in self.rows.items():
            have = mm.is_downloaded(key)
            downloading = key in mm.progress
            active = key == CFG["model"]
            r["bar"].setHidden_(not downloading)
            r["dl"].setHidden_(have or downloading)
            r["rm"].setHidden_(not have or active or downloading)
            if downloading:
                r["bar"].setValue_(mm.progress.get(key, 0.0))
                r["pill"].set(f"{int(mm.progress.get(key, 0.0) * 100)}%", "accent")
                pf = r["pill"].frame()
                r["pill"].setFrame_(NSMakeRect(pf.origin.x, key_row_y(key) + 6, pf.size.width, pf.size.height))
            elif active and mm.phase == "loading":
                r["pill"].set("Loading…", "accent")
            elif active and mm.phase == "ready":
                r["pill"].set("Active", "ok")
            elif active and mm.phase == "error":
                r["pill"].set("Failed to load", "danger")
            elif have:
                r["pill"].set("Downloaded", "neutral")
            else:
                r["pill"].set("")
            if not downloading:
                pf = r["pill"].frame()
                r["pill"].setFrame_(NSMakeRect(pf.origin.x, key_row_y(key) + 18, pf.size.width, pf.size.height))
            # the Remove button sits left of the pill
            if not r["rm"].isHidden():
                pf, rf = r["pill"].frame(), r["rm"].frame()
                r["rm"].setFrame_(NSMakeRect(pf.origin.x - 8 - rf.size.width, rf.origin.y, rf.size.width, rf.size.height))

        ax = bool(AXIsProcessTrustedWithOptions(None))
        self.ax_pill.set("Granted" if ax else "Not granted", "ok" if ax else "warn")
        self.ax_btn.setHidden_(ax)
        if not ax:
            pf, bf = self.ax_pill.frame(), self.ax_btn.frame()
            self.ax_pill.setFrame_(NSMakeRect(bf.origin.x - 10 - pf.size.width, pf.origin.y, pf.size.width, pf.size.height))
        else:
            pf = self.ax_pill.frame()
            self.ax_pill.setFrame_(NSMakeRect(self.ax_btn.frame().origin.x + self.ax_btn.frame().size.width - pf.size.width,
                                              pf.origin.y, pf.size.width, pf.size.height))
        ms = mic_status()
        self.mic_pill.set({3: "Granted", 2: "Denied", 1: "Restricted", 0: "Not asked yet"}.get(ms, "Unknown"),
                          "ok" if ms == 3 else "warn")
        self.mic_btn.setHidden_(ms == 3)
        self.mic_btn.setTitle_("Open Settings" if ms == 2 else "Allow")
        if ms != 3:
            pf, bf = self.mic_pill.frame(), self.mic_btn.frame()
            self.mic_pill.setFrame_(NSMakeRect(bf.origin.x - 10 - pf.size.width, pf.origin.y, pf.size.width, pf.size.height))
        else:
            pf = self.mic_pill.frame()
            self.mic_pill.setFrame_(NSMakeRect(self.mic_btn.frame().origin.x + self.mic_btn.frame().size.width - pf.size.width,
                                               pf.origin.y, pf.size.width, pf.size.height))

        if BUNDLED and self.login_toggle.on != c.login_enabled():
            self.login_toggle.set_on(c.login_enabled(), animated=False)
        if self.clip_toggle.on != bool(CFG["restore_clipboard"]):
            self.clip_toggle.set_on(bool(CFG["restore_clipboard"]), animated=False)

        self.side_status.set(mm.status_text(short=True))
        self.side_dot.color = {"ready": hush.C.OK, "error": hush.C.DANGER}.get(mm.phase, hush.C.ACCENT)
        self.side_dot.setNeedsDisplay_(True)

    def show(self):
        self.refresh()
        if not self.win.isVisible():
            self.win.center()
        self.win.makeKeyAndOrderFront_(None)
        NSApp.activateIgnoringOtherApps_(True)
        if self.timer is None:
            self.timer = NSTimer.scheduledTimerWithTimeInterval_repeats_block_(1.0, True, self._tick)

    def _tick(self, timer):
        if self.win.isVisible():
            self.refresh()
        else:
            timer.invalidate()
            self.timer = None


def key_row_y(key):
    return MODEL_ORDER.index(key) * SettingsWindow.ROW


def menubar_icon():
    """A small template glyph (five bars) for the status item; adapts to light and dark menu bars."""
    img = NSImage.alloc().initWithSize_((18, 18))
    img.lockFocus()
    NSColor.blackColor().set()
    for i, h in enumerate((5, 9, 14, 9, 5)):
        NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(NSMakeRect(2.5 + i * 3, 9 - h / 2, 2, h), 1, 1).fill()
    img.unlockFocus()
    img.setTemplate_(True)
    return img


# --------------------------------------------------------------------------- controller


class MenuTarget(NSObject):
    def initWithController_(self, ctrl):
        self = objc.super(MenuTarget, self).init()
        self.ctrl = ctrl
        return self

    def toggle_(self, sender):
        self.ctrl.toggle()

    def quit_(self, sender):
        NSApp.terminate_(None)

    def settings_(self, sender):
        self.ctrl.settings.show()

    def grantAccessibility_(self, sender):
        self.ctrl.grant_accessibility()

    def showLog_(self, sender):
        subprocess.Popen(["open", CFG["log"]])


class Controller:
    def __init__(self):
        self.state = "idle"  # idle | recording | transcribing | notice
        self.overlay = Overlay()
        self.recorder = Recorder(on_level=lambda lv: AppHelper.callAfter(self.overlay.push_level, lv))
        self.models = ModelManager(CFG)
        self.models.listeners.append(self._models_changed)
        self.menu_target = MenuTarget.alloc().initWithController_(self)
        self.recording_shortcut = False
        self._rec_monitor = None
        self._rec = None
        self._menu()
        self.settings = SettingsWindow(self)
        self.models.load(CFG["model"])
        self.reopen_requested = False
        signal.signal(signal.SIGUSR1, lambda *_: setattr(self, "reopen_requested", True))
        NSTimer.scheduledTimerWithTimeInterval_repeats_block_(0.25, True, self._tick)

    # -- menu bar
    def _mi(self, title, action, key=""):
        mi = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(title, action, key)
        mi.setTarget_(self.menu_target)
        return mi

    def _menu(self):
        self.item = NSStatusBar.systemStatusBar().statusItemWithLength_(NSVariableStatusItemLength)
        self.item.button().setImage_(menubar_icon())
        self.item.button().setAppearsDisabled_(True)
        menu = NSMenu.alloc().init()
        self.status_mi = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_("Loading…", None, "")
        self.status_mi.setEnabled_(False)
        menu.addItem_(self.status_mi)
        menu.addItem_(NSMenuItem.separatorItem())
        self.toggle_mi = self._mi(f"Start dictation  ({hotkey_hint(CFG['hotkey'])})", "toggle:")
        menu.addItem_(self.toggle_mi)
        menu.addItem_(self._mi("Settings…", "settings:", ","))
        menu.addItem_(NSMenuItem.separatorItem())
        self.ax_mi = self._mi("Grant Accessibility Permission…", "grantAccessibility:")
        menu.addItem_(self.ax_mi)
        menu.addItem_(self._mi("Show Log", "showLog:"))
        menu.addItem_(NSMenuItem.separatorItem())
        menu.addItem_(self._mi("Quit Khachiwhisper", "quit:", "q"))
        self.item.setMenu_(menu)

    def _models_changed(self):
        self.status_mi.setTitle_(self.models.status_text())
        self.item.button().setAppearsDisabled_(self.models.phase not in ("ready", "error"))
        if self.settings.win.isVisible():
            self.settings.refresh()

    def _tick(self, timer):
        self.ax_mi.setHidden_(bool(AXIsProcessTrustedWithOptions(None)))
        if self.reopen_requested:
            self.reopen_requested = False
            self.settings.show()

    # -- settings actions
    def set_hotkey(self, hk):
        hk = normalize_hotkey(hk)
        if not hk:
            return
        CFG["hotkey"] = hk
        save_config(CFG)
        self.overlay = Overlay()  # footer keycaps depend on the hotkey
        self.recorder.on_level = lambda lv: AppHelper.callAfter(self.overlay.push_level, lv)
        self.toggle_mi.setTitle_(f"Start dictation  ({hotkey_hint(hk)})")
        log(f"hotkey -> {hotkey_hint(hk)}")

    # -- shortcut recorder: captures the next keys typed while the settings window has focus
    def begin_shortcut_recording(self):
        if self.recording_shortcut:
            return
        from AppKit import NSEvent
        rec = ShortcutRecorder()
        self._rec = rec
        self.recording_shortcut = True          # the global hotkey tap stands down meanwhile
        ui = self.settings
        ui.set_recording(True)

        def finish(hk):
            if self._rec is not rec:
                return
            self.end_shortcut_recording()
            if hk:
                self.set_hotkey(hk)
            ui.set_recording(False)

        def handler(event):
            t = int(event.type())
            if t not in (10, 12):
                return event
            chars = event.charactersIgnoringModifiers() if t == 10 else ""
            rep = bool(event.isARepeat()) if t == 10 else False
            status, val = rec.feed(t, int(event.keyCode()), int(event.modifierFlags()), chars, rep)
            if status == "done":
                finish(val)
            elif status == "cancel":
                finish(None)
            elif status == "invalid":
                ui.set_hint(val, hush.C.WARN)
            return None  # swallow everything while recording

        self._rec_monitor = NSEvent.addLocalMonitorForEventsMatchingMask_handler_((1 << 10) | (1 << 12), handler)
        AppHelper.callLater(15.0, lambda: finish(None))

    def end_shortcut_recording(self):
        from AppKit import NSEvent
        if self._rec_monitor is not None:
            NSEvent.removeMonitor_(self._rec_monitor)
            self._rec_monitor = None
        self.recording_shortcut = False
        self._rec = None

    def set_language(self, code):
        CFG["language"] = code
        save_config(CFG)
        self._models_changed()
        log(f"language -> {code}")

    def set_model(self, key):
        if key == CFG["model"] and self.models.key == key:
            return
        if not self.models.is_downloaded(key):
            log(f"model {key} is not downloaded — use Download first")
            self.settings.refresh()
            return
        CFG["model"] = key
        if CFG["language"] not in MODELS[key]["langs"]:
            CFG["language"] = "en"
        save_config(CFG)
        log(f"model -> {key}")
        self.models.load(key)
        self.settings.refresh()

    def set_restore_clipboard(self, on):
        CFG["restore_clipboard"] = bool(on)
        save_config(CFG)

    def remove_model(self, key):
        if self.models.remove(key):
            self.settings.refresh()

    def open_cache(self):
        from huggingface_hub import constants
        subprocess.Popen(["open", constants.HF_HUB_CACHE])

    def open_config(self):
        subprocess.Popen(["open", "-t", str(CONFIG_PATH)])

    def show_log(self):
        subprocess.Popen(["open", CFG["log"]])

    def grant_accessibility(self):
        AXIsProcessTrustedWithOptions({kAXTrustedCheckOptionPrompt: True})
        subprocess.Popen(["open", "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility"])

    def request_microphone(self):
        st = mic_status()
        if st == 0:
            from AVFoundation import AVCaptureDevice
            AVCaptureDevice.requestAccessForMediaType_completionHandler_(
                "soun", lambda ok: AppHelper.callAfter(self.settings.refresh))
        else:
            subprocess.Popen(["open", "x-apple.systempreferences:com.apple.preference.security?Privacy_Microphone"])

    def login_enabled(self):
        try:
            from ServiceManagement import SMAppService
            return SMAppService.mainAppService().status() == 1
        except Exception:  # noqa: BLE001
            return False

    def toggle_login(self):
        try:
            from ServiceManagement import SMAppService
            svc = SMAppService.mainAppService()
            if svc.status() == 1:
                svc.unregisterAndReturnError_(None)
            else:
                svc.registerAndReturnError_(None)
            log(f"start at login -> {'enabled' if svc.status() == 1 else 'disabled'}")
        except Exception as e:  # noqa: BLE001
            log("start at login failed:", repr(e))
        self.settings.refresh()

    # -- notices
    def notice(self, text, seconds=2.4):
        if self.state != "idle":
            return
        self.state = "notice"
        self.overlay.show()
        self.overlay.set_notice(text)
        log(f"notice: {text}")
        AppHelper.callLater(seconds, self._reset)

    def startup(self):
        if CFG.get("_fresh") or not AXIsProcessTrustedWithOptions(None) or not self.models.is_downloaded(CFG["model"]):
            self.settings.show()
        else:
            self.notice(f"Khachiwhisper is running · press {hotkey_hint(CFG['hotkey'])} to dictate", 3.0)

    # -- state machine (always on the main thread)
    def toggle(self):
        if self.state == "notice":
            return
        if self.state == "idle" and not self.models.ready.is_set():
            self.notice(self.models.status_text(), 1.8)
            return
        if self.state == "idle":
            self.state = "recording"
            self.overlay.show()
            try:
                self.recorder.start()
                log("recording started")
            except Exception as e:  # noqa: BLE001
                log("mic error:", repr(e))
                self.overlay.set_notice("Microphone unavailable — check Permissions in Settings")
                AppHelper.callLater(2.0, self._reset)
        elif self.state == "recording":
            self.state = "transcribing"
            audio = self.recorder.stop()
            secs = len(audio) / SAMPLE_RATE
            log(f"recording stopped: {secs:.1f}s")
            rms = float(np.sqrt(np.mean(audio * audio))) if len(audio) else 0.0
            if secs < CFG["min_seconds"] or rms < CFG["silence_rms"]:
                log("dropped (too short / silent)")
                self._reset()
                return
            self.overlay.set_busy("Transcribing")
            threading.Thread(target=self._transcribe, args=(audio,), daemon=True).start()

    def cancel(self):
        if self.state == "recording":
            self.recorder.stop()
            log("cancelled")
            self._reset()

    def _transcribe(self, audio):
        t0 = time.time()
        try:
            text = self.models.transcribe(audio)
            log(f"transcribed in {time.time() - t0:.2f}s: {text!r}")
        except Exception as e:  # noqa: BLE001
            log("transcribe error:", repr(e))
            text = ""
        AppHelper.callAfter(self._finish, text)

    def _finish(self, text):
        self.overlay.hide()
        self.state = "idle"
        if text:
            paste_text(text)

    def _reset(self):
        self.overlay.hide()
        self.state = "idle"


# --------------------------------------------------------------------------- global hotkey (event tap)


def start_hotkey(ctrl: Controller):
    held = {"down": False, "other": False}

    def intercept(event_type, event):
        if ctrl.recording_shortcut:
            return event                           # the settings window is capturing a new shortcut
        hk = CFG["hotkey"]
        keycode = Quartz.CGEventGetIntegerValueField(event, Quartz.kCGKeyboardEventKeycode)
        flags = Quartz.CGEventGetFlags(event)

        if event_type == Quartz.kCGEventFlagsChanged:
            if hk["kind"] == "tap" and keycode == hk["keycode"]:
                down = bool(flags & TAP_KEYS[keycode][1])
                if down:
                    held["down"], held["other"] = True, False
                elif held["down"]:
                    held["down"] = False
                    if not held["other"]:          # a clean tap, not part of a shortcut
                        AppHelper.callAfter(ctrl.toggle)
            return event

        if event_type not in (Quartz.kCGEventKeyDown, Quartz.kCGEventKeyUp):
            return event

        if held["down"]:
            held["other"] = True                   # the tap key is being used as a modifier

        if hk["kind"] == "combo" and keycode == hk["keycode"] and (flags & ALL_MOD_MASK) == hotkey_mask(hk):
            if event_type == Quartz.kCGEventKeyDown:
                AppHelper.callAfter(ctrl.toggle)
            return None  # swallow so the combo doesn't type anything

        if keycode == KEY_ESC and ctrl.state == "recording":
            if event_type == Quartz.kCGEventKeyDown:
                AppHelper.callAfter(ctrl.cancel)
            return None
        return event

    def make():
        lst = keyboard.Listener(on_press=lambda k: None, darwin_intercept=intercept)
        lst.daemon = True
        lst.start()
        return lst

    state = {"listener": make()}

    def supervise():
        warned = False
        while True:
            time.sleep(2.0)
            if state["listener"].is_alive():
                if warned:
                    log(f"hotkey listener is up — {hotkey_hint(CFG['hotkey'])} is live")
                    warned = False
                continue
            if not warned:
                log("HOTKEY LISTENER NOT RUNNING — Accessibility permission is missing. "
                    "Grant it in System Settings > Privacy & Security > Accessibility; it starts on its own once granted.")
                warned = True
            if AXIsProcessTrustedWithOptions(None):
                state["listener"] = make()

    threading.Thread(target=supervise, daemon=True, name="hotkey-supervisor").start()
    return state


# --------------------------------------------------------------------------- main


def render(out_path: str):
    """Draw the overlay into a PNG (offscreen; no permissions needed)."""
    from AppKit import NSBitmapImageFileTypePNG
    NSApplication.sharedApplication()
    ov = Overlay()
    rng = np.random.default_rng(1)
    for i in range(BAR_COUNT + 1):
        env = max(0.0, np.sin(i / 7.0)) * (0.6 + 0.4 * np.sin(i / 19.0))
        ov.push_level(float(abs(rng.standard_normal()) * 0.08 * env + 0.0015))
    if "--busy" in sys.argv:
        ov.set_busy()
    v = ov.root
    v.setFrame_(NSMakeRect(0, 0, PANEL_W, PANEL_H))
    rep = v.bitmapImageRepForCachingDisplayInRect_(v.bounds())
    v.cacheDisplayInRect_toBitmapImageRep_(v.bounds(), rep)
    rep.representationUsingType_properties_(NSBitmapImageFileTypePNG, None).writeToFile_atomically_(out_path, True)
    print("wrote", out_path)


def render_settings(out_path: str):
    """Draw the settings window's content into a PNG (offscreen)."""
    from AppKit import NSBitmapImageFileTypePNG
    NSApplication.sharedApplication()
    ctrl = Controller.__new__(Controller)
    ctrl.models = ModelManager(CFG)
    ctrl.models.key, ctrl.models.phase = CFG["model"], "ready"
    ctrl.login_enabled = lambda: False
    ctrl.recording_shortcut = False
    ctrl.settings = SettingsWindow(ctrl)
    if "--pane" in sys.argv:
        ctrl.settings.select(int(sys.argv[sys.argv.index("--pane") + 1]))
    v = ctrl.settings.root
    rep = v.bitmapImageRepForCachingDisplayInRect_(v.bounds())
    v.cacheDisplayInRect_toBitmapImageRep_(v.bounds(), rep)
    rep.representationUsingType_properties_(NSBitmapImageFileTypePNG, None).writeToFile_atomically_(out_path, True)
    print("wrote", out_path)


def demo(seconds: float):
    """Show the overlay with fake levels (no mic, no model, no hotkey) for a visual check."""
    app = NSApplication.sharedApplication()
    app.setActivationPolicy_(NSApplicationActivationPolicyAccessory)
    ov = Overlay()
    ov.show()
    t0 = time.time()

    def tick(timer):
        t = time.time() - t0
        env = max(0.0, np.sin(t * 2.4)) * (0.6 + 0.4 * np.sin(t * 0.9))
        ov.push_level(float(abs(np.random.randn()) * 0.08 * env + 0.0015))
        if seconds - t < 1.5:
            ov.set_busy()
        if t > seconds:
            AppHelper.stopEventLoop()

    NSTimer.scheduledTimerWithTimeInterval_repeats_block_(1 / 30, True, tick)
    AppHelper.runEventLoop()


_CTRL = None


def main():
    global _CTRL
    if "--render" in sys.argv:
        render(sys.argv[sys.argv.index("--render") + 1])
        return
    if "--render-settings" in sys.argv:
        render_settings(sys.argv[sys.argv.index("--render-settings") + 1])
        return
    if "--demo" in sys.argv:
        i = sys.argv.index("--demo")
        demo(float(sys.argv[i + 1]) if len(sys.argv) > i + 1 else 4)
        return
    app = NSApplication.sharedApplication()
    app.setActivationPolicy_(NSApplicationActivationPolicyAccessory)
    if not AXIsProcessTrustedWithOptions({kAXTrustedCheckOptionPrompt: True}):
        log("Accessibility permission not granted yet — waiting for it.")
    _CTRL = Controller()
    start_hotkey(_CTRL)
    AppHelper.callLater(0.6, _CTRL.startup)
    log(f"Khachiwhisper {APP_VERSION} started (model={CFG['model']}, lang={CFG['language']}, hotkey={hotkey_hint(CFG['hotkey'])})")
    AppHelper.runEventLoop()


if __name__ == "__main__":
    main()
