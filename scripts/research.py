#!/usr/bin/env python3
"""Portable absolute-path wrapper; install the package into the local venv first."""

import sys
from pathlib import Path

from research_vault.cli import main

if __name__ == "__main__":
    raise SystemExit(main(["--vault", str(Path(__file__).resolve().parents[1]), *sys.argv[1:]]))
