#!/usr/bin/env python3
"""
Launch the contract pipeline GUI.
Double-click this file or run: python launch.py

Pokrenite pipeline GUI.
Dvostruki klik na ovu datoteku ili pokrenite: python launch.py
"""

import os
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent


def find_venv_python() -> str | None:
    """Find the Python executable in the virtual environment."""
    if sys.platform == "win32":
        suffix = os.path.join("Scripts", "python.exe")
    else:
        suffix = os.path.join("bin", "python")

    for venv_name in (".venv", "venv"):
        candidate = PROJECT_ROOT / venv_name / suffix
        if candidate.exists():
            return str(candidate)

    return None


def main() -> None:
    python = find_venv_python()

    if python is None:
        print("Virtualno okruženje nije pronađeno.")
        print("Virtual environment not found.")
        print()
        print("Pokrenite najprije / Run first:")
        print(f"  python {PROJECT_ROOT / 'setup_env.py'}")
        input("\nPritisnite Enter za izlaz / Press Enter to exit...")
        sys.exit(1)

    os.chdir(str(PROJECT_ROOT))
    result = subprocess.run([python, "-m", "doc_pipeline.gui"])
    sys.exit(result.returncode)


if __name__ == "__main__":
    main()
