"""Deciding which candidate makes the better wallpaper.

`score` first applies the hard rules — is this even a usable photograph of a city?
— and returns None when one of them fires. Everything that survives is then ranked
on provenance, community distinctions, reuse, framing and resolution.
"""

from __future__ import annotations

import re
from typing import Any

from wallpaper_capital.commons import Page, assessments, image_info, metadata_value
from wallpaper_capital.imaging import TARGET_RATIO
from wallpaper_capital.models import Capital
from wallpaper_capital.text import has_term, mentions_place, strip_html

# A wallpaper has to fill the screen without being obviously upscaled. Lowering
# this floor let old 1800 px night shots back in, nearly black once enlarged.
MIN_WIDTH = 1920
MIN_PIXELS = 2_300_000
MIN_RATIO = 1.3

# Past this ratio the image is a frieze rather than a view: even when the crop
# keeps enough pixels, the original framing no longer means anything.
MAX_COMFORTABLE_RATIO = 3.0

# Candidates below this score are dropped rather than used. A low-resolution
# panorama found by full-text search is worse than no wallpaper at all.
MINIMUM_SCORE = 0.0

# Commons has its community assess images. These distinctions, exposed through
# extmetadata.Assessments, remain a good hint at beauty, but they weigh less than
# they used to: relevance is now guaranteed by the anchoring on the city, and no
# longer has to be carried by the awards.
ASSESSMENT_BONUS = {
    "featured": 100,  # featured picture, elected by vote
    "quality": 35,  # technical quality validated by a reviewer
    "valued": 25,  # best illustration of its subject
    "poty": 50,  # picture of the year
    "potd": 20,  # picture of the day
}

# Distinctions stack and overlap: "picture of the year" implies "featured
# picture". Without a ceiling a four-time winner beats relevance outright.
MAX_ASSESSMENT_BONUS = 120

# Where a candidate came from, in decreasing order of trust. An image drawn from
# a "Views of X" category shows the city by construction, where a seven-kilometre
# radius catches a church interior just as readily as a panorama.
SOURCE_BONUS = {
    "view-category": 80,
    "wikidata": 55,
    "city-category": 20,
    "geosearch": 10,
    "search": 0,
}

# Deal-breakers, read from the file title and its categories. A fine photograph of
# a ceremony, an interior or a bombing is still a poor wallpaper.
REJECT_TERMS = (
    # Not a photograph, or not usable full-screen.
    "map", "maps", "plan", "atlas", "engraving", "etching", "lithograph",
    "woodcut", "painting", "drawing", "sketch", "illustration", "manuscript",
    "poster", "stamp", "banknote", "coin", "medal", "diagram", "chart",
    "coat of arms", "flag", "flags", "logo", "icon", "seal", "emblem", "blazon",
    "pd-art", "pd-old", "caricature", "postcard", "screenshot", "collage",
    "montage",
    # Scans of old books: "AFR V2 D333 General view of Algiers" is a digitised
    # book plate, not a photograph of the city.
    "scan", "scans", "internet archive", "digitised", "digitized",
    "black and white", "monochrome", "sepia", "glass plate", "archival",
    # Interiors: no depth, no sky.
    "interior", "interiors", "inside", "indoor", "ceiling", "staircase",
    "corridor", "nave", "altar", "exhibit", "exhibition", "museum object",
    # People and events: the city is only a backdrop.
    "portrait", "portraits", "celebration", "celebrations", "independence day",
    "festival", "parade", "march", "crowd", "crowds", "protest", "rally",
    "riot", "funeral", "wedding", "election", "campaign", "carnival",
    "concert", "conference", "meeting", "summit", "workshop", "ceremony",
    "visit", "visits", "secretary", "admiral", "ambassador", "delegation",
    "navy", "marine corps", "army", "troops", "soldiers", "police", "military",
    "dvids",
    # War and disaster.
    "bomb", "bombing", "explosion", "attack", "destroyed", "destruction",
    "ruins", "damage", "damaged", "war", "airstrike", "shelling",
    # Orbital views: spectacular, but no longer a city.
    "astronaut", "satellite image", "from space", "earth observation",
    # Vehicles: they are filed under view categories, yet the craft is the
    # subject. The pleasure boat "Capitan Morgan" was illustrating Algiers.
    "boat", "boats", "ship", "ships", "ferry", "yacht", "vessel", "aircraft",
    "airplane", "helicopter", "train", "trains", "locomotive", "railway",
    "railroad", "tram", "bus", "car", "cars", "motorcycle", "highway",
    "motorway",
    # Details and building sites.
    "close-up", "closeup", "detail", "plaque", "signage", "construction site",
    "under construction", "unidentified",
)

# Words that promise a wide view rather than a single building, and often a
# flattering light.
PREFERRED_TERMS = {
    "skyline": 14, "cityscape": 14, "panorama": 8, "panoramic": 8,
    "aerial": 8, "night": 10, "sunset": 10, "sunrise": 10, "dusk": 8,
    "dawn": 8, "twilight": 8, "downtown": 6, "overlooking": 8, "view": 5,
}

