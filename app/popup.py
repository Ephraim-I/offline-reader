"""Compact contextual reading popup for definitions and follow-up questions."""

import re
import tkinter as tk

BACKGROUND = "#ffffff"
TEXT = "#252b32"
MUTED = "#68717c"
ACCENT = "#287d6d"
ERROR = "#b42318"
BORDER = "#dfe4e8"
SOFT = "#f5f7f8"
HOVER = "#e8f3f0"
PALETTES = {
    "light": {
        "BACKGROUND": BACKGROUND,
        "TEXT": TEXT,
        "MUTED": MUTED,
        "ACCENT": ACCENT,
        "ERROR": ERROR,
        "BORDER": BORDER,
        "SOFT": SOFT,
        "HOVER": HOVER,
        "BUTTON_TEXT": "#ffffff",
        "ACCENT_ACTIVE": "#22695c",
        "ACCENT_TEXT": "#09231f",
        "ACCENT_HOVER": "#62cabb",
        "CITATION_HOVER": "#1f6558",
    },
    "dark": {
        "BACKGROUND": "#1b242e",
        "TEXT": "#d5dee8",
        "MUTED": "#aab5c1",
        "ACCENT": "#73c6ae",
        "ERROR": "#ff8f86",
        "BORDER": "#354250",
        "SOFT": "#151d26",
        "HOVER": "#2a3945",
        "BUTTON_TEXT": "#071c18",
        "ACCENT_ACTIVE": "#3b8c7d",
        "ACCENT_TEXT": "#071c18",
        "ACCENT_HOVER": "#4c9f8d",
        "CITATION_HOVER": "#b2e3d5",
    },
}


def valid_citation_spans(text, sources):
    """Return citation ranges only for references supplied by the retriever."""
    source_by_ref = {source.ref: source for source in (sources or ())}
    return [
        (match.start(), match.end(), source_by_ref[match.group(1)])
        for match in re.finditer(r"\[(R\d+)\]", text)
        if match.group(1) in source_by_ref
    ]


def _popup_bounds(root):
    """Return the usable reader-window rectangle in screen coordinates."""
    left = root.winfo_rootx()
    top = root.winfo_rooty()
    width = root.winfo_width()
    height = root.winfo_height()
    if width > 0 and height > 0:
        return left, top, left + width, top + height
    return 0, 0, root.winfo_screenwidth(), root.winfo_screenheight()


def _popup_position(x, y, width, height, bounds, anchor_y=None, margin=8):
    """Keep a popup inside its reader window and flip it above a low anchor."""
    left, top, right, bottom = bounds
    min_x = left + margin
    max_x = max(min_x, right - width - margin)
    min_y = top + margin
    max_y = max(min_y, bottom - height - margin)

    if anchor_y is not None and y + height > bottom - margin:
        y = anchor_y - height - 12
    return max(min_x, min(x, max_x)), max(min_y, min(y, max_y))


def _study_menu_actions(study_handler, quiz_handler, flashcard_handler):
    """Return only the study actions supported by the current reader UI."""
    return tuple(
        (label, handler)
        for label, handler in (
            ("Revision notes", study_handler),
            ("Create quiz", quiz_handler),
            ("Create flashcards", flashcard_handler),
        )
        if handler is not None
    )


