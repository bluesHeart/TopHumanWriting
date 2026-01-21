# -*- coding: utf-8 -*-
"""
AI Word Detector v2.1 - Academic Writing Style Analyzer
Compare your text against real academic papers to identify unusual word choices.
"""

import os
import sys
import json
import re
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from collections import Counter
import threading
from pathlib import Path

from version import VERSION
from i18n import get_i18n, t, set_language, get_language


# Font configuration - clean, readable fonts
FONT_UI = "Microsoft YaHei UI"  # For UI elements (supports Chinese)
FONT_MONO = "Cascadia Code"     # For code/text display (fallback to Consolas)

# Smooth corner radius
CORNER_RADIUS = 16


def get_resource_path(relative_path):
    if hasattr(sys, '_MEIPASS'):
        return os.path.join(sys._MEIPASS, relative_path)
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), relative_path)


def get_app_dir():
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


def get_settings_dir():
    appdata = os.environ.get('APPDATA', os.path.expanduser('~'))
    settings_dir = os.path.join(appdata, 'AIWordDetector')
    os.makedirs(settings_dir, exist_ok=True)
    return settings_dir


try:
    import fitz
except ImportError:
    fitz = None

try:
    import jieba
except ImportError:
    jieba = None


# Extended stop words list - words to ignore in analysis
STOP_WORDS = {
    'a', 'an', 'the',
    'i', 'me', 'my', 'myself', 'we', 'our', 'ours', 'ourselves',
    'you', 'your', 'yours', 'yourself', 'yourselves',
    'he', 'him', 'his', 'himself', 'she', 'her', 'hers', 'herself',
    'it', 'its', 'itself', 'they', 'them', 'their', 'theirs', 'themselves',
    'what', 'which', 'who', 'whom', 'this', 'that', 'these', 'those',
    'in', 'on', 'at', 'to', 'for', 'of', 'with', 'by', 'from', 'as',
    'into', 'through', 'during', 'before', 'after', 'above', 'below',
    'between', 'under', 'over', 'out', 'off', 'down', 'up', 'about',
    'against', 'within', 'without', 'along', 'around', 'among',
    'and', 'or', 'but', 'nor', 'so', 'yet', 'both', 'either', 'neither',
    'not', 'only', 'than', 'when', 'while', 'if', 'then', 'else',
    'because', 'although', 'though', 'unless', 'since', 'until',
    'is', 'am', 'are', 'was', 'were', 'be', 'been', 'being',
    'have', 'has', 'had', 'having', 'do', 'does', 'did', 'doing',
    'will', 'would', 'could', 'should', 'may', 'might', 'must',
    'shall', 'can', 'need', 'dare', 'ought',
    'very', 'just', 'also', 'now', 'here', 'there', 'where',
    'how', 'why', 'when', 'all', 'each', 'every', 'any', 'some',
    'no', 'more', 'most', 'other', 'such', 'own', 'same', 'too',
    'few', 'many', 'much', 'less', 'least', 'further', 'once', 'again',
    'et', 'al', 'ie', 'eg', 'cf', 'vs', 'etc', 'pp', 'vol',
    'fig', 'table', 'eq', 'eqs', 'ref', 'refs',
    'one', 'two', 'three', 'four', 'five', 'six', 'seven', 'eight', 'nine', 'ten',
    'first', 'second', 'third',
}


class Theme:
    """Theme configuration - supports light and dark modes"""

    # Light theme (default) - clean, bright, consistent
    LIGHT = {
        'BG_PRIMARY': "#f8f9fa",        # Very light gray background
        'BG_SECONDARY': "#ffffff",       # Pure white panels
        'BG_TERTIARY': "#e9ecef",        # Subtle gray for borders/dividers
        'BG_INPUT': "#ffffff",           # White text areas
        'BG_HOVER': "#e9ecef",           # Hover state
        'TEXT_PRIMARY': "#212529",       # Near black text
        'TEXT_SECONDARY': "#495057",     # Dark gray secondary text
        'TEXT_MUTED': "#6c757d",         # Muted gray
        'BORDER': "#dee2e6",             # Light border
    }

    # Dark theme - cohesive, easy on eyes
    DARK = {
        'BG_PRIMARY': "#1a1a1a",         # Main background
        'BG_SECONDARY': "#242424",       # Panels
        'BG_TERTIARY': "#2e2e2e",        # Borders, separators
        'BG_INPUT': "#2a2a2a",           # Text input areas
        'BG_HOVER': "#363636",           # Hover state
        'TEXT_PRIMARY': "#e4e4e4",       # Light text
        'TEXT_SECONDARY': "#a8a8a8",     # Secondary text
        'TEXT_MUTED': "#707070",         # Muted text
        'BORDER': "#3a3a3a",             # Subtle borders
    }

    # Current theme colors (will be set based on mode)
    BG_PRIMARY = LIGHT['BG_PRIMARY']
    BG_SECONDARY = LIGHT['BG_SECONDARY']
    BG_TERTIARY = LIGHT['BG_TERTIARY']
    BG_INPUT = LIGHT['BG_INPUT']
    BG_HOVER = LIGHT['BG_HOVER']
    TEXT_PRIMARY = LIGHT['TEXT_PRIMARY']
    TEXT_SECONDARY = LIGHT['TEXT_SECONDARY']
    TEXT_MUTED = LIGHT['TEXT_MUTED']
    BORDER = LIGHT['BORDER']

    # Accent colors (same for both themes)
    PRIMARY = "#3b82f6"
    PRIMARY_HOVER = "#2563eb"
    PRIMARY_DARK = "#1d4ed8"

    # Status colors
    SUCCESS = "#16a34a"           # Green (darker for light theme)
    WARNING = "#d97706"           # Orange/amber
    DANGER = "#dc2626"            # Red
    NORMAL_COLOR = "#525252"      # Gray for normal

    @classmethod
    def set_mode(cls, dark_mode: bool):
        """Switch between light and dark theme"""
        theme = cls.DARK if dark_mode else cls.LIGHT
        cls.BG_PRIMARY = theme['BG_PRIMARY']
        cls.BG_SECONDARY = theme['BG_SECONDARY']
        cls.BG_TERTIARY = theme['BG_TERTIARY']
        cls.BG_INPUT = theme['BG_INPUT']
        cls.BG_HOVER = theme['BG_HOVER']
        cls.TEXT_PRIMARY = theme['TEXT_PRIMARY']
        cls.TEXT_SECONDARY = theme['TEXT_SECONDARY']
        cls.TEXT_MUTED = theme['TEXT_MUTED']
        cls.BORDER = theme['BORDER']
        # Adjust status colors for dark mode
        if dark_mode:
            cls.SUCCESS = "#22c55e"
            cls.NORMAL_COLOR = "#a3a3a3"
        else:
            cls.SUCCESS = "#16a34a"
            cls.NORMAL_COLOR = "#525252"


