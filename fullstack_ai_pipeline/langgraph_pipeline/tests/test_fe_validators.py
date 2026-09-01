"""
Deterministic tests for skills/fe_validators.py's NavigationConsistencyValidator
- specifically the two false-positive bugs found live this session: a
data-driven nav array (`to={item.to}`) and a template-literal path stored
in a variable then passed by name to navigate()/<Link to={...}>. Both are
common, idiomatic React patterns the original literal-string-only regexes
could not see at all, and both caused real, working navigation code to be
flagged as broken, sending the repair loop into a non-convergent retry
thrash against a bug that didn't exist in the app.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from skills.fe_validators import NavigationConsistencyValidator


def test_literal_link_is_recognized_as_before():
    files = {
        "src/App.jsx": "<Route path=\"/reports\" element={<Reports />} />",
        "src/Sidebar.jsx": "<Link to=\"/reports\">Reports</Link>",
    }
    assert NavigationConsistencyValidator().validate(files) == []


def test_route_with_zero_evidence_is_still_flagged():
    files = {"src/App.jsx": "<Route path=\"/reports\" element={<Reports />} />"}
    problems = NavigationConsistencyValidator().validate(files)
    assert any("/reports" in p for p in problems)


def test_data_driven_navlink_array_is_not_a_false_positive():
    """The real bug: `to={item.to}` from a navItems array is invisible to
    a literal-string-only regex."""
    files = {
        "src/App.jsx": '<Route path="/reports" element={<Reports />} />',
        "src/Sidebar.jsx": """
            const navItems = [{ to: '/reports', label: 'Reports' }];
            export default function Sidebar() {
              return navItems.map(item => <NavLink to={item.to}>{item.label}</NavLink>);
            }
        """,
    }
    assert NavigationConsistencyValidator().validate(files) == []


def test_navitem_object_key_ignored_without_a_dynamic_navlink_tag():
    """An unrelated `to: '/x'` object field (e.g. an email/message payload)
    must not be misread as navigation evidence just because it exists
    somewhere in the file."""
    files = {
        "src/App.jsx": '<Route path="/reports" element={<Reports />} />',
        "src/Mailer.jsx": "const message = { to: '/reports', subject: 'hi' };",
    }
    problems = NavigationConsistencyValidator().validate(files)
    assert any("/reports" in p for p in problems)


def test_template_literal_variable_passed_to_navigate_is_not_a_false_positive():
    """The second real bug: a path built into a template-literal variable,
    then passed BY NAME to navigate() - a bare identifier, not a literal
    string, so the original regex could never match it."""
    files = {
        "src/App.jsx": '<Route path="/users/:id" element={<UserDetail />} />',
        "src/Users.jsx": """
            function Row({ user, navigate }) {
              const userDetailPath = `/users/${user.id}`;
              return <tr onClick={() => navigate(userDetailPath)}>{user.name}</tr>;
            }
        """,
    }
    assert NavigationConsistencyValidator().validate(files) == []


def test_template_literal_variable_passed_to_link_to_is_not_a_false_positive():
    files = {
        "src/App.jsx": '<Route path="/visitors/:id" element={<VisitorDetail />} />',
        "src/Visitors.jsx": """
            function Row({ visitor }) {
              const visitorDetailPath = `/visitors/${visitor.id}`;
              return <Link to={visitorDetailPath}>{visitor.name}</Link>;
            }
        """,
    }
    assert NavigationConsistencyValidator().validate(files) == []


def test_template_literal_variable_unused_in_navigation_is_not_evidence():
    """A template-literal variable that's declared but only used for
    something else (e.g. a fetch URL) must not count as nav evidence."""
    files = {
        "src/App.jsx": '<Route path="/users/:id" element={<UserDetail />} />',
        "src/api.js": """
            function loadUser(id) {
              const userDetailPath = `/users/${id}`;
              return fetch(userDetailPath);
            }
        """,
    }
    problems = NavigationConsistencyValidator().validate(files)
    assert any("/users/{param}" in p for p in problems)
