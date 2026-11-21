"""Runs the node-based frontend template tests under pytest.

The templates are plain ES modules, so ``node --test`` can render them with
hostile data and check nothing executable comes out. Skipped when node is
not installed.
"""
import pathlib
import shutil
import subprocess

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_profile_templates_escape_user_controlled_content():
    result = subprocess.run(
        ["node", "--test", str(ROOT / "tests/js/profile_escaping.test.mjs")],
        capture_output=True, text=True, cwd=ROOT, timeout=120,
    )
    assert result.returncode == 0, result.stdout[-3000:] + result.stderr[-1000:]