class LanguageDetector:
    @staticmethod
    def detect(text: str) -> str:
        if not text:
            return 'en'
        chinese_chars = len(re.findall(r'[\u4e00-\u9fff]', text))
        english_chars = len(re.findall(r'[a-zA-Z]', text))
        total = chinese_chars + english_chars
        if total == 0:
            return 'en'
        chinese_ratio = chinese_chars / total
        if chinese_ratio > 0.7:
            return 'zh'
        elif chinese_ratio > 0.3:
            return 'mixed'
        return 'en'


class Settings:
    def __init__(self):
        self.settings_file = os.path.join(get_settings_dir(), 'settings.json')
        self._settings = self._load_settings()

    def _load_settings(self) -> dict:
        defaults = {'language': 'en', 'font_size': 13, 'dark_mode': False}
        try:
            if os.path.exists(self.settings_file):
                with open(self.settings_file, 'r', encoding='utf-8') as f:
                    defaults.update(json.load(f))
        except (json.JSONDecodeError, IOError):
            pass
        return defaults

    def save(self):
        try:
            with open(self.settings_file, 'w', encoding='utf-8') as f:
                json.dump(self._settings, f, ensure_ascii=False, indent=2)
        except IOError:
            pass

    def get(self, key: str, default=None):
        return self._settings.get(key, default)

    def set(self, key: str, value):
        self._settings[key] = value
        self.save()


