"""Text helpers shared by the download and lock screen commands."""

from __future__ import annotations

import html
import re
import unicodedata

# "Mt Wellington" is not Wellington and "Lake Victoria" is not Victoria: a city
# name introduced by one of these words denotes a different place entirely.
QUALIFIER_PREFIXES = frozenset(
    {"mount", "mt", "mt.", "lake", "port", "fort", "cape", "gulf", "bay", "river"}
)

_TAG_RE = re.compile(r"<[^>]+>")
_WHITESPACE_RE = re.compile(r"\s+")
_NON_SLUG_RE = re.compile(r"[^a-z0-9]+")


def strip_html(value: str | None) -> str:
    """Reduce a Commons metadata fragment to plain, single-spaced text."""
    if not value:
        return ""
    text = _TAG_RE.sub(" ", value)
    return _WHITESPACE_RE.sub(" ", html.unescape(text)).strip()


def slugify(value: str) -> str:
    """Turn a label into a lowercase ASCII filename stem: "Le Caire" -> "le_caire"."""
    folded = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii")
    return _NON_SLUG_RE.sub("_", folded.lower()).strip("_") or "image"


def capitalize_first(value: str) -> str:
    """Uppercase the first letter and leave the rest untouched.

    Wikidata returns a few all-lowercase labels ("république démocratique du Congo").
    `str.title` would break the particles and `str.capitalize` would lowercase the rest.
    """
    return value[:1].upper() + value[1:] if value else value


def has_term(text: str, term: str) -> bool:
    """Match a whole word, so that "plan" does not fire on "planetarium"."""
    return re.search(rf"\b{re.escape(term)}\b", text) is not None


def mentions_place(text: str, name: str) -> bool:
    """True when the place is named for itself, not through "Mount X" or "Lake X"."""
    for match in re.finditer(rf"\b{re.escape(name)}\b", text):
        head = text[: match.start()].rstrip().rsplit(" ", 1)
        previous = head[-1].strip(",.-\"'()") if head and head[-1] else ""
        if previous not in QUALIFIER_PREFIXES:
            return True
    return False
