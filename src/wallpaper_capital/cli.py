"""Command line interface: `wpcapital download` and `wpcapital lockscreen`."""

from __future__ import annotations

import argparse
import logging
import sys
from collections.abc import Callable
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import TYPE_CHECKING

from wallpaper_capital import __version__, overrides
from wallpaper_capital.api import ApiError, create_session
from wallpaper_capital.commons import CommonsClient
from wallpaper_capital.download import DownloadOptions, download_all
from wallpaper_capital.lockscreen import state
from wallpaper_capital.lockscreen.app import LockScreenOptions, restore
from wallpaper_capital.lockscreen.app import run as run_lockscreen
from wallpaper_capital.lockscreen.render import DEFAULT_MARGIN_X, DEFAULT_MARGIN_Y
from wallpaper_capital.lockscreen.windows import disable_spotlight, spotlight_enabled
from wallpaper_capital.models import Capital
from wallpaper_capital.wikidata import load_capitals

DEFAULT_OUTPUT_DIR = Path("data/wallpaper")
DEFAULT_CACHE_FILE = Path("data/capitals_wikidata.json")
DEFAULT_OVERRIDES = Path("data/overrides.json")
DEFAULT_MANUAL_DIR = Path("data/manual")

MIN_WIDTH = 640

log = logging.getLogger("wallpaper_capital")


def configure_logging(verbose: bool = False, log_file: Path | None = None) -> None:
    """Log to stderr, and to a file when asked — at logon there is no console."""
    root = logging.getLogger("wallpaper_capital")
    root.setLevel(logging.DEBUG if verbose else logging.INFO)
    formatter = logging.Formatter("%(asctime)s | %(levelname)-7s | %(message)s", "%H:%M:%S")

    stream = logging.StreamHandler(sys.stderr)
    stream.setFormatter(formatter)
    root.addHandler(stream)

    if log_file is None:
        return
    try:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        handler = RotatingFileHandler(
            log_file, maxBytes=256 * 1024, backupCount=1, encoding="utf-8"
        )
        handler.setFormatter(formatter)
        root.addHandler(handler)
    except OSError as exc:  # pragma: no cover - depends on the filesystem
        root.warning("File logging unavailable (%s)", exc)


def select_capitals(capitals: list[Capital], args: argparse.Namespace) -> list[Capital]:
    """Apply the --capital, --country-code and --limit filters, in that order."""
    selected = capitals
    if args.capital:
        # Both spellings are accepted: Abou Dabi or Abu Dhabi.
        selected = [item for item in selected if item.matches_name(args.capital)]
    if args.country_code:
        code = args.country_code.upper()
        selected = [item for item in selected if item.country_code == code]
    if args.limit:
        selected = selected[: args.limit]
    return selected


def common_options() -> argparse.ArgumentParser:
    """Flags shared by every subcommand."""
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--verbose", action="store_true", help="Verbose logging")
    return parser


if TYPE_CHECKING:
    # Only subscriptable for type checkers; argparse exposes it unparameterised.
    SubParsers = argparse._SubParsersAction[argparse.ArgumentParser]
else:
    SubParsers = argparse._SubParsersAction


def add_download_parser(subparsers: SubParsers, common: argparse.ArgumentParser) -> None:
    parser = subparsers.add_parser(
        "download",
        parents=[common],
        help="Download one wallpaper per capital from Wikimedia Commons",
        description="Download one wallpaper per capital from Wikimedia Commons.",
    )
    parser.add_argument(
        "--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR, help="Output directory"
    )
    parser.add_argument(
        "--cache-file", type=Path, default=DEFAULT_CACHE_FILE, help="Capitals list cache"
    )
    parser.add_argument("--limit", type=int, help="Process at most N capitals, handy for testing")
    parser.add_argument("--capital", help="Process a single capital, e.g. Paris")
    parser.add_argument("--country-code", help="Process a single ISO alpha-3 code, e.g. FRA")
    parser.add_argument("--force", action="store_true", help="Replace files already present")
    parser.add_argument("--width", type=int, default=2560, help="Final width in pixels")
    parser.add_argument("--results", type=int, default=20, help="Commons results per query")
    parser.add_argument("--pause", type=float, default=0.6, help="Pause between requests, seconds")
    parser.add_argument(
        "--quality-only",
        action="store_true",
        help="Only accept images distinguished by the Commons community",
    )
    parser.add_argument(
        "--overrides",
        type=Path,
        default=DEFAULT_OVERRIDES,
        help="Hand-picked files, { capital: Commons file }",
    )
    parser.add_argument(
        "--keep-original-format",
        action="store_true",
        help="Keep the downloaded file as-is instead of converting to 16:9 JPEG",
    )


