"""Exceptions raised by ``EngineClient``."""

from holdem_solver import protocol


class EngineError(Exception):
    """Base class for every engine client failure."""


class WorkerStartError(EngineError):
    """The worker process did not come up."""


class WorkerDiedError(EngineError):
    """The worker is gone; all engine state (build, iterations) is lost."""


class EngineTimeoutError(EngineError, TimeoutError):
    """The worker did not answer in time; it may still be working."""


class RemoteEngineError(EngineError):
    """The worker reported an error for this call."""

    def __init__(self, code: int, message: str, data: object = None) -> None:
        super().__init__(message)
        self.code = code
        self.data = data
        details = data if isinstance(data, dict) else {}
        self.remote_type: str | None = details.get("type")
        self.remote_traceback: str | None = details.get("traceback")


class NotBuiltError(RemoteEngineError):
    """No game is built on the worker (also the state after a restart)."""


class EngineBusyError(RemoteEngineError):
    """A solve or build is running; only progress/cancel/close are allowed."""


def remote_error(error: dict) -> RemoteEngineError:
    code = int(error["code"])
    match code:
        case protocol.ERROR_NOT_BUILT:
            cls = NotBuiltError
        case protocol.ERROR_BUSY:
            cls = EngineBusyError
        case _:
            cls = RemoteEngineError
    return cls(code, str(error.get("message", "")), error.get("data"))
