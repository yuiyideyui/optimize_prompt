"""Paths for persistent plugin data."""

import hashlib
import os
from pathlib import Path


def get_data_root() -> Path:
    configured = os.environ.get("COMIC2ANIME_DATA_DIR", "").strip()
    if configured:
        return Path(configured).expanduser().resolve()
    plugin_root = Path(__file__).resolve().parents[2]
    return (plugin_root / "comic2anime_data").resolve()


def safe_component(value: str) -> str:
    cleaned = "".join(
        char if char.isalnum() or char in ("-", "_", ".") else "_"
        for char in value.strip()
    ).strip("._")
    if not cleaned or cleaned in (".", ".."):
        raise ValueError("Project and chapter IDs must contain a safe path character.")
    reserved_names = {
        "CON",
        "PRN",
        "AUX",
        "NUL",
    }
    for number in range(1, 10):
        reserved_names.add("COM{}".format(number))
        reserved_names.add("LPT{}".format(number))
    if len(cleaned) > 100 or cleaned.upper() in reserved_names:
        suffix = hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]
        cleaned = "{}-{}".format(cleaned[:80].rstrip("._"), suffix)
    return cleaned
