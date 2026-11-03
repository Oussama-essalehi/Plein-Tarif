"""Configuration loading, project paths and logging setup."""

from __future__ import annotations

import logging
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[2]


@lru_cache(maxsize=None)
def load_yaml(relative_path: str) -> dict[str, Any]:
    """Load a YAML file relative to the repository root."""
    with open(ROOT / relative_path, encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def cfg() -> dict[str, Any]:
    """Main configuration (config.yaml)."""
    return load_yaml("config.yaml")


def taxes() -> dict[str, Any]:
    """Tax rates (config/taxes.yaml)."""
    return load_yaml("config/taxes.yaml")


def events() -> dict[str, Any]:
    """Discount periods and annotations (config/events.yaml)."""
    return load_yaml("config/events.yaml")


def path(key: str) -> Path:
    """Absolute path for a key of the `paths` section, created if it is a directory."""
    p = ROOT / cfg()["paths"][key]
    if p.suffix == "":
        p.mkdir(parents=True, exist_ok=True)
    else:
        p.parent.mkdir(parents=True, exist_ok=True)
    return p


def years() -> list[int]:
    """Years covered by the analysis."""
    period = cfg()["period"]
    return list(range(period["start_year"], period["end_year"] + 1))


def get_logger(name: str) -> logging.Logger:
    """Module logger with a single consistent format."""
    if not logging.getLogger().handlers:
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s %(levelname)-7s %(name)s | %(message)s",
            datefmt="%H:%M:%S",
        )
    return logging.getLogger(name)
