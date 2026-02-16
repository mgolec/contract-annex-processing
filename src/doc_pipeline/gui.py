"""Tkinter GUI wizard for the contract price adjustment pipeline."""

from __future__ import annotations

import os
import platform
import queue
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

class _BufferedConsole:
    """Captures Rich console output into a StringIO for GUI display."""

    def __init__(self) -> None:
        self._buffer = StringIO()
        self._original_console = None

    def install(self) -> None:
        """Replace the global Rich console with one that writes to our buffer.

        Also patches console.input() to auto-confirm, since the GUI handles
        all user confirmations via its own dialogs before launching tasks.
        """
        from rich.console import Console
        from doc_pipeline.utils import progress

        self._original_console = progress.console
        console = Console(
            file=self._buffer,
            force_terminal=False,
            no_color=True,
            width=120,
        )
        # Patch input() to auto-confirm — GUI already asked the user
        console.input = lambda prompt="": "y"
        progress.console = console

    def restore(self) -> None:
        if self._original_console is not None:
            from doc_pipeline.utils import progress
            progress.console = self._original_console

    def read_new(self) -> str:
        """Read any new output since last call."""
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


# ── Main Application ─────────────────────────────────────────────────────────

class PipelineGUI:
    """Main GUI window — wizard style with sidebar steps."""

    def __init__(self) -> None:
        self.root = tk.Tk()
        self.root.title("Procudo — Pipeline za ugovore")
        self.root.geometry("960x680")
        self.root.minsize(800, 560)

        # Message queue for background thread → GUI communication
        self._queue: queue.Queue[tuple[str, Any]] = queue.Queue()
        self._buffered = _BufferedConsole()
        self._running = False  # Is a background task running?

        self._current_step = 0
        self._step_labels: list[tk.Label] = []
        self._content_frame: tk.Frame | None = None

        self._build_ui()
        self._show_step(0)

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

        # Step labels
        for i, (num, hr_name, _en_name) in enumerate(STEPS):
            marker = "\u25cb"  # ○
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
        if not self._running:
            self._show_step(step)

    def _show_step(self, step: int) -> None:
        self._current_step = step
        self._update_sidebar()

        # Clear content
        if self._content_frame is not None:
            self._content_frame.destroy()
        self._content_frame = ttk.Frame(self._content_outer, padding=16)
        self._content_frame.pack(fill=tk.BOTH, expand=True)

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
                lbl.configure(text=f"  \u25cb  {hr_name}", style="SidebarStep.TLabel")

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

    def _log_append(self, log_widget: tk.Text, text: str) -> None:
        log_widget.configure(state=tk.NORMAL)
        log_widget.insert(tk.END, text)
        log_widget.see(tk.END)
        log_widget.configure(state=tk.DISABLED)

    def _load_config_safe(self) -> Any:
        """Load config, return None on error."""
        try:
            from doc_pipeline.config import load_config
            return load_config()
        except Exception as exc:
            messagebox.showerror(
                "Greška / Error",
                f"Konfiguracija se ne može učitati:\n{exc}\n\n"
                "Provjerite pipeline.toml i .env datoteke.",
            )
            return None

    # ── Step 0: Settings ─────────────────────────────────────────────────

    def _build_settings(self, parent: ttk.Frame) -> None:
        self._add_title(parent, "Postavke", "Konfiguracija pipeline-a / Pipeline settings")

        # Load current values
        cfg = self._load_config_safe()

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
            "Mapa s ugovorima:",
            "paths.source",
            cfg.paths.source if cfg else "./contracts",
        )

        # Section: Company info
        ttk.Label(
            form_frame, text="Podaci o tvrtki / Company Info", font=("Arial", 11, "bold")
        ).grid(row=row, column=0, columnspan=2, sticky=tk.W, pady=(16, 4))
        row += 1

        add_field("Naziv tvrtke:", "general.company_name", cfg.general.company_name if cfg else "")
        add_field("OIB:", "general.company_oib", cfg.general.company_oib if cfg else "")
        add_field("Adresa:", "general.company_address", cfg.general.company_address if cfg else "")
        add_field("Direktor:", "general.company_director", cfg.general.company_director if cfg else "")

        # Section: API
        ttk.Label(
            form_frame, text="API / Ekstrakcija", font=("Arial", 11, "bold")
        ).grid(row=row, column=0, columnspan=2, sticky=tk.W, pady=(16, 4))
        row += 1

        add_field(
            "Anthropic API ključ:",
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
            "Datum stupanja na snagu (YYYY-MM-DD):",
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
        try:
            toml_lines = [
                "[general]",
                f'company_name = "{entries["general.company_name"].get()}"',
                f'company_oib = "{entries["general.company_oib"].get()}"',
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
                f'default_effective_date = "{entries["generation.default_effective_date"].get()}"',
                'vat_note = "Sve cijene su izražene bez PDV-a."',
                "",
            ]

            toml_path = _PROJECT_ROOT / "pipeline.toml"
            toml_path.write_text("\n".join(toml_lines), encoding="utf-8")

            # Write .env
            api_key = entries["api_key"].get().strip()
            env_path = _PROJECT_ROOT / ".env"
            env_path.write_text(f"ANTHROPIC_API_KEY={api_key}\n", encoding="utf-8")

            self._set_status("Postavke spremljene / Settings saved")
            messagebox.showinfo(
                "Spremljeno / Saved",
                "Postavke su spremljene.\nSettings have been saved.\n\n"
                "Možete nastaviti na sljedeći korak.\n"
                "You can proceed to the next step.",
            )
        except Exception as exc:
            messagebox.showerror("Greška / Error", f"Spremanje nije uspjelo:\n{exc}")

    # ── Step 1: Setup ────────────────────────────────────────────────────

    def _build_setup(self, parent: ttk.Frame) -> None:
        self._add_title(
            parent,
            "Priprema",
            "Skeniranje i kopiranje ugovora / Scan and copy contracts",
        )

        # Show inventory status if it exists
        cfg = self._load_config_safe()
        if cfg and cfg.inventory_path.exists():
            try:
                from doc_pipeline.models import Inventory
                inv = Inventory.load(cfg.inventory_path)
                info = (
                    f"Postojeći inventar: {inv.total_clients} klijenata, "
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

        self._setup_progress = self._add_progress(parent)
        self._setup_log = self._add_log_area(parent)

    def _run_setup(self, scan_only: bool = False) -> None:
        if self._running:
            return
        cfg = self._load_config_safe()
        if cfg is None:
            return

        self._running = True
        self._setup_btn.configure(state=tk.DISABLED)
        self._setup_rescan_btn.configure(state=tk.DISABLED)
        self._setup_progress.start(10)
        self._set_status("Priprema u tijeku... / Setup running...")
        self._buffered.install()

        def task() -> None:
            try:
                from doc_pipeline.phases.setup import run_setup
                inventory = run_setup(cfg, scan_only=scan_only)
                self._queue.put(("setup_done", inventory))
            except Exception as exc:
                self._queue.put(("setup_error", str(exc)))
            finally:
                self._buffered.restore()

        threading.Thread(target=task, daemon=True).start()
        self._poll_queue(self._setup_log, self._on_setup_done)

    def _on_setup_done(self, msg_type: str, data: Any) -> None:
        self._setup_progress.stop()
        self._running = False
        self._setup_btn.configure(state=tk.NORMAL)
        self._setup_rescan_btn.configure(state=tk.NORMAL)

        if msg_type == "setup_done":
            inv = data
            self._set_status(
                f"Priprema završena — {inv.total_clients} klijenata / "
                f"Setup complete — {inv.total_clients} clients"
            )
            self._log_append(
                self._setup_log,
                f"\n--- ZAVRŠENO / COMPLETE ---\n"
                f"Klijenti: {inv.total_clients}\n"
                f"S ugovorima: {inv.clients_with_contracts}\n"
                f"S aneksima: {inv.clients_with_annexes}\n"
                f"Označeni: {len(inv.flagged_clients)}\n",
            )
        else:
            self._set_status("Priprema neuspjela / Setup failed")
            self._log_append(self._setup_log, f"\n--- GREŠKA / ERROR ---\n{data}\n")
            messagebox.showerror("Greška / Error", f"Priprema nije uspjela:\n{data}")

    # ── Step 2: Extraction ───────────────────────────────────────────────

    def _build_extraction(self, parent: ttk.Frame) -> None:
        self._add_title(
            parent,
            "Ekstrakcija",
            "Čitanje ugovora i ekstrakcija cijena / Parse contracts and extract pricing",
        )

        # Show extraction status
        cfg = self._load_config_safe()
        if cfg and cfg.extractions_path.exists():
            n_extracted = len(list(cfg.extractions_path.glob("*.json")))
            if n_extracted > 0:
                ttk.Label(
                    parent,
                    text=f"Već ekstrahirano: {n_extracted} klijenata / "
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
                "Nedostaje inventar",
                "Inventar nije pronađen. Pokrenite najprije korak 'Priprema'.\n\n"
                "Inventory not found. Run the 'Setup' step first.",
            )
            return

        self._running = True
        self._extract_btn.configure(state=tk.DISABLED)
        self._extract_force_btn.configure(state=tk.DISABLED)
        self._extract_ss_btn.configure(state=tk.DISABLED)
        self._extract_progress.start(10)
        self._set_status("Ekstrakcija u tijeku... / Extraction running...")
        self._buffered.install()

        def task() -> None:
            try:
                from doc_pipeline.phases.extraction import run_extraction
                results = run_extraction(
                    cfg,
                    force=force,
                    spreadsheet_only=spreadsheet_only,
                )
                self._queue.put(("extract_done", len(results)))
            except Exception as exc:
                self._queue.put(("extract_error", str(exc)))
            finally:
                self._buffered.restore()

        threading.Thread(target=task, daemon=True).start()
        self._poll_queue(self._extract_log, self._on_extract_done)

    def _on_extract_done(self, msg_type: str, data: Any) -> None:
        self._extract_progress.stop()
        self._running = False
        self._extract_btn.configure(state=tk.NORMAL)
        self._extract_force_btn.configure(state=tk.NORMAL)
        self._extract_ss_btn.configure(state=tk.NORMAL)

        if msg_type == "extract_done":
            n = data
            self._set_status(f"Ekstrakcija završena — {n} klijenata / Extraction complete")
            self._log_append(
                self._extract_log,
                f"\n--- ZAVRŠENO / COMPLETE ---\n"
                f"Ekstrahirano klijenata: {n}\n"
                f"Tablica spremna u output/control_spreadsheet.xlsx\n",
            )
            # Offer to open spreadsheet
            cfg = self._load_config_safe()
            if cfg and cfg.spreadsheet_path.exists():
                if messagebox.askyesno(
                    "Tablica spremna",
                    "Kontrolna tablica je kreirana.\n\n"
                    "Želite li je otvoriti u Excelu?\n"
                    "Would you like to open it in Excel?",
                ):
                    self._open_file(cfg.spreadsheet_path)
        else:
            self._set_status("Ekstrakcija neuspjela / Extraction failed")
            self._log_append(self._extract_log, f"\n--- GREŠKA / ERROR ---\n{data}\n")
            messagebox.showerror("Greška / Error", f"Ekstrakcija nije uspjela:\n{data}")

    # ── Step 3: Review ───────────────────────────────────────────────────

    def _build_review(self, parent: ttk.Frame) -> None:
        self._add_title(
            parent,
            "Pregled tablice",
            "Ručni pregled i odobravanje / Manual review and approval",
        )

        instructions = ttk.Frame(parent)
        instructions.pack(fill=tk.X, pady=(0, 16))

        steps_text = (
            "Koraci / Steps:\n\n"
            "1. Otvorite kontrolnu tablicu (output/control_spreadsheet.xlsx)\n"
            "   Open the control spreadsheet\n\n"
            "2. Na listu 'Pregled klijenata' (Sheet 1):\n"
            "   Označite stupac Status (I) kao 'Odobreno' za klijente kojima\n"
            "   želite generirati aneks\n"
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
                "Tablica nije pronađena",
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

        ttk.Label(num_frame, text="Početni broj aneksa / Starting annex number:").pack(
            side=tk.LEFT
        )
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
                "Neispravan broj",
                "Unesite ispravan početni broj (npr. 1, 30).\n"
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
        self._gen_preview_btn.configure(state=tk.DISABLED)
        self._gen_btn.configure(state=tk.DISABLED)
        self._gen_progress.start(10)
        self._set_status("Pregledavanje... / Previewing...")
        self._buffered.install()

        def task() -> None:
            try:
                from doc_pipeline.phases.generation import run_generation
                run_generation(cfg, start_number=start, dry_run=True)
                self._queue.put(("preview_done", None))
            except Exception as exc:
                self._queue.put(("preview_error", str(exc)))
            finally:
                self._buffered.restore()

        threading.Thread(target=task, daemon=True).start()
        self._poll_queue(self._gen_log, self._on_preview_done)

    def _on_preview_done(self, msg_type: str, data: Any) -> None:
        self._gen_progress.stop()
        self._running = False
        self._gen_preview_btn.configure(state=tk.NORMAL)
        self._gen_btn.configure(state=tk.NORMAL)

        if msg_type == "preview_done":
            self._set_status("Pregled završen / Preview complete")
        else:
            self._set_status("Pregled neuspio / Preview failed")
            self._log_append(self._gen_log, f"\n--- GREŠKA / ERROR ---\n{data}\n")

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
            "Jeste li sigurni da želite generirati anekse?\n"
            "Are you sure you want to generate annexes?\n\n"
            "Provjerite najprije pregled (Preview).",
        ):
            return

        self._running = True
        self._gen_preview_btn.configure(state=tk.DISABLED)
        self._gen_btn.configure(state=tk.DISABLED)
        self._gen_progress.start(10)
        self._set_status("Generiranje u tijeku... / Generating...")
        self._buffered.install()

        def task() -> None:
            try:
                from doc_pipeline.phases.generation import run_generation
                paths = run_generation(cfg, start_number=start)
                self._queue.put(("gen_done", len(paths)))
            except Exception as exc:
                self._queue.put(("gen_error", str(exc)))
            finally:
                self._buffered.restore()

        threading.Thread(target=task, daemon=True).start()
        self._poll_queue(self._gen_log, self._on_gen_done)

    def _on_gen_done(self, msg_type: str, data: Any) -> None:
        self._gen_progress.stop()
        self._running = False
        self._gen_preview_btn.configure(state=tk.NORMAL)
        self._gen_btn.configure(state=tk.NORMAL)

        if msg_type == "gen_done":
            n = data
            self._set_status(f"Generirano {n} aneksa / Generated {n} annexes")
            self._log_append(
                self._gen_log,
                f"\n--- ZAVRŠENO / COMPLETE ---\nGenerirano aneksa: {n}\n",
            )
            messagebox.showinfo(
                "Gotovo / Done",
                f"Generirano {n} aneksa!\n"
                f"Generated {n} annexes!\n\n"
                f"Datoteke se nalaze u mapi output/annexes/",
            )
        else:
            self._set_status("Generiranje neuspjelo / Generation failed")
            self._log_append(self._gen_log, f"\n--- GREŠKA / ERROR ---\n{data}\n")
            messagebox.showerror("Greška / Error", f"Generiranje nije uspjelo:\n{data}")

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
        done_callback: Any,
    ) -> None:
        """Poll for background thread messages and buffered console output."""
        # Check for buffered console output
        new_text = self._buffered.read_new()
        if new_text:
            self._log_append(log_widget, new_text)

        # Check message queue
        try:
            msg_type, data = self._queue.get_nowait()
            done_callback(msg_type, data)
            return
        except queue.Empty:
            pass

        # Continue polling
        self.root.after(100, lambda: self._poll_queue(log_widget, done_callback))

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
        except Exception:
            pass  # Silently ignore if open fails

    def run(self) -> None:
        """Start the GUI event loop."""
        self.root.mainloop()


def main() -> None:
    app = PipelineGUI()
    app.run()


if __name__ == "__main__":
    main()
