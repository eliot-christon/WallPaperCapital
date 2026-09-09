"""Hand-picked files, for the capitals Commons photographs badly.

No heuristic rescues a city nobody has photographed well. Rather than bending the
ranking for one special case, the choice is handed back to a human.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from wallpaper_capital.models import Capital

log = logging.getLogger(__name__)


def load(path: Path) -> dict[str, str]:
    """Read `{ "Bichkek": "File:A fine view.jpg" }`, keyed by casefolded name.

    A malformed file is reported and ignored: a typo in an optional convenience
    should not stop a two-hundred-city run.
    """
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        log.warning("Overrides file unreadable (%s): %s", path, exc)
        return {}
    if not isinstance(raw, dict):
        log.warning("The overrides file must be a JSON object: %s", path)
        return {}

    overrides: dict[str, str] = {}
    for key, value in raw.items():
        title = str(value or "").strip()
        if not title:
            continue
        # "A view.jpg" and "File:A view.jpg" name the same file.
        if not title.lower().startswith("file:"):
            title = f"File:{title}"
        overrides[str(key).strip().casefold()] = title
    return overrides


def for_capital(overrides: dict[str, str], item: Capital) -> str:
    """The override for this capital, by French name, English name or ISO-3 code."""
    for key in (item.capital, item.search_capital, item.country_code):
        if title := overrides.get(key.casefold()):
            return title
    return ""
