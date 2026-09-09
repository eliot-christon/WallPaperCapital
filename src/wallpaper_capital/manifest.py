"""The manifest: one record per downloaded wallpaper, with credits and licence."""

from __future__ import annotations

import json
import logging
from dataclasses import asdict
from pathlib import Path
from typing import Any

from wallpaper_capital.models import WallpaperRecord

log = logging.getLogger(__name__)

FILENAME = "manifest.json"

#: Manifest entries indexed by (country code, capital).
Entries = dict[tuple[str, str], dict[str, Any]]


def display_path(path: Path) -> str:
    """A readable path for the manifest, relative to the project when possible."""
    try:
        return path.resolve().relative_to(Path.cwd().resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def load(manifest_path: Path) -> Entries:
    """Index an existing manifest, tolerating a missing or corrupted file."""
    if not manifest_path.exists():
        return {}
    try:
        entries = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        log.warning("Manifest unreadable, it will be recreated: %s", manifest_path)
        return {}
    if not isinstance(entries, list):
        return {}
    return {
        (str(entry.get("country_code", "")), str(entry.get("capital", ""))): entry
        for entry in entries
        if isinstance(entry, dict)
    }


def add(entries: Entries, record: WallpaperRecord) -> None:
    """Insert or replace the record for a capital."""
    entries[(record.country_code, record.capital)] = asdict(record)


def write(manifest_path: Path, entries: Entries) -> None:
    """Write the manifest sorted by capital, atomically.

    The manifest is rewritten after every download; a sync client such as OneDrive
    that observed a truncated file could restore it to its previous state. A
    single-operation replace always shows it a complete file.
    """
    ordered = sorted(
        entries.values(),
        key=lambda entry: (str(entry.get("capital", "")), str(entry.get("country", ""))),
    )
    temporary = manifest_path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(ordered, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(manifest_path)
