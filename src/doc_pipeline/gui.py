"""Tkinter GUI wizard for the contract price adjustment pipeline."""

from __future__ import annotations

import logging
import os
import platform
import queue
import re
import subprocess
import sys
import threading
import tkinter as tk
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
    ("0", "Postavke", "Settings"),
    ("1", "Priprema", "Setup — scan contracts"),
    ("2", "Ekstrakcija", "Extract pricing data"),
    ("3", "Pregled", "Review spreadsheet"),
    ("4", "Generiranje", "Generate annexes"),
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


# ── Main Application ─────────────────────────────────────────────────────────

class PipelineGUI:
    """Main GUI window — wizard style with sidebar steps."""

    def __init__(self) -> None:
        self.root = tk.Tk()
        self.root.title("Procudo — Pipeline za ugovore / Contract Pipeline")
        self.root.geometry("960x680")
        self.root.minsize(800, 560)

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

        # Cancel button reference (shown/hidden dynamically)
        self._cancel_btn: ttk.Button | None = None

        # "Next step" button reference (shown after phase completion)
        self._next_step_btn: ttk.Button | None = None

        # Mouse wheel binding id (for cleanup)
        self._mousewheel_binding_id: str | None = None

        self._build_ui()
        self._show_step(0)

    # ── WM_DELETE_WINDOW handler (C6) ─────────────────────────────────────

    def _on_close(self) -> None:
        """Handle window close request."""
        if self._running:
            if messagebox.askyesno(
                "Operacija u tijeku / Operation Running",
                "Operacija je u tijeku. Jeste li sigurni da \u017eelite zatvoriti?\n"
                "An operation is running. Are you sure you want to close?",
            ):
                self._cancel_event.set()
                self.root.destroy()
            # else: do nothing, user chose not to close
        else:
            self.root.destroy()

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
        style.configure(
            "NextStep.TButton",
            font=("Arial", 10, "bold"),
        )

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

        # Step labels
        for i, (num, hr_name, _en_name) in enumerate(STEPS):
            marker = "\u25cb"  # open circle
            lbl = ttk.Label(
                sidebar,
                text=f"  {marker}  {hr_name}",
                style="SidebarStep.TLabel",
                cursor="hand2",
            )
            lbl.pack(fill=tk.X)
            step_idx = i
            lbl.bind("<Button-1>", lambda e, s=step_idx: self._on_step_click(s))
            self._step_labels.append(lbl)

        # ── Content area ──
        self._content_outer = ttk.Frame(main_pane)
        main_pane.add(self._content_outer, weight=1)

        # ── Status bar ──
        self._status_var = tk.StringVar(value="Spreman / Ready")
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
                "Korak nije dostupan / Step Not Available",
                "Prvo dovr\u0161ite prethodni korak.\n"
                "Please complete the previous step first.",
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
            hr_name = STEPS[i][1]
            if i < self._current_step:
                lbl.configure(text=f"  \u2713  {hr_name}", style="SidebarStepDone.TLabel")
            elif i == self._current_step:
                lbl.configure(text=f"  \u25cf  {hr_name}", style="SidebarStepActive.TLabel")
            else:
                # H20: visually indicate locked vs available steps
                if self._is_step_available(i):
                    lbl.configure(
                        text=f"  \u25cb  {hr_name}",
                        style="SidebarStep.TLabel",
                    )
                    lbl.configure(cursor="hand2")
                else:
                    lbl.configure(
                        text=f"  \u25cb  {hr_name}",
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

    def _add_log_area(self, parent: ttk.Frame) -> tk.Text:
        """Add a scrollable log text area."""
        frame = ttk.Frame(parent)
        frame.pack(fill=tk.BOTH, expand=True, pady=(8, 0))

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
        return log

    def _add_progress(self, parent: ttk.Frame) -> ttk.Progressbar:
        bar = ttk.Progressbar(parent, mode="indeterminate", length=400)
        bar.pack(fill=tk.X, pady=(8, 0))
        return bar

    def _add_cancel_button(self, parent: ttk.Frame) -> ttk.Button:
        """Add a cancel button (initially hidden) for long operations."""
        btn = ttk.Button(
            parent,
            text="Odustani / Cancel",
            command=self._on_cancel_click,
        )
        # Don't pack yet — shown when operation starts
        return btn

    def _on_cancel_click(self) -> None:
        """Handle cancel button click."""
        self._cancel_event.set()
        if self._cancel_btn is not None:
            self._cancel_btn.configure(state=tk.DISABLED)
        self._set_status("Otkazivanje... / Cancelling...")

    def _show_cancel_button(self, btn: ttk.Button) -> None:
        """Show the cancel button."""
        self._cancel_btn = btn
        btn.pack(side=tk.LEFT, padx=(8, 0))

    def _hide_cancel_button(self) -> None:
        """Hide the cancel button."""
        if self._cancel_btn is not None:
            self._cancel_btn.pack_forget()
            self._cancel_btn = None

    def _update_progress(self, bar: ttk.Progressbar, current: int, total: int) -> None:
        """Switch progress bar to determinate mode and update value (H19)."""
        if total > 0:
            bar.stop()
            bar.configure(mode="determinate", maximum=100)
            bar["value"] = (current / total) * 100
        else:
            # Fallback to indeterminate
            bar.configure(mode="indeterminate")
            bar.start(10)

    def _log_append(self, log_widget: tk.Text, text: str) -> None:
        log_widget.configure(state=tk.NORMAL)
        log_widget.insert(tk.END, text)
        # H18: Bounded log area
        line_count = int(log_widget.index("end-1c").split(".")[0])
        if line_count > MAX_LOG_LINES:
            log_widget.delete("1.0", f"{line_count - MAX_LOG_LINES}.0")
        log_widget.see(tk.END)
        log_widget.configure(state=tk.DISABLED)

    def _add_next_step_button(self, parent: ttk.Frame, next_step: int) -> None:
        """Add a prominent 'Continue to Next Step' button after phase completion (M34)."""
        if next_step >= len(STEPS):
            return  # No next step
        btn = ttk.Button(
            parent,
            text="Nastavi na sljede\u0107i korak / Continue to Next Step",
            style="NextStep.TButton",
            command=lambda: self._show_step(next_step),
        )
        btn.pack(anchor=tk.W, pady=(8, 0))
        self._next_step_btn = btn

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
                    "Gre\u0161ka / Error",
                    f"Konfiguracija se ne mo\u017ee u\u010ditati:\n{exc}\n\n"
                    "Provjerite pipeline.toml i .env datoteke.\n"
                    "Check your pipeline.toml and .env files.",
                )
            return None

    def _add_config_warning(self, parent: ttk.Frame) -> None:
        """Show a subtle config warning label if config failed to load (M36)."""
        if self._config_load_error:
            lbl = ttk.Label(
                parent,
                text="\u26a0 Konfiguracija nije u\u010ditana / Config not loaded",
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
            "Konfiguracija pipeline-a / Pipeline settings",
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

        def add_field(label: str, key: str, default: str = "", masked: bool = False) -> None:
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
            row += 1

        def add_folder_field(label: str, key: str, default: str = "") -> None:
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
            row += 1

        form_frame.columnconfigure(1, weight=1)

        # Section: Paths
        ttk.Label(form_frame, text="Putanje / Paths", font=("Arial", 11, "bold")).grid(
            row=row, column=0, columnspan=2, sticky=tk.W, pady=(8, 4)
        )
        row += 1

        add_folder_field(
            "Mapa s ugovorima / Contracts folder:",
            "paths.source",
            cfg.paths.source if cfg else "./contracts",
        )

        # Section: Company info
        ttk.Label(
            form_frame, text="Podaci o tvrtki / Company Info", font=("Arial", 11, "bold")
        ).grid(row=row, column=0, columnspan=2, sticky=tk.W, pady=(16, 4))
        row += 1

        add_field(
            "Naziv tvrtke / Company name:",
            "general.company_name",
            cfg.general.company_name if cfg else "",
        )
        add_field("OIB:", "general.company_oib", cfg.general.company_oib if cfg else "")
        add_field(
            "Adresa / Address:",
            "general.company_address",
            cfg.general.company_address if cfg else "",
        )
        add_field(
            "Direktor / Director:",
            "general.company_director",
            cfg.general.company_director if cfg else "",
        )

        # Section: API
        ttk.Label(
            form_frame, text="API / Ekstrakcija / Extraction", font=("Arial", 11, "bold")
        ).grid(row=row, column=0, columnspan=2, sticky=tk.W, pady=(16, 4))
        row += 1

        add_field(
            "Anthropic API klju\u010d / API key:",
            "api_key",
            cfg.anthropic_api_key if cfg else "",
            masked=True,
        )

        # Section: Generation
        ttk.Label(
            form_frame, text="Generiranje / Generation", font=("Arial", 11, "bold")
        ).grid(row=row, column=0, columnspan=2, sticky=tk.W, pady=(16, 4))
        row += 1

        add_field(
            "Datum stupanja na snagu / Effective date (YYYY-MM-DD):",
            "generation.default_effective_date",
            cfg.generation.default_effective_date if cfg else "2026-03-01",
        )

        # Save button
        btn_frame = ttk.Frame(form_frame)
        btn_frame.grid(row=row, column=0, columnspan=2, pady=(20, 8))

        ttk.Button(
            btn_frame,
            text="Spremi postavke / Save Settings",
            command=lambda: self._save_settings(entries),
        ).pack()

        self._settings_entries = entries

    def _pick_folder(self, var: tk.StringVar) -> None:
        path = filedialog.askdirectory(title="Odaberite mapu / Select folder")
        if path:
            var.set(path)

    def _save_settings(self, entries: dict[str, tk.StringVar]) -> None:
        """Write pipeline.toml and .env from form values."""
        # M31: Validate fields before saving
        oib = entries["general.company_oib"].get().strip()
        if oib and not re.match(r"^\d{11}$", oib):
            messagebox.showwarning(
                "Neispravan OIB / Invalid OIB",
                "OIB mora sadr\u017eavati to\u010dno 11 znamenki.\n"
                "OIB must be exactly 11 digits.",
            )
            return

        eff_date = entries["generation.default_effective_date"].get().strip()
        if eff_date and not re.match(r"^\d{4}-\d{2}-\d{2}$", eff_date):
            messagebox.showwarning(
                "Neispravan datum / Invalid Date",
                "Datum mora biti u formatu YYYY-MM-DD.\n"
                "Date must be in YYYY-MM-DD format.",
            )
            return

        source_path = entries["paths.source"].get().strip()
        if source_path and not Path(source_path).is_dir():
            # Try as relative to project root
            abs_path = _PROJECT_ROOT / source_path
            if not abs_path.is_dir():
                messagebox.showwarning(
                    "Mapa ne postoji / Folder Not Found",
                    f"Mapa s ugovorima ne postoji:\n{source_path}\n\n"
                    f"The contracts folder does not exist.",
                )
                return

        api_key = entries["api_key"].get().strip()
        if not api_key:
            messagebox.showwarning(
                "Nedostaje API klju\u010d / Missing API Key",
                "Anthropic API klju\u010d ne smije biti prazan.\n"
                "Anthropic API key must not be empty.",
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
                'model = "claude-sonnet-4-5-20250929"',
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

            self._set_status("Postavke spremljene / Settings saved")
            messagebox.showinfo(
                "Spremljeno / Saved",
                "Postavke su spremljene.\nSettings have been saved.\n\n"
                "Mo\u017eete nastaviti na sljede\u0107i korak.\n"
                "You can proceed to the next step.",
            )
        except Exception as exc:
            messagebox.showerror(
                "Gre\u0161ka / Error",
                f"Spremanje nije uspjelo:\nSave failed:\n\n{exc}",
            )

    # ── Step 1: Setup ────────────────────────────────────────────────────

    def _build_setup(self, parent: ttk.Frame) -> None:
        self._add_title(
            parent,
            "Priprema",
            "Skeniranje i kopiranje ugovora / Scan and copy contracts",
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
                    f"{inv.clients_with_annexes} s aneksima\n"
                    f"Existing inventory: {inv.total_clients} clients, "
                    f"{inv.clients_with_contracts} with contracts, "
                    f"{inv.clients_with_annexes} with annexes"
                )
                ttk.Label(parent, text=info, foreground="#27ae60").pack(
                    anchor=tk.W, pady=(0, 8)
                )
            except Exception:
                pass

        # Buttons
        btn_frame = ttk.Frame(parent)
        btn_frame.pack(fill=tk.X, pady=(0, 4))

        self._setup_btn = ttk.Button(
            btn_frame,
            text="Pokreni pripremu / Run Setup",
            command=self._run_setup,
        )
        self._setup_btn.pack(side=tk.LEFT)

        self._setup_rescan_btn = ttk.Button(
            btn_frame,
            text="Samo skeniraj / Rescan Only",
            command=lambda: self._run_setup(scan_only=True),
        )
        self._setup_rescan_btn.pack(side=tk.LEFT, padx=(8, 0))

        # Cancel button (H13) — initially hidden
        self._setup_cancel_btn = self._add_cancel_button(btn_frame)

        self._setup_progress = self._add_progress(parent)
        self._setup_log = self._add_log_area(parent)

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
        self._show_cancel_button(self._setup_cancel_btn)
        self._setup_progress.start(10)
        self._set_status("Priprema u tijeku... / Setup running...")
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
        self._poll_queue(self._setup_log, self._setup_progress, self._on_setup_done)

    def _on_setup_done(self, msg_type: str, data: Any) -> None:
        self._setup_progress.stop()
        self._setup_progress.configure(mode="indeterminate")
        self._running = False
        self._setup_btn.configure(state=tk.NORMAL)
        self._setup_rescan_btn.configure(state=tk.NORMAL)
        self._hide_cancel_button()

        if msg_type == "setup_cancelled":
            self._set_status("Priprema otkazana / Setup cancelled")
            self._log_append(
                self._setup_log,
                "\n--- OTKAZANO / CANCELLED ---\n",
            )
            return

        if msg_type == "setup_done":
            inv = data
            self._set_status(
                f"Priprema zavr\u0161ena \u2014 {inv.total_clients} klijenata / "
                f"Setup complete \u2014 {inv.total_clients} clients"
            )
            self._log_append(
                self._setup_log,
                f"\n--- ZAVR\u0160ENO / COMPLETE ---\n"
                f"Klijenti / Clients: {inv.total_clients}\n"
                f"S ugovorima / With contracts: {inv.clients_with_contracts}\n"
                f"S aneksima / With annexes: {inv.clients_with_annexes}\n"
                f"Ozna\u010deni / Flagged: {len(inv.flagged_clients)}\n",
            )
            # Update sidebar availability after setup completes
            self._update_sidebar()
            # M34: Next step affordance
            self._add_next_step_button(self._content_frame, 2)
        else:
            self._set_status("Priprema neuspjela / Setup failed")
            self._log_append(self._setup_log, f"\n--- GRE\u0160KA / ERROR ---\n{data}\n")
            messagebox.showerror(
                "Gre\u0161ka / Error",
                f"Priprema nije uspjela:\nSetup failed:\n\n{data}",
            )

    # ── Step 2: Extraction ───────────────────────────────────────────────

    def _build_extraction(self, parent: ttk.Frame) -> None:
        self._add_title(
            parent,
            "Ekstrakcija",
            "\u010citanje ugovora i ekstrakcija cijena / Parse contracts and extract pricing",
        )

        # Show extraction status (quiet config load during UI build)
        cfg = self._load_config_safe(quiet=True)
        self._add_config_warning(parent)

        if cfg and cfg.extractions_path.exists():
            n_extracted = len(list(cfg.extractions_path.glob("*.json")))
            if n_extracted > 0:
                ttk.Label(
                    parent,
                    text=f"Ve\u0107 ekstrahirano: {n_extracted} klijenata / "
                    f"Already extracted: {n_extracted} clients",
                    foreground="#27ae60",
                ).pack(anchor=tk.W, pady=(0, 8))

        # Buttons
        btn_frame = ttk.Frame(parent)
        btn_frame.pack(fill=tk.X, pady=(0, 4))

        self._extract_btn = ttk.Button(
            btn_frame,
            text="Pokreni ekstrakciju / Run Extraction",
            command=self._run_extraction,
        )
        self._extract_btn.pack(side=tk.LEFT)

        self._extract_force_btn = ttk.Button(
            btn_frame,
            text="Ponovi sve / Re-extract All",
            command=lambda: self._run_extraction(force=True),
        )
        self._extract_force_btn.pack(side=tk.LEFT, padx=(8, 0))

        self._extract_ss_btn = ttk.Button(
            btn_frame,
            text="Samo tablica / Spreadsheet Only",
            command=lambda: self._run_extraction(spreadsheet_only=True),
        )
        self._extract_ss_btn.pack(side=tk.LEFT, padx=(8, 0))

        # Cancel button (H13)
        self._extract_cancel_btn = self._add_cancel_button(btn_frame)

        self._extract_progress = self._add_progress(parent)
        self._extract_log = self._add_log_area(parent)

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
                "Nedostaje inventar / Inventory Missing",
                "Inventar nije prona\u0111en. Pokrenite najprije korak 'Priprema'.\n\n"
                "Inventory not found. Run the 'Setup' step first.",
            )
            return

        # M32: Re-extract confirmation for force mode
        if force:
            if not messagebox.askyesno(
                "Potvrda / Confirm",
                "Ovo \u0107e ponovo ekstrahirati sve klijente i koristiti API kredite (~$6-13).\n"
                "This will re-extract all clients and use API credits (~$6-13).\n\n"
                "Nastaviti? / Continue?",
            ):
                return

        self._running = True
        self._cancel_event.clear()
        self._extract_btn.configure(state=tk.DISABLED)
        self._extract_force_btn.configure(state=tk.DISABLED)
        self._extract_ss_btn.configure(state=tk.DISABLED)
        self._show_cancel_button(self._extract_cancel_btn)
        self._extract_progress.start(10)
        self._set_status("Ekstrakcija u tijeku... / Extraction running...")
        self._buffered.install()

        def task() -> None:
            try:
                if self._cancel_event.is_set():
                    self._queue.put(("extract_cancelled", None))
                    return
                from doc_pipeline.phases.extraction import run_extraction
                results = run_extraction(
                    cfg,
                    force=force,
                    spreadsheet_only=spreadsheet_only,
                )
                if self._cancel_event.is_set():
                    self._queue.put(("extract_cancelled", None))
                    return
                self._queue.put(("extract_done", len(results)))
            except Exception as exc:
                self._queue.put(("extract_error", str(exc)))
            finally:
                self._buffered.restore()

        threading.Thread(target=task, daemon=True).start()
        self._poll_queue(self._extract_log, self._extract_progress, self._on_extract_done)

    def _on_extract_done(self, msg_type: str, data: Any) -> None:
        self._extract_progress.stop()
        self._extract_progress.configure(mode="indeterminate")
        self._running = False
        self._extract_btn.configure(state=tk.NORMAL)
        self._extract_force_btn.configure(state=tk.NORMAL)
        self._extract_ss_btn.configure(state=tk.NORMAL)
        self._hide_cancel_button()

        if msg_type == "extract_cancelled":
            self._set_status("Ekstrakcija otkazana / Extraction cancelled")
            self._log_append(
                self._extract_log,
                "\n--- OTKAZANO / CANCELLED ---\n",
            )
            return

        if msg_type == "extract_done":
            n = data
            self._set_status(
                f"Ekstrakcija zavr\u0161ena \u2014 {n} klijenata / "
                f"Extraction complete \u2014 {n} clients"
            )
            self._log_append(
                self._extract_log,
                f"\n--- ZAVR\u0160ENO / COMPLETE ---\n"
                f"Ekstrahirano klijenata / Clients extracted: {n}\n"
                f"Tablica spremna / Spreadsheet ready: output/control_spreadsheet.xlsx\n",
            )
            # Update sidebar availability
            self._update_sidebar()
            # Offer to open spreadsheet
            cfg = self._load_config_safe(quiet=True)
            if cfg and cfg.spreadsheet_path.exists():
                if messagebox.askyesno(
                    "Tablica spremna / Spreadsheet Ready",
                    "Kontrolna tablica je kreirana.\nThe control spreadsheet has been created.\n\n"
                    "\u017delite li je otvoriti u Excelu?\n"
                    "Would you like to open it in Excel?",
                ):
                    self._open_file(cfg.spreadsheet_path)
            # M34: Next step affordance
            self._add_next_step_button(self._content_frame, 3)
        else:
            self._set_status("Ekstrakcija neuspjela / Extraction failed")
            self._log_append(self._extract_log, f"\n--- GRE\u0160KA / ERROR ---\n{data}\n")
            messagebox.showerror(
                "Gre\u0161ka / Error",
                f"Ekstrakcija nije uspjela:\nExtraction failed:\n\n{data}",
            )

    # ── Step 3: Review ───────────────────────────────────────────────────

    def _build_review(self, parent: ttk.Frame) -> None:
        self._add_title(
            parent,
            "Pregled tablice",
            "Ru\u010dni pregled i odobravanje / Manual review and approval",
        )

        instructions = ttk.Frame(parent)
        instructions.pack(fill=tk.X, pady=(0, 16))

        steps_text = (
            "Koraci / Steps:\n\n"
            "1. Otvorite kontrolnu tablicu (output/control_spreadsheet.xlsx)\n"
            "   Open the control spreadsheet\n\n"
            "2. Na listu 'Pregled klijenata' (Sheet 1):\n"
            "   Ozna\u010dite stupac Status (I) kao 'Odobreno' za klijente kojima\n"
            "   \u017eelite generirati aneks\n"
            "   Mark the Status column (I) as 'Odobreno' for clients\n"
            "   you want to generate an annex for\n\n"
            "3. Na listu 'Cijene' (Sheet 2):\n"
            "   Unesite nove cijene u stupac 'Nova cijena EUR' (G)\n"
            "   Enter new prices in the 'Nova cijena EUR' column (G)\n\n"
            "4. Spremite i zatvorite tablicu\n"
            "   Save and close the spreadsheet\n\n"
            "5. Kliknite 'Gotovo, nastavi' za nastavak\n"
            "   Click 'Done, continue' to proceed"
        )

        text = tk.Text(
            instructions,
            wrap=tk.WORD,
            font=("Arial", 11),
            bg="#fdf6e3",
            fg="#586e75",
            height=16,
            relief=tk.FLAT,
            padx=16,
            pady=12,
        )
        text.insert("1.0", steps_text)
        text.configure(state=tk.DISABLED)
        text.pack(fill=tk.BOTH, expand=True)

        # Buttons
        btn_frame = ttk.Frame(parent)
        btn_frame.pack(fill=tk.X)

        ttk.Button(
            btn_frame,
            text="Otvori tablicu / Open Spreadsheet",
            command=self._open_spreadsheet,
        ).pack(side=tk.LEFT)

        ttk.Button(
            btn_frame,
            text="Gotovo, nastavi / Done, Continue",
            command=lambda: self._show_step(4),
        ).pack(side=tk.LEFT, padx=(8, 0))

    def _open_spreadsheet(self) -> None:
        cfg = self._load_config_safe()
        if cfg is None:
            return
        if not cfg.spreadsheet_path.exists():
            messagebox.showwarning(
                "Tablica nije prona\u0111ena / Spreadsheet Not Found",
                "Kontrolna tablica ne postoji.\n"
                "Pokrenite najprije korak 'Ekstrakcija'.\n\n"
                "Spreadsheet not found. Run 'Extraction' first.",
            )
            return
        self._open_file(cfg.spreadsheet_path)

    # ── Step 4: Generation ───────────────────────────────────────────────

    def _build_generation(self, parent: ttk.Frame) -> None:
        self._add_title(
            parent,
            "Generiranje aneksa",
            "Kreiranje novih aneks dokumenata / Create new annex documents",
        )

        # Starting number input
        num_frame = ttk.Frame(parent)
        num_frame.pack(fill=tk.X, pady=(0, 8))

        ttk.Label(
            num_frame,
            text="Po\u010detni broj aneksa / Starting annex number:",
        ).pack(side=tk.LEFT)
        self._start_num_var = tk.StringVar(value="1")
        ttk.Entry(num_frame, textvariable=self._start_num_var, width=8).pack(
            side=tk.LEFT, padx=(8, 0)
        )

        # Buttons
        btn_frame = ttk.Frame(parent)
        btn_frame.pack(fill=tk.X, pady=(0, 4))

        self._gen_preview_btn = ttk.Button(
            btn_frame,
            text="Pregledaj / Preview",
            command=self._run_preview,
        )
        self._gen_preview_btn.pack(side=tk.LEFT)

        self._gen_btn = ttk.Button(
            btn_frame,
            text="Generiraj anekse / Generate Annexes",
            command=self._run_generation,
        )
        self._gen_btn.pack(side=tk.LEFT, padx=(8, 0))

        self._gen_open_btn = ttk.Button(
            btn_frame,
            text="Otvori mapu / Open Folder",
            command=self._open_output_folder,
        )
        self._gen_open_btn.pack(side=tk.LEFT, padx=(8, 0))

        # Cancel button (H13)
        self._gen_cancel_btn = self._add_cancel_button(btn_frame)

        self._gen_progress = self._add_progress(parent)
        self._gen_log = self._add_log_area(parent)

    def _get_start_number(self) -> int | None:
        try:
            n = int(self._start_num_var.get())
            if n < 1:
                raise ValueError
            return n
        except ValueError:
            messagebox.showwarning(
                "Neispravan broj / Invalid Number",
                "Unesite ispravan po\u010detni broj (npr. 1, 30).\n"
                "Enter a valid starting number (e.g. 1, 30).",
            )
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

        self._running = True
        self._cancel_event.clear()
        self._gen_preview_btn.configure(state=tk.DISABLED)
        self._gen_btn.configure(state=tk.DISABLED)
        self._show_cancel_button(self._gen_cancel_btn)
        self._gen_progress.start(10)
        self._set_status("Pregledavanje... / Previewing...")
        self._buffered.install()

        def task() -> None:
            try:
                if self._cancel_event.is_set():
                    self._queue.put(("preview_cancelled", None))
                    return
                from doc_pipeline.phases.generation import run_generation
                run_generation(cfg, start_number=start, dry_run=True)
                if self._cancel_event.is_set():
                    self._queue.put(("preview_cancelled", None))
                    return
                self._queue.put(("preview_done", None))
            except Exception as exc:
                self._queue.put(("preview_error", str(exc)))
            finally:
                self._buffered.restore()

        threading.Thread(target=task, daemon=True).start()
        self._poll_queue(self._gen_log, self._gen_progress, self._on_preview_done)

    def _on_preview_done(self, msg_type: str, data: Any) -> None:
        self._gen_progress.stop()
        self._gen_progress.configure(mode="indeterminate")
        self._running = False
        self._gen_preview_btn.configure(state=tk.NORMAL)
        self._gen_btn.configure(state=tk.NORMAL)
        self._hide_cancel_button()

        if msg_type == "preview_cancelled":
            self._set_status("Pregled otkazan / Preview cancelled")
            self._log_append(self._gen_log, "\n--- OTKAZANO / CANCELLED ---\n")
            return

        if msg_type == "preview_done":
            self._set_status("Pregled zavr\u0161en / Preview complete")
        else:
            self._set_status("Pregled neuspio / Preview failed")
            self._log_append(self._gen_log, f"\n--- GRE\u0160KA / ERROR ---\n{data}\n")

    def _run_generation(self) -> None:
        if self._running:
            return
        cfg = self._load_config_safe()
        if cfg is None:
            return
        start = self._get_start_number()
        if start is None:
            return

        if not messagebox.askyesno(
            "Potvrda / Confirm",
            "Jeste li sigurni da \u017eelite generirati anekse?\n"
            "Are you sure you want to generate annexes?\n\n"
            "Provjerite najprije pregled (Preview).\n"
            "Check the preview first.",
        ):
            return

        self._running = True
        self._cancel_event.clear()
        self._gen_preview_btn.configure(state=tk.DISABLED)
        self._gen_btn.configure(state=tk.DISABLED)
        self._show_cancel_button(self._gen_cancel_btn)
        self._gen_progress.start(10)
        self._set_status("Generiranje u tijeku... / Generating...")
        self._buffered.install()

        def task() -> None:
            try:
                if self._cancel_event.is_set():
                    self._queue.put(("gen_cancelled", None))
                    return
                from doc_pipeline.phases.generation import run_generation
                paths = run_generation(cfg, start_number=start)
                if self._cancel_event.is_set():
                    self._queue.put(("gen_cancelled", None))
                    return
                self._queue.put(("gen_done", len(paths)))
            except Exception as exc:
                self._queue.put(("gen_error", str(exc)))
            finally:
                self._buffered.restore()

        threading.Thread(target=task, daemon=True).start()
        self._poll_queue(self._gen_log, self._gen_progress, self._on_gen_done)

    def _on_gen_done(self, msg_type: str, data: Any) -> None:
        self._gen_progress.stop()
        self._gen_progress.configure(mode="indeterminate")
        self._running = False
        self._gen_preview_btn.configure(state=tk.NORMAL)
        self._gen_btn.configure(state=tk.NORMAL)
        self._hide_cancel_button()

        if msg_type == "gen_cancelled":
            self._set_status("Generiranje otkazano / Generation cancelled")
            self._log_append(self._gen_log, "\n--- OTKAZANO / CANCELLED ---\n")
            return

        if msg_type == "gen_done":
            n = data
            self._set_status(
                f"Generirano {n} aneksa / Generated {n} annexes"
            )
            self._log_append(
                self._gen_log,
                f"\n--- ZAVR\u0160ENO / COMPLETE ---\n"
                f"Generirano aneksa / Annexes generated: {n}\n",
            )
            messagebox.showinfo(
                "Gotovo / Done",
                f"Generirano {n} aneksa!\n"
                f"Generated {n} annexes!\n\n"
                f"Datoteke se nalaze u mapi output/annexes/\n"
                f"Files are located in the output/annexes/ folder.",
            )
        else:
            self._set_status("Generiranje neuspjelo / Generation failed")
            self._log_append(self._gen_log, f"\n--- GRE\u0160KA / ERROR ---\n{data}\n")
            messagebox.showerror(
                "Gre\u0161ka / Error",
                f"Generiranje nije uspjelo:\nGeneration failed:\n\n{data}",
            )

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
                self._update_progress(progress_bar, current, total)
                # Don't call done_callback for progress messages — keep polling
            else:
                done_callback(msg_type, msg[1] if len(msg) > 1 else None)
                return
        except queue.Empty:
            pass

        # Continue polling
        self.root.after(
            100,
            lambda: self._poll_queue(log_widget, progress_bar, done_callback),
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
                "Gre\u0161ka / Error",
                f"Nije mogu\u0107e otvoriti datoteku.\nCannot open file.\n\n{e}",
            )

    def run(self) -> None:
        """Start the GUI event loop."""
        self.root.mainloop()


def main() -> None:
    app = PipelineGUI()
    app.run()


if __name__ == "__main__":
    main()
