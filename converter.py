"""
Image conversion + EXIF + resize/compress logic.

Pure in-memory: takes bytes, returns bytes. No disk I/O.
"""

from __future__ import annotations
import io
from typing import Any

from PIL import Image, ImageOps
from PIL.ExifTags import TAGS, GPSTAGS

# Register HEIC/HEIF decoder with Pillow
try:
    import pillow_heif
    pillow_heif.register_heif_opener()
    HEIF_OK = True
except ImportError:
    HEIF_OK = False


# ─── COMMONS RULES ────────────────────────────────────────────────

# Formats Commons accepts (file extension → friendly name)
COMMONS_ACCEPTED = {
    "jpg":  "JPEG",
    "jpeg": "JPEG",
    "png":  "PNG",
    "gif":  "GIF",
    "svg":  "SVG",
    "tif":  "TIFF",
    "tiff": "TIFF",
    "webp": "WebP",
    "xcf":  "XCF (GIMP)",
    "pdf":  "PDF",
    "djvu": "DjVu",
}

# Common formats Commons rejects
COMMONS_REJECTED = {
    "heic": "HEIC (Apple) — patent-encumbered, must convert",
    "heif": "HEIF — patent-encumbered, must convert",
    "bmp":  "BMP — convert to PNG instead",
    "raw":  "Camera RAW — convert to JPEG or TIFF",
    "cr2":  "Canon RAW — convert to JPEG or TIFF",
    "nef":  "Nikon RAW — convert to JPEG or TIFF",
    "arw":  "Sony RAW — convert to JPEG or TIFF",
    "psd":  "Photoshop — convert to PNG or flatten to JPEG",
    "ai":   "Illustrator — export to SVG instead",
}

COMMONS_MAX_BYTES = 100 * 1024 * 1024  # 100 MB


def file_extension(filename: str) -> str:
    return (filename.rsplit(".", 1)[-1] if "." in filename else "").lower()


def commons_check(filename: str, size_bytes: int) -> dict[str, Any]:
    """
    Pre-flight check: would Commons accept this file?
    Returns {accepted: bool, reason: str, severity: ok|warn|block}.
    """
    ext = file_extension(filename)

    if ext in COMMONS_REJECTED:
        return {
            "accepted": False,
            "severity": "block",
            "reason":   COMMONS_REJECTED[ext],
            "extension": ext,
            "suggested_action": "Convert below to a Commons-supported format",
        }

    if ext not in COMMONS_ACCEPTED:
        return {
            "accepted": False,
            "severity": "block",
            "reason":   f"Unknown format ‘.{ext}’ — Commons accepts JPEG, PNG, GIF, SVG, TIFF, WebP, XCF, PDF, DjVu",
            "extension": ext,
            "suggested_action": "Convert to PNG or JPEG",
        }

    if size_bytes > COMMONS_MAX_BYTES:
        return {
            "accepted": False,
            "severity": "warn",
            "reason":   f"File is {size_bytes / 1024 / 1024:.1f} MB — Commons web upload limit is 100 MB",
            "extension": ext,
            "suggested_action": "Compress below, or use chunked upload at Special:Upload",
        }

    return {
        "accepted": True,
        "severity": "ok",
        "reason":   f"{COMMONS_ACCEPTED[ext]} is accepted by Commons",
        "extension": ext,
        "suggested_action": None,
    }


# ─── EXIF ─────────────────────────────────────────────────────────

# Tags considered private and worth highlighting to the user
PRIVACY_SENSITIVE_TAGS = {
    "GPSInfo", "GPSLatitude", "GPSLongitude", "GPSAltitude",
    "GPSTimeStamp", "GPSDateStamp", "GPSProcessingMethod",
    "SerialNumber", "BodySerialNumber", "LensSerialNumber",
    "OwnerName", "Artist", "CameraOwnerName",
}


