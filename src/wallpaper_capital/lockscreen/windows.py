"""Windows-specific plumbing: the WinRT bridge and the Spotlight switch."""

from __future__ import annotations

import logging
import subprocess
import sys
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

# Shipped inside the package so it is found whether the project is installed or
# run from a checkout.
BRIDGE_SCRIPT = Path(__file__).resolve().parent / "Set-LockScreen.ps1"

SPOTLIGHT_KEY = r"SOFTWARE\Microsoft\Windows\CurrentVersion\ContentDeliveryManager"
SPOTLIGHT_VALUE = "RotatingLockScreenEnabled"


def _decode(raw: bytes) -> str:
    """PowerShell 5.1 writes in the console code page, so try a few encodings."""
    for encoding in ("utf-8", "cp1252", "latin-1"):
        try:
            return raw.decode(encoding).strip()
        except UnicodeDecodeError:
            continue
    return raw.decode("latin-1", errors="replace").strip()


def run_bridge(arguments: list[str]) -> tuple[int, str, str]:
    """Invoke the PowerShell bridge and return (exit code, stdout, stderr)."""
    if not BRIDGE_SCRIPT.is_file():
        log.error("PowerShell bridge not found: %s", BRIDGE_SCRIPT)
        return 1, "", "missing bridge"
    completed = subprocess.run(
        [
            # powershell.exe (5.1) is required: the WinRT projection relies on
            # System.Runtime.WindowsRuntime, which PowerShell 7 does not ship.
            "powershell.exe",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(BRIDGE_SCRIPT),
            *arguments,
        ],
        capture_output=True,
        check=False,
    )
    return completed.returncode, _decode(completed.stdout), _decode(completed.stderr)


def current_lockscreen() -> str | None:
    """Path of the lock screen image currently in place, if it can be read."""
    code, out, err = run_bridge(["-Query"])
    if code != 0:
        log.warning("Could not read the current lock screen (%s)", err)
        return None
    return out or None


def apply_lockscreen(image_path: Path) -> bool:
    """Set the lock screen image. False means Windows refused it."""
    code, _, err = run_bridge(["-ImagePath", str(image_path)])
    if code != 0:
        log.error("Failed to apply the lock screen: %s", err)
        return False
    return True


def spotlight_enabled() -> bool | None:
    """True when Windows Spotlight would overwrite the custom image.

    None means the question could not be answered: no registry value, or not
    running on Windows at all.
    """
    if sys.platform != "win32":  # pragma: no cover - not on Windows
        return None
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, SPOTLIGHT_KEY) as key:
            value, _ = winreg.QueryValueEx(key, SPOTLIGHT_VALUE)
    except OSError:
        return None
    return bool(value)


def disable_spotlight() -> bool:
    """Turn Spotlight off for the current user (HKCU, so no elevation needed)."""
    if sys.platform != "win32":  # pragma: no cover - not on Windows
        return False
    import winreg

    try:
        with winreg.CreateKeyEx(
            winreg.HKEY_CURRENT_USER, SPOTLIGHT_KEY, 0, winreg.KEY_SET_VALUE
        ) as key:
            winreg.SetValueEx(key, SPOTLIGHT_VALUE, 0, winreg.REG_DWORD, 0)
            winreg.SetValueEx(key, "RotatingLockScreenOverlayEnabled", 0, winreg.REG_DWORD, 0)
    except OSError as exc:
        log.error("Could not disable Spotlight (%s)", exc)
        return False
    log.info("Windows Spotlight disabled")
    return True


def remember_original(state: dict[str, Any], output: Path) -> None:
    """Record the pre-existing lock screen once, so `--restore` has a target."""
    if state.get("original_lockscreen"):
        return
    current = current_lockscreen()
    if not current:
        return
    # Never remember our own image, nor the internal copy Windows keeps.
    normalized = current.casefold()
    if "systemdata" in normalized or normalized == str(output).casefold():
        return
    state["original_lockscreen"] = current
    log.info("Original lock screen remembered: %s", current)
