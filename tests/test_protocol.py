import ast
import io
import sys
from pathlib import Path

import pytest

from holdem_solver import protocol

ROOT = Path(__file__).resolve().parents[1] / "holdem_solver"
# Everything the Python 3.9 runtime imports.
PY39_PACKAGES = ("protocol", "engine_worker", "engine")


def _sources(package):
    return sorted((ROOT / package).glob("*.py"))


def test_request_round_trip():
    request = protocol.make_request("solve", {"iterations": 10}, request_id=7)
    line = protocol.encode_message(request)
    assert line.endswith(b"\n") and line.count(b"\n") == 1
    message = protocol.read_message(io.BytesIO(line))
    assert protocol.parse_request(message) == (7, "solve", {"iterations": 10})


def test_response_round_trip():
    ok = protocol.decode_message(protocol.encode_message(protocol.make_result(1, {"a": 1})))
    assert protocol.parse_response(ok) == (1, {"a": 1}, None)
    failed = protocol.decode_message(
        protocol.encode_message(protocol.make_error(2, protocol.ERROR_BUSY, "busy", {"type": "X"}))
    )
    response_id, result, error = protocol.parse_response(failed)
    assert (response_id, result) == (2, None)
    assert error == {"code": protocol.ERROR_BUSY, "message": "busy", "data": {"type": "X"}}


def test_read_message_eof_and_truncation():
    assert protocol.read_message(io.BytesIO(b"")) is None
    with pytest.raises(protocol.ProtocolError) as excinfo:
        protocol.read_message(io.BytesIO(b'{"jsonrpc":"2.0"'))
    assert excinfo.value.code == protocol.ERROR_PARSE


@pytest.mark.parametrize(
    "line, code",
    [
        (b"not json\n", protocol.ERROR_PARSE),
        (b"[1]\n", protocol.ERROR_INVALID_REQUEST),
        (b'{"jsonrpc":"1.0","method":"ping"}\n', protocol.ERROR_INVALID_REQUEST),
    ],
)
def test_decode_rejects_bad_messages(line, code):
    with pytest.raises(protocol.ProtocolError) as excinfo:
        protocol.decode_message(line)
    assert excinfo.value.code == code


@pytest.mark.parametrize(
    "message, code",
    [
        ({"jsonrpc": "2.0", "id": 1, "method": "nope"}, protocol.ERROR_METHOD_NOT_FOUND),
        ({"jsonrpc": "2.0", "id": 1, "method": 3}, protocol.ERROR_INVALID_REQUEST),
        ({"jsonrpc": "2.0", "id": 1, "method": "ping", "params": [1]}, protocol.ERROR_INVALID_PARAMS),
    ],
)
def test_parse_request_rejects(message, code):
    with pytest.raises(protocol.ProtocolError) as excinfo:
        protocol.parse_request(message)
    assert excinfo.value.code == code


def test_nan_and_oversized_messages_are_refused():
    with pytest.raises(ValueError):
        protocol.encode_message(protocol.make_result(1, float("nan")))
    with pytest.raises(protocol.ProtocolError):
        protocol.encode_message(protocol.make_result(1, "x" * protocol.MAX_MESSAGE_BYTES))


def test_array_ref_must_stay_inside_shm_dir(tmp_path):
    allowed = tmp_path / "worker"
    allowed.mkdir()
    good = protocol.array_ref(str(allowed / "a.npy"), (2, 3), "float32")
    assert good == {protocol.ARRAY_REF_KEY: str(allowed / "a.npy"), "shape": [2, 3], "dtype": "float32"}
    assert protocol.resolve_array_ref(good, str(allowed)) == str((allowed / "a.npy").resolve())
    for bad in (str(tmp_path / "other.npy"), str(allowed / ".." / "x.npy"), str(allowed / "a.txt"), "/etc/passwd"):
        with pytest.raises(ValueError):
            protocol.resolve_array_ref(protocol.array_ref(bad, (1,), "float32"), str(allowed))
    with pytest.raises(ValueError):
        protocol.resolve_array_ref({"path": "x"}, str(allowed))


def test_paths_are_per_gpu(monkeypatch):
    monkeypatch.delenv("HOLDEM_SOLVER_RUNTIME_DIR", raising=False)
    monkeypatch.delenv("HOLDEM_SOLVER_SHM_DIR", raising=False)
    assert protocol.socket_path(0) != protocol.socket_path(1)
    assert protocol.socket_path(0).endswith("gpu0.sock")
    assert protocol.shm_dir(1).startswith(protocol.SHM_ROOT + "/") and protocol.shm_dir(1).endswith("gpu1")
    assert len(protocol.socket_path(0)) < 100


@pytest.mark.parametrize("package", PY39_PACKAGES)
def test_python39_compatible_syntax(package):
    """No match statements and no ``X | Y`` annotations in 3.9-runtime code."""
    for path in _sources(package):
        source = path.read_text()
        tree = ast.parse(source, filename=str(path), feature_version=(3, 9))
        annotations = []
        for node in ast.walk(tree):
            assert type(node).__name__ != "Match", "%s uses a match statement" % path
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                args = node.args
                every = args.posonlyargs + args.args + args.kwonlyargs + [args.vararg, args.kwarg]
                annotations += [arg.annotation for arg in every if arg is not None]
                annotations.append(node.returns)
            elif isinstance(node, ast.AnnAssign):
                annotations.append(node.annotation)
        for annotation in annotations:
            if annotation is None:
                continue
            for sub in ast.walk(annotation):
                assert not (isinstance(sub, ast.BinOp) and isinstance(sub.op, ast.BitOr)), (
                    "%s:%d uses an X | Y annotation" % (path, annotation.lineno)
                )


@pytest.mark.skipif(sys.version_info < (3, 10), reason="sys.stdlib_module_names needs Python 3.10+")
def test_protocol_has_no_third_party_imports():
    for path in _sources("protocol"):
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0:
                names = [node.module]
            else:
                continue
            for name in names:
                assert name.split(".")[0] in sys.stdlib_module_names, "%s imports %s" % (path, name)
