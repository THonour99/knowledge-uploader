from __future__ import annotations

import re
from pathlib import PurePath

WINDOWS_RESERVED_NAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *{f"COM{index}" for index in range(10)},
    *{f"LPT{index}" for index in range(10)},
}
INVALID_FILENAME_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def sanitize_filename(filename: str, max_length: int = 200) -> str:
    name = PurePath(filename).name.strip()
    name = INVALID_FILENAME_CHARS.sub("_", name)
    name = name.lstrip(".").rstrip(" ")
    if not name:
        return "unnamed"
    stem = name.split(".", 1)[0].upper()
    if stem in WINDOWS_RESERVED_NAMES:
        name = f"_{name}"
    return name[:max_length]
