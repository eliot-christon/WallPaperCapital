"""Matching a wallpaper file back to the capital and country it shows."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path

from wallpaper_capital.models import Capital
from wallpaper_capital.text import capitalize_first, slugify
from wallpaper_capital.wikidata import EXTRA_STATES

log = logging.getLogger(__name__)

SEPARATOR = " · "


@dataclass(frozen=True)
class Labels:
    """The text to burn into one image."""

    capital: str
    country: str
    capital_en: str = ""
    country_en: str = ""
    country_code: str = ""

    @classmethod
    def from_capital(cls, item: Capital) -> Labels:
        return cls(
            capital=item.capital,
            country=capitalize_first(item.country),
            capital_en=item.capital_en,
            country_en=item.country_en,
            country_code=item.country_code,
        )

    def lines(self) -> tuple[str, str | None]:
        """The main line and, when it adds anything, the secondary one.

        "BUDAPEST · Hongrie" then "Hungary": the English labels only show up when
        they differ from the French ones.
        """
        primary = f"{self.capital.upper()}{SEPARATOR}{self.country}"
        pairs = ((self.capital_en, self.capital), (self.country_en, self.country))
        extras = [
            english
            for english, french in pairs
            if english and english.strip().casefold() != french.strip().casefold()
        ]
        return primary, SEPARATOR.join(extras) or None


def build_index(cache_file: Path) -> dict[str, Labels]:
    """Map each filename stem to the labels of its capital.

    The key is rebuilt with the same `slugify` that named the files, which keeps
    the index correct even when two countries share a capital name.
    """
    try:
        entries = json.loads(cache_file.read_text(encoding="utf-8"))
    except FileNotFoundError:
        log.error("Capitals cache not found: %s", cache_file)
        return {}
    except (OSError, json.JSONDecodeError) as exc:
        log.error("Capitals cache unreadable (%s)", exc)
        return {}

    index: dict[str, Labels] = {}
    for entry in entries if isinstance(entries, list) else []:
        if not isinstance(entry, dict):
            continue
        capital = str(entry.get("capital") or "").strip()
        country = str(entry.get("country") or "").strip()
        code = str(entry.get("country_code") or "").strip().upper()
        if not capital or not country or not code:
            continue
        index[f"{slugify(capital)}_{slugify(code)}"] = Labels(
            capital=capital,
            country=capitalize_first(country),
            capital_en=str(entry.get("capital_en") or "").strip(),
            country_en=str(entry.get("country_en") or "").strip(),
            country_code=code,
        )

    # Same fix as the downloader: Denmark is missing from the SPARQL query, yet
    # its image exists in the library.
    for extra in EXTRA_STATES:
        index.setdefault(extra.file_stem, Labels.from_capital(extra))

    log.debug("%d entries indexed from %s", len(index), cache_file.name)
    return index


def labels_for(stem: str, index: dict[str, Labels]) -> Labels:
    """Labels for one file, falling back to the filename when the cache misses it."""
    if (known := index.get(stem)) is not None:
        return known

    # Fallback: names follow `<capital>_<iso3>`, so the ISO-3 is the last segment.
    capital_slug, _, code_slug = stem.rpartition("_")
    code = code_slug.upper()
    country = next(
        (item.country for item in index.values() if item.country_code == code), code or "?"
    )
    log.warning("No cache entry for %r, labels inferred from the filename", stem)
    return Labels(
        capital=capital_slug.replace("_", " ").title() or stem,
        country=country,
        country_code=code,
    )
