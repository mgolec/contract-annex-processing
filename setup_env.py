#!/usr/bin/env python3
"""
One-time environment setup for the contract pipeline.
Creates a virtual environment, installs dependencies, and validates the setup.

Usage:
    python setup_env.py
    python3 setup_env.py
"""

import os
import subprocess
import sys
import venv
from pathlib import Path

# Minimum Python version
MIN_PYTHON = (3, 9)

# Project root = directory containing this script
PROJECT_ROOT = Path(__file__).resolve().parent
VENV_DIR = PROJECT_ROOT / ".venv"


def print_header(msg: str) -> None:
    print(f"\n{'=' * 60}")
    print(f"  {msg}")
    print(f"{'=' * 60}")


def print_ok(msg: str) -> None:
    print(f"  [OK] {msg}")


def print_warn(msg: str) -> None:
    print(f"  [!]  {msg}")


def print_err(msg: str) -> None:
    print(f"  [X]  {msg}")


def check_python_version() -> None:
    """Verify Python version meets minimum requirement."""
    ver = sys.version_info[:2]
    if ver < MIN_PYTHON:
        print_err(
            f"Python {MIN_PYTHON[0]}.{MIN_PYTHON[1]}+ je potreban. "
            f"Trenutna verzija: {ver[0]}.{ver[1]}"
        )
        print_err(
            f"Python {MIN_PYTHON[0]}.{MIN_PYTHON[1]}+ is required. "
            f"Current version: {ver[0]}.{ver[1]}"
        )
        sys.exit(1)
    print_ok(f"Python {ver[0]}.{ver[1]} — OK")


def create_venv() -> Path:
    """Create virtual environment and return path to its Python executable."""
    if VENV_DIR.exists():
        print_ok(f"Virtualno okruženje već postoji: {VENV_DIR}")
        print_ok(f"Virtual environment already exists: {VENV_DIR}")
    else:
        print(f"  Kreiram virtualno okruženje: {VENV_DIR} ...")
        venv.create(str(VENV_DIR), with_pip=True)
        print_ok("Virtualno okruženje kreirano / Virtual environment created")

    if sys.platform == "win32":
        python = VENV_DIR / "Scripts" / "python.exe"
    else:
        python = VENV_DIR / "bin" / "python"

    if not python.exists():
        print_err(f"Python executable nije pronađen: {python}")
        sys.exit(1)

    return python


def install_dependencies(python: Path) -> None:
    """Install project dependencies using pip."""
    print("  Instaliram ovisnosti / Installing dependencies ...")

    # Upgrade pip first
    subprocess.run(
        [str(python), "-m", "pip", "install", "--upgrade", "pip"],
        check=True,
        capture_output=True,
    )

    # Install the project in editable mode (pulls all deps from pyproject.toml)
    result = subprocess.run(
        [str(python), "-m", "pip", "install", "-e", str(PROJECT_ROOT)],
        capture_output=True,
        text=True,
    )

    if result.returncode != 0:
        print_err("Instalacija nije uspjela / Installation failed:")
        print(result.stderr)
        sys.exit(1)

    print_ok("Sve ovisnosti instalirane / All dependencies installed")