def read_exif(img: Image.Image) -> dict[str, Any]:
    """Extract EXIF as a friendly dict. Flags privacy-sensitive entries."""
    raw = img.getexif()
    if not raw:
        return {"present": False, "tags": {}, "sensitive": []}

    tags: dict[str, Any] = {}
    sensitive: list[str] = []

    for tag_id, value in raw.items():
        name = TAGS.get(tag_id, f"Tag{tag_id}")

        # GPSInfo is a sub-IFD
        if name == "GPSInfo" and isinstance(value, dict):
            gps_friendly = {GPSTAGS.get(k, str(k)): str(v) for k, v in value.items()}
            tags["GPSInfo"] = gps_friendly
            sensitive.append("GPSInfo")
            continue

        try:
            if isinstance(value, bytes):
                value = value.decode("utf-8", errors="replace").strip("\x00")
            tags[name] = str(value)[:200]  # truncate huge values
        except Exception:
            continue

        if name in PRIVACY_SENSITIVE_TAGS:
            sensitive.append(name)

    return {"present": True, "tags": tags, "sensitive": sensitive}


def strip_exif(img: Image.Image, mode: str = "all") -> Image.Image:
    """
    Return a copy of the image with EXIF removed.
    mode = 'all'  → drop everything
    mode = 'gps'  → drop only GPS tags, keep timestamp/camera info
    """
    if mode == "gps":
        exif = img.getexif()
        # GPSInfo tag id is 34853
        if 34853 in exif:
            del exif[34853]
        # Re-export with the modified EXIF
        out = io.BytesIO()
        img.save(out, format=img.format or "JPEG", exif=exif.tobytes())
        out.seek(0)
        new = Image.open(out)
        new.load()
        return new

    # mode == 'all'
    data = list(img.getdata())
    clean = Image.new(img.mode, img.size)
    clean.putdata(data)
    return clean


# ─── CORE PIPELINE ────────────────────────────────────────────────

def open_image(blob: bytes) -> Image.Image:
    img = Image.open(io.BytesIO(blob))
    img = ImageOps.exif_transpose(img)  # honor EXIF orientation
    return img


def convert_image(
    blob: bytes,
    filename: str,
    target_format: str = "jpg",
    quality: int = 90,
    max_dim: int | None = None,
    exif_mode: str = "keep",  # keep | gps | all
) -> dict[str, Any]:
    """
    Main pipeline: returns {bytes, mime, suggested_filename, info}.
    """
    target_format = target_format.lower()
    if target_format in ("jpg", "jpeg"):
        pil_format, ext, mime = "JPEG", "jpg", "image/jpeg"
    elif target_format == "png":
        pil_format, ext, mime = "PNG", "png", "image/png"
    elif target_format == "webp":
        pil_format, ext, mime = "WEBP", "webp", "image/webp"
    else:
        raise ValueError(f"Unsupported target format: {target_format}")

    img = open_image(blob)
    original_size = img.size
    original_mode = img.mode

    # Convert mode: JPEG can't handle alpha, PNG can
    if pil_format == "JPEG" and img.mode in ("RGBA", "LA", "P"):
        # Flatten transparency to white background
        bg = Image.new("RGB", img.size, (255, 255, 255))
        if img.mode == "P":
            img = img.convert("RGBA")
        bg.paste(img, mask=img.split()[-1] if img.mode in ("RGBA", "LA") else None)
        img = bg
    elif pil_format != "JPEG" and img.mode == "P":
        img = img.convert("RGBA")

    # Resize if requested
    if max_dim and max(img.size) > max_dim:
        img.thumbnail((max_dim, max_dim), Image.Resampling.LANCZOS)

    # EXIF handling
    if exif_mode != "keep":
        img = strip_exif(img, mode=exif_mode)

    # Encode
    out = io.BytesIO()
    save_kwargs: dict[str, Any] = {}
    if pil_format == "JPEG":
        save_kwargs.update({"quality": quality, "optimize": True, "progressive": True})
    elif pil_format == "PNG":
        save_kwargs.update({"optimize": True, "compress_level": 9})
    elif pil_format == "WEBP":
        save_kwargs.update({"quality": quality, "method": 6})

    img.save(out, format=pil_format, **save_kwargs)
    out_bytes = out.getvalue()

    # Build suggested filename
    base = filename.rsplit(".", 1)[0] if "." in filename else filename
    suggested = f"{base}.{ext}"

    return {
        "bytes":              out_bytes,
        "mime":               mime,
        "suggested_filename": suggested,
        "info": {
            "original_size_px":  list(original_size),
            "final_size_px":     list(img.size),
            "original_mode":     original_mode,
            "final_format":      pil_format,
            "input_size_bytes":  len(blob),
            "output_size_bytes": len(out_bytes),
            "compression_pct":   round((1 - len(out_bytes) / len(blob)) * 100, 1)
                                 if len(blob) else 0,
        },
    }