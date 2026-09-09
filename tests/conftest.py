"""Builders for the Commons page shapes the tests exercise."""

from __future__ import annotations

from typing import Any

import pytest

from wallpaper_capital.models import Capital


def extmetadata(**fields: str) -> dict[str, dict[str, str]]:
    """Wrap plain values in the `{"value": ...}` shape Commons uses."""
    return {key: {"value": value} for key, value in fields.items()}


def make_page(
    title: str = "File:Nice view.jpg",
    *,
    width: int = 4000,
    height: int = 2250,
    mime: str = "image/jpeg",
    categories: str = "",
    assessments: str = "",
    date: str = "",
    usages: int = 0,
) -> dict[str, Any]:
    """A Commons query page carrying one usable `imageinfo` block."""
    metadata = extmetadata(Categories=categories, Assessments=assessments)
    if date:
        metadata.update(extmetadata(DateTimeOriginal=date))
    return {
        "title": title,
        "globalusage": [{"url": f"https://example.org/{index}"} for index in range(usages)],
        "imageinfo": [
            {
                "url": f"https://upload.example.org/{title}",
                "width": width,
                "height": height,
                "mime": mime,
                "extmetadata": metadata,
            }
        ],
    }


@pytest.fixture
def paris() -> Capital:
    return Capital(
        country="France",
        country_code="FRA",
        capital="Paris",
        country_en="France",
        capital_en="Paris",
        commons_category="Paris",
        latitude=48.8566,
        longitude=2.3522,
    )


@pytest.fixture
def cairo() -> Capital:
    """A capital whose French and English names differ."""
    return Capital(
        country="Égypte",
        country_code="EGY",
        capital="Le Caire",
        country_en="Egypt",
        capital_en="Cairo",
        commons_category="Cairo",
    )
