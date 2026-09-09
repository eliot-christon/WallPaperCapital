"""Building the candidate pool and picking the winner.

Searching Commons for a city name does not work: the full-text index has no idea
where a photo was taken. A search for "Algiers" used to return a prize-winning
shot of the harbour of Sète, and "Belmopan" a Belizean motorway.

So the pool is built from what Commons genuinely ties to a place — the categories
a file sits in and its coordinates — in decreasing order of trust. The old text
search survives only as a last resort, for capitals too rarely photographed to be
categorised at all.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable, Iterable
from functools import partial
from typing import NamedTuple

import requests

from wallpaper_capital.api import ApiError
from wallpaper_capital.commons import CommonsClient, Page, assessments, image_info
from wallpaper_capital.models import Capital
from wallpaper_capital.scoring import MINIMUM_SCORE, score, subject_text
from wallpaper_capital.text import mentions_place

log = logging.getLogger(__name__)

# How many view categories to explore per city, to bound the number of requests.
MAX_VIEW_CATEGORIES = 8

# Names Commons uses by convention to group the views of a city. Probing them
# directly catches the categories the tree does not hang off the main city
# category, such as "Views of Baghdad".
VIEW_CATEGORY_PATTERNS = (
    "Views of {}", "Cityscapes of {}", "Cityscape of {}", "Skyline of {}",
    "Skylines of {}", "Aerial photographs of {}", "Aerial views of {}",
    "Panoramics of {}", "Panoramas of {}", "Night views of {}",
    "Sunsets in {}", "Sunrises in {}", "{} at night", "{} skyline",
)

# The same net, cast over the sub-categories that really exist under the city.
VIEW_CATEGORY_RE = re.compile(
    r"^(views?|cityscapes?|skylines?|panoramas?|panoramics?|aerial photographs?|"
    r"aerial views?|aerial photography|night views?|sunsets?|sunrises?) (of|in|from) "
    r"| at night$| skyline$| panorama$",
    re.I,
)

# Errors that mean "this source is unavailable" rather than "this run is broken".
SOURCE_ERRORS = (requests.RequestException, ApiError, ValueError)


class Candidate(NamedTuple):
    """A Commons file and the kind of source that produced it."""

    page: Page
    source: str


def city_aliases(item: Capital) -> list[str]:
    """City spellings worth probing, most authoritative first.

    Wikidata gives the disambiguated category, "Wellington urban area", while the
    view categories follow the common name, "Cityscapes of Wellington". Without
    both spellings, 21 capitals lose their best source of images.
    """
    aliases = [item.city_category]
    seen = {item.city_category.casefold()}
    for alias in (item.search_capital, item.capital):
        if alias and alias.casefold() not in seen:
            aliases.append(alias)
            seen.add(alias.casefold())
    return aliases


def keep_local_categories(
    client: CommonsClient, names: list[str], item: Capital
) -> list[str]:
    """Drop homonyms: "Views of Victoria" may well be a different Victoria.

    A category named after the city's Wikidata category is trustworthy by
    construction. For the others, derived from the common name, we require a
    parent category naming the country or the city — such as "Cityscapes in New
    Zealand" above "Cityscapes of Wellington".
    """
    trusted = {pattern.format(item.city_category).casefold() for pattern in VIEW_CATEGORY_PATTERNS}
    doubtful = [name for name in names if name.casefold() not in trusted]
    if not doubtful:
        return names

    parents = client.category_parents(doubtful)
    anchors = {
        text.casefold()
        for text in (item.search_country, item.country, item.city_category)
        if text
    }
    kept: list[str] = []
    for name in names:
        if name.casefold() in trusted:
            kept.append(name)
            continue
        lineage = " | ".join(parents.get(name, [])).casefold()
        if any(anchor in lineage for anchor in anchors):
            kept.append(name)
        else:
            log.debug("Category dropped, uncertain attachment: %s", name)
    return kept


def view_categories(client: CommonsClient, item: Capital) -> list[str]:
    """View categories for a city: conventional names, then real sub-categories."""
    found: list[str] = []
    try:
        found = client.existing_categories(
            pattern.format(name)
            for name in city_aliases(item)
            for pattern in VIEW_CATEGORY_PATTERNS
        )
        found = keep_local_categories(client, found, item)
    except SOURCE_ERRORS as exc:
        log.warning("View categories unreadable for %s: %s", item.city_category, exc)

    try:
        found.extend(
            name
            for name in client.subcategories(item.city_category)
            if VIEW_CATEGORY_RE.search(name) and name not in found
        )
    except SOURCE_ERRORS as exc:
        log.debug("Sub-categories unreadable for %s: %s", item.city_category, exc)
    return found[:MAX_VIEW_CATEGORIES]


def query_variants(item: Capital) -> list[str]:
    """Fallback full-text queries, for cities with no usable category."""
    city = item.search_capital
    country = item.search_country
    return [
        f'"{city}" skyline {country}',
        f'"{city}" cityscape {country}',
        f'"{city}" panorama {country}',
        f'"{city}" aerial view {country}',
    ]


class CandidatePool(dict[str, Candidate]):
    """Candidates keyed by file title, keeping the most trusted source per file."""

    def absorb(self, pages: Iterable[Page], source: str) -> None:
        for page in pages:
            if image_info(page):
                # setdefault: a file seen through several sources keeps the most
                # trustworthy one, which is the one encountered first.
                self.setdefault(str(page.get("title") or ""), Candidate(page, source))

    def best(self, item: Capital, *, quality_only: bool = False) -> Candidate | None:
        """The highest-scoring candidate above `MINIMUM_SCORE`, or None."""
        entries = [
            candidate
            for candidate in self.values()
            if not quality_only or assessments(image_info(candidate.page) or {})
        ]
        ranked = [
            (value, candidate)
            for candidate in entries
            if (value := score(candidate.page, candidate.source, item)) is not None
            and value > MINIMUM_SCORE
        ]
        if not ranked:
            return None
        return max(ranked, key=lambda entry: entry[0])[1]


def collect_candidates(client: CommonsClient, item: Capital) -> CandidatePool:
    """Gather candidates, from the tightest attachment to the loosest."""
    pool = CandidatePool()

    def attempt(label: str, fetch: Callable[[], list[Page]], source: str) -> None:
        try:
            pool.absorb(fetch(), source)
        except SOURCE_ERRORS as exc:
            log.warning("%s unavailable for %s: %s", label, item.capital, exc)

    for category in view_categories(client, item):
        attempt(f"Category {category!r}", partial(client.category_files, category), "view-category")

    if item.wikidata_image:
        attempt(
            "Wikidata image",
            partial(client.files_by_title, [item.wikidata_image]),
            "wikidata",
        )

    attempt(
        f"Category {item.city_category!r}",
        partial(client.category_files, item.city_category),
        "city-category",
    )

    if item.latitude is not None and item.longitude is not None:
        attempt(
            "Coordinate search",
            partial(client.geosearch_files, item.latitude, item.longitude),
            "geosearch",
        )
    return pool


def search_candidates(client: CommonsClient, item: Capital, result_limit: int) -> CandidatePool:
    """Last-resort pool, built from full-text search and filtered on the city name."""
    pool = CandidatePool()
    names = {item.capital.casefold(), item.search_capital.casefold()}
    for query in query_variants(item):
        try:
            pages = client.search(query, result_limit)
        except SOURCE_ERRORS as exc:
            log.warning("Search failed for %s (%s): %s", item.capital, query, exc)
            continue
        for page in pages:
            info = image_info(page)
            if not info:
                continue
            # Full-text search guarantees nothing, so require the city to be named
            # in the file's own title or categories.
            subject = subject_text(page, info)
            if any(mentions_place(subject, name) for name in names):
                pool.setdefault(str(page.get("title") or ""), Candidate(page, "search"))
    return pool


def resolve_override(client: CommonsClient, item: Capital, title: str) -> Candidate | None:
    """The manually chosen file, or None if it cannot be fetched."""
    try:
        pages = client.files_by_title([title])
    except SOURCE_ERRORS as exc:
        log.warning("Override unreadable for %s: %s", item.capital, exc)
        return None
    for page in pages:
        if image_info(page):
            return Candidate(page, "override")
    log.warning(
        "Override not found for %s (%s), falling back to normal selection", item.capital, title
    )
    return None


def find_best_image(
    client: CommonsClient,
    item: Capital,
    *,
    result_limit: int = 20,
    quality_only: bool = False,
    override_title: str = "",
) -> Candidate | None:
    """Pick an image: manual override first, then categories, then full-text search."""
    if override_title and (chosen := resolve_override(client, item, override_title)):
        return chosen

    if best := collect_candidates(client, item).best(item, quality_only=quality_only):
        return best

    log.info("No categorised candidate for %s, falling back to search", item.capital)
    return search_candidates(client, item, result_limit).best(item, quality_only=quality_only)
