"""Where the lock screen keeps its runtime state.

Everything lives under `%LOCALAPPDATA%\\WallPaperCapital\\`, outside the repository:
writing a fresh image into `data/` at every logon would trigger a pointless
OneDrive resync.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

APP_DIR = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
APP_DIR /= "WallPaperCapital"

DEFAULT_OUTPUT = APP_DIR / "lockscreen.jpg"
STATE_FILE = APP_DIR / "state.json"
LOG_FILE = APP_DIR / "lockscreen.log"

# Recent draws excluded from the next pick, and how much history is kept.
HISTORY_EXCLUDE = 20
HISTORY_MAX = 40


def load(path: Path = STATE_FILE) -> dict[str, Any]:
    """Read the state file, starting from scratch if it is missing or broken."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, json.JSONDecodeError) as exc:
        log.warning("State unreadable, starting over (%s)", exc)
        return {}
    return data if isinstance(data, dict) else {}


def save(state: dict[str, Any], path: Path = STATE_FILE) -> None:
    """Write the state atomically, so a truncated JSON is never left behind."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(path)
    except OSError as exc:
        log.warning("Could not save the state (%s)", exc)


def history_of(state: dict[str, Any]) -> list[str]:
    """The recorded draws, ignoring anything that is not a filename stem."""
    return [item for item in state.get("history", []) if isinstance(item, str)]


def remember_draw(state: dict[str, Any], stem: str) -> None:
    """Append a draw, keeping the history bounded."""
    state["history"] = [*history_of(state), stem][-HISTORY_MAX:]
