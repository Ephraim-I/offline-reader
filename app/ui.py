import json
import logging
import queue
import sqlite3
import threading
import time
import tkinter as tk
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from tkinter import filedialog, messagebox

import pymupdf

from app.reader_controller import ReaderController
from app.popup import show_popup
from documents.manager import DocumentOpenError
from documents.models import PDFDocument, ReadingContext, TextDocument
from notes import NotesStore, SourceReference
from notes.store import fingerprint_file


def _pdf_wheel_direction(event):
    """Return -1 for up, +1 for down across Linux and MouseWheel platforms."""
    number = getattr(event, "num", None)
    if number == 4:
        return -1
    if number == 5:
        return 1
    delta = getattr(event, "delta", 0) or 0
    if delta > 0:
        return -1
    if delta < 0:
        return 1
    return 0


def _pdf_boundary_transition(direction, yview):
    """Return +1 at the bottom when scrolling down, -1 at the top when scrolling up."""
    if direction > 0 and yview[1] >= 0.999:
        return 1
    if direction < 0 and yview[0] <= 0.001:
        return -1
    return 0


def _layout_profile_for_width(width):
    """Choose a compact UI profile from the usable window width.

    Tk reports the live window size, which is more useful than an advertised
    monitor resolution when a reader is tiled or used with display scaling.
    """
    if width < 920:
        return "compact"
    if width < 1120:
        return "standard"
    return "expanded"


def _reading_column_metrics(available_width):
    """Return a comfortable reading width and safe inner gutter.

    The old fixed minimum width and 42 px gutter could consume most of a tiled
    window. Keep a readable measure on wide displays, but let the canvas shrink
    gracefully on constrained laptops.
    """
    width = max(280, min(920, available_width - 32))
    gutter = 18 if width < 480 else 28 if width < 680 else 42
    return width, gutter


def _sidebar_recent_label(path, limit=28):
    """Keep a recent-document row legible without losing the full path in File."""
    name = Path(path).name
    return name if len(name) <= limit else f"{name[:limit - 1].rstrip()}…"


