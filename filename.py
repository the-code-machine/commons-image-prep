"""
Suggest a Commons-policy-compliant filename.

Rules (https://commons.wikimedia.org/wiki/Commons:File_naming):
- Descriptive, not auto-camera names like IMG_1234
- No special characters, prefer ASCII
- Use spaces or underscores (Commons normalises both to spaces)
- Keep under ~240 characters total path
"""

import re

# iPhone / camera default filename patterns
CAMERA_DEFAULTS = [
    re.compile(r"^IMG[_-]?\d+$",   re.I),
    re.compile(r"^DSC[_-]?\d+$",   re.I),
    re.compile(r"^DSCN\d+$",       re.I),
    re.compile(r"^P\d{7}$",        re.I),
    re.compile(r"^GOPR\d+$",       re.I),
    re.compile(r"^\d{8}_\d{6}$"),       # 20240301_140532
    re.compile(r"^[a-f0-9]{8,}$"), # UUIDs
]


def is_camera_default(stem: str) -> bool:
    return any(p.match(stem) for p in CAMERA_DEFAULTS)


def sanitize(stem: str) -> str:
    """Replace problematic characters with safe ones."""
    s = stem.replace("\\", "_").replace("/", "_")
    s = re.sub(r"[<>:\"|?*\x00-\x1f]", "", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def suggest_filename(original: str, hint: str = "") -> dict:
    """
    Returns {ok, suggestion, warnings, reasons}.
    `hint` is an optional user-typed description ("Hyderabad Charminar at sunset").
    """
    if "." not in original:
        stem, ext = original, ""
    else:
        stem, ext = original.rsplit(".", 1)

    warnings: list[str] = []
    reasons:  list[str] = []
    suggestion = sanitize(stem)

    if is_camera_default(stem):
        warnings.append("Filename looks like a camera default (e.g. IMG_1234)")
        reasons.append("Commons policy: filenames should describe the subject")
        if hint.strip():
            suggestion = sanitize(hint.strip())
        else:
            suggestion = stem  # leave as-is, user must replace

    # Length check
    if len(suggestion) + len(ext) > 200:
        warnings.append("Filename is very long; consider shortening")

    # Special chars surviving sanitisation?
    if re.search(r"[^\w\s\-\(\)\.]", suggestion, flags=re.UNICODE):
        warnings.append("Contains characters that may not display well on all wikis")

    if not suggestion or suggestion == stem and is_camera_default(stem):
        reasons.append("Add a description (subject, location, year) before uploading")

    full = f"{suggestion}.{ext}" if ext else suggestion

    return {
        "ok":         not warnings,
        "suggestion": full,
        "warnings":   warnings,
        "reasons":    reasons,
    }