"""
tests/unit/services/test_homework_validation.py

Pure unit tests for services/homework_validation.py (audit F-01, F-09, F-10).
"""

import datetime

import pytest

from services.homework_validation import (
    HomeworkValidationError,
    clean_estimated_hours,
    clean_resources,
    parse_due_date,
    require_json_object,
    validate_assignment_create,
    validate_feedback,
    validate_solution_submit,
    validate_assignment_update,
)


def _future(days=3):
    return (datetime.datetime.utcnow() + datetime.timedelta(days=days)).isoformat()


class TestParseDueDate:
    def test_z_suffix_is_utc_naive(self):
        assert parse_due_date("2030-01-01T10:00:00Z") == datetime.datetime(2030, 1, 1, 10)

    def test_offset_converted_to_utc(self):
        assert parse_due_date("2030-01-01T10:00:00+02:00") == datetime.datetime(2030, 1, 1, 8)

    def test_bare_value_treated_as_utc(self):
        assert parse_due_date("2030-01-01T10:00:00") == datetime.datetime(2030, 1, 1, 10)

    @pytest.mark.parametrize("bad", [None, "", "   ", "tomorrow", 12345, ["x"]])
    def test_rejects_garbage(self, bad):
        with pytest.raises(HomeworkValidationError):
            parse_due_date(bad)


class TestEstimatedHours:
    def test_none_and_empty_clear(self):
        assert clean_estimated_hours(None) is None
        assert clean_estimated_hours("") is None

    def test_accepts_numeric_strings(self):
        assert clean_estimated_hours("2.5") == 2.5

    @pytest.mark.parametrize("bad", [0, -1, "abc", True, float("inf"), float("nan"), 10_000])
    def test_rejects_bad_values(self, bad):
        with pytest.raises(HomeworkValidationError):
            clean_estimated_hours(bad)


class TestCleanResources:
    def test_normalises_and_drops_unknown_keys(self):
        out = clean_resources(
            [{"url": " https://x.test/a.pdf ", "type": "PDF", "filename": "a.pdf", "evil": "<script>"}]
        )
        assert out == [{"url": "https://x.test/a.pdf", "type": "pdf", "filename": "a.pdf"}]

    @pytest.mark.parametrize(
        "url",
        [
            "javascript:alert(1)",
            "JaVaScRiPt:alert(1)",
            "data:text/html,<script>alert(1)</script>",
            "vbscript:x",
            "//evil.test/x",
            "/static/x.png",
            "https://",
            "https://x.test/a\nb",
            "https://x.test/" + "a" * 2100,
        ],
    )
    def test_rejects_unsafe_urls(self, url):
        with pytest.raises(HomeworkValidationError):
            clean_resources([{"url": url, "type": "image"}])

    def test_rejects_unknown_type(self):
        with pytest.raises(HomeworkValidationError):
            clean_resources([{"url": "https://x.test/a", "type": "exe"}])

    def test_too_many(self):
        item = {"url": "https://x.test/a", "type": "image"}
        with pytest.raises(HomeworkValidationError):
            clean_resources([item] * 11)

    def test_not_a_list_and_non_dict_items(self):
        with pytest.raises(HomeworkValidationError):
            clean_resources("nope")
        with pytest.raises(HomeworkValidationError):
            clean_resources(["nope"])

    def test_empty_values(self):
        assert clean_resources(None) == []
        assert clean_resources([]) == []


class TestAssignmentPayloads:
    def test_require_json_object(self):
        for bad in (None, [], "x", 3):
            with pytest.raises(HomeworkValidationError):
                require_json_object(bad)

    def test_create_happy_path(self):
        out = validate_assignment_create(
            {"title": "  Essay ", "due_date": _future(), "difficulty": "HARD", "estimated_hours": 2}
        )
        assert out["title"] == "Essay"
        assert out["difficulty"] == "hard"
        assert out["estimated_hours"] == 2.0
        assert out["is_shared_for_help"] is False
        assert out["resources"] == []

    @pytest.mark.parametrize(
        "patch",
        [
            {"title": "   "},
            {"title": None},
            {"title": "x" * 201},
            {"subject": "s" * 101},
            {"difficulty": "impossible"},
            {"estimated_hours": -3},
            {"share_for_help": "yes"},
            {"due_date": "2000-01-01T00:00:00Z"},
            {"description": 5},
        ],
    )
    def test_create_rejects(self, patch):
        body = {"title": "T", "due_date": _future(), **patch}
        with pytest.raises(HomeworkValidationError):
            validate_assignment_create(body)

    def test_update_only_returns_supplied_keys(self):
        assert validate_assignment_update({"title": "New"}) == {"title": "New"}

    def test_update_allows_past_due_date(self):
        out = validate_assignment_update({"due_date": "2000-01-01T00:00:00Z"})
        assert out["due_date"].year == 2000

    @pytest.mark.parametrize(
        "body",
        [{"status": "banana"}, {"title": ""}, {"difficulty": "x"}, {"estimated_hours": 0}, None, []],
    )
    def test_update_rejects(self, body):
        with pytest.raises(HomeworkValidationError):
            validate_assignment_update(body)


class TestSubmissionPayloads:
    def test_solution_happy_path(self):
        out = validate_solution_submit({"solution_text": "  answer ", "resources": []})
        assert out == {"solution_text": "answer", "resources": []}

    @pytest.mark.parametrize("body", [None, {}, {"solution_text": " "}, {"solution_text": 3}])
    def test_solution_rejects(self, body):
        with pytest.raises(HomeworkValidationError):
            validate_solution_submit(body)

    def test_feedback_defaults(self):
        out = validate_feedback({})
        assert out["mark_complete"] is True
        assert out["rating"] is None and out["reaction_type"] is None
        assert out["is_helpful"] is None
        assert out["feedback_text"] == "" and out["feedback_resources"] == []

    def test_feedback_accepts_numeric_string_rating_and_whole_floats(self):
        assert validate_feedback({"rating": "4"})["rating"] == 4
        assert validate_feedback({"rating": 5.0})["rating"] == 5

    @pytest.mark.parametrize(
        "body",
        [{"rating": 6}, {"rating": 0}, {"rating": 2.5}, {"rating": "x"}, {"rating": False},
         {"reaction_type": "nope"}, {"is_helpful": "yes"}, {"mark_complete": 1}],
    )
    def test_feedback_rejects(self, body):
        with pytest.raises(HomeworkValidationError):
            validate_feedback(body)
