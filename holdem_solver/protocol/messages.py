"""JSON-RPC 2.0 messages, framed as one UTF-8 JSON object per line."""

import json
from typing import Any, Dict, Optional, Tuple

JSONRPC_VERSION = "2.0"
PROTOCOL_VERSION = 1
MAX_MESSAGE_BYTES = 16 * 1024 * 1024

METHODS = (
    "ping",
    "build",
    "solve",
    "solve_wait",
    "progress",
    "cancel",
    "get_strategy",
    "reset",
    "close",
    "shutdown",
)

# ``progress`` reports one of these as ``state``.
JOB_STATES = ("unbuilt", "idle", "solving", "done", "cancelled", "failed")

ERROR_PARSE = -32700
ERROR_INVALID_REQUEST = -32600
ERROR_METHOD_NOT_FOUND = -32601
ERROR_INVALID_PARAMS = -32602
ERROR_INTERNAL = -32603
# Application errors (JSON-RPC reserves -32000..-32099 for the server).
ERROR_ENGINE = -32000
ERROR_NOT_BUILT = -32001
ERROR_BUSY = -32002
ERROR_NO_JOB = -32003


class ProtocolError(Exception):
    """A message that is not valid JSON-RPC 2.0 for this protocol."""

    def __init__(self, code: int, message: str, data: Any = None) -> None:
        Exception.__init__(self, message)
        self.code = code
        self.message = message
        self.data = data


def make_request(method: str, params: Optional[Dict[str, Any]] = None, request_id: Any = None) -> Dict[str, Any]:
    return {
        "jsonrpc": JSONRPC_VERSION,
        "id": request_id,
        "method": method,
        "params": {} if params is None else params,
    }


def make_result(request_id: Any, result: Any) -> Dict[str, Any]:
    return {"jsonrpc": JSONRPC_VERSION, "id": request_id, "result": result}


def make_error(request_id: Any, code: int, message: str, data: Any = None) -> Dict[str, Any]:
    error: Dict[str, Any] = {"code": int(code), "message": str(message)}
    if data is not None:
        error["data"] = data
    return {"jsonrpc": JSONRPC_VERSION, "id": request_id, "error": error}


def encode_message(message: Dict[str, Any]) -> bytes:
    """Serialize one message as a newline-terminated JSON line."""
    data = json.dumps(message, separators=(",", ":"), allow_nan=False).encode("utf-8")
    if len(data) + 1 > MAX_MESSAGE_BYTES:
        raise ProtocolError(
            ERROR_INVALID_REQUEST,
            "message is %d bytes; bulk data must go through /dev/shm .npy files" % len(data),
        )
    return data + b"\n"


def decode_message(line: bytes) -> Dict[str, Any]:
    try:
        message = json.loads(line.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise ProtocolError(ERROR_PARSE, "invalid JSON: %s" % exc)
    if not isinstance(message, dict):
        raise ProtocolError(ERROR_INVALID_REQUEST, "message must be a JSON object")
    if message.get("jsonrpc") != JSONRPC_VERSION:
        raise ProtocolError(ERROR_INVALID_REQUEST, "jsonrpc must be %r" % JSONRPC_VERSION)
    return message


def read_message(stream: Any) -> Optional[Dict[str, Any]]:
    """Read one message from a binary file-like object; ``None`` at EOF."""
    line = stream.readline(MAX_MESSAGE_BYTES + 1)
    if not line:
        return None
    if not line.endswith(b"\n"):
        if len(line) > MAX_MESSAGE_BYTES:
            raise ProtocolError(ERROR_INVALID_REQUEST, "message exceeds %d bytes" % MAX_MESSAGE_BYTES)
        raise ProtocolError(ERROR_PARSE, "connection closed mid-message")
    return decode_message(line)


def parse_request(message: Dict[str, Any]) -> Tuple[Any, str, Dict[str, Any]]:
    """Return ``(id, method, params)``; params must be named (an object)."""
    method = message.get("method")
    if not isinstance(method, str):
        raise ProtocolError(ERROR_INVALID_REQUEST, "method must be a string")
    params = message.get("params", {})
    if params is None:
        params = {}
    if not isinstance(params, dict):
        raise ProtocolError(ERROR_INVALID_PARAMS, "params must be an object")
    if method not in METHODS:
        raise ProtocolError(ERROR_METHOD_NOT_FOUND, "unknown method %r" % method)
    return message.get("id"), method, params


def parse_response(message: Dict[str, Any]) -> Tuple[Any, Any, Optional[Dict[str, Any]]]:
    """Return ``(id, result, error)``; exactly one of result/error is set."""
    has_result = "result" in message
    error = message.get("error")
    if has_result == (error is not None):
        raise ProtocolError(ERROR_INVALID_REQUEST, "response needs exactly one of result/error")
    if error is not None:
        if not isinstance(error, dict) or not isinstance(error.get("code"), int):
            raise ProtocolError(ERROR_INVALID_REQUEST, "malformed error object")
        return message.get("id"), None, error
    return message.get("id"), message["result"], None
