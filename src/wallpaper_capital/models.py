"""Domain objects passed between the Wikidata, Commons and manifest layers."""

from __future__ import annotations

from dataclasses import dataclass

from wallpaper_capital.text import slugify


@dataclass(frozen=True)
class Capital:
    """A capital city and the anchors that let Commons be searched for it."""

    country: str
    country_code: str
    capital: str
    # English labels drive the Commons queries; the French ones stay on screen and
    # in the manifest. Commons is indexed in English: "Abou Dabi" returns nothing
    # where "Abu Dhabi" returns the city.
    country_en: str = ""
    capital_en: str = ""
    # Wikidata anchors: the city's Commons category, the coordinates of its centre
    # and the reference image the community settled on.
    commons_category: str = ""
    latitude: float | None = None
    longitude: float | None = None
    wikidata_image: str = ""

    @property
    def search_country(self) -> str:
        return self.country_en or self.country

    @property
    def search_capital(self) -> str:
        return self.capital_en or self.capital

    @property
    def city_category(self) -> str:
        """The city's Commons category, falling back to its English label."""
        return self.commons_category or self.search_capital

    @property
    def has_coordinates(self) -> bool:
        return self.latitude is not None and self.longitude is not None

    @property
    def file_stem(self) -> str:
        """Filename stem of this capital's wallpaper, e.g. `le_caire_egy`."""
        return f"{slugify(self.capital)}_{slugify(self.country_code)}"

    def matches_name(self, needle: str) -> bool:
        """True for either spelling of the city, so `--capital` accepts both."""
        folded = needle.casefold()
        return folded in {self.capital.casefold(), self.search_capital.casefold()}


@dataclass(frozen=True)
class WallpaperRecord:
    """One manifest entry: what was downloaded, from where, and under which licence."""

    country: str
    country_code: str
    capital: str
    filename: str
    search_query: str
    source_url: str
    source_page: str
    author: str
    license: str
    license_url: str
    assessment: str
    source_kind: str
    original_width: int
    original_height: int
    downloaded_at: str
