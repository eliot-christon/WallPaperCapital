from __future__ import annotations

import pytest

from conftest import make_page
from wallpaper_capital.commons import Page, image_info
from wallpaper_capital.models import Capital
from wallpaper_capital.scoring import (
    MINIMUM_SCORE,
    capture_year,
    is_usable,
    score,
    usable_width,
)


def usable(page: Page) -> bool:
    """Run the hard rules on a page, asserting it carries the imageinfo they need."""
    info = image_info(page)
    assert info is not None
    return is_usable(page, info)


class TestUsableWidth:
    def test_a_panorama_loses_most_of_its_width_to_the_crop(self) -> None:
        assert usable_width(5061, 1200) == pytest.approx(2133, abs=1)

    def test_a_tall_image_is_limited_by_its_height(self) -> None:
        assert usable_width(7585, 2217) == pytest.approx(3941, abs=1)

    def test_a_16_9_image_keeps_its_full_width(self) -> None:
        assert usable_width(2560, 1440) == pytest.approx(2560, abs=1)


class TestHardRules:
    def test_a_large_landscape_photograph_passes(self) -> None:
        assert usable(make_page())

    @pytest.mark.parametrize(
        "mime", ["image/svg+xml", "image/gif", "video/webm", "application/pdf"]
    )
    def test_non_photographs_are_rejected(self, mime: str) -> None:
        assert not usable(make_page(mime=mime))

    def test_missing_dimensions_are_rejected(self) -> None:
        assert not usable(make_page(width=0, height=0))

    @pytest.mark.parametrize(
        "title",
        [
            "File:Map of Algiers.jpg",
            "File:Engraving of Belmopan.jpg",
            "File:Interior of the cathedral.jpg",
            "File:Independence day parade.jpg",
            "File:Bombing aftermath.jpg",
            "File:ISS064-E-412 city lights.jpg",
            "File:STS-51 orbital view.jpg",
        ],
    )
    def test_rejected_subjects(self, title: str) -> None:
        assert not usable(make_page(title=title))

    def test_a_reject_term_in_the_categories_also_counts(self) -> None:
        assert not usable(make_page(categories="Military parades in Accra"))

    def test_a_reject_term_must_be_a_whole_word(self) -> None:
        """"plan" must not fire on "planetarium"."""
        assert usable(make_page(title="File:Planetarium at dusk.jpg"))

    def test_photographs_older_than_1995_are_rejected(self) -> None:
        assert not usable(make_page(title="File:Asmara aerial view 1981.jpg"))

    def test_a_recent_photograph_passes(self) -> None:
        assert usable(make_page(title="File:Asmara aerial view 2018.jpg"))

    @pytest.mark.parametrize(
        ("width", "height"),
        [
            (1600, 900),  # below MIN_WIDTH once cropped
            (6000, 1000),  # extreme panorama, only 1778 px survive the crop
            (1200, 1000),  # ratio below MIN_RATIO
            (1950, 1150),  # wide and tall enough, but under MIN_PIXELS
        ],
    )
    def test_undersized_or_misshapen_images_are_rejected(self, width: int, height: int) -> None:
        assert not usable(make_page(width=width, height=height))


class TestCaptureYear:
    def test_the_title_wins_over_the_file_date(self) -> None:
        """A 1981 print digitised in 2007 carries a DateTimeOriginal of 2007."""
        page = make_page(title="File:Asmara aerial view 1981.jpg", date="2007:04:11 10:00:00")
        info = page["imageinfo"][0]
        assert capture_year(page, info) == 1981

    def test_falls_back_to_the_file_date(self) -> None:
        page = make_page(title="File:A view.jpg", date="2018:04:11 10:00:00")
        assert capture_year(page, page["imageinfo"][0]) == 2018

    def test_no_date_at_all(self) -> None:
        page = make_page()
        assert capture_year(page, page["imageinfo"][0]) is None


class TestScore:
    def test_disqualified_candidates_return_none(self) -> None:
        assert score(make_page(title="File:Map of Algiers.jpg")) is None

    def test_a_page_without_imageinfo_returns_none(self) -> None:
        assert score({"title": "File:Nothing.jpg"}) is None

    def test_provenance_outranks_a_bare_search_hit(self) -> None:
        page = make_page()
        from_category = score(page, "view-category")
        from_search = score(page, "search")
        assert from_category is not None and from_search is not None
        assert from_category > from_search

    def test_distinctions_help(self) -> None:
        plain = score(make_page(), "city-category")
        featured = score(make_page(assessments="featured"), "city-category")
        assert plain is not None and featured is not None
        assert featured > plain

    def test_stacked_distinctions_are_capped(self) -> None:
        """A four-time winner must not beat relevance outright."""
        every = score(make_page(assessments="featured|quality|valued|poty|potd"), "search")
        featured = score(make_page(assessments="featured"), "search")
        assert every is not None and featured is not None
        assert every - featured <= 20

    def test_the_city_name_in_the_title_is_rewarded(self, paris: Capital) -> None:
        named = score(make_page(title="File:Paris skyline.jpg"), "city-category", paris)
        anonymous = score(make_page(title="File:Skyline.jpg"), "city-category", paris)
        assert named is not None and anonymous is not None
        assert named > anonymous

    def test_the_french_and_english_names_both_count(self, cairo: Capital) -> None:
        french = score(make_page(title="File:Le Caire at night.jpg"), "city-category", cairo)
        english = score(make_page(title="File:Cairo at night.jpg"), "city-category", cairo)
        unrelated = score(make_page(title="File:A tower.jpg"), "city-category", cairo)
        assert french is not None and english is not None and unrelated is not None
        assert french > unrelated
        assert english > unrelated

    def test_reuse_across_wikimedia_is_rewarded(self) -> None:
        iconic = score(make_page(usages=10), "city-category")
        obscure = score(make_page(usages=0), "city-category")
        assert iconic is not None and obscure is not None
        assert iconic > obscure

    def test_framing_prefers_16_9(self) -> None:
        wide_screen = score(make_page(width=4000, height=2250), "city-category")
        squarish = score(make_page(width=3000, height=2000), "city-category")
        assert wide_screen is not None and squarish is not None
        assert wide_screen > squarish

    def test_a_wide_panorama_falls_below_the_quality_floor(self) -> None:
        """5061x1200 survives the hard rules but only 2133 px reach the screen."""
        value = score(make_page(width=5061, height=1200), "search")
        assert value is not None
        assert value < MINIMUM_SCORE

    def test_panoramio_uploads_are_penalised(self) -> None:
        plain = score(make_page(categories="Views of Accra"), "city-category")
        panoramio = score(
            make_page(categories="Views of Accra Panoramio uploads"), "city-category"
        )
        assert plain is not None and panoramio is not None
        assert plain > panoramio
