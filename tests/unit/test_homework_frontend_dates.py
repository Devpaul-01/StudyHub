"""
Regression tests for audit finding F-10 (due-date timezone handling in the
homework UI). Runs the real helpers in Node under a non-UTC timezone.
Skipped when Node is not installed.
"""
import json
import os
import shutil
import subprocess

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
HARNESS = os.path.join(ROOT, "tests", "unit", "js", "homework_dates_harness.mjs")

node = shutil.which("node")
pytestmark = pytest.mark.skipif(node is None, reason="node not installed")


def _run(tz):
    env = {**os.environ, "TZ": tz}
    proc = subprocess.run(
        [node, HARNESS, ROOT], capture_output=True, text=True, timeout=30, cwd=ROOT, env=env
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout.strip().splitlines()[-1])


def test_local_input_becomes_utc_instant_in_plus_two_zone():
    # 10:00 wall-clock in UTC+2 (CEST, June) is 08:00Z
    assert _run("Europe/Paris")["toUtc"] == "2030-06-01T08:00:00.000Z"


def test_local_input_becomes_utc_instant_in_negative_zone():
    # 10:00 in UTC-4 (EDT, June) is 14:00Z
    assert _run("America/New_York")["toUtc"] == "2030-06-01T14:00:00.000Z"


def test_empty_and_invalid_inputs():
    out = _run("Europe/Paris")
    assert out["toUtcEmpty"] == ""
    assert out["toUtcBad"] == ""
    assert out["fromBad"] == ""


def test_server_naive_utc_prefill_is_shifted_into_local_time():
    # Server sends naive UTC 08:00 -> must show 10:00 locally in Paris (June).
    out = _run("Europe/Paris")
    assert out["fromNaive"] == "2030-06-01T10:00"
    assert out["fromZ"] == "2030-06-01T10:00"
    assert out["fromOffset"] == "2030-06-01T10:00"


def test_round_trip_is_stable():
    # create (local -> UTC) then edit prefill (UTC -> local) returns the same value
    out = _run("America/New_York")
    assert out["fromNaive"] == "2030-06-01T04:00"
    assert out["toUtc"].startswith("2030-06-01T14:00")
