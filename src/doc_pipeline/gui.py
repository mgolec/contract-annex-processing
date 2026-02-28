"""Tkinter GUI wizard for the contract price adjustment pipeline."""

from __future__ import annotations

import json
import logging
import os
import platform
import queue
import re
import subprocess
import sys
import threading
import tkinter as tk
from datetime import datetime
from io import StringIO
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Any

# Project root — same logic as config.py
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


# ── Redirect Rich console output to a string buffer ─────────────────────────

# Keywords that indicate safe auto-confirm prompts
_SAFE_PROMPTS = {"continue", "proceed", "y/n", "da/ne"}


class _BufferedConsole:
    """Captures Rich console output into a StringIO for GUI display."""

    def __init__(self) -> None:
        self._buffer = StringIO()
        self._original_console = None
        self._lock = threading.Lock()

    def install(self) -> None:
        """Replace the global Rich console with one that writes to our buffer.

        Also patches console.input() to auto-confirm safe prompts, since the
        GUI handles all user confirmations via its own dialogs before launching
        tasks.  Unknown prompts default to 'n' for safety.
        """
        from rich.console import Console
        from doc_pipeline.utils import progress

        self._original_console = progress.console

        # Thread-safe write wrapper around the StringIO buffer
        lock = self._lock
        raw_buffer = self._buffer

        class _LockedWriter:
            """A file-like wrapper that acquires the lock on every write."""

            def write(self, s: str) -> int:
                with lock:
                    return raw_buffer.write(s)

            def flush(self) -> None:
                with lock:
                    raw_buffer.flush()

            # Rich console checks for these
            @property
            def encoding(self) -> str:
                return "utf-8"

            def isatty(self) -> bool:
                return False

            def fileno(self) -> int:
                raise OSError("_LockedWriter has no fileno")

        console = Console(
            file=_LockedWriter(),  # type: ignore[arg-type]
            force_terminal=False,
            no_color=True,
            width=120,
        )

        # Safe auto-confirm — only auto-yes for known safe prompts
        def _safe_auto_confirm(prompt: str = "") -> str:
            prompt_lower = prompt.lower()
            if any(kw in prompt_lower for kw in _SAFE_PROMPTS):
                return "y"
            logging.warning(f"GUI auto-confirm blocked unexpected prompt: {prompt}")
            return "n"

        console.input = _safe_auto_confirm  # type: ignore[assignment]
        progress.console = console

    def restore(self) -> None:
        if self._original_console is not None:
            from doc_pipeline.utils import progress
            progress.console = self._original_console

    def read_new(self) -> str:
        """Read any new output since last call."""
        with self._lock:
            val = self._buffer.getvalue()
            if val:
                self._buffer.truncate(0)
                self._buffer.seek(0)
            return val


# ── Step definitions ─────────────────────────────────────────────────────────

STEPS = [
    ("1", "Postavke", "Konfiguracija"),
    ("2", "Priprema", "Skeniranje ugovora"),
    ("3", "Ekstrakcija", "Izvlačenje cijena"),
    ("4", "Pregled", "Odobravanje cijena"),
    ("5", "Generiranje", "Kreiranje aneksa"),
]

# Step dependencies: step index -> list of prerequisite step indices
_STEP_DEPS: dict[int, list[int]] = {
    0: [],       # Settings: always available
    1: [],       # Setup: no hard deps (settings are optional)
    2: [1],      # Extraction: requires Setup
    3: [2],      # Review: requires Extraction
    4: [3],      # Generation: requires Review (which implies Extraction)
}

# Maximum lines to keep in log area
MAX_LOG_LINES = 3000

# Platform detection for keyboard shortcuts
_IS_MAC = platform.system() == "Darwin"
_MOD = "Command" if _IS_MAC else "Control"
_MOD_DISPLAY = "\u2318" if _IS_MAC else "Ctrl+"


# ── Tooltip helper (L9) ──────────────────────────────────────────────────────

class ToolTip:
    """Simple hover tooltip for any widget."""

    def __init__(self, widget: tk.Widget, text: str) -> None:
        self.widget = widget
        self.text = text
        self.tip_window: tk.Toplevel | None = None
        widget.bind("<Enter>", self._show)
        widget.bind("<Leave>", self._hide)

    def _show(self, event: tk.Event | None = None) -> None:
        try:
            bbox = self.widget.bbox("insert")
        except Exception:
            bbox = None
        x = (bbox[0] if bbox else 0) + self.widget.winfo_rootx() + 25
        y = (bbox[1] if bbox else 0) + self.widget.winfo_rooty() + 25
        self.tip_window = tw = tk.Toplevel(self.widget)
        tw.wm_overrideredirect(True)
        tw.wm_geometry(f"+{x}+{y}")
        label = tk.Label(
            tw,
            text=self.text,
            background="#ffffe0",
            relief="solid",
            borderwidth=1,
            font=("Arial", 9),
        )
        label.pack()

    def _hide(self, event: tk.Event | None = None) -> None:
        if self.tip_window:
            self.tip_window.destroy()
            self.tip_window = None


# ── Main Application ─────────────────────────────────────────────────────────

