"""Runs the node-based frontend tests (tests/js/*.test.mjs) under pytest.

The profile frontend modules are plain ES modules, so ``node --test`` can
import them with a few stubbed browser globals. Skipped when node is not
installed.
"""
import pathlib
import shutil
import subprocess

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
JS_TESTS = sorted((ROOT / "tests/js").glob("*.test.mjs"))


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
@pytest.mark.parametrize("test_file", JS_TESTS, ids=[p.name for p in JS_TESTS])
def test_frontend_module(test_file):
    result = subprocess.run(
        ["node", "--test", str(test_file)],
        capture_output=True, text=True, cwd=ROOT, timeout=120,
    )
    assert result.returncode == 0, result.stdout[-3000:] + result.stderr[-1000:]
