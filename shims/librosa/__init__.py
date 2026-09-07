"""Minimal stand-in for librosa used by parakeet-mlx (only librosa.filters.mel is needed)."""
from . import filters  # noqa: F401
__version__ = "0.0-khachiwhisper-shim"
