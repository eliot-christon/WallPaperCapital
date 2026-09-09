"""Wiring the lock screen command together."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from wallpaper_capital.lockscreen import gallery, state, windows
from wallpaper_capital.lockscreen.labels import build_index, labels_for
from wallpaper_capital.lockscreen.render import DEFAULT_MARGIN_X, DEFAULT_MARGIN_Y, render

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class LockScreenOptions:
    """Everything the lock screen command needs to run."""

    source_dir: Path
    cache_file: Path
    output: Path
    capital: str | None = None
    country_code: str | None = None
    margin_x: float = DEFAULT_MARGIN_X
    margin_y: float = DEFAULT_MARGIN_Y
    dry_run: bool = False
    record_history: bool = True


def restore(current_state: dict[str, Any]) -> int:
    """Put the original lock screen back."""
    original = current_state.get("original_lockscreen")
    if not original:
        log.error("No original lock screen was ever remembered")
        return 1
    path = Path(str(original))
    if not path.is_file():
        log.error("The original image no longer exists: %s", path)
        return 1
    if not windows.apply_lockscreen(path):
        return 1
    log.info("Lock screen restored: %s", path)
    return 0


def run(options: LockScreenOptions) -> int:
    """Draw a wallpaper, tag it, and make it the lock screen. Returns an exit code."""
    if not options.source_dir.is_dir():
        log.error("Image directory not found: %s", options.source_dir)
        return 1

    images = gallery.list_images(options.source_dir)
    if not images:
        log.error("No image in %s — run `wpcapital download` first", options.source_dir)
        return 1

    candidates = gallery.filter_images(images, options.capital, options.country_code)
    if not candidates:
        log.error("No image matches the requested filters")
        return 1

    current_state = state.load()
    chosen = gallery.pick(candidates, state.history_of(current_state))
    labels = labels_for(chosen.stem, build_index(options.cache_file))

    try:
        render(chosen, labels, options.output, options.margin_x, options.margin_y)
    except (OSError, ValueError) as exc:
        log.error("Failed to render %s (%s)", chosen.name, exc)
        return 1

    primary, secondary = labels.lines()
    log.info("Selected: %s%s", primary, f" ({secondary})" if secondary else "")

    if options.dry_run:
        log.info("Dry run: lock screen untouched, image written to %s", options.output)
    else:
        windows.remember_original(current_state, options.output)
        if not windows.apply_lockscreen(options.output):
            return 1
        log.info("Lock screen applied")

    # A dry run displayed nothing, so it must not consume a capital: otherwise
    # tuning the margins by trial and error would drain the exclusion window.
    if options.record_history and not options.dry_run:
        state.remember_draw(current_state, chosen.stem)
    current_state["last_applied"] = {
        "stem": chosen.stem,
        "capital": labels.capital,
        "country": labels.country,
        "dry_run": options.dry_run,
    }
    state.save(current_state)
    return 0
