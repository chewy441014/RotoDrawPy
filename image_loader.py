"""RotoDraw stage 1: decode an image and create an sRGB RGBA working copy.

No background removal, palette reduction, or stencil geometry happens here.
"""

from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
import json
import os
from pathlib import Path
import tempfile
import warnings

from PIL import Image, ImageCms, ImageOps, UnidentifiedImageError
from PIL.PngImagePlugin import PngInfo


MAX_SIDE = 2048
MAX_SOURCE_PIXELS = 80_000_000
SUPPORTED_FORMATS = frozenset({"PNG", "JPEG", "WEBP", "BMP", "TIFF", "GIF"})


class ImageLoadError(ValueError):
    """An image cannot be normalized; the message can be shown to a user."""


@dataclass(frozen=True)
class NormalizedImage:
    source_path: Path
    source_format: str
    source_mode: str
    source_size: tuple[int, int]
    oriented_size: tuple[int, int]
    image: Image.Image
    icc_profile: bytes
    orientation_corrected: bool
    frame_count: int
    color_handling: str
    notes: tuple[str, ...]

    @property
    def resized(self) -> bool:
        return self.image.size != self.oriented_size

    @property
    def has_transparency(self) -> bool:
        return self.image.getchannel("A").getextrema()[0] < 255


def _convert_to_srgb(
    image: Image.Image, embedded_profile: bytes | None
) -> tuple[Image.Image, bytes, str]:
    """Convert color independently of alpha, including indexed transparency."""
    srgb = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB"))
    alpha = None
    expanded = None
    if "A" in image.getbands() or "transparency" in image.info:
        expanded = image.convert("RGBA")
        alpha = expanded.getchannel("A")

    # Retain native CMS-supported color channels until the ICC transformation.
    # In particular, converting CMYK or LAB to RGB first loses profile meaning.
    if image.mode in {"RGB", "CMYK", "LAB", "L"}:
        color = image
    elif image.mode == "LA":
        color = image.getchannel("L")
    else:
        color = (expanded if expanded is not None else image).convert("RGB")

    if embedded_profile:
        try:
            source_profile = ImageCms.ImageCmsProfile(BytesIO(embedded_profile))
            rgb = ImageCms.profileToProfile(
                color,
                source_profile,
                srgb,
                outputMode="RGB",
                renderingIntent=ImageCms.Intent.RELATIVE_COLORIMETRIC,
            )
        except (ImageCms.PyCMSError, OSError, ValueError, TypeError) as exc:
            raise ImageLoadError(
                "The embedded color profile is invalid or incompatible with the image. "
                "Export the artwork as an sRGB PNG or JPEG and try again."
            ) from exc
        handling = "Embedded color profile converted to sRGB."
    elif color.mode == "LAB":
        raise ImageLoadError(
            "This LAB image has no color profile. Export it as an sRGB PNG or JPEG."
        )
    else:
        rgb = color.convert("RGB")
        handling = (
            "Untagged CMYK converted to RGB; colors are approximate."
            if color.mode == "CMYK"
            else "No embedded profile; color values assumed to be sRGB."
        )

    rgba = rgb.convert("RGBA")
    if alpha is not None:
        rgba.putalpha(alpha)
    return rgba, srgb.tobytes(), handling


