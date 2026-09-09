from __future__ import annotations

import json
from pathlib import Path

import pytest

from wallpaper_capital.lockscreen import gallery, state
from wallpaper_capital.lockscreen.labels import Labels, build_index, labels_for
from wallpaper_capital.lockscreen.state import HISTORY_MAX


class TestLabelLines:
    def test_the_main_line_shouts_the_capital(self) -> None:
        primary, secondary = Labels(capital="Paris", country="France").lines()
        assert primary == "PARIS · France"
        assert secondary is None

    def test_english_names_appear_only_when_they_differ(self) -> None:
        labels = Labels(
            capital="Budapest", country="Hongrie", capital_en="Budapest", country_en="Hungary"
        )
        assert labels.lines() == ("BUDAPEST · Hongrie", "Hungary")

    def test_both_names_may_differ(self) -> None:
        labels = Labels(
            capital="Le Caire", country="Égypte", capital_en="Cairo", country_en="Egypt"
        )
        assert labels.lines() == ("LE CAIRE · Égypte", "Cairo · Egypt")

    def test_case_differences_do_not_count_as_a_difference(self) -> None:
        labels = Labels(capital="Paris", country="France", country_en="france")
        assert labels.lines()[1] is None


class TestBuildIndex:
    def write_cache(self, tmp_path: Path, entries: object) -> Path:
        path = tmp_path / "capitals.json"
        path.write_text(json.dumps(entries, ensure_ascii=False), encoding="utf-8")
        return path

    def test_keys_match_the_downloaded_filenames(self, tmp_path: Path) -> None:
        path = self.write_cache(
            tmp_path,
            [{"capital": "Le Caire", "country": "Égypte", "country_code": "EGY"}],
        )
        assert "le_caire_egy" in build_index(path)

    def test_country_labels_get_a_leading_capital(self, tmp_path: Path) -> None:
        path = self.write_cache(
            tmp_path,
            [
                {
                    "capital": "Kinshasa",
                    "country": "république démocratique du Congo",
                    "country_code": "COD",
                }
            ],
        )
        assert build_index(path)["kinshasa_cod"].country == "République démocratique du Congo"

    def test_denmark_is_always_present(self, tmp_path: Path) -> None:
        assert "copenhague_dnk" in build_index(self.write_cache(tmp_path, []))

    def test_a_missing_cache_is_not_fatal(self, tmp_path: Path) -> None:
        assert build_index(tmp_path / "absent.json") == {}

    def test_a_broken_cache_is_not_fatal(self, tmp_path: Path) -> None:
        path = tmp_path / "capitals.json"
        path.write_text("{ broken", encoding="utf-8")
        assert build_index(path) == {}


class TestLabelsFor:
    def test_known_stems_come_from_the_cache(self) -> None:
        index = {"paris_fra": Labels(capital="Paris", country="France", country_code="FRA")}
        assert labels_for("paris_fra", index).capital == "Paris"

    def test_unknown_stems_fall_back_to_the_filename(self) -> None:
        labels = labels_for("le_caire_egy", {})
        assert labels.capital == "Le Caire"
        assert labels.country_code == "EGY"

    def test_the_country_is_recovered_from_a_sibling_entry(self) -> None:
        pretoria = Labels(capital="Pretoria", country="Afrique du Sud", country_code="ZAF")
        index = {"pretoria_zaf": pretoria}
        assert labels_for("le_cap_zaf", index).country == "Afrique du Sud"


class TestGallery:
    @pytest.fixture
    def library(self, tmp_path: Path) -> list[Path]:
        for stem in ("paris_fra", "le_caire_egy", "pretoria_zaf", "le_cap_zaf"):
            (tmp_path / f"{stem}.jpg").write_bytes(b"")
        (tmp_path / "notes.txt").write_text("ignored", encoding="utf-8")
        return gallery.list_images(tmp_path)

    def test_only_jpegs_are_listed(self, library: list[Path]) -> None:
        assert [path.stem for path in library] == [
            "le_caire_egy",
            "le_cap_zaf",
            "paris_fra",
            "pretoria_zaf",
        ]

    def test_filter_by_country(self, library: list[Path]) -> None:
        selected = gallery.filter_images(library, country_code="ZAF")
        assert {path.stem for path in selected} == {"pretoria_zaf", "le_cap_zaf"}

    def test_filter_by_capital_accepts_the_display_name(self, library: list[Path]) -> None:
        selected = gallery.filter_images(library, capital="Le Caire")
        assert [path.stem for path in selected] == ["le_caire_egy"]

    def test_filters_combine(self, library: list[Path]) -> None:
        assert gallery.filter_images(library, capital="Paris", country_code="ZAF") == []

    def test_pick_avoids_recent_draws(self, library: list[Path]) -> None:
        history = [path.stem for path in library[:-1]]
        assert gallery.pick(library, history) == library[-1]

    def test_pick_reopens_everything_when_the_pool_is_exhausted(self, library: list[Path]) -> None:
        history = [path.stem for path in library]
        assert gallery.pick(library, history) in library


class TestState:
    def test_round_trip(self, tmp_path: Path) -> None:
        path = tmp_path / "state.json"
        state.save({"history": ["paris_fra"]}, path)
        assert state.load(path) == {"history": ["paris_fra"]}

    def test_a_missing_file_reads_as_empty(self, tmp_path: Path) -> None:
        assert state.load(tmp_path / "absent.json") == {}

    def test_a_broken_file_reads_as_empty(self, tmp_path: Path) -> None:
        path = tmp_path / "state.json"
        path.write_text("{ broken", encoding="utf-8")
        assert state.load(path) == {}

    def test_non_string_history_entries_are_ignored(self) -> None:
        assert state.history_of({"history": ["paris_fra", 42, None]}) == ["paris_fra"]

    def test_history_is_bounded(self) -> None:
        current: dict[str, object] = {}
        for index in range(HISTORY_MAX + 10):
            state.remember_draw(current, f"city_{index:03d}")
        history = state.history_of(current)
        assert len(history) == HISTORY_MAX
        assert history[-1] == f"city_{HISTORY_MAX + 9:03d}"
