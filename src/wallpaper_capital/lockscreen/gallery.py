"""Listing, filtering and drawing from the downloaded wallpapers."""

from __future__ import annotations

import logging
import random
from pathlib import Path

from wallpaper_capital.lockscreen.state import HISTORY_EXCLUDE
from wallpaper_capital.text import slugify

log = logging.getLogger(__name__)


def list_images(source_dir: Path) -> list[Path]:
    """Every wallpaper in the library, in a stable order."""
    images = sorted(path for path in source_dir.glob("*.jpg") if path.is_file())
    log.debug("%d image(s) in %s", len(images), source_dir)
    return images


def filter_images(
    images: list[Path], capital: str | None = None, country_code: str | None = None
) -> list[Path]:
    """Narrow the selection to one capital or one country, handy for testing."""
    selected = images
    if country_code:
        suffix = f"_{slugify(country_code)}"
        selected = [path for path in selected if path.stem.endswith(suffix)]
    if capital:
        prefix = f"{slugify(capital)}_"
        selected = [path for path in selected if path.stem.startswith(prefix)]
    return selected


def pick(images: list[Path], history: list[str]) -> Path:
    """Draw at random, avoiding the most recent displays.

    Twenty recent draws are excluded so the same capital does not come back two
    days running. If the selection is smaller than that window, everything is
    back in play rather than nothing.
    """
    recent = set(history[-HISTORY_EXCLUDE:])
    pool = [path for path in images if path.stem not in recent] or images
    return random.choice(pool)