def check_libreoffice() -> None:
    """Check if LibreOffice is available (needed for .doc conversion)."""
    lo_names = ["soffice", "libreoffice"]
    if sys.platform == "darwin":
        lo_names.append("/Applications/LibreOffice.app/Contents/MacOS/soffice")
    elif sys.platform == "win32":
        # Common Windows install paths
        for prog in [os.environ.get("PROGRAMFILES", ""), os.environ.get("PROGRAMFILES(X86)", "")]:
            if prog:
                lo_names.append(os.path.join(prog, "LibreOffice", "program", "soffice.exe"))

    found = False
    for name in lo_names:
        try:
            result = subprocess.run(
                [name, "--version"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode == 0:
                ver = result.stdout.strip()
                print_ok(f"LibreOffice: {ver}")
                found = True
                break
        except (FileNotFoundError, subprocess.TimeoutExpired):
            continue

    if not found:
        print_warn(
            "LibreOffice nije pronađen. Potreban je samo za .doc datoteke."
        )
        print_warn(
            "LibreOffice not found. Only needed for .doc files."
        )


def validate_imports(python: Path) -> None:
    """Validate that key imports work."""
    test_code = (
        "import doc_pipeline.config; "
        "import doc_pipeline.models; "
        "import doc_pipeline.phases.setup; "
        "import doc_pipeline.phases.extraction; "
        "import doc_pipeline.phases.generation; "
        "print('OK')"
    )
    result = subprocess.run(
        [str(python), "-c", test_code],
        capture_output=True,
        text=True,
        cwd=str(PROJECT_ROOT),
    )
    if result.returncode != 0:
        print_err("Validacija importa nije uspjela / Import validation failed:")
        print(result.stderr)
        sys.exit(1)
    print_ok("Svi moduli se uspješno učitavaju / All modules import successfully")


def check_template() -> None:
    """Check if the annex template exists."""
    template = PROJECT_ROOT / "templates" / "default" / "aneks_template.docx"
    if template.exists():
        print_ok(f"Predložak pronađen / Template found: {template.name}")
    else:
        print_warn(
            "Predložak za aneks nije pronađen. "
            "Potreban je za fazu generiranja."
        )
        print_warn(
            "Annex template not found. "
            "Needed for the generation phase."
        )


def check_config() -> None:
    """Check if pipeline.toml exists, create from template if not."""
    config_path = PROJECT_ROOT / "pipeline.toml"
    template_path = PROJECT_ROOT / "pipeline.toml.template"

    if config_path.exists():
        print_ok("Konfiguracija pronađena / Config found: pipeline.toml")
    elif template_path.exists():
        import shutil
        shutil.copy2(template_path, config_path)
        print_ok(
            "pipeline.toml kreiran iz predloška — prilagodite vrijednosti"
        )
        print_ok(
            "pipeline.toml created from template — adjust the values"
        )
    else:
        print_warn("pipeline.toml nije pronađen / pipeline.toml not found")


def check_env() -> None:
    """Check if .env with API key exists."""
    env_path = PROJECT_ROOT / ".env"
    if env_path.exists():
        content = env_path.read_text()
        if "ANTHROPIC_API_KEY" in content:
            # Check it's not a placeholder
            for line in content.splitlines():
                if line.startswith("ANTHROPIC_API_KEY=") and len(line.split("=", 1)[1].strip()) > 10:
                    print_ok("API ključ pronađen / API key found in .env")
                    return
        print_warn(
            ".env postoji ali API ključ nije postavljen / "
            ".env exists but API key not set"
        )
    else:
        print_warn(
            ".env nije pronađen — API ključ možete postaviti kroz GUI"
        )
        print_warn(
            ".env not found — you can set the API key through the GUI"
        )


def main() -> None:
    print_header("Pipeline za ugovore — Postavljanje okruženja")
    print("  Contract Pipeline — Environment Setup")
    print()

    # 1. Check Python version
    print("\n--- Python ---")
    check_python_version()

    # 2. Create virtual environment
    print("\n--- Virtualno okruženje / Virtual Environment ---")
    python = create_venv()

    # 3. Install dependencies
    print("\n--- Ovisnosti / Dependencies ---")
    install_dependencies(python)

    # 4. Validate imports
    print("\n--- Validacija / Validation ---")
    validate_imports(python)

    # 5. Check system dependencies
    print("\n--- Sistemske ovisnosti / System Dependencies ---")
    check_libreoffice()

    # 6. Check project files
    print("\n--- Projektne datoteke / Project Files ---")
    check_config()
    check_env()
    check_template()

    # Done
    print_header("Postavljanje završeno! / Setup complete!")
    print()
    print("  Sljedeći korak / Next step:")
    print("    python launch.py")
    print()
    print("  Ili dvostruki klik na launch.py / Or double-click launch.py")
    print()


if __name__ == "__main__":
    main()
