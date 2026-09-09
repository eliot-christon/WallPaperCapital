"""A thin client over the Wikimedia Commons query API.

Every endpoint the selector needs lives here, so the rest of the package deals in
pages and titles rather than in query parameters.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Iterable
from typing import Any

import requests

from wallpaper_capital.api import ApiError
from wallpaper_capital.text import strip_html

log = logging.getLogger(__name__)

API_URL = "https://commons.wikimedia.org/w/api.php"

# Radius of the coordinate search around the centre of a capital.
GEOSEARCH_RADIUS_M = 7000

# Titles are batched; the API caps a `titles` list at 50 for anonymous clients.
TITLES_PER_REQUEST = 40

#: One page of the Commons query API, i.e. one file and its metadata.
Page = dict[str, Any]

_IMAGE_PROPS: dict[str, Any] = {
    "prop": "imageinfo|globalusage",
    "iiprop": "url|size|mime|extmetadata",
    # An image reused across the Wikipedias genuinely illustrates the city: it is
    # the best automatic stand-in for the judgement "this view is iconic".
    "guprop": "url",
    "gulimit": 20,
    "gufilterlocal": 1,
}


def image_info(page: Page) -> dict[str, Any] | None:
    """The first `imageinfo` block of a page, or None when the page carries none."""
    infos = page.get("imageinfo") or []
    return infos[0] if infos else None


def assessments(info: dict[str, Any]) -> set[str]:
    """Commons distinctions on an image: featured, quality, valued, poty, potd."""
    metadata = info.get("extmetadata") or {}
    raw = str((metadata.get("Assessments") or {}).get("value") or "")
    return {part.strip().lower() for part in raw.split("|") if part.strip()}


def metadata_value(metadata: dict[str, Any], key: str) -> str:
    """Read one `extmetadata` field as plain text."""
    value = metadata.get(key) or {}
    if isinstance(value, dict):
        return strip_html(str(value.get("value") or ""))
    return strip_html(str(value))


def _chunks(items: list[str], size: int) -> Iterable[list[str]]:
    for offset in range(0, len(items), size):
        yield items[offset : offset + size]


class CommonsClient:
    """Queries Commons, pacing requests so the API stays happy."""

    def __init__(
        self,
        session: requests.Session,
        *,
        pause: float = 0.6,
        thumb_width: int = 2560,
    ) -> None:
        self._session = session
        self._pause = pause
        self._thumb_width = thumb_width

    def query(self, **params: Any) -> dict[str, Any]:
        """Call the query API and return its JSON, honouring the configured pause."""
        params.update({"action": "query", "format": "json", "formatversion": 2})
        response = self._session.get(API_URL, params=params, timeout=45)
        response.raise_for_status()
        data: dict[str, Any] = response.json()
        time.sleep(self._pause)
        if error := data.get("error"):
            raise ApiError(f"Wikimedia Commons API: {error}")
        return data

    def _pages(self, **params: Any) -> list[Page]:
        pages = self.query(**params).get("query", {}).get("pages", [])
        return list(pages)

    def existing_categories(self, names: Iterable[str]) -> list[str]:
        """Keep only the categories that exist and actually hold files."""
        wanted = list(dict.fromkeys(names))
        found: list[str] = []
        for chunk in _chunks(wanted, TITLES_PER_REQUEST):
            pages = self._pages(
                titles="|".join(f"Category:{name}" for name in chunk),
                prop="categoryinfo",
            )
            found.extend(
                str(page.get("title", "")).removeprefix("Category:")
                for page in pages
                if (page.get("categoryinfo") or {}).get("files")
            )
        # The API returns pages out of order; restore the caller's preference order.
        return sorted(found, key=lambda name: wanted.index(name) if name in wanted else len(wanted))

    def subcategories(self, category: str) -> list[str]:
        """Direct sub-categories of a category."""
        members = (
            self.query(
                list="categorymembers",
                cmtitle=f"Category:{category}",
                cmtype="subcat",
                cmlimit=200,
            )
            .get("query", {})
            .get("categorymembers", [])
        )
        return [str(member.get("title", "")).removeprefix("Category:") for member in members]

    def category_parents(self, names: Iterable[str]) -> dict[str, list[str]]:
        """Parent categories, used to check that a category is about the right country."""
        parents: dict[str, list[str]] = {}
        for chunk in _chunks(list(dict.fromkeys(names)), TITLES_PER_REQUEST):
            pages = self._pages(
                titles="|".join(f"Category:{name}" for name in chunk),
                prop="categories",
                cllimit=500,
            )
            for page in pages:
                title = str(page.get("title", "")).removeprefix("Category:")
                parents[title] = [
                    str(entry.get("title", "")).removeprefix("Category:")
                    for entry in page.get("categories") or []
                ]
        return parents

    def category_files(self, category: str, limit: int = 200) -> list[Page]:
        """Files filed directly under a category."""
        return self._pages(
            generator="categorymembers",
            gcmtitle=f"Category:{category}",
            gcmtype="file",
            gcmlimit=limit,
            iiurlwidth=self._thumb_width,
            **_IMAGE_PROPS,
        )

    def geosearch_files(self, latitude: float, longitude: float, limit: int = 100) -> list[Page]:
        """Files geotagged within `GEOSEARCH_RADIUS_M` of a point."""
        return self._pages(
            generator="geosearch",
            ggscoord=f"{latitude}|{longitude}",
            ggsradius=GEOSEARCH_RADIUS_M,
            ggsnamespace=6,
            ggslimit=limit,
            iiurlwidth=self._thumb_width,
            **_IMAGE_PROPS,
        )

    def files_by_title(self, titles: Iterable[str]) -> list[Page]:
        """Fetch specific files, e.g. a Wikidata reference image or a manual override."""
        wanted = [title for title in titles if title]
        if not wanted:
            return []
        return self._pages(titles="|".join(wanted), iiurlwidth=self._thumb_width, **_IMAGE_PROPS)

    def search(self, query: str, limit: int) -> list[Page]:
        """Full-text search, the last-resort source of candidates."""
        # A single file that cannot be thumbnailed — a multi-gigapixel satellite
        # TIFF, say — fails the whole request. Retry without thumbnails rather
        # than lose the other results.
        for width in (self._thumb_width, 0):
            params: dict[str, Any] = {
                "generator": "search",
                # filetype:bitmap rules out SVG and PDF at the search stage.
                "gsrsearch": f"{query} filetype:bitmap",
                "gsrnamespace": 6,
                "gsrlimit": min(limit, 50),
                **_IMAGE_PROPS,
            }
            if width:
                params["iiurlwidth"] = width
            try:
                return self._pages(**params)
            except ApiError as exc:
                if width and "urlparamnormal" in str(exc):
                    log.debug("Commons refused a thumbnail for %r", query)
                    continue
                raise
        return []
