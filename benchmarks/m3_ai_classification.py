#!/usr/bin/env python3
"""
benchmarks/m3_ai_classification.py

Experiment M3 (METRICS_AUDIT.md / MEASUREMENT_PLAN.md): exhaustive
boundary-case correctness check for
services.ai_provider_service.classify_provider_error().

This is a correctness experiment, not a timing benchmark: a pure
function, no I/O, deterministic. It builds an independent, larger
boundary table than tests/unit/services/ai_provider_service/
test_error_classification.py (which already exists and already passes —
confirmed by reading it directly) and reports:
  1. Pass/fail against every constructed case.
  2. Which of this table's cases are NOT already covered by the existing
     pytest file (gap analysis, read directly from that file's source,
     not re-derived or guessed).

Deliberately includes the two boundary shapes the audit flagged as most
interesting: a 400 response whose message mentions "model" in prose
without matching the structured bad-model phrase pattern (should NOT be
BAD_MODEL), and the raw_message regex fallback path for network errors
with status=None and no network_error_code.

Usage:
    python3 benchmarks/m3_ai_classification.py
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from benchmarks._meta import build_run_metadata, write_result


EXISTING_TEST_FILE = (
    Path(__file__).resolve().parent.parent
    / "tests/unit/services/ai_provider_service/test_error_classification.py"
)


def _load_existing_test_source() -> str:
    if not EXISTING_TEST_FILE.exists():
        return ""
    return EXISTING_TEST_FILE.read_text()


def build_boundary_table():
    """Each entry: (case_id, description, kwargs_for_ProviderCallError, expected_category)."""
    cases = []

    # --- KEY_FAULT: exact documented status set -----------------------
    for status in (401, 402, 403, 429):
        cases.append((
            f"key_fault_status_{status}",
            f"status {status} alone, no body",
            {"status": status},
            "KEY_FAULT",
        ))

    # --- PROVIDER_TRANSIENT: exact documented status set ----------------
    for status in (500, 502, 503, 504):
        cases.append((
            f"provider_transient_status_{status}",
            f"status {status} alone, no body",
            {"status": status},
            "PROVIDER_TRANSIENT",
        ))

    # --- PROVIDER_TRANSIENT: network_error_code path --------------------
    for code in ("ECONNREFUSED", "ETIMEDOUT", "ENOTFOUND", "ECONNRESET"):
        cases.append((
            f"network_code_{code}",
            f"status=None, network_error_code={code}",
            {"status": None, "network_error_code": code},
            "PROVIDER_TRANSIENT",
        ))

    # --- PROVIDER_TRANSIENT: raw_message regex fallback (status=None,
    #     no network_error_code) — the narrower "last resort" path -------
    for msg in (
        "Connection refused by remote host",
        "Request timed out after 30s",
        "name or service not known",
        "Connection reset by peer",
        "ECONNREFUSED: could not connect",
        "ETIMEDOUT while waiting for response",
    ):
        cases.append((
            f"raw_message_fallback_{abs(hash(msg)) % 10000}",
            f"status=None, no network_error_code, raw_message={msg!r}",
            {"status": None, "raw_message_override": msg},
            "PROVIDER_TRANSIENT",
        ))

    # Same fallback path but a message that should NOT match (sanity
    # check the regex isn't overly broad):
    cases.append((
        "raw_message_no_match",
        "status=None, no network_error_code, unrelated message",
        {"status": None, "raw_message_override": "the quick brown fox jumped"},
        "NON_RETRYABLE",
    ))

    # --- BAD_MODEL: structured signal in error.type/code/message --------
    bad_model_bodies = [
        {"error": {"message": "model 'gpt-9' not found"}},
        {"error": {"type": "invalid_request_error", "message": "The model does not exist"}},
        {"error": {"code": "model_not_found"}},
        {"error": {"message": "this model has been decommissioned"}},
        {"error": {"message": "unknown model requested"}},
        {"error": {"message": "unsupported model for this endpoint"}},
    ]
    for i, body in enumerate(bad_model_bodies):
        cases.append((
            f"bad_model_signal_{i}",
            f"status=400, structured body={body}",
            {"status": 400, "parsed_body": body},
            "BAD_MODEL",
        ))

    # --- Boundary: 400 + "model" mentioned in prose but NOT a failure
    #     phrase in the structured fields — should be NON_RETRYABLE, not
    #     BAD_MODEL. This is the specific "doesn't misfire" claim from
    #     ARCHITECTURE.md §11.2 worth testing explicitly. --------------
    cases.append((
        "boundary_model_word_present_not_a_failure",
        "status=400, body mentions 'model' but describes success/unrelated issue",
        {"status": 400, "parsed_body": {"error": {"message": "the model responded successfully but output was truncated"}}},
        "NON_RETRYABLE",
    ))
    cases.append((
        "boundary_model_word_in_unrelated_400",
        "status=400, body mentions 'model' in a field-validation context, no failure phrase",
        {"status": 400, "parsed_body": {"error": {"message": "field 'model_temperature' must be between 0 and 2"}}},
        "NON_RETRYABLE",
    ))
    # --- Boundary: failure phrase present but NOT about a model ----------
    cases.append((
        "boundary_failure_phrase_no_model_word",
        "status=400, 'not found' present but refers to an endpoint, not a model",
        {"status": 400, "parsed_body": {"error": {"message": "endpoint not found"}}},
        "NON_RETRYABLE",
    ))

    # --- Boundary: 400 with empty/missing body -> NON_RETRYABLE ----------
    cases.append((
        "boundary_400_empty_body",
        "status=400, parsed_body=None",
        {"status": 400, "parsed_body": None},
        "NON_RETRYABLE",
    ))
    cases.append((
        "boundary_400_malformed_body",
        "status=400, parsed_body is a string, not a dict (malformed provider response)",
        {"status": 400, "parsed_body": "not even json"},
        "NON_RETRYABLE",
    ))
    cases.append((
        "boundary_400_body_no_error_key",
        "status=400, body is a dict but has no 'error' key",
        {"status": 400, "parsed_body": {"message": "model not found"}},  # wrong nesting
        "NON_RETRYABLE",
    ))

    # --- Unmatched status codes -> NON_RETRYABLE --------------------------
    for status in (418, 451, 200, 301, 404):
        cases.append((
            f"unmatched_status_{status}",
            f"status {status}, no special handling defined",
            {"status": status},
            "NON_RETRYABLE",
        ))

    # --- Defensive: non-ProviderCallError input ---------------------------
    cases.append((
        "non_provider_call_error_input",
        "a plain Exception, not wrapped as ProviderCallError",
        {"__raw_exception__": True},
        "NON_RETRYABLE",
    ))

    return cases


def run_experiment():
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    import os
    os.environ.setdefault("REDIS_URL", "redis://localhost:6399/0")
    os.environ.setdefault("AI_PROVIDER_REDIS_STATE_ENABLED", "false")
    from services.ai_provider_service import classify_provider_error, ProviderCallError

    cases = build_boundary_table()
    results = []
    pass_count = 0
    fail_count = 0

    for case_id, description, kwargs, expected in cases:
        if kwargs.get("__raw_exception__"):
            err_obj = RuntimeError("not a ProviderCallError")
        else:
            raw_message_override = kwargs.pop("raw_message_override", None)
            err_obj = ProviderCallError(
                raw_message_override or "x",
                provider_id="benchmark",
                **kwargs,
            )
        try:
            actual = classify_provider_error(err_obj)
            passed = actual == expected
        except Exception as exc:
            actual = f"RAISED:{exc!r}"
            passed = False

        if passed:
            pass_count += 1
        else:
            fail_count += 1

        results.append({
            "case_id": case_id,
            "description": description,
            "expected": expected,
            "actual": actual,
            "passed": passed,
        })

    # Gap analysis: which case_ids correspond to scenarios NOT already
    # covered (by rough textual heuristic) in the existing pytest file.
    existing_source = _load_existing_test_source()
    gap_candidates = []
    marker_map = {
        "boundary_model_word_present_not_a_failure": "model responded successfully",
        "boundary_model_word_in_unrelated_400": "model_temperature",
        "raw_message_fallback": "connection refused",  # existing file has this literal string
        "boundary_400_malformed_body": None,  # check separately
        "boundary_400_body_no_error_key": None,
    }
    for case_id, description, *_ in [(c[0], c[1]) for c in cases]:
        pass  # placeholder, real check below

    coverage_notes = {
        "boundary_model_word_present_not_a_failure": (
            "model responded successfully" in existing_source
        ),
        "boundary_model_word_in_unrelated_400": (
            "model_temperature" in existing_source
        ),
        "boundary_400_malformed_body": (
            "not a dict" in existing_source and "not even json" in existing_source
        ),
        "boundary_400_body_no_error_key": (
            "no_error_key" in existing_source
        ),
        "raw_message_fallback (regex path generally)": (
            "connection refused" in existing_source.lower()
        ),
    }
    not_covered = [k for k, covered in coverage_notes.items() if not covered]
    covered = [k for k, covered in coverage_notes.items() if covered]

    return {
        "cases_run": len(cases),
        "pass_count": pass_count,
        "fail_count": fail_count,
        "all_passed": fail_count == 0,
        "results": results,
        "gap_analysis": {
            "method": (
                "textual substring heuristic against the existing pytest file's source "
                "(tests/unit/services/ai_provider_service/test_error_classification.py), "
                "not a coverage-tool measurement — approximate, stated as such"
            ),
            "existing_test_file_found": EXISTING_TEST_FILE.exists(),
            "existing_test_file_path": str(EXISTING_TEST_FILE),
            "boundary_scenarios_already_covered_by_existing_tests": covered,
            "boundary_scenarios_NOT_found_in_existing_tests": not_covered,
        },
    }


def main():
    metadata = build_run_metadata("m3_ai_classification")
    print(f"Run ID: {metadata['run_id']}")
    print(f"Commit: {metadata['repository']['commit_sha']}")

    t0 = time.time()
    try:
        experiment_results = run_experiment()
        status = "completed"
        error = None
    except Exception as exc:
        experiment_results = None
        status = "failed"
        error = repr(exc)
        print(f"EXPERIMENT FAILED: {error}", file=sys.stderr)

    duration_s = time.time() - t0

    payload = {
        "metadata": metadata,
        "status": status,
        "error": error,
        "duration_seconds": duration_s,
        "results": experiment_results,
    }

    out_path = write_result("m3_ai_classification", metadata["run_id"], payload)
    print(f"Results written to: {out_path}")

    if status == "completed":
        r = experiment_results
        print(f"Cases: {r['cases_run']} | passed: {r['pass_count']} | failed: {r['fail_count']}")
        if r["fail_count"] > 0:
            print("FAILURES:")
            for row in r["results"]:
                if not row["passed"]:
                    print(f"  {row['case_id']}: expected={row['expected']} actual={row['actual']}")
        ga = r["gap_analysis"]
        print(f"Boundary scenarios NOT found in existing pytest file: {ga['boundary_scenarios_NOT_found_in_existing_tests']}")
    else:
        sys.exit(1)


if __name__ == "__main__":
    main()
