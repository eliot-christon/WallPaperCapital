"""The list of sovereign states and their capitals, read from Wikidata.

Wikidata is queried once and cached on disk, so a run can proceed while the SPARQL
endpoint is busy — which it regularly is.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Iterable
from pathlib import Path
from typing import Any
from urllib.parse import unquote

import requests

from wallpaper_capital.api import ApiError
from wallpaper_capital.models import Capital

log = logging.getLogger(__name__)

SPARQL_URL = "https://query.wikidata.org/sparql"

EXPECTED_STATE_COUNT = 195  # 193 UN members + the Holy See + the State of Palestine

# Q1065 = United Nations, Q237 = Holy See, Q219060 = State of Palestine.
# P463 = member of, P298 = ISO 3166-1 alpha-3, P36 = capital, P582 = end time.
# Filtering on P298 drops historical states (Kingdom of Nepal, Republic of
# Afghanistan and friends) that still carry a UN membership with no end date.
# P625 = coordinates, P373 = Commons category, P18 = image: these three anchor the
# search on the city itself rather than on a string of characters.
CAPITALS_QUERY = """
SELECT ?countryLabel ?code ?capitalLabel ?countryEn ?capitalEn
       ?coord ?commonscat ?image WHERE {
  {
    ?country p:P463 ?membership .
    ?membership ps:P463 wd:Q1065 .
    FILTER NOT EXISTS { ?membership pq:P582 ?endTime }
  } UNION {
    VALUES ?country { wd:Q237 wd:Q219060 }
  }
  ?country wdt:P298 ?code .
  ?country wdt:P36 ?capital .
  OPTIONAL { ?capital wdt:P625 ?coord }
  OPTIONAL { ?capital wdt:P373 ?commonscat }
  OPTIONAL { ?capital wdt:P18 ?image }
  OPTIONAL { ?country rdfs:label ?countryEn . FILTER(LANG(?countryEn) = "en") }
  OPTIONAL { ?capital rdfs:label ?capitalEn . FILTER(LANG(?capitalEn) = "en") }
  SERVICE wikibase:label { bd:serviceParam wikibase:language "fr,en". }
}
"""

# Wikidata attaches Denmark's UN membership to "Kingdom of Denmark" (Q756617), an
# entity with no ISO alpha-3 code, while the DNK code and the capital hang off
# "Denmark" (Q35). The query cannot bridge the two, so the state is added by hand.
EXTRA_STATES: tuple[Capital, ...] = (
    Capital(
        country="Danemark",
        country_code="DNK",
        capital="Copenhague",
        country_en="Denmark",
        capital_en="Copenhagen",
        commons_category="Copenhagen",
        latitude=55.6761,
        longitude=12.5683,
    ),
)

_QID_RE = re.compile(r"Q\d+")
_POINT_RE = re.compile(r"\s*Point\(\s*(-?[\d.]+)\s+(-?[\d.]+)\s*\)\s*")


def parse_point(literal: str) -> tuple[float | None, float | None]:
    """Decode `Point(longitude latitude)`, the shape Wikidata gives coordinates."""
    match = _POINT_RE.fullmatch(literal or "")
    if not match:
        return None, None
    return float(match.group(2)), float(match.group(1))


def file_title_from_url(url: str) -> str:
    """Turn `.../Special:FilePath/Name.jpg` into `File:Name.jpg`."""
    if not url:
        return ""
    name = unquote(url.rsplit("/", 1)[-1]).replace("_", " ").strip()
    return f"File:{name}" if name else ""


def parse_capitals(entries: Iterable[dict[str, Any]]) -> list[Capital]:
    """Turn Wikidata rows, or cached rows, into `Capital` objects."""
    capitals: dict[tuple[str, str], Capital] = {}
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        code = str(entry.get("country_code") or "").upper()
        country = str(entry.get("country") or "").strip()
        capital = str(entry.get("capital") or "").strip()
        if not code or not country or not capital:
            continue
        # Wikidata falls back to the raw Qxx identifier when no label exists.
        if _QID_RE.fullmatch(country) or _QID_RE.fullmatch(capital):
            continue
        latitude = entry.get("latitude")
        longitude = entry.get("longitude")
        capitals[(code, capital.casefold())] = Capital(
            country=country,
            country_code=code,
            capital=capital,
            country_en=str(entry.get("country_en") or "").strip(),
            capital_en=str(entry.get("capital_en") or "").strip(),
            commons_category=str(entry.get("commons_category") or "").strip(),
            latitude=float(latitude) if latitude is not None else None,
            longitude=float(longitude) if longitude is not None else None,
            wikidata_image=str(entry.get("wikidata_image") or "").strip(),
        )

    for extra in EXTRA_STATES:
        capitals.setdefault((extra.country_code, extra.capital.casefold()), extra)

    return sorted(
        capitals.values(),
        key=lambda item: (item.capital.casefold(), item.country.casefold()),
    )


def fetch_capitals(session: requests.Session) -> list[dict[str, Any]]:
    """Run the SPARQL query and return serialisable rows."""
    response = session.get(
        SPARQL_URL,
        params={"query": CAPITALS_QUERY, "format": "json"},
        headers={"Accept": "application/sparql-results+json"},
        timeout=90,
    )
    response.raise_for_status()
    bindings = response.json().get("results", {}).get("bindings", [])

    def value(binding: dict[str, Any], key: str) -> str:
        return str((binding.get(key) or {}).get("value", ""))

    entries: list[dict[str, Any]] = []
    for binding in bindings:
        latitude, longitude = parse_point(value(binding, "coord"))
        entries.append(
            {
                "country": value(binding, "countryLabel"),
                "country_code": value(binding, "code"),
                "capital": value(binding, "capitalLabel"),
                "country_en": value(binding, "countryEn"),
                "capital_en": value(binding, "capitalEn"),
                "commons_category": value(binding, "commonscat"),
                "latitude": latitude,
                "longitude": longitude,
                "wikidata_image": file_title_from_url(value(binding, "image")),
            }
        )
    if not entries:
        raise ApiError("Wikidata returned no usable rows.")
    return entries


def read_cache(cache_path: Path) -> list[dict[str, Any]]:
    """Read cached rows, refusing a file that is malformed rather than guessing."""
    try:
        data = json.loads(cache_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ApiError(f"Capitals cache unreadable: {cache_path}") from exc
    if not isinstance(data, list):
        raise ApiError(f"Capitals cache is not a JSON list: {cache_path}")
    return data


def load_capitals(session: requests.Session, cache_path: Path) -> list[Capital]:
    """Fetch the UN members plus observers, refreshing the on-disk cache."""
    try:
        entries = fetch_capitals(session)
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(json.dumps(entries, ensure_ascii=False, indent=2), encoding="utf-8")
        log.info("Capitals list fetched from Wikidata")
    except (requests.RequestException, ApiError, ValueError) as exc:
        if not cache_path.exists():
            raise ApiError("Cannot reach Wikidata and no cache exists.") from exc
        log.warning("Wikidata unavailable (%s), falling back to cache %s", exc, cache_path)
        entries = read_cache(cache_path)
        # A cache written before the geographic anchoring carries neither
        # coordinates nor Commons categories, leaving only the text search.
        if entries and not any(entry.get("commons_category") for entry in entries):
            log.warning(
                "Cache predates city anchoring: selection will be less reliable, "
                "re-run once Wikidata answers"
            )

    capitals = parse_capitals(entries)
    if not capitals:
        raise ApiError("The capitals list contains no usable entry.")

    states = {item.country_code for item in capitals}
    if len(states) != EXPECTED_STATE_COUNT:
        log.warning(
            "Found %d states instead of the expected %d; the Wikidata modelling may have changed",
            len(states),
            EXPECTED_STATE_COUNT,
        )
    log.info("%d states, %d capital(s) in total", len(states), len(capitals))
    return capitals