def show_popup(
    root,
    text_widget,
    selected_text,
    meaning,
    anchor_index="insert",
    anchor_position=None,
    question_handler=None,
    is_error=False,
    source_handler=None,
    theme="light",
    note_handler=None,
    annotation_handler=None,
    study_handler=None,
    quiz_handler=None,
    flashcard_handler=None,
):
    palette = PALETTES.get(theme, PALETTES["light"])
    if anchor_position is not None:
        x, y = anchor_position
    else:
        bounds = text_widget.bbox(anchor_index)
        if bounds:
            x = text_widget.winfo_rootx() + bounds[0]
            y = text_widget.winfo_rooty() + bounds[1] + bounds[3] + 8
        else:
            x = root.winfo_pointerx()
            y = root.winfo_pointery() + 8

    popup = tk.Toplevel(root)
    popup.wm_overrideredirect(True)
    popup.transient(root)
    popup.configure(bg=palette["BACKGROUND"])

    frame = tk.Frame(
        popup,
        bg=palette["BACKGROUND"],
        padx=14,
        pady=12,
        highlightbackground=palette["BORDER"],
        highlightcolor=palette["ACCENT"],
        highlightthickness=1,
    )
    frame.pack()

    heading = tk.Frame(frame, bg=palette["BACKGROUND"])
    heading.pack(fill="x")
    selected_label = tk.Label(
        heading,
        text=(selected_text[:67] + "…") if len(selected_text) > 70 else selected_text,
        fg=palette["ACCENT"],
        bg=palette["BACKGROUND"],
        font=("Segoe UI", 10, "bold"),
        anchor="w",
        justify="left",
        wraplength=300,
    )
    selected_label.pack(side="left", fill="x", expand=True)
    close_button = tk.Button(
        heading,
        text="×",
        command=popup.destroy,
        bg=palette["BACKGROUND"],
        fg=palette["MUTED"],
        activebackground=palette["HOVER"],
        activeforeground=palette["TEXT"],
        relief="flat",
        borderwidth=0,
        padx=5,
        cursor="hand2",
        takefocus=True,
        font=("Segoe UI", 12),
    )
    close_button.pack(side="right", anchor="n")

    tk.Label(
        frame,
        text="CONTEXT EXPLANATION",
        fg=palette["MUTED"],
        bg=palette["BACKGROUND"],
        font=("Segoe UI", 8, "bold"),
        anchor="w",
    ).pack(anchor="w", pady=(5, 0))

    meaning_var = tk.StringVar(value=meaning)
    meaning_error = {"value": is_error}
    meaning_label = tk.Label(
        frame,
        textvariable=meaning_var,
        fg=palette["ERROR"] if is_error else palette["TEXT"],
        bg=palette["BACKGROUND"],
        wraplength=480,
        justify="left",
        anchor="w",
        font=("Segoe UI", 10),
    )
    meaning_label.pack(anchor="w", fill="x", pady=(8, 4))

    actions = tk.Frame(frame, bg=palette["BACKGROUND"])
    actions.pack(fill="x", pady=(7, 0))

    def copy_text(value, button=None, default_label=None):
        try:
            root.clipboard_clear()
            root.clipboard_append(value)
            if button is not None and default_label is not None:
                button.config(text="Copied")
                root.after(
                    1200,
                    lambda: button.config(text=default_label) if button.winfo_exists() else None,
                )
        except tk.TclError:
            # Clipboard failures should not close or disrupt the reader.
            pass

    def action_menu(label):
        button = tk.Menubutton(
            actions,
            text=label,
            bg=palette["BACKGROUND"],
            fg=palette["MUTED"],
            activebackground=palette["HOVER"],
            activeforeground=palette["TEXT"],
            relief="flat",
            borderwidth=0,
            padx=7,
            pady=4,
            cursor="hand2",
            takefocus=True,
            font=("Segoe UI", 9),
            direction="below",
        )
        menu = tk.Menu(
            button,
            tearoff=0,
            bg=palette["BACKGROUND"],
            fg=palette["TEXT"],
            activebackground=palette["HOVER"],
            activeforeground=palette["TEXT"],
            disabledforeground=palette["MUTED"],
            relief="flat",
            borderwidth=0,
        )
        button.configure(menu=menu)
        return button, menu

    copy_button = tk.Button(
        actions,
        text="Copy selection",
        command=lambda: copy_text(selected_text, copy_button, "Copy selection"),
        bg=palette["BACKGROUND"],
        fg=palette["MUTED"],
        activebackground=palette["HOVER"],
        activeforeground=palette["TEXT"],
        relief="flat",
        padx=7,
        pady=4,
        cursor="hand2",
        takefocus=True,
        font=("Segoe UI", 9),
    )
    copy_button.pack(side="left")

    if note_handler is not None:
        save_note_button = tk.Button(
            actions,
            text="Save note",
            command=lambda: note_handler(
                selected_text, meaning_var.get(), meaning_error["value"]
            ),
            bg=palette["BACKGROUND"],
            fg=palette["ACCENT"],
            activebackground=palette["HOVER"],
            activeforeground=palette["TEXT"],
            relief="flat",
            padx=7,
            pady=4,
            cursor="hand2",
            takefocus=True,
            font=("Segoe UI", 9),
        )
        save_note_button.pack(side="left", padx=(2, 0))

    if annotation_handler is not None:
        highlight_button = tk.Button(
            actions,
            text="Highlight",
            command=lambda: annotation_handler(selected_text, "highlight"),
            bg=palette["BACKGROUND"],
            fg=palette["ACCENT"],
            activebackground=palette["HOVER"],
            activeforeground=palette["TEXT"],
            relief="flat",
            padx=7,
            pady=4,
            cursor="hand2",
            takefocus=True,
            font=("Segoe UI", 9),
        )
        highlight_button.pack(side="left", padx=(2, 0))

    more_button, more_menu = action_menu("More ▾")
    more_menu.add_command(label="Copy explanation", command=lambda: copy_text(meaning_var.get()))
    if annotation_handler is not None:
        more_menu.add_command(
            label="Bookmark",
            command=lambda: annotation_handler(selected_text, "bookmark"),
        )
    more_button.pack(side="left", padx=(2, 0))

    study_actions = _study_menu_actions(
        study_handler, quiz_handler, flashcard_handler
    )
    if study_actions:
        study_button, study_menu = action_menu("Study ▾")
        for label, handler in study_actions:
            study_menu.add_command(
                label=label,
                command=lambda action=handler: action(selected_text),
            )
        study_button.pack(side="left", padx=(2, 0))

    followup_button = tk.Button(
        actions,
        text="Ask a follow-up",
        command=lambda: expand_popup(
            root, popup, frame, selected_text, question_handler, source_handler, theme
        ),
        bg=palette["ACCENT"],
        fg=palette["BUTTON_TEXT"],
        activebackground=palette["ACCENT_ACTIVE"],
        activeforeground=palette["TEXT"],
        relief="flat",
        padx=10,
        pady=5,
        cursor="hand2",
        takefocus=True,
        font=("Segoe UI", 9),
    )
    followup_button.pack(side="right")

    def update_meaning(new_meaning, is_error=False):
        if not popup.winfo_exists():
            return
        meaning_var.set(new_meaning)
        meaning_error["value"] = is_error
        meaning_label.configure(fg=palette["ERROR"] if is_error else palette["TEXT"])
        popup.update_idletasks()
        _fit_popup(root, popup, anchor_y=y)

    # The UI can update the existing popup after inference instead of
    # destroying/recreating it, preserving focus and any expanded follow-up UI.
    popup.update_meaning = update_meaning
    popup.bind("<Escape>", lambda _event: popup.destroy())
    popup.bind("<Return>", lambda _event: followup_button.invoke() if popup.focus_get() == followup_button else None)
    popup.geometry(f"+{x}+{y}")
    _fit_popup(root, popup, anchor_y=y)
    return popup


