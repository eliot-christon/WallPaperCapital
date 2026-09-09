from __future__ import annotations

import json
from pathlib import Path

import pytest

from wallpaper_capital import manifest, overrides
from wallpaper_capital.download import DownloadOptions, existing_output
from wallpaper_capital.imaging import extension_for_mime, target_size
from wallpaper_capital.models import Capital, WallpaperRecord


def record(capital: str = "Paris", country_code: str = "FRA") -> WallpaperRecord:
    return WallpaperRecord(
        country="France",
        country_code=country_code,
        capital=capital,
        filename=f"data/wallpaper/{capital.lower()}.jpg",
        search_query="Paris",
        source_url="https://upload.example.org/a.jpg",
        source_page="https://commons.wikimedia.org/wiki/File:A.jpg",
        author="Jane Doe",
        license="CC BY-SA 4.0",
        license_url="https://creativecommons.org/licenses/by-sa/4.0/",
        assessment="quality",
        source_kind="view-category",
        original_width=4000,
        original_height=2250,
        downloaded_at="2026-01-01T00:00:00+00:00",
    )


class TestCapital:
    def test_file_stem_matches_the_download_naming(self, cairo: Capital) -> None:
        assert cairo.file_stem == "le_caire_egy"

    def test_search_labels_prefer_english(self, cairo: Capital) -> None:
        assert cairo.search_capital == "Cairo"
        assert cairo.search_country == "Egypt"

    def test_search_labels_fall_back_to_the_display_name(self) -> None:
        item = Capital(country="Tchad", country_code="TCD", capital="N'Djaména")
        assert item.search_capital == "N'Djaména"
        assert item.city_category == "N'Djaména"

    @pytest.mark.parametrize("needle", ["Le Caire", "Cairo", "le caire", "CAIRO"])
    def test_matches_name_accepts_both_spellings(self, cairo: Capital, needle: str) -> None:
        assert cairo.matches_name(needle)

    def test_matches_name_rejects_other_cities(self, cairo: Capital) -> None:
        assert not cairo.matches_name("Alexandria")

    def test_coordinates_are_optional(self, cairo: Capital, paris: Capital) -> None:
        assert paris.has_coordinates
        assert not cairo.has_coordinates


class TestManifest:
    def test_round_trip(self, tmp_path: Path) -> None:
        path = tmp_path / "manifest.json"
        entries: manifest.Entries = {}
        manifest.add(entries, record())
        manifest.write(path, entries)
        assert manifest.load(path)[("FRA", "Paris")]["author"] == "Jane Doe"

    def test_entries_are_sorted_by_capital(self, tmp_path: Path) -> None:
        path = tmp_path / "manifest.json"
        entries: manifest.Entries = {}
        manifest.add(entries, record("Zagreb", "HRV"))
        manifest.add(entries, record("Alger", "DZA"))
        manifest.write(path, entries)
        written = json.loads(path.read_text(encoding="utf-8"))
        assert [entry["capital"] for entry in written] == ["Alger", "Zagreb"]

    def test_re_adding_a_capital_replaces_its_entry(self) -> None:
        entries: manifest.Entries = {}
        manifest.add(entries, record())
        manifest.add(entries, record())
        assert len(entries) == 1

    def test_a_missing_manifest_reads_as_empty(self, tmp_path: Path) -> None:
        assert manifest.load(tmp_path / "absent.json") == {}

    def test_a_broken_manifest_reads_as_empty(self, tmp_path: Path) -> None:
        path = tmp_path / "manifest.json"
        path.write_text("{ broken", encoding="utf-8")
        assert manifest.load(path) == {}


class TestOverrides:
    def write(self, tmp_path: Path, payload: str) -> Path:
        path = tmp_path / "overrides.json"
        path.write_text(payload, encoding="utf-8")
        return path

    def test_the_file_prefix_is_optional(self, tmp_path: Path) -> None:
        path = self.write(tmp_path, '{"Bichkek": "A view.jpg", "Basseterre": "File:B.jpg"}')
        loaded = overrides.load(path)
        assert loaded["bichkek"] == "File:A view.jpg"
        assert loaded["basseterre"] == "File:B.jpg"

    def test_lookup_by_name_or_code(self, tmp_path: Path, cairo: Capital) -> None:
        for key in ("Le Caire", "Cairo", "EGY"):
            path = self.write(tmp_path, json.dumps({key: "X.jpg"}))
            assert overrides.for_capital(overrides.load(path), cairo) == "File:X.jpg"

    def test_no_override_returns_an_empty_string(self, paris: Capital) -> None:
        assert overrides.for_capital({}, paris) == ""

    def test_empty_values_are_skipped(self, tmp_path: Path) -> None:
        assert overrides.load(self.write(tmp_path, '{"Bichkek": "  "}')) == {}

    def test_a_missing_file_is_not_an_error(self, tmp_path: Path) -> None:
        assert overrides.load(tmp_path / "absent.json") == {}

    def test_a_broken_file_is_not_fatal(self, tmp_path: Path) -> None:
        assert overrides.load(self.write(tmp_path, "{ broken")) == {}

    def test_a_json_list_is_rejected(self, tmp_path: Path) -> None:
        assert overrides.load(self.write(tmp_path, '["Bichkek"]')) == {}


class TestImaging:
    @pytest.mark.parametrize(
        ("mime", "expected"),
        [("image/jpeg", ".jpg"), ("image/PNG", ".png"), ("image/heic", ".img"), ("", ".img")],
    )
    def test_extension_for_mime(self, mime: str, expected: str) -> None:
        assert extension_for_mime(mime) == expected

    def test_target_size_is_16_9(self) -> None:
        assert target_size(2560) == (2560, 1440)
        assert target_size(1920) == (1920, 1080)


class TestDownloadOptions:
    def test_converting_asks_for_a_thumbnail_at_least_as_wide_as_the_output(self) -> None:
        assert DownloadOptions(Path("out"), width=3840).thumb_width == 3840

    def test_a_tiny_output_still_asks_for_a_usable_thumbnail(self) -> None:
        assert DownloadOptions(Path("out"), width=640).thumb_width == 640

    def test_keeping_the_original_format_needs_no_large_thumbnail(self) -> None:
        assert DownloadOptions(Path("out"), convert_to_jpeg=False).thumb_width == 1280


class TestExistingOutput:
    def test_finds_the_jpeg_when_converting(self, tmp_path: Path) -> None:
        (tmp_path / "paris_fra.jpg").write_bytes(b"")
        assert existing_output(tmp_path, "paris_fra", True) is not None

    def test_ignores_other_extensions_when_converting(self, tmp_path: Path) -> None:
        (tmp_path / "paris_fra.png").write_bytes(b"")
        assert existing_output(tmp_path, "paris_fra", True) is None

    def test_accepts_any_extension_when_keeping_the_original(self, tmp_path: Path) -> None:
        (tmp_path / "paris_fra.png").write_bytes(b"")
        assert existing_output(tmp_path, "paris_fra", False) is not None

    def test_returns_none_when_nothing_was_downloaded(self, tmp_path: Path) -> None:
        assert existing_output(tmp_path, "paris_fra", True) is None
