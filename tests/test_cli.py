from __future__ import annotations

import argparse
from pathlib import Path

import pytest

from wallpaper_capital.cli import build_parser, select_capitals
from wallpaper_capital.models import Capital

CAPITALS = [
    Capital(country="Afrique du Sud", country_code="ZAF", capital="Le Cap", capital_en="Cape Town"),
    Capital(country="Égypte", country_code="EGY", capital="Le Caire", capital_en="Cairo"),
    Capital(country="France", country_code="FRA", capital="Paris", capital_en="Paris"),
    Capital(country="Afrique du Sud", country_code="ZAF", capital="Pretoria"),
]


def parse(*argv: str) -> argparse.Namespace:
    return build_parser().parse_args(argv)


class TestParser:
    def test_a_subcommand_is_required(self) -> None:
        with pytest.raises(SystemExit):
            parse()

    def test_download_defaults(self) -> None:
        args = parse("download")
        assert args.command == "download"
        assert args.width == 2560
        assert not args.force
        assert not args.verbose

    def test_lockscreen_defaults(self) -> None:
        args = parse("lockscreen")
        assert args.command == "lockscreen"
        assert not args.dry_run
        assert args.manual_dir == Path("data/manual")
        assert args.file is None

    def test_lockscreen_accepts_an_explicit_file(self) -> None:
        args = parse("lockscreen", "--file", "data/manual/port_vila_vut.jpg")
        assert args.file == Path("data/manual/port_vila_vut.jpg")

    def test_verbose_is_available_on_both_subcommands(self) -> None:
        assert parse("download", "--verbose").verbose
        assert parse("lockscreen", "--verbose").verbose


class TestSelectCapitals:
    def test_no_filter_keeps_everything(self) -> None:
        assert select_capitals(CAPITALS, parse("download")) == CAPITALS

    @pytest.mark.parametrize("name", ["Le Caire", "Cairo"])
    def test_capital_filter_accepts_both_spellings(self, name: str) -> None:
        selected = select_capitals(CAPITALS, parse("download", "--capital", name))
        assert [item.capital for item in selected] == ["Le Caire"]

    def test_country_filter_is_case_insensitive(self) -> None:
        selected = select_capitals(CAPITALS, parse("download", "--country-code", "zaf"))
        assert [item.capital for item in selected] == ["Le Cap", "Pretoria"]

    def test_limit_truncates(self) -> None:
        assert len(select_capitals(CAPITALS, parse("download", "--limit", "2"))) == 2

    def test_filters_combine(self) -> None:
        args = parse("download", "--country-code", "ZAF", "--capital", "Pretoria")
        assert [item.capital for item in select_capitals(CAPITALS, args)] == ["Pretoria"]

    def test_an_unknown_capital_selects_nothing(self) -> None:
        assert select_capitals(CAPITALS, parse("download", "--capital", "Atlantis")) == []
