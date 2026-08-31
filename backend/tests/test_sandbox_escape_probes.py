"""Security escape probes for the RestrictedPython sandbox (app.sandbox.runner).

Each probe calls the REAL ``run_sandboxed()`` entry point with a hostile
payload and asserts that no escape succeeds. Payloads are harmless by
construction: no network, no subprocesses, no writes anywhere. An "escape"
would be indicated by a returned value proving reachability (e.g. an os
module attribute such as ``os.getpid()``).

Verified findings against RestrictedPython 8.5 + the runner namespace:

- Dunder attribute access and any underscore-prefixed name are rejected at
  COMPILE time by the AST transformer, so literal dunder chains never reach
  the runtime guards (safer_getattr et al.).
- ``getattr``/``type``/``vars``/``dir``/``globals``/``open`` are absent from
  the namespace entirely, so aliased-reference bypasses (``g = getattr``)
  fail with NameError before any guard is consulted.
- ``eval(...)`` / ``exec(...)`` calls and ``__import__`` are compile errors.
- ``safe_builtins['setattr'/'delattr']`` are replaced by guarded versions
  that route through ``full_write_guard``; its Wrapper only forwards writes
  to objects implementing ``__guarded_setattr__``, which nothing in the
  namespace does — so even plain (non-dunder) attribute writes fail.
- Class statements are broken in this setup (the transformer emits a
  ``__metaclass__`` reference the namespace does not provide), so user code
  cannot create class objects at all.

No probe currently escapes; all tests are plain "blocked" assertions. If a
probe ever starts succeeding, convert it to
``@pytest.mark.xfail(strict=True, reason="sandbox escape: ...")`` so the
suite fails loudly on regression in either direction.
"""
from app.sandbox.runner import run_sandboxed


def _assert_blocked(out: dict) -> None:
    assert "error" in out, f"payload was NOT blocked, result leaked: {out}"


def test_harness_sanity_result_still_works():
    """Guard against false 'blocked' results if run_sandboxed itself breaks."""
    out = run_sandboxed("result['x'] = 40 + 2", {})
    assert out == {"x": 42}


def test_p1_direct_dunder_chain_blocked():
    code = "result['n'] = len(().__class__.__bases__[0].__subclasses__())"
    out = run_sandboxed(code, {})
    _assert_blocked(out)
    assert "invalid attribute name" in out["error"]


def test_p2_aliased_getattr_bypass_blocked():
    # Suspected hole: alias getattr so the compile-time rewrite to
    # _getattr_(...) never happens and the call skips safer_getattr.
    # In practice blocked one level earlier: 'getattr' is not in
    # safe_builtins (8.5) at all, so the alias itself is a NameError.
    code = "g = getattr\nresult['x'] = g((), '__class__')"
    out = run_sandboxed(code, {})
    _assert_blocked(out)
    assert "getattr" in out["error"]


def test_p3_aliased_type_chain_blocked():
    # P2 is blocked, so per plan this is a plain blocked-probe with another
    # chain: alias `type`, then walk to __subclasses__.
    code = "t = type\nresult['x'] = t(())"
    out = run_sandboxed(code, {})
    _assert_blocked(out)
    assert "type" in out["error"]


def test_p3b_dunder_subclasses_on_named_builtin_type_blocked():
    # The class objects themselves are nameable (dict is a builtin), but the
    # dunder attribute on them is still a compile error.
    out = run_sandboxed("result['x'] = dict.__subclasses__", {})
    _assert_blocked(out)
    assert "invalid attribute name" in out["error"]


def test_p4_eval_call_blocked():
    out = run_sandboxed("result['x'] = eval('1 + 1')", {})
    _assert_blocked(out)
    assert "Eval calls are not allowed" in out["error"]


def test_p4b_exec_call_blocked():
    out = run_sandboxed("exec('x = 1')", {})
    _assert_blocked(out)
    assert "Exec calls are not allowed" in out["error"]


def test_p4c_import_name_blocked():
    out = run_sandboxed("result['x'] = __import__('os').getpid()", {})
    _assert_blocked(out)
    assert "invalid variable name" in out["error"]


def test_p4d_eval_exec_names_not_in_namespace():
    # Name binding (no call) compiles for eval/exec but must still fail at
    # runtime: they are not provided by safe_builtins.
    out = run_sandboxed("result['a'] = eval\nresult['b'] = exec", {})
    _assert_blocked(out)


def test_p5_setattr_alias_plain_attr_inert():
    # safe_builtins['setattr'] is guarded_setattr: it wraps the target in
    # full_write_guard's Wrapper, whose __setattr__ requires the target to
    # implement __guarded_setattr__. Nothing reachable does, so even a plain
    # (non-dunder) attribute write on an exception instance fails.
    code = "s = setattr\ne = ValueError()\ns(e, 'note', 1)\nresult['ok'] = True"
    out = run_sandboxed(code, {})
    _assert_blocked(out)
    assert "attribute-less object" in out["error"]


def test_p5b_setattr_alias_dunder_string_inert():
    code = "s = setattr\ne = ValueError()\ns(e, '__class__', int)\nresult['ok'] = True"
    out = run_sandboxed(code, {})
    _assert_blocked(out)
    assert "attribute-less object" in out["error"]


def test_p5c_delattr_alias_inert():
    code = "d = delattr\ne = ValueError()\nd(e, 'args')"
    out = run_sandboxed(code, {})
    _assert_blocked(out)
    assert "attribute-less object" in out["error"]


def test_p6_class_definitions_unusable():
    # The RP 8.5 transformer rewrites class statements into a reference to
    # __metaclass__, which the runner namespace does not provide — so user
    # code cannot create any class objects at all. Not an escape, but it
    # removes the "mutate my own class" surface (and is a functional bug:
    # legitimate user code using classes always errors).
    out = run_sandboxed("class A: pass\nresult['ok'] = True", {})
    _assert_blocked(out)
    assert "__metaclass__" in out["error"]


def test_p7_type_subscript_dunder_string_blocked():
    # default_guarded_getitem is unrestricted (plain ob[index]), but no
    # reachable object implements a meaningful dunder-string subscript, so
    # the trick dies in CPython itself.
    out = run_sandboxed("result['x'] = int['__mro__']", {})
    _assert_blocked(out)


def test_p8_function_object_introspection_blocked():
    # Reaching runner module globals via get_secret.__globals__ is the most
    # promising object-graph path; the dunder attribute is a compile error.
    out = run_sandboxed("result['x'] = get_secret.__globals__", {})
    _assert_blocked(out)
    assert "invalid attribute name" in out["error"]
