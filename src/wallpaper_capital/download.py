"""Downloading one wallpaper per capital, and recording what was taken."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import requests

from wallpaper_capital import manifest, overrides
from wallpaper_capital.commons import CommonsClient, assessments, image_info, metadata_value
from wallpaper_capital.imaging import extension_for_mime, save_as_wallpaper
from wallpaper_capital.models import Capital, WallpaperRecord
from wallpaper_capital.selection import Candidate, find_best_image

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class DownloadOptions:
    """Everything the downloader needs beyond the capital itself."""

    output_dir: Path
    width: int = 2560
    result_limit: int = 20
    force: bool = False
    convert_to_jpeg: bool = True
    quality_only: bool = False

    @property
    def thumb_width(self) -> int:
        """Thumbnail width to ask Commons for, given the output format."""
        return max(self.width, 640) if self.convert_to_jpeg else 1280


def existing_output(output_dir: Path, stem: str, convert_to_jpeg: bool) -> Path | None:
    """Find an already downloaded file, whatever its extension."""
    if convert_to_jpeg:
        candidate = output_dir / f"{stem}.jpg"
        return candidate if candidate.exists() else None
    matches = sorted(output_dir.glob(f"{stem}.*"))
    return matches[0] if matches else None


def build_record(
    item: Capital, candidate: Candidate, output_path: Path, fallback_url: str
) -> WallpaperRecord:
    """Describe a download well enough to check its licence later."""
    info = image_info(candidate.page) or {}
    metadata = info.get("extmetadata") or {}
    title = candidate.page.get("title", "")
    return WallpaperRecord(
        country=item.country,
        country_code=item.country_code,
        capital=item.capital,
        filename=manifest.display_path(output_path),
        search_query=item.city_category,
        source_url=str(info.get("url") or fallback_url),
        source_page=str(
            info.get("descriptionurl") or f"https://commons.wikimedia.org/wiki/{title}"
        ),
        author=metadata_value(metadata, "Artist"),
        license=metadata_value(metadata, "LicenseShortName"),
        license_url=metadata_value(metadata, "LicenseUrl"),
        assessment="|".join(sorted(assessments(info))),
        source_kind=candidate.source,
        original_width=int(info.get("width") or 0),
        original_height=int(info.get("height") or 0),
        downloaded_at=datetime.now(timezone.utc).isoformat(),
    )


def download_one(
    session: requests.Session,
    client: CommonsClient,
    item: Capital,
    options: DownloadOptions,
    override_title: str = "",
) -> WallpaperRecord | None:
    """Download the best image for one capital. None means nothing was written."""
    stem = item.file_stem
    already = existing_output(options.output_dir, stem, options.convert_to_jpeg)
    if already and not options.force:
        log.info("Already present, skipped: %s", already.name)
        return None

    candidate = find_best_image(
        client,
        item,
        result_limit=options.result_limit,
        quality_only=options.quality_only,
        override_title=override_title,
    )
    if not candidate:
        log.warning("No image retained for %s, %s", item.capital, item.country)
        return None

    info = image_info(candidate.page) or {}
    original_url = str(info.get("url") or "")

    # When converting, the thumbnail is enough and spares the original download.
    # Otherwise the source file is required, or the extension would lie about the
    # content: Commons re-encodes its thumbnails to JPEG or PNG.
    if options.convert_to_jpeg:
        image_url = str(info.get("thumburl") or original_url)
        output_path = options.output_dir / f"{stem}.jpg"
    else:
        image_url = original_url
        extension = extension_for_mime(str(info.get("mime") or ""))
        output_path = options.output_dir / f"{stem}{extension}"

    if not image_url:
        log.warning("No URL for %s", candidate.page.get("title"))
        return None
    if output_path.exists() and not options.force:
        log.info("Already present, skipped: %s", output_path.name)
        return None

    try:
        response = session.get(image_url, timeout=90, headers={"Accept": "image/*,*/*;q=0.8"})
        response.raise_for_status()
        options.output_dir.mkdir(parents=True, exist_ok=True)
        if options.convert_to_jpeg:
            save_as_wallpaper(response.content, output_path, options.width)
        else:
            output_path.write_bytes(response.content)
    except (requests.RequestException, OSError, ValueError) as exc:
        log.warning("Download failed for %s: %s", item.capital, exc)
        return None

    awards = assessments(info)
    log.info(
        "Downloaded: %s (%s, %s)",
        output_path.name,
        candidate.source,
        ", ".join(sorted(awards)) if awards else "no distinction",
    )
    return build_record(item, candidate, output_path, image_url)


def download_all(
    session: requests.Session,
    client: CommonsClient,
    capitals: list[Capital],
    options: DownloadOptions,
    override_map: dict[str, str],
) -> int:
    """Download every capital in turn, saving the manifest after each success."""
    options.output_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = options.output_dir / manifest.FILENAME
    entries = manifest.load(manifest_path)

    log.info("%d capital(s) to process", len(capitals))
    skipped = 0
    for index, item in enumerate(capitals, start=1):
        log.info("[%d/%d] %s, %s", index, len(capitals), item.capital, item.country)
        record = download_one(
            session, client, item, options, overrides.for_capital(override_map, item)
        )
        if record is None:
            skipped += 1
            continue
        manifest.add(entries, record)
        manifest.write(manifest_path, entries)

    log.info("Done. Manifest: %s", manifest_path)
    if skipped:
        log.info("%d capital(s) without a new image, already present or not found", skipped)
    return skipped