class LibraryManager:
    """Manage multiple vocabulary libraries"""
    def __init__(self):
        self.libraries_dir = os.path.join(get_settings_dir(), 'libraries')
        os.makedirs(self.libraries_dir, exist_ok=True)

    def get_library_path(self, name: str) -> str:
        """Get full path for a library file"""
        safe_name = re.sub(r'[^\w\-]', '_', name)
        return os.path.join(self.libraries_dir, f"{safe_name}.json")

    def list_libraries(self) -> list:
        """List all available libraries"""
        libraries = []
        if os.path.exists(self.libraries_dir):
            for f in os.listdir(self.libraries_dir):
                if f.endswith('.json'):
                    name = f[:-5]  # Remove .json
                    path = os.path.join(self.libraries_dir, f)
                    try:
                        with open(path, 'r', encoding='utf-8') as file:
                            data = json.load(file)
                            libraries.append({
                                'name': name,
                                'path': path,
                                'doc_count': data.get('doc_count', 0),
                                'word_count': len(data.get('word_doc_freq', {}))
                            })
                    except:
                        libraries.append({
                            'name': name,
                            'path': path,
                            'doc_count': 0,
                            'word_count': 0
                        })
        return libraries

    def create_library(self, name: str) -> str:
        """Create a new empty library, return path"""
        path = self.get_library_path(name)
        data = {
            'word_doc_freq': {},
            'word_total_freq': {},
            'doc_count': 0,
            'total_words': 0,
            'version': '2.1'
        }
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False)
        return path

    def delete_library(self, name: str) -> bool:
        """Delete a library"""
        path = self.get_library_path(name)
        if os.path.exists(path):
            os.remove(path)
            return True
        return False

    def rename_library(self, old_name: str, new_name: str) -> bool:
        """Rename a library"""
        old_path = self.get_library_path(old_name)
        new_path = self.get_library_path(new_name)
        if os.path.exists(old_path) and not os.path.exists(new_path):
            os.rename(old_path, new_path)
            return True
        return False

    def clear_library(self, name: str) -> bool:
        """Clear library data but keep the library"""
        path = self.get_library_path(name)
        if os.path.exists(path):
            data = {
                'word_doc_freq': {},
                'word_total_freq': {},
                'doc_count': 0,
                'total_words': 0,
                'version': '2.1'
            }
            with open(path, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False)
            return True
        return False

    def get_library_info(self, name: str) -> dict:
        """Get detailed library info"""
        path = self.get_library_path(name)
        info = {
            'name': name,
            'path': path,
            'folder': self.libraries_dir,
            'doc_count': 0,
            'word_count': 0,
            'exists': os.path.exists(path)
        }
        if info['exists']:
            try:
                with open(path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    info['doc_count'] = data.get('doc_count', 0)
                    info['word_count'] = len(data.get('word_doc_freq', {}))
            except:
                pass
        return info

    def library_exists(self, name: str) -> bool:
        """Check if library exists"""
        return os.path.exists(self.get_library_path(name))


class AcademicCorpus:
    def __init__(self, library_path: str = None):
        self.word_doc_freq = Counter()
        self.word_total_freq = Counter()
        self.doc_count = 0
        self.total_words = 0
        self.library_path = library_path

    def _is_valid_word(self, word: str) -> bool:
        if len(word) < 3:
            return False
        if word in STOP_WORDS:
            return False
        if not word.isalpha():
            return False
        return True

    def _tokenize(self, text: str) -> list:
        text = text.lower()
        words = re.findall(r'\b[a-z]+\b', text)
        return [w for w in words if self._is_valid_word(w)]

    def extract_text_from_pdf(self, pdf_path: str) -> str:
        if not fitz:
            return ""
        try:
            doc = fitz.open(pdf_path)
            text = ""
            for page in doc:
                text += page.get_text(flags=fitz.TEXT_DEHYPHENATE)
            doc.close()
            return text
        except Exception:
            return ""

    def process_pdf_folder(self, folder_path: str, progress_callback=None) -> int:
        self.word_doc_freq = Counter()
        self.word_total_freq = Counter()
        self.doc_count = 0
        self.total_words = 0

        pdf_files = list(Path(folder_path).glob("*.pdf"))
        total_files = len(pdf_files)

        for idx, pdf_file in enumerate(pdf_files):
            try:
                text = self.extract_text_from_pdf(str(pdf_file))
                words = self._tokenize(text)

                if words:
                    unique_words_in_doc = set(words)
                    for word in unique_words_in_doc:
                        self.word_doc_freq[word] += 1
                    self.word_total_freq.update(words)
                    self.total_words += len(words)
                    self.doc_count += 1

                if progress_callback:
                    progress_callback(idx + 1, total_files, pdf_file.name)
            except Exception:
                pass

        return self.doc_count

    def get_word_stats(self, word: str) -> dict:
        word = word.lower()
        doc_freq = self.word_doc_freq.get(word, 0)
        total_freq = self.word_total_freq.get(word, 0)
        return {
            'word': word,
            'doc_freq': doc_freq,
            'doc_percent': (doc_freq / self.doc_count * 100) if self.doc_count > 0 else 0,
            'total_freq': total_freq,
            'docs_total': self.doc_count
        }

    def classify_word(self, word: str) -> str:
        stats = self.get_word_stats(word)
        pct = stats['doc_percent']
        if pct == 0:
            return 'unseen'
        elif pct < 10:
            return 'rare'
        elif pct < 50:
            return 'normal'
        else:
            return 'common'

    def save_vocabulary(self, filepath=None):
        if filepath is None:
            filepath = self.library_path
        if not filepath:
            return
        data = {
            'word_doc_freq': dict(self.word_doc_freq),
            'word_total_freq': dict(self.word_total_freq),
            'doc_count': self.doc_count,
            'total_words': self.total_words,
            'version': '2.1'
        }
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False)

    def load_vocabulary(self, filepath=None) -> bool:
        if filepath is None:
            filepath = self.library_path
        if not filepath or not os.path.exists(filepath):
            return False
        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                data = json.load(f)

            if 'word_freq' in data and 'word_doc_freq' not in data:
                self.word_total_freq = Counter(data.get('word_freq', {}))
                self.doc_count = data.get('pdf_count', 0)
                self.word_doc_freq = Counter()
                for word, freq in self.word_total_freq.items():
                    estimated_docs = min(self.doc_count, max(1, freq // 10))
                    self.word_doc_freq[word] = estimated_docs
                self.total_words = data.get('total_words', 0)
            else:
                self.word_doc_freq = Counter(data.get('word_doc_freq', {}))
                self.word_total_freq = Counter(data.get('word_total_freq', {}))
                self.doc_count = data.get('doc_count', 0)
                self.total_words = data.get('total_words', 0)
            self.library_path = filepath
            return True
        except Exception:
            return False

    def get_common_words(self, top_n=300) -> list:
        return self.word_doc_freq.most_common(top_n)


class ModernButton(tk.Canvas):
    """Smooth pill-shaped button with hover animation"""
    def __init__(self, parent, text, command=None, width=100, height=36,
                 bg=Theme.BG_TERTIARY, hover_bg=Theme.BG_HOVER, fg=Theme.TEXT_PRIMARY,
                 font_size=11, accent=False, **kwargs):
        super().__init__(parent, width=width, height=height,
                        bg=parent["bg"], highlightthickness=0, **kwargs)
        self.command = command
        self.bg = Theme.PRIMARY if accent else bg
        self.hover_bg = Theme.PRIMARY_HOVER if accent else hover_bg
        self.fg = "white" if accent else fg
        self.text = text
        self.font_size = font_size
        self._width = width
        self._height = height
        self.draw_button(self.bg)
        self.bind("<Enter>", self.on_enter)
        self.bind("<Leave>", self.on_leave)
        self.bind("<Button-1>", self.on_click)

    def draw_rounded_rect(self, x1, y1, x2, y2, radius, **kwargs):
        """Draw a smooth rounded rectangle"""
        points = [
            x1+radius, y1,
            x2-radius, y1,
            x2, y1,
            x2, y1+radius,
            x2, y2-radius,
            x2, y2,
            x2-radius, y2,
            x1+radius, y2,
            x1, y2,
            x1, y2-radius,
            x1, y1+radius,
            x1, y1,
        ]
        return self.create_polygon(points, smooth=True, **kwargs)

    def draw_button(self, color):
        self.delete("all")
        r = min(self._height // 2, 18)  # Pill shape
        self.draw_rounded_rect(0, 0, self._width, self._height, r, fill=color, outline="")
        self.create_text(self._width//2, self._height//2, text=self.text,
                        fill=self.fg, font=(FONT_UI, self.font_size))

    def set_text(self, text):
        self.text = text
        self.draw_button(self.bg)

    def on_enter(self, e):
        self.draw_button(self.hover_bg)
        self.config(cursor="hand2")

    def on_leave(self, e):
        self.draw_button(self.bg)

    def on_click(self, e):
        if self.command:
            self.command()


class IconButton(tk.Canvas):
    """Small circular icon button"""
    def __init__(self, parent, text, command=None, width=28, height=28,
                 bg=Theme.BG_TERTIARY, hover_bg=Theme.PRIMARY, fg=Theme.TEXT_SECONDARY,
                 **kwargs):
        super().__init__(parent, width=width, height=height,
                        bg=parent["bg"], highlightthickness=0, **kwargs)
        self.command = command
        self.bg = bg
        self.hover_bg = hover_bg
        self.fg = fg
        self.text = text
        self._width = width
        self._height = height
        self.draw_button(self.bg, self.fg)
        self.bind("<Enter>", self.on_enter)
        self.bind("<Leave>", self.on_leave)
        self.bind("<Button-1>", self.on_click)

    def draw_button(self, color, text_color):
        self.delete("all")
        # Draw circle
        self.create_oval(0, 0, self._width, self._height, fill=color, outline="")
        self.create_text(self._width//2, self._height//2, text=self.text,
                        fill=text_color, font=(FONT_UI, 11, "bold"))

    def on_enter(self, e):
        self.draw_button(self.hover_bg, "white")
        self.config(cursor="hand2")

    def on_leave(self, e):
        self.draw_button(self.bg, self.fg)

    def on_click(self, e):
        if self.command:
            self.command()


class RoundedPanel(tk.Canvas):
    """A panel with smooth rounded corners"""
    def __init__(self, parent, bg=Theme.BG_SECONDARY, radius=CORNER_RADIUS, **kwargs):
        super().__init__(parent, bg=parent["bg"], highlightthickness=0, **kwargs)
        self.panel_bg = bg
        self.radius = radius
        self._inner_frame = None
        self.bind("<Configure>", self._on_configure)

    def _on_configure(self, event):
        self.delete("bg")
        w, h = event.width, event.height
        r = self.radius
        # Draw rounded rectangle background
        points = [
            r, 0,
            w-r, 0,
            w, 0,
            w, r,
            w, h-r,
            w, h,
            w-r, h,
            r, h,
            0, h,
            0, h-r,
            0, r,
            0, 0,
        ]
        self.create_polygon(points, smooth=True, fill=self.panel_bg, outline="", tags="bg")
        self.tag_lower("bg")

    def get_inner_frame(self):
        if self._inner_frame is None:
            self._inner_frame = tk.Frame(self, bg=self.panel_bg)
            self.create_window(0, 0, window=self._inner_frame, anchor="nw", tags="inner")
            self.bind("<Configure>", lambda e: (self._on_configure(e),
                      self.itemconfig("inner", width=e.width, height=e.height)))
        return self._inner_frame


class ModernApp:
    def __init__(self, root):
        self.root = root
        self.settings = Settings()
        self.i18n = get_i18n()

        # Load and apply theme
        self.dark_mode = self.settings.get('dark_mode', False)
        Theme.set_mode(self.dark_mode)

        saved_lang = self.settings.get('language', 'en')
        set_language(saved_lang)

        self.root.title(f"{t('app.title')} v{VERSION}")
        self.root.geometry("1400x900")
        self.root.configure(bg=Theme.BG_PRIMARY)
        self.root.minsize(1100, 750)

        # Library management
        self.library_manager = LibraryManager()
        self.current_library = self.settings.get('current_library', None)

        # Initialize corpus with current library
        library_path = None
        if self.current_library:
            library_path = self.library_manager.get_library_path(self.current_library)
            if not os.path.exists(library_path):
                self.current_library = None
                library_path = None

        self.corpus = AcademicCorpus(library_path)
        self.font_size = self.settings.get('font_size', 13)
        self.detected_language = 'en'
        self.last_word_stats = []

        self._widgets = {}
        self.create_ui()
        self.load_vocabulary()
        self.i18n.register_callback(self.refresh_ui)

    def create_ui(self):
        # ========== Header Bar ==========
        header = tk.Frame(self.root, bg=Theme.BG_SECONDARY, height=56)
        header.pack(fill=tk.X, padx=16, pady=(16, 0))
        header.pack_propagate(False)

        # Round the header corners using a canvas overlay approach
        header_inner = tk.Frame(header, bg=Theme.BG_SECONDARY)
        header_inner.pack(fill=tk.BOTH, expand=True, padx=20, pady=12)

        # Title
        title_frame = tk.Frame(header_inner, bg=Theme.BG_SECONDARY)
        title_frame.pack(side=tk.LEFT)

        self._widgets['title_label'] = tk.Label(title_frame,
                text=t('app.title'),
                font=(FONT_UI, 15, "bold"),
                bg=Theme.BG_SECONDARY, fg=Theme.TEXT_PRIMARY)
        self._widgets['title_label'].pack(side=tk.LEFT)

        tk.Label(title_frame, text=f"  v{VERSION}",
                font=(FONT_UI, 10),
                bg=Theme.BG_SECONDARY, fg=Theme.TEXT_MUTED).pack(side=tk.LEFT, pady=(3, 0))

        # Right side: Status + Language + Font controls
        right_controls = tk.Frame(header_inner, bg=Theme.BG_SECONDARY)
        right_controls.pack(side=tk.RIGHT)

        # Status
        self.status_label = tk.Label(right_controls, text=t("status.loading"),
                                    font=(FONT_UI, 10),
                                    bg=Theme.BG_SECONDARY, fg=Theme.SUCCESS)
        self.status_label.pack(side=tk.LEFT, padx=(0, 20))

        # Language selector (styled)
        lang_frame = tk.Frame(right_controls, bg=Theme.BG_TERTIARY)
        lang_frame.pack(side=tk.LEFT, padx=(0, 12))

        self.lang_var = tk.StringVar(value=get_language())
        lang_dropdown = ttk.Combobox(lang_frame, textvariable=self.lang_var,
                                    values=['en', 'zh_CN'], width=6, state='readonly',
                                    font=(FONT_UI, 10))
        lang_dropdown.pack(padx=4, pady=4)
        lang_dropdown.bind('<<ComboboxSelected>>', self.on_language_change)

        # Font size controls
        font_frame = tk.Frame(right_controls, bg=Theme.BG_SECONDARY)
        font_frame.pack(side=tk.LEFT)

        tk.Label(font_frame, text="A", font=(FONT_UI, 10),
                bg=Theme.BG_SECONDARY, fg=Theme.TEXT_MUTED).pack(side=tk.LEFT, padx=(0, 4))

        IconButton(font_frame, "−", self.decrease_font).pack(side=tk.LEFT, padx=2)

        self.font_label = tk.Label(font_frame, text=str(self.font_size),
                                  font=(FONT_UI, 10, "bold"),
                                  bg=Theme.BG_SECONDARY, fg=Theme.TEXT_PRIMARY, width=2)
        self.font_label.pack(side=tk.LEFT)

        IconButton(font_frame, "+", self.increase_font).pack(side=tk.LEFT, padx=2)

        # Theme toggle (sun/moon icon)
        theme_icon = "☀" if self.dark_mode else "☾"
        self._widgets['btn_theme'] = IconButton(right_controls, theme_icon,
                                                self.toggle_theme, width=28, height=28)
        self._widgets['btn_theme'].pack(side=tk.LEFT, padx=(12, 0))

        # ========== Toolbar ==========
        toolbar = tk.Frame(self.root, bg=Theme.BG_PRIMARY, height=50)
        toolbar.pack(fill=tk.X, padx=16, pady=(12, 8))

        # Library selector section
        lib_frame = tk.Frame(toolbar, bg=Theme.BG_PRIMARY)
        lib_frame.pack(side=tk.LEFT)

        tk.Label(lib_frame, text=t("library.label"), font=(FONT_UI, 10),
                bg=Theme.BG_PRIMARY, fg=Theme.TEXT_MUTED).pack(side=tk.LEFT, padx=(0, 6))

        # Library dropdown
        self.library_var = tk.StringVar(value=self.current_library or "")
        self._widgets['lib_dropdown'] = ttk.Combobox(lib_frame, textvariable=self.library_var,
                                    width=15, state='readonly', font=(FONT_UI, 10))
        self._widgets['lib_dropdown'].pack(side=tk.LEFT, padx=(0, 8))
        self._widgets['lib_dropdown'].bind('<<ComboboxSelected>>', self.on_library_change)
        self.update_library_dropdown()

        # Library buttons
        self._widgets['btn_new_lib'] = ModernButton(lib_frame, "+",
                    self.create_new_library, width=36, height=32, font_size=12)
        self._widgets['btn_new_lib'].pack(side=tk.LEFT, padx=(0, 4))

        self._widgets['btn_lib_menu'] = ModernButton(lib_frame, "⋮",
                    self.show_library_menu, width=36, height=32, font_size=14)
        self._widgets['btn_lib_menu'].pack(side=tk.LEFT, padx=(0, 16))

        # Action buttons
        self._widgets['btn_load_pdf'] = ModernButton(toolbar, t("toolbar.load_pdf"),
                    self.select_pdf_folder, width=100, height=36, font_size=11)
        self._widgets['btn_load_pdf'].pack(side=tk.LEFT, padx=(0, 10))

        self._widgets['btn_show_vocab'] = ModernButton(toolbar, t("toolbar.show_vocab"),
                    self.show_vocabulary, width=90, height=36, font_size=11)
        self._widgets['btn_show_vocab'].pack(side=tk.LEFT, padx=(0, 10))

        # ========== Main Content Area ==========
        content = tk.Frame(self.root, bg=Theme.BG_PRIMARY)
        content.pack(fill=tk.BOTH, expand=True, padx=16, pady=0)

        # Left panel: Input
        left_panel = tk.Frame(content, bg=Theme.BG_SECONDARY)
        left_panel.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(0, 8))

        left_header = tk.Frame(left_panel, bg=Theme.BG_SECONDARY)
        left_header.pack(fill=tk.X, padx=16, pady=(16, 12))

        self._widgets['input_title'] = tk.Label(left_header, text=t("panel.input_title"),
                font=(FONT_UI, 13, "bold"),
                bg=Theme.BG_SECONDARY, fg=Theme.TEXT_PRIMARY)
        self._widgets['input_title'].pack(side=tk.LEFT)

        self._widgets['btn_detect'] = ModernButton(left_header, t("panel.analyze"),
                    self.analyze_text, width=90, height=36, font_size=11, accent=True)
        self._widgets['btn_detect'].pack(side=tk.RIGHT)

        self._widgets['lang_indicator'] = tk.Label(left_header, text="",
                font=(FONT_UI, 10),
                bg=Theme.BG_SECONDARY, fg=Theme.WARNING)
        self._widgets['lang_indicator'].pack(side=tk.RIGHT, padx=12)

        # Input text area with dark styling
        input_frame = tk.Frame(left_panel, bg=Theme.BG_INPUT)
        input_frame.pack(fill=tk.BOTH, expand=True, padx=16, pady=(0, 16))

        self.input_text = tk.Text(input_frame, wrap=tk.WORD,
                                 font=(FONT_MONO, self.font_size),
                                 bg=Theme.BG_INPUT, fg=Theme.TEXT_PRIMARY,
                                 insertbackground=Theme.TEXT_PRIMARY,
                                 relief=tk.FLAT, borderwidth=0, padx=12, pady=12,
                                 selectbackground=Theme.PRIMARY,
                                 selectforeground="white")
        self.input_text.pack(fill=tk.BOTH, expand=True)

        scrollbar_left = tk.Scrollbar(self.input_text, bg=Theme.BG_TERTIARY,
                                     troughcolor=Theme.BG_INPUT)
        scrollbar_left.pack(side=tk.RIGHT, fill=tk.Y)
        self.input_text.config(yscrollcommand=scrollbar_left.set)
        scrollbar_left.config(command=self.input_text.yview)

        # Ctrl+scroll wheel zoom
        def _on_ctrl_wheel_input(event):
            if event.state & 0x4:
                if event.delta > 0:
                    self.increase_font()
                else:
                    self.decrease_font()
                return "break"
        self.input_text.bind("<MouseWheel>", _on_ctrl_wheel_input)

        # Right panel: Results
        right_panel = tk.Frame(content, bg=Theme.BG_SECONDARY)
        right_panel.pack(side=tk.RIGHT, fill=tk.BOTH, expand=True, padx=(8, 0))

        right_header = tk.Frame(right_panel, bg=Theme.BG_SECONDARY)
        right_header.pack(fill=tk.X, padx=16, pady=(16, 12))

        self._widgets['result_title'] = tk.Label(right_header, text=t("panel.result_title"),
                font=(FONT_UI, 13, "bold"),
                bg=Theme.BG_SECONDARY, fg=Theme.TEXT_PRIMARY)
        self._widgets['result_title'].pack(side=tk.LEFT)

        # Legend with colored dots
        legend_frame = tk.Frame(right_header, bg=Theme.BG_SECONDARY)
        legend_frame.pack(side=tk.RIGHT)

        self._legend_labels = []
        legend_colors = [Theme.SUCCESS, Theme.NORMAL_COLOR, Theme.WARNING, Theme.DANGER]
        legend_keys = ["stats.common_short", "stats.normal_short", "stats.rare_short", "stats.unseen_short"]

        for color, key in zip(legend_colors, legend_keys):
            tk.Label(legend_frame, text="●", font=(FONT_UI, 10),
                    bg=Theme.BG_SECONDARY, fg=color).pack(side=tk.LEFT, padx=(10, 3))
            lbl = tk.Label(legend_frame, text=t(key), font=(FONT_UI, 10),
                    bg=Theme.BG_SECONDARY, fg=Theme.TEXT_SECONDARY)
            lbl.pack(side=tk.LEFT)
            self._legend_labels.append((lbl, key))

        # Result text area
        result_frame = tk.Frame(right_panel, bg=Theme.BG_INPUT)
        result_frame.pack(fill=tk.BOTH, expand=True, padx=16, pady=(0, 16))

        self.result_text = tk.Text(result_frame, wrap=tk.WORD,
                                  font=(FONT_MONO, self.font_size),
                                  bg=Theme.BG_INPUT, fg=Theme.TEXT_PRIMARY,
                                  relief=tk.FLAT, borderwidth=0, padx=12, pady=12,
                                  state=tk.DISABLED,
                                  selectbackground=Theme.PRIMARY,
                                  selectforeground="white")
        self.result_text.pack(fill=tk.BOTH, expand=True)

        self.result_text.tag_configure("common", foreground=Theme.SUCCESS)
        self.result_text.tag_configure("normal", foreground=Theme.NORMAL_COLOR)
        self.result_text.tag_configure("rare", foreground=Theme.WARNING,
                                       font=(FONT_MONO, self.font_size, "bold"))
        self.result_text.tag_configure("unseen", foreground=Theme.DANGER,
                                       font=(FONT_MONO, self.font_size, "bold"))

        scrollbar_right = tk.Scrollbar(self.result_text, bg=Theme.BG_TERTIARY,
                                      troughcolor=Theme.BG_INPUT)
        scrollbar_right.pack(side=tk.RIGHT, fill=tk.Y)
        self.result_text.config(yscrollcommand=scrollbar_right.set)
        scrollbar_right.config(command=self.result_text.yview)

        # Ctrl+scroll wheel zoom
        def _on_ctrl_wheel_result(event):
            if event.state & 0x4:
                if event.delta > 0:
                    self.increase_font()
                else:
                    self.decrease_font()
                return "break"
        self.result_text.bind("<MouseWheel>", _on_ctrl_wheel_result)

        # ========== Statistics Panel (Bottom) ==========
        stats_panel = tk.Frame(self.root, bg=Theme.BG_SECONDARY, height=200)
        stats_panel.pack(fill=tk.X, padx=16, pady=(8, 16))
        stats_panel.pack_propagate(False)

        stats_header = tk.Frame(stats_panel, bg=Theme.BG_SECONDARY)
        stats_header.pack(fill=tk.X, padx=16, pady=(12, 8))

        self._widgets['stats_title'] = tk.Label(stats_header, text=t("stats.panel_title"),
                font=(FONT_UI, 13, "bold"),
                bg=Theme.BG_SECONDARY, fg=Theme.TEXT_PRIMARY)
        self._widgets['stats_title'].pack(side=tk.LEFT)

        self._widgets['stats_hint'] = tk.Label(stats_header, text=t("stats.panel_hint"),
                font=(FONT_UI, 10),
                bg=Theme.BG_SECONDARY, fg=Theme.TEXT_MUTED)
        self._widgets['stats_hint'].pack(side=tk.LEFT, padx=16)

        # Table container
        table_container = tk.Frame(stats_panel, bg=Theme.BG_TERTIARY)
        table_container.pack(fill=tk.BOTH, expand=True, padx=16, pady=(0, 16))

        # Table header
        header_row = tk.Frame(table_container, bg=Theme.BG_TERTIARY)
        header_row.pack(fill=tk.X)

        col_widths = [20, 14, 12, 14, 14]
        header_keys = ["stats.word", "stats.doc_freq", "stats.doc_pct",
                       "stats.total_freq", "stats.status"]

        self._table_headers = []
        for key, width in zip(header_keys, col_widths):
            lbl = tk.Label(header_row, text=t(key), font=(FONT_UI, 11, "bold"),
                    bg=Theme.BG_TERTIARY, fg=Theme.TEXT_PRIMARY,
                    width=width, anchor='w')
            lbl.pack(side=tk.LEFT, padx=10, pady=8)
            self._table_headers.append((lbl, key))

        # Scrollable table body
        table_body = tk.Frame(table_container, bg=Theme.BG_INPUT)
        table_body.pack(fill=tk.BOTH, expand=True)

        self.stats_canvas = tk.Canvas(table_body, bg=Theme.BG_INPUT, highlightthickness=0)
        stats_scrollbar = tk.Scrollbar(table_body, orient="vertical",
                                       command=self.stats_canvas.yview,
                                       bg=Theme.BG_TERTIARY, troughcolor=Theme.BG_INPUT)
        self.stats_frame = tk.Frame(self.stats_canvas, bg=Theme.BG_INPUT)

        self.stats_frame.bind("<Configure>",
            lambda e: self.stats_canvas.configure(scrollregion=self.stats_canvas.bbox("all")))

        self.stats_canvas.create_window((0, 0), window=self.stats_frame, anchor="nw")
        self.stats_canvas.configure(yscrollcommand=stats_scrollbar.set)

        self.stats_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        stats_scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        # Mouse wheel scrolling
        def _on_mousewheel(event):
            self.stats_canvas.yview_scroll(int(-1*(event.delta/120)), "units")
        self.stats_canvas.bind("<MouseWheel>", _on_mousewheel)
        self.stats_frame.bind("<MouseWheel>", _on_mousewheel)

        # Placeholder
        self._widgets['stats_placeholder'] = tk.Label(self.stats_frame,
                text=t("stats.placeholder"),
                font=(FONT_UI, 11),
                bg=Theme.BG_INPUT, fg=Theme.TEXT_MUTED)
        self._widgets['stats_placeholder'].pack(pady=30)

    def on_language_change(self, event=None):
        new_lang = self.lang_var.get()
        set_language(new_lang)
        self.settings.set('language', new_lang)

    def refresh_ui(self):
        self.root.title(f"{t('app.title')} v{VERSION}")
        self._widgets['title_label'].config(text=t('app.title'))
        self._widgets['btn_load_pdf'].set_text(t("toolbar.load_pdf"))
        self._widgets['btn_show_vocab'].set_text(t("toolbar.show_vocab"))
        self._widgets['btn_detect'].set_text(t("panel.analyze"))
        self._widgets['input_title'].config(text=t("panel.input_title"))
        self._widgets['result_title'].config(text=t("panel.result_title"))
        self._widgets['stats_title'].config(text=t("stats.panel_title"))
        self._widgets['stats_hint'].config(text=t("stats.panel_hint"))
        self._widgets['stats_placeholder'].config(text=t("stats.placeholder"))

        # Update legend labels
        for lbl, key in self._legend_labels:
            lbl.config(text=t(key))

        # Update table header labels
        for lbl, key in self._table_headers:
            lbl.config(text=t(key))

        # Refresh stats table if data exists
        if self.last_word_stats:
            self.update_stats_table()

        if self.corpus.doc_count > 0:
            self.status_label.config(
                text=t("status.ready",
                      pdf_count=self.corpus.doc_count,
                      word_count=f"{len(self.corpus.word_doc_freq):,}"),
                fg=Theme.SUCCESS
            )

    def increase_font(self):
        if self.font_size < 18:
            self.font_size += 1
            self.settings.set('font_size', self.font_size)
            self.update_font_size()

    def decrease_font(self):
        if self.font_size > 9:
            self.font_size -= 1
            self.settings.set('font_size', self.font_size)
            self.update_font_size()

    def update_font_size(self):
        self.font_label.config(text=str(self.font_size))
        self.input_text.config(font=(FONT_MONO, self.font_size))
        self.result_text.config(font=(FONT_MONO, self.font_size))
        for tag in ["rare", "unseen"]:
            self.result_text.tag_configure(tag, font=(FONT_MONO, self.font_size, "bold"))
        self.result_text.tag_configure("common", font=(FONT_MONO, self.font_size))
        self.result_text.tag_configure("normal", font=(FONT_MONO, self.font_size))

    def toggle_theme(self):
        """Toggle between light and dark theme"""
        self.dark_mode = not self.dark_mode
        self.settings.set('dark_mode', self.dark_mode)
        Theme.set_mode(self.dark_mode)

        # Save current input text
        input_content = self.input_text.get("1.0", tk.END)

        # Destroy all widgets and rebuild UI
        for widget in self.root.winfo_children():
            widget.destroy()

        self._widgets = {}
        self.create_ui()

        # Restore input text
        self.input_text.insert("1.0", input_content.strip())

        # Update status
        self.load_vocabulary()

    def update_library_dropdown(self):
        """Update library dropdown with available libraries"""
        libraries = self.library_manager.list_libraries()
        lib_names = [lib['name'] for lib in libraries]
        self._widgets['lib_dropdown']['values'] = lib_names
        if self.current_library and self.current_library in lib_names:
            self.library_var.set(self.current_library)
        elif lib_names:
            self.library_var.set(lib_names[0])
        else:
            self.library_var.set("")

    def on_library_change(self, event=None):
        """Handle library selection change"""
        selected = self.library_var.get()
        if selected and selected != self.current_library:
            self.current_library = selected
            self.settings.set('current_library', selected)
            library_path = self.library_manager.get_library_path(selected)
            self.corpus = AcademicCorpus(library_path)
            self.load_vocabulary()
            self.last_word_stats = []
            self.update_stats_table()

    def create_new_library(self):
        """Create a new library"""
        from tkinter import simpledialog
        name = simpledialog.askstring(
            t("library.new_title"),
            t("library.new_prompt"),
            parent=self.root
        )
        if name:
            name = name.strip()
            if name and not self.library_manager.library_exists(name):
                path = self.library_manager.create_library(name)
                self.current_library = name
                self.settings.set('current_library', name)
                self.corpus = AcademicCorpus(path)
                self.update_library_dropdown()
                self.load_vocabulary()
                self.last_word_stats = []
                self.update_stats_table()
            elif self.library_manager.library_exists(name):
                messagebox.showwarning(t("msg.warning"), t("library.exists"))

    def delete_current_library(self):
        """Delete the currently selected library"""
        if not self.current_library:
            return
        if messagebox.askyesno(t("library.delete_title"),
                              t("library.delete_confirm", name=self.current_library)):
            self.library_manager.delete_library(self.current_library)
            self.current_library = None
            self.settings.set('current_library', None)
            self.corpus = AcademicCorpus(None)
            self.update_library_dropdown()
            # Select first available library if any
            libraries = self.library_manager.list_libraries()
            if libraries:
                self.on_library_change()
            else:
                self.load_vocabulary()
                self.last_word_stats = []
                self.update_stats_table()

    def show_library_menu(self):
        """Show library management popup menu"""
        menu = tk.Menu(self.root, tearoff=0, font=(FONT_UI, 10),
                      bg=Theme.BG_SECONDARY, fg=Theme.TEXT_PRIMARY,
                      activebackground=Theme.PRIMARY, activeforeground="white")

        has_library = bool(self.current_library)

        menu.add_command(label=t("library.menu_rename"),
                        command=self.rename_current_library,
                        state=tk.NORMAL if has_library else tk.DISABLED)
        menu.add_command(label=t("library.menu_info"),
                        command=self.show_library_info,
                        state=tk.NORMAL if has_library else tk.DISABLED)
        menu.add_command(label=t("library.menu_open_folder"),
                        command=self.open_library_folder)
        menu.add_separator()
        menu.add_command(label=t("library.menu_clear"),
                        command=self.clear_current_library,
                        state=tk.NORMAL if has_library else tk.DISABLED)
        menu.add_command(label=t("library.menu_delete"),
                        command=self.delete_current_library,
                        state=tk.NORMAL if has_library else tk.DISABLED)

        # Show menu at button position
        btn = self._widgets['btn_lib_menu']
        x = btn.winfo_rootx()
        y = btn.winfo_rooty() + btn.winfo_height()
        menu.post(x, y)

    def rename_current_library(self):
        """Rename the current library"""
        if not self.current_library:
            return
        from tkinter import simpledialog
        new_name = simpledialog.askstring(
            t("library.rename_title"),
            t("library.rename_prompt"),
            initialvalue=self.current_library,
            parent=self.root
        )
        if new_name and new_name.strip() and new_name.strip() != self.current_library:
            new_name = new_name.strip()
            if self.library_manager.library_exists(new_name):
                messagebox.showwarning(t("msg.warning"), t("library.exists"))
                return
            if self.library_manager.rename_library(self.current_library, new_name):
                self.current_library = new_name
                self.settings.set('current_library', new_name)
                self.corpus.library_path = self.library_manager.get_library_path(new_name)
                self.update_library_dropdown()

    def show_library_info(self):
        """Show library information dialog"""
        if not self.current_library:
            return
        info = self.library_manager.get_library_info(self.current_library)
        msg = t("library.info_message",
               name=info['name'],
               doc_count=info['doc_count'],
               word_count=f"{info['word_count']:,}",
               path=info['path'])
        messagebox.showinfo(t("library.info_title"), msg)

    def open_library_folder(self):
        """Open the libraries folder in file explorer"""
        folder = self.library_manager.libraries_dir
        if os.path.exists(folder):
            os.startfile(folder)
        else:
            os.makedirs(folder, exist_ok=True)
            os.startfile(folder)

    def clear_current_library(self):
        """Clear all data in the current library"""
        if not self.current_library:
            return
        if messagebox.askyesno(t("library.clear_title"),
                              t("library.clear_confirm", name=self.current_library)):
            self.library_manager.clear_library(self.current_library)
            self.corpus = AcademicCorpus(self.library_manager.get_library_path(self.current_library))
            self.load_vocabulary()
            self.last_word_stats = []
            self.update_stats_table()

    def load_vocabulary(self):
        if self.corpus.load_vocabulary():
            self.status_label.config(
                text=t("status.ready",
                      pdf_count=self.corpus.doc_count,
                      word_count=f"{len(self.corpus.word_doc_freq):,}"),
                fg=Theme.SUCCESS
            )
            # Clear onboarding text if present
            current_text = self.input_text.get("1.0", tk.END).strip()
            if current_text.startswith("📚") or current_text.startswith("Welcome"):
                self.input_text.delete("1.0", tk.END)
        else:
            # Show onboarding guidance for first-time users
            if not self.current_library:
                self.status_label.config(text=t("status.not_found"), fg=Theme.WARNING)
                self.show_onboarding()
            else:
                self.status_label.config(
                    text=t("status.ready", pdf_count=0, word_count="0"),
                    fg=Theme.WARNING
                )

    def show_onboarding(self):
        """Show onboarding instructions in input text area"""
        self.input_text.delete("1.0", tk.END)
        onboarding = t("onboarding.text")
        self.input_text.insert("1.0", onboarding)
        self.input_text.config(fg=Theme.TEXT_MUTED)

    def select_pdf_folder(self):
        # Check if a library is selected first
        if not self.current_library:
            if messagebox.askyesno(t("library.new_title"), t("library.create_first")):
                self.create_new_library()
                if not self.current_library:
                    return  # User cancelled creating library
            else:
                return

        folder = filedialog.askdirectory(title=t("msg.select_folder"))
        if not folder:
            return

        def process():
            def progress_callback(current, total, filename):
                self.status_label.config(
                    text=t("status.processing", current=current, total=total,
                          filename=filename[:25]),
                    fg=Theme.WARNING
                )
                self.root.update_idletasks()

            self.status_label.config(text=t("status.starting"), fg=Theme.WARNING)
            count = self.corpus.process_pdf_folder(folder, progress_callback)
            self.corpus.save_vocabulary()

            self.status_label.config(
                text=t("status.complete", count=count,
                      word_count=f"{len(self.corpus.word_doc_freq):,}"),
                fg=Theme.SUCCESS
            )
            messagebox.showinfo(t("msg.complete"),
                              t("msg.success_process", count=count))

        threading.Thread(target=process).start()

    def show_vocabulary(self):
        if self.corpus.doc_count == 0:
            messagebox.showwarning(t("msg.warning"), t("msg.load_vocab_first"))
            return

        words = self.corpus.get_common_words(300)
        content = t("vocab.header", doc_count=self.corpus.doc_count)
        content += "\n\n"

        for word, doc_freq in words[:100]:
            pct = doc_freq / self.corpus.doc_count * 100
            content += f"{word}: {doc_freq}/{self.corpus.doc_count} ({pct:.0f}%)\n"

        dialog = tk.Toplevel(self.root)
        dialog.title(t("toolbar.show_vocab"))
        dialog.geometry("550x450")
        dialog.configure(bg=Theme.BG_PRIMARY)

        text = tk.Text(dialog, wrap=tk.WORD, font=(FONT_MONO, 11),
                      bg=Theme.BG_INPUT, fg=Theme.TEXT_PRIMARY, padx=12, pady=12,
                      relief=tk.FLAT)
        text.pack(fill=tk.BOTH, expand=True, padx=16, pady=16)
        text.insert("1.0", content)

    def update_stats_table(self):
        """Update the statistics table with word stats sorted by rarity"""
        # Clear existing rows
        for widget in self.stats_frame.winfo_children():
            widget.destroy()

        if not self.last_word_stats:
            self._widgets['stats_placeholder'] = tk.Label(self.stats_frame,
                    text=t("stats.placeholder"),
                    font=(FONT_UI, 11),
                    bg=Theme.BG_INPUT, fg=Theme.TEXT_MUTED)
            self._widgets['stats_placeholder'].pack(pady=30)
            return

        col_widths = [20, 14, 12, 14, 14]
        color_map = {
            'common': Theme.SUCCESS,
            'normal': Theme.NORMAL_COLOR,
            'rare': Theme.WARNING,
            'unseen': Theme.DANGER
        }
        status_map = {
            'common': t("stats.common_short"),
            'normal': t("stats.normal_short"),
            'rare': t("stats.rare_short"),
            'unseen': t("stats.unseen_short")
        }

        for i, stats in enumerate(self.last_word_stats):
            row_bg = Theme.BG_INPUT if i % 2 == 0 else Theme.BG_SECONDARY
            row = tk.Frame(self.stats_frame, bg=row_bg)
            row.pack(fill=tk.X)
            row.bind("<MouseWheel>", lambda e: self.stats_canvas.yview_scroll(int(-1*(e.delta/120)), "units"))

            classification = self.corpus.classify_word(stats['word'])
            color = color_map.get(classification, Theme.TEXT_PRIMARY)

            # Word
            lbl = tk.Label(row, text=stats['word'], font=(FONT_MONO, 11),
                    bg=row_bg, fg=color, width=col_widths[0], anchor='w')
            lbl.pack(side=tk.LEFT, padx=10, pady=6)
            lbl.bind("<MouseWheel>", lambda e: self.stats_canvas.yview_scroll(int(-1*(e.delta/120)), "units"))

            # Doc frequency
            doc_freq_text = f"{stats['doc_freq']}/{stats['docs_total']}"
            lbl = tk.Label(row, text=doc_freq_text, font=(FONT_MONO, 11),
                    bg=row_bg, fg=color, width=col_widths[1], anchor='w')
            lbl.pack(side=tk.LEFT, padx=10, pady=6)
            lbl.bind("<MouseWheel>", lambda e: self.stats_canvas.yview_scroll(int(-1*(e.delta/120)), "units"))

            # Doc percentage
            pct_text = f"{stats['doc_percent']:.1f}%"
            lbl = tk.Label(row, text=pct_text, font=(FONT_MONO, 11),
                    bg=row_bg, fg=color, width=col_widths[2], anchor='w')
            lbl.pack(side=tk.LEFT, padx=10, pady=6)
            lbl.bind("<MouseWheel>", lambda e: self.stats_canvas.yview_scroll(int(-1*(e.delta/120)), "units"))

            # Total frequency
            lbl = tk.Label(row, text=str(stats['total_freq']), font=(FONT_MONO, 11),
                    bg=row_bg, fg=color, width=col_widths[3], anchor='w')
            lbl.pack(side=tk.LEFT, padx=10, pady=6)
            lbl.bind("<MouseWheel>", lambda e: self.stats_canvas.yview_scroll(int(-1*(e.delta/120)), "units"))

            # Status
            lbl = tk.Label(row, text=status_map.get(classification, ''), font=(FONT_UI, 10),
                    bg=row_bg, fg=color, width=col_widths[4], anchor='w')
            lbl.pack(side=tk.LEFT, padx=10, pady=6)
            lbl.bind("<MouseWheel>", lambda e: self.stats_canvas.yview_scroll(int(-1*(e.delta/120)), "units"))

    def analyze_text(self):
        text = self.input_text.get("1.0", tk.END).strip()
        if not text:
            messagebox.showwarning(t("msg.warning"), t("msg.enter_text"))
            return

        if self.corpus.doc_count == 0:
            messagebox.showwarning(t("msg.warning"), t("msg.load_vocab_first"))
            return

        self.detected_language = LanguageDetector.detect(text)
        lang_display = {
            'en': t("lang.english"),
            'zh': t("lang.chinese"),
            'mixed': t("lang.mixed")
        }.get(self.detected_language, self.detected_language)

        self._widgets['lang_indicator'].config(
            text=t("lang.detected", lang=lang_display)
        )

        self.result_text.config(state=tk.NORMAL)
        self.result_text.delete("1.0", tk.END)

        words = re.findall(r'\b[a-zA-Z]+\b|\S+|\s+', text)

        word_stats_dict = {}
        total_words = 0
        stats_by_type = {'common': 0, 'normal': 0, 'rare': 0, 'unseen': 0}

        for word in words:
            if re.match(r'^[a-zA-Z]+$', word):
                lower_word = word.lower()

                if lower_word in STOP_WORDS or len(lower_word) < 3:
                    self.result_text.insert(tk.END, word, "normal")
                    continue

                total_words += 1
                classification = self.corpus.classify_word(lower_word)
                stats_by_type[classification] += 1

                if lower_word not in word_stats_dict:
                    word_stats_dict[lower_word] = self.corpus.get_word_stats(lower_word)

                self.result_text.insert(tk.END, word, classification)
            else:
                self.result_text.insert(tk.END, word, "normal")

        self.result_text.config(state=tk.DISABLED)

        # Sort by doc_percent ascending (rarest first)
        self.last_word_stats = sorted(
            word_stats_dict.values(),
            key=lambda x: x['doc_percent']
        )

        # Update stats table
        self.update_stats_table()

        # Update stats title with summary
        unseen_count = stats_by_type['unseen']
        rare_count = stats_by_type['rare']
        self._widgets['stats_hint'].config(
            text=t("stats.summary", total=len(word_stats_dict),
                   unseen=unseen_count, rare=rare_count),
            fg=Theme.DANGER if unseen_count > 0 else Theme.SUCCESS
        )


def main():
    root = tk.Tk()
    try:
        from ctypes import windll
        windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass
    app = ModernApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
