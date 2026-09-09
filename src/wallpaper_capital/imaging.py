"""Cropping and encoding, shared by the downloader and the lock screen renderer."""

from __future__ import annotations

from io import BytesIO
from pathlib import Path

from PIL import Image, ImageOps

TARGET_RATIO = 16 / 9
JPEG_QUALITY = 92

# Large Commons panoramas exceed Pillow's default decompression-bomb ceiling. The
# source is trusted, so the ceiling is raised rather than the files skipped.
Image.MAX_IMAGE_PIXELS = 300_000_000

_MIME_EXTENSIONS = {
    "image/jpeg": ".jpg",
    "image/jpg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/avif": ".avif",
    "image/bmp": ".bmp",
    "image/tiff": ".tif",
}


def extension_for_mime(mime: str) -> str:
    """File extension matching a Commons MIME type."""
    return _MIME_EXTENSIONS.get(mime.lower(), ".img")


def target_size(width: int) -> tuple[int, int]:
    """The 16:9 size for a given width."""
    return width, round(width / TARGET_RATIO)


def fit(image: Image.Image, size: tuple[int, int]) -> Image.Image:
    """Centre-crop and resample an image to exactly `size`, honouring EXIF rotation."""
    oriented = ImageOps.exif_transpose(image) or image
    return ImageOps.fit(
        oriented.convert("RGB"),
        size,
        method=Image.Resampling.LANCZOS,
        centering=(0.5, 0.5),
    )


def save_jpeg(image: Image.Image, output_path: Path) -> None:
    """Write a JPEG in one move, so no reader ever sees a half-written file."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = output_path.with_suffix(output_path.suffix + ".tmp")
    image.save(temporary, format="JPEG", quality=JPEG_QUALITY, optimize=True)
    temporary.replace(output_path)


def save_as_wallpaper(raw_bytes: bytes, output_path: Path, width: int) -> None:
    """Crop downloaded bytes to 16:9 at `width` and store them as JPEG."""
    with Image.open(BytesIO(raw_bytes)) as image:
        save_jpeg(fit(image, target_size(width)), output_path)