def add_lockscreen_parser(subparsers: SubParsers, common: argparse.ArgumentParser) -> None:
    parser = subparsers.add_parser(
        "lockscreen",
        parents=[common],
        help="Apply a random wallpaper, tagged capital/country, as the lock screen",
        description="Apply a random wallpaper, tagged capital/country, as the Windows lock screen.",
    )
    parser.add_argument(
        "--source-dir", type=Path, default=DEFAULT_OUTPUT_DIR, help="Wallpaper directory"
    )
    parser.add_argument(
        "--cache-file", type=Path, default=DEFAULT_CACHE_FILE, help="Cache providing the labels"
    )
    parser.add_argument(
        "--output", type=Path, default=state.DEFAULT_OUTPUT, help="Path of the tagged JPEG"
    )
    parser.add_argument(
        "--manual-dir",
        type=Path,
        default=DEFAULT_MANUAL_DIR,
        help="Hand-picked images, same naming as --source-dir, override the draw",
    )
    parser.add_argument(
        "--file",
        type=Path,
        help="Apply this exact image as the lock screen, skipping the random draw",
    )
    parser.add_argument("--capital", help="Force a capital, to preview the rendering")
    parser.add_argument("--country-code", help="Force a country by ISO alpha-3 code")
    parser.add_argument(
        "--dry-run", action="store_true", help="Render the image without touching the lock screen"
    )
    parser.add_argument(
        "--margin-x", type=float, default=DEFAULT_MARGIN_X, help="Right margin, fraction of width"
    )
    parser.add_argument(
        "--margin-y", type=float, default=DEFAULT_MARGIN_Y, help="Bottom margin, fraction of height"
    )
    parser.add_argument("--no-history", action="store_true", help="Do not record this draw")
    parser.add_argument(
        "--restore", action="store_true", help="Restore the original lock screen and exit"
    )
    parser.add_argument(
        "--disable-spotlight",
        action="store_true",
        help="Disable Windows Spotlight (HKCU), then continue",
    )


def run_download(args: argparse.Namespace) -> int:
    if args.width < MIN_WIDTH:
        log.error("--width must be at least %d", MIN_WIDTH)
        return 1

    session = create_session()
    capitals = select_capitals(load_capitals(session, args.cache_file), args)
    if not capitals:
        log.error("No capital matches the filters.")
        return 1

    override_map = overrides.load(args.overrides)
    if override_map:
        log.info("%d override(s) loaded from %s", len(override_map), args.overrides)

    options = DownloadOptions(
        output_dir=args.output_dir,
        width=args.width,
        result_limit=args.results,
        force=args.force,
        convert_to_jpeg=not args.keep_original_format,
        quality_only=args.quality_only,
    )
    client = CommonsClient(session, pause=args.pause, thumb_width=options.thumb_width)
    download_all(session, client, capitals, options, override_map)
    return 0


def run_lockscreen_command(args: argparse.Namespace) -> int:
    if args.restore:
        return restore(state.load())

    if args.disable_spotlight:
        disable_spotlight()
    elif spotlight_enabled():
        log.warning(
            "Windows Spotlight is on and will replace the image: re-run with --disable-spotlight"
        )

    return run_lockscreen(
        LockScreenOptions(
            source_dir=args.source_dir,
            cache_file=args.cache_file,
            output=args.output,
            manual_dir=args.manual_dir,
            file=args.file,
            capital=args.capital,
            country_code=args.country_code,
            margin_x=args.margin_x,
            margin_y=args.margin_y,
            dry_run=args.dry_run,
            record_history=not args.no_history,
        )
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="wpcapital",
        description="One wallpaper per capital city, sourced from Wikimedia Commons.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    common = common_options()
    subparsers = parser.add_subparsers(dest="command", required=True)
    add_download_parser(subparsers, common)
    add_lockscreen_parser(subparsers, common)
    return parser


HANDLERS: dict[str, Callable[[argparse.Namespace], int]] = {
    "download": run_download,
    "lockscreen": run_lockscreen_command,
}


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    # The lock screen runs from a scheduled task with no console, so it also logs
    # to a file; the downloader is always run by hand.
    log_file = state.LOG_FILE if args.command == "lockscreen" else None
    configure_logging(args.verbose, log_file)

    try:
        return HANDLERS[args.command](args)
    except ApiError as exc:
        log.error("%s", exc)
        return 1
    except KeyboardInterrupt:
        log.warning("Interrupted by the user")
        return 130