class ReaderUI:
    THEMES = {
        "light": {
            "BACKGROUND": "#f5f6f8",
            "PANEL": "#ffffff",
            "EDITOR": "#fffefa",
            "READER_TEXT": "#20252b",
            "READER_MUTED": "#68717c",
            "TEXT": "#252b32",
            "MUTED": "#68717c",
            "ACCENT": "#b9e4d7",
            "BORDER": "#dfe4e8",
            "CONTROL": "#eef1f4",
            "PRIMARY_TEXT": "#183c33",
            "PRIMARY_ACTIVE": "#a1d8c8",
            "CONTROL_ACTIVE": "#e1e7eb",
            "FOCUS": "#27856f",
            "SELECT": "#c9b47c",
            "SELECT_TEXT": "#201d17",
            "SCROLL_TRACK": "#e4e8ec",
            "SCROLL_ACTIVE": "#c8d0d8",
            "PDF_SURFACE": "#e9edf0",
            "SEARCH_HIGHLIGHT": "#e6d59e",
            "HIGHLIGHT_TEXT": "#30291c",
            "PDF_HIGHLIGHT": "#f6d365",
            "PDF_HIGHLIGHT_BORDER": "#d79c24",
            "PDF_SELECTION": "#f5c451",
            "TOOLTIP_BACKGROUND": "#343b43",
            "TOOLTIP_TEXT": "#f8fafc",
        },
        "dark": {
            "BACKGROUND": "#0d131a",
            "PANEL": "#151e28",
            "EDITOR": "#111a23",
            "READER_TEXT": "#ecf2f8",
            "READER_MUTED": "#aab8c6",
            "TEXT": "#e2eaf2",
            "MUTED": "#aab8c6",
            "ACCENT": "#39b99b",
            "BORDER": "#2a3847",
            "CONTROL": "#212d3a",
            "PRIMARY_TEXT": "#06231e",
            "PRIMARY_ACTIVE": "#59cdb2",
            "CONTROL_ACTIVE": "#2d3b4a",
            "FOCUS": "#7ae0c7",
            "SELECT": "#76623a",
            "SELECT_TEXT": "#f3e8cc",
            "SCROLL_TRACK": "#27333f",
            "SCROLL_ACTIVE": "#425160",
            "PDF_SURFACE": "#101820",
            "SEARCH_HIGHLIGHT": "#655632",
            "HIGHLIGHT_TEXT": "#f0d9a0",
            "PDF_HIGHLIGHT": "#b58b2a",
            "PDF_HIGHLIGHT_BORDER": "#e4b54a",
            "PDF_SELECTION": "#ffca62",
            "TOOLTIP_BACKGROUND": "#070b10",
            "TOOLTIP_TEXT": "#f4f7fa",
        },
    }
    THEME_COLOR_OPTIONS = (
        "background", "foreground", "activebackground", "activeforeground",
        "disabledforeground", "insertbackground", "selectbackground",
        "selectforeground", "highlightbackground", "highlightcolor",
        "troughcolor",
    )

    def __init__(self):
        self.root = tk.Tk()
        self.root.title("Offline Reader")
        self.root.geometry("1100x760")
        self.root.minsize(760, 520)
        self._fullscreen = False
        self._focus_mode = False
        self._chrome_visibility = {}
        self._sidebar_was_packed = False
        self._layout_profile = None
        self._layout_job = None
        self._sidebar_user_visible = True
        self._sidebar_auto_collapsed = False
        self._line_spacing = 1.0
        self.root.option_add("*tearOff", False)

        self.active_popup = None
        self.last_selection = None
        self._selection_request = 0
        self._meaning_results = queue.Queue()
        self._study_results = queue.Queue()
        self._study_request = 0
        self.revision_window = None
        self._quiz_results = queue.Queue()
        self._quiz_request = 0
        self.quiz_setup_window = None
        self.quiz_window = None
        self.quiz_library_window = None
        self._flashcard_results = queue.Queue()
        self._flashcard_request = 0
        self.flashcard_setup_window = None
        self.flashcard_window = None
        self.flashcard_library_window = None
        self._document_search_results = queue.Queue()
        self._search_request = 0
        self._selection_job = None
        self.settings_path = Path.home() / ".config" / "offline-reader" / "settings.json"
        self.settings = self._load_settings()
        self._notes_store = None
        self.notes_window = None
        self.annotations_window = None
        self._annotation_fingerprint = None
        saved_theme = self.settings.get("theme", "light")
        self.theme = (
            saved_theme if isinstance(saved_theme, str) and saved_theme in self.THEMES
            else "light"
        )
        self._set_theme_palette(self.theme)
        self.root.configure(bg=self.BACKGROUND)
        saved_directory = self.settings.get("last_directory", str(Path.home()))
        self.last_directory = Path(saved_directory) if isinstance(saved_directory, str) else Path.home()
        saved_recent = self.settings.get("recent_documents", [])
        self.recent_documents = [item for item in saved_recent if isinstance(item, str)] if isinstance(saved_recent, list) else []
        saved_states = self.settings.get("document_states", {})
        self.document_states = saved_states if isinstance(saved_states, dict) else {}
        try:
            self._line_spacing = max(0.7, min(1.8, float(self.settings.get("line_spacing", 1.0))))
        except (TypeError, ValueError):
            self._line_spacing = 1.0
        try:
            self.text_font_size = max(8, min(24, int(self.settings.get("text_font_size", 11))))
        except (TypeError, ValueError):
            self.text_font_size = 11
        self.controller = ReaderController()
        self.document_path = None
        self.document_type = None
        self.pdf_document: PDFDocument | None = None
        self.pdf_page_index = 0
        self.pdf_zoom = 1.25
        self.fit_width_enabled = True
        self._page_image = None
        # Rendered PNG bytes are cheap to retain compared with re-rasterizing a
        # PDF page on every navigation. Keep a small LRU to bound memory use.
        self._pdf_render_cache = OrderedDict()
        self._pdf_render_cache_limit = 5
        self._pdf_prefetch_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="pdf-prefetch")
        self._pdf_prefetch_results = queue.Queue()
        self._pdf_prefetch_inflight = set()
        self._pdf_render_document_path = None
        self._pdf_prefetch_generation = 0
        self._pdf_render_log_path = Path.home() / ".cache" / "offline-reader" / "pdf-render.log"
        self.root.after(80, self._process_pdf_prefetch_results)
        self._source_highlight_ids = []
        self._resize_job = None
        self._page_image_left = 0
        self._page_image_top = 0
        self._render_scale = 1.0
        self._pdf_selection_start = None
        self._pdf_selection_rectangle = None
        self.search_results: list = []

        self._build_menu()
        self._build_layout()
        self._refresh_recent_menu()
        self._apply_text_font_size()
        # Mouse release is the explicit selection gesture. A generic KeyRelease
        # also fires for unrelated keys and used to reopen stale selections.
        self.text.bind("<ButtonRelease-1>", self.on_select)
        self.root.bind_all("<Control-o>", lambda _event: self.open_file())
        self.root.bind_all("<Control-f>", self._focus_document_search)
        self.root.bind_all("<Control-b>", self._toggle_sidebar_shortcut)
        self.root.bind_all("<Control-Shift-d>", self._toggle_theme_shortcut)
        self.root.bind_all("<Control-Shift-D>", self._toggle_theme_shortcut)
        self.root.bind_all("<Control-Shift-n>", self._open_notes_shortcut)
        self.root.bind_all("<Control-Shift-N>", self._open_notes_shortcut)
        self.root.bind_all("<Control-Shift-b>", self._open_annotations_shortcut)
        self.root.bind_all("<Control-Shift-B>", self._open_annotations_shortcut)
        self.root.bind_all("<Control-w>", lambda _event: self.close_file())
        self.root.bind_all("<Control-plus>", lambda _event: self.zoom_in())
        self.root.bind_all("<Control-equal>", lambda _event: self.zoom_in())
        self.root.bind_all("<Control-minus>", lambda _event: self.zoom_out())
        self.root.bind_all("<Control-0>", lambda _event: self.reset_reading_zoom())
        self.root.bind_all("<Prior>", self._page_up_shortcut)
        self.root.bind_all("<Next>", self._page_down_shortcut)
        self.root.bind_all("<Home>", self._document_home_shortcut)
        self.root.bind_all("<End>", self._document_end_shortcut)
        self.root.bind_all("<Escape>", self._escape_reading_modes)
        self.root.bind_all("<F11>", self._toggle_fullscreen_shortcut)
        self.root.bind_all("<Control-Shift-f>", self._toggle_focus_mode_shortcut)
        self.root.bind_all("<Control-Shift-F>", self._toggle_focus_mode_shortcut)
        self.root.bind("<Configure>", self._schedule_adaptive_layout, add="+")
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self.root.after(50, self._process_meaning_results)
        self.root.after(50, self._process_study_results)
        self.root.after(50, self._process_quiz_results)
        self.root.after(50, self._process_flashcard_results)
        self.root.after(75, self._process_search_results)
        self.root.after_idle(lambda: self._apply_adaptive_layout(self.root.winfo_width()))

    def _button(self, parent, text, command, primary=False, **kwargs):
        options = {
            "bg": self.ACCENT if primary else self.CONTROL,
            "fg": self.PRIMARY_TEXT if primary else self.TEXT,
            "activebackground": self.PRIMARY_ACTIVE if primary else self.CONTROL_ACTIVE,
            "activeforeground": self.TEXT,
            "relief": "flat",
            "borderwidth": 0,
            "padx": 10,
            "pady": 6,
            "cursor": "hand2",
            "font": ("Segoe UI", 9, "bold"),
            # Tk buttons otherwise have almost invisible keyboard focus on some themes.
            "takefocus": True,
            "highlightthickness": 2,
            "highlightbackground": self.BACKGROUND,
            "highlightcolor": self.FOCUS,
        }
        options.update(kwargs)
        return tk.Button(
            parent,
            text=text,
            command=command,
            **options,
        )

    def _build_menu(self):
        menu = tk.Menu(self.root)
        self.root.config(menu=menu)
        file_menu = tk.Menu(menu, tearoff=0)
        menu.add_cascade(label="File", menu=file_menu)
        file_menu.add_command(label="Open…", accelerator="Ctrl+O", command=self.open_file)
        file_menu.add_command(label="Close document", accelerator="Ctrl+W", command=self.close_file)
        file_menu.add_command(
            label="My notes…",
            accelerator="Ctrl+Shift+N",
            command=self.open_notes,
        )
        file_menu.add_command(
            label="Highlights & bookmarks…",
            accelerator="Ctrl+Shift+B",
            command=self.open_annotations,
        )
        file_menu.add_command(label="My quizzes…", command=self.open_quiz_library)
        file_menu.add_command(label="My flashcards…", command=self.open_flashcard_library)
        file_menu.add_command(
            label="Review due flashcards", command=self.open_due_flashcards
        )
        self.recent_menu = tk.Menu(file_menu, tearoff=0)
        file_menu.add_cascade(label="Open recent", menu=self.recent_menu)
        file_menu.add_separator()
        view_menu = tk.Menu(menu, tearoff=0)
        menu.add_cascade(label="View", menu=view_menu)
        view_menu.add_command(label="Toggle full screen", accelerator="F11", command=self.toggle_fullscreen)
        view_menu.add_command(label="Toggle focus mode", accelerator="Ctrl+Shift+F", command=self.toggle_focus_mode)
        view_menu.add_separator()
        view_menu.add_command(label="Increase reading size", accelerator="Ctrl++", command=self.zoom_in)
        view_menu.add_command(label="Decrease reading size", accelerator="Ctrl+-", command=self.zoom_out)
        view_menu.add_command(label="Reset reading size", accelerator="Ctrl+0", command=self.reset_reading_zoom)
        view_menu.add_separator()
        view_menu.add_command(
            label=self._theme_menu_label(),
            accelerator="Ctrl+Shift+D",
            command=self.toggle_theme,
        )
        self.theme_menu_index = view_menu.index("end")
        self.menubar = menu
        self.file_menu = file_menu
        self.view_menu = view_menu
        self._style_menus()
        file_menu.add_separator()
        file_menu.add_command(label="Exit", command=self.close)

    def _theme_menu_label(self):
        return "Use dark theme" if self.theme == "light" else "Use light theme"

    def _set_theme_palette(self, theme):
        self.theme = theme
        for name, value in self.THEMES[theme].items():
            setattr(self, name, value)

    @classmethod
    def _theme_color_mapping(cls, from_theme, to_theme):
        source = cls.THEMES[from_theme]
        target = cls.THEMES[to_theme]
        return {source[name]: target[name] for name in source}

    def _style_menus(self):
        for menu in (self.menubar, self.file_menu, self.view_menu, self.recent_menu):
            menu.configure(
                bg=self.PANEL,
                fg=self.TEXT,
                activebackground=self.CONTROL_ACTIVE,
                activeforeground=self.TEXT,
                disabledforeground=self.MUTED,
                relief="flat",
                borderwidth=0,
            )

    def _recolor_widget_tree(self, widget, color_mapping):
        try:
            for option in self.THEME_COLOR_OPTIONS:
                try:
                    value = widget.cget(option)
                except tk.TclError:
                    continue
                replacement = color_mapping.get(value) if isinstance(value, str) else None
                if replacement is not None:
                    widget.configure(**{option: replacement})
            children = widget.winfo_children()
        except tk.TclError:
            return
        for child in children:
            self._recolor_widget_tree(child, color_mapping)

    def _refresh_theme_control(self):
        label = self._theme_menu_label()
        self.view_menu.entryconfigure(self.theme_menu_index, label=label)
        self.theme_button.configure(text="◐")

    def toggle_theme(self):
        previous_theme = self.theme
        next_theme = "dark" if previous_theme == "light" else "light"
        color_mapping = self._theme_color_mapping(previous_theme, next_theme)
        self._selection_request += 1
        self._close_active_popup()
        self._set_theme_palette(next_theme)
        self._recolor_widget_tree(self.root, color_mapping)
        self._style_menus()
        self._refresh_theme_control()
        if self.notes_window is not None:
            try:
                if self.notes_window.winfo_exists():
                    self._style_notes_window()
            except tk.TclError:
                self.notes_window = None
        if self.annotations_window is not None:
            try:
                if self.annotations_window.winfo_exists():
                    self._style_annotations_window()
            except tk.TclError:
                self.annotations_window = None
        if self.revision_window is not None:
            try:
                if self.revision_window.winfo_exists():
                    self._style_revision_window()
            except tk.TclError:
                self.revision_window = None
        for attribute, style_method in (
            ("quiz_setup_window", self._style_quiz_setup_window),
            ("quiz_library_window", self._style_quiz_library_window),
            ("quiz_window", self._style_quiz_window),
            ("flashcard_setup_window", self._style_flashcard_setup_window),
            ("flashcard_library_window", self._style_flashcard_library_window),
            ("flashcard_window", self._style_flashcard_window),
        ):
            window = getattr(self, attribute)
            if window is not None:
                try:
                    if window.winfo_exists():
                        style_method()
                except tk.TclError:
                    setattr(self, attribute, None)
        if self.document_type == "text":
            self.text.tag_configure(
                "search_hit",
                background=self.SEARCH_HIGHLIGHT,
                foreground=self.HIGHLIGHT_TEXT,
            )
            self._configure_saved_highlight_tag()
            self._refresh_text_annotations()
        elif self.pdf_document is not None:
            self._render_current_page(reset_view=False)
        try:
            self._save_settings()
        except OSError as error:
            self.status.set(f"Theme changed, but settings could not be saved: {error}")
        else:
            self.status.set(f"{next_theme.title()} theme enabled")

    def _toggle_theme_shortcut(self, _event=None):
        self.toggle_theme()
        return "break"

    def _build_layout(self):
        header = tk.Frame(self.root, bg=self.BACKGROUND, padx=24, pady=14)
        self.header = header
        header.pack(fill="x")
        title = tk.Frame(header, bg=self.BACKGROUND)
        title.pack(side="left", fill="x", expand=True)
        self.header_title = tk.Label(
            title, text="Offline Reader", font=("Segoe UI", 16, "bold"),
            fg=self.TEXT, bg=self.BACKGROUND,
        )
        self.header_title.pack(anchor="w")
        self.header_subtitle = tk.Label(
            title, text="A calm, private workspace for focused reading.",
            font=("Segoe UI", 10), fg=self.MUTED, bg=self.BACKGROUND,
        )
        self.header_subtitle.pack(anchor="w", pady=(3, 0))
        self.sidebar_toggle_button = self._button(
            header, "☰", self.toggle_navigation_sidebar, padx=10,
            takefocus=True,
        )
        self.sidebar_toggle_button.pack(side="left", padx=(0, 12))
        self._add_tooltip(self.sidebar_toggle_button, "Toggle navigation sidebar (Ctrl+B)")
        self.reading_zoom_out_button = self._button(header, "A−", self.zoom_out, padx=9)
        self.reading_zoom_out_button.pack(side="right", padx=(5, 0))
        self._add_tooltip(self.reading_zoom_out_button, "Decrease reading size (Ctrl+-)")
        self.reading_zoom_in_button = self._button(header, "A+", self.zoom_in, padx=9)
        self.reading_zoom_in_button.pack(side="right", padx=(5, 0))
        self._add_tooltip(self.reading_zoom_in_button, "Increase reading size (Ctrl++)")
        self.line_spacing_button = self._button(
            header, "Aa↕", self.cycle_line_spacing, padx=8,
        )
        self.line_spacing_button.pack(side="right", padx=(5, 0))
        self._add_tooltip(self.line_spacing_button, "Cycle line spacing")
        self.theme_button = self._button(
            header, "◐", self.toggle_theme, padx=9,
        )
        self.theme_button.pack(side="right", padx=(5, 0))
        self._add_tooltip(self.theme_button, self._theme_menu_label())
        self._button(header, "Open document", self.open_file, primary=True).pack(side="right", padx=(0, 8))
        self._button(header, "Focus", self.toggle_focus_mode, padx=10).pack(side="right", padx=(0, 8))
        fullscreen_button = self._button(header, "⛶", self.toggle_fullscreen, padx=9)
        fullscreen_button.pack(side="right", padx=(0, 5))
        self._add_tooltip(fullscreen_button, "Toggle full screen (F11)")

        self._build_toolbar()
        self._build_navigation_sidebar()
        document_bar = tk.Frame(self.root, bg=self.PANEL, padx=18, pady=7,
                                highlightbackground=self.BORDER, highlightthickness=1)
        self.document_bar = document_bar
        document_bar.pack(fill="x", padx=18)
        self.file_label = tk.Label(document_bar, text="No document selected", font=("Segoe UI", 9, "bold"), fg=self.TEXT, bg=self.PANEL, anchor="w")
        self.file_label.pack(fill="x")

        # Document-wide search is always easy to find, but results only appear
        # when the user searches, keeping the reading canvas uncluttered.
        self.search_panel = tk.Frame(self.root, bg=self.BACKGROUND, padx=0, pady=0)
        search_row = tk.Frame(
            self.search_panel, bg=self.PANEL, padx=12, pady=8,
            highlightbackground=self.BORDER, highlightthickness=1,
        )
        search_row.pack(fill="x")
        self.search_label = tk.Label(
            search_row, text="Find in document", bg=self.PANEL, fg=self.MUTED,
            font=("Segoe UI", 9),
        )
        self.search_label.pack(side="left", padx=(0, 10))
        self.search_entry = tk.Entry(search_row, bg=self.PANEL, fg=self.TEXT,
                                     insertbackground=self.TEXT, relief="flat",
                                     font=("Segoe UI", 10))
        self.search_entry.pack(side="left", fill="x", expand=True, ipady=5)
        self.search_entry.bind("<Return>", self._run_document_search)
        self._button(search_row, "Find", self._run_document_search, primary=True).pack(side="left", padx=(8, 0))
        self.search_clear_button = self._button(search_row, "Clear", self._clear_document_search, padx=8)
        self.search_clear_button.pack(side="left", padx=(5, 0))
        self.search_results_frame = tk.Frame(self.search_panel, bg=self.PANEL, padx=10, pady=8)
        self.search_feedback = tk.Label(
            self.search_results_frame, text="", bg=self.PANEL, fg=self.MUTED,
            font=("Segoe UI", 9), anchor="w", justify="left", wraplength=760,
        )
        self.search_feedback.pack(fill="x", pady=(0, 4))
        self.search_results_list = tk.Listbox(
            self.search_results_frame, height=5, bg=self.PANEL, fg=self.TEXT,
            selectbackground=self.ACCENT, selectforeground=self.PRIMARY_TEXT, relief="flat",
            borderwidth=0, activestyle="none", font=("Segoe UI", 9),
        )
        search_scroll = tk.Scrollbar(self.search_results_frame, orient="vertical",
                                     command=self.search_results_list.yview)
        self.search_results_list.configure(yscrollcommand=search_scroll.set)
        self.search_results_list.pack(side="left", fill="both", expand=True)
        search_scroll.pack(side="right", fill="y")
        self.search_results_list.bind("<Double-Button-1>", self._open_selected_search_result)
        self.search_results_list.bind("<Return>", self._open_selected_search_result)
        self.search_panel.pack(fill="x", padx=24, pady=(8, 0))
        self.viewer_host = tk.Frame(self.root, bg=self.BACKGROUND, padx=24, pady=16)
        self.viewer_host.pack(fill="both", expand=True)
        self._build_text_viewer()
        self._build_pdf_viewer()

        status_bar = tk.Frame(self.root, bg=self.BACKGROUND, padx=24, pady=8)
        self.status_bar = status_bar
        status_bar.pack(fill="x")
        self.status = tk.StringVar(value="Ready | Open a TXT or PDF document")
        tk.Label(status_bar, textvariable=self.status, font=("Segoe UI", 9), fg=self.MUTED, bg=self.BACKGROUND, anchor="w").pack(fill="x")

    def _build_navigation_sidebar(self):
        """Build a lightweight navigation rail shared by TXT and PDF reading."""
        self.navigation_sidebar = tk.Frame(
            self.root, bg=self.PANEL, width=248,
            highlightbackground=self.BORDER, highlightthickness=1,
        )
        self.navigation_sidebar.pack(side="left", fill="y", before=self.header)
        self.navigation_sidebar.pack_propagate(False)
        tk.Label(
            self.navigation_sidebar, text="WORKSPACE", bg=self.PANEL, fg=self.MUTED,
            font=("Segoe UI", 9, "bold"), anchor="w", padx=18, pady=18,
        ).pack(fill="x")
        self._button(
            self.navigation_sidebar, "+  Open document", self.open_file, primary=True,
            anchor="w", padx=14, pady=9,
        ).pack(fill="x", padx=12, pady=(0, 10))
        self._button(
            self.navigation_sidebar, "⌕  Search document",
            lambda: self.search_entry.focus_set(), anchor="w", padx=14, pady=8,
        ).pack(fill="x", padx=12)
        tk.Label(
            self.navigation_sidebar, text="STUDY", bg=self.PANEL, fg=self.MUTED,
            font=("Segoe UI", 9, "bold"), anchor="w", padx=18, pady=14,
        ).pack(fill="x")
        self._button(
            self.navigation_sidebar, "▤  Notes",
            self.open_notes, anchor="w", padx=14, pady=8,
        ).pack(fill="x", padx=12)
        self._button(
            self.navigation_sidebar, "✎  Highlights",
            self.open_annotations, anchor="w", padx=14, pady=8,
        ).pack(fill="x", padx=12)
        self._button(
            self.navigation_sidebar, "✓  Quizzes",
            self.open_quiz_library, anchor="w", padx=14, pady=8,
        ).pack(fill="x", padx=12)
        self._button(
            self.navigation_sidebar, "▣  Flashcards",
            self.open_flashcard_library, anchor="w", padx=14, pady=8,
        ).pack(fill="x", padx=12)
        self._button(
            self.navigation_sidebar, "▶  Review due",
            self.open_due_flashcards, anchor="w", padx=14, pady=8,
        ).pack(fill="x", padx=12)
        tk.Label(
            self.navigation_sidebar, text="RECENT DOCUMENTS", bg=self.PANEL, fg=self.MUTED,
            font=("Segoe UI", 9, "bold"), anchor="w", padx=18, pady=14,
        ).pack(fill="x")
        self.recent_documents_list = tk.Listbox(
            self.navigation_sidebar, bg=self.PANEL, fg=self.TEXT,
            selectbackground=self.ACCENT, selectforeground=self.TEXT,
            relief="flat", borderwidth=0, activestyle="none",
            font=("Segoe UI", 9), highlightthickness=0,
            selectmode="browse", height=8,
        )
        self.recent_documents_list.pack(fill="both", expand=True, padx=12, pady=(0, 12))
        self.recent_documents_list.bind("<Double-Button-1>", self._open_recent_sidebar_item)
        self.recent_documents_list.bind("<Return>", self._open_recent_sidebar_item)
        self._refresh_recent_sidebar()
        self._navigation_sidebar_visible = True

    def _focus_document_search(self, _event=None):
        """Focus and select the search query so Ctrl+F is immediately useful."""
        self.search_entry.focus_set()
        self.search_entry.selection_range(0, tk.END)
        return "break"

    def _toggle_sidebar_shortcut(self, _event=None):
        self.toggle_navigation_sidebar()
        return "break"

    def _add_tooltip(self, widget, message):
        """Attach a small native Tk tooltip without adding a third-party dependency."""
        state = {"window": None, "after_id": None}

        def hide(_event=None):
            if state["after_id"] is not None:
                try:
                    widget.after_cancel(state["after_id"])
                except tk.TclError:
                    pass
                state["after_id"] = None
            if state["window"] is not None:
                try:
                    state["window"].destroy()
                except tk.TclError:
                    pass
                state["window"] = None

        def show():
            state["after_id"] = None
            if not widget.winfo_exists() or not widget.winfo_ismapped():
                return
            try:
                x = widget.winfo_rootx()
                y = widget.winfo_rooty() + widget.winfo_height() + 5
                tip = tk.Toplevel(widget)
                tip.wm_overrideredirect(True)
                tip.wm_geometry(f"+{x}+{y}")
                label = tk.Label(
                    tip, text=message, bg=self.TOOLTIP_BACKGROUND, fg=self.TOOLTIP_TEXT,
                    padx=8, pady=5, font=("Segoe UI", 9),
                    relief="flat", borderwidth=0,
                )
                label.pack()
                state["window"] = tip
            except tk.TclError:
                state["window"] = None

        widget.bind("<Enter>", lambda _event: state.update(after_id=widget.after(550, show)), add="+")
        widget.bind("<Leave>", hide, add="+")
        widget.bind("<ButtonPress>", hide, add="+")
        widget.bind("<FocusOut>", hide, add="+")

    def toggle_navigation_sidebar(self):
        """Show or hide the navigation sidebar without changing document state."""
        if self.navigation_sidebar.winfo_manager():
            self.navigation_sidebar.pack_forget()
            self._navigation_sidebar_visible = False
            self._sidebar_user_visible = False
            self.sidebar_toggle_button.config(text="☰")
        else:
            self.navigation_sidebar.pack(side="left", fill="y", before=self.header)
            self._navigation_sidebar_visible = True
            self._sidebar_user_visible = True
            self._sidebar_auto_collapsed = False
            self.sidebar_toggle_button.config(text="☰")
        self.root.after_idle(self._resize_reading_surface)
        return "break"

    def _schedule_adaptive_layout(self, event):
        """Debounce resize events so PDF rendering is not churned while dragging."""
        if event.widget is not self.root:
            return
        if self._layout_job is not None:
            try:
                self.root.after_cancel(self._layout_job)
            except tk.TclError:
                pass
        width = event.width
        self._layout_job = self.root.after(
            80, lambda: self._apply_adaptive_layout(width)
        )

    def _set_header_control_visible(self, widget, visible, **pack_options):
        """Keep compact-mode controls reachable through existing menus/shortcuts."""
        is_visible = bool(widget.winfo_manager())
        if visible and not is_visible:
            widget.pack(**pack_options)
        elif not visible and is_visible:
            widget.pack_forget()

    def _apply_adaptive_layout(self, width):
        """Adapt surrounding chrome while keeping the reading surface stable."""
        self._layout_job = None
        profile = _layout_profile_for_width(width)
        if profile == self._layout_profile:
            return
        self._layout_profile = profile
        compact = profile == "compact"

        self.header.configure(padx=12 if compact else 24, pady=8 if compact else 14)
        self._set_header_control_visible(
            self.header_subtitle, not compact, anchor="w", pady=(3, 0)
        )
        # These actions remain in View and on their established keyboard
        # shortcuts, so compact mode does not remove functionality.
        self._set_header_control_visible(
            self.line_spacing_button, not compact, side="right", padx=(5, 0)
        )
        self._set_header_control_visible(
            self.reading_zoom_in_button, not compact, side="right", padx=(5, 0)
        )
        self._set_header_control_visible(
            self.reading_zoom_out_button, not compact, side="right", padx=(5, 0)
        )
        self._set_header_control_visible(
            self.search_label, not compact, side="left", padx=(0, 10),
            before=self.search_entry,
        )

        if self._focus_mode:
            return
        if compact and self.navigation_sidebar.winfo_manager():
            self.navigation_sidebar.pack_forget()
            self._navigation_sidebar_visible = False
            self._sidebar_auto_collapsed = True
        elif (
            not compact
            and self._sidebar_auto_collapsed
            and self._sidebar_user_visible
            and not self.navigation_sidebar.winfo_manager()
        ):
            self.navigation_sidebar.pack(side="left", fill="y", before=self.header)
            self._navigation_sidebar_visible = True
            self._sidebar_auto_collapsed = False
        self.root.after_idle(self._resize_reading_surface)

    def _refresh_recent_sidebar(self):
        if not hasattr(self, "recent_documents_list"):
            return
        self.recent_documents_list.delete(0, tk.END)
        for item in self.recent_documents:
            path = Path(item)
            if path.is_file():
                self.recent_documents_list.insert(tk.END, _sidebar_recent_label(path))
        if self.recent_documents_list.size() == 0:
            self.recent_documents_list.insert(tk.END, "No recent documents")

    def _open_recent_sidebar_item(self, _event=None):
        selected = self.recent_documents_list.curselection()
        if not selected:
            return "break"
        index = selected[0]
        existing = [item for item in self.recent_documents if Path(item).is_file()]
        if index < len(existing):
            self._open_path(Path(existing[index]))
        return "break"

    def toggle_fullscreen(self):
        """Toggle native full-screen while preserving the current document view."""
        self._fullscreen = not self._fullscreen
        try:
            self.root.attributes("-fullscreen", self._fullscreen)
        except tk.TclError:
            self._fullscreen = False
            self.status.set("Full-screen mode is not supported by this window manager")
        return "break"

    def _toggle_fullscreen_shortcut(self, _event=None):
        self.toggle_fullscreen()
        return "break"

    def toggle_focus_mode(self):
        """Hide application chrome for distraction-free reading; Escape restores it."""
        if not self._focus_mode:
            chrome = [self.header, self.toolbar, self.document_bar, self.search_panel, self.status_bar]
            self._sidebar_was_visible = bool(self.navigation_sidebar.winfo_manager())
            if self._sidebar_was_visible:
                self.navigation_sidebar.pack_forget()
            self._chrome_visibility = {widget: bool(widget.winfo_manager()) for widget in chrome}
            self._sidebar_was_packed = bool(self.document_type == "pdf" and getattr(self, "pdf_sidebar", None) and self.pdf_sidebar.winfo_manager())
            for widget, was_visible in self._chrome_visibility.items():
                if was_visible:
                    widget.pack_forget()
            if self._sidebar_was_packed:
                self.pdf_sidebar.pack_forget()
            self._focus_mode = True
            self.root.title(f"{self.document_path.name if self.document_path else 'Offline Reader'} · Focus mode · Esc to exit")
            self.root.after_idle(self._resize_reading_surface)
        else:
            self._restore_focus_chrome()
        return "break"

    def _toggle_focus_mode_shortcut(self, _event=None):
        self.toggle_focus_mode()
        return "break"

    def _restore_focus_chrome(self):
        # Repack in the same vertical order used by the normal layout.
        for widget in (self.header, self.toolbar, self.document_bar, self.search_panel):
            if not self._chrome_visibility.get(widget, False) or widget.winfo_manager():
                continue
            if widget is self.toolbar and self.document_type != "pdf":
                continue
            if widget is self.header:
                widget.pack(fill="x", before=self.viewer_host)
            elif widget is self.toolbar:
                widget.pack(fill="x", padx=18, pady=(0, 8), before=self.viewer_host)
            elif widget is self.document_bar:
                widget.pack(fill="x", padx=18, before=self.viewer_host)
            elif widget is self.search_panel:
                widget.pack(fill="x", padx=18, pady=(8, 0), before=self.viewer_host)
        if getattr(self, "_sidebar_was_visible", False) and not self.navigation_sidebar.winfo_manager():
            self.navigation_sidebar.pack(side="left", fill="y", before=self.header)
        if self._sidebar_was_packed and self.document_type == "pdf" and not self.pdf_sidebar.winfo_manager():
            self.pdf_sidebar.pack(side="left", fill="y", padx=(0, 12), before=self.pdf_viewport)
        if self._chrome_visibility.get(self.status_bar, False) and not self.status_bar.winfo_manager():
            self.status_bar.pack(fill="x")
        self._focus_mode = False
        self.root.title(f"{self.document_path.name} — Offline Reader" if self.document_path else "Offline Reader")
        self.root.after_idle(self._resize_reading_surface)

    def _escape_reading_modes(self, _event=None):
        if self._focus_mode:
            self._restore_focus_chrome()
        elif self._fullscreen:
            self.toggle_fullscreen()
        else:
            self._close_active_popup()
        return "break"

    def _resize_reading_surface(self):
        if self.document_type == "text":
            self._center_text_column()
        elif self.document_type == "pdf" and self.pdf_document is not None and self.fit_width_enabled:
            self._on_pdf_viewport_resize()

    def _build_toolbar(self):
        self.toolbar = tk.Frame(self.root, bg=self.PANEL, padx=18, pady=8)
        self.previous_button = self._button(self.toolbar, "← Previous", self.previous_page, state="disabled", padx=9)
        self.next_button = self._button(self.toolbar, "Next →", self.next_page, state="disabled", padx=9)
        self.previous_button.pack(side="left")
        self.next_button.pack(side="left", padx=(5, 14))
        tk.Label(self.toolbar, text="Page", bg=self.PANEL, fg=self.MUTED, font=("Segoe UI", 9)).pack(side="left")
        self.page_number = tk.StringVar(value="—")
        self.page_entry = tk.Entry(self.toolbar, textvariable=self.page_number, width=5, justify="center", bg=self.PANEL, fg=self.TEXT, insertbackground=self.TEXT, relief="flat", state="disabled", font=("Segoe UI", 9))
        self.page_entry.pack(side="left", padx=(6, 5), ipady=4)
        self.page_entry.bind("<Return>", self.go_to_entered_page)
        self.page_total = tk.Label(self.toolbar, text="/ —", bg=self.PANEL, fg=self.MUTED, font=("Segoe UI", 9))
        self.page_total.pack(side="left", padx=(0, 18))
        self.zoom_out_button = self._button(self.toolbar, "−", self.zoom_out, state="disabled")
        self.zoom_in_button = self._button(self.toolbar, "+", self.zoom_in, state="disabled")
        self.zoom_out_button.pack(side="left")
        self.zoom_label = tk.Label(self.toolbar, text="—", bg=self.PANEL, fg=self.TEXT, width=12, font=("Segoe UI", 9))
        self.zoom_label.pack(side="left", padx=5)
        self.zoom_in_button.pack(side="left")
        self.fit_button = self._button(self.toolbar, "Fit width", self.fit_width, state="disabled")
        self.fit_button.pack(side="left", padx=(12, 0))

    def _build_text_viewer(self):
        self.text_viewer = tk.Frame(self.viewer_host, bg=self.BACKGROUND)
        self.text_empty = tk.Frame(self.text_viewer, bg=self.PANEL, padx=32, pady=30,
                                   highlightbackground=self.BORDER, highlightthickness=1)
        tk.Label(self.text_empty, text="Your reading space is ready", bg=self.PANEL,
                 fg=self.READER_TEXT, font=("Noto Sans", 16, "bold")).pack()
        tk.Label(
            self.text_empty,
            text="Open a TXT or PDF to begin. Select any text for a plain-language explanation, or search the whole document for evidence.",
            bg=self.PANEL, fg=self.READER_MUTED, font=("Noto Sans", 10),
            wraplength=480, justify="center",
        ).pack(pady=(8, 16))
        self._button(self.text_empty, "Open a document", self.open_file, primary=True).pack()
        self.text_empty.place(relx=0.5, rely=0.5, anchor="center")

        # A centered, paper-toned reading column avoids very long lines on wide
        # monitors while leaving the surrounding application chrome quiet.
        self.text_column = tk.Frame(
            self.text_viewer, bg=self.EDITOR, highlightbackground=self.BORDER,
            highlightthickness=1,
        )
        self._text_column_visible = False
        self.text_column.place(relx=0.5, rely=0, width=720, relheight=1, anchor="n")
        self.text = tk.Text(
            self.text_column, wrap="word", font=("Noto Serif", self.text_font_size),
            padx=42, pady=30, spacing1=5, spacing3=7, bg=self.EDITOR,
            fg=self.READER_TEXT, insertbackground=self.READER_TEXT,
            selectbackground=self.SELECT, selectforeground=self.SELECT_TEXT,
            relief="flat", undo=False, borderwidth=0, highlightthickness=0,
        )
        self.text.pack(side="left", fill="both", expand=True)
        scrollbar = tk.Scrollbar(
            self.text_column, orient="vertical", command=self.text.yview,
            troughcolor=self.EDITOR, bg=self.SCROLL_TRACK, activebackground=self.SCROLL_ACTIVE,
            relief="flat", width=12,
        )
        scrollbar.pack(side="right", fill="y")
        self.text.configure(yscrollcommand=scrollbar.set)
        self.text_viewer.bind("<Configure>", self._center_text_column)
        self.text_viewer.pack(fill="both", expand=True)
        self.text_column.place_forget()
        self.text_empty.lift()

    def _center_text_column(self, event=None):
        if not hasattr(self, "text_column") or not self._text_column_visible:
            return
        available = event.width if event is not None else self.text_viewer.winfo_width()
        width, gutter = _reading_column_metrics(available)
        self.text_column.place_configure(relx=0.5, width=width, relheight=1, anchor="n")
        self.text.configure(padx=gutter)

    def _set_text_column_visible(self, visible):
        """Show the paper-like reader only when a text document is open."""
        self._text_column_visible = visible
        if visible:
            self._center_text_column()
        else:
            self.text_column.place_forget()

    def _set_text_read_only(self, read_only=True):
        self.text.config(state="disabled" if read_only else "normal")

    def _run_document_search(self, _event=None):
        query = self.search_entry.get().strip()
        if not query:
            self.status.set("Enter a word or phrase to search this document")
            return "break"
        if self.controller.document is None:
            self.status.set("Open a document before searching")
            return "break"
        self._search_request += 1
        request_id = self._search_request
        self.search_results = []
        self.search_results_list.delete(0, tk.END)
        self.search_feedback.config(text="Searching locally…")
        self.search_results_frame.pack(fill="x", pady=(5, 0))
        self.status.set("Searching the document locally…")

        def search_worker():
            try:
                results = self.controller.search_document(query, limit=8)
                self._document_search_results.put((request_id, query, results, None))
            except Exception as error:
                self._document_search_results.put((request_id, query, [], str(error)))

        threading.Thread(target=search_worker, daemon=True).start()
        return "break"

    def _process_search_results(self):
        while True:
            try:
                request_id, query, results, error = self._document_search_results.get_nowait()
            except queue.Empty:
                break
            if request_id != self._search_request:
                continue
            self.search_results = results
            self.search_results_list.delete(0, tk.END)
            if error:
                self.search_feedback.config(text="Search couldn't finish. Check the document and try again.")
                self.status.set(f"Search failed: {error}")
                continue
            if not results:
                self.search_feedback.config(text=f"No matches for {query!r}. Try a shorter phrase or different keywords.")
                self.status.set(f"No matches for {query!r}")
                continue
            self.search_feedback.config(text=f"{len(results)} passages found  ·  Select a result and press Enter to jump to its location.")
            for result in results:
                preview = " ".join(result.text.split())
                if len(preview) > 130:
                    preview = preview[:127].rstrip() + "…"
                location = " ".join(str(result.location).split())
                if getattr(result, "section", None):
                    location += f" · {result.section}"
                self.search_results_list.insert(tk.END, f"{location}  ·  {preview}")
            self.status.set(f"Found {len(results)} relevant passage(s). Select a result to open its location.")
            self.search_results_list.selection_clear(0, tk.END)
            self.search_results_list.selection_set(0)
        self.root.after(75, self._process_search_results)

    def _clear_document_search(self):
        self._search_request += 1
        self.search_entry.delete(0, tk.END)
        self.search_results = []
        self.search_results_list.delete(0, tk.END)
        self.search_feedback.config(text="")
        self.search_results_frame.pack_forget()
        if self.document_type == "text":
            self.text.tag_remove("search_hit", "1.0", tk.END)
        self.status.set("Search cleared")

    def _open_selected_search_result(self, _event=None):
        selected = self.search_results_list.curselection()
        if not selected or selected[0] >= len(self.search_results):
            return "break"
        self.open_source(self.search_results[selected[0]])
        return "break"

    def _get_notes_store(self):
        if self._notes_store is None:
            self._notes_store = NotesStore()
        return self._notes_store

    def _open_notes_shortcut(self, _event=None):
        self.open_notes()
        return "break"

    def _open_annotations_shortcut(self, _event=None):
        self.open_annotations()
        return "break"

    def open_annotations(self):
        try:
            self._get_notes_store()
        except (OSError, sqlite3.Error, RuntimeError) as error:
            self.status.set(f"Highlights and bookmarks are unavailable: {error}")
            messagebox.showerror(
                "Annotations unavailable",
                f"The local annotation database could not be opened.\n\n{error}",
                parent=self.root,
            )
            return
        if self.annotations_window is not None:
            try:
                if self.annotations_window.winfo_exists():
                    self.annotations_window.deiconify()
                    self.annotations_window.lift()
                    self._refresh_annotations_list()
                    return
            except tk.TclError:
                self.annotations_window = None

        window = tk.Toplevel(self.root)
        self.annotations_window = window
        window.title("Offline Reader — Highlights & bookmarks")
        window.geometry("760x520")
        window.minsize(580, 380)
        window.configure(bg=self.BACKGROUND)
        window.transient(self.root)
        shell = tk.Frame(window, bg=self.PANEL, padx=14, pady=14)
        shell.pack(fill="both", expand=True, padx=14, pady=14)
        tk.Label(
            shell,
            text="HIGHLIGHTS & BOOKMARKS",
            bg=self.PANEL,
            fg=self.TEXT,
            font=("Segoe UI", 10, "bold"),
            anchor="w",
        ).pack(fill="x", pady=(0, 10))
        self.annotations_list = tk.Listbox(
            shell,
            bg=self.PANEL,
            fg=self.TEXT,
            selectbackground=self.ACCENT,
            selectforeground=self.PRIMARY_TEXT,
            relief="flat",
            borderwidth=0,
            activestyle="none",
            font=("Segoe UI", 9),
            highlightthickness=0,
            selectmode="browse",
        )
        self.annotations_list.pack(fill="both", expand=True)
        self.annotations_list.bind("<Double-Button-1>", self._open_selected_annotation)
        self.annotations_list.bind("<Return>", self._open_selected_annotation)
        self._annotation_items = []
        footer = tk.Frame(shell, bg=self.PANEL)
        footer.pack(fill="x", pady=(10, 0))
        self.annotations_status = tk.StringVar(value="Saved locally")
        tk.Label(
            footer,
            textvariable=self.annotations_status,
            bg=self.PANEL,
            fg=self.MUTED,
            font=("Segoe UI", 9),
            anchor="w",
        ).pack(side="left", fill="x", expand=True)
        self._button(
            footer, "Close", self._close_annotations_window
        ).pack(side="right", padx=(5, 0))
        self._button(
            footer, "Delete", self._delete_selected_annotation
        ).pack(side="right", padx=(5, 0))
        self._button(
            footer, "Open source", self._open_selected_annotation
        ).pack(side="right", padx=(5, 0))
        window.protocol("WM_DELETE_WINDOW", self._close_annotations_window)
        self._style_annotations_window()
        self._refresh_annotations_list()

    def _style_annotations_window(self):
        if self.annotations_window is None:
            return
        self._recolor_widget_tree(
            self.annotations_window,
            self._theme_color_mapping("dark" if self.theme == "light" else "light", self.theme),
        )
        self.annotations_window.configure(bg=self.BACKGROUND)

    def _refresh_annotations_list(self):
        if self.annotations_window is None or not self.annotations_window.winfo_exists():
            return
        try:
            self._annotation_items = self._get_notes_store().list_annotations()
        except (OSError, sqlite3.Error, RuntimeError, ValueError) as error:
            self.annotations_status.set(f"Could not load annotations: {error}")
            return
        self.annotations_list.delete(0, tk.END)
        for annotation in self._annotation_items:
            source = annotation.source
            kind = "Highlight" if annotation.kind == "highlight" else "Bookmark"
            excerpt = " ".join(source.text.split())
            if len(excerpt) > 90:
                excerpt = excerpt[:87].rstrip() + "…"
            self.annotations_list.insert(
                tk.END,
                f"{kind} · {Path(source.document_path).name} · {source.location} · {excerpt}",
            )
        self.annotations_status.set(
            f"{len(self._annotation_items)} saved item(s)"
            if self._annotation_items
            else "No highlights or bookmarks yet"
        )

    def _selected_annotation(self):
        selection = self.annotations_list.curselection()
        if not selection or selection[0] >= len(self._annotation_items):
            return None
        return self._annotation_items[selection[0]]

    def _open_selected_annotation(self, _event=None):
        annotation = self._selected_annotation()
        if annotation is None:
            self.annotations_status.set("Select a highlight or bookmark first")
            return "break"
        self._open_verified_source(
            annotation.source,
            self.annotations_window,
            self.annotations_status.set,
        )
        return "break"

    def _delete_selected_annotation(self):
        annotation = self._selected_annotation()
        if annotation is None:
            self.annotations_status.set("Select a highlight or bookmark first")
            return
        try:
            self._get_notes_store().delete_annotation(annotation.id)
        except (OSError, sqlite3.Error, KeyError, RuntimeError) as error:
            self.annotations_status.set(f"Delete failed: {error}")
            return
        self._refresh_annotations_list()
        self._refresh_text_annotations()
        if self.pdf_document is not None:
            self._render_current_page(reset_view=False)
        self.annotations_status.set("Annotation deleted")

    def _close_annotations_window(self):
        if self.annotations_window is not None:
            self.annotations_window.destroy()
            self.annotations_window = None

    def _create_annotation_from_context(self, context, selected_text, kind):
        try:
            source = self._source_reference_for_context(context)
            if selected_text != source.text:
                raise ValueError("The selection changed; please select the passage again.")
            self._get_notes_store().create_annotation(source, kind)
        except (OSError, sqlite3.Error, ValueError, RuntimeError) as error:
            self.status.set(f"Could not save {kind}: {error}")
            messagebox.showerror(
                f"Could not save {kind}",
                f"The {kind} was not saved.\n\n{error}",
                parent=self.root,
            )
            return
        self.status.set(
            "Highlight saved locally" if kind == "highlight" else "Bookmark saved locally"
        )
        if self.document_type == "text":
            self._refresh_text_annotations()
        elif self.pdf_document is not None:
            self._render_current_page(reset_view=False)
        if self.annotations_window is not None:
            self._refresh_annotations_list()

    def _open_revision_notes(self, context, selected_text):
        if selected_text != context.selected_text:
            self.status.set("The selection changed; please select the passage again")
            return
        try:
            source = self._source_reference_for_context(context)
        except (OSError, RuntimeError) as error:
            self.status.set(f"Could not prepare revision notes: {error}")
            return
        if self.revision_window is not None:
            try:
                if self.revision_window.winfo_exists():
                    if self._revision_is_dirty() and not messagebox.askyesno(
                        "Replace revision draft",
                        "Discard the current revision draft and generate notes for this passage?",
                        parent=self.revision_window,
                    ):
                        return
                    self._study_request += 1
                    self._revision_context = context
                    self._revision_source = source
                    self.revision_title.delete(0, tk.END)
                    self.revision_title.insert(
                        0, self._revision_title_for_context(context)
                    )
                    self.revision_body.delete("1.0", tk.END)
                    self.revision_body.insert(
                        "1.0", f"Selected passage:\n{context.selected_text}"
                    )
                    self._revision_snapshot = self._revision_values()
                    self.revision_status.set("Preparing revision notes…")
                    self.revision_save_button.configure(state="disabled")
                    self.revision_window.deiconify()
                    self.revision_window.lift()
                    self._generate_revision_notes()
                    return
            except tk.TclError:
                self.revision_window = None

        window = tk.Toplevel(self.root)
        self.revision_window = window
        self._revision_context = context
        self._revision_source = source
        window.title("Offline Reader — Revision notes")
        window.geometry("720x560")
        window.minsize(540, 420)
        window.configure(bg=self.BACKGROUND)
        window.transient(self.root)
        shell = tk.Frame(window, bg=self.PANEL, padx=16, pady=14)
        shell.pack(fill="both", expand=True, padx=14, pady=14)
        tk.Label(
            shell,
            text="REVISION NOTES · SELECTED PASSAGE",
            bg=self.PANEL,
            fg=self.TEXT,
            font=("Segoe UI", 10, "bold"),
            anchor="w",
        ).pack(fill="x")
        tk.Label(
            shell,
            text=f"{context.document_title} · {context.location}",
            bg=self.PANEL,
            fg=self.MUTED,
            font=("Segoe UI", 9),
            anchor="w",
        ).pack(fill="x", pady=(3, 10))
        tk.Label(
            shell,
            text="Note title",
            bg=self.PANEL,
            fg=self.MUTED,
            font=("Segoe UI", 9, "bold"),
            anchor="w",
        ).pack(fill="x")
        self.revision_title = tk.Entry(
            shell,
            bg=self.EDITOR,
            fg=self.TEXT,
            insertbackground=self.TEXT,
            relief="flat",
            font=("Segoe UI", 11, "bold"),
        )
        self.revision_title.pack(fill="x", pady=(4, 10), ipady=6)
        self.revision_title.insert(0, self._revision_title_for_context(context))
        self.revision_body = tk.Text(
            shell,
            wrap="word",
            bg=self.EDITOR,
            fg=self.READER_TEXT,
            insertbackground=self.READER_TEXT,
            relief="flat",
            font=("Noto Sans", 10),
            padx=10,
            pady=10,
            highlightthickness=1,
            highlightbackground=self.BORDER,
            highlightcolor=self.FOCUS,
        )
        self.revision_body.pack(fill="both", expand=True, pady=(0, 10))
        self.revision_body.insert(
            "1.0", f"Selected passage:\n{context.selected_text}"
        )
        self._revision_snapshot = self._revision_values()
        footer = tk.Frame(shell, bg=self.PANEL)
        footer.pack(fill="x")
        self.revision_status = tk.StringVar(value="Ready to generate")
        tk.Label(
            footer,
            textvariable=self.revision_status,
            bg=self.PANEL,
            fg=self.MUTED,
            font=("Segoe UI", 9),
            anchor="w",
        ).pack(side="left", fill="x", expand=True)
        self.revision_close_button = self._button(
            footer, "Close", self._close_revision_notes
        )
        self.revision_close_button.pack(side="right", padx=(5, 0))
        self.revision_save_button = self._button(
            footer,
            "Save as note",
            self._save_revision_note,
            primary=True,
            state="disabled",
        )
        self.revision_save_button.pack(side="right", padx=(5, 0))
        self.revision_generate_button = self._button(
            footer, "Generate again", self._generate_revision_notes
        )
        self.revision_generate_button.pack(side="right", padx=(5, 0))
        window.protocol("WM_DELETE_WINDOW", self._close_revision_notes)
        self._style_revision_window()
        self._generate_revision_notes()

    @staticmethod
    def _revision_title_for_context(context):
        excerpt = " ".join(context.selected_text.split())
        if len(excerpt) > 48:
            excerpt = excerpt[:45].rstrip() + "…"
        return f"Revision notes: {excerpt}" if excerpt else "Revision notes"

    def _style_revision_window(self):
        if self.revision_window is None:
            return
        self._recolor_widget_tree(
            self.revision_window,
            self._theme_color_mapping("dark" if self.theme == "light" else "light", self.theme),
        )
        self.revision_window.configure(bg=self.BACKGROUND)

    def _revision_values(self):
        return (
            self.revision_title.get().strip(),
            self.revision_body.get("1.0", "end-1c"),
        )

    def _revision_is_dirty(self):
        return self._revision_values() != self._revision_snapshot

    def _generate_revision_notes(self):
        if self.revision_window is None or not self.revision_window.winfo_exists():
            return
        if self._revision_is_dirty() and not messagebox.askyesno(
            "Replace revision draft",
            "Discard your edits and generate a new revision draft?",
            parent=self.revision_window,
        ):
            return
        self._study_request += 1
        request_id = self._study_request
        context = self._revision_context
        self.revision_generate_button.configure(state="disabled")
        self.revision_save_button.configure(state="disabled")
        self.revision_status.set("Generating from the selected passage…")
        threading.Thread(
            target=self._generate_revision_notes_worker,
            args=(request_id, context),
            daemon=True,
        ).start()

    def _generate_revision_notes_worker(self, request_id, context):
        try:
            response = self.controller.generate_revision_notes(context)
        except Exception as error:
            from services.api_client import ServiceResponse
            response = ServiceResponse(
                ok=False,
                text=f"The local assistant could not generate revision notes: {error}",
                code="generation_error",
            )
        self._study_results.put((request_id, context, response))

    def _process_study_results(self):
        while True:
            try:
                request_id, context, response = self._study_results.get_nowait()
            except queue.Empty:
                break
            if request_id != self._study_request or self._revision_context is not context:
                continue
            if self.revision_window is None:
                continue
            try:
                if not self.revision_window.winfo_exists():
                    continue
            except tk.TclError:
                continue
            self.revision_generate_button.configure(state="normal")
            if not response.ok:
                self.revision_status.set(response.text)
                self.status.set("Revision-note generation failed")
                continue
            self.revision_body.delete("1.0", tk.END)
            self.revision_body.insert("1.0", response.text)
            self._revision_snapshot = self._revision_values()
            self.revision_save_button.configure(state="normal")
            self.revision_status.set(
                "Generated from the selected passage; review and edit before saving"
            )
        self.root.after(50, self._process_study_results)

    def _save_revision_note(self):
        context = self._revision_context
        title = self.revision_title.get().strip()
        body = self.revision_body.get("1.0", "end-1c").strip()
        if not title:
            self.revision_status.set("Enter a title before saving")
            return
        if not body:
            self.revision_status.set("There is no revision content to save")
            return
        try:
            note = self._get_notes_store().create_note(
                title, body, source=self._revision_source
            )
        except (OSError, sqlite3.Error, ValueError, RuntimeError) as error:
            self.revision_status.set(f"Save failed: {error}")
            self.status.set(f"Could not save revision note: {error}")
            return
        self.revision_save_button.configure(state="disabled")
        self._revision_snapshot = self._revision_values()
        self.revision_status.set(f"Saved as source-linked note: {note.title}")
        self.status.set("Revision notes saved locally")

    def _close_revision_notes(self):
        if self.revision_window is None:
            return
        if self._revision_is_dirty() and not messagebox.askyesno(
            "Close revision notes",
            "Close this draft? Unsaved edits will be lost.",
            parent=self.revision_window,
        ):
            return
        self._study_request += 1
        self.revision_window.destroy()
        self.revision_window = None

    def _open_quiz_setup(self, context, selected_text):
        if selected_text != context.selected_text:
            self.status.set("The selection changed; please select the passage again")
            return
        try:
            source = self._source_reference_for_context(context)
        except (OSError, RuntimeError) as error:
            self.status.set(f"Could not prepare quiz: {error}")
            return
        if self.quiz_setup_window is not None:
            try:
                if self.quiz_setup_window.winfo_exists():
                    self._quiz_request += 1
                    self._quiz_context = context
                    self._quiz_source = source
                    self.quiz_context_label.configure(
                        text=f"{context.document_title} · {context.location}\n"
                        "Questions will use only the selected passage and its immediate context."
                    )
                    if hasattr(self, "quiz_generate_button"):
                        self.quiz_generate_button.configure(state="normal")
                    if hasattr(self, "quiz_setup_status"):
                        self.quiz_setup_status.set(
                            "Answers stay hidden until you submit."
                        )
                    self.quiz_setup_window.deiconify()
                    self.quiz_setup_window.lift()
                    return
            except tk.TclError:
                self.quiz_setup_window = None

        window = tk.Toplevel(self.root)
        self.quiz_setup_window = window
        self._quiz_context = context
        self._quiz_source = source
        window.title("Offline Reader — Create quiz")
        window.geometry("440x390")
        window.minsize(400, 360)
        window.configure(bg=self.BACKGROUND)
        window.transient(self.root)
        shell = tk.Frame(window, bg=self.PANEL, padx=18, pady=16)
        shell.pack(fill="both", expand=True, padx=14, pady=14)
        tk.Label(
            shell,
            text="CREATE A PASSAGE QUIZ",
            bg=self.PANEL,
            fg=self.TEXT,
            font=("Segoe UI", 10, "bold"),
            anchor="w",
        ).pack(fill="x")
        self.quiz_context_label = tk.Label(
            shell,
            text=f"{context.document_title} · {context.location}\n"
            "Questions will use only the selected passage and its immediate context.",
            bg=self.PANEL,
            fg=self.MUTED,
            font=("Segoe UI", 9),
            anchor="w",
            justify="left",
            wraplength=380,
        )
        self.quiz_context_label.pack(fill="x", pady=(5, 14))
        self.quiz_count = tk.IntVar(value=5)
        self.quiz_difficulty = tk.StringVar(value="medium")
        self.quiz_kind = tk.StringVar(value="multiple_choice")
        self._quiz_option_row(shell, "Question count", self.quiz_count, ("3", "5", "8"))
        self._quiz_option_row(shell, "Difficulty", self.quiz_difficulty, ("easy", "medium", "hard"))
        self._quiz_option_row(
            shell,
            "Question style",
            self.quiz_kind,
            ("multiple_choice", "true_false", "short_answer"),
            labels=("Multiple choice", "True / false", "Short answer"),
        )
        self.quiz_setup_status = tk.StringVar(value="Answers stay hidden until you submit.")
        tk.Label(
            shell,
            textvariable=self.quiz_setup_status,
            bg=self.PANEL,
            fg=self.MUTED,
            font=("Segoe UI", 9),
            anchor="w",
            wraplength=380,
        ).pack(fill="x", pady=(12, 8))
        footer = tk.Frame(shell, bg=self.PANEL)
        footer.pack(fill="x")
        self._button(footer, "Cancel", self._close_quiz_setup).pack(side="right", padx=(5, 0))
        self.quiz_generate_button = self._button(
            footer, "Generate quiz", self._generate_quiz, primary=True
        )
        self.quiz_generate_button.pack(side="right")
        window.protocol("WM_DELETE_WINDOW", self._close_quiz_setup)
        self._style_quiz_setup_window()

    def _quiz_option_row(self, parent, label, variable, values, labels=None):
        row = tk.Frame(parent, bg=self.PANEL)
        row.pack(fill="x", pady=5)
        tk.Label(
            row, text=label, bg=self.PANEL, fg=self.TEXT,
            font=("Segoe UI", 9, "bold"), anchor="w", width=16,
        ).pack(side="left")
        display_labels = labels or values
        option_menu = tk.OptionMenu(
            row,
            variable,
            *values,
        )
        option_menu.configure(
            bg=self.CONTROL,
            fg=self.TEXT,
            activebackground=self.CONTROL_ACTIVE,
            activeforeground=self.TEXT,
            relief="flat",
            highlightthickness=0,
            font=("Segoe UI", 9),
        )
        option_menu["menu"].configure(
            bg=self.PANEL, fg=self.TEXT, activebackground=self.ACCENT,
            activeforeground=self.PRIMARY_TEXT,
        )
        for value, display in zip(values, display_labels):
            option_menu["menu"].entryconfigure(
                values.index(value), label=display
            )
        option_menu.pack(side="left", fill="x", expand=True)

    def _style_quiz_setup_window(self):
        if self.quiz_setup_window is None:
            return
        self._recolor_widget_tree(
            self.quiz_setup_window,
            self._theme_color_mapping("dark" if self.theme == "light" else "light", self.theme),
        )
        self.quiz_setup_window.configure(bg=self.BACKGROUND)

    def _generate_quiz(self):
        if self.quiz_setup_window is None or not self.quiz_setup_window.winfo_exists():
            return
        count = self.quiz_count.get()
        difficulty = self.quiz_difficulty.get()
        kind = self.quiz_kind.get()
        context = self._quiz_context
        source = self._quiz_source
        self._quiz_request += 1
        request_id = self._quiz_request
        self.quiz_generate_button.configure(state="disabled")
        self.quiz_setup_status.set("Generating and validating questions locally…")
        threading.Thread(
            target=self._generate_quiz_worker,
            args=(request_id, context, source, count, difficulty, kind),
            daemon=True,
        ).start()

    def _generate_quiz_worker(self, request_id, context, source, count, difficulty, kind):
        try:
            response, questions = self.controller.generate_quiz(
                context, count, difficulty, kind
            )
        except Exception as error:
            from services.api_client import ServiceResponse
            response, questions = ServiceResponse(
                False,
                f"The local assistant could not generate a quiz: {error}",
                "generation_error",
            ), ()
        self._quiz_results.put(
            (request_id, context, source, difficulty, response, questions)
        )

    def _process_quiz_results(self):
        while True:
            try:
                result = self._quiz_results.get_nowait()
            except queue.Empty:
                break
            (
                request_id,
                context,
                source,
                difficulty,
                response,
                questions,
            ) = result
            if request_id != self._quiz_request or context is not self._quiz_context:
                continue
            if self.quiz_setup_window is None:
                continue
            try:
                if not self.quiz_setup_window.winfo_exists():
                    continue
            except tk.TclError:
                continue
            self.quiz_generate_button.configure(state="normal")
            if not response.ok:
                self.quiz_setup_status.set(response.text)
                self.status.set("Quiz generation failed")
                continue
            try:
                study_set = self._get_notes_store().create_study_set(
                    self._quiz_title(context),
                    difficulty,
                    questions,
                    source,
                )
            except (OSError, sqlite3.Error, ValueError, RuntimeError) as error:
                self.quiz_setup_status.set(f"Could not save quiz: {error}")
                self.status.set(f"Could not save quiz: {error}")
                continue
            self._close_quiz_setup(force=True)
            self._open_quiz(study_set)
            self.status.set(f"Quiz saved locally: {len(study_set.questions)} questions")
        self.root.after(50, self._process_quiz_results)

    @staticmethod
    def _quiz_title(context):
        excerpt = " ".join(context.selected_text.split())
        if len(excerpt) > 48:
            excerpt = excerpt[:45].rstrip() + "…"
        return f"Quiz: {excerpt}" if excerpt else "Passage quiz"

    def _close_quiz_setup(self, force=False):
        if self.quiz_setup_window is None:
            return
        if not force:
            self._quiz_request += 1
        self.quiz_setup_window.destroy()
        self.quiz_setup_window = None

    def open_quiz_library(self):
        try:
            self._get_notes_store()
        except (OSError, sqlite3.Error, RuntimeError) as error:
            self.status.set(f"Saved quizzes are unavailable: {error}")
            messagebox.showerror(
                "Quizzes unavailable",
                f"The local study database could not be opened.\n\n{error}",
                parent=self.root,
            )
            return
        if self.quiz_library_window is not None:
            try:
                if self.quiz_library_window.winfo_exists():
                    self.quiz_library_window.deiconify()
                    self.quiz_library_window.lift()
                    self._refresh_quiz_library()
                    return
            except tk.TclError:
                self.quiz_library_window = None
        window = tk.Toplevel(self.root)
        self.quiz_library_window = window
        window.title("Offline Reader — My quizzes")
        window.geometry("760x520")
        window.minsize(560, 380)
        window.configure(bg=self.BACKGROUND)
        window.transient(self.root)
        shell = tk.Frame(window, bg=self.PANEL, padx=14, pady=14)
        shell.pack(fill="both", expand=True, padx=14, pady=14)
        tk.Label(
            shell, text="SAVED QUIZZES", bg=self.PANEL, fg=self.TEXT,
            font=("Segoe UI", 10, "bold"), anchor="w",
        ).pack(fill="x", pady=(0, 10))
        self.quiz_library_list = tk.Listbox(
            shell, bg=self.PANEL, fg=self.TEXT,
            selectbackground=self.ACCENT, selectforeground=self.PRIMARY_TEXT,
            relief="flat", borderwidth=0, activestyle="none",
            font=("Segoe UI", 9), highlightthickness=0, selectmode="browse",
        )
        self.quiz_library_list.pack(fill="both", expand=True)
        self.quiz_library_list.bind("<Double-Button-1>", self._open_selected_quiz)
        self.quiz_library_list.bind("<Return>", self._open_selected_quiz)
        self._quiz_library_items = []
        footer = tk.Frame(shell, bg=self.PANEL)
        footer.pack(fill="x", pady=(10, 0))
        self.quiz_library_status = tk.StringVar(value="")
        tk.Label(
            footer, textvariable=self.quiz_library_status, bg=self.PANEL,
            fg=self.MUTED, font=("Segoe UI", 9), anchor="w",
        ).pack(side="left", fill="x", expand=True)
        self._button(footer, "Close", self._close_quiz_library).pack(side="right", padx=(5, 0))
        self._button(footer, "Delete", self._delete_selected_quiz).pack(side="right", padx=(5, 0))
        self._button(footer, "Open", self._open_selected_quiz, primary=True).pack(side="right", padx=(5, 0))
        window.protocol("WM_DELETE_WINDOW", self._close_quiz_library)
        self._style_quiz_library_window()
        self._refresh_quiz_library()

    def _style_quiz_library_window(self):
        if self.quiz_library_window is None:
            return
        self._recolor_widget_tree(
            self.quiz_library_window,
            self._theme_color_mapping("dark" if self.theme == "light" else "light", self.theme),
        )
        self.quiz_library_window.configure(bg=self.BACKGROUND)

    def _refresh_quiz_library(self):
        if self.quiz_library_window is None or not self.quiz_library_window.winfo_exists():
            return
        try:
            self._quiz_library_items = self._get_notes_store().list_study_sets()
        except (OSError, sqlite3.Error, RuntimeError, ValueError) as error:
            self.quiz_library_status.set(f"Could not load quizzes: {error}")
            return
        self.quiz_library_list.delete(0, tk.END)
        for study_set in self._quiz_library_items:
            answered = sum(answer is not None for answer in study_set.answers)
            self.quiz_library_list.insert(
                tk.END,
                f"{study_set.title} · {study_set.difficulty} · "
                f"{answered}/{len(study_set.questions)} attempted · "
                f"{Path(study_set.source.document_path).name} · {study_set.source.location}",
            )
        self.quiz_library_status.set(
            f"{len(self._quiz_library_items)} saved quiz(es)"
            if self._quiz_library_items
            else "No saved quizzes yet"
        )

    def _selected_quiz(self):
        selection = self.quiz_library_list.curselection()
        if not selection or selection[0] >= len(self._quiz_library_items):
            return None
        return self._quiz_library_items[selection[0]]

    def _open_selected_quiz(self, _event=None):
        study_set = self._selected_quiz()
        if study_set is None:
            self.quiz_library_status.set("Select a quiz first")
            return "break"
        self._open_quiz(study_set)
        return "break"

    def _delete_selected_quiz(self):
        study_set = self._selected_quiz()
        if study_set is None:
            self.quiz_library_status.set("Select a quiz first")
            return
        if not messagebox.askyesno(
            "Delete quiz",
            "Delete this saved quiz and its answers permanently?",
            parent=self.quiz_library_window,
        ):
            return
        try:
            self._get_notes_store().delete_study_set(study_set.id)
        except (OSError, sqlite3.Error, KeyError, RuntimeError) as error:
            self.quiz_library_status.set(f"Delete failed: {error}")
            return
        self._refresh_quiz_library()

    def _close_quiz_library(self):
        if self.quiz_library_window is not None:
            self.quiz_library_window.destroy()
            self.quiz_library_window = None

    def _open_quiz(self, study_set):
        if self.quiz_window is not None:
            try:
                if self.quiz_window.winfo_exists():
                    self.quiz_window.destroy()
            except tk.TclError:
                pass
        self._active_study_set = study_set
        self._quiz_question_index = next(
            (
                index
                for index, answer in enumerate(study_set.answers)
                if answer is None
            ),
            0,
        )
        window = tk.Toplevel(self.root)
        self.quiz_window = window
        window.title(f"Offline Reader — {study_set.title}")
        window.geometry("760x610")
        window.minsize(580, 460)
        window.configure(bg=self.BACKGROUND)
        window.transient(self.root)
        shell = tk.Frame(window, bg=self.PANEL, padx=16, pady=14)
        shell.pack(fill="both", expand=True, padx=14, pady=14)
        tk.Label(
            shell,
            text=study_set.title,
            bg=self.PANEL,
            fg=self.TEXT,
            font=("Segoe UI", 12, "bold"),
            anchor="w",
        ).pack(fill="x")
        tk.Label(
            shell,
            text=f"{study_set.difficulty.title()} · {len(study_set.questions)} questions · "
            f"{Path(study_set.source.document_path).name} · {study_set.source.location}",
            bg=self.PANEL,
            fg=self.MUTED,
            font=("Segoe UI", 9),
            anchor="w",
        ).pack(fill="x", pady=(4, 10))
        self.quiz_question_number = tk.StringVar()
        tk.Label(
            shell, textvariable=self.quiz_question_number, bg=self.PANEL,
            fg=self.MUTED, font=("Segoe UI", 9, "bold"), anchor="w",
        ).pack(fill="x")
        self.quiz_question_label = tk.Label(
            shell, bg=self.PANEL, fg=self.TEXT, font=("Segoe UI", 11, "bold"),
            anchor="w", justify="left", wraplength=660,
        )
        self.quiz_question_label.pack(fill="x", pady=(7, 12))
        self.quiz_answer_frame = tk.Frame(shell, bg=self.PANEL)
        self.quiz_answer_frame.pack(fill="both", expand=True)
        self.quiz_feedback = tk.Label(
            shell, bg=self.PANEL, fg=self.TEXT, font=("Segoe UI", 9),
            anchor="w", justify="left", wraplength=660,
        )
        self.quiz_feedback.pack(fill="x", pady=(8, 0))
        footer = tk.Frame(shell, bg=self.PANEL)
        footer.pack(fill="x", pady=(12, 0))
        self.quiz_status = tk.StringVar(value="Submit your answer to reveal the answer and explanation.")
        tk.Label(
            footer, textvariable=self.quiz_status, bg=self.PANEL, fg=self.MUTED,
            font=("Segoe UI", 9), anchor="w",
        ).pack(side="left", fill="x", expand=True)
        self._button(footer, "Open source", self._open_quiz_source).pack(side="right", padx=(5, 0))
        self._button(footer, "Next", lambda: self._navigate_quiz(1)).pack(side="right", padx=(5, 0))
        self._button(footer, "Previous", lambda: self._navigate_quiz(-1)).pack(side="right", padx=(5, 0))
        self.quiz_submit_button = self._button(
            footer, "Submit answer", self._submit_quiz_answer, primary=True
        )
        self.quiz_submit_button.pack(side="right", padx=(5, 0))
        window.protocol("WM_DELETE_WINDOW", self._close_quiz)
        self._style_quiz_window()
        self._render_quiz_question()

    def _style_quiz_window(self):
        if self.quiz_window is None:
            return
        self._recolor_widget_tree(
            self.quiz_window,
            self._theme_color_mapping("dark" if self.theme == "light" else "light", self.theme),
        )
        self.quiz_window.configure(bg=self.BACKGROUND)

    def _render_quiz_question(self):
        if self.quiz_window is None or not self.quiz_window.winfo_exists():
            return
        study_set = self._active_study_set
        index = self._quiz_question_index
        question = study_set.questions[index]
        self.quiz_question_number.set(f"Question {index + 1} of {len(study_set.questions)}")
        self.quiz_question_label.configure(text=question.prompt)
        for child in self.quiz_answer_frame.winfo_children():
            child.destroy()
        self.quiz_feedback.configure(text="")
        saved_answer = study_set.answers[index]
        if question.kind == "short_answer":
            self.quiz_input_var = tk.StringVar(value=saved_answer or "")
            tk.Entry(
                self.quiz_answer_frame,
                textvariable=self.quiz_input_var,
                bg=self.EDITOR,
                fg=self.TEXT,
                insertbackground=self.TEXT,
                relief="flat",
                font=("Segoe UI", 10),
            ).pack(fill="x", pady=6, ipady=7)
        else:
            self.quiz_input_var = tk.StringVar(value=saved_answer or "")
            for option in question.options:
                tk.Radiobutton(
                    self.quiz_answer_frame,
                    text=option,
                    value=option,
                    variable=self.quiz_input_var,
                    bg=self.PANEL,
                    fg=self.TEXT,
                    selectcolor=self.EDITOR,
                    activebackground=self.PANEL,
                    activeforeground=self.TEXT,
                    anchor="w",
                    font=("Segoe UI", 10),
                ).pack(fill="x", pady=3)
        if saved_answer is None:
            self.quiz_submit_button.configure(state="normal", text="Submit answer")
            self.quiz_status.set("Submit your answer to reveal the answer and explanation.")
        else:
            self.quiz_submit_button.configure(state="disabled", text="Answer submitted")
            self._show_quiz_feedback(saved_answer)

    def _submit_quiz_answer(self):
        study_set = self._active_study_set
        index = self._quiz_question_index
        if study_set.answers[index] is not None:
            return
        answer = self.quiz_input_var.get().strip()
        if not answer:
            self.quiz_status.set("Choose or enter an answer before submitting.")
            return
        try:
            study_set = self._get_notes_store().save_study_set_answer(
                study_set.id, index, answer
            )
        except (OSError, sqlite3.Error, KeyError, ValueError, RuntimeError) as error:
            self.quiz_status.set(f"Could not save answer: {error}")
            return
        self._active_study_set = study_set
        self.quiz_submit_button.configure(state="disabled", text="Answer submitted")
        self._show_quiz_feedback(answer)
        if self.quiz_library_window is not None:
            self._refresh_quiz_library()

    def _show_quiz_feedback(self, submitted_answer):
        question = self._active_study_set.questions[self._quiz_question_index]
        is_objective = question.kind in {"multiple_choice", "true_false"}
        correct = submitted_answer.casefold() == question.answer.casefold()
        headline = (
            "Correct."
            if correct
            else "Not quite."
        ) if is_objective else "Answer recorded. Compare it with the expected answer below."
        self.quiz_feedback.configure(
            text=f"{headline}\nExpected answer: {question.answer}\n\n{question.explanation}"
        )
        self.quiz_status.set("Answer and explanation revealed after submission.")

    def _navigate_quiz(self, direction):
        target = self._quiz_question_index + direction
        if not 0 <= target < len(self._active_study_set.questions):
            return
        self._quiz_question_index = target
        self._render_quiz_question()

    def _open_quiz_source(self):
        source = self._active_study_set.source
        self._open_verified_source(source, self.quiz_window, self.quiz_status.set)

    def _close_quiz(self):
        if self.quiz_window is not None:
            self.quiz_window.destroy()
            self.quiz_window = None

    def _open_flashcard_setup(self, context, selected_text):
        if selected_text != context.selected_text:
            self.status.set("The selection changed; please select the passage again")
            return
        try:
            source = self._source_reference_for_context(context)
        except (OSError, RuntimeError) as error:
            self.status.set(f"Could not prepare flashcards: {error}")
            return
        if self.flashcard_setup_window is not None:
            try:
                if self.flashcard_setup_window.winfo_exists():
                    self._flashcard_request += 1
                    self._flashcard_context = context
                    self._flashcard_source = source
                    self.flashcard_context_label.configure(
                        text=f"{context.document_title} · {context.location}\n"
                        "Cards use only this passage and its immediate context."
                    )
                    self.flashcard_generate_button.configure(state="normal")
                    self.flashcard_setup_status.set(
                        "Answers stay hidden until you reveal them."
                    )
                    self.flashcard_setup_window.deiconify()
                    self.flashcard_setup_window.lift()
                    return
            except tk.TclError:
                self.flashcard_setup_window = None

        window = tk.Toplevel(self.root)
        self.flashcard_setup_window = window
        self._flashcard_context = context
        self._flashcard_source = source
        window.title("Offline Reader — Create flashcards")
        window.geometry("440x320")
        window.minsize(400, 290)
        window.configure(bg=self.BACKGROUND)
        window.transient(self.root)
        shell = tk.Frame(window, bg=self.PANEL, padx=18, pady=16)
        shell.pack(fill="both", expand=True, padx=14, pady=14)
        tk.Label(
            shell,
            text="CREATE PASSAGE FLASHCARDS",
            bg=self.PANEL,
            fg=self.TEXT,
            font=("Segoe UI", 10, "bold"),
            anchor="w",
        ).pack(fill="x")
        self.flashcard_context_label = tk.Label(
            shell,
            text=f"{context.document_title} · {context.location}\n"
            "Cards use only this passage and its immediate context.",
            bg=self.PANEL,
            fg=self.MUTED,
            font=("Segoe UI", 9),
            anchor="w",
            justify="left",
            wraplength=380,
        )
        self.flashcard_context_label.pack(fill="x", pady=(5, 14))
        self.flashcard_count = tk.IntVar(value=5)
        self._quiz_option_row(shell, "Card count", self.flashcard_count, ("3", "5", "8"))
        self.flashcard_setup_status = tk.StringVar(
            value="Answers stay hidden until you reveal them."
        )
        tk.Label(
            shell,
            textvariable=self.flashcard_setup_status,
            bg=self.PANEL,
            fg=self.MUTED,
            font=("Segoe UI", 9),
            anchor="w",
            wraplength=380,
        ).pack(fill="x", pady=(12, 8))
        footer = tk.Frame(shell, bg=self.PANEL)
        footer.pack(fill="x")
        self._button(
            footer, "Cancel", self._close_flashcard_setup
        ).pack(side="right", padx=(5, 0))
        self.flashcard_generate_button = self._button(
            footer, "Generate flashcards", self._generate_flashcards, primary=True
        )
        self.flashcard_generate_button.pack(side="right")
        window.protocol("WM_DELETE_WINDOW", self._close_flashcard_setup)
        self._style_flashcard_setup_window()

    def _style_flashcard_setup_window(self):
        if self.flashcard_setup_window is None:
            return
        self._recolor_widget_tree(
            self.flashcard_setup_window,
            self._theme_color_mapping("dark" if self.theme == "light" else "light", self.theme),
        )
        self.flashcard_setup_window.configure(bg=self.BACKGROUND)

    def _generate_flashcards(self):
        if (
            self.flashcard_setup_window is None
            or not self.flashcard_setup_window.winfo_exists()
        ):
            return
        count = self.flashcard_count.get()
        context = self._flashcard_context
        source = self._flashcard_source
        self._flashcard_request += 1
        request_id = self._flashcard_request
        self.flashcard_generate_button.configure(state="disabled")
        self.flashcard_setup_status.set("Generating and validating cards locally…")
        threading.Thread(
            target=self._generate_flashcards_worker,
            args=(request_id, context, source, count),
            daemon=True,
        ).start()

    def _generate_flashcards_worker(self, request_id, context, source, count):
        try:
            response, cards = self.controller.generate_flashcards(context, count)
        except Exception as error:
            from services.api_client import ServiceResponse
            response, cards = ServiceResponse(
                False,
                f"The local assistant could not generate flashcards: {error}",
                "generation_error",
            ), ()
        self._flashcard_results.put((request_id, context, source, response, cards))

    def _process_flashcard_results(self):
        while True:
            try:
                result = self._flashcard_results.get_nowait()
            except queue.Empty:
                break
            request_id, context, source, response, cards = result
            if (
                request_id != self._flashcard_request
                or context is not self._flashcard_context
            ):
                continue
            if self.flashcard_setup_window is None:
                continue
            try:
                if not self.flashcard_setup_window.winfo_exists():
                    continue
            except tk.TclError:
                continue
            self.flashcard_generate_button.configure(state="normal")
            if not response.ok:
                self.flashcard_setup_status.set(response.text)
                self.status.set("Flashcard generation failed")
                continue
            try:
                deck = self._get_notes_store().create_flashcard_deck(
                    self._flashcard_title(context), cards, source
                )
            except (OSError, sqlite3.Error, ValueError, RuntimeError) as error:
                self.flashcard_setup_status.set(f"Could not save flashcards: {error}")
                self.status.set(f"Could not save flashcards: {error}")
                continue
            self._close_flashcard_setup(force=True)
            self._open_flashcards(deck)
            self.status.set(f"Flashcards saved locally: {len(deck.cards)} cards")
        self.root.after(50, self._process_flashcard_results)

    @staticmethod
    def _flashcard_title(context):
        excerpt = " ".join(context.selected_text.split())
        if len(excerpt) > 48:
            excerpt = excerpt[:45].rstrip() + "…"
        return f"Flashcards: {excerpt}" if excerpt else "Passage flashcards"

    def _close_flashcard_setup(self, force=False):
        if self.flashcard_setup_window is None:
            return
        if not force:
            self._flashcard_request += 1
        self.flashcard_setup_window.destroy()
        self.flashcard_setup_window = None

    def open_flashcard_library(self):
        try:
            self._get_notes_store()
        except (OSError, sqlite3.Error, RuntimeError) as error:
            self.status.set(f"Saved flashcards are unavailable: {error}")
            messagebox.showerror(
                "Flashcards unavailable",
                f"The local study database could not be opened.\n\n{error}",
                parent=self.root,
            )
            return
        if self.flashcard_library_window is not None:
            try:
                if self.flashcard_library_window.winfo_exists():
                    self.flashcard_library_window.deiconify()
                    self.flashcard_library_window.lift()
                    self._refresh_flashcard_library()
                    return
            except tk.TclError:
                self.flashcard_library_window = None
        window = tk.Toplevel(self.root)
        self.flashcard_library_window = window
        window.title("Offline Reader — My flashcards")
        window.geometry("760x520")
        window.minsize(560, 380)
        window.configure(bg=self.BACKGROUND)
        window.transient(self.root)
        shell = tk.Frame(window, bg=self.PANEL, padx=14, pady=14)
        shell.pack(fill="both", expand=True, padx=14, pady=14)
        tk.Label(
            shell, text="SAVED FLASHCARD DECKS", bg=self.PANEL, fg=self.TEXT,
            font=("Segoe UI", 10, "bold"), anchor="w",
        ).pack(fill="x", pady=(0, 10))
        self.flashcard_library_list = tk.Listbox(
            shell, bg=self.PANEL, fg=self.TEXT,
            selectbackground=self.ACCENT, selectforeground=self.PRIMARY_TEXT,
            relief="flat", borderwidth=0, activestyle="none",
            font=("Segoe UI", 9), highlightthickness=0, selectmode="browse",
        )
        self.flashcard_library_list.pack(fill="both", expand=True)
        self.flashcard_library_list.bind(
            "<Double-Button-1>", self._open_selected_flashcard_deck
        )
        self.flashcard_library_list.bind(
            "<Return>", self._open_selected_flashcard_deck
        )
        self._flashcard_library_items = []
        footer = tk.Frame(shell, bg=self.PANEL)
        footer.pack(fill="x", pady=(10, 0))
        self.flashcard_library_status = tk.StringVar(value="")
        tk.Label(
            footer, textvariable=self.flashcard_library_status, bg=self.PANEL,
            fg=self.MUTED, font=("Segoe UI", 9), anchor="w",
        ).pack(side="left", fill="x", expand=True)
        self._button(
            footer, "Close", self._close_flashcard_library
        ).pack(side="right", padx=(5, 0))
        self._button(
            footer, "Delete", self._delete_selected_flashcard_deck
        ).pack(side="right", padx=(5, 0))
        self._button(
            footer, "Open", self._open_selected_flashcard_deck, primary=True
        ).pack(side="right", padx=(5, 0))
        window.protocol("WM_DELETE_WINDOW", self._close_flashcard_library)
        self._style_flashcard_library_window()
        self._refresh_flashcard_library()

    def _style_flashcard_library_window(self):
        if self.flashcard_library_window is None:
            return
        self._recolor_widget_tree(
            self.flashcard_library_window,
            self._theme_color_mapping("dark" if self.theme == "light" else "light", self.theme),
        )
        self.flashcard_library_window.configure(bg=self.BACKGROUND)

    def _refresh_flashcard_library(self):
        if (
            self.flashcard_library_window is None
            or not self.flashcard_library_window.winfo_exists()
        ):
            return
        try:
            self._flashcard_library_items = (
                self._get_notes_store().list_flashcard_decks()
            )
        except (OSError, sqlite3.Error, RuntimeError, ValueError) as error:
            self.flashcard_library_status.set(f"Could not load flashcards: {error}")
            return
        self.flashcard_library_list.delete(0, tk.END)
        for deck in self._flashcard_library_items:
            due_count = sum(
                datetime.fromisoformat(schedule.due_at)
                <= datetime.now(timezone.utc)
                for schedule in deck.schedules
            )
            self.flashcard_library_list.insert(
                tk.END,
                f"{deck.title} · {due_count} due / {len(deck.cards)} cards · "
                f"{Path(deck.source.document_path).name} · {deck.source.location}",
            )
        self.flashcard_library_status.set(
            f"{len(self._flashcard_library_items)} saved deck(s)"
            if self._flashcard_library_items
            else "No saved flashcards yet"
        )

    def _selected_flashcard_deck(self):
        selection = self.flashcard_library_list.curselection()
        if not selection or selection[0] >= len(self._flashcard_library_items):
            return None
        return self._flashcard_library_items[selection[0]]

    def _open_selected_flashcard_deck(self, _event=None):
        deck = self._selected_flashcard_deck()
        if deck is None:
            self.flashcard_library_status.set("Select a deck first")
            return "break"
        self._open_flashcards(deck)
        return "break"

    def _delete_selected_flashcard_deck(self):
        deck = self._selected_flashcard_deck()
        if deck is None:
            self.flashcard_library_status.set("Select a deck first")
            return
        if not messagebox.askyesno(
            "Delete flashcards",
            "Delete this saved deck and its review progress permanently?",
            parent=self.flashcard_library_window,
        ):
            return
        try:
            self._get_notes_store().delete_flashcard_deck(deck.id)
        except (OSError, sqlite3.Error, KeyError, RuntimeError) as error:
            self.flashcard_library_status.set(f"Delete failed: {error}")
            return
        self._refresh_flashcard_library()

    def _close_flashcard_library(self):
        if self.flashcard_library_window is not None:
            self.flashcard_library_window.destroy()
            self.flashcard_library_window = None

    def open_due_flashcards(self):
        try:
            due_cards = self._get_notes_store().list_due_flashcards()
        except (OSError, sqlite3.Error, RuntimeError, ValueError) as error:
            self.status.set(f"Could not load due flashcards: {error}")
            messagebox.showerror(
                "Flashcards unavailable",
                f"Due flashcards could not be loaded.\n\n{error}",
                parent=self.root,
            )
            return
        if not due_cards:
            self.status.set("No flashcards are due right now")
            messagebox.showinfo(
                "No flashcards due",
                "There are no flashcards due for review right now.",
                parent=self.root,
            )
            return
        self._open_flashcards(due_cards[0][0], due_queue=due_cards)

    def _open_flashcards(self, deck, due_queue=None):
        if self.flashcard_window is not None:
            try:
                if self.flashcard_window.winfo_exists():
                    self.flashcard_window.destroy()
            except tk.TclError:
                pass
        self._flashcard_due_queue = tuple(due_queue) if due_queue is not None else None
        self._flashcard_queue_index = 0
        if self._flashcard_due_queue:
            deck, first_index = self._flashcard_due_queue[0]
            self._flashcard_index = first_index
        else:
            self._flashcard_index = 0
        self._active_flashcard_deck = deck
        self._flashcard_due_indices = self._due_flashcard_indices(deck)
        self._flashcard_revealed = False
        window = tk.Toplevel(self.root)
        self.flashcard_window = window
        window.title(f"Offline Reader — {deck.title}")
        window.geometry("760x610")
        window.minsize(580, 460)
        window.configure(bg=self.BACKGROUND)
        window.transient(self.root)
        shell = tk.Frame(window, bg=self.PANEL, padx=16, pady=14)
        shell.pack(fill="both", expand=True, padx=14, pady=14)
        self.flashcard_deck_label = tk.Label(
            shell, text=deck.title, bg=self.PANEL, fg=self.TEXT,
            font=("Segoe UI", 12, "bold"), anchor="w",
        )
        self.flashcard_deck_label.pack(fill="x")
        self.flashcard_source_label = tk.Label(
            shell,
            text=f"{len(deck.cards)} cards · {Path(deck.source.document_path).name} · "
            f"{deck.source.location}",
            bg=self.PANEL, fg=self.MUTED, font=("Segoe UI", 9), anchor="w",
        )
        self.flashcard_source_label.pack(fill="x", pady=(4, 10))
        self.flashcard_number = tk.StringVar()
        tk.Label(
            shell, textvariable=self.flashcard_number, bg=self.PANEL, fg=self.MUTED,
            font=("Segoe UI", 9, "bold"), anchor="w",
        ).pack(fill="x")
        self.flashcard_front_label = tk.Label(
            shell, bg=self.PANEL, fg=self.TEXT, font=("Segoe UI", 14, "bold"),
            anchor="center", justify="center", wraplength=660,
        )
        self.flashcard_front_label.pack(fill="both", expand=True, pady=(16, 8))
        self.flashcard_back_label = tk.Label(
            shell, bg=self.PANEL, fg=self.TEXT, font=("Segoe UI", 11),
            anchor="center", justify="center", wraplength=660,
        )
        self.flashcard_back_label.pack(fill="x", pady=(0, 12))
        tk.Label(
            shell,
            text="Again: 10 min · Hard: slower · Good: 1/6 days · Easy: 4 days initially",
            bg=self.PANEL,
            fg=self.MUTED,
            font=("Segoe UI", 9),
            anchor="w",
        ).pack(fill="x", pady=(0, 8))
        rating_row = tk.Frame(shell, bg=self.PANEL)
        rating_row.pack(fill="x", pady=(0, 8))
        self.flashcard_rating_buttons = {}
        for rating, label in (
            ("again", "Again"),
            ("hard", "Hard"),
            ("good", "Good"),
            ("easy", "Easy"),
        ):
            button = self._button(
                rating_row,
                label,
                lambda choice=rating: self._rate_flashcard(choice),
            )
            button.configure(state="disabled")
            button.pack(side="left", padx=(0, 5))
            self.flashcard_rating_buttons[rating] = button
        footer = tk.Frame(shell, bg=self.PANEL)
        footer.pack(fill="x", pady=(12, 0))
        self.flashcard_status = tk.StringVar(
            value="Reveal the back when you are ready."
        )
        tk.Label(
            footer, textvariable=self.flashcard_status, bg=self.PANEL, fg=self.MUTED,
            font=("Segoe UI", 9), anchor="w",
        ).pack(side="left", fill="x", expand=True)
        self._button(
            footer, "Open source", self._open_flashcard_source
        ).pack(side="right", padx=(5, 0))
        self._button(
            footer, "Next", lambda: self._navigate_flashcard(1)
        ).pack(side="right", padx=(5, 0))
        self._button(
            footer, "Previous", lambda: self._navigate_flashcard(-1)
        ).pack(side="right", padx=(5, 0))
        self.flashcard_reveal_button = self._button(
            footer, "Show answer", self._reveal_flashcard, primary=True
        )
        self.flashcard_reveal_button.pack(side="right", padx=(5, 0))
        window.protocol("WM_DELETE_WINDOW", self._close_flashcards)
        self._style_flashcard_window()
        self._render_flashcard()

    def _style_flashcard_window(self):
        if self.flashcard_window is None:
            return
        self._recolor_widget_tree(
            self.flashcard_window,
            self._theme_color_mapping("dark" if self.theme == "light" else "light", self.theme),
        )
        self.flashcard_window.configure(bg=self.BACKGROUND)

    def _render_flashcard(self):
        if self.flashcard_window is None or not self.flashcard_window.winfo_exists():
            return
        if self._flashcard_due_queue is not None:
            if self._flashcard_queue_index >= len(self._flashcard_due_queue):
                deck = self._active_flashcard_deck
                self.flashcard_number.set("Review complete")
                self.flashcard_front_label.configure(
                    text="No more cards are due in this session."
                )
                self.flashcard_back_label.configure(text="")
                self.flashcard_status.set("All currently due cards have been rated.")
                self.flashcard_reveal_button.configure(
                    state="disabled", text="Session complete"
                )
                for button in self.flashcard_rating_buttons.values():
                    button.configure(state="disabled")
                return
            deck, index = self._flashcard_due_queue[self._flashcard_queue_index]
            self._active_flashcard_deck = deck
            self._flashcard_index = index
            due_count = len(self._flashcard_due_queue)
            self._flashcard_due_indices = self._due_flashcard_indices(deck)
        else:
            deck = self._active_flashcard_deck
            self._flashcard_due_indices = self._due_flashcard_indices(deck)
            if not self._flashcard_due_indices:
                deck = self._active_flashcard_deck
            else:
                due_count = len(self._flashcard_due_indices)
                if self._flashcard_index not in self._flashcard_due_indices:
                    self._flashcard_index = self._flashcard_due_indices[0]
                index = self._flashcard_index
        if self._flashcard_due_queue is None and not self._flashcard_due_indices:
            next_due = min(
                (schedule.due_at for schedule in deck.schedules),
                default=None,
            )
            self.flashcard_number.set("Review complete")
            self.flashcard_front_label.configure(text="No cards are due right now.")
            if next_due is not None:
                due_label = datetime.fromisoformat(next_due).astimezone().strftime(
                    "%Y-%m-%d %H:%M"
                )
                self.flashcard_status.set(f"Next scheduled review: {due_label}")
            self.flashcard_back_label.configure(text="")
            self.flashcard_reveal_button.configure(
                state="disabled", text="No cards due"
            )
            for button in self.flashcard_rating_buttons.values():
                button.configure(state="disabled")
            return
        card = deck.cards[index]
        self._flashcard_revealed = False
        self.flashcard_deck_label.configure(text=deck.title)
        self.flashcard_source_label.configure(
            text=f"{len(deck.cards)} cards · "
            f"{Path(deck.source.document_path).name} · {deck.source.location}"
        )
        self.flashcard_window.title(f"Offline Reader — {deck.title}")
        if self._flashcard_due_queue is not None:
            self.flashcard_number.set(
                f"Due card {self._flashcard_queue_index + 1} of {due_count} · "
                f"{Path(deck.source.document_path).name}"
            )
        else:
            self.flashcard_number.set(
                f"Card {index + 1} of {len(deck.cards)} · {due_count} due"
            )
        self.flashcard_front_label.configure(text=card.front)
        self.flashcard_back_label.configure(text="")
        self.flashcard_reveal_button.configure(state="normal", text="Show answer")
        for button in self.flashcard_rating_buttons.values():
            button.configure(state="disabled")
        self.flashcard_status.set("Recall the answer, then reveal and rate it.")

    def _reveal_flashcard(self):
        if self._flashcard_revealed:
            return
        card = self._active_flashcard_deck.cards[self._flashcard_index]
        self._flashcard_revealed = True
        self.flashcard_back_label.configure(
            text=f"{card.back}\n\n{card.explanation}"
        )
        self.flashcard_reveal_button.configure(state="disabled", text="Answer shown")
        for button in self.flashcard_rating_buttons.values():
            button.configure(state="normal")
        self.flashcard_status.set(
            "Rate your recall: Again, Hard, Good, or Easy."
        )

    @staticmethod
    def _due_flashcard_indices(deck, now=None):
        current_time = now or datetime.now(timezone.utc)
        return [
            index
            for index, schedule in enumerate(deck.schedules)
            if datetime.fromisoformat(schedule.due_at) <= current_time
        ]

    def _rate_flashcard(self, rating):
        deck = self._active_flashcard_deck
        index = self._flashcard_index
        try:
            store = self._get_notes_store()
            deck = store.record_flashcard_review(
                deck.id, index, rating
            )
        except (OSError, sqlite3.Error, KeyError, ValueError, RuntimeError) as error:
            self.flashcard_status.set(f"Could not save review rating: {error}")
            return
        self._active_flashcard_deck = deck
        self.flashcard_status.set(
            f"Rated {rating.title()}. Loading the next due card…"
        )
        if self.flashcard_library_window is not None:
            self._refresh_flashcard_library()
        if self._flashcard_due_queue is not None:
            try:
                self._flashcard_due_queue = tuple(store.list_due_flashcards())
            except (OSError, sqlite3.Error, RuntimeError, ValueError) as error:
                self.flashcard_status.set(f"Could not load due cards: {error}")
                return
            self._flashcard_queue_index = 0
            if self._flashcard_due_queue:
                self._active_flashcard_deck = self._flashcard_due_queue[0][0]
                self._flashcard_index = self._flashcard_due_queue[0][1]
            self._render_flashcard()
            return
        self._flashcard_due_indices = self._due_flashcard_indices(deck)
        if self._flashcard_due_indices:
            self._flashcard_index = self._flashcard_due_indices[0]
        self._render_flashcard()

    def _navigate_flashcard(self, direction):
        if self._flashcard_due_queue is not None:
            target = self._flashcard_queue_index + direction
            if not 0 <= target < len(self._flashcard_due_queue):
                return
            self._flashcard_queue_index = target
            self._active_flashcard_deck, self._flashcard_index = (
                self._flashcard_due_queue[target]
            )
            self._render_flashcard()
            return
        due_indices = self._due_flashcard_indices(self._active_flashcard_deck)
        if not due_indices or self._flashcard_index not in due_indices:
            return
        position = due_indices.index(self._flashcard_index) + direction
        if not 0 <= position < len(due_indices):
            return
        self._flashcard_index = due_indices[position]
        self._render_flashcard()

    def _open_flashcard_source(self):
        source = self._active_flashcard_deck.source
        self._open_verified_source(
            source, self.flashcard_window, self.flashcard_status.set
        )

    def _close_flashcards(self):
        if self.flashcard_window is not None:
            self.flashcard_window.destroy()
            self.flashcard_window = None

    def _current_document_fingerprint(self):
        if self.document_path is None:
            return None
        try:
            stat = self.document_path.stat()
            signature = (stat.st_size, stat.st_mtime_ns)
        except OSError:
            self._annotation_fingerprint = None
            raise
        cached = self._annotation_fingerprint
        if cached is None or cached[0] != str(self.document_path) or cached[1] != signature:
            fingerprint = fingerprint_file(self.document_path)
            self._annotation_fingerprint = (
                str(self.document_path),
                signature,
                fingerprint,
            )
        return self._annotation_fingerprint[2]

    def _configure_saved_highlight_tag(self):
        self.text.tag_configure(
            "saved_highlight",
            background=self.PDF_HIGHLIGHT,
            foreground=self.HIGHLIGHT_TEXT,
        )
        self.text.tag_lower("saved_highlight")

    def _refresh_text_annotations(self):
        if not hasattr(self, "text"):
            return
        self.text.tag_remove("saved_highlight", "1.0", tk.END)
        if self.document_type != "text" or self.document_path is None:
            return
        self._configure_saved_highlight_tag()
        try:
            fingerprint = self._current_document_fingerprint()
            highlights = self._get_notes_store().list_annotations(
                self.document_path, fingerprint, "highlight"
            )
        except (OSError, sqlite3.Error, RuntimeError, ValueError) as error:
            self.status.set(f"Could not load document highlights: {error}")
            return
        for annotation in highlights:
            source = annotation.source
            if source.char_start is None or source.char_end is None:
                continue
            start = f"1.0 + {source.char_start} chars"
            end = f"1.0 + {source.char_end} chars"
            self.text.tag_add("saved_highlight", start, end)

    def _render_saved_pdf_highlights(self):
        if self.pdf_document is None or self.document_path is None:
            return
        self.pdf_canvas.delete("saved_highlight")
        try:
            fingerprint = self._current_document_fingerprint()
            highlights = self._get_notes_store().list_annotations(
                self.document_path, fingerprint, "highlight"
            )
        except (OSError, sqlite3.Error, RuntimeError, ValueError) as error:
            self.status.set(f"Could not load document highlights: {error}")
            return
        for annotation in highlights:
            source = annotation.source
            if source.page_index != self.pdf_page_index or source.rect is None:
                continue
            left, top, right, bottom = source.rect
            self.pdf_canvas.create_rectangle(
                self._page_image_left + left * self._render_scale,
                self._page_image_top + top * self._render_scale,
                self._page_image_left + right * self._render_scale,
                self._page_image_top + bottom * self._render_scale,
                fill=self.PDF_HIGHLIGHT,
                stipple="gray50",
                outline=self.PDF_HIGHLIGHT_BORDER,
                width=2,
                tags=("saved_highlight",),
            )
        self.pdf_canvas.tag_raise("saved_highlight")

    def _open_verified_source(self, source, parent, set_status):
        path = Path(source.document_path)
        if not path.is_file():
            set_status("Source file is missing; no jump was made")
            messagebox.showwarning(
                "Source unavailable",
                f"The linked source file could not be found:\n\n{path}",
                parent=parent,
            )
            return
        try:
            current_fingerprint = fingerprint_file(path)
        except OSError as error:
            set_status(f"Could not verify source: {error}")
            return
        if current_fingerprint != source.fingerprint:
            set_status("Source has changed; no jump was made")
            messagebox.showwarning(
                "Source changed",
                "This file's contents differ from the version used when the item was created. "
                "No passage jump was made.",
                parent=parent,
            )
            return
        self.open_source(source)
        set_status(f"Opened source at {source.location}")

    def _source_reference_for_context(self, context):
        if self.document_path is None:
            raise RuntimeError("Open the source document again before saving this note.")
        if not self.document_path.is_file():
            raise OSError(f"Source document is no longer available: {self.document_path}")
        return SourceReference(
            document_path=str(self.document_path.resolve()),
            document_format=context.document_format,
            fingerprint=self._current_document_fingerprint(),
            location=context.location,
            text=context.selected_text,
            char_start=context.char_start,
            char_end=context.char_end,
            page_index=context.page_index,
            rect=context.selection_rect,
        )

    def _create_note_from_context(self, context, selected_text, explanation, is_error):
        try:
            source = self._source_reference_for_context(context)
            title = " ".join(selected_text.split()).strip()
            if len(title) > 72:
                title = title[:69].rstrip() + "…"
            body = f"## Source passage\n{selected_text.strip()}"
            if explanation.strip() and explanation.strip() != "Thinking…" and not is_error:
                body += f"\n\n## AI explanation\n{explanation.strip()}"
            note = self._get_notes_store().create_note(
                title=title or "Study note",
                body=body,
                source=source,
            )
        except (OSError, sqlite3.Error, ValueError, RuntimeError) as error:
            self.status.set(f"Could not save note: {error}")
            messagebox.showerror(
                "Could not save note",
                f"The note was not saved.\n\n{error}",
                parent=self.root,
            )
            return
        self.status.set("Source-linked note saved")
        self.open_notes(note_id=note.id)

    def open_notes(self, note_id=None):
        try:
            store = self._get_notes_store()
        except (OSError, sqlite3.Error, RuntimeError) as error:
            self.status.set(f"Notes are unavailable: {error}")
            messagebox.showerror(
                "Notes unavailable",
                f"The local notes database could not be opened.\n\n{error}",
                parent=self.root,
            )
            return

        if self.notes_window is not None:
            try:
                if self.notes_window.winfo_exists():
                    self.notes_window.deiconify()
                    self.notes_window.lift()
                    if (
                        note_id is not None
                        and (
                            self._notes_current_note is None
                            or self._notes_current_note.id != note_id
                        )
                        and not self._notes_confirm_discard_or_save()
                    ):
                        return
                    self._notes_refresh_list(select_id=note_id)
                    return
            except tk.TclError:
                self.notes_window = None

        window = tk.Toplevel(self.root)
        self.notes_window = window
        window.title("Offline Reader — My notes")
        window.geometry("900x620")
        window.minsize(680, 460)
        window.configure(bg=self.BACKGROUND)
        window.transient(self.root)

        shell = tk.Frame(window, bg=self.BACKGROUND, padx=14, pady=14)
        shell.pack(fill="both", expand=True)
        left = tk.Frame(shell, bg=self.PANEL, width=260)
        left.pack(side="left", fill="y")
        left.pack_propagate(False)
        right = tk.Frame(shell, bg=self.PANEL, padx=14, pady=12)
        right.pack(side="left", fill="both", expand=True, padx=(12, 0))

        tk.Label(
            left, text="MY NOTES", bg=self.PANEL, fg=self.TEXT,
            font=("Segoe UI", 10, "bold"), anchor="w", padx=12, pady=12,
        ).pack(fill="x")
        self.notes_search = tk.Entry(
            left, bg=self.EDITOR, fg=self.TEXT, insertbackground=self.TEXT,
            relief="flat", font=("Segoe UI", 10),
        )
        self.notes_search.pack(fill="x", padx=10, pady=(0, 8), ipady=6)
        self.notes_search.bind("<KeyRelease>", lambda _event: self._notes_refresh_list())
        notes_list_frame = tk.Frame(left, bg=self.PANEL)
        notes_list_frame.pack(fill="both", expand=True, padx=8, pady=(0, 8))
        self.notes_list = tk.Listbox(
            notes_list_frame, bg=self.PANEL, fg=self.TEXT,
            selectbackground=self.ACCENT, selectforeground=self.PRIMARY_TEXT,
            relief="flat", borderwidth=0, activestyle="none",
            font=("Segoe UI", 9), highlightthickness=0, selectmode="browse",
        )
        notes_scroll = tk.Scrollbar(
            notes_list_frame, orient="vertical", command=self.notes_list.yview,
        )
        self.notes_list.configure(yscrollcommand=notes_scroll.set)
        self.notes_list.pack(side="left", fill="both", expand=True)
        notes_scroll.pack(side="right", fill="y")
        self.notes_list.bind("<<ListboxSelect>>", self._notes_select_item)
        self.notes_list.bind("<Return>", self._notes_select_item)
        self.notes_list.bind("<Double-Button-1>", self._notes_select_item)
        self.notes_list_ids = []
        self._notes_current_note = None
        self._notes_snapshot = None

        tk.Label(
            right, text="Title", bg=self.PANEL, fg=self.MUTED,
            font=("Segoe UI", 9, "bold"), anchor="w",
        ).pack(fill="x")
        self.notes_title = tk.Entry(
            right, bg=self.EDITOR, fg=self.TEXT, insertbackground=self.TEXT,
            relief="flat", font=("Segoe UI", 12, "bold"),
        )
        self.notes_title.pack(fill="x", pady=(5, 10), ipady=7)
        metadata = tk.Frame(right, bg=self.PANEL)
        metadata.pack(fill="x", pady=(0, 10))
        tk.Label(
            metadata, text="Collection", bg=self.PANEL, fg=self.MUTED,
            font=("Segoe UI", 9),
        ).pack(side="left")
        self.notes_collection = tk.Entry(
            metadata, bg=self.EDITOR, fg=self.TEXT, insertbackground=self.TEXT,
            relief="flat", font=("Segoe UI", 9),
        )
        self.notes_collection.pack(side="left", fill="x", expand=True, padx=(6, 12), ipady=5)
        tk.Label(
            metadata, text="Tags", bg=self.PANEL, fg=self.MUTED,
            font=("Segoe UI", 9),
        ).pack(side="left")
        self.notes_tags = tk.Entry(
            metadata, bg=self.EDITOR, fg=self.TEXT, insertbackground=self.TEXT,
            relief="flat", font=("Segoe UI", 9),
        )
        self.notes_tags.pack(side="left", fill="x", expand=True, padx=(6, 0), ipady=5)

        self.notes_source_label = tk.Label(
            right, text="No linked source", bg=self.PANEL, fg=self.MUTED,
            font=("Segoe UI", 9), anchor="w",
        )
        self.notes_source_label.pack(fill="x", pady=(0, 8))
        self.notes_body = tk.Text(
            right, wrap="word", bg=self.EDITOR, fg=self.READER_TEXT,
            insertbackground=self.READER_TEXT, relief="flat",
            font=("Noto Sans", 10), padx=10, pady=10,
            highlightthickness=1, highlightbackground=self.BORDER,
            highlightcolor=self.FOCUS,
        )
        self.notes_body.pack(fill="both", expand=True)

        footer = tk.Frame(right, bg=self.PANEL)
        footer.pack(fill="x", pady=(10, 0))
        self.notes_status = tk.StringVar(value="Notes are stored locally")
        tk.Label(
            footer, textvariable=self.notes_status, bg=self.PANEL, fg=self.MUTED,
            font=("Segoe UI", 9), anchor="w",
        ).pack(side="left", fill="x", expand=True)
        self._button(footer, "New", self._notes_new).pack(side="right", padx=(5, 0))
        self._button(footer, "Delete", self._notes_delete).pack(side="right", padx=(5, 0))
        self._button(footer, "Open source", self._notes_open_source).pack(side="right", padx=(5, 0))
        self._button(footer, "Save", self._notes_save, primary=True).pack(side="right", padx=(5, 0))
        window.protocol("WM_DELETE_WINDOW", self._notes_close_window)
        self._style_notes_window()
        self._notes_refresh_list(select_id=note_id)

    def _style_notes_window(self):
        if self.notes_window is None:
            return
        self._recolor_widget_tree(
            self.notes_window,
            self._theme_color_mapping("dark" if self.theme == "light" else "light", self.theme),
        )
        self.notes_window.configure(bg=self.BACKGROUND)

    def _notes_field_values(self):
        return (
            self.notes_title.get().strip(),
            self.notes_body.get("1.0", "end-1c"),
            self.notes_collection.get().strip(),
            self.notes_tags.get().strip(),
        )

    def _notes_is_dirty(self):
        values = self._notes_field_values()
        if self._notes_snapshot is None:
            return any(values)
        return values != self._notes_snapshot

    def _notes_save(self):
        title, body, collection, tags = self._notes_field_values()
        try:
            store = self._get_notes_store()
            if self._notes_current_note is None:
                note = store.create_note(title, body, tags=tags, collection=collection)
            else:
                note = store.update_note(
                    self._notes_current_note.id, title, body,
                    tags=tags, collection=collection,
                )
        except (OSError, sqlite3.Error, ValueError, RuntimeError, KeyError) as error:
            self.notes_status.set(f"Save failed: {error}")
            self.status.set(f"Could not save note: {error}")
            return False
        self._notes_load(note)
        self._notes_refresh_list(select_id=note.id)
        self.notes_status.set("Saved locally")
        return True

    def _notes_load(self, note):
        self._notes_current_note = note
        self.notes_title.delete(0, tk.END)
        self.notes_title.insert(0, note.title)
        self.notes_collection.delete(0, tk.END)
        self.notes_collection.insert(0, note.collection or "")
        self.notes_tags.delete(0, tk.END)
        self.notes_tags.insert(0, ", ".join(note.tags))
        self.notes_body.delete("1.0", tk.END)
        self.notes_body.insert("1.0", note.body)
        if note.source is None:
            self.notes_source_label.configure(text="No linked source")
        else:
            self.notes_source_label.configure(
                text=f"Source: {Path(note.source.document_path).name} · {note.source.location}"
            )
        self._notes_snapshot = self._notes_field_values()

    def _notes_refresh_list(self, select_id=None):
        if self.notes_window is None or not self.notes_window.winfo_exists():
            return
        if select_id is None and self._notes_current_note is not None:
            select_id = self._notes_current_note.id
        try:
            notes = self._get_notes_store().list_notes(self.notes_search.get())
        except (OSError, sqlite3.Error, RuntimeError) as error:
            self.notes_status.set(f"Could not load notes: {error}")
            return
        self.notes_list.delete(0, tk.END)
        self.notes_list_ids = []
        for note in notes:
            self.notes_list_ids.append(note.id)
            source_label = f" · {note.source.location}" if note.source else ""
            self.notes_list.insert(tk.END, f"{note.title}{source_label}")
        if select_id in self.notes_list_ids:
            index = self.notes_list_ids.index(select_id)
            self.notes_list.selection_set(index)
            self.notes_list.see(index)
            if self._notes_current_note is None or self._notes_current_note.id != select_id:
                try:
                    note = self._get_notes_store().get_note(select_id)
                except (OSError, sqlite3.Error, RuntimeError) as error:
                    self.notes_status.set(f"Could not open note: {error}")
                    return
                if note is not None:
                    self._notes_load(note)

    def _notes_select_item(self, _event=None):
        selection = self.notes_list.curselection()
        if not selection or selection[0] >= len(self.notes_list_ids):
            return "break"
        target_id = self.notes_list_ids[selection[0]]
        if self._notes_current_note is not None and target_id == self._notes_current_note.id:
            return "break"
        if not self._notes_confirm_discard_or_save():
            if self._notes_current_note is not None:
                current = self.notes_list_ids.index(self._notes_current_note.id) if self._notes_current_note.id in self.notes_list_ids else None
                if current is not None:
                    self.notes_list.selection_clear(0, tk.END)
                    self.notes_list.selection_set(current)
            return "break"
        try:
            note = self._get_notes_store().get_note(target_id)
        except (OSError, sqlite3.Error, RuntimeError) as error:
            self.notes_status.set(f"Could not open note: {error}")
            return "break"
        if note is not None:
            self._notes_load(note)
            self.notes_status.set("Saved locally")
        return "break"

    def _notes_confirm_discard_or_save(self):
        if not self._notes_is_dirty():
            return True
        choice = messagebox.askyesnocancel(
            "Unsaved note",
            "Save your changes before continuing?",
            parent=self.notes_window,
        )
        if choice is None:
            return False
        return self._notes_save() if choice else True

    def _notes_new(self):
        if not self._notes_confirm_discard_or_save():
            return
        self._notes_current_note = None
        self._notes_snapshot = None
        self.notes_list.selection_clear(0, tk.END)
        self.notes_title.delete(0, tk.END)
        self.notes_collection.delete(0, tk.END)
        self.notes_tags.delete(0, tk.END)
        self.notes_body.delete("1.0", tk.END)
        self.notes_source_label.configure(text="No linked source")
        self.notes_status.set("New note — not saved")
        self.notes_title.focus_set()

    def _notes_delete(self):
        if self._notes_current_note is None:
            return
        if self._notes_is_dirty() and not self._notes_confirm_discard_or_save():
            return
        if not messagebox.askyesno(
            "Delete note",
            "Delete this note permanently?",
            parent=self.notes_window,
        ):
            return
        try:
            self._get_notes_store().delete_note(self._notes_current_note.id)
        except (OSError, sqlite3.Error, KeyError, RuntimeError) as error:
            self.notes_status.set(f"Delete failed: {error}")
            return
        self._notes_current_note = None
        self._notes_snapshot = None
        self.notes_list.selection_clear(0, tk.END)
        self.notes_title.delete(0, tk.END)
        self.notes_collection.delete(0, tk.END)
        self.notes_tags.delete(0, tk.END)
        self.notes_body.delete("1.0", tk.END)
        self.notes_source_label.configure(text="No linked source")
        self.notes_status.set("Note deleted")
        self._notes_refresh_list()

    def _notes_open_source(self):
        note = self._notes_current_note
        if note is None or note.source is None:
            self.notes_status.set("This note has no linked source")
            return
        self._open_verified_source(
            note.source, self.notes_window, self.notes_status.set
        )

    def _notes_close_window(self):
        if not self._notes_confirm_discard_or_save():
            return
        self.notes_window.destroy()
        self.notes_window = None

    def open_source(self, source):
        """Navigate to a search result or AI citation in the current document."""
        source_label = (
            f"[{source.ref}]" if hasattr(source, "ref") else "Saved note"
        )
        self._close_active_popup()
        if self.document_path is None or str(self.document_path) != source.document_path:
            try:
                self._open_path(Path(source.document_path))
            except Exception:
                self.status.set("Could not reopen the cited document")
                return
        if source.page_index is not None and self.pdf_document is not None:
            self._go_to_page(source.page_index)
            rectangles = self.pdf_document.find_text_rects(source.page_index, source.text)
            for left, top, right, bottom in rectangles:
                item = self.pdf_canvas.create_rectangle(
                    self._page_image_left + left * self._render_scale,
                    self._page_image_top + top * self._render_scale,
                    self._page_image_left + right * self._render_scale,
                    self._page_image_top + bottom * self._render_scale,
                    fill=self.PDF_HIGHLIGHT, stipple="gray50", outline=self.PDF_HIGHLIGHT_BORDER, width=2,
                    tags="source_highlight",
                )
                self._source_highlight_ids.append(item)
            if rectangles:
                self.pdf_canvas.tag_raise("source_highlight")
            self.status.set(
                f"Opened source {source_label} · Page {source.page_index + 1}"
                + (" · passage highlighted" if rectangles else " · page opened; exact text highlight unavailable")
            )
            return
        if source.char_start is not None and self.document_type == "text":
            start = f"1.0 + {source.char_start} chars"
            end = f"1.0 + {source.char_end or source.char_start + len(source.text)} chars"
            self.text.tag_remove("search_hit", "1.0", tk.END)
            self.text.tag_configure(
                "search_hit",
                background=self.SEARCH_HIGHLIGHT,
                foreground=self.HIGHLIGHT_TEXT,
            )
            self.text.tag_add("search_hit", start, end)
            self.text.see(start)
            self.status.set(f"Opened source {source_label} · {source.location}")

    def close_file(self):
        self._save_current_document_state()
        self._selection_request += 1
        if self._selection_job is not None:
            try:
                self.root.after_cancel(self._selection_job)
            except tk.TclError:
                pass
            self._selection_job = None
        self.last_selection = None
        self._close_active_popup()
        self.controller.close_document()
        self.document_path = None
        self.document_type = None
        self._clear_document_search()
        self.reading_zoom_out_button.config(text="A−")
        self.reading_zoom_in_button.config(text="A+")
        self.pdf_document = None
        self.text.config(state="normal")
        self.text.delete("1.0", tk.END)
        self.text.config(state="disabled")
        self._set_text_column_visible(False)
        self.text_empty.place(relx=0.5, rely=0.5, anchor="center")
        self.text_empty.lift()
        self.pdf_viewer.pack_forget()
        self.toolbar.pack_forget()
        self.text_viewer.pack(fill="both", expand=True)
        self.file_label.config(text="No document selected")
        self.root.title("Offline Reader")
        self.status.set("Ready | Open a TXT or PDF document")
        self._set_pdf_controls_enabled(False)

    def _build_pdf_viewer(self):
        self.pdf_viewer = tk.Frame(self.viewer_host, bg=self.BACKGROUND)
        sidebar = tk.Frame(self.pdf_viewer, bg=self.PANEL, width=160)
        self.pdf_sidebar = sidebar
        sidebar.pack(side="left", fill="y", padx=(0, 12))
        sidebar.pack_propagate(False)
        tk.Label(sidebar, text="PAGES", bg=self.PANEL, fg=self.MUTED, font=("Segoe UI", 9, "bold"), padx=12, pady=10, anchor="w").pack(fill="x")
        page_list_frame = tk.Frame(sidebar, bg=self.PANEL)
        page_list_frame.pack(fill="both", expand=True, padx=8, pady=(0, 8))
        self.page_list = tk.Listbox(page_list_frame, bg=self.PANEL, fg=self.TEXT, selectbackground=self.ACCENT, selectforeground=self.PRIMARY_TEXT, relief="flat", borderwidth=0, activestyle="none", font=("Segoe UI", 9))
        list_scrollbar = tk.Scrollbar(page_list_frame, orient="vertical", command=self.page_list.yview)
        self.page_list.configure(yscrollcommand=list_scrollbar.set)
        self.page_list.pack(side="left", fill="both", expand=True)
        list_scrollbar.pack(side="right", fill="y")
        self.page_list.bind("<<ListboxSelect>>", self._select_page_from_sidebar)

        viewport = tk.Frame(self.pdf_viewer, bg=self.PDF_SURFACE)
        self.pdf_viewport = viewport
        viewport.pack(side="left", fill="both", expand=True)
        y_scrollbar = tk.Scrollbar(viewport, orient="vertical")
        x_scrollbar = tk.Scrollbar(viewport, orient="horizontal")
        self.pdf_canvas = tk.Canvas(viewport, bg=self.PDF_SURFACE, highlightthickness=0, borderwidth=0, yscrollcommand=y_scrollbar.set, xscrollcommand=x_scrollbar.set)
        y_scrollbar.config(command=self.pdf_canvas.yview)
        x_scrollbar.config(command=self.pdf_canvas.xview)
        y_scrollbar.pack(side="right", fill="y")
        x_scrollbar.pack(side="bottom", fill="x")
        self.pdf_canvas.pack(side="left", fill="both", expand=True)
        self.pdf_canvas.bind("<Configure>", self._on_pdf_viewport_resize)
        # Linux emits Button-4/Button-5 instead of MouseWheel. Route all
        # platforms through one boundary-aware handler; plain yview scrolling
        # alone can never change pages when the rendered page fits the viewport.
        self.pdf_canvas.bind("<MouseWheel>", self._scroll_pdf)
        self.pdf_canvas.bind("<Button-4>", self._scroll_pdf)
        self.pdf_canvas.bind("<Button-5>", self._scroll_pdf)
        self.pdf_canvas.bind("<ButtonPress-1>", self._start_pdf_selection)
        self.pdf_canvas.bind("<B1-Motion>", self._update_pdf_selection)
        self.pdf_canvas.bind("<ButtonRelease-1>", self._finish_pdf_selection)

    def _load_settings(self):
        try:
            data = json.loads(self.settings_path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (OSError, json.JSONDecodeError):
            return {}

    def _save_settings(self):
        self.settings_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "last_directory": str(self.last_directory),
            "recent_documents": self.recent_documents[:8],
            "text_font_size": self.text_font_size,
            "line_spacing": self._line_spacing,
            "theme": self.theme,
            "document_states": self.document_states,
        }
        temporary_path = self.settings_path.with_suffix(".tmp")
        temporary_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        temporary_path.replace(self.settings_path)

    def _save_current_document_state(self):
        if self.document_path is None or self.document_type is None:
            return
        state = self.document_states.get(str(self.document_path), {})
        if not isinstance(state, dict):
            state = {}
        if self.document_type == "text":
            try:
                state["text_yview"] = float(self.text.yview()[0])
            except (tk.TclError, AttributeError, TypeError, ValueError):
                pass
        elif self.document_type == "pdf":
            state["pdf_page"] = self.pdf_page_index
            state["pdf_zoom"] = self.pdf_zoom
            state["fit_width"] = self.fit_width_enabled
        self.document_states[str(self.document_path)] = state
        try:
            self._save_settings()
        except OSError:
            pass

    def _refresh_recent_menu(self):
        self.recent_menu.delete(0, tk.END)
        existing = [item for item in self.recent_documents if Path(item).is_file()]
        self.recent_documents = existing[:8]
        self._refresh_recent_sidebar()
        if not self.recent_documents:
            self.recent_menu.add_command(label="No recent documents", state="disabled")
            return
        for item in self.recent_documents:
            path = Path(item)
            label = f"{path.name}  ·  {path.parent}"
            self.recent_menu.add_command(label=label, command=lambda value=item: self._open_path(Path(value)))

    def open_file(self):
        initial_directory = self.last_directory if self.last_directory.is_dir() else Path.home()
        file_path = filedialog.askopenfilename(
            parent=self.root,
            initialdir=str(initial_directory),
            title="Open a document",
            filetypes=[("Supported documents", "*.txt *.TXT *.pdf *.PDF"), ("Text files", "*.txt *.TXT"), ("PDF files", "*.pdf *.PDF")],
        )
        if file_path:
            self._open_path(Path(file_path))

    def _open_path(self, path):
        path = Path(path).expanduser()
        self._save_current_document_state()
        self.status.set(f"Opening {path.name}…")
        self.root.update_idletasks()
        try:
            if path.suffix.lower() == ".txt":
                self._open_text_document(path)
            elif path.suffix.lower() == ".pdf":
                self._open_pdf_document(path)
            else:
                raise DocumentOpenError("Please choose a TXT or PDF file.")
        except DocumentOpenError as error:
            self.status.set(f"Could not open {path.name}")
            messagebox.showerror("Could not open document", f"{path.name}\n\n{error}", parent=self.root)

    def _prepare_new_document(self, path, document_type):
        self._selection_request += 1
        if self._selection_job is not None:
            try:
                self.root.after_cancel(self._selection_job)
            except tk.TclError:
                pass
            self._selection_job = None
        self._close_active_popup()
        self.last_selection = None
        self._clear_document_search()
        self._clear_pdf_view()
        self.document_path = path
        self.document_type = document_type
        self._annotation_fingerprint = None
        zoom_label = "−" if document_type == "pdf" else "A−"
        zoom_in_label = "+" if document_type == "pdf" else "A+"
        self.reading_zoom_out_button.config(text=zoom_label)
        self.reading_zoom_in_button.config(text=zoom_in_label)
        self.last_directory = path.parent
        self.recent_documents = [str(path)] + [item for item in self.recent_documents if item != str(path)]
        self.recent_documents = self.recent_documents[:8]
        try:
            self._save_settings()
        except OSError:
            # Reading must still work if the user's configuration directory is read-only.
            pass
        self._refresh_recent_menu()
        self.file_label.config(text=path.name)
        self.root.title(f"{path.name} — Offline Reader")
        self.text_empty.place_forget()

    def _open_text_document(self, path):
        document = self.controller.open_document(path)
        if not isinstance(document, TextDocument):
            raise DocumentOpenError("The selected document is not text.")
        self._prepare_new_document(path, "text")
        self.pdf_document = None
        self.pdf_viewer.pack_forget()
        self.toolbar.pack_forget()
        self.text_viewer.pack(fill="both", expand=True)
        self._set_text_column_visible(True)
        self.text.config(state="normal")
        self.text.delete("1.0", tk.END)
        self.text.insert("1.0", document.content)
        self.text.config(state="disabled")
        self._refresh_text_annotations()
        state = self.document_states.get(str(path), {})
        try:
            self.text.yview_moveto(max(0.0, min(1.0, float(state.get("text_yview", 0.0)))))
        except (TypeError, ValueError):
            self.text.yview_moveto(0.0)
        self.status.set("Text document opened | the file is empty" if not document.content else f"TXT · {len(document.content.split()):,} words · {len(document.content):,} characters · Select text to explain")

    def _open_pdf_document(self, path):
        document = self.controller.open_document(path)
        if not isinstance(document, PDFDocument):
            raise DocumentOpenError("The selected document is not a PDF.")
        self._prepare_new_document(path, "pdf")
        self.pdf_document = document
        state = self.document_states.get(str(path), {})
        try:
            self.pdf_page_index = max(0, min(document.page_count - 1, int(state.get("pdf_page", 0))))
        except (TypeError, ValueError):
            self.pdf_page_index = 0
        self.fit_width_enabled = bool(state.get("fit_width", True))
        try:
            self.pdf_zoom = max(0.25, min(4.0, float(state.get("pdf_zoom", 1.25))))
        except (TypeError, ValueError):
            self.pdf_zoom = 1.25
        self.text_viewer.pack_forget()
        self.toolbar.pack(fill="x", padx=18, pady=(0, 8), before=self.viewer_host)
        self.pdf_viewer.pack(fill="both", expand=True)
        self._set_pdf_controls_enabled(True)
        self._populate_page_list()
        self.root.update_idletasks()
        self._render_current_page()

    def _clear_pdf_view(self):
        self._pdf_prefetch_generation += 1
        self._pdf_render_cache.clear()
        self._pdf_prefetch_inflight.clear()
        self.pdf_document = None
        self._page_image = None
        if hasattr(self, "page_list"):
            self.page_list.delete(0, tk.END)
        if hasattr(self, "pdf_canvas"):
            self.pdf_canvas.delete("all")
        if hasattr(self, "previous_button"):
            self._set_pdf_controls_enabled(False)

    def _set_pdf_controls_enabled(self, enabled):
        state = "normal" if enabled else "disabled"
        for button in (self.previous_button, self.next_button, self.zoom_out_button, self.zoom_in_button, self.fit_button):
            button.config(state=state)
        self.page_entry.config(state=state)
        if not enabled:
            self.page_number.set("—")
            self.page_total.config(text="/ —")
            self.zoom_label.config(text="—")

    def _populate_page_list(self):
        self.page_list.delete(0, tk.END)
        for number in range(1, self.pdf_document.page_count + 1):
            self.page_list.insert(tk.END, f"Page {number}")

    def _render_current_page(self, reset_view=True):
        if self.pdf_document is None:
            return
        viewport_width = max(self.pdf_canvas.winfo_width(), 640)
        if self.fit_width_enabled:
            page_width = self.pdf_document.page_width(self.pdf_page_index)
            scale = max(0.25, min(4.0, (viewport_width - 56) / page_width))
        else:
            scale = self.pdf_zoom
        self.pdf_zoom = scale
        cache_key = self._pdf_cache_key(self.pdf_page_index, scale)
        started = time.perf_counter()
        cached_png = self._pdf_render_cache.get(cache_key)
        if cached_png is not None:
            self._pdf_render_cache.move_to_end(cache_key)
            png_bytes = cached_png
            render_source = "buffer"
        else:
            self.status.set(f"Rendering PDF page {self.pdf_page_index + 1}…")
            self.root.update_idletasks()
            rendered_page = self.pdf_document.render_page(self.pdf_page_index, scale)
            png_bytes = rendered_page.png_bytes
            self._cache_pdf_render(cache_key, png_bytes)
            render_source = "rendered"
        elapsed = time.perf_counter() - started
        if elapsed >= 0.5:
            self._write_pdf_render_log(
                f"Rendered page {self.pdf_page_index + 1} at {scale:.2f}x in {elapsed:.2f}s ({render_source})"
            )
        self._page_image = tk.PhotoImage(data=png_bytes, format="png")
        image_width, image_height = self._page_image.width(), self._page_image.height()
        canvas_width = self.pdf_canvas.winfo_width()
        image_center_x = max(canvas_width // 2, image_width // 2 + 24)
        self._page_image_left = image_center_x - image_width / 2
        self._page_image_top = 24
        self._render_scale = scale
        self.pdf_canvas.delete("page")
        self.pdf_canvas.delete("selection")
        self.pdf_canvas.delete("source_highlight")
        self._source_highlight_ids = []
        self.pdf_canvas.create_image(image_center_x, 24, image=self._page_image, anchor="n", tags="page")
        self._render_saved_pdf_highlights()
        self.pdf_canvas.configure(scrollregion=(0, 0, max(canvas_width, image_width + 48), image_height + 48))
        if reset_view:
            self.pdf_canvas.xview_moveto(0)
            self.pdf_canvas.yview_moveto(0)
        self.page_number.set(str(self.pdf_page_index + 1))
        self.page_total.config(text=f"/ {self.pdf_document.page_count}")
        self.zoom_label.config(text=f"Fit ({scale * 100:.0f}%)" if self.fit_width_enabled else f"{scale * 100:.0f}%")
        render_note = f" · {elapsed:.2f}s {render_source}" if elapsed >= 0.35 or render_source == "buffer" else ""
        self.status.set(f"PDF · Page {self.pdf_page_index + 1} of {self.pdf_document.page_count} · Drag over text to explain{render_note}")
        self.page_list.selection_clear(0, tk.END)
        self._schedule_pdf_prefetch(scale)
        self.page_list.selection_set(self.pdf_page_index)
        self.page_list.see(self.pdf_page_index)

    def _pdf_cache_key(self, page_index, scale):
        path = str(self.pdf_document.path.resolve()) if self.pdf_document is not None else ""
        return (path, int(page_index), round(float(scale), 3))

    def _cache_pdf_render(self, key, png_bytes):
        self._pdf_render_cache[key] = png_bytes
        self._pdf_render_cache.move_to_end(key)
        while len(self._pdf_render_cache) > self._pdf_render_cache_limit:
            self._pdf_render_cache.popitem(last=False)

    def _schedule_pdf_prefetch(self, scale):
        """Rasterize the adjacent pages off the Tk thread using a separate PDF handle."""
        if self.pdf_document is None:
            return
        path = str(self.pdf_document.path)
        generation = self._pdf_prefetch_generation
        for page_index in (self.pdf_page_index - 1, self.pdf_page_index + 1):
            if not 0 <= page_index < self.pdf_document.page_count:
                continue
            key = self._pdf_cache_key(page_index, scale)
            if key in self._pdf_render_cache or key in self._pdf_prefetch_inflight:
                continue
            self._pdf_prefetch_inflight.add(key)
            self._pdf_prefetch_executor.submit(self._render_pdf_prefetch, path, page_index, float(scale), key, generation)

    def _render_pdf_prefetch(self, path, page_index, scale, key, generation):
        started = time.perf_counter()
        try:
            # PyMuPDF document handles are not shared across threads.
            with pymupdf.open(path) as document:
                page = document[page_index]
                pixmap = page.get_pixmap(matrix=pymupdf.Matrix(scale, scale), alpha=False)
                payload = (key, pixmap.tobytes("png"), None, time.perf_counter() - started, generation)
        except Exception as error:
            payload = (key, None, f"{type(error).__name__}: {error}", time.perf_counter() - started, generation)
        self._pdf_prefetch_results.put(payload)

    def _process_pdf_prefetch_results(self):
        try:
            while True:
                key, png_bytes, error, elapsed, generation = self._pdf_prefetch_results.get_nowait()
                self._pdf_prefetch_inflight.discard(key)
                if generation != self._pdf_prefetch_generation:
                    continue
                if error:
                    self._write_pdf_render_log(f"Prefetch failed for {key}: {error}")
                    continue
                self._cache_pdf_render(key, png_bytes)
                if elapsed >= 0.5:
                    self._write_pdf_render_log(f"Prefetched page {key[1] + 1} at {key[2]:.2f}x in {elapsed:.2f}s")
        except queue.Empty:
            pass
        if self.root.winfo_exists():
            self.root.after(80, self._process_pdf_prefetch_results)

    def _write_pdf_render_log(self, message):
        try:
            self._pdf_render_log_path.parent.mkdir(parents=True, exist_ok=True)
            with self._pdf_render_log_path.open("a", encoding="utf-8") as log_file:
                log_file.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {message}\n")
        except OSError:
            pass

    def _go_to_page(self, page_index):
        if self.pdf_document is not None and 0 <= page_index < self.pdf_document.page_count:
            self._cancel_pdf_selection()
            self.pdf_page_index = page_index
            self._render_current_page()
            self._save_current_document_state()
        elif self.pdf_document is not None:
            self.page_number.set(str(self.pdf_page_index + 1))

    def previous_page(self):
        self._go_to_page(self.pdf_page_index - 1)

    def next_page(self):
        self._go_to_page(self.pdf_page_index + 1)

    def go_to_entered_page(self, _event=None):
        try:
            self._go_to_page(int(self.page_number.get()) - 1)
        except ValueError:
            self.page_number.set(str(self.pdf_page_index + 1))
        return "break"

    def _select_page_from_sidebar(self, _event=None):
        selection = self.page_list.curselection()
        if selection and selection[0] != self.pdf_page_index:
            self._go_to_page(selection[0])

    def zoom_in(self):
        if self.document_type == "text":
            self.text_font_size = min(24, self.text_font_size + 1)
            self._apply_text_font_size()
            self._persist_font_size()
            return
        self._change_zoom(1.25)

    def zoom_out(self):
        if self.document_type == "text":
            self.text_font_size = max(8, self.text_font_size - 1)
            self._apply_text_font_size()
            self._persist_font_size()
            return
        self._change_zoom(0.8)

    def _apply_text_font_size(self):
        if hasattr(self, "text"):
            self.text.configure(
                font=("Noto Serif", self.text_font_size),
                spacing1=round(5 * self._line_spacing),
                spacing3=round(7 * self._line_spacing),
            )

    def cycle_line_spacing(self):
        """Cycle through compact, comfortable, and spacious text line spacing."""
        levels = (0.75, 1.0, 1.35)
        current = min(range(len(levels)), key=lambda index: abs(levels[index] - self._line_spacing))
        self._line_spacing = levels[(current + 1) % len(levels)]
        self._apply_text_font_size()
        self._persist_font_size()
        labels = {0.75: "Compact line spacing", 1.0: "Comfortable line spacing", 1.35: "Spacious line spacing"}
        self.status.set(labels[self._line_spacing])
        return "break"

    def _persist_font_size(self):
        try:
            self._save_settings()
        except OSError:
            pass

    def _change_zoom(self, factor):
        if self.pdf_document is not None:
            self._cancel_pdf_selection()
            self.fit_width_enabled = False
            self.pdf_zoom = max(0.25, min(4.0, self.pdf_zoom * factor))
            self._render_current_page(reset_view=False)
            self._save_current_document_state()

    def fit_width(self):
        if self.pdf_document is not None:
            self._cancel_pdf_selection()
            self.fit_width_enabled = True
            self._render_current_page(reset_view=False)
            self._save_current_document_state()

    def _on_pdf_viewport_resize(self, _event=None):
        if self.pdf_document is None or not self.fit_width_enabled:
            return
        if self._resize_job is not None:
            self.root.after_cancel(self._resize_job)
        self._resize_job = self.root.after(120, self._render_after_resize)

    def _render_after_resize(self):
        self._resize_job = None
        self._render_current_page(reset_view=False)

    def reset_reading_zoom(self):
        if self.document_type == "text":
            self.text_font_size = 11
            self._apply_text_font_size()
            self._persist_font_size()
        elif self.pdf_document is not None:
            self.fit_width()
        return "break"

    def _page_up_shortcut(self, event=None):
        if event is not None and isinstance(event.widget, (tk.Entry, tk.Text)):
            return None
        if self.pdf_document is not None:
            self.previous_page()
            return "break"
        return None

    def _page_down_shortcut(self, event=None):
        if event is not None and isinstance(event.widget, (tk.Entry, tk.Text)):
            return None
        if self.pdf_document is not None:
            self.next_page()
            return "break"
        return None

    def _document_home_shortcut(self, event=None):
        if event is not None and isinstance(event.widget, (tk.Entry, tk.Text)):
            return None
        if self.pdf_document is not None:
            self._go_to_page(0)
            return "break"
        return None

    def _document_end_shortcut(self, event=None):
        if event is not None and isinstance(event.widget, (tk.Entry, tk.Text)):
            return None
        if self.pdf_document is not None:
            self._go_to_page(self.pdf_document.page_count - 1)
            return "break"
        return None

    def _scroll_pdf(self, event):
        """Scroll within the current PDF page and cross page boundaries naturally.

        Supports Linux X11 wheel buttons and MouseWheel events on Windows/macOS.
        At a boundary, the wheel advances immediately even when yview cannot
        change (for example, when Fit Width makes the whole page visible).
        """
        if self.pdf_document is None:
            return "break"
        direction = _pdf_wheel_direction(event)
        if not direction:
            return "break"

        before = self.pdf_canvas.yview()
        transition = _pdf_boundary_transition(direction, before)
        if transition:
            if transition > 0:
                self.next_page()
            else:
                self.previous_page()
            return "break"

        delta = getattr(event, "delta", 0) or 0
        units = max(1, min(12, int(abs(delta) / 120) * 3)) if delta else 3
        self.pdf_canvas.yview_scroll(direction * units, "units")
        after = self.pdf_canvas.yview()

        # If this gesture reaches a boundary, advance on the same gesture.
        # Do not repeat the transition when no movement occurred: that case was
        # handled by the pre-scroll boundary check above.
        if after != before:
            transition = _pdf_boundary_transition(direction, after)
            if transition > 0:
                self.next_page()
            elif transition < 0:
                self.previous_page()
        return "break"

    def _cancel_pdf_selection(self):
        self._selection_request += 1
        self._pdf_selection_start = None
        self._pdf_selection_rectangle = None
        self._close_active_popup()
        self.pdf_canvas.delete("selection")

    def _pdf_canvas_point(self, event):
        if self.pdf_document is None or self._page_image is None:
            return None
        x = self.pdf_canvas.canvasx(event.x)
        y = self.pdf_canvas.canvasy(event.y)
        left = self._page_image_left
        top = self._page_image_top
        right = left + self._page_image.width()
        bottom = top + self._page_image.height()
        if x < left or x > right or y < top or y > bottom:
            return None
        return x, y

    def _start_pdf_selection(self, event):
        point = self._pdf_canvas_point(event)
        if point is None:
            self._cancel_pdf_selection()
            return
        self._cancel_pdf_selection()
        self._pdf_selection_start = point
        self._pdf_selection_rectangle = self.pdf_canvas.create_rectangle(
            *point,
            *point,
            outline=self.PDF_SELECTION,
            width=2,
            tags="selection",
        )

    def _update_pdf_selection(self, event):
        if self._pdf_selection_start is None or self._pdf_selection_rectangle is None:
            return
        point = self._pdf_canvas_point(event)
        if point is None:
            return
        self.pdf_canvas.coords(
            self._pdf_selection_rectangle,
            *self._pdf_selection_start,
            *point,
        )

    def _finish_pdf_selection(self, event):
        if self._pdf_selection_start is None:
            return
        end = self._pdf_canvas_point(event)
        start = self._pdf_selection_start
        self._pdf_selection_start = None
        # A horizontal drag on one line is a valid selection too. Treat it as
        # a click only when there was virtually no movement on either axis.
        if end is None or (abs(end[0] - start[0]) < 4 and abs(end[1] - start[1]) < 4):
            self.pdf_canvas.delete("selection")
            return

        context = self.controller.pdf_context(
            self.pdf_page_index,
            (min(start[0], end[0]) - self._page_image_left) / self._render_scale,
            (min(start[1], end[1]) - self._page_image_top) / self._render_scale,
            (max(start[0], end[0]) - self._page_image_left) / self._render_scale,
            (max(start[1], end[1]) - self._page_image_top) / self._render_scale,
        )
        if context is None:
            self.pdf_canvas.delete("selection")
            self.status.set("No text found in that selection")
            return

        request_id = self._selection_request
        anchor_position = (event.x_root, event.y_root + 8)
        self.root.after(
            80,
            lambda: self.create_popup(
                request_id,
                context,
                anchor_position=anchor_position,
            ),
        )

    def on_select(self, _event=None):
        """Debounce mouse selection and resolve its exact widget range."""
        if self._selection_job is not None:
            try:
                self.root.after_cancel(self._selection_job)
            except tk.TclError:
                pass
        self._selection_job = self.root.after(90, self._process_text_selection)

    def _process_text_selection(self):
        self._selection_job = None
        if self.document_type != "text":
            return
        try:
            raw_selected = self.text.get("sel.first", "sel.last")
            selection_start = self.text.index("sel.first")
            selection_end = self.text.index("sel.last")
        except tk.TclError:
            self._selection_request += 1
            self.last_selection = None
            self._close_active_popup()
            return

        if not raw_selected.strip():
            self._selection_request += 1
            self.last_selection = None
            self._close_active_popup()
            return

        # Capture the raw range as part of the identity, even though the
        # document model trims edge whitespace before sending text to the AI.
        selection_key = (raw_selected, selection_start, selection_end)
        if selection_key == self.last_selection:
            return
        self.last_selection = selection_key
        self._selection_request += 1
        request_id = self._selection_request
        start_offset = self.text.count("1.0", selection_start, "chars")[0]
        try:
            context = self.controller.text_context(raw_selected, start_offset)
        except (ValueError, RuntimeError):
            self.status.set("That selection changed; please select the text again")
            self.last_selection = None
            return
        self.root.after(30, lambda: self.create_popup(request_id, context, selection_end))

    def create_popup(
        self,
        request_id,
        context: ReadingContext,
        anchor_index="insert",
        anchor_position=None,
    ):
        if request_id != self._selection_request:
            return
        self._close_active_popup()
        self.active_popup = show_popup(
            self.root,
            self.text,
            context.selected_text,
            "Thinking…",
            anchor_index=anchor_index,
            anchor_position=anchor_position,
            question_handler=lambda question, set_response, entry, ask_button, popup: self._ask_followup(
                question, context, set_response, entry, ask_button, popup
            ),
            source_handler=self.open_source,
            theme=self.theme,
            note_handler=lambda selected, meaning, is_error: self._create_note_from_context(
                context, selected, meaning, is_error
            ),
            annotation_handler=lambda selected, kind: self._create_annotation_from_context(
                context, selected, kind
            ),
            study_handler=lambda selected: self._open_revision_notes(
                context, selected
            ),
            quiz_handler=lambda selected: self._open_quiz_setup(
                context, selected
            ),
            flashcard_handler=lambda selected: self._open_flashcard_setup(
                context, selected
            ),
        )
        popup = self.active_popup
        threading.Thread(
            target=self.fetch_meaning,
            args=(request_id, popup, context, anchor_index, anchor_position),
            daemon=True,
        ).start()

    def _is_current_popup(self, popup, request_id):
        if popup is None:
            return False
        if request_id != self._selection_request:
            return False
        if self.active_popup is not popup:
            return False
        try:
            return popup.winfo_exists()
        except tk.TclError:
            return False

    def fetch_meaning(self, request_id, popup, context, anchor_index, anchor_position):
        try:
            explanation = self.controller.explain(context)
        except Exception as error:
            # A backend exception must not leave the popup stuck on Thinking…
            from ai.assistant import Explanation
            explanation = Explanation(
                text=f"The local assistant could not complete this request: {error}",
                context=context,
                is_error=True,
            )
        self._meaning_results.put(
            (request_id, popup, explanation, anchor_index, anchor_position)
        )

    def _process_meaning_results(self):
        while True:
            try:
                result = self._meaning_results.get_nowait()
            except queue.Empty:
                break

            if len(result) == 5:
                request_id, popup, explanation, anchor_index, anchor_position = result
            elif len(result) == 4:
                request_id, explanation, anchor_index, anchor_position = result
                popup = self.active_popup
            else:
                continue

            if not self._is_current_popup(popup, request_id):
                continue

            update_meaning = getattr(popup, "update_meaning", None)
            if callable(update_meaning):
                update_meaning(explanation.text, is_error=explanation.is_error)
            else:
                popup.destroy()
                self.active_popup = show_popup(
                    self.root,
                    self.text,
                    explanation.context.selected_text,
                    explanation.text,
                    anchor_index=anchor_index,
                    anchor_position=anchor_position,
                    is_error=explanation.is_error,
                    question_handler=lambda question, set_response, entry, ask_button, popup: self._ask_followup(
                        question,
                        explanation.context,
                        set_response,
                        entry,
                        ask_button,
                        popup,
                    ),
                    source_handler=self.open_source,
                    theme=self.theme,
                    note_handler=lambda selected, meaning, is_error: self._create_note_from_context(
                        explanation.context, selected, meaning, is_error
                    ),
                    annotation_handler=lambda selected, kind: self._create_annotation_from_context(
                        explanation.context, selected, kind
                    ),
                    study_handler=lambda selected: self._open_revision_notes(
                        explanation.context, selected
                    ),
                    quiz_handler=lambda selected: self._open_quiz_setup(
                        explanation.context, selected
                    ),
                    flashcard_handler=lambda selected: self._open_flashcard_setup(
                        explanation.context, selected
                    ),
                )
        self.root.after(50, self._process_meaning_results)

    def _ask_followup(self, question, context, set_response, entry, ask_button, popup):
        results = queue.Queue()
        request_id = self._selection_request

        def fetch_answer():
            try:
                answer = self.controller.answer(question, context)
                results.put((answer.text, answer.is_error, answer.sources))
            except Exception as error:
                results.put((f"The local assistant could not complete this request: {error}", True, ()))

        threading.Thread(target=fetch_answer, daemon=True).start()

        def show_answer():
            # The selection, document, or popup can change while the local
            # model is working. Never resurrect controls or write a response
            # into a stale floating window.
            if not self._is_current_popup(popup, request_id):
                return
            try:
                answer = results.get_nowait()
            except queue.Empty:
                self.root.after(50, show_answer)
                return
            answer_text, is_error, sources = answer
            set_response(answer_text, sources)
            if is_error:
                self.status.set("The local assistant returned an error")
            entry.config(state="normal")
            ask_button.config(state="normal")

        self.root.after(50, show_answer)

    def _close_active_popup(self):
        if self.active_popup is not None:
            try:
                if self.active_popup.winfo_exists():
                    self.active_popup.destroy()
            except tk.TclError:
                pass
            self.active_popup = None

    def close(self):
        if self.revision_window is not None:
            self._close_revision_notes()
            if self.revision_window is not None:
                return
        if self.notes_window is not None:
            self._notes_close_window()
            if self.notes_window is not None:
                return
        self._quiz_request += 1
        self._flashcard_request += 1
        self._close_quiz_setup(force=True)
        self._close_quiz_library()
        self._close_quiz()
        self._close_flashcard_setup(force=True)
        self._close_flashcard_library()
        self._close_flashcards()
        if self._focus_mode:
            self._restore_focus_chrome()
        if self._fullscreen:
            try:
                self.root.attributes("-fullscreen", False)
            except tk.TclError:
                pass
        self.close_file()
        self._pdf_prefetch_generation += 1
        self._pdf_prefetch_executor.shutdown(wait=False, cancel_futures=True)
        self.root.destroy()

    def run(self):
        self.root.mainloop()