class PipelineGUI:
    """Main GUI window — wizard style with sidebar steps."""

    def __init__(self) -> None:
        self.root = tk.Tk()
        self.root.title("Procudo — Pipeline za ugovore")
        self.root.geometry("960x680")
        self.root.minsize(800, 560)

        # L3: Window icon
        try:
            icon_path = Path(__file__).parent.parent.parent / "assets" / "icon.png"
            if icon_path.exists():
                img = tk.PhotoImage(file=str(icon_path))
                self.root.iconphoto(True, img)
        except Exception:
            pass  # No icon available, use default

        # WM_DELETE_WINDOW handler (C6)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

        # Message queue for background thread -> GUI communication
        self._queue: queue.Queue[tuple[str, Any]] = queue.Queue()
        self._buffered = _BufferedConsole()
        self._running = False  # Is a background task running?

        # Cancel event for long operations (H13)
        self._cancel_event = threading.Event()

        # Deferred config error for UI building (M36)
        self._config_load_error: str | None = None

        self._current_step = 0
        self._step_labels: list[tk.Label] = []
        self._content_frame: tk.Frame | None = None

        # "Next step" button reference (shown after phase completion)
        self._next_step_btn: ttk.Button | None = None

        # Mouse wheel binding id (for cleanup)
        self._mousewheel_binding_id: str | None = None

        # F4: Log search state — maps log widget id to search state dict
        self._log_search_state: dict[str, dict[str, Any]] = {}

        # Collapsible log state: step_index -> {"collapsed": bool, "container": Frame, "toggle_var": StringVar, "line_count": int}
        self._log_states: dict[int, dict[str, Any]] = {}

        # Store settings-related widget refs for tooltips / API test (L9, F3)
        self._settings_entries: dict[str, tk.StringVar] = {}
        self._api_key_var: tk.StringVar | None = None

        # F1: Client filter variables for extraction and generation steps
        self._extract_clients_var: tk.StringVar | None = None
        self._gen_clients_var: tk.StringVar | None = None

        self._build_ui()
        self._bind_keyboard_shortcuts()
        self._show_step(0)

    # ── WM_DELETE_WINDOW handler (C6) ─────────────────────────────────────

    def _on_close(self) -> None:
        """Handle window close request."""
        if self._running:
            if messagebox.askyesno(
                "Operacija u tijeku",
                "Operacija je u tijeku. Jeste li sigurni da \u017eelite zatvoriti?",
            ):
                self._cancel_event.set()
                self.root.destroy()
            # else: do nothing, user chose not to close
        else:
            self.root.destroy()

    # ── Keyboard shortcuts (L1) ──────────────────────────────────────────

    def _bind_keyboard_shortcuts(self) -> None:
        """Bind global keyboard shortcuts."""
        mod = _MOD
        # Ctrl/Cmd+S: Save settings (when on settings step)
        self.root.bind_all(f"<{mod}-s>", self._on_shortcut_save)
        self.root.bind_all(f"<{mod}-S>", self._on_shortcut_save)
        # Enter/Return: Trigger primary action
        self.root.bind_all("<Return>", self._on_shortcut_enter)
        # Escape: Cancel running operation
        self.root.bind_all("<Escape>", self._on_shortcut_escape)
        # Ctrl/Cmd+1-5: Navigate to steps (key N -> internal index N-1)
        for i in range(1, 6):
            self.root.bind_all(
                f"<{mod}-Key-{i}>",
                lambda e, step=i - 1: self._on_shortcut_step(step),
            )
        # Ctrl/Cmd+F: Focus log search (F4)
        self.root.bind_all(f"<{mod}-f>", self._on_shortcut_search)
        self.root.bind_all(f"<{mod}-F>", self._on_shortcut_search)

    def _on_shortcut_save(self, event: tk.Event) -> str:
        """Ctrl/Cmd+S: Save settings if on settings step."""
        # Don't trigger if focus is in a text widget (avoid interfering with text editing)
        if self._current_step == 0 and hasattr(self, "_settings_entries") and self._settings_entries:
            self._save_settings(self._settings_entries)
        return "break"

    def _on_shortcut_enter(self, event: tk.Event) -> str | None:
        """Enter: Trigger primary action on current step."""
        # Don't capture Enter from Entry widgets where user might be typing
        focused = self.root.focus_get()
        if isinstance(focused, (tk.Text, ttk.Entry, tk.Entry)):
            return None  # Let default behavior handle it
        if self._running:
            return "break"
        actions = {
            0: lambda: (
                self._save_settings(self._settings_entries)
                if self._settings_entries
                else None
            ),
            1: self._run_setup,
            2: self._run_extraction,
            3: lambda: self._show_step(4),
            4: self._run_generation,
        }
        action = actions.get(self._current_step)
        if action:
            action()
        return "break"

    def _on_shortcut_escape(self, event: tk.Event) -> str:
        """Escape: Cancel running operation."""
        if self._running:
            self._on_cancel_click()
        return "break"

    def _on_shortcut_step(self, step: int) -> str:
        """Ctrl/Cmd+N: Navigate directly to step N (internal index N-1).

        Ctrl+1 -> step 0 (Postavke), Ctrl+5 -> step 4 (Generiranje).
        """
        if 0 <= step < len(STEPS):
            self._on_step_click(step)
        return "break"

    def _on_shortcut_search(self, event: tk.Event) -> str:
        """Ctrl/Cmd+F: Focus the log search field on the current step."""
        # Find the search entry for the current step's log widget
        for wid, state in self._log_search_state.items():
            entry = state.get("entry")
            if entry and entry.winfo_exists():
                entry.focus_set()
                entry.select_range(0, tk.END)
                return "break"
        return "break"

    # ── UI construction ──────────────────────────────────────────────────

    def _build_ui(self) -> None:
        # Use ttk theme
        style = ttk.Style()
        available = style.theme_names()
        for preferred in ("aqua", "clam", "vista", "default"):
            if preferred in available:
                style.theme_use(preferred)
                break

        # Configure custom styles
        style.configure("Sidebar.TFrame", background="#2c3e50")
        style.configure(
            "SidebarStep.TLabel",
            background="#2c3e50",
            foreground="#95a5a6",
            font=("Arial", 11),
            padding=(12, 8),
        )
        style.configure(
            "SidebarStepActive.TLabel",
            background="#34495e",
            foreground="#ecf0f1",
            font=("Arial", 11, "bold"),
            padding=(12, 8),
        )
        style.configure(
            "SidebarStepDone.TLabel",
            background="#2c3e50",
            foreground="#2ecc71",
            font=("Arial", 11),
            padding=(12, 8),
        )
        style.configure(
            "SidebarStepLocked.TLabel",
            background="#2c3e50",
            foreground="#4a5568",
            font=("Arial", 11),
            padding=(12, 8),
        )
        style.configure("Status.TLabel", font=("Arial", 10), padding=(8, 4))
        style.configure("Title.TLabel", font=("Arial", 14, "bold"))
        style.configure("Subtitle.TLabel", font=("Arial", 10), foreground="#7f8c8d")
        # Top-level layout
        main_pane = ttk.PanedWindow(self.root, orient=tk.HORIZONTAL)
        main_pane.pack(fill=tk.BOTH, expand=True)

        # ── Sidebar ──
        sidebar = ttk.Frame(main_pane, style="Sidebar.TFrame", width=180)
        main_pane.add(sidebar, weight=0)

        # App title in sidebar
        title_lbl = ttk.Label(
            sidebar,
            text="Pipeline",
            font=("Arial", 16, "bold"),
            background="#2c3e50",
            foreground="#ecf0f1",
            padding=(12, 16, 12, 8),
        )
        title_lbl.pack(fill=tk.X)

        # Step labels with descriptions
        for i, (num, hr_name, description) in enumerate(STEPS):
            step_frame = ttk.Frame(sidebar, style="Sidebar.TFrame")
            step_frame.pack(fill=tk.X)

            # Two-line label: name + description
            lbl = ttk.Label(
                step_frame,
                text=f"  {num}   {hr_name}\n        {description}",
                style="SidebarStep.TLabel",
                cursor="hand2",
            )
            lbl.pack(fill=tk.X)
            step_idx = i
            lbl.bind("<Button-1>", lambda e, s=step_idx: self._on_step_click(s))
            step_frame.bind("<Button-1>", lambda e, s=step_idx: self._on_step_click(s))
            self._step_labels.append(lbl)

        # L1: Keyboard shortcut help text at bottom of sidebar
        shortcut_frame = ttk.Frame(sidebar, style="Sidebar.TFrame")
        shortcut_frame.pack(side=tk.BOTTOM, fill=tk.X, pady=(8, 8))
        shortcut_text = (
            f"{_MOD_DISPLAY}1-5: Navigacija\n"
            f"Enter: Pokreni\n"
            f"Esc: Odustani\n"
            f"{_MOD_DISPLAY}S: Spremi\n"
            f"{_MOD_DISPLAY}F: Tra\u017ei"
        )
        ttk.Label(
            shortcut_frame,
            text=shortcut_text,
            background="#2c3e50",
            foreground="#7f8c8d",
            font=("Arial", 8),
            padding=(12, 4),
            justify=tk.LEFT,
        ).pack(anchor=tk.W)

        # ── Content area ──
        self._content_outer = ttk.Frame(main_pane)
        main_pane.add(self._content_outer, weight=1)

        # ── Status bar ──
        self._status_var = tk.StringVar(value="Spreman")
        status_bar = ttk.Label(
            self.root,
            textvariable=self._status_var,
            style="Status.TLabel",
            relief=tk.SUNKEN,
            anchor=tk.W,
        )
        status_bar.pack(fill=tk.X, side=tk.BOTTOM)

    def _on_step_click(self, step: int) -> None:
        if self._running:
            return

        # H20: Step ordering enforcement
        if not self._is_step_available(step):
            messagebox.showwarning(
                "Korak nije dostupan",
                "Prvo dovršite prethodni korak.",
            )
            return

        self._show_step(step)

    def _is_step_available(self, step: int) -> bool:
        """Check whether prerequisites for the given step are met."""
        # Step 0 (Settings) and Step 1 (Setup) are always available
        if step <= 1:
            return True

        try:
            from doc_pipeline.config import load_config
            cfg = load_config()
        except Exception:
            # If config can't load, only settings step is safe
            return step == 0

        if step == 2:
            # Extraction requires inventory from Setup
            return cfg.inventory_path.exists()
        elif step == 3:
            # Review requires spreadsheet from Extraction
            return cfg.spreadsheet_path.exists()
        elif step == 4:
            # Generation requires spreadsheet (reviewed)
            return cfg.spreadsheet_path.exists()

        return True

    def _show_step(self, step: int) -> None:
        self._current_step = step
        self.root.title(f"Procudo \u2014 {STEPS[step][1]}")
        self._update_sidebar()

        # Unbind mouse wheel from previous view (M37 cleanup)
        self._unbind_mousewheel()

        # Clear content
        if self._content_frame is not None:
            self._content_frame.destroy()
        self._content_frame = ttk.Frame(self._content_outer, padding=16)
        self._content_frame.pack(fill=tk.BOTH, expand=True)

        # Reset next-step button reference
        self._next_step_btn = None

        # Banner container (sits at top of content area)
        self._banner_container = ttk.Frame(self._content_frame)
        self._banner_container.pack(fill=tk.X, pady=(0, 4))

        # Build step content
        builders = [
            self._build_settings,
            self._build_setup,
            self._build_extraction,
            self._build_review,
            self._build_generation,
        ]
        builders[step](self._content_frame)

    def _update_sidebar(self) -> None:
        for i, lbl in enumerate(self._step_labels):
            num = STEPS[i][0]
            hr_name = STEPS[i][1]
            description = STEPS[i][2]
            if i < self._current_step:
                lbl.configure(
                    text=f"  \u2713   {hr_name}\n        {description}",
                    style="SidebarStepDone.TLabel",
                )
            elif i == self._current_step:
                lbl.configure(
                    text=f"  {num}   {hr_name}\n        {description}",
                    style="SidebarStepActive.TLabel",
                )
            else:
                if self._is_step_available(i):
                    lbl.configure(
                        text=f"  {num}   {hr_name}\n        {description}",
                        style="SidebarStep.TLabel",
                    )
                    lbl.configure(cursor="hand2")
                else:
                    lbl.configure(
                        text=f"  \U0001f512   {hr_name}\n        {description}",
                        style="SidebarStepLocked.TLabel",
                    )
                    lbl.configure(cursor="arrow")

    def _set_status(self, msg: str) -> None:
        self._status_var.set(msg)

    # ── Helpers for content building ────────────────────────────────────

    def _add_title(self, parent: ttk.Frame, title: str, subtitle: str = "") -> None:
        ttk.Label(parent, text=title, style="Title.TLabel").pack(anchor=tk.W, pady=(0, 2))
        if subtitle:
            ttk.Label(parent, text=subtitle, style="Subtitle.TLabel").pack(
                anchor=tk.W, pady=(0, 12)
            )

    def _make_button(
        self,
        parent: tk.Widget,
        text: str,
        command: Any,
        style: str = "secondary",
        width: int | None = None,
    ) -> tk.Button:
        """Create a styled button with color hierarchy.

        Styles: 'primary' (blue), 'secondary' (neutral), 'danger' (red), 'success' (green).
        Uses tk.Button instead of ttk.Button because macOS aqua theme
        ignores ttk background colors.
        """
        colors = {
            "primary":   {"bg": "#2980b9", "fg": "#ffffff", "active_bg": "#2471a3"},
            "secondary": {"bg": "#bdc3c7", "fg": "#2c3e50", "active_bg": "#a6acaf"},
            "danger":    {"bg": "#e74c3c", "fg": "#ffffff", "active_bg": "#cb4335"},
            "success":   {"bg": "#27ae60", "fg": "#ffffff", "active_bg": "#229954"},
        }
        c = colors.get(style, colors["secondary"])
        btn = tk.Button(
            parent,
            text=text,
            command=command,
            bg=c["bg"],
            fg=c["fg"],
            activebackground=c["active_bg"],
            activeforeground=c["fg"],
            relief=tk.FLAT,
            padx=12,
            pady=4,
            font=("Arial", 10, "bold") if style == "primary" else ("Arial", 10),
            cursor="hand2",
        )
        if width:
            btn.configure(width=width)
        # Hover effect (skip when disabled)
        btn.bind(
            "<Enter>",
            lambda e, b=btn, col=c: b.configure(bg=col["active_bg"])
            if str(b.cget("state")) != "disabled"
            else None,
        )
        btn.bind(
            "<Leave>",
            lambda e, b=btn, col=c: b.configure(bg=col["bg"])
            if str(b.cget("state")) != "disabled"
            else None,
        )
        return btn

    def _restyle_button(self, btn: tk.Button, style: str) -> None:
        """Change a button's color style and rebind hover events."""
        colors = {
            "primary":   {"bg": "#2980b9", "fg": "#ffffff", "active_bg": "#2471a3"},
            "secondary": {"bg": "#bdc3c7", "fg": "#2c3e50", "active_bg": "#a6acaf"},
            "danger":    {"bg": "#e74c3c", "fg": "#ffffff", "active_bg": "#cb4335"},
            "success":   {"bg": "#27ae60", "fg": "#ffffff", "active_bg": "#229954"},
        }
        c = colors.get(style, colors["secondary"])
        btn.configure(
            bg=c["bg"],
            fg=c["fg"],
            activebackground=c["active_bg"],
            activeforeground=c["fg"],
            font=("Arial", 10, "bold") if style == "primary" else ("Arial", 10),
        )
        btn.bind(
            "<Enter>",
            lambda e, b=btn, col=c: b.configure(bg=col["active_bg"])
            if str(b.cget("state")) != "disabled"
            else None,
        )
        btn.bind(
            "<Leave>",
            lambda e, b=btn, col=c: b.configure(bg=col["bg"])
            if str(b.cget("state")) != "disabled"
            else None,
        )

    def _add_log_area(self, parent: ttk.Frame, step_name: str = "log") -> tk.Text:
        """Add a collapsible scrollable log text area with search bar and save button."""
        step_idx = self._current_step

        # Toggle button
        toggle_frame = ttk.Frame(parent)
        toggle_frame.pack(fill=tk.X, pady=(8, 0))

        toggle_var = tk.StringVar(value="\u25b6 Zapisnik (0 linija)")
        toggle_btn = tk.Button(
            toggle_frame,
            textvariable=toggle_var,
            font=("Arial", 9, "bold"),
            relief=tk.FLAT,
            bg="#ecf0f1",
            fg="#2c3e50",
            activebackground="#d5dbdb",
            cursor="hand2",
            anchor=tk.W,
            padx=8,
            pady=2,
        )
        toggle_btn.pack(fill=tk.X)

        # Collapsible container (holds search bar + log)
        log_container = ttk.Frame(parent)
        # Starts collapsed — don't pack yet

        # F4: Search bar
        search_frame = ttk.Frame(log_container)
        search_frame.pack(fill=tk.X, pady=(4, 2))

        ttk.Label(search_frame, text="Tra\u017ei:", font=("Arial", 9)).pack(side=tk.LEFT)
        search_var = tk.StringVar()
        search_entry = ttk.Entry(search_frame, textvariable=search_var, width=30)
        search_entry.pack(side=tk.LEFT, padx=(4, 0))

        search_btn = ttk.Button(search_frame, text="Tra\u017ei", width=8)
        search_btn.pack(side=tk.LEFT, padx=(4, 0))

        next_btn = ttk.Button(search_frame, text="Sljede\u0107i", width=8)
        next_btn.pack(side=tk.LEFT, padx=(4, 0))

        # L6: Save log button
        save_btn = ttk.Button(search_frame, text="Spremi zapisnik", width=14)
        save_btn.pack(side=tk.RIGHT, padx=(4, 0))

        # Log text area
        frame = ttk.Frame(log_container)
        frame.pack(fill=tk.BOTH, expand=True, pady=(2, 0))

        scrollbar = ttk.Scrollbar(frame)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        log = tk.Text(
            frame,
            wrap=tk.WORD,
            font=("Consolas" if sys.platform == "win32" else "Menlo", 10),
            bg="#1e1e1e",
            fg="#d4d4d4",
            insertbackground="#d4d4d4",
            state=tk.DISABLED,
            height=12,
            yscrollcommand=scrollbar.set,
        )
        log.pack(fill=tk.BOTH, expand=True)
        scrollbar.config(command=log.yview)

        # Configure search highlight tag
        log.tag_configure("search_highlight", background="#b58900", foreground="#1e1e1e")

        # F4: Store search state
        wid = str(id(log))
        self._log_search_state[wid] = {
            "entry": search_entry,
            "var": search_var,
            "match_index": 0,
            "matches": [],
            "log": log,
        }

        # Wire up search actions
        search_btn.configure(command=lambda: self._log_search(log))
        next_btn.configure(command=lambda: self._log_search_next(log))
        search_entry.bind("<Return>", lambda e: self._log_search(log))
        search_var.trace_add(
            "write", lambda *_: self._log_search_clear(log) if not search_var.get() else None
        )

        # L6: Wire up save button
        save_btn.configure(command=lambda: self._save_log(log, step_name))

        # Store collapsible state
        self._log_states[step_idx] = {
            "collapsed": True,
            "container": log_container,
            "toggle_var": toggle_var,
            "toggle_btn": toggle_btn,
            "line_count": 0,
        }

        def _toggle_log() -> None:
            state = self._log_states.get(step_idx)
            if not state:
                return
            if state["collapsed"]:
                log_container.pack(fill=tk.BOTH, expand=True, pady=(2, 0))
                state["collapsed"] = False
                n = state["line_count"]
                state["toggle_var"].set(f"\u25bc Zapisnik ({n} linija)")
            else:
                log_container.pack_forget()
                state["collapsed"] = True
                n = state["line_count"]
                state["toggle_var"].set(f"\u25b6 Zapisnik ({n} linija)")

        toggle_btn.configure(command=_toggle_log)

        return log

    def _add_progress(self, parent: ttk.Frame) -> tuple[ttk.Progressbar, tk.StringVar]:
        """Add a progress bar with percentage label."""
        prog_frame = ttk.Frame(parent)
        prog_frame.pack(fill=tk.X, pady=(8, 0))
        bar = ttk.Progressbar(prog_frame, mode="indeterminate", length=400)
        bar.pack(side=tk.LEFT, fill=tk.X, expand=True)
        pct_var = tk.StringVar(value="")
        pct_label = ttk.Label(prog_frame, textvariable=pct_var, font=("Arial", 9), width=6)
        pct_label.pack(side=tk.LEFT, padx=(8, 0))
        return bar, pct_var

    def _on_cancel_click(self) -> None:
        """Handle cancel button click."""
        self._cancel_event.set()
        for attr in ("_setup_cancel_btn", "_extract_cancel_btn", "_gen_cancel_btn"):
            btn = getattr(self, attr, None)
            if btn and btn.winfo_exists():
                btn.configure(state=tk.DISABLED)
        self._set_status("Otkazivanje...")

    def _update_progress(
        self, bar: ttk.Progressbar, current: int, total: int, pct_var: tk.StringVar | None = None
    ) -> None:
        """Switch progress bar to determinate mode and update value (H19)."""
        if total > 0:
            bar.stop()
            bar.configure(mode="determinate", maximum=100)
            pct = (current / total) * 100
            bar["value"] = pct
            if pct_var:
                pct_var.set(f"{int(pct)}%")
        else:
            bar.configure(mode="indeterminate")
            bar.start(10)

    def _log_append(self, log_widget: tk.Text, text: str) -> None:
        log_widget.configure(state=tk.NORMAL)
        log_widget.insert(tk.END, text)
        # H18: Bounded log area
        line_count = int(log_widget.index("end-1c").split(".")[0])
        if line_count > MAX_LOG_LINES:
            log_widget.delete("1.0", f"{line_count - MAX_LOG_LINES}.0")
            line_count = MAX_LOG_LINES
        log_widget.see(tk.END)
        log_widget.configure(state=tk.DISABLED)

        # Update collapsible log state
        state = self._log_states.get(self._current_step)
        if state:
            state["line_count"] = line_count
            # Auto-expand on first content
            if state["collapsed"] and line_count > 0:
                state["container"].pack(fill=tk.BOTH, expand=True, pady=(2, 0))
                state["collapsed"] = False
            arrow = "\u25bc" if not state["collapsed"] else "\u25b6"
            state["toggle_var"].set(f"{arrow} Zapisnik ({line_count} linija)")

    # ── F4: Log search methods ────────────────────────────────────────────

    def _log_search(self, log_widget: tk.Text) -> None:
        """Highlight all matches of the search term in the log."""
        wid = str(id(log_widget))
        state = self._log_search_state.get(wid)
        if not state:
            return
        term = state["var"].get().strip()
        if not term:
            self._log_search_clear(log_widget)
            return

        # Clear previous highlights
        log_widget.tag_remove("search_highlight", "1.0", tk.END)
        state["matches"] = []
        state["match_index"] = 0

        # Find all occurrences
        start_pos = "1.0"
        while True:
            pos = log_widget.search(term, start_pos, stopindex=tk.END, nocase=True)
            if not pos:
                break
            end_pos = f"{pos}+{len(term)}c"
            log_widget.tag_add("search_highlight", pos, end_pos)
            state["matches"].append(pos)
            start_pos = end_pos

        # Jump to first match
        if state["matches"]:
            log_widget.see(state["matches"][0])
            self._set_status(
                f"Prona\u0111eno {len(state['matches'])} rezultata"
            )
        else:
            self._set_status("Nema rezultata")

    def _log_search_next(self, log_widget: tk.Text) -> None:
        """Jump to the next search match."""
        wid = str(id(log_widget))
        state = self._log_search_state.get(wid)
        if not state or not state["matches"]:
            return
        state["match_index"] = (state["match_index"] + 1) % len(state["matches"])
        pos = state["matches"][state["match_index"]]
        log_widget.see(pos)
        self._set_status(
            f"Rezultat {state['match_index'] + 1}/{len(state['matches'])}"
        )

    def _log_search_clear(self, log_widget: tk.Text) -> None:
        """Clear search highlights."""
        log_widget.tag_remove("search_highlight", "1.0", tk.END)
        wid = str(id(log_widget))
        state = self._log_search_state.get(wid)
        if state:
            state["matches"] = []
            state["match_index"] = 0

    # ── L6: Log save method ───────────────────────────────────────────────

    def _save_log(self, log_widget: tk.Text, step_name: str) -> None:
        """Save log contents to a text file."""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        default_name = f"pipeline_log_{step_name}_{timestamp}.txt"
        filepath = filedialog.asksaveasfilename(
            title="Spremi zapisnik",
            defaultextension=".txt",
            filetypes=[("Text files", "*.txt"), ("All files", "*.*")],
            initialfile=default_name,
        )
        if not filepath:
            return
        try:
            content = log_widget.get("1.0", tk.END)
            Path(filepath).write_text(content, encoding="utf-8")
            self._set_status(f"Zapisnik spremljen: {Path(filepath).name}")
        except Exception as exc:
            messagebox.showerror(
                "Gre\u0161ka",
                f"Spremanje zapisnika nije uspjelo:\n{exc}",
            )

    def _add_next_step_button(self, parent: ttk.Frame, next_step: int) -> None:
        """Add a prominent 'Continue to Next Step' button after phase completion."""
        if next_step >= len(STEPS):
            return
        btn = self._make_button(
            parent,
            text="Nastavi na sljede\u0107i korak \u2192",
            command=lambda: self._show_step(next_step),
            style="success",
        )
        btn.pack(anchor=tk.W, pady=(8, 0))
        self._next_step_btn = btn

    def _show_banner(
        self, message: str, level: str = "info", auto_dismiss: bool | None = None
    ) -> tk.Frame:
        """Show an inline banner at the top of the content area.

        Levels: 'success', 'error', 'warning', 'info'.
        Auto-dismiss defaults to True for success/info, False for error/warning.
        """
        colors = {
            "success": {"bg": "#d4edda", "fg": "#155724", "border": "#28a745", "icon": "\u2713"},
            "error":   {"bg": "#f8d7da", "fg": "#721c24", "border": "#dc3545", "icon": "\u2717"},
            "warning": {"bg": "#fff3cd", "fg": "#856404", "border": "#ffc107", "icon": "\u26a0"},
            "info":    {"bg": "#d1ecf1", "fg": "#0c5460", "border": "#17a2b8", "icon": "\u2139"},
        }
        c = colors.get(level, colors["info"])
        if auto_dismiss is None:
            auto_dismiss = level in ("success", "info")

        banner = tk.Frame(
            self._banner_container,
            bg=c["bg"],
            highlightbackground=c["border"],
            highlightthickness=1,
            padx=12,
            pady=8,
        )
        banner.pack(fill=tk.X, pady=(0, 4))

        # Icon + message
        tk.Label(
            banner,
            text=f"{c['icon']}  {message}",
            bg=c["bg"],
            fg=c["fg"],
            font=("Arial", 10),
            anchor=tk.W,
            wraplength=650,
            justify=tk.LEFT,
        ).pack(side=tk.LEFT, fill=tk.X, expand=True)

        # Close button
        close_btn = tk.Label(
            banner,
            text="\u2715",
            bg=c["bg"],
            fg=c["fg"],
            font=("Arial", 10, "bold"),
            cursor="hand2",
            padx=4,
        )
        close_btn.pack(side=tk.RIGHT)
        close_btn.bind("<Button-1>", lambda e: banner.destroy())

        if auto_dismiss:
            self.root.after(5000, lambda: banner.destroy() if banner.winfo_exists() else None)

        return banner

    def _clear_banners(self) -> None:
        """Remove all banners from the banner container."""
        if hasattr(self, "_banner_container") and self._banner_container.winfo_exists():
            for child in self._banner_container.winfo_children():
                child.destroy()

    def _load_config_safe(self, quiet: bool = False) -> Any:
        """Load config, return None on error.

        If *quiet* is True (M36), suppress the error dialog and store the
        error for later display.  A warning label should be shown instead.
        """
        try:
            from doc_pipeline.config import load_config
            self._config_load_error = None
            return load_config()
        except Exception as exc:
            self._config_load_error = str(exc)
            if not quiet:
                messagebox.showerror(
                    "Gre\u0161ka",
                    f"Konfiguracija se ne mo\u017ee u\u010ditati:\n{exc}\n\n"
                    "Provjerite pipeline.toml i .env datoteke.",
                )
            return None

    def _add_config_warning(self, parent: ttk.Frame) -> None:
        """Show a subtle config warning label if config failed to load (M36)."""
        if self._config_load_error:
            lbl = ttk.Label(
                parent,
                text="\u26a0 Konfiguracija nije u\u010ditana",
                foreground="#e67e22",
                font=("Arial", 10),
            )
            lbl.pack(anchor=tk.W, pady=(0, 8))

    # ── Mouse wheel helpers (M37) ────────────────────────────────────────

    def _bind_mousewheel(self, canvas: tk.Canvas) -> None:
        """Bind mouse wheel scrolling to the canvas."""
        if platform.system() == "Darwin":
            self._mousewheel_binding_id = canvas.bind_all(
                "<MouseWheel>",
                lambda e: canvas.yview_scroll(-1 * e.delta, "units"),
            )
        else:
            self._mousewheel_binding_id = canvas.bind_all(
                "<MouseWheel>",
                lambda e: canvas.yview_scroll(-1 * (e.delta // 120), "units"),
            )
        self._mousewheel_canvas = canvas

    def _unbind_mousewheel(self) -> None:
        """Unbind mouse wheel events to avoid affecting other scrollable widgets."""
        if self._mousewheel_binding_id is not None:
            try:
                self._mousewheel_canvas.unbind_all("<MouseWheel>")
            except Exception:
                pass
            self._mousewheel_binding_id = None

    # ── Step 0: Settings ─────────────────────────────────────────────────

    def _build_settings(self, parent: ttk.Frame) -> None:
        self._add_title(
            parent,
            "Postavke",
            "Konfiguracija pipeline-a",
        )

        # Load current values (quiet — don't pop up error during UI build)
        cfg = self._load_config_safe(quiet=True)
        self._add_config_warning(parent)

        # Scrollable form
        canvas = tk.Canvas(parent, highlightthickness=0)
        scrollbar = ttk.Scrollbar(parent, orient=tk.VERTICAL, command=canvas.yview)
        form_frame = ttk.Frame(canvas)

        form_frame.bind(
            "<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all"))
        )
        canvas.create_window((0, 0), window=form_frame, anchor=tk.NW)
        canvas.configure(yscrollcommand=scrollbar.set)

        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        # M37: Mouse wheel scrolling on settings canvas
        self._bind_mousewheel(canvas)

        row = 0
        entries: dict[str, tk.StringVar] = {}
        # L9: collect entry widgets for tooltips
        entry_widgets: dict[str, ttk.Entry] = {}

        def add_field(
            label: str, key: str, default: str = "", masked: bool = False
        ) -> ttk.Entry:
            nonlocal row
            ttk.Label(form_frame, text=label, font=("Arial", 10)).grid(
                row=row, column=0, sticky=tk.W, padx=(0, 12), pady=4
            )
            var = tk.StringVar(value=default)
            entries[key] = var
            entry = ttk.Entry(form_frame, textvariable=var, width=60)
            if masked:
                entry.configure(show="*")
            entry.grid(row=row, column=1, sticky=tk.EW, pady=4)
            entry_widgets[key] = entry
            row += 1
            return entry

        def add_folder_field(
            label: str, key: str, default: str = ""
        ) -> ttk.Entry:
            nonlocal row
            ttk.Label(form_frame, text=label, font=("Arial", 10)).grid(
                row=row, column=0, sticky=tk.W, padx=(0, 12), pady=4
            )
            var = tk.StringVar(value=default)
            entries[key] = var
            frame = ttk.Frame(form_frame)
            frame.grid(row=row, column=1, sticky=tk.EW, pady=4)
            entry = ttk.Entry(frame, textvariable=var, width=50)
            entry.pack(side=tk.LEFT, fill=tk.X, expand=True)
            btn = ttk.Button(
                frame,
                text="...",
                width=3,
                command=lambda v=var: self._pick_folder(v),
            )
            btn.pack(side=tk.RIGHT, padx=(4, 0))
            entry_widgets[key] = entry
            row += 1
            return entry

        form_frame.columnconfigure(1, weight=1)

        # Section: Paths
        ttk.Label(form_frame, text="Putanje", font=("Arial", 11, "bold")).grid(
            row=row, column=0, columnspan=2, sticky=tk.W, pady=(8, 4)
        )
        row += 1

        add_folder_field(
            "Mapa s ugovorima:",
            "paths.source",
            cfg.paths.source if cfg else "./contracts",
        )

        # Section: Company info
        ttk.Label(
            form_frame, text="Podaci o tvrtki", font=("Arial", 11, "bold")
        ).grid(row=row, column=0, columnspan=2, sticky=tk.W, pady=(16, 4))
        row += 1

        add_field(
            "Naziv tvrtke:",
            "general.company_name",
            cfg.general.company_name if cfg else "",
        )
        add_field("OIB:", "general.company_oib", cfg.general.company_oib if cfg else "")
        add_field(
            "Adresa:",
            "general.company_address",
            cfg.general.company_address if cfg else "",
        )
        add_field(
            "Direktor:",
            "general.company_director",
            cfg.general.company_director if cfg else "",
        )

        # Section: API
        ttk.Label(
            form_frame, text="API / Ekstrakcija", font=("Arial", 11, "bold")
        ).grid(row=row, column=0, columnspan=2, sticky=tk.W, pady=(16, 4))
        row += 1

        add_field(
            "Anthropic API klju\u010d:",
            "api_key",
            cfg.anthropic_api_key if cfg else "",
            masked=True,
        )

        # F3: API test button — placed on same row as API key, in column 2
        self._api_key_var = entries["api_key"]
        api_test_frame = ttk.Frame(form_frame)
        api_test_frame.grid(row=row - 1, column=2, padx=(8, 0), pady=4)
        self._api_test_btn = ttk.Button(
            api_test_frame,
            text="Testiraj API",
            command=self._test_api_key,
        )
        self._api_test_btn.pack(side=tk.LEFT)
        self._api_test_status = ttk.Label(
            api_test_frame, text="", font=("Arial", 9)
        )
        self._api_test_status.pack(side=tk.LEFT, padx=(6, 0))

        # Section: Generation
        ttk.Label(
            form_frame, text="Generiranje", font=("Arial", 11, "bold")
        ).grid(row=row, column=0, columnspan=2, sticky=tk.W, pady=(16, 4))
        row += 1

        add_field(
            "Datum stupanja na snagu (GGGG-MM-DD):",
            "generation.default_effective_date",
            cfg.generation.default_effective_date if cfg else "2026-03-01",
        )

        # Save button
        btn_frame = ttk.Frame(form_frame)
        btn_frame.grid(row=row, column=0, columnspan=2, pady=(20, 8))

        self._make_button(
            btn_frame,
            "Spremi postavke",
            lambda: self._save_settings(entries),
            style="primary",
        ).pack()

        self._settings_entries = entries

        # L9: Attach tooltips to key settings fields
        _tooltips = {
            "general.company_oib": "OIB mora sadr\u017eavati to\u010dno 11 znamenki",
            "generation.default_effective_date": "Format: GGGG-MM-DD (npr. 2026-03-01)",
            "api_key": "Anthropic API klju\u010d (po\u010dinje s 'sk-ant-')",
            "paths.source": "Putanja do mape s ugovorima",
        }
        for key, tip_text in _tooltips.items():
            widget = entry_widgets.get(key)
            if widget:
                ToolTip(widget, tip_text)

    def _pick_folder(self, var: tk.StringVar) -> None:
        path = filedialog.askdirectory(title="Odaberite mapu")
        if path:
            var.set(path)

    def _save_settings(self, entries: dict[str, tk.StringVar]) -> None:
        """Write pipeline.toml and .env from form values."""
        # M31: Validate fields before saving
        oib = entries["general.company_oib"].get().strip()
        if oib and not re.match(r"^\d{11}$", oib):
            messagebox.showwarning(
                "Neispravan OIB",
                "OIB mora sadr\u017eavati to\u010dno 11 znamenki.",
            )
            return

        eff_date = entries["generation.default_effective_date"].get().strip()
        if eff_date and not re.match(r"^\d{4}-\d{2}-\d{2}$", eff_date):
            messagebox.showwarning(
                "Neispravan datum",
                "Datum mora biti u formatu GGGG-MM-DD.",
            )
            return

        source_path = entries["paths.source"].get().strip()
        if source_path and not Path(source_path).is_dir():
            # Try as relative to project root
            abs_path = _PROJECT_ROOT / source_path
            if not abs_path.is_dir():
                messagebox.showwarning(
                    "Mapa ne postoji",
                    f"Mapa s ugovorima ne postoji:\n{source_path}",
                )
                return

        api_key = entries["api_key"].get().strip()
        if not api_key:
            messagebox.showwarning(
                "Nedostaje API klju\u010d",
                "Anthropic API klju\u010d ne smije biti prazan.",
            )
            return

        try:
            toml_lines = [
                "[general]",
                f'company_name = "{entries["general.company_name"].get()}"',
                f'company_oib = "{oib}"',
                f'company_address = "{entries["general.company_address"].get()}"',
                f'company_director = "{entries["general.company_director"].get()}"',
                'default_location = "Zagreb"',
                "",
                "[paths]",
                f'source = "{entries["paths.source"].get()}"',
                'working_dir = "./data"',
                'output_dir = "./output"',
                'template = "./templates/default/aneks_template.docx"',
                "",
                "[extraction]",
                'model = "claude-sonnet-4-6-20250514"',
                "use_batch_api = true",
                'confidence_threshold = "medium"',
                "",
                "[currency]",
                "hrk_to_eur_rate = 7.53450",
                'default_currency = "EUR"',
                "",
                "[generation]",
                f'default_effective_date = "{eff_date}"',
                'vat_note = "Sve cijene su izra\u017eene bez PDV-a."',
                "",
            ]

            toml_path = _PROJECT_ROOT / "pipeline.toml"
            toml_path.write_text("\n".join(toml_lines), encoding="utf-8")

            # Write .env
            env_path = _PROJECT_ROOT / ".env"
            env_path.write_text(f"ANTHROPIC_API_KEY={api_key}\n", encoding="utf-8")

            self._set_status("Postavke spremljene")
            self._show_banner("Postavke uspje\u0161no spremljene.", "success")
        except Exception as exc:
            self._show_banner(f"Spremanje nije uspjelo: {exc}", "error")

    # ── F3: API key connection test ──────────────────────────────────────

    def _test_api_key(self) -> None:
        """Test the Anthropic API key in a background thread."""
        if self._api_key_var is None:
            return
        key = self._api_key_var.get().strip()
        if not key:
            messagebox.showwarning(
                "Gre\u0161ka",
                "API klju\u010d je prazan",
            )
            return

        self._api_test_btn.configure(state=tk.DISABLED)
        self._api_test_status.configure(text="Testiranje...", foreground="#3498db")

        # Use a dedicated queue to avoid conflicts with main pipeline queue
        api_queue: queue.Queue[tuple[str, Any]] = queue.Queue()

        def _do_test() -> None:
            try:
                import anthropic

                client = anthropic.Anthropic(api_key=key)
                client.messages.create(
                    model="claude-haiku-4-5-20251001",
                    max_tokens=10,
                    messages=[{"role": "user", "content": "Hi"}],
                )
                api_queue.put(("api_test_ok", None))
            except Exception as exc:
                exc_type = type(exc).__name__
                api_queue.put(("api_test_fail", f"{exc_type}: {exc}"))

        def _poll_api_test() -> None:
            try:
                msg = api_queue.get_nowait()
                self._api_test_btn.configure(state=tk.NORMAL)
                if msg[0] == "api_test_ok":
                    self._api_test_status.configure(
                        text="Uspjeh!", foreground="#27ae60"
                    )
                else:
                    err_msg = msg[1] if len(msg) > 1 else "Nepoznata greška"
                    if "AuthenticationError" in str(err_msg):
                        self._api_test_status.configure(
                            text="Nevaljan", foreground="#e74c3c"
                        )
                    else:
                        self._api_test_status.configure(
                            text="Gre\u0161ka", foreground="#e74c3c"
                        )
                    self._show_banner(f"Gre\u0161ka pri testiranju API-ja: {err_msg}", "error")
                return
            except queue.Empty:
                pass
            self.root.after(200, _poll_api_test)

        threading.Thread(target=_do_test, daemon=True).start()
        _poll_api_test()

    # ── Step 1: Setup ────────────────────────────────────────────────────

    def _build_setup(self, parent: ttk.Frame) -> None:
        self._add_title(
            parent,
            "Priprema",
            "Skeniranje i kopiranje ugovora",
        )

        # Show inventory status if it exists (quiet config load during UI build)
        cfg = self._load_config_safe(quiet=True)
        self._add_config_warning(parent)

        if cfg and cfg.inventory_path.exists():
            try:
                from doc_pipeline.models import Inventory
                inv = Inventory.load(cfg.inventory_path)
                info = (
                    f"Postoje\u0107i inventar: {inv.total_clients} klijenata, "
                    f"{inv.clients_with_contracts} s ugovorima, "
                    f"{inv.clients_with_annexes} s aneksima"
                )
                ttk.Label(parent, text=info, foreground="#27ae60").pack(
                    anchor=tk.W, pady=(0, 8)
                )
            except Exception:
                pass

        # Buttons
        btn_frame = ttk.Frame(parent)
        btn_frame.pack(fill=tk.X, pady=(0, 4))

        self._setup_btn = self._make_button(btn_frame, "Pokreni pripremu", self._run_setup, style="primary")
        self._setup_btn.pack(side=tk.LEFT)

        self._setup_rescan_btn = self._make_button(btn_frame, "Samo skeniraj", lambda: self._run_setup(scan_only=True), style="secondary")
        self._setup_rescan_btn.pack(side=tk.LEFT, padx=(8, 0))

        # Cancel button — initially disabled
        self._setup_cancel_btn = self._make_button(btn_frame, "Odustani", self._on_cancel_click, style="secondary")
        self._setup_cancel_btn.configure(state=tk.DISABLED)
        self._setup_cancel_btn.pack(side=tk.LEFT, padx=(8, 0))

        self._setup_progress, self._setup_pct = self._add_progress(parent)
        self._setup_log = self._add_log_area(parent, step_name="setup")

    def _run_setup(self, scan_only: bool = False) -> None:
        if self._running:
            return
        cfg = self._load_config_safe()
        if cfg is None:
            return

        self._running = True
        self._cancel_event.clear()
        self._setup_btn.configure(state=tk.DISABLED)
        self._setup_rescan_btn.configure(state=tk.DISABLED)
        self._setup_cancel_btn.configure(state=tk.NORMAL)
        self._setup_progress.start(10)
        self._set_status("Priprema u tijeku...")
        self._buffered.install()

        def task() -> None:
            try:
                if self._cancel_event.is_set():
                    self._queue.put(("setup_cancelled", None))
                    return
                from doc_pipeline.phases.setup import run_setup
                inventory = run_setup(cfg, scan_only=scan_only)
                if self._cancel_event.is_set():
                    self._queue.put(("setup_cancelled", None))
                    return
                self._queue.put(("setup_done", inventory))
            except Exception as exc:
                self._queue.put(("setup_error", str(exc)))
            finally:
                self._buffered.restore()

        threading.Thread(target=task, daemon=True).start()
        self._poll_queue(self._setup_log, self._setup_progress, self._on_setup_done, self._setup_pct)

    def _on_setup_done(self, msg_type: str, data: Any) -> None:
        self._setup_progress.stop()
        self._setup_pct.set("")
        self._setup_progress.configure(mode="indeterminate")
        self._running = False
        self._setup_btn.configure(state=tk.NORMAL)
        self._setup_rescan_btn.configure(state=tk.NORMAL)
        if hasattr(self, "_setup_cancel_btn") and self._setup_cancel_btn.winfo_exists():
            self._setup_cancel_btn.configure(state=tk.DISABLED)

        if msg_type == "setup_cancelled":
            self._set_status("Priprema otkazana")
            self._log_append(
                self._setup_log,
                "\n--- OTKAZANO ---\n",
            )
            return

        if msg_type == "setup_done":
            inv = data
            self._set_status(
                f"Priprema zavr\u0161ena \u2014 {inv.total_clients} klijenata"
            )
            self._log_append(
                self._setup_log,
                f"\n--- ZAVR\u0160ENO ---\n"
                f"Klijenti: {inv.total_clients}\n"
                f"S ugovorima: {inv.clients_with_contracts}\n"
                f"S aneksima: {inv.clients_with_annexes}\n"
                f"Ozna\u010deni: {len(inv.flagged_clients)}\n",
            )
            self._show_banner(
                f"Priprema zavr\u0161ena \u2014 {inv.total_clients} klijenata, "
                f"{inv.clients_with_contracts} s ugovorima, "
                f"{inv.clients_with_annexes} s aneksima",
                "success",
            )
            # Update sidebar availability after setup completes
            self._update_sidebar()
            # M34: Next step affordance
            self._add_next_step_button(self._content_frame, 2)
        else:
            self._set_status("Priprema neuspjela")
            self._log_append(self._setup_log, f"\n--- GRE\u0160KA ---\n{data}\n")
            self._show_banner(f"Priprema nije uspjela: {data}", "error")

    # ── Step 2: Extraction ───────────────────────────────────────────────

    def _build_extraction(self, parent: ttk.Frame) -> None:
        self._add_title(
            parent,
            "Ekstrakcija",
            "Čitanje ugovora i izvlačenje cijena",
        )

        # Show extraction status (quiet config load during UI build)
        cfg = self._load_config_safe(quiet=True)
        self._add_config_warning(parent)

        # L8: Show which clients were extracted
        if cfg and cfg.extractions_path.exists():
            json_files = sorted(cfg.extractions_path.glob("*.json"))
            n_extracted = len(json_files)
            if n_extracted > 0:
                # Build list of extracted client names from filenames
                client_names = [f.stem for f in json_files]
                if len(client_names) > 10:
                    display_names = ", ".join(client_names[:10])
                    display_names += f" ... i još {len(client_names) - 10}"
                else:
                    display_names = ", ".join(client_names)

                ttk.Label(
                    parent,
                    text=f"Već ekstrahirano: {n_extracted} klijenata",
                    foreground="#27ae60",
                ).pack(anchor=tk.W, pady=(0, 2))
                ttk.Label(
                    parent,
                    text=display_names,
                    foreground="#7f8c8d",
                    font=("Arial", 9),
                    wraplength=700,
                    justify=tk.LEFT,
                ).pack(anchor=tk.W, pady=(0, 8))

        # F1: Client filter for extraction
        filter_frame = ttk.Frame(parent)
        filter_frame.pack(fill=tk.X, pady=(0, 4))
        ttk.Label(
            filter_frame,
            text="Klijenti (odvojeno zarezom):",
            font=("Arial", 9),
        ).pack(side=tk.LEFT)
        self._extract_clients_var = tk.StringVar()
        extract_filter_entry = ttk.Entry(
            filter_frame, textvariable=self._extract_clients_var, width=50
        )
        extract_filter_entry.pack(side=tk.LEFT, padx=(8, 0), fill=tk.X, expand=True)
        ToolTip(
            extract_filter_entry,
            "Prazno = svi klijenti",
        )

        # Buttons
        btn_frame = ttk.Frame(parent)
        btn_frame.pack(fill=tk.X, pady=(0, 4))

        self._extract_btn = self._make_button(
            btn_frame, "Pokreni ekstrakciju", self._run_extraction, style="primary"
        )
        self._extract_btn.pack(side=tk.LEFT)

        self._extract_force_btn = self._make_button(
            btn_frame, "Ponovi sve", lambda: self._run_extraction(force=True), style="danger"
        )
        self._extract_force_btn.pack(side=tk.LEFT, padx=(8, 0))

        self._extract_ss_btn = self._make_button(
            btn_frame, "Samo tablica", lambda: self._run_extraction(spreadsheet_only=True), style="secondary"
        )
        self._extract_ss_btn.pack(side=tk.LEFT, padx=(8, 0))

        # Cancel button (always visible, disabled until operation starts)
        self._extract_cancel_btn = self._make_button(
            btn_frame, "Odustani", self._on_cancel_click, style="secondary"
        )
        self._extract_cancel_btn.configure(state=tk.DISABLED)
        self._extract_cancel_btn.pack(side=tk.LEFT, padx=(8, 0))

        self._extract_progress, self._extract_pct = self._add_progress(parent)
        self._extract_log = self._add_log_area(parent, step_name="extraction")

    def _run_extraction(
        self, force: bool = False, spreadsheet_only: bool = False
    ) -> None:
        if self._running:
            return
        cfg = self._load_config_safe()
        if cfg is None:
            return

        if not cfg.inventory_path.exists():
            messagebox.showwarning(
                "Nedostaje inventar",
                "Inventar nije pronađen. Pokrenite najprije korak 'Priprema'.",
            )
            return

        # F1: Parse client filter
        client_names: list[str] | None = None
        if self._extract_clients_var:
            raw = self._extract_clients_var.get().strip()
            if raw:
                client_names = [c.strip() for c in raw.split(",") if c.strip()]

        # M32: Re-extract confirmation for force mode
        if force:
            if not messagebox.askyesno(
                "Potvrda",
                "Ovo će ponovo ekstrahirati sve klijente i koristiti API kredite (~$6-13).\n\nNastaviti?",
            ):
                return

        self._running = True
        self._cancel_event.clear()
        self._extract_btn.configure(state=tk.DISABLED)
        self._extract_force_btn.configure(state=tk.DISABLED)
        self._extract_ss_btn.configure(state=tk.DISABLED)
        self._extract_cancel_btn.configure(state=tk.NORMAL)
        self._extract_progress.start(10)
        self._set_status("Ekstrakcija u tijeku...")
        self._buffered.install()

        def task() -> None:
            try:
                if self._cancel_event.is_set():
                    self._queue.put(("extract_cancelled", None))
                    return
                from doc_pipeline.phases.extraction import run_extraction

                kwargs: dict[str, Any] = {
                    "force": force,
                    "spreadsheet_only": spreadsheet_only,
                }
                if client_names:
                    kwargs["client_names"] = client_names

                results = run_extraction(cfg, **kwargs)
                if self._cancel_event.is_set():
                    self._queue.put(("extract_cancelled", None))
                    return
                self._queue.put(("extract_done", len(results)))
            except Exception as exc:
                self._queue.put(("extract_error", str(exc)))
            finally:
                self._buffered.restore()

        threading.Thread(target=task, daemon=True).start()
        self._poll_queue(self._extract_log, self._extract_progress, self._on_extract_done, self._extract_pct)

    def _on_extract_done(self, msg_type: str, data: Any) -> None:
        self._extract_progress.stop()
        self._extract_pct.set("")
        self._extract_progress.configure(mode="indeterminate")
        self._running = False
        self._extract_btn.configure(state=tk.NORMAL)
        self._extract_force_btn.configure(state=tk.NORMAL)
        self._extract_ss_btn.configure(state=tk.NORMAL)
        if hasattr(self, "_extract_cancel_btn") and self._extract_cancel_btn.winfo_exists():
            self._extract_cancel_btn.configure(state=tk.DISABLED)

        if msg_type == "extract_cancelled":
            self._set_status("Ekstrakcija otkazana")
            self._log_append(
                self._extract_log,
                "\n--- OTKAZANO ---\n",
            )
            return

        if msg_type == "extract_done":
            n = data
            self._set_status(f"Ekstrakcija završena — {n} klijenata")
            self._log_append(
                self._extract_log,
                f"\n--- ZAVRŠENO ---\n"
                f"Ekstrahirano klijenata: {n}\n"
                f"Tablica spremna: output/control_spreadsheet.xlsx\n",
            )
            # Update sidebar availability
            self._update_sidebar()
            self._show_banner(
                f"Ekstrakcija završena — {n} klijenata. Otvorite tablicu u koraku Pregled.",
                "success",
            )
            # M34: Next step affordance
            self._add_next_step_button(self._content_frame, 3)
        else:
            self._set_status("Ekstrakcija neuspjela")
            self._log_append(self._extract_log, f"\n--- GREŠKA ---\n{data}\n")
            self._show_banner(f"Ekstrakcija nije uspjela: {data}", "error")

    # ── Step 3: Review ───────────────────────────────────────────────────

    def _build_review(self, parent: ttk.Frame) -> None:
        self._add_title(parent, "Pregled tablice", "Ručni pregled i odobravanje")

        # Concise instruction steps
        instructions = ttk.Frame(parent)
        instructions.pack(fill=tk.X, pady=(0, 8))

        steps = [
            ("1.", "Otvorite kontrolnu tablicu klikom na gumb ispod"),
            ("2.", "Na listu 'Pregled klijenata' — stupac Status (I) označite kao 'Odobreno'"),
            ("3.", "Na listu 'Cijene' — unesite nove cijene u stupac 'Nova cijena EUR' (G)"),
            ("4.", "Spremite i zatvorite tablicu"),
            ("5.", "Kliknite 'Nastavi na generiranje'"),
        ]
        for num, text in steps:
            step_row = ttk.Frame(instructions)
            step_row.pack(fill=tk.X, pady=2)
            ttk.Label(
                step_row,
                text=num,
                font=("Arial", 10, "bold"),
                foreground="#2980b9",
                width=3,
            ).pack(side=tk.LEFT)
            ttk.Label(
                step_row,
                text=text,
                font=("Arial", 10),
                wraplength=600,
                justify=tk.LEFT,
            ).pack(side=tk.LEFT, fill=tk.X, expand=True)

        # Buttons
        btn_frame = ttk.Frame(parent)
        btn_frame.pack(fill=tk.X, pady=(4, 0))

        self._make_button(
            btn_frame,
            text="Otvori tablicu",
            command=self._open_spreadsheet,
            style="primary",
        ).pack(side=tk.LEFT)

        self._make_button(
            btn_frame,
            text="Nastavi na generiranje \u2192",
            command=lambda: self._show_step(4),
            style="success",
        ).pack(side=tk.LEFT, padx=(8, 0))

        # F2: Per-client extraction preview
        preview_frame = ttk.LabelFrame(
            parent,
            text="Pregled ekstrakcija po klijentu",
            padding=8,
        )
        preview_frame.pack(fill=tk.BOTH, expand=True, pady=(8, 0))

        selector_frame = ttk.Frame(preview_frame)
        selector_frame.pack(fill=tk.X, pady=(0, 4))
        ttk.Label(selector_frame, text="Klijent:", font=("Arial", 10)).pack(side=tk.LEFT)

        cfg = self._load_config_safe(quiet=True)
        client_list: list[str] = []
        if cfg and cfg.extractions_path.exists():
            client_list = sorted(f.stem for f in cfg.extractions_path.glob("*.json"))

        self._review_client_var = tk.StringVar()
        client_combo = ttk.Combobox(
            selector_frame,
            textvariable=self._review_client_var,
            values=client_list,
            state="readonly",
            width=40,
        )
        client_combo.pack(side=tk.LEFT, padx=(8, 0))
        client_combo.bind("<<ComboboxSelected>>", lambda e: self._show_client_preview())

        self._review_preview_text = tk.Text(
            preview_frame,
            wrap=tk.WORD,
            font=("Consolas" if sys.platform == "win32" else "Menlo", 10),
            bg="#fdf6e3",
            fg="#586e75",
            height=8,
            state=tk.DISABLED,
            relief=tk.FLAT,
            padx=8,
            pady=8,
        )
        self._review_preview_text.pack(fill=tk.BOTH, expand=True)

    def _open_spreadsheet(self) -> None:
        cfg = self._load_config_safe()
        if cfg is None:
            return
        if not cfg.spreadsheet_path.exists():
            messagebox.showwarning(
                "Tablica nije pronađena",
                "Kontrolna tablica ne postoji.\nPokrenite najprije korak 'Ekstrakcija'.",
            )
            return
        self._open_file(cfg.spreadsheet_path)

    # ── F2: Per-client extraction preview ─────────────────────────────────

    def _show_client_preview(self) -> None:
        """Show extraction summary for the selected client."""
        client_name = self._review_client_var.get()
        if not client_name:
            return

        cfg = self._load_config_safe(quiet=True)
        if not cfg:
            return

        json_path = cfg.extractions_path / f"{client_name}.json"
        if not json_path.exists():
            self._set_review_preview(f"Datoteka nije pronađena:\n{json_path}")
            return

        try:
            data = json.loads(json_path.read_text(encoding="utf-8"))

            # Extraction data is nested under the 'extraction' key
            ex = data.get("extraction") or {}

            lines: list[str] = []
            lines.append(f"Klijent: {ex.get('client_name') or client_name}")
            lines.append(f"OIB: {ex.get('client_oib') or 'N/A'}")
            lines.append(
                f"Broj ugovora: "
                f"{ex.get('contract_number') or ex.get('parent_contract_number') or 'N/A'}"
            )
            lines.append(f"Datum: {ex.get('document_date') or 'N/A'}")
            lines.append(
                f"Pouzdanost: {ex.get('confidence') or 'N/A'}"
            )
            lines.append(f"Valuta: {ex.get('currency') or 'N/A'}")
            lines.append(f"Izvorni dokument: {data.get('source_file') or 'N/A'}")

            # Show pricing items
            items = ex.get("pricing_items", [])
            lines.append(f"\nStavke ({len(items)}):")
            lines.append("-" * 50)
            for item in items:
                name = item.get("service_name", "?")
                price = item.get("price_value", item.get("price_raw", "?"))
                currency = item.get("currency", ex.get("currency", ""))
                unit = item.get("unit") or item.get("designation") or ""
                lines.append(f"  {name}: {price} {currency} {f'/ {unit}' if unit else ''}")

            # Show notes
            notes = ex.get("notes", [])
            if notes:
                notes_str = "; ".join(notes) if isinstance(notes, list) else str(notes)
                lines.append(f"\nNapomene: {notes_str}")

            # Show error if extraction failed
            error = data.get("error")
            if error:
                lines.append(f"\n[GREŠKA]: {error}")

            self._set_review_preview("\n".join(lines))
        except Exception as exc:
            self._set_review_preview(
                f"Greška pri čitanju:\n{exc}"
            )

    def _set_review_preview(self, text: str) -> None:
        """Set the review preview text area content."""
        self._review_preview_text.configure(state=tk.NORMAL)
        self._review_preview_text.delete("1.0", tk.END)
        self._review_preview_text.insert("1.0", text)
        self._review_preview_text.configure(state=tk.DISABLED)

    # ── Step 4: Generation ───────────────────────────────────────────────

    def _build_generation(self, parent: ttk.Frame) -> None:
        self._add_title(
            parent,
            "Generiranje aneksa",
            "Kreiranje novih aneks dokumenata",
        )

        # Starting number input
        num_frame = ttk.Frame(parent)
        num_frame.pack(fill=tk.X, pady=(0, 4))

        ttk.Label(
            num_frame,
            text="Po\u010detni broj aneksa:",
        ).pack(side=tk.LEFT)
        self._start_num_var = tk.StringVar(value="1")
        ttk.Entry(num_frame, textvariable=self._start_num_var, width=8).pack(
            side=tk.LEFT, padx=(8, 0)
        )

        # F1: Client filter for generation
        filter_frame = ttk.Frame(parent)
        filter_frame.pack(fill=tk.X, pady=(0, 4))
        ttk.Label(
            filter_frame,
            text="Klijenti (odvojeno zarezom):",
            font=("Arial", 9),
        ).pack(side=tk.LEFT)
        self._gen_clients_var = tk.StringVar()
        gen_filter_entry = ttk.Entry(
            filter_frame, textvariable=self._gen_clients_var, width=50
        )
        gen_filter_entry.pack(side=tk.LEFT, padx=(8, 0), fill=tk.X, expand=True)
        ToolTip(
            gen_filter_entry,
            "Prazno = svi odobreni klijenti",
        )

        # Buttons
        btn_frame = ttk.Frame(parent)
        btn_frame.pack(fill=tk.X, pady=(0, 4))

        self._gen_preview_btn = self._make_button(btn_frame, "Pregledaj", self._run_preview, style="primary")
        self._gen_preview_btn.pack(side=tk.LEFT)

        self._gen_btn = self._make_button(btn_frame, "Generiraj anekse", self._run_generation, style="secondary")
        self._gen_btn.pack(side=tk.LEFT, padx=(8, 0))

        self._gen_open_btn = self._make_button(btn_frame, "Otvori mapu", self._open_output_folder, style="secondary")
        self._gen_open_btn.pack(side=tk.LEFT, padx=(8, 0))

        # Cancel button — initially disabled
        self._gen_cancel_btn = self._make_button(btn_frame, "Odustani", self._on_cancel_click, style="secondary")
        self._gen_cancel_btn.configure(state=tk.DISABLED)
        self._gen_cancel_btn.pack(side=tk.LEFT, padx=(8, 0))

        self._gen_progress, self._gen_pct = self._add_progress(parent)
        self._gen_log = self._add_log_area(parent, step_name="generation")

    def _get_start_number(self) -> int | None:
        try:
            n = int(self._start_num_var.get())
            if n < 1:
                raise ValueError
            return n
        except ValueError:
            messagebox.showwarning(
                "Neispravan broj",
                "Unesite ispravan po\u010detni broj (npr. 1, 30).",
            )
            return None

    def _get_gen_client_names(self) -> list[str] | None:
        """Parse the client filter for the generation step (F1)."""
        if self._gen_clients_var:
            raw = self._gen_clients_var.get().strip()
            if raw:
                return [c.strip() for c in raw.split(",") if c.strip()]
        return None

    def _run_preview(self) -> None:
        if self._running:
            return
        cfg = self._load_config_safe()
        if cfg is None:
            return
        start = self._get_start_number()
        if start is None:
            return

        client_names = self._get_gen_client_names()

        self._running = True
        self._cancel_event.clear()
        self._gen_preview_btn.configure(state=tk.DISABLED)
        self._gen_btn.configure(state=tk.DISABLED)
        self._gen_cancel_btn.configure(state=tk.NORMAL)
        self._gen_progress.start(10)
        self._set_status("Pregledavanje...")
        self._buffered.install()

        def task() -> None:
            try:
                if self._cancel_event.is_set():
                    self._queue.put(("preview_cancelled", None))
                    return
                from doc_pipeline.phases.generation import run_generation

                kwargs: dict[str, Any] = {
                    "start_number": start,
                    "dry_run": True,
                }
                if client_names:
                    kwargs["client_names"] = client_names

                run_generation(cfg, **kwargs)
                if self._cancel_event.is_set():
                    self._queue.put(("preview_cancelled", None))
                    return
                self._queue.put(("preview_done", None))
            except Exception as exc:
                self._queue.put(("preview_error", str(exc)))
            finally:
                self._buffered.restore()

        threading.Thread(target=task, daemon=True).start()
        self._poll_queue(self._gen_log, self._gen_progress, self._on_preview_done, self._gen_pct)

    def _on_preview_done(self, msg_type: str, data: Any) -> None:
        self._gen_progress.stop()
        self._gen_pct.set("")
        self._gen_progress.configure(mode="indeterminate")
        self._running = False
        self._gen_preview_btn.configure(state=tk.NORMAL)
        self._gen_btn.configure(state=tk.NORMAL)
        if hasattr(self, "_gen_cancel_btn") and self._gen_cancel_btn.winfo_exists():
            self._gen_cancel_btn.configure(state=tk.DISABLED)

        if msg_type == "preview_cancelled":
            self._set_status("Pregled otkazan")
            self._log_append(self._gen_log, "\n--- OTKAZANO ---\n")
            return

        if msg_type == "preview_done":
            self._set_status("Pregled zavr\u0161en")
            self._show_banner("Pregled zavr\u0161en. Provjerite zapisnik.", "info")
            # Swap: "Generiraj" becomes primary, "Pregledaj" becomes secondary
            if hasattr(self, '_gen_btn') and self._gen_btn.winfo_exists():
                self._restyle_button(self._gen_btn, "primary")
            if hasattr(self, '_gen_preview_btn') and self._gen_preview_btn.winfo_exists():
                self._restyle_button(self._gen_preview_btn, "secondary")
        else:
            self._set_status("Pregled neuspio")
            self._log_append(self._gen_log, f"\n--- GRE\u0160KA ---\n{data}\n")
            self._show_banner(f"Pregled nije uspio: {data}", "error")

    def _run_generation(self) -> None:
        if self._running:
            return
        cfg = self._load_config_safe()
        if cfg is None:
            return
        start = self._get_start_number()
        if start is None:
            return

        client_names = self._get_gen_client_names()

        if not messagebox.askyesno(
            "Potvrda",
            "Jeste li sigurni da \u017eelite generirati anekse?\n"
            "Provjerite najprije pregled.",
        ):
            return

        self._running = True
        self._cancel_event.clear()
        self._gen_preview_btn.configure(state=tk.DISABLED)
        self._gen_btn.configure(state=tk.DISABLED)
        self._gen_cancel_btn.configure(state=tk.NORMAL)
        self._gen_progress.start(10)
        self._set_status("Generiranje u tijeku...")
        self._buffered.install()

        def task() -> None:
            try:
                if self._cancel_event.is_set():
                    self._queue.put(("gen_cancelled", None))
                    return
                from doc_pipeline.phases.generation import run_generation

                kwargs: dict[str, Any] = {"start_number": start}
                if client_names:
                    kwargs["client_names"] = client_names

                paths = run_generation(cfg, **kwargs)
                if self._cancel_event.is_set():
                    self._queue.put(("gen_cancelled", None))
                    return
                self._queue.put(("gen_done", len(paths)))
            except Exception as exc:
                self._queue.put(("gen_error", str(exc)))
            finally:
                self._buffered.restore()

        threading.Thread(target=task, daemon=True).start()
        self._poll_queue(self._gen_log, self._gen_progress, self._on_gen_done, self._gen_pct)

    def _on_gen_done(self, msg_type: str, data: Any) -> None:
        self._gen_progress.stop()
        self._gen_pct.set("")
        self._gen_progress.configure(mode="indeterminate")
        self._running = False
        self._gen_preview_btn.configure(state=tk.NORMAL)
        self._gen_btn.configure(state=tk.NORMAL)
        if hasattr(self, "_gen_cancel_btn") and self._gen_cancel_btn.winfo_exists():
            self._gen_cancel_btn.configure(state=tk.DISABLED)

        if msg_type == "gen_cancelled":
            self._set_status("Generiranje otkazano")
            self._log_append(self._gen_log, "\n--- OTKAZANO ---\n")
            return

        if msg_type == "gen_done":
            n = data
            self._set_status(f"Generirano {n} aneksa")
            self._log_append(
                self._gen_log,
                f"\n--- ZAVR\u0160ENO ---\n"
                f"Generirano aneksa: {n}\n",
            )
            self._show_banner(
                f"Generirano {n} aneksa! Datoteke se nalaze u mapi output/annexes/",
                "success",
                auto_dismiss=False,
            )
        else:
            self._set_status("Generiranje neuspjelo")
            self._log_append(self._gen_log, f"\n--- GRE\u0160KA ---\n{data}\n")
            self._show_banner(f"Generiranje nije uspjelo: {data}", "error")

    def _open_output_folder(self) -> None:
        cfg = self._load_config_safe()
        if cfg is None:
            return
        folder = cfg.annexes_output_path
        if not folder.exists():
            folder = cfg.output_path
        self._open_file(folder)

    # ── Background task polling ──────────────────────────────────────────

    def _poll_queue(
        self,
        log_widget: tk.Text,
        progress_bar: ttk.Progressbar,
        done_callback: Any,
        pct_var: tk.StringVar | None = None,
    ) -> None:
        """Poll for background thread messages and buffered console output."""
        # Check for buffered console output
        new_text = self._buffered.read_new()
        if new_text:
            self._log_append(log_widget, new_text)

        # Check message queue
        try:
            msg = self._queue.get_nowait()
            msg_type = msg[0]

            # H19: Handle progress updates
            if msg_type == "progress" and len(msg) >= 3:
                current, total = msg[1], msg[2]
                self._update_progress(progress_bar, current, total, pct_var)
                # Don't call done_callback for progress messages — keep polling
            else:
                done_callback(msg_type, msg[1] if len(msg) > 1 else None)
                return
        except queue.Empty:
            pass

        # Continue polling
        self.root.after(
            100,
            lambda: self._poll_queue(log_widget, progress_bar, done_callback, pct_var),
        )

    # ── Utility ──────────────────────────────────────────────────────────

    @staticmethod
    def _open_file(path: Path) -> None:
        """Open a file or folder with the default system application."""
        path = Path(path)
        try:
            if platform.system() == "Darwin":
                subprocess.run(["open", str(path)], check=True)
            elif platform.system() == "Windows":
                os.startfile(str(path))  # type: ignore[attr-defined]
            else:
                subprocess.run(["xdg-open", str(path)], check=True)
        except Exception as e:
            # M35: Show error instead of silently ignoring
            messagebox.showwarning(
                "Gre\u0161ka",
                f"Nije mogu\u0107e otvoriti datoteku.\n\n{e}",
            )

    def run(self) -> None:
        """Start the GUI event loop."""
        self.root.mainloop()


def main() -> None:
    app = PipelineGUI()
    app.run()


if __name__ == "__main__":
    main()
