"""Evaluate ConditionConfig expressions against workflow state."""
import re
from typing import Any

from schema.models import ConditionConfig, ConditionType


class ConditionError(ValueError):
    """Raised when a condition cannot be evaluated or no branch matches."""


def _resolve_path(state: dict, path: str) -> Any:
    """Resolve a dot-separated path (optional '$.' prefix) into state."""
    path = path.strip()
    if path.startswith("$."):
        path = path[2:]
    elif path.startswith("$"):
        path = path[1:]
    if not path:
        return state
    current: Any = state
    for part in path.split("."):
        if not part:
            continue
        if isinstance(current, dict):
            if part not in current:
                return None  # missing key -> no match (falsy)
            current = current[part]
        elif isinstance(current, (list, tuple)):
            try:
                idx = int(part)
            except ValueError:
                return None
            if not -len(current) <= idx < len(current):
                return None
            current = current[idx]
        else:
            return None  # cannot traverse into a scalar -> no match
    return current


def _coerce_literal(raw: str) -> Any:
    raw = raw.strip()
    if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in ("'", '"'):
        return raw[1:-1]
    low = raw.lower()
    if low == "true":
        return True
    if low == "false":
        return False
    if low in ("none", "null"):
        return None
    try:
        return int(raw)
    except ValueError:
        pass
    try:
        return float(raw)
    except ValueError:
        pass
    return raw


_CMP_RE = re.compile(r"^(.*?)\s*(==|!=|>=|<=|>|<)\s*(.*)$")


def _eval_comparison(expr: str, state: dict) -> bool:
    m = _CMP_RE.match(expr.strip())
    if not m:
        return False
    left_expr, op, right_raw = m.groups()
    left = _resolve_path(state, left_expr)
    right = _coerce_literal(right_raw)

    def compare(a: Any, b: Any) -> bool:
        if op == "==":
            return a == b
        if op == "!=":
            return a != b
        if op in ("<", ">", "<=", ">=") and (a is None or b is None):
            return False  # ordering on a missing value -> no match
        if isinstance(a, (int, float)) and isinstance(b, (int, float)) and not isinstance(a, bool):
            return {"<": a < b, ">": a > b, "<=": a <= b, ">=": a >= b}[op]
        if isinstance(a, str) and isinstance(b, str):
            return {"<": a < b, ">": a > b, "<=": a <= b, ">=": a >= b}[op]
        raise ConditionError(f"Cannot compare {a!r} {op} {b!r}")

    try:
        return compare(left, right)
    except ConditionError:
        raise
    except Exception as e:
        raise ConditionError(f"Comparison failed for '{expr}': {e}")


def evaluate_condition(condition: ConditionConfig, state: dict) -> bool:
    """Evaluate a single condition against the current state."""
    expr = condition.expression.strip()

    if condition.type == ConditionType.JSON_PATH:
        m = _CMP_RE.match(expr)
        if m:
            return _eval_comparison(expr, state)
        return bool(_resolve_path(state, expr))

    if condition.type == ConditionType.REGEX:
        try:
            pattern = re.compile(expr)
        except re.error as e:
            raise ConditionError(f"Invalid regex {expr!r}: {e}")
        target = state.get("output", "")
        if not isinstance(target, str):
            target = str(target)
        return pattern.search(target) is not None

    if condition.type == ConditionType.LLM:
        raise NotImplementedError(
            "LLM-based conditions are not implemented yet (needs a model binding)"
        )

    raise ConditionError(f"Unknown condition type: {condition.type}")
