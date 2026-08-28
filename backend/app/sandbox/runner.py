"""Sandboxed execution of custom Python functions."""
import threading
from typing import Any

from RestrictedPython import compile_restricted_exec, safe_builtins
from RestrictedPython.Eval import default_guarded_getitem, default_guarded_getiter
from RestrictedPython.Guards import (
    full_write_guard,
    guarded_iter_unpack_sequence,
    safer_getattr,
)
from RestrictedPython.PrintCollector import PrintCollector

_EXTRA_BUILTINS = {
    "list": list,
    "set": set,
    "dict": dict,
    "min": min,
    "max": max,
    "sum": sum,
    "any": any,
    "all": all,
    "map": map,
    "filter": filter,
    "enumerate": enumerate,
    "reversed": reversed,
}

_INPLACE_OPS = {
    "+=": lambda a, b: a + b,
    "-=": lambda a, b: a - b,
    "*=": lambda a, b: a * b,
    "/=": lambda a, b: a / b,
    "//=": lambda a, b: a // b,
    "%=": lambda a, b: a % b,
    "**=": lambda a, b: a ** b,
    "&=": lambda a, b: a & b,
    "|=": lambda a, b: a | b,
    "^=": lambda a, b: a ^ b,
    ">>=": lambda a, b: a >> b,
    "<<=": lambda a, b: a << b,
}


def _inplacevar_(op: str, lhs: Any, rhs: Any) -> Any:
    try:
        return _INPLACE_OPS[op](lhs, rhs)
    except KeyError:
        raise ValueError(f"Unknown operator: {op}") from None


def _build_namespace(input_state: dict[str, Any]) -> dict[str, Any]:
    namespace: dict[str, Any] = {"__builtins__": {**safe_builtins, **_EXTRA_BUILTINS}}
    namespace.update({
        "_getattr_": safer_getattr,
        "_getitem_": default_guarded_getitem,
        "_write_": full_write_guard,
        "_getiter_": default_guarded_getiter,
        "_inplacevar_": _inplacevar_,
        "_iter_unpack_sequence_": guarded_iter_unpack_sequence,
        "_print_": PrintCollector,
        "state": input_state,
        "result": {},
    })
    return namespace


def _execute(code: str, input_state: dict[str, Any]) -> dict[str, Any]:
    compiled = compile_restricted_exec(code)
    if compiled.errors:
        raise RuntimeError(f"Compilation error: {compiled.errors[0]}")

    namespace = _build_namespace(input_state)
    exec(compiled.code, namespace)
    return namespace["result"]


def run_sandboxed(code: str, input_state: dict[str, Any], timeout: int = 30) -> dict[str, Any]:
    """Execute Python code in a RestrictedPython sandbox.

    Returns the ``result`` dict written by the code, or ``{"error": ...}``
    on compile/runtime failure or timeout.

    Phase 4: Will use container-based isolation (the worker thread is a
    daemon, so a timed-out execution may keep running but cannot block
    process shutdown).
    """
    container: dict[str, Any] = {}

    def target() -> None:
        try:
            container["result"] = _execute(code, input_state)
        except BaseException as e:
            container["error"] = f"{type(e).__name__}: {e}"

    worker = threading.Thread(target=target, daemon=True)
    worker.start()
    worker.join(timeout)

    if worker.is_alive():
        return {"error": f"Execution timed out after {timeout}s"}
    if "error" in container:
        return {"error": container["error"]}
    return container.get("result", {})
