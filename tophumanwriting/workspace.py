# -*- coding: utf-8 -*-

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Workspace:
    """
    Filesystem workspace that stores *reusable* artifacts.

    This intentionally mirrors the repo's existing layout under `get_settings_dir()`:
      - rag/<library>/
      - cite/<library>/
      - materials/<library>/
      - libraries/<library>.json   (vocab/stat baselines)
      - audit/exports/
      - audit/coverage/
      - citecheck/cache/

    The key design principle is the classic fit/transform split:
      - Build library artifacts once (slow)  -> reusable
      - Run audits repeatedly (fast-ish)      -> uses artifacts
    """

    data_dir: Path

    @staticmethod
    def default() -> "Workspace":
        try:
            from ai_word_detector import get_settings_dir  # type: ignore
        except Exception as e:  # pragma: no cover
            raise RuntimeError(f"Cannot import get_settings_dir from ai_word_detector: {e}") from e
        return Workspace(Path(get_settings_dir()))

    @staticmethod
    def from_env() -> "Workspace":
        env_dir = (os.environ.get("TOPHUMANWRITING_DATA_DIR", "") or "").strip()
        if env_dir:
            return Workspace(Path(env_dir))
        return Workspace.default()

    def ensure_dirs(self) -> None:
        for rel in [
            "audit/exports",
            "audit/coverage",
            "citecheck/cache",
            "logs",
            "tmp",
        ]:
            try:
                (self.data_dir / rel).mkdir(parents=True, exist_ok=True)
            except Exception:
                pass

    def rag_library_dir(self, library: str) -> Path:
        return self.data_dir / "rag" / str(library).strip()

    def cite_library_dir(self, library: str) -> Path:
        return self.data_dir / "cite" / str(library).strip()

    def materials_library_dir(self, library: str) -> Path:
        return self.data_dir / "materials" / str(library).strip()

    def vocab_library_path(self, library: str) -> Path:
        try:
            from ai_word_detector import LibraryManager  # type: ignore
        except Exception:
            return self.data_dir / "libraries" / f"{library}.json"
        lm = LibraryManager()
        return Path(lm.get_library_path(str(library).strip()))

    def audit_exports_dir(self) -> Path:
        return self.data_dir / "audit" / "exports"

    def audit_coverage_dir(self) -> Path:
        return self.data_dir / "audit" / "coverage"