def load_and_normalize(
    filename: str | Path,
    *,
    max_side: int = MAX_SIDE,
    max_source_pixels: int = MAX_SOURCE_PIXELS,
) -> NormalizedImage:
    """Return a detached 8-bit RGBA image; never modify the source file.

    Apply EXIF orientation, transform a valid ICC profile to sRGB, preserve
    alpha, and proportionally downscale using Lanczos. Small images are not
    enlarged. The first frame/page is used for multi-frame inputs, with a note.
    """
    if type(max_side) is not int or max_side < 1:
        raise ValueError("The maximum image side must be a positive integer.")
    if type(max_source_pixels) is not int or max_source_pixels < 1:
        raise ValueError("The source-pixel limit must be a positive integer.")

    path = Path(filename).expanduser().resolve()
    if not path.is_file():
        raise ImageLoadError("Choose an existing image file, rather than a folder.")

    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(path) as source:
                source_format = source.format or "Unknown"
                if source_format not in SUPPORTED_FORMATS:
                    raise ImageLoadError(
                        "Choose a PNG, JPEG, WebP, BMP, TIFF, or GIF image. "
                        "Export vector artwork as PNG first."
                    )
                source_size = source.size
                if source.width * source.height > max_source_pixels:
                    raise ImageLoadError(
                        f"The source image exceeds {max_source_pixels:,} pixels. "
                        "Resize it before importing."
                    )
                source_mode = source.mode
                if source_mode.startswith("I") or source_mode == "F":
                    raise ImageLoadError(
                        "This version accepts 8-bit color or grayscale images. "
                        "Export the artwork as an 8-bit PNG or JPEG."
                    )
                frame_count = getattr(source, "n_frames", 1)
                embedded_profile = source.info.get("icc_profile")
                orientation = source.getexif().get(274, 1)
                source.load()
                oriented = ImageOps.exif_transpose(source)
                oriented_size = oriented.size
                rgba, icc_bytes, handling = _convert_to_srgb(oriented, embedded_profile)

        rgba.thumbnail((max_side, max_side), Image.Resampling.LANCZOS)
        # Stale source EXIF must not rotate an already-oriented exported image.
        rgba.info.clear()
        rgba.info["icc_profile"] = icc_bytes
        notes = []
        if frame_count > 1:
            notes.append(f"Only frame/page 1 of {frame_count} was imported.")
        if source_mode == "CMYK" and not embedded_profile:
            notes.append("For accurate CMYK colors, use an image with an embedded profile.")

        return NormalizedImage(
            source_path=path,
            source_format=source_format,
            source_mode=source_mode,
            source_size=source_size,
            oriented_size=oriented_size,
            image=rgba,
            icc_profile=icc_bytes,
            orientation_corrected=orientation in {2, 3, 4, 5, 6, 7, 8},
            frame_count=frame_count,
            color_handling=handling,
            notes=tuple(notes),
        )
    except ImageLoadError:
        raise
    except (Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
        raise ImageLoadError("This source image is too large. Resize it before importing.") from exc
    except UnidentifiedImageError as exc:
        raise ImageLoadError("The file is not a supported image, or its contents are damaged.") from exc
    except (OSError, ValueError, SyntaxError, EOFError) as exc:
        raise ImageLoadError(f"The image could not be read: {exc}") from exc


def save_normalized_png(
    document: NormalizedImage, filename: str | Path, *, color_limit: int = 30
) -> Path:
    """Atomically save the working image and its requested future color limit.

    The color limit is recorded, not applied: palette extraction is a later
    stage. Saving back onto the input path is explicitly rejected.
    """
    if type(color_limit) is not int or not 1 <= color_limit <= 30:
        raise ValueError("Choose a whole-number pencil limit from 1 to 30.")
    target = Path(filename).expanduser().resolve()
    if target == document.source_path:
        raise ValueError("Save a new file; the original image cannot be overwritten.")
    if target.suffix.lower() != ".png":
        raise ValueError("Choose a filename ending in .png.")

    settings = {
        "schema_version": 1,
        "color_limit": color_limit,
        "working_size": list(document.image.size),
        "source_size": list(document.source_size),
        "oriented_size": list(document.oriented_size),
        "color_space": "sRGB",
        "stage": "image_normalization",
    }
    info = PngInfo()
    info.add_text("rotodraw.settings", json.dumps(settings))
    temporary_path = None
    try:
        fd, temporary_name = tempfile.mkstemp(
            dir=target.parent, prefix=f".{target.stem}-", suffix=".png"
        )
        os.close(fd)
        temporary_path = Path(temporary_name)
        document.image.save(
            temporary_path,
            format="PNG",
            icc_profile=document.icc_profile,
            pnginfo=info,
        )
        os.replace(temporary_path, target)
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
    return target