# Cities change: a view from before this year no longer depicts the place.
OLDEST_ACCEPTABLE_YEAR = 1995

# A pre-1995 year in the title, as in "Asmara aerial view 1981".
_OUTDATED_YEAR_RE = re.compile(r"\b(1[89]\d\d|199[0-4])\b")
_ANY_YEAR_RE = re.compile(r"\b(1[89]\d\d|20\d\d)\b")
# Orbital photographs are titled "ISS064-E-412" or "STS-51".
_ORBITAL_RE = re.compile(r"\b(iss|sts)[-\s]?\d")

_UNUSABLE_MIMES = frozenset({"image/svg+xml", "image/gif"})


def subject_text(page: Page, info: dict[str, Any]) -> str:
    """Title and categories: what describes the subject, not the photographer."""
    metadata = info.get("extmetadata") or {}
    categories = strip_html((metadata.get("Categories") or {}).get("value"))
    return f"{page.get('title', '')} {categories}".lower()


def capture_year(page: Page, info: dict[str, Any]) -> int | None:
    """Year the photograph was taken. The title wins, because file dates lie.

    "Asmara aerial view 1981" carries a DateTimeOriginal of 2007: that is when the
    print was digitised, not when the photograph was taken.
    """
    if match := _OUTDATED_YEAR_RE.search(str(page.get("title", ""))):
        return int(match.group(1))
    metadata = info.get("extmetadata") or {}
    for key in ("DateTimeOriginal", "DateTime"):
        if match := _ANY_YEAR_RE.search(metadata_value(metadata, key)):
            return int(match.group(1))
    return None


def is_usable(page: Page, info: dict[str, Any]) -> bool:
    """Apply the hard rules: a recent, large-enough photograph of a city outdoors."""
    width = int(info.get("width") or 0)
    height = int(info.get("height") or 0)
    mime = str(info.get("mime") or "").lower()
    # Commons hosts videos and SVGs too, and geosearch returns them like the rest.
    if not width or not height or not mime.startswith("image/") or mime in _UNUSABLE_MIMES:
        return False

    subject = subject_text(page, info)
    if any(has_term(subject, term) for term in REJECT_TERMS) or _ORBITAL_RE.search(subject):
        return False
    year = capture_year(page, info)
    if year is not None and year < OLDEST_ACCEPTABLE_YEAR:
        return False

    ratio = width / height
    return (
        ratio >= MIN_RATIO
        and usable_width(width, height) >= MIN_WIDTH
        and width * height >= MIN_PIXELS
    )


def usable_width(width: int, height: int) -> float:
    """Width surviving the centred 16:9 crop, which is what actually matters.

    A 5061x1200 panorama leaves a 2133 px band, where a 7585x2217 keeps 3941.
    """
    return min(width, height * TARGET_RATIO)


def score(page: Page, source: str = "search", item: Capital | None = None) -> float | None:
    """Rank a candidate, or return None when a hard rule disqualifies it."""
    info = image_info(page)
    if info is None or not is_usable(page, info):
        return None

    width = int(info["width"])
    height = int(info["height"])
    ratio = width / height
    kept_width = usable_width(width, height)
    subject = subject_text(page, info)

    total = float(SOURCE_BONUS.get(source, 0))

    awards = sum(ASSESSMENT_BONUS.get(award, 0) for award in assessments(info))
    total += min(awards, MAX_ASSESSMENT_BONUS)
    if "featured desktop backgrounds" in subject:
        total += 100

    # Framing: reward proximity to 16:9, penalise friezes.
    total += max(0.0, 30 - abs(ratio - TARGET_RATIO) * 26)
    if ratio > MAX_COMFORTABLE_RATIO:
        total -= (ratio - MAX_COMFORTABLE_RATIO) * 25

    # Resolution: below a common wallpaper width the image will be upscaled.
    total += min(kept_width / 300, 20)
    if kept_width < 2560:
        total -= 25

    # Iconicity: how many Wikimedia pages reuse the image.
    total += min(len(page.get("globalusage") or []), 10) * 7

    total += sum(bonus for term, bonus in PREFERRED_TERMS.items() if has_term(subject, term))

    # The city name in the title separates "Belmopan Sunset" from an "Aerials
    # Belize WHwy 02" filed in the same category. The bonus is deliberately heavy
    # but stays a bonus: a Cyrillic title, or the name of a well-known monument,
    # must not eliminate a good photograph.
    if item is not None:
        title = str(page.get("title", "")).lower()
        names = {item.capital.casefold(), item.search_capital.casefold()}
        if any(mentions_place(title, name) for name in names):
            total += 55

    # Panoramio uploads are old and often low-resolution.
    if "panoramio" in subject:
        total -= 8
    return total
