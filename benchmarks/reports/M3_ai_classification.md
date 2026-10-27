# M3 — AI Provider Error Classification Boundary Correctness

**Status: executed successfully — found one genuine correctness gap.**
**Script:** `benchmarks/m3_ai_classification.py`
**Result file:** `benchmarks/results/m3_ai_classification/m3_ai_classification_20261014T172041Z_bd05e5f3.json`
**Run ID:** `m3_ai_classification_20261014T172041Z_bd05e5f3`
**Commit:** `a2c2996f632734f4186a289b0876988ff4ddc871`

## Objective

Does `services.ai_provider_service.classify_provider_error()` correctly sort every representative failure shape into the right one of its four categories (`KEY_FAULT`, `PROVIDER_TRANSIENT`, `BAD_MODEL`, `NON_RETRYABLE`), including boundary cases not already covered by the project's own existing unit test file?

## Implementation under test

`services/ai_provider_service.py::classify_provider_error()` and `_is_bad_model_signal()`, unmodified — a pure function, no I/O, no Flask/DB dependency.

## Environment

Python 3.13.16, direct import of the module (no running server needed — pure function test). `AI_PROVIDER_REDIS_STATE_ENABLED=false` set to avoid any incidental Redis interaction during import (confirmed in prior investigation that `classify_provider_error` itself touches no Redis state regardless).

## Workload

37 constructed cases spanning: all 8 documented status-code memberships (`KEY_FAULT`: 401/402/403/429; `PROVIDER_TRANSIENT`: 500/502/503/504), the `network_error_code` fallback path (4 codes), the `raw_message` regex fallback path (6 matching messages + 1 deliberately non-matching message), 6 structured `BAD_MODEL` signal shapes, 3 "near-miss" boundary cases designed to test whether the classifier over- or under-fires, 3 malformed/empty-body edge cases, 5 unmatched status codes, and 1 defensive non-`ProviderCallError` input.

## Results

**36 of 37 cases passed.** One failed:

| case_id | Input | Expected | Actual | 
|---|---|---|---|
| `bad_model_signal_2` | `status=400`, `parsed_body={"error": {"code": "model_not_found"}}` | `BAD_MODEL` | **`NON_RETRYABLE`** |

### Root cause (traced to source, independently reproduced outside the harness)

`_is_bad_model_signal()` requires the combined `error.type`/`error.code`/`error.message` text to match **both** a word containing `model` **and** one of the literal phrases `not found|does not exist|invalid|decommission|unknown|unsupported` — joined by a space in the regex pattern (`r"not found|..."`). An `error.code` value of `"model_not_found"` (underscore-joined — a plausible real shape for a provider's structured error *code* field specifically, as distinct from a prose *message* field) contains `model` but does **not** contain the literal substring `"not found"` with a space, because the underscore breaks the match. Verified directly and independently of the benchmark harness:

```python
>>> from services.ai_provider_service import _is_bad_model_signal
>>> _is_bad_model_signal({"error": {"code": "model_not_found"}})
False
```

**Consequence if this shape occurs with a real provider:** the error is classified `NON_RETRYABLE` instead of `BAD_MODEL`, which means `classify_provider_error`'s caller aborts the entire fallback chain for that request instead of evicting just the bad model from the shared cache and advancing to the next queue entry (a different model or provider) — the specific behavior `BAD_MODEL` classification exists to enable. This is a real, if narrow, behavioral difference, not a cosmetic one.

**This gap is newly found by this audit, not previously known:** the existing `tests/unit/services/ai_provider_service/test_error_classification.py` tests `"model not found"` (prose, space-separated, inside an `error.message` field) but does not test an underscore-joined `error.code` value — confirmed by reading that file's source directly (see Gap Analysis below).

## Gap Analysis

The harness also checked, by direct textual inspection of the existing pytest file's source (not a coverage-tool measurement — a simpler, approximate method, stated as such), whether two other constructed boundary cases were already covered:

| Scenario | Already covered by existing pytest file? |
|---|---|
| `model_not_found` (the failing case above) | No — root cause of the one real failure |
| 400 body mentioning "model" in an unrelated field-validation context (`"field 'model_temperature' must be between 0 and 2"`) | **No** — genuinely new coverage; this case passed (correctly classified `NON_RETRYABLE`), so no bug here, but it wasn't previously tested |
| 400 body with a malformed (non-dict) `parsed_body` | Initially flagged as uncovered by the harness's substring heuristic, but a closer read shows it **is** covered — `test_bad_model_signal_not_a_dict` (line 89 of the existing file) tests this exact shape, just phrased differently than the heuristic's search string expected. **This is a limitation of the harness's own gap-analysis method, not a real gap** — corrected here rather than left as a false claim. |

This self-correction is worth stating plainly: the gap-analysis method is a simple substring heuristic, and it produced one false positive, caught by reading the actual test file rather than trusting the heuristic's own output. The one genuine failure (`model_not_found`) was independently confirmed by direct function invocation, not by the heuristic.

## Baseline comparison

Not applicable in the traditional sense — M3 is a correctness-against-specification check, not a performance comparison. The "comparison" here is against the function's own documented behavior (4-category classification per `ARCHITECTURE.md` §11.2) and against the existing test suite's coverage.

## Interpretation

**What this demonstrates:** 36 of 37 constructed boundary cases classify correctly, including several cases not previously tested. One genuine, narrow, reproducible correctness gap was found in the `BAD_MODEL` detection path for underscore-joined error codes.

**What this does not demonstrate:** that `classify_provider_error` is correct against every real provider's actual error response shape — this proves the classifier's logic against constructed inputs built from the documented specification, not live-provider fidelity (the mission's own caution against presenting mock-based correctness as proof of real-world behavior applies directly here).

## Limitations

- The gap-analysis method (substring search against test file source) is approximate and produced one false positive, documented and corrected above rather than silently fixed.
- This experiment does not fix the discovered gap — fixing application code was out of scope for a measurement task per the mission's constraints ("do not change unrelated application functionality"). The finding is reported for the user to act on at their discretion.

## Resume suitability

**The 36/37 pass rate and the methodology: resume-ready** as a correctness-testing claim ("built an exhaustive boundary-case test suite for a 4-category error classifier, independently discovering a classification gap in production code").

**The specific discovered bug: useful for engineering discussion, not a standalone resume bullet.** It's excellent interview material — it demonstrates the kind of edge-case thinking that finds real bugs — but it describes a gap in the *project's* code, not an achievement to claim credit for as a shipped fix (it wasn't fixed here). Frame it in an interview as "while auditing my own code for a resume claim, I found and could reproduce a classification gap in how structured error codes are matched" — honest, and arguably more impressive than a clean pass rate alone would have been.
