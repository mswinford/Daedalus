"""Unit tests for the RestrictedPython sandbox (app.sandbox.runner)."""
import pytest

from app.sandbox.runner import run_sandboxed


def test_writes_result_dict():
    out = run_sandboxed('result["doubled"] = 21 * 2', {})
    assert out == {"doubled": 42}


def test_reads_state():
    code = 'result["msg"] = state.get("output", "") + "!"'
    out = run_sandboxed(code, {"output": "hello"})
    assert out == {"msg": "hello!"}


def test_extra_builtins_available():
    code = 'result["n"] = sum([1, 2, 3])\nresult["m"] = max(4, 9)'
    out = run_sandboxed(code, {})
    assert out == {"n": 6, "m": 9}


def test_blocked_import_returns_error():
    out = run_sandboxed('result["x"] = __import__("os")', {})
    assert "error" in out


def test_blocked_open_returns_error():
    out = run_sandboxed('result["x"] = open("/etc/passwd").read()', {})
    assert "error" in out


def test_runtime_error_captured():
    out = run_sandboxed("result['x'] = 1 / 0", {})
    assert "error" in out
    assert "ZeroDivisionError" in out["error"]


def test_syntax_error_returns_error():
    out = run_sandboxed("def broken(:", {})
    assert "error" in out


def test_infinite_loop_times_out():
    out = run_sandboxed("while True:\n    pass", {}, timeout=1)
    assert "error" in out
    assert "timed out" in out["error"]
