"""Unit tests for services/profile_validation.py."""
import pytest

from services.profile_validation import (
    ProfileValidationError,
    clean_bio,
    clean_class_level,
    clean_plain_text,
    clean_study_schedule,
    clean_text_list,
    strip_markup_chars,
)


class TestCleanPlainText:
    def test_trims_and_collapses_whitespace(self):
        assert clean_plain_text("  Ada   Lovelace \n", "Name", max_len=50) == "Ada Lovelace"

    @pytest.mark.parametrize("bad", ["<b>x</b>", 'a"><img src=x>', "1 > 0", "a<b"])
    def test_rejects_angle_brackets(self, bad):
        with pytest.raises(ProfileValidationError):
            clean_plain_text(bad, "Name", max_len=50)

    @pytest.mark.parametrize("bad", [123, None, ["a"], {"a": 1}])
    def test_rejects_non_strings(self, bad):
        with pytest.raises(ProfileValidationError):
            clean_plain_text(bad, "Name", max_len=50)

    def test_length_bounds(self):
        with pytest.raises(ProfileValidationError):
            clean_plain_text("ab", "Name", max_len=50, min_len=3)
        with pytest.raises(ProfileValidationError):
            clean_plain_text("x" * 51, "Name", max_len=50)

    def test_empty_handling(self):
        with pytest.raises(ProfileValidationError):
            clean_plain_text("   ", "Name", max_len=50)
        assert clean_plain_text("   ", "Name", max_len=50, allow_empty=True) == ""

    def test_control_characters_removed(self):
        assert clean_plain_text("Ad\x00a\x07", "Name", max_len=50) == "Ada"

    def test_keeps_ordinary_punctuation(self):
        assert clean_plain_text("C/C++ & O'Brien (II)", "Skill", max_len=50) == "C/C++ & O'Brien (II)"


class TestCleanBio:
    def test_allows_plain_symbols(self):
        assert clean_bio("I <3 maths and 2 > 1") == "I <3 maths and 2 > 1"

    @pytest.mark.parametrize("bad", ["<script>alert(1)</script>", "hi <img src=x>", "< /b>", "<!-- x"])
    def test_rejects_tag_like(self, bad):
        with pytest.raises(ProfileValidationError):
            clean_bio(bad)

    def test_max_len_and_type(self):
        with pytest.raises(ProfileValidationError):
            clean_bio("x" * 501)
        with pytest.raises(ProfileValidationError):
            clean_bio(5)
        assert clean_bio("") == ""

    def test_keeps_newlines(self):
        assert clean_bio("a\r\nb") == "a\nb"


class TestCleanTextList:
    def test_dedupes_case_insensitively_keeping_first(self):
        assert clean_text_list(["Python", "python", " Go "], "Skills", max_items=5, max_item_len=20) == ["Python", "Go"]

    @pytest.mark.parametrize("bad", [[{"a": 1}], [["x"]], [1], "abc", None])
    def test_rejects_non_string_items_and_non_lists(self, bad):
        with pytest.raises(ProfileValidationError):
            clean_text_list(bad, "Skills", max_items=5, max_item_len=20)

    def test_item_and_count_limits(self):
        with pytest.raises(ProfileValidationError):
            clean_text_list(["x" * 21], "Skills", max_items=5, max_item_len=20)
        with pytest.raises(ProfileValidationError):
            clean_text_list([str(i) for i in range(6)], "Skills", max_items=5, max_item_len=20)


class TestClassLevel:
    def test_canonicalises_case(self):
        assert clean_class_level("200 level") == "200 Level"
        assert clean_class_level("junior") == "Junior"

    def test_rejects_unknown(self):
        with pytest.raises(ProfileValidationError):
            clean_class_level("Year 3")


class TestStudySchedule:
    def test_normalises_to_onboarding_format(self):
        assert clean_study_schedule({"monday": ["Morning", "morning", "evening"], "FRIDAY": []}) == {
            "Monday": ["morning", "evening"]
        }

    @pytest.mark.parametrize("bad", [
        {"funday": ["morning"]},
        {"monday": ["midnight"]},
        {"monday": "morning"},
        {"monday": [1]},
        ["monday"],
    ])
    def test_rejects_bad_shapes(self, bad):
        with pytest.raises(ProfileValidationError):
            clean_study_schedule(bad)


def test_strip_markup_chars():
    assert strip_markup_chars("a<b>c") == "abc"
