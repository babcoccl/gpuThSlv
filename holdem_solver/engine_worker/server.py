"""Unix-socket JSON-RPC server and the worker entry point."""

import argparse
import fcntl
import logging
import os
import signal
import socket
import socketserver
import sys
import threading
import time
from typing import Any, List, Optional

from holdem_solver import protocol

from .service import EngineService, RpcError

LOG = logging.getLogger("holdem_solver.engine_worker")

# sockaddr_un.sun_path is 108 bytes on Linux, including the terminating NUL.
_MAX_SOCKET_PATH = 107


class _Handler(socketserver.StreamRequestHandler):
    def handle(self) -> None:
        service: EngineService = self.server.service  # type: ignore[attr-defined]
        while True:
            request_id = None
            try:
                message = protocol.read_message(self.rfile)
                if message is None:
                    return
                request_id = message.get("id")
                request_id, method, params = protocol.parse_request(message)
                response = protocol.make_result(request_id, service.dispatch(method, params))
            except protocol.ProtocolError as exc:
                response = protocol.make_error(request_id, exc.code, exc.message, exc.data)
            except RpcError as exc:
                response = protocol.make_error(request_id, exc.code, exc.message, exc.data)
            except (ConnectionError, socket.timeout):
                return
            except Exception as exc:
                LOG.exception("internal error")
                response = protocol.make_error(request_id, protocol.ERROR_INTERNAL, "%s: %s" % (type(exc).__name__, exc))
            try:
                self.wfile.write(protocol.encode_message(response))
                self.wfile.flush()
            except (ConnectionError, OSError):
                return
            if service.shutdown_requested:
                threading.Thread(target=self.server.shutdown, daemon=True).start()
                return


class _Server(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    daemon_threads = True
    allow_reuse_address = False
    request_queue_size = 32


def _lock_socket(path: str) -> Any:
    """Take the per-socket lock, then clear a socket left by a killed worker."""
    # O_NOFOLLOW and no O_TRUNC: never write through a planted symlink, and
    # leave a live owner's lock file alone until the lock is ours.
    handle = os.fdopen(os.open(path + ".lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600), "r+")
    try:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        handle.close()
        raise SystemExit("another engine worker already owns %s" % path)
    handle.truncate(0)
    handle.write("%d\n" % os.getpid())
    handle.flush()
    if os.path.lexists(path):
        os.unlink(path)
    return handle


def _watch_parent(parent_pid: int, server: _Server) -> None:
    while os.getppid() == parent_pid:
        time.sleep(0.5)
    LOG.warning("parent %d exited; shutting down", parent_pid)
    server.shutdown()


def _make_engine(kind: str) -> Any:
    if kind == "fake":
        from .fake import FakeEngine

        return FakeEngine(), {"engine": "fake"}, None, None
    from . import real

    info = {"engine": "real"}
    info.update(real.runtime_info())
    return real.RealEngine(), info, real.vram_stats, real.reset_peak_vram


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m holdem_solver.engine_worker")
    parser.add_argument("--gpu", type=int, default=None, help="physical GPU index; sets CUDA_VISIBLE_DEVICES")
    parser.add_argument("--socket", default=None, help="Unix socket path (default: per-user, per-GPU)")
    parser.add_argument("--shm-dir", default=None, help="directory under /dev/shm for .npy transfers")
    parser.add_argument("--engine", choices=("real", "fake"), default="real")
    parser.add_argument("--parent-pid", type=int, default=None, help="exit when this process is no longer the parent")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO, stream=sys.stderr, format="%(asctime)s %(levelname)s worker[%(process)d] %(message)s"
    )
    # One worker owns one GPU: pin before torch is imported.
    if args.gpu is not None:
        os.environ["CUDA_VISIBLE_DEVICES"] = str(args.gpu)

    socket_path = args.socket or protocol.socket_path(args.gpu)
    shm_dir = args.shm_dir or protocol.shm_dir(args.gpu)
    if len(socket_path.encode("utf-8")) > _MAX_SOCKET_PATH:
        raise SystemExit("socket path is longer than %d bytes: %s" % (_MAX_SOCKET_PATH, socket_path))

    os.umask(0o077)
    try:
        protocol.ensure_private_dir(os.path.dirname(socket_path) or ".")
        protocol.ensure_private_dir(shm_dir)
    except OSError as exc:
        raise SystemExit("unsafe worker directory: %s" % exc)
    lock = _lock_socket(socket_path)

    engine, info, vram_stats, reset_peak_vram = _make_engine(args.engine)
    info.update(
        {
            "gpu": args.gpu,
            "cuda_visible_devices": os.environ.get("CUDA_VISIBLE_DEVICES"),
            "python": "%d.%d.%d" % sys.version_info[:3],
            "socket": socket_path,
            "shm_dir": shm_dir,
        }
    )
    service = EngineService(engine, shm_dir, info, vram_stats, reset_peak_vram)
    server = _Server(socket_path, _Handler)
    server.service = service  # type: ignore[attr-defined]

    def on_signal(signum: int, frame: Any) -> None:
        threading.Thread(target=server.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, on_signal)
    signal.signal(signal.SIGINT, on_signal)
    if args.parent_pid is not None:
        threading.Thread(target=_watch_parent, args=(args.parent_pid, server), daemon=True).start()

    LOG.info("ready engine=%s gpu=%s socket=%s shm=%s", args.engine, args.gpu, socket_path, shm_dir)
    try:
        server.serve_forever(poll_interval=0.1)
    finally:
        LOG.info("stopping")
        server.server_close()
        service.stop()
        try:
            os.unlink(socket_path)
        except OSError:
            pass
        lock.close()
    return 0
