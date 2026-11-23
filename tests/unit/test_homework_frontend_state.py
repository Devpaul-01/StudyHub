"""
Regression tests for audit findings F-25 (stale-response sequencing) and F-29
(attachments leaking between the create and solution flows). Runs the real
modules in Node; skipped when Node is not installed.
"""
import json
import os
import shutil
import subprocess

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
HARNESS = os.path.join(ROOT, "tests", "unit", "js", "homework_state_harness.mjs")

node = shutil.which("node")
pytestmark = pytest.mark.skipif(node is None, reason="node not installed")


@pytest.fixture(scope="module")
def out():
    proc = subprocess.run([node, HARNESS, ROOT], capture_output=True, text=True, timeout=30, cwd=ROOT)
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout.strip().splitlines()[-1])


def test_sequencer_marks_older_requests_stale(out):
    assert out["seqOut"] == {
        "firstStale": True,
        "secondCurrent": True,
        "currentValue": True,
        "currentDoesNotAdvance": True,
    }


def test_resource_flows_are_isolated(out):
    assert out["afterAdd"] == {"create": ["draft.pdf"], "solution": ["sol.png"]}
    assert out["afterClearSolution"] == {"create": 1, "solution": 0}
    assert out["afterRemove"] == 0


def test_unknown_flow_is_rejected(out):
    assert out["unknownFlowThrows"] is True


def test_reset_clears_both_flows(out):
    assert out["afterReset"] == [0, 0]


def test_single_flight_blocks_duplicate_until_released(out):
    # first claim wins, concurrent duplicate is refused, claim works again after release
    assert out["flight"] == [True, False, True]
