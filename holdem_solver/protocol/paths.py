"""Socket and /dev/shm locations, and the JSON reference to a .npy file."""

import os
import stat
import tempfile
from typing import Any, Dict, Optional, Sequence

SHM_ROOT = "/dev/shm"
ARRAY_REF_KEY = "__npy__"

_PREFIX = "holdem_solver"


def _worker_name(gpu: Optional[int]) -> str:
    return "cpu" if gpu is None else "gpu%d" % int(gpu)


def runtime_dir() -> str:
    """Per-user directory for worker sockets, locks, and logs."""
    base = os.environ.get("HOLDEM_SOLVER_RUNTIME_DIR")
    if base:
        return base
    return os.path.join(tempfile.gettempdir(), "%s_%d" % (_PREFIX, os.getuid()))


def ensure_private_dir(path: str) -> str:
    """Create ``path`` (mode 0700) and refuse one another user could tamper with.

    The default locations sit in world-writable ``/tmp`` and ``/dev/shm``, so a
    directory that already exists is only used if it is a real directory owned
    by this user, not writable by group or others, inside a parent that other
    users cannot rename entries in.
    """
    path = os.path.abspath(path)
    os.makedirs(path, mode=0o700, exist_ok=True)
    info = os.lstat(path)
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o022:
        raise PermissionError("%s must be a directory owned by uid %d and not group/world writable" % (path, os.getuid()))
    parent = os.stat(os.path.dirname(path))
    if parent.st_uid not in (0, os.getuid()) or (parent.st_mode & 0o022 and not parent.st_mode & stat.S_ISVTX):
        raise PermissionError("%s is inside a directory other users can modify" % path)
    return path


def socket_path(gpu: Optional[int]) -> str:
    return os.path.join(runtime_dir(), "%s.sock" % _worker_name(gpu))


def shm_dir(gpu: Optional[int]) -> str:
    """Per-user, per-worker directory under /dev/shm for bulk arrays."""
    root = os.environ.get("HOLDEM_SOLVER_SHM_DIR")
    if not root:
        root = os.path.join(SHM_ROOT, "%s_%d" % (_PREFIX, os.getuid()))
    return os.path.join(root, _worker_name(gpu))


def array_ref(path: str, shape: Sequence[int], dtype: str) -> Dict[str, Any]:
    return {ARRAY_REF_KEY: path, "shape": [int(n) for n in shape], "dtype": str(dtype)}


def is_array_ref(value: Any) -> bool:
    return isinstance(value, dict) and isinstance(value.get(ARRAY_REF_KEY), str)


def resolve_array_ref(ref: Dict[str, Any], allowed_dir: str) -> str:
    """Return the real path of ``ref``, refusing anything outside ``allowed_dir``."""
    if not is_array_ref(ref):
        raise ValueError("not an array reference: %r" % (ref,))
    path = os.path.realpath(ref[ARRAY_REF_KEY])
    root = os.path.realpath(allowed_dir)
    if os.path.commonpath([path, root]) != root or not path.endswith(".npy"):
        raise ValueError("array path %r is outside %r" % (ref[ARRAY_REF_KEY], allowed_dir))
    return path
