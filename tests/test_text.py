from __future__ import annotations

import pytest

from wallpaper_capital.text import (
    capitalize_first,
    has_term,
    mentions_place,
    slugify,
    strip_html,
)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("Paris", "paris"),
        ("Le Caire", "le_caire"),
        ("Abou Dabi", "abou_dabi"),
        ("São Tomé", "sao_tome"),
        ("N'Djamena", "n_djamena"),
        ("  ---  ", "image"),
        ("", "image"),
    ],
)
def test_slugify(value: str, expected: str) -> None:
    assert slugify(value) == expected


def test_slugify_is_stable_across_accents() -> None:
    """Filenames must not change when Wikidata adds or drops a diacritic."""
    assert slugify("Asuncion") == slugify("Asunción")


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("<a href='#'>Jane Doe</a>", "Jane Doe"),
        ("CC&nbsp;BY-SA", "CC BY-SA"),
        ("  spread   out  ", "spread out"),
        (None, ""),
        ("", ""),
    ],
)
def test_strip_html(value: str | None, expected: str) -> None:
    assert strip_html(value) == expected


def test_capitalize_first_leaves_the_rest_alone() -> None:
    assert capitalize_first("république démocratique du Congo") == (
        "République démocratique du Congo"
    )
    assert capitalize_first("") == ""


def test_has_term_matches_whole_words_only() -> None:
    assert has_term("a city plan", "plan")
    assert not has_term("the planetarium at night", "plan")


class TestMentionsPlace:
    def test_plain_mention(self) -> None:
        assert mentions_place("wellington harbour at dusk", "wellington")

    @pytest.mark.parametrize("prefix", ["mount", "mt", "lake", "port", "cape"])
    def test_qualified_mention_is_another_place(self, prefix: str) -> None:
        assert not mentions_place(f"{prefix} wellington in the snow", "wellington")

    def test_one_good_mention_is_enough(self) -> None:
        assert mentions_place("mount wellington seen from wellington", "wellington")

    def test_punctuation_before_the_name_does_not_hide_it(self) -> None:
        assert mentions_place("skyline (wellington) at night", "wellington")
