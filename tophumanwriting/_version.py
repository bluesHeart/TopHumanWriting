# -*- coding: utf-8 -*-

from __future__ import annotations

from pathlib import Path


def _read_version_from_pyproject() -> str:
    # Best-effort fallback for "run from source tree" without installation.
    try:
        import tomllib  # py>=3.11
    except Exception:
        return "0.0.0"

    try:
        root = Path(__file__).resolve().parent.parent
        pyproject = root / "pyproject.toml"
        if not pyproject.exists():
            return "0.0.0"
        with open(pyproject, "rb") as f:
            data = tomllib.load(f)
        proj = data.get("project", {}) if isinstance(data, dict) else {}
        v = proj.get("version", "") if isinstance(proj, dict) else ""
        v = str(v or "").strip()
        return v or "0.0.0"
    except Exception:
        return "0.0.0"


def _read_version_from_metadata() -> str:
    try:
        from importlib.metadata import version  # py>=3.8

        v = str(version("tophumanwriting") or "").strip()
        return v or "0.0.0"
    except Exception:
        return "0.0.0"


VERSION = _read_version_from_metadata()
if VERSION == "0.0.0":
    VERSION = _read_version_from_pyproject()

__version__ = VERSION

__all__ = ["VERSION", "__version__"]

