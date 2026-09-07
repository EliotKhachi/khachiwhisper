"""librosa.filters.mel re-implemented in numpy (htk=False, Slaney scale, optional Slaney norm)."""
import numpy as np


def _hz_to_mel(f):
    f = np.asarray(f, dtype=np.float64)
    f_sp = 200.0 / 3
    mels = f / f_sp
    min_log_hz = 1000.0
    min_log_mel = min_log_hz / f_sp
    logstep = np.log(6.4) / 27.0
    if mels.ndim:
        log_t = f >= min_log_hz
        mels[log_t] = min_log_mel + np.log(f[log_t] / min_log_hz) / logstep
    elif f >= min_log_hz:
        mels = min_log_mel + np.log(f / min_log_hz) / logstep
    return mels


def _mel_to_hz(m):
    m = np.asarray(m, dtype=np.float64)
    f_sp = 200.0 / 3
    freqs = f_sp * m
    min_log_hz = 1000.0
    min_log_mel = min_log_hz / f_sp
    logstep = np.log(6.4) / 27.0
    if m.ndim:
        log_t = m >= min_log_mel
        freqs[log_t] = min_log_hz * np.exp(logstep * (m[log_t] - min_log_mel))
    elif m >= min_log_mel:
        freqs = min_log_hz * np.exp(logstep * (m - min_log_mel))
    return freqs


def mel(*, sr, n_fft, n_mels=128, fmin=0.0, fmax=None, htk=False, norm="slaney", dtype=np.float32):
    if htk:
        raise NotImplementedError("shim supports htk=False only")
    if fmax is None:
        fmax = float(sr) / 2
    weights = np.zeros((n_mels, 1 + n_fft // 2), dtype=dtype)
    fftfreqs = np.fft.rfftfreq(n=n_fft, d=1.0 / sr)
    mel_f = _mel_to_hz(np.linspace(_hz_to_mel(fmin), _hz_to_mel(fmax), n_mels + 2))
    fdiff = np.diff(mel_f)
    ramps = np.subtract.outer(mel_f, fftfreqs)
    for i in range(n_mels):
        lower = -ramps[i] / fdiff[i]
        upper = ramps[i + 2] / fdiff[i + 1]
        weights[i] = np.maximum(0, np.minimum(lower, upper))
    if norm == "slaney":
        enorm = 2.0 / (mel_f[2 : n_mels + 2] - mel_f[:n_mels])
        weights *= enorm[:, np.newaxis]
    elif norm is not None:
        weights /= np.linalg.norm(weights, ord=norm, axis=-1, keepdims=True)
    return weights
