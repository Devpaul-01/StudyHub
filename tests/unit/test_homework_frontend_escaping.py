"""
Regression tests for audit finding F-01 (stored XSS in the homework UI).

The homework frontend builds HTML strings and assigns them via innerHTML. These
tests execute the real template modules in Node with hostile payloads and assert
that no executable markup (raw tags, attribute breakouts, javascript: URLs)
survives in the rendered output.

Skipped automatically when Node is not installed.
"""
import json
import os
import re
import shutil
import subprocess

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
HARNESS = os.path.join(ROOT, "tests", "unit", "js", "homework_escape_harness.mjs")

node = shutil.which("node")
pytestmark = pytest.mark.skipif(node is None, reason="node not installed")


@pytest.fixture(scope="module")
def rendered():
    proc = subprocess.run(
        [node, HARNESS, ROOT], capture_output=True, text=True, timeout=60, cwd=ROOT
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout.strip().splitlines()[-1])


FORBIDDEN = [
    r"<img src=x",          # raw injected tag
    r"<script",             # raw script tag
    r"<svg onload",         # raw injected svg
    r'"><svg',             # attribute breakout from the injected name
    r"(?:src|href)=\"javascript:",  # javascript: URL reaching an attribute
    r"data-resource-url=\"javascript:",
]


@pytest.mark.parametrize(
    "key",
    ["myCard", "connCard", "helpRequests", "helping", "resourceItem",
     "subHelper", "subRequester", "activity"],
)
def test_hostile_payloads_are_neutralised(rendered, key):
    html = rendered[key]
    assert html, f"{key} rendered nothing - the harness payload shape is wrong"
    for pattern in FORBIDDEN:
        assert not re.search(pattern, html), f"{key}: unescaped content matched {pattern!r}"


def test_user_text_is_escaped_not_dropped(rendered):
    # The text must still be visible to the user, just inert.
    assert "&lt;img src=x onerror=alert(1)&gt;" in rendered["connCard"]
    assert "&lt;img src=x onerror=alert(1)&gt;" in rendered["subHelper"]
    assert "&lt;img src=x onerror=alert(1)&gt;" in rendered["activity"]


def test_unsafe_urls_are_blanked(rendered):
    assert 'data-resource-url=""' in rendered["resourceItem"]
