# -*- coding: utf-8 -*-
"""
Internationalization (i18n) module for AI Word Detector
Supports English and Simplified Chinese
"""

import os
import sys
import json
from typing import Dict, Optional, Callable, List


def get_resource_path(relative_path):
    """Get absolute path to resource, works for dev and for PyInstaller"""
    if hasattr(sys, '_MEIPASS'):
        return os.path.join(sys._MEIPASS, relative_path)
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), relative_path)


class I18n:
    """Simple JSON-based internationalization system"""

    SUPPORTED_LANGUAGES = {
        'en': 'English',
        'zh_CN': '中文'
    }

    DEFAULT_LANGUAGE = 'en'

    def __init__(self):
        self._current_language = self.DEFAULT_LANGUAGE
        self._translations: Dict[str, Dict[str, str]] = {}
        self._callbacks: List[Callable] = []
        self._load_all_translations()

    def _load_all_translations(self):
        """Load all available translation files"""
        for lang_code in self.SUPPORTED_LANGUAGES:
            self._load_translation(lang_code)

    def _load_translation(self, lang_code: str) -> bool:
        """Load a single translation file"""
        locale_path = get_resource_path(os.path.join('locales', f'{lang_code}.json'))
        try:
            with open(locale_path, 'r', encoding='utf-8') as f:
                self._translations[lang_code] = json.load(f)
            return True
        except (FileNotFoundError, json.JSONDecodeError) as e:
            print(f"Warning: Could not load translation for {lang_code}: {e}")
            self._translations[lang_code] = {}
            return False

    @property
    def current_language(self) -> str:
        """Get current language code"""
        return self._current_language

    @current_language.setter
    def current_language(self, lang_code: str):
        """Set current language and notify listeners"""
        if lang_code in self.SUPPORTED_LANGUAGES:
            self._current_language = lang_code
            self._notify_language_change()

    def get(self, key: str, **kwargs) -> str:
        """
        Get translated string for the given key.
        Supports placeholder replacement with {placeholder} syntax.

        Args:
            key: Translation key (e.g., "app.title")
            **kwargs: Placeholder values (e.g., pdf_count=10)

        Returns:
            Translated string or the key itself if not found
        """
        translation = self._translations.get(self._current_language, {}).get(key)

        # Fallback to English if translation not found
        if translation is None and self._current_language != 'en':
            translation = self._translations.get('en', {}).get(key)

        # Return key if no translation found
        if translation is None:
            return key

        # Replace placeholders
        if kwargs:
            try:
                translation = translation.format(**kwargs)
            except KeyError:
                pass  # Keep original if placeholder not provided

        return translation

    def t(self, key: str, **kwargs) -> str:
        """Alias for get() method"""
        return self.get(key, **kwargs)

    def register_callback(self, callback: Callable):
        """Register a callback to be called when language changes"""
        if callback not in self._callbacks:
            self._callbacks.append(callback)

    def unregister_callback(self, callback: Callable):
        """Unregister a language change callback"""
        if callback in self._callbacks:
            self._callbacks.remove(callback)

    def _notify_language_change(self):
        """Notify all registered callbacks about language change"""
        for callback in self._callbacks:
            try:
                callback()
            except Exception as e:
                print(f"Error in language change callback: {e}")

    def get_language_name(self, lang_code: Optional[str] = None) -> str:
        """Get human-readable language name"""
        if lang_code is None:
            lang_code = self._current_language
        return self.SUPPORTED_LANGUAGES.get(lang_code, lang_code)

    def get_available_languages(self) -> Dict[str, str]:
        """Get dictionary of available languages {code: name}"""
        return self.SUPPORTED_LANGUAGES.copy()


# Global i18n instance
_i18n = I18n()


def get_i18n() -> I18n:
    """Get the global i18n instance"""
    return _i18n


def t(key: str, **kwargs) -> str:
    """Shortcut function for translation"""
    return _i18n.get(key, **kwargs)


def set_language(lang_code: str):
    """Shortcut function to set language"""
    _i18n.current_language = lang_code


def get_language() -> str:
    """Shortcut function to get current language"""
    return _i18n.current_language