def _fit_popup(root, popup, anchor_y=None):
    if not popup.winfo_exists():
        return
    popup.update_idletasks()
    width = popup.winfo_reqwidth()
    height = popup.winfo_reqheight()
    x, y = _popup_position(
        popup.winfo_x(), popup.winfo_y(), width, height,
        _popup_bounds(root), anchor_y=anchor_y,
    )
    popup.geometry(f"+{x}+{y}")


def expand_popup(root, popup, frame, selected_text, question_handler, source_handler=None, theme="light"):
    palette = PALETTES.get(theme, PALETTES["light"])
    for widget in frame.winfo_children():
        if isinstance(widget, tk.Entry):
            widget.focus_set()
            return

    tk.Label(
        frame,
        text="Ask about the selected text",
        fg=palette["MUTED"],
        bg=palette["BACKGROUND"],
        font=("Segoe UI", 9),
    ).pack(anchor="w", pady=(10, 2))

    entry = tk.Entry(
        frame,
        width=38,
        bg=palette["SOFT"],
        fg=palette["TEXT"],
        insertbackground=palette["TEXT"],
        relief="flat",
        highlightthickness=1,
        highlightbackground=palette["BORDER"],
        highlightcolor=palette["ACCENT"],
        font=("Segoe UI", 10),
    )
    entry.pack(fill="x", pady=(6, 5), ipady=6)

    response_frame = tk.Frame(frame, bg=palette["BACKGROUND"])
    response_frame.pack(fill="both", expand=True, pady=(4, 0))

    scrollbar = tk.Scrollbar(response_frame, orient="vertical")
    scrollbar.pack(side="right", fill="y")
    response_text = tk.Text(
        response_frame,
        height=10,
        width=54,
        wrap="word",
        yscrollcommand=scrollbar.set,
        bg=palette["SOFT"],
        fg=palette["TEXT"],
        insertbackground=palette["TEXT"],
        relief="flat",
        font=("Segoe UI", 9),
        state="disabled",
    )
    response_text.pack(side="left", fill="both", expand=True)
    response_frame.configure(height=220)
    response_frame.pack_propagate(False)
    scrollbar.config(command=response_text.yview)

    source_frame = tk.Frame(frame, bg=palette["BACKGROUND"])
    source_frame.pack(fill="x", pady=(4, 0))

    ask_button = tk.Button(
        frame,
        text="Ask",
        bg=palette["ACCENT"],
        fg=palette["ACCENT_TEXT"],
        activebackground=palette["ACCENT_HOVER"],
        activeforeground=palette["ACCENT_TEXT"],
        relief="flat",
        padx=12,
        pady=5,
        cursor="hand2",
        takefocus=True,
        font=("Segoe UI", 9, "bold"),
    )
    ask_button.pack(anchor="e", pady=(7, 0))

    def set_response(text, sources=None):
        if not popup.winfo_exists():
            return
        response_text.config(state="normal")
        response_text.delete("1.0", tk.END)
        response_text.tag_configure("citation", foreground=palette["ACCENT"], underline=True)
        response_text.tag_configure("citation_hover", foreground=palette["CITATION_HOVER"], underline=True)
        response_text.insert(tk.END, text)
        # Render only valid source IDs as links. The model cannot create a link
        # to an arbitrary file or path by inventing a citation label.
        for start_offset, end_offset, source in valid_citation_spans(text, sources):
            if source_handler is None:
                continue
            start = f"1.0 + {start_offset} chars"
            end = f"1.0 + {end_offset} chars"
            tag = f"citation_{start_offset}"
            response_text.tag_add(tag, start, end)
            response_text.tag_add("citation", start, end)
            response_text.tag_bind(tag, "<Button-1>", lambda _event, item=source: source_handler(item))
            response_text.tag_bind(
                tag, "<Enter>",
                lambda _event: response_text.tag_configure(
                    "citation", foreground=palette["CITATION_HOVER"]
                ),
            )
            response_text.tag_bind(
                tag, "<Leave>",
                lambda _event: response_text.tag_configure(
                    "citation", foreground=palette["ACCENT"]
                ),
            )
        response_text.config(state="disabled")
        for widget in source_frame.winfo_children():
            widget.destroy()
        available_sources = tuple(sources or ())
        if available_sources:
            tk.Label(
                source_frame, text="SOURCES", bg=palette["BACKGROUND"], fg=palette["MUTED"],
                font=("Segoe UI", 8, "bold"), anchor="w"
            ).pack(anchor="w", fill="x", pady=(5, 3))
        for source in available_sources:
            label = f"{source.ref}  ·  {source.location}"
            link = tk.Button(
                source_frame,
                text=label,
                command=lambda item=source: source_handler(item) if source_handler else None,
                bg=palette["SOFT"],
                fg=palette["ACCENT"],
                activebackground=palette["HOVER"],
                activeforeground=palette["TEXT"],
                relief="flat",
                borderwidth=0,
                padx=8,
                pady=5,
                cursor="hand2",
                anchor="w",
                font=("Segoe UI", 9, "underline"),
            )
            link.pack(anchor="w", fill="x")
        _fit_popup(root, popup, anchor_y=popup.winfo_y())

    def ask_question(_event=None):
        question = entry.get().strip()
        if not question:
            return "break"
        entry.config(state="disabled")
        ask_button.config(state="disabled")
        set_response("Thinking…")
        if question_handler is None:
            set_response("Follow-up questions are unavailable.")
            entry.config(state="normal")
            ask_button.config(state="normal")
        else:
            question_handler(question, set_response, entry, ask_button, popup)
        return "break"

    ask_button.config(command=ask_question)
    entry.bind("<Return>", ask_question)
    entry.focus_set()
    root.after_idle(lambda: _fit_popup(root, popup, anchor_y=popup.winfo_y()) if popup.winfo_exists() else None)
