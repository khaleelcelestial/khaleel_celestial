"""
Deterministic tests for skills/quality_skills.py's _check_bracket_balance -
a lightweight JS/TS structural check with no real parser behind it, so it
has repeatedly needed narrow, confirmed-false-positive-driven fixes this
session (English contractions, and now regex literals). No LLM calls.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from skills.quality_skills import _check_bracket_balance


def test_real_division_is_not_mistaken_for_regex():
    assert _check_bracket_balance("const x = a / b; const y = (c / d) * 2;") is None


def test_division_immediately_after_closing_paren():
    assert _check_bracket_balance("function f(a, b) { return (a + b) / 2; }") is None


def test_regex_literal_with_brackets_and_quotes_inside():
    """The exact real-world case that caused a genuine non-convergence bug:
    a regex containing '"' and '[...]'/'(...)' was being read as real
    string/bracket tokens with no regex awareness at all."""
    code = 'const re = /filename="?([^"]+)"?/i; console.log(re);'
    assert _check_bracket_balance(code) is None


def test_regex_literal_after_return_keyword():
    assert _check_bracket_balance('function f(s) { return /^[a-z]+$/.test(s); }') is None


def test_regex_literal_after_assignment_and_comparison_operators():
    assert _check_bracket_balance('if (x = /[a-z]/.test(y)) { z(); }') is None
    assert _check_bracket_balance('const ok = x === /[a-z]/.test(y);') is None


def test_line_comment_containing_a_slash_is_not_a_regex():
    code = "// this / is a comment with a / in it\nconst x = 1;"
    assert _check_bracket_balance(code) is None


def test_genuine_unbalanced_paren_still_caught():
    result = _check_bracket_balance("function f() { return (a + b; }")
    assert result is not None
    assert "Unbalanced" in result


def test_genuine_unclosed_brace_still_caught():
    result = _check_bracket_balance("function f() { const x = 1;")
    assert result == "Unclosed '{'"


def test_regex_literal_does_not_mask_a_real_bug_afterward():
    """A valid regex earlier in the file must not swallow a genuine
    bracket error that comes later."""
    result = _check_bracket_balance("const re = /a[b]c/; function f() { return (x; }")
    assert result is not None
    assert "Unbalanced" in result


def test_english_contraction_still_not_mistaken_for_string_open():
    """Pre-existing fix (not regex-related) - kept as a regression guard
    since both fixes live in the same character-by-character scanner."""
    assert _check_bracket_balance("const msg = <p>Don't worry, it's fine (really)</p>;") is None
