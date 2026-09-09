from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from wallpaper_capital.api import ApiError
from wallpaper_capital.wikidata import (
    EXTRA_STATES,
    file_title_from_url,
    parse_capitals,
    parse_point,
    read_cache,
)


def row(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "country": "France",
        "country_code": "fra",
        "capital": "Paris",
        "country_en": "France",
        "capital_en": "Paris",
        "commons_category": "Paris",
        "latitude": 48.8566,
        "longitude": 2.3522,
    }
    return {**base, **overrides}


class TestParsePoint:
    def test_longitude_comes_first_in_wikidata(self) -> None:
        assert parse_point("Point(2.3522 48.8566)") == (48.8566, 2.3522)

    def test_negative_coordinates(self) -> None:
        assert parse_point("Point(-77.0364 38.8951)") == (38.8951, -77.0364)

    @pytest.mark.parametrize("literal", ["", "Point(1)", "48.85, 2.35", "not a point"])
    def test_unparsable_input_yields_no_coordinates(self, literal: str) -> None:
        assert parse_point(literal) == (None, None)


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://commons.wikimedia.org/wiki/Special:FilePath/A%20view.jpg", "File:A view.jpg"),
        ("https://example.org/Special:FilePath/Tour_Eiffel.jpg", "File:Tour Eiffel.jpg"),
        ("", ""),
    ],
)
def test_file_title_from_url(url: str, expected: str) -> None:
    assert file_title_from_url(url) == expected


class TestParseCapitals:
    def test_country_codes_are_upper_cased(self) -> None:
        (capital,) = [item for item in parse_capitals([row()]) if item.country_code == "FRA"]
        assert capital.capital == "Paris"

    def test_denmark_is_added_even_when_absent(self) -> None:
        codes = {item.country_code for item in parse_capitals([])}
        assert codes == {extra.country_code for extra in EXTRA_STATES}

    def test_wikidata_rows_win_over_the_hardcoded_fallback(self) -> None:
        supplied = row(country="Danemark", country_code="DNK", capital="Copenhague")
        (denmark,) = [
            item for item in parse_capitals([supplied]) if item.country_code == "DNK"
        ]
        assert denmark.country_en == "France"  # i.e. the supplied row, not EXTRA_STATES

    @pytest.mark.parametrize(
        "bad",
        [
            {"country_code": "", "country": "France", "capital": "Paris"},
            {"country_code": "FRA", "country": "", "capital": "Paris"},
            {"country_code": "FRA", "country": "France", "capital": ""},
            "not a dict",
        ],
    )
    def test_incomplete_rows_are_dropped(self, bad: Any) -> None:
        assert not [item for item in parse_capitals([bad]) if item.country_code == "FRA"]

    def test_unlabelled_entities_are_dropped(self) -> None:
        """Wikidata returns the raw Qxx identifier when a label is missing."""
        rows = [row(capital="Q1490"), row(country="Q17", country_code="JPN")]
        assert not [item for item in parse_capitals(rows) if item.country_code in {"FRA", "JPN"}]

    def test_countries_may_hold_several_capitals(self) -> None:
        rows = [
            row(country="Afrique du Sud", country_code="ZAF", capital="Pretoria"),
            row(country="Afrique du Sud", country_code="ZAF", capital="Le Cap"),
        ]
        south_africa = [item for item in parse_capitals(rows) if item.country_code == "ZAF"]
        assert {item.capital for item in south_africa} == {"Pretoria", "Le Cap"}

    def test_result_is_sorted_by_capital(self) -> None:
        rows = [row(capital="Zagreb", country_code="HRV"), row(capital="Alger", country_code="DZA")]
        capitals = [item.capital for item in parse_capitals(rows)]
        assert capitals == sorted(capitals, key=str.casefold)


class TestReadCache:
    def test_reads_a_list(self, tmp_path: Path) -> None:
        path = tmp_path / "cache.json"
        path.write_text(json.dumps([row()]), encoding="utf-8")
        assert read_cache(path)[0]["capital"] == "Paris"

    def test_rejects_malformed_json(self, tmp_path: Path) -> None:
        path = tmp_path / "cache.json"
        path.write_text("{ broken", encoding="utf-8")
        with pytest.raises(ApiError):
            read_cache(path)

    def test_rejects_a_non_list_payload(self, tmp_path: Path) -> None:
        path = tmp_path / "cache.json"
        path.write_text('{"capital": "Paris"}', encoding="utf-8")
        with pytest.raises(ApiError):
            read_cache(path)
