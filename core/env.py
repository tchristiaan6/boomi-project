"""Minimal .env loader. No dependency, no override of real environment.

Looks for a .env file at the project root (the parent of core/). Lines are
KEY=VALUE; blanks and #comments ignored. Values already present in the
environment win, so `FDATRACK_MODEL=x fdatrack assess ...` still overrides.
"""

from __future__ import annotations

import os
from pathlib import Path


def load_env(path: str | Path | None = None) -> None:
    env_path = Path(path) if path else Path(__file__).resolve().parent.parent / ".env"
    if not env_path.is_file():
        return
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip().strip('"').strip("'")
        if key and value and key not in os.environ:
            os.environ[key] = value
