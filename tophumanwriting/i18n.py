# -*- coding: utf-8 -*-
"""
Internationalization (i18n) module for TopHumanWriting.

This is intentionally namespaced under `tophumanwriting` to avoid installing a
top-level `i18n` module on PyPI (which would shadow other packages).
"""

from __future__ import annotations

import importlib.resources
import json
import os
import sys
from typing import Callable, Dict, List, Optional


def get_resource_path(relative_path: str) -> str:
    """Get absolute path to resource (dev + bundled builds)."""
    if hasattr(sys, "_MEIPASS"):
        return os.path.join(sys._MEIPASS, relative_path)
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), relative_path)


class I18n:
    """Simple JSON-based internationalization system."""

    SUPPORTED_LANGUAGES = {"en": "English", "zh_CN": "中文"}
    DEFAULT_LANGUAGE = "en"

    def __init__(self):
        self._current_language = self.DEFAULT_LANGUAGE
        self._translations: Dict[str, Dict[str, str]] = {}
        self._callbacks: List[Callable] = []
        self._load_all_translations()

    def _load_all_translations(self) -> None:
        for lang_code in self.SUPPORTED_LANGUAGES:
            self._load_translation(lang_code)

    def _load_translation(self, lang_code: str) -> bool:
        locale_path = get_resource_path(os.path.join("locales", f"{lang_code}.json"))
        try:
            with open(locale_path, "r", encoding="utf-8") as f:
                self._translations[lang_code] = json.load(f)
            return True
        except Exception:
            # When installed as a normal python package, translations live under:
            #   tophumanwriting/locales/<lang>.json
            try:
                data = (
                    importlib.resources.files("tophumanwriting")
                    .joinpath("locales", f"{lang_code}.json")
                    .read_text(encoding="utf-8")
                )
                self._translations[lang_code] = json.loads(data)
                return True
            except Exception:
                self._translations[lang_code] = {}
                return False

    @property
    def current_language(self) -> str:
        return self._current_language

    @current_language.setter
    def current_language(self, lang_code: str) -> None:
        if lang_code in self.SUPPORTED_LANGUAGES:
            self._current_language = lang_code
            self._notify_language_change()

    def get(self, key: str, **kwargs) -> str:
        translation = self._translations.get(self._current_language, {}).get(key)
        if translation is None and self._current_language != "en":
            translation = self._translations.get("en", {}).get(key)
        if translation is None:
            return key
        if kwargs:
            try:
                translation = translation.format(**kwargs)
            except KeyError:
                pass
        return translation

    def t(self, key: str, **kwargs) -> str:
        return self.get(key, **kwargs)

    def register_callback(self, callback: Callable) -> None:
        if callback not in self._callbacks:
            self._callbacks.append(callback)

    def unregister_callback(self, callback: Callable) -> None:
        if callback in self._callbacks:
            self._callbacks.remove(callback)

    def _notify_language_change(self) -> None:
        for callback in self._callbacks:
            try:
                callback()
            except Exception:
                pass

    def get_language_name(self, lang_code: Optional[str] = None) -> str:
        lang_code = lang_code or self._current_language
        return self.SUPPORTED_LANGUAGES.get(lang_code, lang_code)

    def get_available_languages(self) -> Dict[str, str]:
        return dict(self.SUPPORTED_LANGUAGES)


_i18n = I18n()


def get_i18n() -> I18n:
    return _i18n


def t(key: str, **kwargs) -> str:
    return _i18n.get(key, **kwargs)


def set_language(lang_code: str) -> None:
    _i18n.current_language = lang_code


def get_language() -> str:
    return _i18n.current_language

