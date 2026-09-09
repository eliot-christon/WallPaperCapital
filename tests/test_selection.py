from __future__ import annotations

from conftest import make_page
from wallpaper_capital.models import Capital
from wallpaper_capital.selection import (
    VIEW_CATEGORY_RE,
    CandidatePool,
    city_aliases,
    query_variants,
)


class TestCityAliases:
    def test_the_wikidata_category_comes_first(self) -> None:
        wellington = Capital(
            country="Nouvelle-Zélande",
            country_code="NZL",
            capital="Wellington",
            capital_en="Wellington",
            commons_category="Wellington urban area",
        )
        assert city_aliases(wellington) == ["Wellington urban area", "Wellington"]

    def test_both_spellings_are_probed(self, cairo: Capital) -> None:
        assert city_aliases(cairo) == ["Cairo", "Le Caire"]

    def test_duplicates_are_collapsed(self, paris: Capital) -> None:
        assert city_aliases(paris) == ["Paris"]


class TestViewCategoryPattern:
    def test_matches_the_conventional_names(self) -> None:
        for name in (
            "Views of Baghdad",
            "Cityscapes of Wellington",
            "Aerial photographs of Accra",
            "Night views of Doha",
            "Bogota at night",
            "Helsinki skyline",
        ):
            assert VIEW_CATEGORY_RE.search(name), name

    def test_ignores_unrelated_subcategories(self) -> None:
        for name in ("Churches in Baghdad", "People of Wellington", "Maps of Accra"):
            assert not VIEW_CATEGORY_RE.search(name), name


def test_query_variants_quote_the_city(cairo: Capital) -> None:
    variants = query_variants(cairo)
    assert all(variant.startswith('"Cairo"') for variant in variants)
    assert all(variant.endswith("Egypt") for variant in variants)


class TestCandidatePool:
    def test_the_first_source_seen_wins(self) -> None:
        pool = CandidatePool()
        pool.absorb([make_page("File:A.jpg")], "view-category")
        pool.absorb([make_page("File:A.jpg")], "geosearch")
        assert pool["File:A.jpg"].source == "view-category"

    def test_pages_without_imageinfo_are_ignored(self) -> None:
        pool = CandidatePool()
        pool.absorb([{"title": "File:Empty.jpg"}], "geosearch")
        assert not pool

    def test_best_prefers_the_higher_score(self, paris: Capital) -> None:
        pool = CandidatePool()
        pool.absorb([make_page("File:Paris skyline.jpg", assessments="featured")], "view-category")
        pool.absorb([make_page("File:Anonymous.jpg")], "geosearch")
        best = pool.best(paris)
        assert best is not None
        assert best.page["title"] == "File:Paris skyline.jpg"

    def test_best_returns_none_when_everything_is_disqualified(self, paris: Capital) -> None:
        pool = CandidatePool()
        pool.absorb([make_page("File:Map of Paris.jpg")], "view-category")
        assert pool.best(paris) is None

    def test_quality_only_drops_undistinguished_images(self, paris: Capital) -> None:
        pool = CandidatePool()
        pool.absorb([make_page("File:Plain.jpg")], "view-category")
        assert pool.best(paris) is not None
        assert pool.best(paris, quality_only=True) is None

    def test_quality_only_keeps_distinguished_images(self, paris: Capital) -> None:
        pool = CandidatePool()
        pool.absorb([make_page("File:Fine.jpg", assessments="quality")], "view-category")
        assert pool.best(paris, quality_only=True) is not None
