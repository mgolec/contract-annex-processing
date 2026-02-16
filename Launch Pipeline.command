#!/bin/bash
# Double-click this file to launch the Pipeline GUI
cd "$(dirname "$0")"

# Find Python in venv
if [ -f ".venv/bin/python" ]; then
    PYTHON=".venv/bin/python"
elif [ -f "venv/bin/python" ]; then
    PYTHON="venv/bin/python"
else
    echo "Virtualno okruženje nije pronađeno."
    echo "Virtual environment not found."
    echo ""
    echo "Pokrenite najprije / Run first:"
    echo "  python3 setup_env.py"
    echo ""
    read -p "Pritisnite Enter za izlaz / Press Enter to exit..."
    exit 1
fi

$PYTHON -m doc_pipeline.gui
