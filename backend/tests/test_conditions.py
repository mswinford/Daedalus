"""Unit tests for the condition evaluator (app.engine.conditions)."""
import pytest

from app.engine.conditions import (
    ConditionError,
    _coerce_literal,
    _resolve_path,
    evaluate_condition,
)
from schema.models import ConditionConfig, ConditionType


def cond(type_, expression):
    return ConditionConfig(type=type_, expression=expression)


# ── _resolve_path ──────────────────────────────────────────────────────
def test_resolve_top_level():
    assert _resolve_path({"a": 1}, "a") == 1


def test_resolve_dollar_prefix():
    assert _resolve_path({"data": {"score": 5}}, "$.data.score") == 5


def test_resolve_nested_and_list_index():
    state = {"rows": [{"v": 7}]}
    assert _resolve_path(state, "rows.0.v") == 7


def test_resolve_missing_key_is_none():
    assert _resolve_path({"a": 1}, "b") is None


def test_resolve_list_out_of_range_is_none():
    assert _resolve_path({"l": [1, 2]}, "l.5") is None


def test_resolve_scalar_traversal_is_none():
    assert _resolve_path({"a": 1}, "a.b") is None


# ── _coerce_literal ────────────────────────────────────────────────────
def test_coerce_int_float_string_bool_none():
    assert _coerce_literal("42") == 42
    assert _coerce_literal("3.5") == 3.5
    assert _coerce_literal('"hello"') == "hello"
    assert _coerce_literal("'hi'") == "hi"
    assert _coerce_literal("true") is True
    assert _coerce_literal("false") is False
    assert _coerce_literal("null") is None
    assert _coerce_literal("abc") == "abc"  # bare word stays a string


# ── json_path comparisons ──────────────────────────────────────────────
@pytest.mark.parametrize(
    "expr, state, expected",
    [
        ("$.data.score >= 80", {"data": {"score": 95}}, True),
        ("$.data.score >= 80", {"data": {"score": 40}}, False),
        ("$.n == 3", {"n": 3}, True),
        ("$.n != 3", {"n": 4}, True),
        ("$.s > 'b'", {"s": "c"}, True),
        ("$.s < 'b'", {"s": "a"}, True),
    ],
)
def test_json_path_comparison(expr, state, expected):
    assert evaluate_condition(cond("json_path", expr), state) is expected


def test_ordering_on_missing_value_is_false():
    # No `score` in state -> left resolves to None -> ordering op is False, not an error.
    assert evaluate_condition(cond("json_path", "$.data.score >= 80"), {"data": {}}) is False


def test_type_mismatch_raises_condition_error():
    with pytest.raises(ConditionError):
        evaluate_condition(cond("json_path", "$.n > 'text'"), {"n": 5})


def test_json_path_bare_truthiness():
    assert evaluate_condition(cond("json_path", "$.flag"), {"flag": True}) is True
    assert evaluate_condition(cond("json_path", "$.flag"), {"flag": False}) is False
    assert evaluate_condition(cond("json_path", "$.missing"), {}) is False


# ── regex ──────────────────────────────────────────────────────────────
def test_regex_match_and_nonmatch():
    assert evaluate_condition(cond("regex", "error"), {"output": "error: disk full"}) is True
    assert evaluate_condition(cond("regex", "error"), {"output": "all good"}) is False


def test_regex_non_string_output_coerced():
    assert evaluate_condition(cond("regex", "42"), {"output": 42}) is True


def test_regex_invalid_pattern_raises():
    with pytest.raises(ConditionError):
        evaluate_condition(cond("regex", "(unclosed"), {"output": "x"})


# ── llm (deferred) ─────────────────────────────────────────────────────
def test_llm_condition_not_implemented():
    with pytest.raises(NotImplementedError):
        evaluate_condition(cond("llm", "is this positive?"), {"output": "great"})
