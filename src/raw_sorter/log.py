"""Minimal leveled, line-oriented logging to stderr."""
from __future__ import annotations

import logging
import sys

_LEVELS = {"debug": logging.DEBUG, "info": logging.INFO,
           "warn": logging.WARNING, "warning": logging.WARNING, "error": logging.ERROR}


def setup(level: str = "info") -> None:
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter(
        fmt="%(asctime)s %(levelname)-5s %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    ))
    root = logging.getLogger("raw_sorter")
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(_LEVELS.get(level.lower(), logging.INFO))
    root.propagate = False


def get(name: str = "raw_sorter") -> logging.Logger:
    return logging.getLogger("raw_sorter" if name == "raw_sorter" else f"raw_sorter.{name}")
