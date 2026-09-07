"""Stand-in for numba: mlx-whisper's timing module decorates functions with @numba.jit at import
time. Word-level timestamps are never used here, so the decorators become no-ops."""


def jit(*args, **kwargs):
    if args and callable(args[0]) and len(args) == 1 and not kwargs:
        return args[0]
    return lambda fn: fn


njit = jit
